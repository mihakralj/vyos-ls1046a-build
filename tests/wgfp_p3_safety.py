"""Host regression checks for Priority 2/3 WGFP safety fixes and defaults."""

from pathlib import Path
import pytest

pytestmark = pytest.mark.host

ROOT = Path(__file__).resolve().parents[1]
WGFP_H = ROOT / "kernel/common/files/include/linux/fsl/dpaa_wgfp.h"
WGFP_C = ROOT / "kernel/common/files/drivers/net/ethernet/freescale/dpaa/dpaa_wgfp.c"
FASTPATH_C = ROOT / "kernel/common/files/drivers/net/wireguard/fastpath.c"
COUNTERS_SH = ROOT / "bin/testrig-wgfp-counters.sh"


def _between(src: str, start: str, end: str) -> str:
    idx = src.index(start)
    return src[idx:src.index(end, idx)]


def test_wgfp_safe_defaults():
    wgfp_c = WGFP_C.read_text()
    fp = FASTPATH_C.read_text()
    # A2 decision: threaded=1 by default (and actually programmed via dev_set_threaded),
    # wgfp_rx_fast=0 (safe P3) by default, prefetch=1 (read-only DMA buffer).
    assert "static bool wgfp_threaded = true;" in wgfp_c
    init_fn = _between(wgfp_c, "static int __init dpaa_wgfp_init(", "late_initcall(")
    assert "dev_set_threaded(wgfp_ndev, NETDEV_NAPI_THREADED_ENABLED)" in init_fn
    assert "static unsigned int wgfp_prefetch = 1;" in wgfp_c
    assert "static unsigned int wgfp_rx_fast;" in fp or "static unsigned int wgfp_rx_fast = 0;" in fp


def test_wgfp_dma_readonly_out_of_place_tx_and_rx():
    fp = FASTPATH_C.read_text()
    # TX seal must allocate skb with >= CHACHA_BLOCK_SIZE headroom and never write f->buf
    peer_tx = _between(fp, "static int wgfp_peer_frame(", "\nstatic const struct dpaa_wgfp_ops")
    assert "dpaa_wgfp_alloc_xmit_skb(" in peer_tx
    assert "dpaa_wgfp_xmit_skb(" in peer_tx
    assert "wgfp_seal(dst, plain, CHACHA_BLOCK_SIZE" in peer_tx
    assert "memset(inner + ip_len" not in peer_tx

    lab_tx = _between(fp, "static int wgfp_lab_frame(", "\nstatic const struct dpaa_wgfp_ops wgfp_lab_ops")
    assert "dpaa_wgfp_alloc_xmit_skb(" in lab_tx
    assert "dpaa_wgfp_xmit_skb(" in lab_tx
    assert "memset(inner + ip_len" not in lab_tx

    # RX P6 open must decrypt out-of-place into skb and never write f->buf
    p6 = _between(fp, "static int wgfp_rx_fast_frame(", "\n/*\n * P2b-3 slow-path punt")
    assert "wgfp_open_dst(" in p6
    assert "dpaa_wgfp_drop(f);" in p6


def test_wgfp_tx_alloc_failure_punts_and_refreshes():
    fp = FASTPATH_C.read_text()
    peer_tx = _between(fp, "static int wgfp_peer_frame(", "\nstatic const struct dpaa_wgfp_ops")
    alloc_err = _between(peer_tx, "if (unlikely(!skb)) {", "\n\t\tskb_reserve(")
    assert "mod_delayed_work(system_wq, &pst->refresh, 0);" in alloc_err
    assert "goto decline;" in alloc_err
    assert "dpaa_wgfp_drop(f);" not in alloc_err


def test_wgfp_uncertain_insert_stuck_obs_ttl_and_quiesce():
    wgfp_h = WGFP_H.read_text()
    wgfp_c = WGFP_C.read_text()
    fp = FASTPATH_C.read_text()

    assert "dpaa_wgfp_port_quiesce(" in wgfp_h
    assert "dpaa_wgfp_policy_revoke(" in wgfp_h
    assert "EXPORT_SYMBOL_GPL(dpaa_wgfp_port_quiesce)" in wgfp_c
    assert "EXPORT_SYMBOL_GPL(dpaa_wgfp_policy_revoke)" in wgfp_c

    # Uncertain insert error (-ETIMEDOUT/-EIO) -> WGFP_STUCK (not WGFP_FAILED freed at 30s)
    work = _between(fp, "static void wgfp_flow_work_fn(", "\n/* Free peers without flows")
    assert "err == -ETIMEDOUT || err == -EIO" in work
    assert "WGFP_STUCK" in work

    # wgfp_obs checks TTL and policy epoch
    obs = _between(fp, "static bool wgfp_rx_observed(", "\n/* Install, move or withdraw")
    assert "WGFP_OBS_TTL" in obs
    assert "wgfp_obs_epoch" in obs

    # port_quiesce and policy_revoke wired in wgfp_peer_ops
    ops = _between(fp, "static const struct dpaa_wgfp_ops wgfp_peer_ops", "\n/* Is @t")
    assert ".port_quiesce" in ops
    assert ".policy_revoke" in ops


def test_wgfp_xmit_counters_and_slot_reuse():
    wgfp_c = WGFP_C.read_text()
    eg = _between(wgfp_c, "int dpaa_wgfp_egress(", "\nEXPORT_SYMBOL_GPL(dpaa_wgfp_egress)")
    assert "same_free" in eg and "any_free" in eg

    xmit = _between(wgfp_c, "static void wgfp_submit_skb(", "\nvoid dpaa_wgfp_xmit(")
    assert "NET_XMIT_CN" in xmit
    assert "wgfp_xmit_qdisc_drop" in xmit
    assert "wgfp_xmit_invalid_egress" not in xmit


def test_wgfp_counters_harness_has_teardown_and_drain():
    sh = COUNTERS_SH.read_text()
    assert "wgfp_counter_teardown_check" in sh
    assert "frm_cnt" in sh
    assert "ethtool -S" in sh
