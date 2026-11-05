"""Host tests for the F-280 shared-ehash owner helpers."""

from pathlib import Path
import importlib.util
import subprocess

import pytest

pytestmark = pytest.mark.host
ROOT = Path(__file__).resolve().parents[1]
FIXUP = ROOT / "bin/kernel-fixups/F_280.py"
HARNESS = ROOT / "tests/dut/fman_ehash_owner.c"
PCD = "drivers/net/ethernet/freescale/fman/fman_pcd.c"


def _helpers():
    spec = importlib.util.spec_from_file_location("f280", FIXUP)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    for path, _old, new in module.EDITS:
        if path == PCD and "fman_pcd_ehash_foreign(struct" in new:
            return new[:new.index("int fman_pcd_fe_flow_add(")]
    raise AssertionError("F-280 helper edit not found")


def test_foreign_owner_detection(tmp_path):
    (tmp_path / "fman_ehash_owner_body.h").write_text(_helpers())
    binary = tmp_path / "fman_ehash_owner"
    subprocess.run(
        [
            "cc", "-std=gnu11", "-Wall", "-Wextra", "-Werror",
            "-Wno-unused-parameter", "-fsanitize=address,undefined",
            "-I", str(tmp_path), str(HARNESS), "-o", str(binary),
        ],
        check=True,
    )
    subprocess.run([str(binary)], check=True)
