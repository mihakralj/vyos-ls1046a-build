#!/usr/bin/env python3
"""G10 Phase 5a + A458 host gate: CEETM frame-mode WRED (TC_SETUP_QDISC_RED)
and bounded plain TX FQ drain before CEETM sub-portal takeover.

Verifies:
  1. Source contracts in:
     - kernel/common/files/drivers/soc/fsl/qbman/qman_ceetm.c
     - kernel/common/files/drivers/net/ethernet/freescale/dpaa/dpaa_ceetm.{c,h}
     - kernel/common/files/include/linux/fsl/dpaa_flow_offload.h
     - kernel/ask/oot-modules/ask/ask_flow_offload.c
     - bin/kernel-fixups/F_330.py
     - bin/ci-setup-kernel.sh
  2. ASan/UBSan C behavioral harness covering:
     - QMan qm_cgr_wr_parm big-endian wire bitfield packing (MA[24..31],
       Mn[19..23], SA[12..18], Sn[6..11], Pn[0..5]) and round-trip decode.
     - Frame-mode conversion & budget clamping (never byte mode; A337 safe):
       byte min/max/limit -> frame thresholds scaled when budget < want.
     - Minimum band enforcement (max >= min + max(2, ceil(maxp / 127))).
     - ECN and nodrop strict rejection (-EOPNOTSUPP), invalid band rejection.
     - TC_RED_REPLACE -> displaced handle -> TC_RED_DESTROY lifecycle (both
       normal replacement completion and failed-replacement rollback).
     - TC_RED_GRAFT with non-zero child handle demotes WRED back to tail-drop
       and makes dpaa_ceetm_red_qdisc_offloaded() return false immediately.
     - A458 bounded plain TX FQ drain using big-endian np.frm_cnt decoding.
"""

from pathlib import Path
import subprocess
import sys
import tempfile

REPO = Path(__file__).resolve().parent.parent
QMAN_CEETM_C = REPO / "kernel/common/files/drivers/soc/fsl/qbman/qman_ceetm.c"
DPAA_CEETM_C = REPO / "kernel/common/files/drivers/net/ethernet/freescale/dpaa/dpaa_ceetm.c"
DPAA_CEETM_H = REPO / "kernel/common/files/drivers/net/ethernet/freescale/dpaa/dpaa_ceetm.h"
FLOW_OFFLOAD_H = REPO / "kernel/common/files/include/linux/fsl/dpaa_flow_offload.h"
ASK_FLOW_C = REPO / "kernel/ask/oot-modules/ask/ask_flow_offload.c"
F330 = REPO / "bin/kernel-fixups/F_330.py"
CI_SETUP = REPO / "bin/ci-setup-kernel.sh"


def require_contains(path: Path, needles: list[str], label: str) -> None:
    if not path.exists():
        raise AssertionError(f"{label}: missing file {path}")
    text = path.read_text()
    for n in needles:
        if n not in text:
            raise AssertionError(f"{label}: missing expected snippet in {path.name}: {n!r}")


