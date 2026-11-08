"""Host checks for ASK generation, conditional release, and flush ownership."""

from pathlib import Path
import subprocess

import pytest


pytestmark = pytest.mark.host
ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "kernel/ask/oot-modules/ask/ask_flow.c"
HARNESS = ROOT / "tests/dut/ask_owner_generation.c"


def _section(source, start, end):
    first = source.index(start)
    last = source.index(end, first) + len(end)
    return source[first:last]


def test_generation_and_flush_ownership(tmp_path):
    source = SOURCE.read_text()
    sections = [
        _section(source, "#define ASK_GEN_STATE_BITS",
                 "EXPORT_SYMBOL_GPL(ask_flow_gen_release);"),
        _section(source, "static int ask_flow_remove_core(",
                 "EXPORT_SYMBOL_GPL(ask_flow_remove_owned_identified);"),
        _section(source, "#define ASK_FLOW_FLUSH_BATCH",
                 "EXPORT_SYMBOL_GPL(ask_flow_flush);"),
    ]
    (tmp_path / "ask_owner_generation_body.h").write_text("\n".join(sections))
    binary = tmp_path / "ask_owner_generation"
    subprocess.run(
        [
            "cc", "-std=gnu11", "-Wall", "-Wextra", "-Werror", "-pthread",
            "-Wno-unused-parameter", "-I", str(tmp_path),
            str(HARNESS), "-o", str(binary),
        ],
        check=True,
    )
    subprocess.run([str(binary)], check=True, timeout=30)
