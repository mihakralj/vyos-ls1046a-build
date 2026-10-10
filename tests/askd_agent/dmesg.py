"""Kernel-log capture with explicit completeness and sequence validation."""

from __future__ import annotations

import os
import re
import asyncio
import json
import tempfile
from pathlib import Path

KMSG_PATH = Path("/dev/kmsg")

# Kernel-splat banner patterns. Match the *first line* of a kernel
# sanitiser/lockdep/BUG report; the agent reports the line as an
# instant-failure signal up to the orchestrator's splat_window.
SPLAT_RE = re.compile(
    r"(BUG: KASAN|"
    r"KFENCE: \w+|"
    r"==============|"          # KASAN/KFENCE/UBSAN banner separator
    r"UBSAN:|"
    r"WARNING:|"                # WARN_ON family + bad-unlock-balance etc.
    r"(?<!DE)BUG: |"            # generic kernel BUG(); not "ASK-DEBUG: " tracing
    r"kernel BUG at|"           # BUG_ON
    r"Oops:|"                   # NULL deref / page fault
    r"Kernel panic|inconsistent lock state|"
    r"Unable to handle|"        # arm64 page-fault banner
    r"INFO: possible (recursive locking|circular locking|"
    r"irq lock inversion)|"     # lockdep dependency reports
    r"INFO: trying to register non-static key|"
    r"inconsistent.*usage)"     # lockdep state-mismatch
)

def open_at_tail() -> int:
    """Open a capture, or fail rather than create an unobservable window."""
    fd = os.open(KMSG_PATH, os.O_RDONLY | os.O_NONBLOCK)
    try:
        os.lseek(fd, 0, os.SEEK_END)
    except BaseException:
        os.close(fd)
        raise
    return fd


def drain(fd: int | None, *, cursor=None, boot=False) -> dict:
    """Retain partial evidence, but never label missing records complete."""
    result = {"complete": True, "error": None, "lines": [], "cursor": cursor}
    if fd is None:
        return {**result, "complete": False, "error": "kernel log is unavailable"}
    previous = None
    while True:
        try:
            chunk = os.read(fd, 65536)
        except BlockingIOError:
            break
        except OSError as error:
            result.update(complete=False, error=f"kernel log read failed: {error}")
            break
        if not chunk:
            break
        try:
            header, message = chunk.split(b";", 1)
            sequence = int(header.split(b",")[1])
        except (ValueError, IndexError):
            result.update(complete=False, error="malformed kernel log record")
            break
        if previous is None and boot and sequence > 1:
            result.update(complete=False, error="boot log has already been overwritten")
        if previous is not None and sequence != previous + 1:
            result.update(complete=False, error="kernel log sequence gap")
        if previous is None and cursor is not None and sequence > cursor + 1:
            result.update(complete=False, error="kernel log cursor has been overwritten")
        previous = sequence
        if cursor is None or sequence > cursor:
            result["lines"].extend(message.decode("utf-8", "replace").splitlines())
            result["cursor"] = sequence
    if boot and previous is None:
        result.update(complete=False, error="boot log is empty")
    return result


def close(fd: int | None) -> None:
    if fd is not None:
        os.close(fd)


def read_since(cursor: int | None) -> dict:
    """Read the retained boot log, or records following an explicit cursor."""
    try:
        fd = os.open(KMSG_PATH, os.O_RDONLY | os.O_NONBLOCK)
    except OSError as error:
        return {"complete": False, "error": str(error), "lines": [], "cursor": cursor}
    try:
        return drain(fd, cursor=cursor, boot=cursor is None)
    finally:
        close(fd)


def has_splat(lines: list[str]) -> list[str]:
    return [line for line in lines if SPLAT_RE.search(line)]


class Window:
    """Drain locally while a test runs; serial throughput never gates kmsg."""

    def __init__(self, fd):
        self.fd = fd
        self.cursor = None
        self.complete, self.error = True, None
        self.log = tempfile.TemporaryFile(mode="w+t")
        self.size = 0
        self.stop = asyncio.Event()
        self.task = asyncio.create_task(self._collect())

    async def _collect(self):
        while True:
            window = await asyncio.to_thread(drain, self.fd, cursor=self.cursor)
            self.cursor = window["cursor"]
            if not window["complete"]:
                self.complete, self.error = False, window["error"]
            data = "".join(json.dumps(line) + "\n" for line in window["lines"])
            self.size += len(data.encode())
            if self.size > 16 << 20:
                self.complete, self.error = False, "kernel capture exceeded its 16 MiB budget"
            else:
                self.log.write(data)
            if self.stop.is_set():
                return
            try:
                await asyncio.wait_for(self.stop.wait(), 0.2)
            except TimeoutError:
                pass

    async def finish(self):
        self.stop.set()
        try:
            await self.task
            self.log.seek(0)
            return {"complete": self.complete, "error": self.error,
                    "lines": [json.loads(line) for line in self.log], "cursor": self.cursor}
        finally:
            close(self.fd)
            self.log.close()


BOOT_LOG = Path("/tmp/ask-boot-kernel.jsonl")


def record_boot():
    """The boot service retains kernel evidence without opening a network port."""
    import fcntl
    import time

    boot = Path("/proc/sys/kernel/random/boot_id").read_text().strip()
    fd = os.open(KMSG_PATH, os.O_RDONLY | os.O_NONBLOCK)
    cursor = None
    try:
        with BOOT_LOG.open("w") as log:
            log.write(json.dumps({"boot_id": boot}) + "\n")
            log.flush()
            while True:
                window = drain(fd, cursor=cursor, boot=cursor is None)
                cursor = window["cursor"]
                if window["lines"] or not window["complete"]:
                    fcntl.flock(log, fcntl.LOCK_EX)
                    try:
                        if log.tell() > 64 << 20:
                            log.write(json.dumps({"complete": False, "error": "boot log budget exhausted", "lines": []}) + "\n")
                            log.flush()
                            return
                        log.write(json.dumps(window) + "\n")
                        log.flush()
                    finally:
                        fcntl.flock(log, fcntl.LOCK_UN)
                time.sleep(0.2)
    finally:
        close(fd)


def retained_boot():
    import fcntl

    if not BOOT_LOG.exists():
        return read_since(None)
    result = {"complete": True, "error": None, "lines": [], "cursor": None}
    with BOOT_LOG.open() as log:
        fcntl.flock(log, fcntl.LOCK_SH)
        header = json.loads(next(log))
        if header["boot_id"] != Path("/proc/sys/kernel/random/boot_id").read_text().strip():
            raise RuntimeError("retained kernel log belongs to another boot")
        for line in log:
            window = json.loads(line)
            result["lines"].extend(window["lines"])
            result["cursor"] = window.get("cursor", result["cursor"])
            if not window["complete"]:
                result.update(complete=False, error=window["error"])
    # Include the tail since the logger's last flush; read_since checks gaps.
    tail = read_since(result["cursor"])
    result["lines"].extend(tail["lines"])
    result["cursor"] = tail["cursor"]
    if not tail["complete"]:
        result.update(complete=False, error=tail["error"])
    return result
