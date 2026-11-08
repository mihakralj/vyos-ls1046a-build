"""Host fault-injection tests for ask.ko retained FE-delete ownership."""

from pathlib import Path
import subprocess

import pytest

pytestmark = pytest.mark.host
ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "kernel/ask/oot-modules/ask/ask_flow_offload.c"
HARNESS = ROOT / "tests/dut/ask_fe_orphan.c"


def test_failed_fe_delete_retains_owner_and_blocks_reinsert(tmp_path):
    source = SOURCE.read_text()
    start = source.index("struct ask_fe_orphan {")
    end = source.index("\n}", source.index("static void ask_fe_orphan_exit(", start)) + 2
    (tmp_path / "ask_fe_orphan_body.h").write_text(source[start:end])
    start = source.index("static int ask_flow_fe_owner_status(")
    end = source.index("static int ask_neigh_republish(", start)
    (tmp_path / "ask_fe_owner_status.h").write_text(source[start:end])
    start = source.index("struct ask_flush_victim {")
    end = source.index("/* Process context only.", start)
    flush = source[start:end]
    start = source.index("static int __ask_flow_flush_matching(", end)
    end = source.index("static void ask_pppoe_flush_fn(", start)
    (tmp_path / "ask_fe_flush_body.h").write_text(flush + source[start:end])
    binary = tmp_path / "ask_fe_orphan"
    subprocess.run(
        [
            "cc", "-std=gnu11", "-Wall", "-Wextra", "-Werror", "-pthread",
            "-Wno-unused-parameter", "-Wno-unused-function",
            "-I", str(tmp_path), str(HARNESS), "-o", str(binary),
        ],
        check=True,
    )
    subprocess.run([str(binary)], check=True, timeout=30)
    subprocess.run([str(binary), "sw-remove-failure"], check=True, timeout=30)
    subprocess.run([str(binary), "pppoe"], check=True, timeout=30)
    subprocess.run([str(binary), "pppoe-sw-remove-failure"], check=True, timeout=30)
