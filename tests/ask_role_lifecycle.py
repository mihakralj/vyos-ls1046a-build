"""Host harness for ASK admin disengage and bridge role reconciliation."""

from pathlib import Path
import subprocess

import pytest

pytestmark = pytest.mark.host
ROOT = Path(__file__).resolve().parents[1]
ASK = ROOT / "kernel/ask/oot-modules/ask"
HARNESS = ROOT / "tests/dut/ask_role_lifecycle.c"


def section(source, start, end):
    return source[source.index(start):source.index(end, source.index(start))]


def test_role_lifecycle(tmp_path):
    hw = (ASK / "ask_hw.c").read_text()
    bridge = (ASK / "ask_bridge.c").read_text()
    (tmp_path / "role_hw.h").write_text(
        section(hw, "int ask_hw_offload_role_prepare(",
                "\nint ask_hw_offload_engage_role(")
    )
    (tmp_path / "role_admin.h").write_text(
        section(hw, "static int ask_hw_offload_quiesce_port(u8",
                "\nEXPORT_SYMBOL_GPL(ask_hw_offload_disengage_admin);")
    )
    (tmp_path / "role_bridge_state.h").write_text(
        section(bridge, "struct ask_bridge_role_state {",
                "\nstatic u64 ask_bridge_role_generation;")
        + "\nstatic u64 ask_bridge_role_generation;\n"
    )
    (tmp_path / "role_bridge.h").write_text(
        section(bridge, "static void ask_bridge_reconcile_port(",
                "\nstatic struct notifier_block ask_bridge_netdev_nb")
    )
    binary = tmp_path / "ask_role_lifecycle"
    subprocess.run(
        ["cc", "-std=gnu11", "-Wall", "-Wextra", "-Werror",
         "-Wno-unused-parameter", "-Wno-unused-function", "-Wno-type-limits",
         "-I", str(tmp_path), str(HARNESS), "-o", str(binary)],
        check=True,
    )
    subprocess.run([str(binary)], check=True, timeout=30)
