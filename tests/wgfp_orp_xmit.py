"""Fail-closed ownership test for the retired WGFP ORP TX path."""

from pathlib import Path

import pytest

pytestmark = pytest.mark.host
ROOT = Path(__file__).resolve().parents[1]
WGFP = ROOT / "kernel/common/files/drivers/net/ethernet/freescale/dpaa/dpaa_wgfp.c"

def test_real_bpid_orp_xmit_is_disabled():
    wgfp = WGFP.read_text()
    assert "static bool wgfp_spread = false;" in wgfp
    assert "static bool wgfp_orp_xmit(" not in wgfp
    assert 'sscanf(buf, "spread %u", &n)' in wgfp
    assert "err = -EOPNOTSUPP;" in wgfp
    xmit = wgfp[wgfp.index("void dpaa_wgfp_xmit("):wgfp.index(
        "\nEXPORT_SYMBOL_GPL(dpaa_wgfp_xmit)"
    )]
    assert "qman_enqueue_orp_fqid" not in xmit
