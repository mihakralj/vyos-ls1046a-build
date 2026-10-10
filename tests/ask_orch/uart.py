"""Shared DUT/dell1 command interface and optional manual DUT UART recovery.

Rig profiles use management SSH. The existing Console API is retained for
the imported scenarios; a raw DUT UART is only used outside the SSH profile
when ASK_TARGET_DEV explicitly names a recovery console.
"""

from __future__ import annotations

import argparse
import errno
import os
import re
import select
import sys
import termios
import threading
import time
from dataclasses import dataclass

import serial


DEFAULT_TARGET_DEV = os.environ.get("ASK_TARGET_DEV", "")
DEFAULT_BAUD       = 115200

_TARGET_SESSION = None


def target_session():
    if _TARGET_SESSION is None:
        raise RuntimeError("DUT UART session is not open; request the target_agent fixture")
    return _TARGET_SESSION


def set_target_session(session):
    global _TARGET_SESSION
    if session is not None and _TARGET_SESSION is not None:
        raise RuntimeError("DUT UART already has an owner")
    _TARGET_SESSION = session

# One UART, one reader -- enforced here rather than left as a convention.
#
# Nothing stops two Console objects naming the same device, and the module
# lifecycle test does exactly that: it opens its own console for the rmmod
# sequence while the rig fixture already holds one. Both then sit in _pump()
# on the same tty, each consuming bytes the other is waiting for, and both
# block until their timeout -- which, for callers that pass no timeout, is
# never. Serial reads are destructive, so this cannot be fixed by buffering.
#
# The lock is keyed by device and shared across instances, because no
# per-instance state can see the other reader. It is re-entrant so run() may
# call expect() while holding it.
#
# It serialises whole operations (run, login, sync_prompt, a bare expect), not
# individual reads. A caller that sends and then expects as two separate steps
# still races another reader between them; use run(), or hold `port_lock()`
# across the pair.
_PORT_LOCKS: dict[str, threading.RLock] = {}
_PORT_LOCKS_GUARD = threading.Lock()


def port_lock(port: str) -> threading.RLock:
    """The lock guarding `port`, created on first use."""
    with _PORT_LOCKS_GUARD:
        lock = _PORT_LOCKS.get(port)
        if lock is None:
            lock = threading.RLock()
            _PORT_LOCKS[port] = lock
        return lock

# Patterns we expect to see from the remote shell. Kept loose — BusyBox,
# systemd's agetty, Armbian, and Yocto all use slightly different prompts.
LOGIN_RE    = re.compile(rb"(\w[\w.-]*)\s+login:\s*$", re.MULTILINE)
PASSWORD_RE = re.compile(rb"[Pp]assword:\s*$")
# Root prompt heuristic. Matches at end-of-buffer: "<hostuser>@... # " or "$ ".
# No leading-newline anchor because the first prompt after login can land at
# the very start of what we've buffered so far. The working directory is part
# of the prompt, and a shell init starts directly sits in "/".
PROMPT_RE   = re.compile(rb"[-@\w.:~/]+[#$]\s*$")
# What can follow a typed username: a shell, a password prompt, or the login
# prompt again (the getty restarted, or the login was refused).
_AFTER_USER_RE = re.compile(b"|".join(p.pattern for p in (PROMPT_RE, PASSWORD_RE, LOGIN_RE)),
                            re.MULTILINE)
_AFTER_PASSWORD_RE = re.compile(b"|".join(p.pattern for p in (PROMPT_RE, LOGIN_RE)),
                                re.MULTILINE)

# ANSI CSI / OSC escape sequences (bracketed-paste, colors, cursor moves, etc.)
# Stripped on ingest so PROMPT_RE's `$` anchor isn't broken by invisibles.
_ANSI_CSI_RE = re.compile(rb"\x1b\[[0-9;?]*[A-Za-z]")
_ANSI_OSC_RE = re.compile(rb"\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)")


def _strip_ansi(b: bytes) -> bytes:
    return _ANSI_OSC_RE.sub(b"", _ANSI_CSI_RE.sub(b"", b))


@dataclass
class RunResult:
    cmd:    str
    stdout: str
    rc:     int


class ConsoleBusy(RuntimeError):
    """run() waited far past its own timeout for another operation.

    Not a TimeoutError: callers that resynchronise after a lost marker must
    not mistake overlapping console use, a harness bug, for line noise."""


# How much longer than its own timeout run() waits for the console.
BUSY_SLACK_S = 60.0


