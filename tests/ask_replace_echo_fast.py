"""Host regression for S8(3) early egress-side echo skip (ask_flow_offload.c)."""

from pathlib import Path
import os
import shlex
import subprocess

import pytest


pytestmark = pytest.mark.host
ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "kernel/ask/oot-modules/ask/ask_flow_offload.c"
HARNESS = ROOT / "tests/dut/ask_replace_echo_fast.c"


def test_replace_echo_skip_before_lock(tmp_path):
    src = SOURCE.read_text()
    start = src.index("static bool ask_flow_cookie_unsettled(")
    end_fn = src.index("static bool ask_flow_replace_echo_unlocked(", start)
    end = src.index("\n}\n", end_fn) + 3
    body = src[start:end]

    cb = src[src.index("int ask_flow_offload_setup_tc_block_cb("):]
    cb = cb[:cb.index("\n}\n") + 3]
    echo_pos = cb.index("ask_flow_replace_echo_unlocked(")
    lock_pos = cb.index("mutex_lock(&ask_flow_lifecycle_lock);")
    assert echo_pos < lock_pos
    assert "ask_flow_cookie_unsettled(" in cb[:lock_pos]

    (tmp_path / "replace_echo_body.h").write_text(body)
    exe = tmp_path / "ask-replace-echo-fast"
    subprocess.run(
        [*shlex.split(os.environ.get("HOSTCC", "cc")), "-std=gnu11",
         "-Wall", "-Wextra", "-Werror",
         "-Wno-unused-parameter", "-Wno-unused-function",
         *shlex.split(os.environ.get("HOSTCFLAGS", "")),
         "-I", str(tmp_path), str(HARNESS), "-o", str(exe)],
        check=True,
    )
    out = subprocess.run([str(exe)], capture_output=True, text=True, check=True, timeout=30)
    assert "ask_replace_echo_fast: OK" in out.stdout
