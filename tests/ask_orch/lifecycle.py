"""Bounded cleanup and exclusive ownership of a physical test bench."""

import asyncio
import fcntl
import hashlib
import os
from contextlib import contextmanager
from pathlib import Path


def checked(result):
    """Accept command results only when the operation succeeded."""
    if isinstance(result, dict):
        assert result.get("rc", 0) == 0 and result.get("errno", 0) == 0, result
    elif hasattr(result, "rc"):
        assert result.rc == 0, result
    return result


class CleanupStack:
    """LIFO callbacks; finish every undo and then report all failures."""

    def __init__(self, timeout=45):
        self._cleanups = []
        self.timeout = timeout

    def push(self, cleanup):
        self._cleanups.append(cleanup)

    async def teardown(self, label="topology"):
        failures = []
        while self._cleanups:
            cleanup = self._cleanups.pop()
            try:
                async with asyncio.timeout(self.timeout):
                    checked(await cleanup())
            except BaseException as error:
                if isinstance(
                    error, (KeyboardInterrupt, SystemExit, asyncio.CancelledError)
                ):
                    raise
                # pytest.fail is a BaseException, too. It must not prevent
                # another resource's cleanup from running.
                failure = RuntimeError(f"{label}: {error}")
                failure.__cause__ = error
                failures.append(failure)
        if failures:
            raise ExceptionGroup(f"{label} restoration failed", failures)


@contextmanager
def bench_lock(resources, directory=Path("/tmp/ask-bench-locks")):
    """Lock each shared resource across runner processes on this host.

    ponytail: local flock; use a lab coordinator if multiple orchestrator
    machines can control the same board.
    """
    directory.mkdir(mode=0o1777, exist_ok=True)
    handles = []
    try:
        for resource in sorted(set(resources)):
            name = hashlib.sha256(resource.encode()).hexdigest()
            fd = os.open(
                directory / name, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o666
            )
            handle = os.fdopen(fd, "r+")
            handles.append(handle)
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as error:
                raise RuntimeError(
                    f"test bench is already in use: {resource}"
                ) from error
            handle.seek(0)
            handle.truncate()
            handle.write(f"pid={os.getpid()} {resource}\n")
            handle.flush()
        yield
    finally:
        for handle in reversed(handles):
            handle.close()
