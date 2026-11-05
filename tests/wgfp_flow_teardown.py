"""Host fault-injection tests for WGFP flow-flush ownership."""

from pathlib import Path
import subprocess

import pytest

pytestmark = pytest.mark.host
ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "kernel/common/files/drivers/net/wireguard/fastpath.c"
HARNESS = ROOT / "tests/dut/wgfp_flow_teardown.c"


def test_failed_delete_quarantines_flow_and_retry_does_not_unlink_again(tmp_path):
    source = SOURCE.read_text()
    withdraw_start = source.index("static void wgfp_flow_withdraw(")
    withdraw_end = source.index("\n/* ---- sink path ---- */", withdraw_start)
    start = source.index("static int wgfp_flows_flush(void)\n{")
    end = source.index("\n/* ---- binding ---- */", start)
    body = source[withdraw_start:withdraw_end] + "\n" + source[start:end]
    (tmp_path / "wgfp_flow_teardown_body.h").write_text(body)
    binary = tmp_path / "wgfp_flow_teardown"
    subprocess.run(
        [
            "cc", "-std=gnu11", "-Wall", "-Wextra", "-Werror",
            "-Wno-unused-parameter", "-I", str(tmp_path),
            str(HARNESS), "-o", str(binary),
        ],
        check=True,
    )
    subprocess.run([str(binary)], check=True)
