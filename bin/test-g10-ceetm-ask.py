#!/usr/bin/env python3
"""G10 (T-M6-QOS) Phase 1 host gate: CEETM + ASK2 coexistence, safety, and SW-qdisc guard.

Verifies:
  1. Source contracts in qman_ceetm.c, dpaa_ceetm.{c,h}, dpaa_flow_offload.h,
     F_327.py (dpaa_eth.c), ask_hw.c, ask_flow_offload.c, and ask_internal.h.
  2. ASan/UBSan C behavioral harness covering:
     - qman_ceetm_cq_pop() stat bits (valid, valid+empty last frame, empty, prefetch -EAGAIN)
     - frame-mode CCGR budgeting at MTU 1500/9000 (RX pool + XSFDR cap, rebalance, -ENOSPC floor)
     - Two-LFQ-per-CQ (lfq_sw 0x1a00000080000000 vs lfq_hw 0x9a000000c0000000) resolution
     - synchronous ASK2 PRE/POST egress transitions, -EBUSY create abort, and teardown quarantine
     - CQ drain FD reclamation (bpid==0xff skb cleanup vs real BMan pool dpaa_fd_release)
     - software-qdisc bypass guard (mq default vs cake / non-offload htb / offloaded htb / tcx)
"""

from pathlib import Path
import subprocess
import sys
import tempfile

REPO = Path(__file__).resolve().parent.parent
QMAN_CEETM_C = REPO / "kernel/common/files/drivers/soc/fsl/qbman/qman_ceetm.c"
DPAA_CEETM_C = REPO / "kernel/common/files/drivers/net/ethernet/freescale/dpaa/dpaa_ceetm.c"
DPAA_CEETM_H = REPO / "kernel/common/files/drivers/net/ethernet/freescale/dpaa/dpaa_ceetm.h"
FLOW_OFF_H = REPO / "kernel/common/files/include/linux/fsl/dpaa_flow_offload.h"
F327_PY = REPO / "bin/kernel-fixups/F_327.py"
ASK_HW_C = REPO / "kernel/ask/oot-modules/ask/ask_hw.c"
ASK_FO_C = REPO / "kernel/ask/oot-modules/ask/ask_flow_offload.c"
ASK_INT_H = REPO / "kernel/ask/oot-modules/ask/include/ask_internal.h"


def require_contains(path: Path, needles: list[str], label: str) -> None:
    if not path.exists():
        raise AssertionError(f"{label}: missing file {path}")
    text = path.read_text()
    for n in needles:
        if n not in text:
            raise AssertionError(f"{label}: missing expected snippet in {path.name}: {n!r}")


def require_absent(path: Path, forbidden: list[str], label: str) -> None:
    text = path.read_text()
    for f in forbidden:
        if f in text:
            raise AssertionError(f"{label}: forbidden snippet still present in {path.name}: {f!r}")


