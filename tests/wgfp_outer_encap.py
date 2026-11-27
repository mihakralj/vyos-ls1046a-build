"""Host ASan/UBSan tests for WGFP Stage A: outer VLAN + PPPoE encap/decap and 50-byte F-259 key."""

from pathlib import Path
import subprocess

import pytest

pytestmark = pytest.mark.host
ROOT = Path(__file__).resolve().parents[1]
WGFP_H = ROOT / "kernel/common/files/include/linux/fsl/dpaa_wgfp.h"
WGFP_C = ROOT / "kernel/common/files/drivers/net/ethernet/freescale/dpaa/dpaa_wgfp.c"
FASTPATH = ROOT / "kernel/common/files/drivers/net/wireguard/fastpath.c"
HARNESS = ROOT / "tests/dut/wgfp_outer_encap.c"


def _between(src: str, start: str, end: str) -> str:
    idx = src.index(start)
    return src[idx:src.index(end, idx)]


def test_stage_a_outer_vlan_pppoe_key_tmpl_and_rx(tmp_path):
    wgfp_h = WGFP_H.read_text()
    wgfp_c = WGFP_C.read_text()
    fp = FASTPATH.read_text()

    # 1. struct dpaa_wgfp_tuple must carry vlan_vid and pppoe_sid
    tup_def = _between(wgfp_h, "struct dpaa_wgfp_tuple {", "\n};") + "\n};\n"
    assert "vlan_vid" in tup_def and "pppoe_sid" in tup_def

    # 2. Extract real wgfp_key() from dpaa_wgfp.c
    key_fn = _between(wgfp_c, "static void wgfp_key(", "\nstatic dpaa_wgfp_family_fn")

    # 3. Extract real wgfp_tmpl_build_l2() and wgfp_parse_l2() from fastpath.c
    tmpl_def = _between(fp, "#define WGFP_PPPOE_HLEN", "\nstruct wgfp_binds {")
    l2_build = _between(fp, "static void wgfp_tmpl_build_l2(", "\nstatic bool wgfp_tmpl_l2(")
    l2_parse = _between(fp, "static bool wgfp_parse_l2(", "\n/* Writer side")

    (tmp_path / "wgfp_outer_encap_body.h").write_text(
        tup_def + "\n" + key_fn + "\n" + tmpl_def + "\n" + l2_build + "\n" + l2_parse
    )

    binary = tmp_path / "wgfp_outer_encap"
    subprocess.run(
        [
            "cc", "-std=gnu11", "-Wall", "-Wextra", "-Werror",
            "-Wno-unused-parameter", "-Wno-unused-function",
            "-fsanitize=address,undefined", "-fno-sanitize-recover=all",
            "-I", str(tmp_path), str(HARNESS), "-o", str(binary),
        ],
        check=True,
    )
    subprocess.run([str(binary)], check=True)


def test_stage_a_wiring_in_fastpath():
    fp = FASTPATH.read_text()
    tmpl4 = _between(fp, "static struct wgfp_tmpl *wgfp_tmpl4(", "\n/* IPv6 outer")
    tmpl6 = _between(fp, "static struct wgfp_tmpl *wgfp_tmpl6(", "\nstatic void wgfp_pst_refresh(")
    rx_sync = _between(fp, "static void wgfp_pst_rx_sync(", "\n/* ---- learning ----")
    peer_frame = _between(fp, "static int wgfp_peer_frame(", "\nstatic const struct dpaa_wgfp_ops")
    rx_fast = _between(fp, "static int wgfp_rx_fast_frame(", "\n/*\n * P2b-3 slow-path punt")
    rx_frame = _between(fp, "static int wgfp_rx_frame(", "\nstatic int wgfp_peer_frame(")

    assert "wgfp_resolve_l2_path(" in tmpl4
    assert "wgfp_resolve_l2_path(" in tmpl6
    assert "wgfp_resolve_l2_path(" in rx_sync
    assert "wgfp_parse_l2(" in peer_frame
    assert "wgfp_parse_l2(" in rx_fast
    assert "t->pppoe_off" in peer_frame
    assert "t->l2_hlen" in peer_frame
    # PPPoE/VLAN non-P6 RX frames must go to full Linux stack (plain = false)
    assert "fl->t.vlan_vid || fl->t.pppoe_sid" in rx_frame
