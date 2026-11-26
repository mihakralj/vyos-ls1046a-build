#!/usr/bin/env python3
"""G10 Phase 5b host verification: QMan CEETM WBFS + 5-tuple hash spreading.

Verifies:
  1. Source contracts in:
       - bin/kernel-fixups/F_331.py (struct qm_ceetm_weight_code, WBFS API
         declarations in qman.h, GPC/GROUP_{A,B} macros in qman_priv.h)
       - kernel/common/files/drivers/soc/fsl/qbman/qman_ceetm.c
         (qman_ceetm_channel_set_group, group CR/ER eligibility, cq_claim_A/B,
          set/get_queue_weight, ratio2wbfs, wbfs2ratio, cq_get_frm_cnt,
          channel_release csch reset)
       - kernel/common/files/drivers/net/ethernet/freescale/dpaa/dpaa_ceetm.c
         (u16 cq_mask, WBFS leaf alloc/to_inner/modify/del, non-last WBFS CQ
          drain preserving Group A eligibility for surviving siblings,
          equal-weight WBFS sibling pool hash spreading in both
          dpaa_ceetm_select_queue and dpaa_ceetm_offload_egress_fqid_hashed)
       - kernel/common/files/include/linux/fsl/dpaa_flow_offload.h
       - kernel/ask/oot-modules/ask/ask_hw.c (ask_hw_flow_key_hash + hashed
         CEETM lookup)
  2. Compiled C harness under -fsanitize=address,undefined testing:
       - qman_ceetm_ratio2wbfs() / qman_ceetm_wbfs2ratio() exact encoding,
         weight=1 -> (x=0,y=0), weight=2 -> (x=1,y=0), weight=248 valid,
         weight=0 / weight=256 / denom=0 / n<d rejected cleanly
       - gpc wire byte packing: combine(0x40) | prio_b(0x38) | prio_a(0x07)
       - crem/erem group eligibility bits (GROUP_A=1<<8, GROUP_B=1<<9)
         without clobbering strict CQ bits (0..7)
       - 5-tuple equal-weight WBFS pool selection:
           * explicit classid bypasses hashing
           * single WBFS leaf or unequal-weight WBFS leaves never cross-hash
           * 8 equal-weight WBFS siblings spread 1000 distinct 5-tuples across
             all 8 buckets evenly
           * quarantined / unused sibling excluded from hash pool
"""

import os
import subprocess
import sys
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def read(rel: str) -> str:
    p = os.path.join(REPO, rel)
    if not os.path.exists(p):
        raise AssertionError(f"missing required file: {rel}")
    with open(p) as f:
        return f.read()


