"""Host subprocesses must finish or release their child on timeout."""

import subprocess


def run_process(*args, timeout=120, **kwargs):
    return subprocess.run(*args, timeout=timeout, **kwargs)
