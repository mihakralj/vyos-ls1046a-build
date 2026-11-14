"""Host regression for the S7 lock-free fast path for never-installed cookies (ask_flow_offload.c).

Extracts the real ask_flow_cookie_unknown(), ask_flow_replace_deferred_unlocked() and
ask_flow_destroy_unknown_unlocked() and runs tests/dut/ask_flow_unknown_fast.c against them:
an owner-only cookie, a generation or flow, an unbound block and a NULL cb_priv must take the
locked path without storing a first-seen timestamp; only an unknown, bound cookie that is too young or (S6)
below the volume threshold is rejected without the lock; DESTROY of an unknown cookie erases first-seen whatever the delay.
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
    start = src.index("static bool ask_flow_cookie_unknown(")
    end_fn = src.index("static bool ask_flow_destroy_unknown_unlocked(")
    end = src.index("\n}\n", end_fn) + 3
    return src[start:end]


def test_unknown_cookie_fast_path(tmp_path):
    (tmp_path / "ask_flow_unknown_fast_body.h").write_text(body())
    exe = tmp_path / "ask-flow-unknown-fast"
    subprocess.run(
        [*shlex.split(os.environ.get("HOSTCC", "cc")), "-std=gnu11", "-Wall", "-Wextra", "-Werror",
         "-Wno-unused-parameter", "-Wno-unused-function", *shlex.split(os.environ.get("HOSTCFLAGS", "")),
         "-I", str(tmp_path), str(ROOT / "tests/dut/ask_flow_unknown_fast.c"), "-o", str(exe)],
        check=True)
    out = subprocess.run([str(exe)], capture_output=True, text=True, check=True)
    assert "ask_flow_unknown_fast: OK" in out.stdout
