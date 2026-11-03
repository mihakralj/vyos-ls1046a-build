#!/usr/bin/env python3
"""Stop one previously recorded iperf process without racing PID reuse."""

import os
from pathlib import Path
import select
import signal
import sys


def stop(pid, started, role, port):
    if pid <= 1 or started < 0 or role not in ("server", "client") or not 1 <= port <= 65535:
        raise ValueError("invalid process identity")
    try:
        fd = os.pidfd_open(pid)
    except ProcessLookupError:
        return
    try:
        poller = select.poll()
        poller.register(fd, select.POLLIN)
        if poller.poll(0):
            return
        try:
            data = Path(f"/proc/{pid}/stat").read_text()
            fields = data[data.rfind(")") + 2:].split()
            if len(fields) <= 19 or not fields[19].isdecimal():
                raise ValueError(f"invalid process state for PID {pid}")
            if int(fields[19]) != started:
                raise ValueError(f"PID {pid} was reused; not signalling it")
            executable = Path(f"/proc/{pid}/exe").resolve().name
            args = Path(f"/proc/{pid}/cmdline").read_bytes().split(b"\0")
        except FileNotFoundError:
            if poller.poll(0):
                return
            raise
        mode = b"-s" if role == "server" else b"-c"
        if (executable != "iperf3" or mode not in args or b"-p" not in args or
                args[args.index(b"-p") + 1:args.index(b"-p") + 2] != [str(port).encode()]):
            raise ValueError(f"PID {pid} does not match the owned {role} command")
        try:
            signal.pidfd_send_signal(fd, signal.SIGTERM)
        except ProcessLookupError:
            return
        if not poller.poll(2000):
            try:
                signal.pidfd_send_signal(fd, signal.SIGKILL)
            except ProcessLookupError:
                return
            if not poller.poll(2000):
                raise RuntimeError(f"owned PID {pid} did not exit")
    finally:
        os.close(fd)


if __name__ == "__main__":
    try:
        if len(sys.argv) != 5:
            raise ValueError("expected PID, start time, role and port")
        stop(int(sys.argv[1]), int(sys.argv[2]), sys.argv[3], int(sys.argv[4]))
    except (OSError, ValueError, RuntimeError) as err:
        print(f"ERROR: {err}", file=sys.stderr)
        sys.exit(1)
