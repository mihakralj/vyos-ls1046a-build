"""Host fault injection for bridge-record and FDB ownership."""

from pathlib import Path
import subprocess

import pytest

pytestmark = pytest.mark.host
ROOT = Path(__file__).resolve().parents[1]
BRIDGE = ROOT / "kernel/ask/oot-modules/ask/ask_bridge.c"
HW = ROOT / "kernel/ask/oot-modules/ask/ask_hw.c"
HARNESS = ROOT / "tests/dut/ask_bridge_ownership.c"


def section(source, start, end):
    return source[source.index(start):source.index(end, source.index(start))]


def test_bridge_record_identity_and_unsettled_ownership(tmp_path):
    bridge = BRIDGE.read_text()
    hw = HW.read_text()
    (tmp_path / "bridge_type.h").write_text(
        section(bridge, "struct ask_bridge_rec {", "\n\n/* A learned SA")
    )
    (tmp_path / "bridge_hw.h").write_text(
        section(hw, "int ask_hw_l2_record(", "\nvoid ask_hw_prewarm_egress_fq(")
    )
    (tmp_path / "bridge_switch.h").write_text(
        section(hw, "static int ask_bridge_offload_set(",
                "\nstatic const struct kernel_param_ops")
    )
    (tmp_path / "bridge_debug.h").write_text(
        section(hw, "int ask_hw_b2_l2_record(",
                "\nstruct ask_hw_fe_transition")
    )
    (tmp_path / "bridge_delete.h").write_text(
        section(bridge, "static bool ask_bridge_sa_in_use(",
                "\n/* Send one FDB notification")
        + "\n"
        + section(bridge, "static int ask_bridge_rec_del(",
                  "\n/* Install a record with ingress-form")
    )
    (tmp_path / "bridge_admit.h").write_text(
        section(bridge, "static void ask_bridge_admit_key(",
                "\n/*\n * A candidate carries")
    )
    (tmp_path / "bridge_work.h").write_text(
        section(bridge, "static void ask_bridge_work_fn(",
                "\n/* Delete records idle")
    )
    (tmp_path / "bridge_age.h").write_text(
        section(bridge, "static void ask_bridge_age_fn(",
                "\n/* A void disengage result")
    )
    (tmp_path / "bridge_forget.h").write_text(
        section(bridge, "void ask_bridge_forget_port(",
                "\n/*\n * NF_BR_FORWARD")
    )
    binary = tmp_path / "ask_bridge_ownership"
    subprocess.run(
        [
            "cc", "-std=gnu11", "-Wall", "-Wextra", "-Werror", "-pthread",
            "-Wno-unused-parameter", "-I", str(tmp_path),
            str(HARNESS), "-o", str(binary),
        ],
        check=True,
    )
    subprocess.run([str(binary)], check=True, timeout=30)
