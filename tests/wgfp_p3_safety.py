"""Host regression checks for WGFP-v2 safety invariants and defaults."""

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


def test_wgfp_v2_defaults():
    wgfp_c = WGFP_C.read_text()
    fp = FASTPATH_C.read_text()
    # WGFP-v2 defaults: threaded=1, spread=1 (ORP), tx_batch=32, prefetch=1,
    # wgfp_rx_fast=2 (P6 direct forward on dedicated confirmed WGFP TX slots).
    assert "static bool wgfp_threaded = true;" in wgfp_c
    init_fn = _between(wgfp_c, "static int __init dpaa_wgfp_init(", "late_initcall(")
    assert "dev_set_threaded(wgfp_ndev, NETDEV_NAPI_THREADED_ENABLED)" in init_fn
    assert "static unsigned int wgfp_prefetch = 1;" in wgfp_c
    assert "static bool wgfp_spread = true;" in wgfp_c
    assert "static unsigned int wgfp_tx_batch = 32;" in wgfp_c
    assert "static unsigned int wgfp_rx_fast = 2;" in fp


def test_wgfp_dma_readonly_out_of_place_tx_and_rx():
    fp = FASTPATH_C.read_text()
    # TX seal must allocate a dedicated WGFP TX slot and seal out-of-place
    # without ever mutating read-only DMA_FROM_DEVICE f->buf.
    peer_tx = _between(fp, "static int wgfp_peer_frame(", "\nstatic const struct dpaa_wgfp_ops")
    assert "dpaa_wgfp_tx_alloc(" in peer_tx
    assert "dpaa_wgfp_tx_submit(" in peer_tx
    assert "wgfp_seal_dst(dst, inner, ip_len, plain, tup.family" in peer_tx
    assert "memset(inner + ip_len" not in peer_tx

    lab_tx = _between(fp, "static int wgfp_lab_frame(", "\nstatic const struct dpaa_wgfp_ops wgfp_lab_ops")
    assert "dpaa_wgfp_tx_alloc(" in lab_tx
    assert "dpaa_wgfp_tx_submit(" in lab_tx
    assert "wgfp_seal_dst(dst, inner, ip_len, plain, 0" in lab_tx
    assert "memset(inner + ip_len" not in lab_tx

    # RX P6 open must decrypt out-of-place into WGFP TX slot or skb, never f->buf
    p6 = _between(fp, "static int wgfp_rx_fast_frame(", "\n/*\n * P2b-3 slow-path punt")
    assert "wgfp_open_dst(" in p6
    assert "dpaa_wgfp_drop(f);" in p6
    assert "dpaa_wgfp_tx_alloc(" in p6
    assert "dpaa_wgfp_tx_cancel(" in p6

    # All stack handoffs settle any owed ORP sequence before netif_receive_skb/wg_packet_receive
    p6_stack = _between(fp, "static int wgfp_rx_fast_stack(", "\n/* P6 direct forward")
    assert p6_stack.index("dpaa_wgfp_orp_settle()") < p6_stack.index("netif_receive_skb(skb)")
    punt = _between(fp, "static int wgfp_punt(", "\n/* Does the template")
    assert punt.index("dpaa_wgfp_orp_settle()") < punt.index("netif_receive_skb(skb)")
    rx = _between(fp, "static int wgfp_rx_frame(", "\nstatic int wgfp_peer_frame(")
    assert rx.index("dpaa_wgfp_orp_settle()") < rx.index("netif_receive_skb(skb)")
    assert rx.index("dpaa_wgfp_orp_settle()") < rx.index("wg_packet_receive(")


def test_wgfp_tx_alloc_failure_punts_and_refreshes():
    fp = FASTPATH_C.read_text()
    peer_tx = _between(fp, "static int wgfp_peer_frame(", "\nstatic const struct dpaa_wgfp_ops")
    alloc_err = _between(peer_tx, "if (unlikely(!dpaa_wgfp_tx_alloc(", "\n\t\tctr = ")
    assert "mod_delayed_work(system_wq, &pst->refresh, 0);" in alloc_err
    assert "goto decline;" in alloc_err
    assert "dpaa_wgfp_drop(f);" not in alloc_err


def test_wgfp_uncertain_insert_stuck_obs_ttl_and_quiesce():
    wgfp_h = WGFP_H.read_text()
    wgfp_c = WGFP_C.read_text()
    fp = FASTPATH_C.read_text()

    assert "dpaa_wgfp_port_quiesce(" in wgfp_h
    assert "dpaa_wgfp_policy_revoke(" in wgfp_h
    assert "dpaa_wgfp_tx_alloc(" in wgfp_h
    assert "dpaa_wgfp_tx_cancel(" in wgfp_h
    assert "dpaa_wgfp_tx_submit(" in wgfp_h
    assert "dpaa_wgfp_orp_settle(" in wgfp_h
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
    # B1 lock order: rtnl_lock outside wgfp_lock; down/up reactivates same dev without realloc
    assert eg.index("rtnl_lock();") < eg.index("mutex_lock(&wgfp_lock);")
    assert "if (eg->dev == dev)" in eg
    assert "eg->retired || eg->quarantined" in eg
    assert "WRITE_ONCE(eg->active, true);" in eg

    xmit = _between(wgfp_c, "static void wgfp_submit_skb(", "\nvoid dpaa_wgfp_xmit(")
    assert "NET_XMIT_CN" in xmit
    assert "wgfp_xmit_qdisc_drop" in xmit
    assert "wgfp_xmit_invalid_egress" not in xmit


def test_wgfp_counters_harness_has_teardown_and_drain():
    sh = COUNTERS_SH.read_text()
    assert "wgfp_counter_teardown_check" in sh
    assert "frm_cnt" in sh
    assert "ethtool -S" in sh
