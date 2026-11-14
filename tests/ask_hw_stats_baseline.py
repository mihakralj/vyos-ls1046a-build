"""Host regression for ask_flow_set_hw_stats() (ask_flow.c) with the F-315 stats cache.

A cached counter read can be older than a previous direct read: it must report no change and keep the
baseline, so the next fresh read does not count the gap twice (tests/dut/ask_hw_stats_baseline.c).
"""
from pathlib import Path
import os
import shlex
import subprocess

import pytest

pytestmark = pytest.mark.host
ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "kernel/ask/oot-modules/ask/ask_flow.c"


def body():
    src = SOURCE.read_text()
    start = src.index("void ask_flow_set_hw_stats(")
    end = src.index("\n}\n", start) + 3
    return src[start:end]


def test_hw_stats_baseline(tmp_path):
    (tmp_path / "ask_hw_stats_body.h").write_text(body())
    exe = tmp_path / "ask-hw-stats-baseline"
    subprocess.run(
        [*shlex.split(os.environ.get("HOSTCC", "cc")), "-std=gnu11", "-Wall", "-Wextra", "-Werror",
         "-Wno-unused-parameter", "-Wno-unused-function", *shlex.split(os.environ.get("HOSTCFLAGS", "")),
         "-I", str(tmp_path), str(ROOT / "tests/dut/ask_hw_stats_baseline.c"), "-o", str(exe)],
        check=True)
    out = subprocess.run([str(exe)], capture_output=True, text=True, check=True)
    assert "ask_hw_stats_baseline: OK" in out.stdout
