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
    end = source.index("/* FE-VM debugfs flow insert helper", start)
    end = source.rindex("/* ----", start, end)
    (tmp_path / "ask_fe_orphan_body.h").write_text(source[start:end])
    binary = tmp_path / "ask_fe_orphan"
    subprocess.run(
        [
            "cc", "-std=gnu11", "-Wall", "-Wextra", "-Werror",
            "-Wno-unused-parameter", "-Wno-unused-function",
            "-I", str(tmp_path), str(HARNESS), "-o", str(binary),
        ],
        check=True,
    )
    subprocess.run([str(binary)], check=True)