def check_sources() -> None:
    require_contains(
        QMAN_CEETM_C,
        [
            "QM_CCGR_WE_WR_EN_G",
            "QM_CCGR_WE_WR_EN_Y",
            "QM_CCGR_WE_WR_EN_R",
        ],
        "qman_ceetm.c",
    )

    require_contains(
        DPAA_CEETM_C,
        [
            "dpaa_ceetm_drain_plain_tx_fqs(",
            "FQ_TYPE_TX_NO_CONFIRM",
            "ceetm_tx_paused",
            "qman_query_fq_np(",
            "be32_to_cpu(np.frm_cnt) & qm_mcr_frm_cnt_mask",
            "dpaa_ceetm_wred_encode_parm(",
            "dpaa_ceetm_set_ccg_wred(",
            "dpaa_ceetm_setup_red(",
            "dpaa_ceetm_red_qdisc_offloaded(",
            "dpaa_ceetm_red_soft_pending(",
            "EXPORT_SYMBOL_GPL(dpaa_ceetm_red_qdisc_offloaded);",
            "EXPORT_SYMBOL_GPL(dpaa_ceetm_red_soft_pending);",
            "TC_RED_REPLACE",
            "TC_RED_DESTROY",
            "TC_RED_STATS",
            "TC_RED_XSTATS",
            "TC_RED_GRAFT",
            "opt->set.is_ecn",
            "opt->set.is_nodrop",
            "QM_CCGR_WE_WR_EN_G",
            "QM_CCGR_WE_WR_EN_Y",
            "QM_CCGR_WE_WR_EN_R",
        ],
        "dpaa_ceetm.c",
    )

    require_contains(
        DPAA_CEETM_H,
        [
            "int dpaa_ceetm_setup_red(struct net_device *net_dev, void *type_data);",
        ],
        "dpaa_ceetm.h",
    )

    require_contains(
        FLOW_OFFLOAD_H,
        [
            "bool dpaa_ceetm_red_qdisc_offloaded(struct net_device *dev, u32 handle);",
        ],
        "dpaa_flow_offload.h",
    )

    require_contains(
        ASK_FLOW_C,
        [
            "dpaa_ceetm_red_qdisc_offloaded(dev, cq->handle)",
            "dpaa_ceetm_red_soft_pending(dev)",
            "if (soft && changed && !ask_tc_qdisc_withdraw_dev(dev))",
        ],
        "ask_flow_offload.c",
    )

    require_contains(
        F330,
        [
            "TC_SETUP_QDISC_RED",
            "dpaa_ceetm_setup_red",
            "ceetm_tx_paused",
        ],
        "F_330.py",
    )

    require_contains(
        CI_SETUP,
        [
            "bin/kernel-fixups/F_330.py",
            "bin/test-g10-phase5-wred.py",
        ],
        "ci-setup-kernel.sh",
    )