class Console:
    """Thin serial wrapper with send/expect + login + run-and-exit-code.

    Keeps a rolling read buffer so overlapping reads don't lose data.
    Logs all raw I/O to `log_path` if set, which is invaluable for
    debugging test failures after the fact.
    """

    def __init__(self, port: str, baud: int = DEFAULT_BAUD,
                 timeout_s: float = 5.0, log_path: str | None = None,
                 write_chunk_bytes: int = 0, write_pause_s: float = 0.003):
        assert write_chunk_bytes >= 0 and write_pause_s >= 0
        self.port      = port
        self.timeout_s = timeout_s
        self.write_chunk_bytes = write_chunk_bytes
        self.write_pause_s = write_pause_s
        self.buf       = b""
        # Callers name a log under an artifact directory a test may not have
        # written to yet, e.g. one set per run through ASK_FLOWTABLE_ARTIFACTS.
        if log_path:
            os.makedirs(os.path.dirname(os.path.abspath(log_path)), exist_ok=True)
        self.log_fp    = open(log_path, "ab", buffering=0) if log_path else None
        try:
            self.ser = serial.Serial(port=port, baudrate=baud, timeout=0)
        except BaseException:
            if self.log_fp:
                self.log_fp.close()
            raise
        self.lock      = port_lock(port)

    # --- factory helpers ------------------------------------------------

    @classmethod
    def target(cls, **kw) -> "Console":
        raw = kw.pop("raw", False)
        if _TARGET_SESSION is not None and not raw:
            from .serial import AgentConsole
            return AgentConsole(_TARGET_SESSION)
        if os.environ.get("ASK_TRANSPORT") == "ssh":
            from .lab import SSHConsole
            return SSHConsole("dut")
        # The physical UART has no flow control. KASAN/fault tracing can delay
        # its receiver enough to overrun a continuous command line. Leave
        # FIFO headroom and a scheduling gap; PTY-backed LAN consoles need
        # neither. Never replay a mutation after losing its result marker.
        kw.setdefault("write_chunk_bytes", 8)
        if not DEFAULT_TARGET_DEV:
            raise ValueError("set ASK_TARGET_DEV to the DUT serial device")
        return cls(port=DEFAULT_TARGET_DEV, **kw)

    @classmethod
    def lan(cls, domain: str | None = None, **kw) -> "Console":
        """Use dell1's management SSH connection in the selected data namespace."""
        if os.environ.get("ASK_TRANSPORT") == "ssh":
            from .lab import SSHConsole
            return SSHConsole("lan")
        raise ValueError("dell1 control requires the SSH bench profile")

    # --- primitives -----------------------------------------------------

    def send(self, data: bytes | str) -> None:
        if isinstance(data, str):
            data = data.encode()
        with self.lock:
            if self.log_fp:
                self.log_fp.write(b"<SEND>" + data + b"</SEND>\n")
            chunk = self.write_chunk_bytes or max(1, len(data))
            for start in range(0, len(data), chunk):
                self.ser.write(data[start:start + chunk])
                while True:
                    try:
                        self.ser.flush()
                        break
                    except termios.error as error:
                        # tcdrain can be interrupted by a child exiting.
                        # The bytes were written already; retry only the drain.
                        if error.args[0] != errno.EINTR:
                            raise
                if start + chunk < len(data):
                    time.sleep(self.write_pause_s)

    def expect(self, pattern: re.Pattern[bytes] | bytes | str,
               timeout: float | None = None) -> tuple[re.Match[bytes], bytes]:
        """Read until `pattern` matches the tail of the rolling buffer.

        Returns (match, before) where `before` is everything in the buffer
        up to (but not including) the match. The match itself and anything
        after it are kept in `self.buf` — so a follow-on expect() sees the
        post-match bytes, which is what you want for sequenced patterns.
        Raises TimeoutError if not seen in time.
        """
        if isinstance(pattern, (bytes, str)):
            pat = re.compile(pattern if isinstance(pattern, bytes) else pattern.encode())
        else:
            pat = pattern
        deadline = time.monotonic() + (timeout if timeout is not None else self.timeout_s)
        with self.lock:
            while True:
                self._pump(timeout_s=0.1)
                m = pat.search(self.buf)
                if m:
                    before = self.buf[:m.start()]
                    self.buf = self.buf[m.end():]
                    return m, before
                if time.monotonic() > deadline:
                    tail = self.buf[-400:].decode(errors="replace")
                    raise TimeoutError(
                        f"expect({pat.pattern!r}) timed out on {self.port}; "
                        f"tail: {tail!r}"
                    )

    def _pump(self, timeout_s: float) -> None:
        r, _, _ = select.select([self.ser.fileno()], [], [], timeout_s)
        if not r:
            return
        try:
            chunk = os.read(self.ser.fileno(), 4096)
        except BlockingIOError:
            # select can wake spuriously on some kernels / USB-serial drivers;
            # a non-blocking read then returns EAGAIN. Just try again next pump.
            return
        if not chunk:
            return
        if self.log_fp:
            self.log_fp.write(b"<RECV>" + chunk + b"</RECV>\n")
        # Append raw then strip over the whole buffer — a multi-byte escape
        # sequence can split across read() boundaries (e.g. `\x1b[` in one
        # chunk, `?2004l` in the next). Per-chunk strip would miss those.
        # Partial sequences at the tail survive this pass and get stripped
        # once the next pump completes them.
        self.buf = _strip_ansi(self.buf + chunk)

    # --- login / prompt sync -------------------------------------------

    def sync_prompt(self, tries: int = 3, timeout: float = 1.0) -> None:
        """Ping the console until we see a shell prompt.

        Sends a harmless Enter to jostle the remote into printing its
        prompt. Idempotent — safe to call multiple times.
        """
        with self.lock:
            for _ in range(tries):
                self.send(b"\n")
                try:
                    self.expect(PROMPT_RE, timeout=timeout)
                    return
                except TimeoutError:
                    continue
        raise TimeoutError(f"no prompt on {self.port} after {tries} tries")

    def login(self, user: str, password: str | None = None,
              timeout: float = 5.0) -> None:
        """Log in if sitting at a login prompt; no-op at an already-logged-in shell."""
        deadline = time.monotonic() + timeout
        with self.lock:
            self.send(b"\n")
            while time.monotonic() < deadline:
                self._pump(0.3)
                if PROMPT_RE.search(self.buf):
                    self.buf = b""
                    return
                if LOGIN_RE.search(self.buf):
                    self.buf = b""
                    self.send(user + "\n")
                    # A password prompt can appear for an account without a
                    # password too: a USB serial port hangs up as its host
                    # closes it, and a getty respawning under the typed name
                    # splits it between two logins. Answer it, and start
                    # again from a fresh login prompt.
                    match, _ = self.expect(_AFTER_USER_RE, timeout=10.0)
                    if PASSWORD_RE.search(match.group(0)):
                        self.send((password or "") + "\n")
                        match, _ = self.expect(_AFTER_PASSWORD_RE, timeout=10.0)
                    if PROMPT_RE.search(match.group(0)):
                        return
                    self.buf = match.group(0) + self.buf
        raise TimeoutError(f"login timed out on {self.port}")

    # --- command execution ---------------------------------------------

    def run(self, cmd: str, timeout: float = 10.0) -> RunResult:
        """Run `cmd` in the remote shell, return stdout + exit code.

        Uses a sentinel marker to disambiguate command output from the
        shell prompt echo — `echo __ASK_RC=$?__` after the command.
        """
        marker = f"__ASK_RC_{os.getpid()}_{int(time.monotonic() * 1e6)}__"
        wrapped = f"{cmd}; echo {marker}=$?\n"
        # Send and read as one unit: a second reader arriving between the two
        # would eat this command's marker and leave both waiting. A command
        # queued behind another waits out that one's timeout and prompt flush;
        # a holder that outlasts this command's own timeout by BUSY_SLACK_S is
        # instead holding the console for its lifetime -- a background peer
        # still running, say -- and waiting on would only hang the caller.
        wait = timeout + BUSY_SLACK_S
        if not self.lock.acquire(timeout=wait):
            raise ConsoleBusy(f"{self.port}: another operation held the console for {wait:g}s")
        try:
            self.send(wrapped)

            # Everything between "the shell's echo of our wrapped command"
            # and the marker is the real command output.
            marker_re = re.compile(rf"{re.escape(marker)}=(\-?\d+)".encode())
            m, before = self.expect(marker_re, timeout=timeout)
            rc = int(m.group(1))

            # Flush up to the next prompt so the buffer is clean for subsequent
            # commands; we ignore what's in there (just the post-marker prompt).
            try:
                self.expect(PROMPT_RE, timeout=5.0)
            except TimeoutError:
                pass
        finally:
            self.lock.release()

        raw = before.decode(errors="replace")
        out = _strip_echo(raw, wrapped)
        return RunResult(cmd=cmd, stdout=out, rc=rc)

    # --- ctx manager ---------------------------------------------------

    def close(self) -> None:
        try:
            self.ser.close()
        finally:
            if self.log_fp:
                self.log_fp.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


