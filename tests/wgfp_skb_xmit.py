"""Ownership checks for WGFP's fallback skb TX path and frame release order."""

from pathlib import Path
import subprocess

import pytest

pytestmark = pytest.mark.host

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / (
    "kernel/common/files/drivers/net/ethernet/freescale/dpaa/dpaa_wgfp.c"
)
HARNESS = ROOT / "tests/dut/wgfp_skb_xmit.c"


def extract(start, end):
    source = SOURCE.read_text()
    offset = source.index(start)
    return source[offset:source.index(end, offset)]


def test_wgfp_xmit_uses_tx_slot_or_owned_skb_never_raw_rx_fd():
    body = extract(
        "static void wgfp_prepare_xmit_skb(",
        "\nstatic int __init dpaa_wgfp_init(",
    )
    xmit = extract("void dpaa_wgfp_xmit(", "\nEXPORT_SYMBOL_GPL(dpaa_wgfp_xmit)")
    assert "dpaa_wgfp_tx_alloc(" in xmit
    assert "dpaa_wgfp_tx_submit(" in xmit
    assert "dpaa_wgfp_alloc_xmit_skb(" in xmit
    assert "skb_put_data(" in xmit
    assert "dpaa_wgfp_xmit_skb(" in xmit
    assert "dev_queue_xmit(" in body
    assert "qman_enqueue" not in body
    assert "qm_fd_addr_set64" not in body

    xmit_skb = extract("void dpaa_wgfp_xmit_skb(", "\nEXPORT_SYMBOL_GPL(dpaa_wgfp_xmit_skb)")
    assert xmit_skb.index("wgfp_frame_release(f)") < xmit_skb.index("dpaa_wgfp_orp_settle()")
    assert xmit_skb.index("dpaa_wgfp_orp_settle()") < xmit_skb.index("wgfp_submit_skb(")

    release = extract(
        "static void wgfp_frame_release_fast(",
        "\nstatic void wgfp_touch_frame(",
    )
    assert "dma_sync_single_for_device" in release
    assert release.index("dma_sync_single_for_device") < release.index(
        "dpaa_wgfp_fd_release"
    )


def test_wgfp_egress_keeps_a_boot_lifetime_netdev_reference():
    body = extract("int dpaa_wgfp_egress(", "\nEXPORT_SYMBOL_GPL(dpaa_wgfp_egress)")
    assert "netif_running" in body
    assert "wgfp_eg_dev" in body
    assert "dev_hold" not in body


def test_wgfp_xmit_fault_injection(tmp_path):
    release_fast = extract(
        "static void wgfp_frame_release_fast(",
        "\nstatic void wgfp_touch_frame(",
    )
    release = extract(
        "static void wgfp_frame_release(",
        "\nvoid dpaa_wgfp_drop(",
    )
    body = extract(
        "static void wgfp_prepare_xmit_skb(",
        "\nstatic int __init dpaa_wgfp_init(",
    )
    (tmp_path / "wgfp_xmit_body.h").write_text(
        release_fast + "\n" + release + "\n" + body + "\n"
    )
    binary = tmp_path / "wgfp_skb_xmit"
    subprocess.run(
        [
            "cc", "-std=gnu11", "-Wall", "-Wextra", "-Werror",
            "-Wno-unused-parameter", "-I", str(tmp_path),
            str(HARNESS), "-o", str(binary),
        ],
        check=True,
    )
    subprocess.run([str(binary)], check=True)
