"""Host regression for the per-port offload counters (kernel/ask/oot-modules/ask/ask_stats.c).

The counters must be per-CPU: on the A72 (no LSE atomics) shared atomic64 counters cost 3 % of CPU
in LL/SC retries with four stats workers at 65536 flows. Runs tests/dut/ask_port_stats.c against
the real add/zero/get/exit bodies: per-CPU sums, zero as a baseline, no leaks at exit.
"""
from pathlib import Path
import os
import shlex
import subprocess

import pytest

pytestmark = pytest.mark.host
ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "kernel/ask/oot-modules/ask/ask_stats.c"


def test_port_stats_are_per_cpu():
    src = SOURCE.read_text()
    assert "atomic64_add(" not in src and "this_cpu_add(st->" not in src and "get_cpu_ptr(st->pcpu)" in src


def test_port_stats_semantics(tmp_path):
    src = SOURCE.read_text()
    (tmp_path / "ask_port_stats_body.h").write_text(src[src.index("struct ask_port_ctr {"):])
    exe = tmp_path / "ask-port-stats"
    subprocess.run(
        [*shlex.split(os.environ.get("HOSTCC", "cc")), "-std=gnu11", "-Wall", "-Wextra", "-Werror",
         "-Wno-unused-parameter", "-Wno-unused-function", *shlex.split(os.environ.get("HOSTCFLAGS", "")),
         "-I", str(tmp_path), str(ROOT / "tests/dut/ask_port_stats.c"), "-o", str(exe)],
        check=True)
    out = subprocess.run([str(exe)], capture_output=True, text=True, check=True)
    assert "ask_port_stats: OK" in out.stdout
