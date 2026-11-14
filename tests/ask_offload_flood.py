"""Host regression for the flood-adaptive age gate (ask_flow_offload.c).

Runs tests/dut/ask_offload_flood.c against the real rate window and ask_offload_too_young(): at a low
new-connection rate the configured delay (default 0) applies; above ask.offload_flood_rate the gate is at
least 500 ms and lifts once the flood ends; flood_rate 0 is the fixed gate alone; no false flood at boot.
"""
from pathlib import Path
import os
import shlex
import subprocess

import pytest

pytestmark = pytest.mark.host
ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "kernel/ask/oot-modules/ask/ask_flow_offload.c"


def body():
    src = SOURCE.read_text()
    start = src.index("static unsigned int ask_offload_flood_rate")
    end = src.index("\n}\n", src.index("static bool ask_offload_too_young(")) + 3
    return src[start:end]


def test_offload_flood_gate(tmp_path):
    (tmp_path / "ask_offload_flood_body.h").write_text(body())
    exe = tmp_path / "ask-offload-flood"
    subprocess.run(
        [*shlex.split(os.environ.get("HOSTCC", "cc")), "-std=gnu11", "-Wall", "-Wextra", "-Werror",
         "-Wno-unused-parameter", "-Wno-unused-function", *shlex.split(os.environ.get("HOSTCFLAGS", "")),
         "-I", str(tmp_path), str(ROOT / "tests/dut/ask_offload_flood.c"), "-o", str(exe)],
        check=True)
    out = subprocess.run([str(exe)], capture_output=True, text=True, check=True)
    assert "ask_offload_flood: OK" in out.stdout
