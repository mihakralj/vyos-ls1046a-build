"""Host check for the RX fan-out worker choice in dpaa_wgfp.c."""

from pathlib import Path
import subprocess

import pytest

pytestmark = pytest.mark.host
ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "kernel/common/files/drivers/net/ethernet/freescale/dpaa/dpaa_wgfp.c"
HARNESS = ROOT / "tests/dut/wgfp_fanout_pick.c"


def test_fanout_pick_weights_the_distributor_cpu(tmp_path):
    source = SOURCE.read_text()
    start = source.index("static unsigned int wgfp_fanout_pick(")
    end = source.index("\nstatic enum qman_cb_dqrr_result wgfp_dqrr(", start)
    (tmp_path / "wgfp_fanout_pick_body.h").write_text(source[start:end])
    binary = tmp_path / "wgfp_fanout_pick"
    subprocess.run(
        ["cc", "-std=gnu11", "-Wall", "-Wextra", "-Werror", "-I", str(tmp_path),
         str(HARNESS), "-o", str(binary)],
        check=True,
    )
    subprocess.run([str(binary)], check=True)
