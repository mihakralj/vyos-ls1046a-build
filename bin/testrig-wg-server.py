#!/usr/bin/env python3
"""Start and identify a uniquely owned WireGuard benchmark server."""

import os
from pathlib import Path
import re
import select
import signal
import stat
import subprocess
import sys
import time


WRAPPER = r"""
import os, sys
from pathlib import Path
directory, port = sys.argv[1:]
data = Path("/proc/self/stat").read_text()
started = data[data.rfind(")") + 2:].split()[19]
fd = os.open(directory + "/identity", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
with os.fdopen(fd, "w") as out:
    out.write(f"{os.getpid()}:{started}:{port}\n")
    out.flush()
    os.fsync(out.fileno())
os.execvp("iperf3", ["iperf3", "-s", "-p", port])
"""


class StartFailed(RuntimeError):
    """The failed start was cleaned up without leaving an owned process."""


def directory_arg(value):
    directory = Path(value)
    if (directory.parent != Path("/tmp") or
            not re.fullmatch(r"wgrig-srv-[0-9a-f]{32}-[a-z0-9]+", directory.name)):
        raise ValueError("invalid server ownership directory")
    return directory


def check_directory(directory):
    info = directory.lstat()
    if (not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid() or
            stat.S_IMODE(info.st_mode) != 0o700):
        raise ValueError("invalid server ownership directory")


def identity(directory, port):
    check_directory(directory)
    path = directory / "identity"
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid():
        raise ValueError("invalid server identity file")
    match = re.fullmatch(r"([0-9]+):([0-9]+):([0-9]+)\n", path.read_text())
    if not match or int(match[1]) <= 1 or int(match[3]) != port:
        raise ValueError("invalid server process identity")
    return int(match[1]), int(match[2])


def proc_state(pid):
    data = Path(f"/proc/{pid}/stat").read_text()
    fields = data[data.rfind(")") + 2:].split()
    return fields[0], int(fields[19])


def listening(pid, port):
    try:
        sockets = {
            os.readlink(path)[8:-1]
            for path in Path(f"/proc/{pid}/fd").iterdir()
            if os.readlink(path).startswith("socket:[")
        }
    except FileNotFoundError:
        return False
    for name in ("tcp", "tcp6"):
        for line in Path(f"/proc/net/{name}").read_text().splitlines()[1:]:
            fields = line.split()
            if (fields[3] == "0A" and int(fields[1].split(":")[1], 16) == port
                    and fields[9] in sockets):
                return True
    return False


def remove(directory, port):
    try:
        pid, started = identity(directory, port)
    except FileNotFoundError:
        if not directory.exists():
            return
        raise ValueError("server directory has no identity")
    try:
        state, current = proc_state(pid)
    except FileNotFoundError:
        pass
    else:
        if current == started and state != "Z":
            raise RuntimeError("owned server is still running")
    if {entry.name for entry in directory.iterdir()} != {"identity", "log"}:
        raise ValueError("server directory contains unexpected files")
    (directory / "identity").unlink()
    (directory / "log").unlink()
    directory.rmdir()


def start(directory, port):
    try:
        directory.mkdir(mode=0o700)
    except FileExistsError:
        raise FileExistsError("server ownership directory already exists")
    child = None
    pidfd = None
    try:
        with (directory / "log").open("x") as log:
            child = subprocess.Popen(
                [sys.executable, "-c", WRAPPER, str(directory), str(port)],
                stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                start_new_session=True,
            )
        pidfd = os.pidfd_open(child.pid)
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if child.poll() is not None:
                raise RuntimeError("iperf3 exited before listening")
            try:
                pid, _ = identity(directory, port)
                executable = Path(f"/proc/{pid}/exe").resolve().name
                if pid == child.pid and executable == "iperf3" and listening(pid, port):
                    return
            except FileNotFoundError:
                pass
            time.sleep(0.05)
        raise RuntimeError("iperf3 did not become ready")
    except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as err:
        if pidfd is not None:
            try:
                signal.pidfd_send_signal(pidfd, signal.SIGTERM)
                poller = select.poll()
                poller.register(pidfd, select.POLLIN)
                if not poller.poll(2000):
                    signal.pidfd_send_signal(pidfd, signal.SIGKILL)
                    if not poller.poll(2000):
                        raise RuntimeError("failed to stop incomplete server")
            except ProcessLookupError:
                pass
        if child is not None:
            child.wait(timeout=2)
        if (directory / "identity").exists():
            remove(directory, port)
        elif {entry.name for entry in directory.iterdir()} <= {"log"}:
            (directory / "log").unlink(missing_ok=True)
            directory.rmdir()
        else:
            raise RuntimeError("incomplete server requires manual inspection")
        raise StartFailed(str(err)) from err
    finally:
        if pidfd is not None:
            os.close(pidfd)


def main():
    if len(sys.argv) != 4:
        raise ValueError("expected start|identity|remove, ownership directory and port")
    operation, path, port_text = sys.argv[1:]
    directory = directory_arg(path)
    if not port_text.isdecimal() or not 1 <= int(port_text) <= 65535:
        raise ValueError("invalid server port")
    port = int(port_text)
    if operation == "start":
        start(directory, port)
    elif operation == "identity":
        pid, started = identity(directory, port)
        print(f"{pid}:{started}:{port}")
    elif operation == "remove":
        remove(directory, port)
    else:
        raise ValueError("invalid server operation")


if __name__ == "__main__":
    try:
        main()
    except FileExistsError as err:
        print(f"ERROR: {err}", file=sys.stderr)
        sys.exit(10)
    except StartFailed as err:
        print(f"ERROR: {err}", file=sys.stderr)
        sys.exit(11)
    except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as err:
        print(f"ERROR: {err}", file=sys.stderr)
        sys.exit(1)