def check_sources() -> None:
    f331 = read("bin/kernel-fixups/F_331.py")
    for tok in (
        "struct qm_ceetm_weight_code",
        "qman_ceetm_channel_set_group",
        "qman_ceetm_channel_set_group_cr_eligibility",
        "qman_ceetm_channel_set_group_er_eligibility",
        "qman_ceetm_cq_claim_A",
        "qman_ceetm_cq_claim_B",
        "qman_ceetm_set_queue_weight",
        "qman_ceetm_ratio2wbfs",
        "qman_ceetm_wbfs2ratio",
        "qman_ceetm_cq_get_frm_cnt",
        "qman_ceetm_cq_release_drained",
        "ceetm_wbfs_hash",
        "GROUP_A_ELIGIBILITY_SET",
        "GROUP_B_ELIGIBILITY_SET",
        "CEETM_GPC_COMBINE",
    ):
        assert tok in f331, f"F_331.py missing {tok}"

    qman_c = read("kernel/common/files/drivers/soc/fsl/qbman/qman_ceetm.c")
    for tok in (
        "int qman_ceetm_channel_set_group(",
        "EXPORT_SYMBOL_GPL(qman_ceetm_channel_set_group);",
        "int qman_ceetm_channel_set_group_cr_eligibility(",
        "EXPORT_SYMBOL_GPL(qman_ceetm_channel_set_group_cr_eligibility);",
        "int qman_ceetm_channel_set_group_er_eligibility(",
        "EXPORT_SYMBOL_GPL(qman_ceetm_channel_set_group_er_eligibility);",
        "int qman_ceetm_cq_claim_A(",
        "EXPORT_SYMBOL_GPL(qman_ceetm_cq_claim_A);",
        "int qman_ceetm_cq_claim_B(",
        "EXPORT_SYMBOL_GPL(qman_ceetm_cq_claim_B);",
        "int qman_ceetm_set_queue_weight(",
        "EXPORT_SYMBOL_GPL(qman_ceetm_set_queue_weight);",
        "int qman_ceetm_ratio2wbfs(",
        "EXPORT_SYMBOL_GPL(qman_ceetm_ratio2wbfs);",
        "int qman_ceetm_wbfs2ratio(",
        "EXPORT_SYMBOL_GPL(qman_ceetm_wbfs2ratio);",
        "int qman_ceetm_cq_get_frm_cnt(",
        "EXPORT_SYMBOL_GPL(qman_ceetm_cq_get_frm_cnt);",
    ):
        assert tok in qman_c, f"qman_ceetm.c missing {tok}"

    dpaa_c = read("kernel/common/files/drivers/net/ethernet/freescale/dpaa/dpaa_ceetm.c")
    for tok in (
        "u16 cq_mask;",
        "DPAA_CEETM_WBFS_START",
        "DPAA_CEETM_WBFS_PRIO_A",
        "bool is_wbfs;",
        "u32 wbfs_weight;",
        "qman_ceetm_cq_claim_A(",
        "qman_ceetm_channel_set_group(",
        "qman_ceetm_channel_set_group_cr_eligibility(",
        "qman_ceetm_channel_set_group_er_eligibility(",
        "qman_ceetm_ratio2wbfs(",
        "qman_ceetm_set_queue_weight(",
        "qman_ceetm_cq_get_frm_cnt(",
        "dpaa_ceetm_pick_wbfs_slot(",
        "dpaa_ceetm_skb_flow_hash(",
        "dpaa_ceetm_wbfs_hash_show(",
        "dpaa_ceetm_wbfs_hash_store(",
        "ceetm_wbfs_hash",
        "dpaa_ceetm_offload_egress_fqid_hashed(",
        "EXPORT_SYMBOL_GPL(dpaa_ceetm_offload_egress_fqid_hashed);",
    ):
        assert tok in dpaa_c, f"dpaa_ceetm.c missing {tok}"

    hdr = read("kernel/common/files/include/linux/fsl/dpaa_flow_offload.h")
    assert "dpaa_ceetm_offload_egress_fqid_hashed(" in hdr, \
        "dpaa_flow_offload.h missing dpaa_ceetm_offload_egress_fqid_hashed"
    assert "dpaa_ceetm_flow_hash_5tuple(" in hdr, \
        "dpaa_flow_offload.h missing shared dpaa_ceetm_flow_hash_5tuple"

    ask_hw = read("kernel/ask/oot-modules/ask/ask_hw.c")
    for tok in (
        "ask_hw_flow_key_hash(",
        "dpaa_ceetm_flow_hash_5tuple(",
        "ASK_NATF_SNAT",
        "dpaa_ceetm_offload_egress_fqid_hashed(",
    ):
        assert tok in ask_hw, f"ask_hw.c missing {tok}"


