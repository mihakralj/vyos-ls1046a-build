"""Host regression for the S6 static volume gate, ask_offload_too_small() (ask_flow_offload.c).

Runs tests/dut/ask_offload_volume.c against the real function: off at 0/0; admit when either the packet or
the byte threshold is met, counted over both directions so both cookies of a connection decide alike; fail
open (with a warning) without conntrack accounting or with a bad cookie.
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
    start = src.index("static bool ask_offload_too_small(")
    end = src.index("\n}\n", start) + 3
    return src[start:end]


def test_offload_volume_gate(tmp_path):
    (tmp_path / "ask_offload_volume_body.h").write_text(body())
    exe = tmp_path / "ask-offload-volume"
    subprocess.run(
        [*shlex.split(os.environ.get("HOSTCC", "cc")), "-std=gnu11", "-Wall", "-Wextra", "-Werror",
         "-Wno-unused-parameter", "-Wno-unused-function", *shlex.split(os.environ.get("HOSTCFLAGS", "")),
         "-I", str(tmp_path), str(ROOT / "tests/dut/ask_offload_volume.c"), "-o", str(exe)],
        check=True)
    out = subprocess.run([str(exe)], capture_output=True, text=True, check=True)
    assert "ask_offload_volume: OK" in out.stdout