def _strip_echo(raw: str, wrapped_cmd: str) -> str:
    """Remove the shell's local echo of the sent command from output."""
    first_line = wrapped_cmd.rstrip("\n").splitlines()[0]
    # The echo appears followed by CR/LF. Find its end and drop everything up to there.
    for newline in ("\r\n", "\n"):
        idx = raw.find(first_line + newline)
        if idx >= 0:
            return raw[idx + len(first_line) + len(newline):]
    return raw


# --------------------------------------------------------------------- CLI

def _main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="ask_orch.uart",
        description="Scripted DUT/dell1 control or manual DUT UART recovery. "
                    "For an interactive terminal, use `tio` directly.",
    )
    p.add_argument("endpoint", choices=("target", "lan"))
    p.add_argument("--run", required=True,
                   help="command to run on the remote shell")
    p.add_argument("--login", help="user[:password] to log in as first")
    p.add_argument("--log", help="write raw I/O to this file")
    p.add_argument("--timeout", type=float, default=5.0)
    args = p.parse_args(argv)

    factory = Console.target if args.endpoint == "target" else Console.lan
    con = factory(log_path=args.log)
    try:
        if args.login:
            user, _, pw = args.login.partition(":")
            con.login(user, pw if pw else None)
        else:
            con.sync_prompt()

        r = con.run(args.run, timeout=args.timeout)
        sys.stdout.write(r.stdout)
        return r.rc
    finally:
        con.close()


if __name__ == "__main__":
    sys.exit(_main())