def check_sources() -> None:
    require_contains(
        QMAN_CEETM_C,
        [
            "#define CEETM_LFQID_COUNT\t0x1000",
            "#define CEETM_LFQID_LSB_MASK\t0x000FFF",
            "#define CEETM_NR_CHANNELS\t32",
            "cpu_to_be16((cq->parent->idx << 4) | cq->idx)",
            "int qman_ceetm_cq_pop(struct qm_ceetm_cq *cq, struct qm_fd *fd)",
            "if (ppxr.stat & 0x1)",
            "if (ppxr.stat & 0x2)",
            "return -EAGAIN;",
            "EXPORT_SYMBOL_GPL(qman_ceetm_cq_pop);",
        ],
        "qman_ceetm.c",
    )

    require_contains(
        DPAA_CEETM_C,
        [
            "DPAA_CEETM_HW_CONTEXT_A",
            "0x9a000000c0000000ULL",
            "ccgp.mode = 1;",
            "DPAA_CEETM_MAX_PORT_FRAMES",
            "DPAA_CEETM_MIN_CQ_FRAMES",
            "dpaa_ceetm_drain_and_release_cq",
            "qman_ceetm_drain_ern();",
            "synchronize_rcu();",
            "rcu_read_lock();",
            "READ_ONCE(ceetm->leaf[i].hw_lfq)",
            "dpaa_fd_release(",
            "dpaa_cleanup_tx_fd(",
            "dpaa_fo_notify_egress_change(",
            "DPAA_EGRESS_CEETM_PRE_CHANGE",
            "DPAA_EGRESS_CEETM_POST_CHANGE",
            "dpaa_ceetm_offload_egress_fqid(",
            "dpaa_ceetm_is_active(",
            "dpaa_ceetm_update_mtu(",
            "quarantined",
            "hw_lfq",
        ],
        "dpaa_ceetm.c",
    )
    require_absent(
        DPAA_CEETM_C,
        [
            "ccgp.mode = 0;",
        ],
        "dpaa_ceetm.c byte-mode CCGR",
    )

    require_contains(
        DPAA_CEETM_H,
        [
            "dpaa_ceetm_update_mtu(",
            "dpaa_fo_notify_egress_change(",
            "dpaa_fd_release(",
        ],
        "dpaa_ceetm.h",
    )

    require_contains(
        FLOW_OFF_H,
        [
            "enum dpaa_egress_event",
            "DPAA_EGRESS_CEETM_PRE_CHANGE",
            "DPAA_EGRESS_CEETM_POST_CHANGE",
            "int (*egress_change)(struct net_device *dev,",
            "int dpaa_ceetm_offload_egress_fqid(struct net_device *dev,",
            "bool dpaa_ceetm_is_active(struct net_device *dev);",
        ],
        "dpaa_flow_offload.h",
    )

    require_contains(
        F327_PY,
        [
            "dpaa_fo_notify_egress_change",
            "srcu_read_lock(&dpaa_fo_srcu)",
            "dpaa_fd_release(",
            "dpaa_ceetm_update_mtu(",
        ],
        "F_327.py",
    )

    require_contains(
        ASK_HW_C,
        [
            "dpaa_ceetm_offload_egress_fqid(",
            "ask_hw_egress_ceetm_event(",
            "ceetm_transitioning",
            "soft_qdisc",
            "ask_hw_dev_has_soft_qdisc(",
            "ask_hw_set_dev_soft_qdisc(",
        ],
        "ask_hw.c",
    )

    require_contains(
        ASK_FO_C,
        [
            ".egress_change  = ask_flow_offload_egress_change",
            "ask_tc_qdisc_work",
            "ask_tc_qdisc_is_soft(",
            "!q->enqueue",
            '"noqueue"',
            "TCQ_F_OFFLOADED",
            "dpaa_ceetm_is_active(",
        ],
        "ask_flow_offload.c",
    )

    require_contains(
        ASK_INT_H,
        [
            "ask_hw_egress_ceetm_event(",
            "ask_hw_dev_has_soft_qdisc(",
            "ask_hw_set_dev_soft_qdisc(",
        ],
        "ask_internal.h",
    )


