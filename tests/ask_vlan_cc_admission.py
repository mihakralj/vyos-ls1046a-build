"""Host tests for retired legacy VLAN CC publishers and retained teardown."""

from pathlib import Path
import re
import subprocess

import pytest


pytestmark = pytest.mark.host
ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "kernel/ask/oot-modules/ask/ask_vlan_cc.c"
HARNESS = ROOT / "tests/dut/ask_vlan_cc_admission.c"


def test_vlan_cc_admission_and_retained_teardown(tmp_path):
    source = SOURCE.read_text()
    start = source.index("int ask_vlan_cc_teardown_port(")
    end = source.index("EXPORT_SYMBOL_GPL(ask_vlan_cc_teardown_port);", start)
    (tmp_path / "vlan_cc_admission_body.h").write_text(source[start:end])
    binary = tmp_path / "vlan_cc_admission"
    subprocess.run(
        [
            "cc", "-std=gnu11", "-Wall", "-Wextra", "-Werror",
            "-Wno-unused-function", "-Wno-unused-parameter", "-Wno-unused-variable",
            "-pthread", "-fsanitize=address,undefined",
            "-I", str(tmp_path), str(HARNESS), "-o", str(binary),
        ],
        check=True,
    )
    subprocess.run([str(binary)], check=True, timeout=30)


def test_legacy_vlan_cc_publishers_are_absent():
    module = ROOT / "kernel/ask/oot-modules/ask"
    publisher = re.compile(r"\bask_vlan_cc_flow_(?:add|del)\s*\(")
    for path in module.rglob("*"):
        if path.suffix in {".c", ".h"}:
            assert not publisher.search(path.read_text()), path
