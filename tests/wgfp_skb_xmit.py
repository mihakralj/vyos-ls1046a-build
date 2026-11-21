"""Ownership checks for WGFP's transition to the normal DPAA skb TX path."""

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


def test_wgfp_xmit_uses_owned_skb_instead_of_real_bpid_fd():
    body = extract(
        "static void wgfp_prepare_xmit_skb(",
        "\nstatic int __init dpaa_wgfp_init(",
    )
    xmit = extract("void dpaa_wgfp_xmit(", "\nEXPORT_SYMBOL_GPL(dpaa_wgfp_xmit)")
    success = xmit[xmit.index("skb_put_data("):]
    assert "netdev_alloc_skb(" in xmit
    assert "skb_put_data(" in xmit
    assert "wgfp_frame_release(f)" in xmit
    assert "dev_queue_xmit(" in body
    assert success.index("skb_put_data(") < success.index("wgfp_frame_release(f)")
    assert success.index("wgfp_frame_release(f)") < success.index("wgfp_submit_skb(")
    assert "qman_enqueue" not in body
    assert "qm_fd_addr_set64" not in body
    release = extract(
        "static void wgfp_frame_release(",
        "\nvoid dpaa_wgfp_drop(",
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
    release = extract(
        "static void wgfp_frame_release(",
        "\nvoid dpaa_wgfp_drop(",
    )
    body = extract(
        "static void wgfp_prepare_xmit_skb(",
        "\nstatic int __init dpaa_wgfp_init(",
    )
    (tmp_path / "wgfp_xmit_body.h").write_text(release + "\n" + body + "\n")
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
