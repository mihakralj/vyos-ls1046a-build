"""Host unit tests for WGFP-v2 dedicated TX slots, confirm/ERN/error guards, and drain."""

from pathlib import Path
import subprocess

import pytest

pytestmark = pytest.mark.host
ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "kernel/common/files/drivers/net/ethernet/freescale/dpaa/dpaa_wgfp.c"
HARNESS = ROOT / "tests/dut/wgfp_tx_slots.c"


def _between(source: str, start: str, end: str) -> str:
    idx = source.index(start)
    return source[idx:source.index(end, idx)]


def test_wgfp_tx_slot_lifecycle_confirm_ern_and_drain(tmp_path):
    src = SOURCE.read_text()
    assert "#define WGFP_XMIT_WAIT_NS\t(1 * NSEC_PER_MSEC)" in src
    body = "\n".join([
        _between(src, "static inline u32 wgfp_dma_hash(", "\nstatic void wgfp_touch_frame("),
        _between(src, "static enum qman_cb_dqrr_result wgfp_conf_dqrr(", "\nstatic enum qman_cb_dqrr_result wgfp_dqrr_frame("),
        _between(src, "static void wgfp_orp_hole(", "\nstatic enum qman_cb_dqrr_result wgfp_dqrr("),
        _between(src, "static void wgfp_tx_enqueue_slot(", "\nstatic int wgfp_cpu_poll("),
        _between(src, "static int wgfp_eg_inflight_sum(", "\nstatic struct notifier_block wgfp_netdev_notifier"),
        _between(src, "bool dpaa_wgfp_tx_alloc(", "\nstatic void wgfp_prepare_xmit_skb("),
    ])
    (tmp_path / "wgfp_tx_slots_body.h").write_text(body)
    binary = tmp_path / "wgfp_tx_slots"
    subprocess.run(
        [
            "cc", "-std=gnu11", "-Wall", "-Wextra", "-Werror",
            "-Wno-unused-parameter", "-Wno-unused-function",
            "-fsanitize=address,undefined",
            "-I", str(tmp_path), str(HARNESS), "-o", str(binary),
        ],
        check=True,
    )
    subprocess.run([str(binary)], check=True)
