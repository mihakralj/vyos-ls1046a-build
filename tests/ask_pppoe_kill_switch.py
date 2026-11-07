"""Host fault tests for the ASK PPPoE offload switch."""

from pathlib import Path
import subprocess

import pytest


pytestmark = pytest.mark.host
ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "kernel/ask/oot-modules/ask/ask_hw.c"
HARNESS = ROOT / "tests/dut/ask_pppoe_kill_switch.c"


def test_pppoe_switch_reports_incomplete_withdrawal(tmp_path):
    source = SOURCE.read_text()
    start = source.index("static int ask_pppoe_offload_set(")
    end = source.index("\nstatic const struct kernel_param_ops", start)
    body = source[start:end]
    start = source.index("void ask_hw_set_ready(bool ready)")
    end = source.index("\nvoid ask_hw_exit(", start)
    body += "\n" + source[start:end]
    (tmp_path / "pppoe_switch_body.h").write_text(body)
    binary = tmp_path / "ask_pppoe_kill_switch"
    subprocess.run(
        [
            "cc", "-std=gnu11", "-Wall", "-Wextra", "-Werror", "-pthread",
            "-Wno-unused-function", "-Wno-unused-parameter",
            "-I", str(tmp_path), str(HARNESS), "-o", str(binary),
        ],
        check=True,
    )
    subprocess.run([str(binary)], check=True, timeout=30)
