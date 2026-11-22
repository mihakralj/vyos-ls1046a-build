"""Ownership and ORP submission tests for WGFP-v2 dedicated TX slots."""

from pathlib import Path

import pytest

pytestmark = pytest.mark.host
ROOT = Path(__file__).resolve().parents[1]
WGFP = ROOT / "kernel/common/files/drivers/net/ethernet/freescale/dpaa/dpaa_wgfp.c"


def test_wgfp_v2_orp_xmit_uses_dedicated_tx_slots_not_rx_bpid():
    wgfp = WGFP.read_text()
    # WGFP-v2 restores spread=1 using dedicated confirmed bpid=0xff TX slots
    assert "static bool wgfp_spread = true;" in wgfp
    assert "static bool wgfp_orp_submit_slot(" in wgfp
    assert "static bool wgfp_orp_xmit(" not in wgfp
    assert 'sscanf(buf, "spread %u", &n)' in wgfp

    submit = wgfp[wgfp.index("void dpaa_wgfp_tx_submit("):wgfp.index(
        "\nEXPORT_SYMBOL_GPL(dpaa_wgfp_tx_submit)"
    )]
    assert "fd.bpid = WGFP_BPID_INV;" in submit
    assert "FM_FD_CMD_FCO" in submit
    assert "wgfp_orp_submit_slot(slot, &fd)" in submit
    assert "dma_sync_single_for_device(eg->tx_dma_dev, slot->dma + off, len," in submit

    # Legacy dpaa_wgfp_xmit() never submits RX f->fd directly to QMan ORP
    xmit = wgfp[wgfp.index("void dpaa_wgfp_xmit("):wgfp.index(
        "\nEXPORT_SYMBOL_GPL(dpaa_wgfp_xmit)"
    )]
    assert "qman_enqueue_orp_fqid" not in xmit
    assert "dpaa_wgfp_tx_alloc(" in xmit
    assert "dpaa_wgfp_tx_submit(" in xmit
