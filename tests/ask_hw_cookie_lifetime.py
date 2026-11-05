"""Host concurrency test for ASK hardware-cookie lookup and removal."""

from pathlib import Path
import subprocess

import pytest


pytestmark = pytest.mark.host
ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "kernel/ask/oot-modules/ask/ask_hw.c"
HARNESS = ROOT / "tests/dut/ask_hw_cookie_lifetime.c"


def test_sink_lookup_pins_cookie_against_immediate_free(tmp_path):
    source = SOURCE.read_text()
    start = source.index("struct ask_hw_flow_cookie *\nask_hw_cookie_lookup(")
    end = source.index("EXPORT_SYMBOL_GPL(ask_hw_cookie_free);", start)
    end += len("EXPORT_SYMBOL_GPL(ask_hw_cookie_free);")
    getter_start = source.index("int ask_hw_flow_get_sink_fqid(")
    getter_end = source.index("EXPORT_SYMBOL_GPL(ask_hw_flow_get_sink_fqid);",
                              getter_start)
    getter_end += len("EXPORT_SYMBOL_GPL(ask_hw_flow_get_sink_fqid);")
    (tmp_path / "ask_hw_cookie_body.h").write_text(
        source[start:end] + "\n" + source[getter_start:getter_end]
    )
    binary = tmp_path / "ask_hw_cookie_lifetime"
    subprocess.run(
        [
            "cc", "-std=gnu11", "-Wall", "-Wextra", "-Werror", "-pthread",
            "-I", str(tmp_path), str(HARNESS), "-o", str(binary),
        ],
        check=True,
    )
    subprocess.run([str(binary)], check=True, timeout=30)