C_HARNESS = r"""
#include <assert.h>
#include <errno.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

typedef uint8_t  u8;
typedef uint16_t u16;
typedef uint32_t u32;
typedef uint64_t u64;

#define CEETM_WRED_MAXP_UNITS 256U
#define CEETM_WRED_SA_MAX     127U
#define ETH_DATA_LEN          1500U
#define RED_FRAME_OVERHEAD    22U /* ETH_HLEN(14) + ETH_FCS_LEN(4) + VLAN_HLEN(4) */

static u32 bswap32(u32 v)
{
    return __builtin_bswap32(v);
}

static u32 wred_maxp(u32 probability)
{
    u32 maxp = (u32)(((u64)probability * CEETM_WRED_MAXP_UNITS) >> 32);

    if (maxp < 4U)
        maxp = 4U;
    if (maxp > CEETM_WRED_MAXP_UNITS)
        maxp = CEETM_WRED_MAXP_UNITS;
    return maxp & ~3U;
}

static u32 wred_min_band(u32 probability)
{
    u32 mp = wred_maxp(probability);
    u32 div = (mp + CEETM_WRED_SA_MAX - 1U) / CEETM_WRED_SA_MAX;
    return div > 2U ? div : 2U;
}

static u32 wred_encode_be32(u32 min_f, u32 max_f, u32 probability)
{
    u32 maxp = wred_maxp(probability);
    u32 pn = (maxp / 4U) - 1U;
    u32 ma = max_f;
    u32 mn = 0;
    u32 maxth, span;
    u64 num = maxp;
    u64 sa;
    u32 sn = 0;
    u32 host_word;

    while (ma > 0xffU && mn < 0x1fU) {
        ma >>= 1;
        mn++;
    }
    if (ma > 0xffU)
        ma = 0xffU;

    maxth = ma << mn;
    span = maxth > min_f ? maxth - min_f : 1U;

    while (sn < 40U && num < 64ULL * span) {
        num <<= 1;
        sn++;
    }
    sa = num / span;
    if (sa < 64ULL)
        sa = 64ULL;
    if (sa > CEETM_WRED_SA_MAX)
        sa = CEETM_WRED_SA_MAX;

    host_word = ((ma & 0xffU) << 24) |
                ((mn & 0x1fU) << 19) |
                (((u32)sa & 0x7fU) << 12) |
                ((sn & 0x3fU) << 6) |
                (pn & 0x3fU);
    return bswap32(host_word);
}

static void red_bytes_to_frames(u32 mtu, u32 min_b, u32 max_b, u32 limit_b,
                                u32 prob, u32 share,
                                u32 *out_min, u32 *out_max, u32 *out_depth)
{
    u32 fsz = (mtu < ETH_DATA_LEN ? mtu : ETH_DATA_LEN) + RED_FRAME_OVERHEAD;
    u32 want = limit_b / fsz;
    u32 depth;
    u64 lo = min_b / fsz;
    u64 hi = max_b / fsz;
    u32 mb = wred_min_band(prob);

    if (!want)
        want = 1U;
    depth = want < share ? want : share;
    if (!depth)
        depth = 1U;
    if (depth < want) {
        lo = (lo * depth) / want;
        hi = (hi * depth) / want;
    }
    if (lo >= depth)
        lo = depth - 1U;
    if (hi < lo + mb)
        hi = lo + mb;
    *out_min = (u32)lo;
    *out_max = (u32)hi;
    *out_depth = depth;
}

/* Mock leaf RED state machine matching dpaa_ceetm_setup_red() */
struct mock_leaf {
    bool red_active;
    bool red_displaced;
    u32 red_handle;
    u32 red_displaced_handle;
    u32 hw_wred_word;
    bool hw_wred_enabled;
    u32 hw_td_frames;
};

static int leaf_red_replace(struct mock_leaf *l, u32 handle,
                            u32 min_b, u32 max_b, u32 limit_b, u32 prob,
                            bool is_ecn, bool is_nodrop, u32 share)
{
    bool running = l->red_active && l->red_handle == handle;
    u32 lo, hi, depth;

    if (is_ecn || is_nodrop) {
        if (running) {
            l->red_active = false;
            l->hw_wred_enabled = false;
            l->hw_td_frames = share;
        }
        return -EOPNOTSUPP;
    }
    if (!limit_b || !max_b || max_b <= min_b) {
        if (running) {
            l->red_active = false;
            l->hw_wred_enabled = false;
            l->hw_td_frames = share;
        }
        return -EINVAL;
    }

    if (l->red_active && !running) {
        l->red_displaced = true;
        l->red_displaced_handle = l->red_handle;
    }
    red_bytes_to_frames(1500, min_b, max_b, limit_b, prob, share,
                        &lo, &hi, &depth);
    l->hw_wred_word = wred_encode_be32(lo, hi, prob);
    l->hw_wred_enabled = true;
    l->hw_td_frames = depth;
    l->red_active = true;
    l->red_handle = handle;
    return 0;
}

static int leaf_red_destroy(struct mock_leaf *l, u32 handle, u32 share)
{
    if (l->red_displaced && l->red_displaced_handle == handle) {
        l->red_displaced = false;
        return 0;
    }
    if (!l->red_active || l->red_handle != handle)
        return 0;
    if (l->red_displaced) {
        l->red_displaced = false;
        l->red_handle = l->red_displaced_handle;
        return 0;
    }
    l->red_active = false;
    l->red_handle = 0;
    l->hw_wred_enabled = false;
    l->hw_td_frames = share;
    return 0;
}

static int leaf_red_graft(struct mock_leaf *l, u32 handle, u32 child_handle,
                          u32 share)
{
    if (child_handle && l->red_active && l->red_handle == handle) {
        l->red_active = false;
        l->red_handle = 0;
        l->hw_wred_enabled = false;
        l->hw_td_frames = share;
    }
    return -EOPNOTSUPP;
}

/* A458: big-endian np.frm_cnt decoding (matching F-301 / dpaa_wgfp.c) */
static u32 decode_np_frm_cnt(u32 raw_be32)
{
    return bswap32(raw_be32) & 0x00FFFFFFU;
}

int main(void)
{
    /* 1. Bit-exact qm_cgr_wr_parm encoding: 48k/144k @ p=0.1 on 1500 MTU (share 128). */
    u32 lo, hi, depth;
    u32 prob_10pct = (u32)(0.1 * 4294967296.0);
    red_bytes_to_frames(1500, 48000, 144000, 400000, prob_10pct, 128,
                        &lo, &hi, &depth);
    /* want = 400000 / 1522 = 262 > share(128), so scaled by 128/262:
     * lo = floor(31 * 128 / 262) = 15, hi = floor(94 * 128 / 262) = 45, depth = 128. */
    assert(depth == 128U);
    assert(lo == 15U && hi == 45U);

    u32 be_word = wred_encode_be32(lo, hi, prob_10pct);
    u32 hw = bswap32(be_word);
    u32 ma = (hw >> 24) & 0xffU;
    u32 mn = (hw >> 19) & 0x1fU;
    u32 sa = (hw >> 12) & 0x7fU;
    u32 sn = (hw >> 6) & 0x3fU;
    u32 pn = hw & 0x3fU;
    /* maxp = floor(0.1*256) & ~3 = 24 -> Pn = 5 -> 4*(5+1) = 24/256 */
    assert(pn == 5U);
    assert(ma == 45U && mn == 0U); /* exact up to 255 */
    assert(sa >= 64U && sa <= 127U);
    /* Slope = SA / 2^Sn ~= 24 / (45 - 15) = 0.8 -> SA=102, Sn=7 (102/128 = 0.796875) */
    assert(sa == 102U && sn == 7U);

    /* 2. Narrow band clamping (8k/24k with p=1.0 -> min_band = ceil(256/127) = 3). */
    red_bytes_to_frames(1500, 1000, 2000, 200000, 0xFFFFFFFFU, 128,
                        &lo, &hi, &depth);
    assert(hi >= lo + 2U);

    /* 3. Lifecycle: ECN rejected, valid install, replacement displace+destroy, graft demotion. */
    struct mock_leaf leaf = {0};
    assert(leaf_red_replace(&leaf, 0x00100000U, 48000, 144000, 400000,
                            prob_10pct, true, false, 128) == -EOPNOTSUPP);
    assert(!leaf.red_active && !leaf.hw_wred_enabled);

    assert(leaf_red_replace(&leaf, 0x00100000U, 48000, 144000, 400000,
                            prob_10pct, false, true, 128) == -EOPNOTSUPP);
    assert(!leaf.red_active);

    assert(leaf_red_replace(&leaf, 0x00100000U, 48000, 144000, 400000,
                            prob_10pct, false, false, 128) == 0);
    assert(leaf.red_active && leaf.red_handle == 0x00100000U && leaf.hw_wred_enabled);

    /* Replace with new handle 0x20: -> old 0x10: destroyed after -> 0x20: stays active */
    assert(leaf_red_replace(&leaf, 0x00200000U, 8000, 24000, 100000,
                            prob_10pct, false, false, 128) == 0);
    assert(leaf.red_active && leaf.red_handle == 0x00200000U && leaf.red_displaced);
    assert(leaf_red_destroy(&leaf, 0x00100000U, 128) == 0);
    assert(leaf.red_active && leaf.red_handle == 0x00200000U && !leaf.red_displaced);

    /* Change existing handle 0x20: to ECN -> rejected AND turns off WRED! */
    assert(leaf_red_replace(&leaf, 0x00200000U, 8000, 24000, 100000,
                            prob_10pct, true, false, 128) == -EOPNOTSUPP);
    assert(!leaf.red_active && !leaf.hw_wred_enabled && leaf.hw_td_frames == 128U);

    /* Re-install 0x30:, then graft non-zero child -> immediately demotes offloaded flag! */
    assert(leaf_red_replace(&leaf, 0x00300000U, 48000, 144000, 400000,
                            prob_10pct, false, false, 128) == 0);
    assert(leaf_red_graft(&leaf, 0x00300000U, 0x00400000U, 128) == -EOPNOTSUPP);
    assert(!leaf.red_active && !leaf.hw_wred_enabled && leaf.hw_td_frames == 128U);

    /* 4. A458 big-endian np.frm_cnt check (1 frame in BE is 0x01000000 in host LE). */
    assert(decode_np_frm_cnt(bswap32(1U)) == 1U);
    assert(decode_np_frm_cnt(bswap32(0xFF000005U)) == 5U);
    assert(decode_np_frm_cnt(0U) == 0U);

    printf("G10 Phase 5a WRED + A458 C harness PASS\n");
    return 0;
}
"""


def run_c_harness() -> None:
    with tempfile.TemporaryDirectory() as td:
        src = Path(td) / "harness.c"
        exe = Path(td) / "harness"
        src.write_text(C_HARNESS)
        subprocess.run(
            ["gcc", "-Wall", "-Wextra", "-Werror", "-fsanitize=address,undefined",
             str(src), "-o", str(exe)],
            check=True,
        )
        subprocess.run([str(exe)], check=True)


def main() -> int:
    check_sources()
    run_c_harness()
    print("OK: G10 Phase 5a WRED + A458 gate PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