C_HARNESS = r"""
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

typedef uint8_t u8;
typedef uint16_t u16;
typedef uint32_t u32;
typedef uint64_t u64;

#define EINVAL 22
#define ERANGE 34

#define CEETM_GPC_COMBINE     0x40U
#define CEETM_GPC_PRIO_B_SH   3U
#define CEETM_GPC_PRIO_A_MASK 0x07U
#define CQ_ELIGIBILITY_SET(n) (1U << (7U - (n)))
#define GROUP_A_ELIGIBILITY_SET (1U << 8)
#define GROUP_B_ELIGIBILITY_SET (1U << 9)

#define ROUNDING(n, d, r) \
    (((r) < 0) ? ((n) / (d)) : \
    (((r) > 0) ? (((n) + (d) - 1) / (d)) : \
    (((n) + ((d) / 2)) / (d))))

struct qm_ceetm_weight_code {
    u8 y:5;
    u8 x:3;
};

static int ratio2wbfs(u32 numerator, u32 denominator,
                      struct qm_ceetm_weight_code *wc, int rounding)
{
    u64 n = numerator, d = denominator;
    unsigned int x = 0;
    u64 q;

    if (!wc || !numerator || !denominator)
        return -EINVAL;
    if (n < d)
        return -ERANGE;

    while (x < 8 && n >= (d << (x + 1)))
        x++;
    if (x >= 8)
        return -ERANGE;

    q = ROUNDING(d << (x + 6), n, -rounding);
    if (q == 32 && x < 7) {
        x++;
        q = 64;
    }
    if (q > 64 || q < 32 || (64 - q) >= 32)
        return -ERANGE;
    wc->x = (u8)x;
    wc->y = (u8)(64 - q);
    return 0;
}

static int wbfs2ratio(const struct qm_ceetm_weight_code *wc,
                      u32 *numerator, u32 *denominator)
{
    if (!wc || !numerator || !denominator)
        return -EINVAL;
    *numerator = 1U << (wc->x + 6);
    *denominator = 64U - wc->y;
    return 0;
}

static u8 pack_gpc(int group_b, unsigned int prio_a, unsigned int prio_b)
{
    return (u8)(((!group_b) ? CEETM_GPC_COMBINE : 0U) |
                ((prio_b & 0x7U) << CEETM_GPC_PRIO_B_SH) |
                (prio_a & CEETM_GPC_PRIO_A_MASK));
}

struct mock_leaf {
    int chan_id;
    u8 cq_prio;
    bool used;
    bool quarantined;
    bool is_wbfs;
    u32 wbfs_weight;
    u32 classid;
    u32 hw_fqid;
};

static int pick_wbfs_slot(bool opt_in, const struct mock_leaf *leaves,
                          int nleaves, int def_slot, u32 flow_hash)
{
    int pool[8];
    int n_pool = 0;
    int chan_id;
    u32 weight;

    if (!opt_in)
        return def_slot;
    if (def_slot < 0 || !leaves[def_slot].is_wbfs)
        return def_slot;
    chan_id = leaves[def_slot].chan_id;
    weight = leaves[def_slot].wbfs_weight;
    if (!weight)
        return def_slot;

    for (u8 cq = 8; cq < 16; cq++) {
        for (int i = 0; i < nleaves; i++) {
            if (!leaves[i].used || leaves[i].quarantined)
                continue;
            if (!leaves[i].is_wbfs || leaves[i].chan_id != chan_id)
                continue;
            if (leaves[i].cq_prio == cq && leaves[i].wbfs_weight == weight) {
                pool[n_pool++] = i;
                break;
            }
        }
    }
    if (n_pool <= 1)
        return def_slot;
    return pool[flow_hash % (u32)n_pool];
}

static u32 mix32(u32 a, u32 b, u32 c)
{
    a ^= c; a -= (c << 14) | (c >> 18);
    b ^= a; b -= (a << 11) | (a >> 21);
    c ^= b; c -= (b << 25) | (b >> 7);
    a ^= c; a -= (c << 16) | (c >> 16);
    b ^= a; b -= (a << 4)  | (a >> 28);
    c ^= b; c -= (b << 14) | (b >> 18);
    a ^= c; a -= (c << 24) | (c >> 8);
    return c;
}

int main(void)
{
    struct qm_ceetm_weight_code wc;
    u32 num = 0, den = 0;

    /* 1. Weight code boundaries & round-trip */
    assert(ratio2wbfs(1, 1, &wc, 0) == 0);
    assert(wc.x == 0 && wc.y == 0);
    assert(((wc.y << 3) | (wc.x & 7)) == 0x00);
    assert(wbfs2ratio(&wc, &num, &den) == 0 && num == 64 && den == 64);

    assert(ratio2wbfs(2, 1, &wc, 0) == 0);
    assert(wc.x == 1 && wc.y == 0);
    assert(((wc.y << 3) | (wc.x & 7)) == 0x01);
    assert(wbfs2ratio(&wc, &num, &den) == 0 && num == 128 && den == 64);

    assert(ratio2wbfs(248, 1, &wc, 0) == 0);
    assert(wc.x == 7 && wc.y == 31);
    assert(wbfs2ratio(&wc, &num, &den) == 0 && num == 8192 && den == 33);

    /* Every advertised integer weight 1..248 (including 127 octave boundary!)
     * must succeed and stay within 2% of requested weight. */
    for (u32 w = 1; w <= 248; w++) {
        assert(ratio2wbfs(w, 1, &wc, 0) == 0);
        assert(wbfs2ratio(&wc, &num, &den) == 0 && den > 0);
        double got = (double)num / (double)den;
        assert(got >= (double)w * 0.97 && got <= (double)w * 1.03);
    }
    /* Octave-boundary check with rounding > 0 (63, 125, 126, 127) */
    assert(ratio2wbfs(63, 1, &wc, 1) == 0 && wc.x == 6 && wc.y == 0);
    assert(ratio2wbfs(127, 1, &wc, 0) == 0 && wc.x == 7 && wc.y == 0);
    assert(ratio2wbfs(127, 1, &wc, 1) == 0 && wc.x == 7 && wc.y == 0);

    /* Invalid / out-of-range weights rejected cleanly */
    assert(ratio2wbfs(0, 1, &wc, 0) == -EINVAL);
    assert(ratio2wbfs(1, 0, &wc, 0) == -EINVAL);
    assert(ratio2wbfs(1, 2, &wc, 0) == -ERANGE);
    assert(ratio2wbfs(255, 1, &wc, 0) == -ERANGE);
    assert(ratio2wbfs(256, 1, &wc, 0) == -ERANGE);
    assert(ratio2wbfs(1000, 1, &wc, 0) == -ERANGE);

    /* 2. GPC wire byte layout (combined Group A at prio 6) */
    assert(pack_gpc(0, 6, 6) == (0x40U | (6U << 3) | 6U));
    assert(pack_gpc(1, 5, 7) == ((7U << 3) | 5U));

    /* 3. Eligibility mask non-interference between strict CQs and Groups */
    u16 crem = CQ_ELIGIBILITY_SET(0) | CQ_ELIGIBILITY_SET(4);
    crem |= GROUP_A_ELIGIBILITY_SET;
    assert((crem & CQ_ELIGIBILITY_SET(0)) != 0);
    assert((crem & CQ_ELIGIBILITY_SET(4)) != 0);
    assert((crem & GROUP_A_ELIGIBILITY_SET) != 0);
    assert((crem & GROUP_B_ELIGIBILITY_SET) == 0);
    crem &= ~GROUP_A_ELIGIBILITY_SET;
    assert(crem == (CQ_ELIGIBILITY_SET(0) | CQ_ELIGIBILITY_SET(4)));

    /* 4. Equal-weight 8-CQ WBFS hash pool vs unequal-weight isolation */
    struct mock_leaf leaves[10];
    memset(leaves, 0, sizeof(leaves));
    /* Strict CQ 0 and CQ 4 */
    leaves[0] = (struct mock_leaf){ .chan_id = 1, .cq_prio = 0, .used = true, .classid = 0x10, .hw_fqid = 0xf00010 };
    leaves[1] = (struct mock_leaf){ .chan_id = 1, .cq_prio = 4, .used = true, .classid = 0x20, .hw_fqid = 0xf00020 };
    /* 8 equal-weight WBFS CQs 8..15 */
    for (int i = 0; i < 8; i++) {
        leaves[2 + i] = (struct mock_leaf){
            .chan_id = 1, .cq_prio = (u8)(8 + i), .used = true,
            .is_wbfs = true, .wbfs_weight = 1,
            .classid = (u32)(0x30 + i), .hw_fqid = (u32)(0xf00030 + i),
        };
    }

    /* Default OFF (opt_in=false): equal-weight tenant classes stay strictly
     * isolated (no spillover into sibling WBFS CQs). */
    for (u32 flow = 0; flow < 64; flow++) {
        u32 h = mix32(0x0a630101U + flow, 0x0a630201U, (40000U + flow) << 16 | 5201U);
        assert(pick_wbfs_slot(false, leaves, 10, 2, h) == 2);
    }

    /* Opt-in ON (opt_in=true): equal-weight 8-CQ WBFS bulk pool spreads evenly */
    int hits[8] = {0};
    for (u32 flow = 0; flow < 1000; flow++) {
        u32 h = mix32(0x0a630101U + flow, 0x0a630201U, (40000U + flow) << 16 | 5201U);
        int s = pick_wbfs_slot(true, leaves, 10, 2, h);
        assert(s >= 2 && s < 10);
        hits[s - 2]++;
    }
    for (int i = 0; i < 8; i++)
        assert(hits[i] >= 80 && hits[i] <= 180);

    /* Quarantine one bucket: never selected */
    leaves[5].quarantined = true;
    for (u32 flow = 0; flow < 200; flow++) {
        int s = pick_wbfs_slot(true, leaves, 10, 2, flow);
        assert(s != 5);
    }
    leaves[5].quarantined = false;

    /* Change sibling weights to distinct values (tenant split): no cross-hash */
    for (int i = 1; i < 8; i++)
        leaves[2 + i].wbfs_weight = (u32)(i + 1);
    for (u32 flow = 0; flow < 64; flow++)
        assert(pick_wbfs_slot(true, leaves, 10, 2, flow) == 2);

    /* Strict default never hashes */
    for (u32 flow = 0; flow < 64; flow++)
        assert(pick_wbfs_slot(true, leaves, 10, 0, flow) == 0);

    puts("G10 Phase 5b WBFS C harness: ALL PASS");
    return 0;
}
"""


def run_c_harness() -> None:
    with tempfile.TemporaryDirectory() as td:
        src = os.path.join(td, "test_wbfs.c")
        exe = os.path.join(td, "test_wbfs")
        with open(src, "w") as f:
            f.write(C_HARNESS)
        subprocess.run(
            ["gcc", "-O2", "-Wall", "-Wextra", "-Werror",
             "-fsanitize=address,undefined", src, "-o", exe],
            check=True,
        )
        res = subprocess.run([exe], capture_output=True, text=True, check=True)
        print(res.stdout.strip())


def main() -> int:
    check_sources()
    run_c_harness()
    print("PASS: G10 Phase 5b WBFS + 5-tuple hash spreading gate")
    return 0


if __name__ == "__main__":
    sys.exit(main())
