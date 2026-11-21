"""Host fault-injection tests for T1(a) FIB/dev/FDB retirement retry and error accounting."""

from pathlib import Path
import os
import shlex
import subprocess

import pytest


pytestmark = pytest.mark.host
ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "kernel/ask/oot-modules/ask/ask_flow_offload.c"
HARNESS = ROOT / "tests/dut/ask_retire_fault.c"


def _slice(source, start, end):
    i = source.index(start)
    j = source.index(end, i)
    return source[i:j]


def test_retire_workers_retry_on_oom_and_account_incomplete(tmp_path):
    source = SOURCE.read_text()
    flush_body = (
        _slice(source, "struct ask_flush_victim {", "\nstatic int ask_pppoe_collect(")
        + "\n"
        + _slice(source, "/* Process context only.", "\nstatic int ask_vlan_collect(")
    )
    workers_body = _slice(
        source,
        "#define ASK_DEVCHG_MAX",
        "\n/* IPv4 calls this under RTNL",
    )
    (tmp_path / "ask_retire_flush_body.h").write_text(flush_body)
    (tmp_path / "ask_retire_workers_body.h").write_text(workers_body)

    binary = tmp_path / "ask_retire_fault"
    subprocess.run(
        [
            *shlex.split(os.environ.get("HOSTCC", "cc")),
            "-std=gnu11", "-Wall", "-Wextra", "-Werror",
            "-Wno-unused-parameter", "-Wno-unused-function",
            *shlex.split(os.environ.get("HOSTCFLAGS", "")),
            "-I", str(tmp_path), str(HARNESS), "-o", str(binary),
        ],
        check=True,
    )
    out = subprocess.run([str(binary)], capture_output=True, text=True, check=True, timeout=30)
    assert "ask_retire_fault: OK" in out.stdout
