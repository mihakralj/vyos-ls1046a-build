"""Host fault tests for NAT44, NAT66, and IPv6 HW-frag kill switches (T1a/T1e)."""

from pathlib import Path
import os
import shlex
import subprocess

import pytest


pytestmark = pytest.mark.host
ROOT = Path(__file__).resolve().parents[1]
HW_SOURCE = ROOT / "kernel/ask/oot-modules/ask/ask_hw.c"
FO_SOURCE = ROOT / "kernel/ask/oot-modules/ask/ask_flow_offload.c"
HARNESS = ROOT / "tests/dut/ask_nat_frag_kill_switch.c"


def test_nat_and_v6_frag_switches_report_incomplete_withdrawal(tmp_path):
    hw = HW_SOURCE.read_text()
    fo = FO_SOURCE.read_text()

    n44_start = hw.index("static bool ask_nat44_offload = true;")
    n44_end = hw.index("\nbool ask_hw_nat44_offload_armed(", n44_start)
    n66_start = hw.index("static bool ask_nat66_offload = true;")
    n66_end = hw.index("\nbool ask_hw_nat66_offload_armed(", n66_start)
    v6f_start = fo.index("static bool ask_ipv6_hw_frag = true;")
    v6f_end = fo.index("\nstatic const struct kernel_param_ops ask_ipv6_hw_frag_ops", v6f_start)

    body = hw[n44_start:n44_end] + "\n" + hw[n66_start:n66_end] + "\n" + fo[v6f_start:v6f_end]
    assert "ask_nat44_offload_set" in body
    assert "ask_nat66_offload_set" in body
    assert "ask_ipv6_hw_frag_disable_incomplete" in body
    assert "ask_flow_nat44_flush(" in fo
    assert "ask_flow_nat66_flush(" in fo
    assert "ask_flow_v6_frag_flush_sync(" in fo
    assert "ask_v6_frag_flush_work" not in fo

    (tmp_path / "nat_frag_switch_body.h").write_text(body)
    exe = tmp_path / "ask-nat-frag-kill-switch"
    subprocess.run(
        [*shlex.split(os.environ.get("HOSTCC", "cc")), "-std=gnu11",
         "-Wall", "-Wextra", "-Werror",
         "-Wno-unused-parameter", "-Wno-unused-function",
         *shlex.split(os.environ.get("HOSTCFLAGS", "")),
         "-I", str(tmp_path), str(HARNESS), "-o", str(exe)],
        check=True,
    )
    out = subprocess.run([str(exe)], capture_output=True, text=True, check=True, timeout=30)
    assert "ask_nat_frag_kill_switch: OK" in out.stdout
