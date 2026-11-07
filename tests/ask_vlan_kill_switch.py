"""Host fault tests for ASK VLAN and bridge offload kill switches."""

from pathlib import Path
import subprocess

import pytest


pytestmark = pytest.mark.host
ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "kernel/ask/oot-modules/ask/ask_hw.c"
HARNESS = ROOT / "tests/dut/ask_vlan_kill_switch.c"


def test_capability_switches_report_incomplete_withdrawal(tmp_path):
    source = SOURCE.read_text()
    start = source.index("static int ask_vlan_offload_set(")
    end = source.index("\nstatic const struct kernel_param_ops", start)
    body = source[start:end]
    legacy = "static void ask_hw_vlan_disarm_all("
    if legacy in source:
        body = source[source.index(legacy):start] + body
    start = source.index("void ask_hw_set_ready(bool ready)")
    end = source.index("\nvoid ask_hw_exit(", start)
    body += "\n" + source[start:end]
    start = source.index("static int ask_bridge_offload_set(")
    end = source.index("\nstatic const struct kernel_param_ops", start)
    body += "\n" + source[start:end]
    (tmp_path / "vlan_switch_body.h").write_text(body)
    binary = tmp_path / "ask_vlan_kill_switch"
    subprocess.run(
        [
            "cc", "-std=gnu11", "-Wall", "-Wextra", "-Werror", "-pthread",
            "-Wno-unused-function", "-Wno-unused-parameter",
            "-I", str(tmp_path), str(HARNESS), "-o", str(binary),
        ],
        check=True,
    )
    subprocess.run([str(binary)], check=True, timeout=30)