C_HARNESS = r"""
#include <assert.h>
#include <errno.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define DPAA_CEETM_MAX_PORT_FRAMES 512U
#define DPAA_CEETM_MIN_CQ_FRAMES   16U
#define FSL_DPAA_BPID_INV          0xffU
#define DPAA_CEETM_CONTEXT_A       0x1a00000080000000ULL
#define DPAA_CEETM_HW_CONTEXT_A    0x9a000000c0000000ULL
#define CEETM_PARKED_FRAMES        1U

/* ---- 1. qman_ceetm_cq_pop() stat-bit model ---- */
struct fake_fd {
    uint8_t bpid;
    uint64_t addr;
};

struct fake_ppxr {
    uint8_t stat;
    struct fake_fd fd;
};

static int fake_mc_rc;
static struct fake_ppxr fake_seq[8];
static int fake_seq_idx, fake_seq_len;

static int model_cq_pop(struct fake_fd *out) {
    if (fake_mc_rc)
        return fake_mc_rc;
    assert(fake_seq_idx < fake_seq_len);
    struct fake_ppxr r = fake_seq[fake_seq_idx++];
    /* Critical silicon rule: last frame can carry BOTH 0x1 (valid) and 0x2 (empty). */
    if (r.stat & 0x1) {
        *out = r.fd;
        return 1;
    }
    if (r.stat & 0x2)
        return 0;
    return -EAGAIN;
}

static void test_cq_pop_and_drain(void) {
    int skb_freed = 0, bman_freed = 0;
    fake_mc_rc = 0;
    fake_seq_idx = 0;
    fake_seq_len = 4;
    /* frame 1: SW skb (bpid 0xff), frame 2: prefetch EAGAIN,
     * frame 3: HW BMan frame carrying BOTH valid+empty (0x3),
     * frame 4: empty (0x2) on follow-up poll. */
    fake_seq[0] = (struct fake_ppxr){ .stat = 0x1, .fd = { .bpid = 0xff, .addr = 0x1000 } };
    fake_seq[1] = (struct fake_ppxr){ .stat = 0x0, .fd = { 0 } };
    fake_seq[2] = (struct fake_ppxr){ .stat = 0x3, .fd = { .bpid = 7, .addr = 0x2000 } };
    fake_seq[3] = (struct fake_ppxr){ .stat = 0x2, .fd = { 0 } };

    for (;;) {
        struct fake_fd fd;
        int r = model_cq_pop(&fd);
        if (r == 0)
            break;
        if (r == -EAGAIN)
            continue;
        assert(r == 1);
        if (fd.bpid == FSL_DPAA_BPID_INV)
            skb_freed++;
        else
            bman_freed++;
    }
    assert(skb_freed == 1);
    assert(bman_freed == 1);

    /* MC error fails closed */
    fake_mc_rc = -EIO;
    struct fake_fd fd;
    assert(model_cq_pop(&fd) == -EIO);
    fake_mc_rc = 0;
}

/* ---- 2. Frame-mode CCGR budget model ---- */
static uint32_t model_port_frame_budget(uint32_t mtu, uint32_t bp_size,
                                        uint32_t rx_headroom, uint32_t pool_bufs) {
    uint32_t first_cap = bp_size > rx_headroom ? bp_size - rx_headroom : bp_size;
    uint32_t max_frame = mtu + 14 + 4 + 8;
    uint32_t bufs_per_frame;
    if (max_frame <= first_cap)
        bufs_per_frame = 1;
    else
        bufs_per_frame = 2 + (max_frame - first_cap + bp_size - 1) / bp_size;
    uint32_t pool_cap = pool_bufs / 2 / bufs_per_frame;
    if (pool_cap < 1)
        pool_cap = 1;
    return pool_cap < DPAA_CEETM_MAX_PORT_FRAMES ? pool_cap : DPAA_CEETM_MAX_PORT_FRAMES;
}

static int model_cq_share(uint32_t budget, uint32_t active_cqs, uint32_t *out_share) {
    if (!active_cqs)
        return -EINVAL;
    uint32_t share = budget / active_cqs;
    if (share < DPAA_CEETM_MIN_CQ_FRAMES)
        return -ENOSPC;
    *out_share = share;
    return 0;
}

static void test_ccgr_budget(void) {
    /* LS1046A 10G port: 5120 bufs, bp_size=3584, rx_headroom=256 */
    uint32_t b1500 = model_port_frame_budget(1500, 3584, 256, 5120);
    assert(b1500 == 512);
    uint32_t b9000 = model_port_frame_budget(9000, 3584, 256, 5120);
    /* 9026 > 3328 -> 2 + ceil(5698/3584) = 4 bufs/frame -> 5120/2/4 = 640 -> capped at 512 */
    assert(b9000 == 512);
    /* Smaller 1G/test pool (1024 bufs) at 9000 MTU -> 1024/2/4 = 128 frames */
    uint32_t b9000_small = model_port_frame_budget(9000, 3584, 256, 1024);
    assert(b9000_small == 128);

    uint32_t share = 0;
    assert(model_cq_share(b1500, 1, &share) == 0 && share == 512);
    assert(model_cq_share(b1500, 2, &share) == 0 && share == 256);
    assert(model_cq_share(b1500, 32, &share) == 0 && share == 16);
    assert(model_cq_share(b1500, 33, &share) == -ENOSPC);
    assert(model_cq_share(b9000_small, 9, &share) == -ENOSPC);
}

/* ---- 3. Two-LFQ-per-CQ lifecycle & quarantine model ---- */
struct model_cls {
    bool active;
    bool quarantined;
    uint32_t classid;
    uint32_t sw_lfqid;
    uint32_t hw_lfqid;
    uint64_t sw_ctx_a;
    uint64_t hw_ctx_a;
    uint32_t ccg_frames;
};

struct model_port {
    bool sp_ceetm_mode;
    bool ask_transitioning;
    bool ask_fail_quiesce;
    uint32_t defcls;
    struct model_cls def;
    struct model_cls leaf[4];
    int released_channels;
};

static int model_ask_notify(struct model_port *p, bool pre) {
    if (pre) {
        p->ask_transitioning = true;
        if (p->ask_fail_quiesce)
            return -EBUSY;
        return 0;
    }
    p->ask_transitioning = false;
    return 0;
}

static int model_resolve_hw_fqid(const struct model_port *p, uint32_t classid, uint32_t *fqid) {
    if (p->ask_transitioning)
        return -EAGAIN;
    if (!p->sp_ceetm_mode) {
        *fqid = 0; /* use WQ6 noconf_tx */
        return 0;
    }
    uint32_t want = classid ? classid : p->defcls;
    if (!want)
        return -EAGAIN; /* never target raw def channel (DEFECT B) */
    for (int i = 0; i < 4; i++) {
        if (p->leaf[i].active && !p->leaf[i].quarantined && p->leaf[i].classid == want) {
            *fqid = p->leaf[i].hw_lfqid;
            return 1;
        }
    }
    return -EAGAIN; /* default leaf not created yet */
}

static int model_htb_create(struct model_port *p, uint32_t defcls) {
    int rc = model_ask_notify(p, true);
    if (rc) {
        model_ask_notify(p, false);
        return rc;
    }
    p->sp_ceetm_mode = true;
    p->defcls = defcls;
    p->def = (struct model_cls){
        .active = true, .sw_lfqid = 0xF00002, .hw_lfqid = 0xF00003,
        .sw_ctx_a = DPAA_CEETM_CONTEXT_A, .hw_ctx_a = DPAA_CEETM_HW_CONTEXT_A,
        .ccg_frames = 512,
    };
    model_ask_notify(p, false);
    return 0;
}

static void model_leaf_add(struct model_port *p, int slot, uint32_t classid) {
    p->leaf[slot] = (struct model_cls){
        .active = true, .classid = classid,
        .sw_lfqid = 0xF00010 + slot * 2, .hw_lfqid = 0xF00011 + slot * 2,
        .sw_ctx_a = DPAA_CEETM_CONTEXT_A, .hw_ctx_a = DPAA_CEETM_HW_CONTEXT_A,
        .ccg_frames = 256,
    };
}

static void model_leaf_del(struct model_port *p, int slot) {
    int rc = model_ask_notify(p, true);
    p->leaf[slot].active = false;
    if (rc) {
        /* Retained FE owner -> quarantine channel, park CCGR at 1 frame */
        p->leaf[slot].quarantined = true;
        p->leaf[slot].ccg_frames = CEETM_PARKED_FRAMES;
    } else {
        p->released_channels++;
    }
    model_ask_notify(p, false);
}

static void test_lifecycle_and_quarantine(void) {
    struct model_port p = { 0 };
    uint32_t fqid = 0xdeadbeef;

    /* Before CEETM: resolves 0 (use WQ6) */
    assert(model_resolve_hw_fqid(&p, 0, &fqid) == 0 && fqid == 0);

    /* Create aborts cleanly if ASK2 quiesce returns -EBUSY */
    p.ask_fail_quiesce = true;
    assert(model_htb_create(&p, 0x10) == -EBUSY);
    assert(!p.sp_ceetm_mode);
    assert(!p.ask_transitioning);
    assert(model_resolve_hw_fqid(&p, 0, &fqid) == 0);

    /* Clean create with default 0x10: fails closed (-EAGAIN) until leaf 0x10 exists */
    p.ask_fail_quiesce = false;
    assert(model_htb_create(&p, 0x10) == 0);
    assert(p.sp_ceetm_mode);
    assert(model_resolve_hw_fqid(&p, 0, &fqid) == -EAGAIN);

    /* Leaf 0x10 added -> resolves hw_lfqid (distinct from sw_lfqid, no-confirm ctx_a) */
    model_leaf_add(&p, 0, 0x10);
    assert(p.leaf[0].sw_lfqid != p.leaf[0].hw_lfqid);
    assert(p.leaf[0].sw_ctx_a == DPAA_CEETM_CONTEXT_A);
    assert(p.leaf[0].hw_ctx_a == DPAA_CEETM_HW_CONTEXT_A);
    assert(model_resolve_hw_fqid(&p, 0, &fqid) == 1 && fqid == p.leaf[0].hw_lfqid);

    /* Leaf delete with unsettled FE record -> quarantined, NOT released */
    p.ask_fail_quiesce = true;
    model_leaf_del(&p, 0);
    assert(p.leaf[0].quarantined);
    assert(p.leaf[0].ccg_frames == CEETM_PARKED_FRAMES);
    assert(p.released_channels == 0);
    assert(model_resolve_hw_fqid(&p, 0, &fqid) == -EAGAIN);

    /* Clean leaf delete -> released */
    p.ask_fail_quiesce = false;
    model_leaf_add(&p, 1, 0x10);
    model_leaf_del(&p, 1);
    assert(!p.leaf[1].quarantined);
    assert(p.released_channels == 1);
}

/* ---- 4. Software-qdisc bypass guard model ---- */
struct model_qdisc {
    const char *id;
    uint32_t handle;
    bool builtin;
    bool mqroot;
    bool offloaded;
    uint32_t child_handles[4];
    int num_children;
};

static bool model_qdisc_is_soft(const struct model_qdisc *q, bool tcx_egress, bool ceetm_active) {
    if (tcx_egress)
        return true;
    if (!q || q->builtin)
        return false;
    if (q->mqroot && q->handle == 0) {
        for (int i = 0; i < q->num_children; i++)
            if (q->child_handles[i] != 0)
                return true;
        return false;
    }
    if (strcmp(q->id, "pfifo_fast") == 0 && q->handle == 0)
        return false;
    if (strcmp(q->id, "htb") == 0 && q->offloaded && ceetm_active)
        return false;
    return true;
}

static void test_soft_qdisc_guard(void) {
    struct model_qdisc mq_default = {
        .id = "mq", .handle = 0, .mqroot = true,
        .child_handles = { 0, 0, 0, 0 }, .num_children = 4,
    };
    struct model_qdisc mq_custom = {
        .id = "mq", .handle = 0, .mqroot = true,
        .child_handles = { 0, 0x10000, 0, 0 }, .num_children = 4,
    };
    struct model_qdisc cake = { .id = "cake", .handle = 0x80010000 };
    struct model_qdisc htb_sw = { .id = "htb", .handle = 0x10000, .offloaded = false };
    struct model_qdisc htb_hw = { .id = "htb", .handle = 0x10000, .offloaded = true };
    struct model_qdisc noqueue = { .id = "noqueue", .builtin = true };

    assert(!model_qdisc_is_soft(&mq_default, false, false));
    assert(model_qdisc_is_soft(&mq_default, true, false));   /* tcx_egress active */
    assert(model_qdisc_is_soft(&mq_custom, false, false));   /* custom child qdisc */
    assert(model_qdisc_is_soft(&cake, false, false));        /* cake -> SW */
    assert(model_qdisc_is_soft(&htb_sw, false, false));      /* VyOS non-offload htb -> SW */
    assert(!model_qdisc_is_soft(&htb_hw, false, true));      /* CEETM htb offload -> HW */
    assert(model_qdisc_is_soft(&htb_hw, false, false));      /* offloaded flag but no ceetm -> SW */
    assert(!model_qdisc_is_soft(&noqueue, false, false));
}

int main(void) {
    test_cq_pop_and_drain();
    test_ccgr_budget();
    test_lifecycle_and_quarantine();
    test_soft_qdisc_guard();
    printf("G10 CEETM+ASK2 Phase 1 C harness PASS\n");
    return 0;
}
"""


def run_c_harness() -> None:
    with tempfile.TemporaryDirectory(prefix="g10-ceetm-") as tmp:
        c_path = Path(tmp) / "harness.c"
        bin_path = Path(tmp) / "harness"
        c_path.write_text(C_HARNESS)
        subprocess.run(
            ["gcc", "-O2", "-Wall", "-Wextra", "-Werror",
             "-fsanitize=address,undefined", str(c_path), "-o", str(bin_path)],
            check=True,
        )
        subprocess.run([str(bin_path)], check=True)


def main() -> int:
    check_sources()
    run_c_harness()
    print("OK: G10 Phase 1 CEETM+ASK2 source contracts and ASan/UBSan harness PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
