"""Host fault-injection test for F-289 ASK FE disengage error handling."""

from pathlib import Path
import subprocess

import pytest


pytestmark = pytest.mark.host
ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "kernel/ask/oot-modules/ask/ask_hw.c"
GENL = ROOT / "kernel/ask/oot-modules/ask/ask_genl.c"
HARNESS = ROOT / "tests/dut/ask_hw_disengage.c"
BRINGUP_HARNESS = ROOT / "tests/dut/ask_hw_bringup.c"


def _slice(source, start, end):
    i = source.index(start)
    j = source.index(end, i) + len(end)
    return source[i:j]


def test_bringup_does_not_create_unused_global_tx_fq(tmp_path):
    source = SOURCE.read_text()
    (tmp_path / "ask_hw_bringup_body.h").write_text(
        _slice(source, "int ask_hw_pcd_bringup(void)\n{", "\n}\n")
    )
    binary = tmp_path / "ask_hw_bringup"
    subprocess.run(
        [
            "cc", "-std=gnu11", "-Wall", "-Wextra", "-Werror",
            "-Wno-unused-function", "-Wno-unused-parameter",
            "-I", str(tmp_path), str(BRINGUP_HARNESS), "-o", str(binary),
        ],
        check=True,
    )
    subprocess.run([str(binary)], check=True, timeout=30)


def test_failed_disengage_is_reported_and_retained(tmp_path):
    source = SOURCE.read_text()
    body = "\n".join((
        _slice(source, "static void ask_hw_set_family_locked(",
               "\n}\n\n/* Flow admission").removesuffix("\n\n/* Flow admission"),
        _slice(source, "void ask_hw_offload_set_family(u8 hw_port_id,",
               "EXPORT_SYMBOL_GPL(ask_hw_offload_set_family);"),
        _slice(source, "void ask_hw_pcd_teardown(void)\n{",
               'ask_pr_dbg("hw: pcd teardown complete\\n");\n}'),
        _slice(source, "static int ask_hw_offload_engage_profile(u8 hw_port_id,",
               "\n}\n\nint ask_hw_offload_engage(").removesuffix(
                   "\n\nint ask_hw_offload_engage("),
        _slice(source, "int ask_hw_offload_engage_role(u8 hw_port_id,",
               "\n}\n\n/* T-M6-2 B2").removesuffix("\n\n/* T-M6-2 B2"),
        _slice(source, "struct ask_hw_fe_transition {",
               "\n}\n\nint ask_hw_offload_disengage(").removesuffix(
                   "\n\nint ask_hw_offload_disengage("),
        _slice(source, "int ask_hw_offload_disengage(u8 hw_port_id)\n{",
               "EXPORT_SYMBOL_GPL(ask_hw_offload_disengage);"),
        _slice(source, "int ask_hw_fe_reengage(u8 hw_port_id)\n{",
               "EXPORT_SYMBOL_GPL(ask_hw_fe_reengage);"),
    ))
    (tmp_path / "ask_hw_disengage_body.h").write_text(body)
    genl = GENL.read_text()
    (tmp_path / "ask_genl_engage_body.h").write_text(
        _slice(genl, "static int ask_genl_engage_doit(struct sk_buff *skb, struct genl_info *info)\n{",
               "\n}\n\nstatic int ask_genl_disengage_doit(").removesuffix(
                   "\n\nstatic int ask_genl_disengage_doit(")
    )
    binary = tmp_path / "ask_hw_disengage"
    subprocess.run(
        [
            "cc", "-std=gnu11", "-Wall", "-Wextra", "-Werror",
            "-Wno-unused-function", "-Wno-unused-parameter",
            "-fsanitize=address,undefined",
            "-I", str(tmp_path), str(HARNESS), "-o", str(binary),
        ],
        check=True,
    )
    subprocess.run([str(binary)], check=True, timeout=30)
