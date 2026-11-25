#!/usr/bin/env python3
"""G10 (T-M6-QOS) Phase 2 host gate: 2-level CEETM HQoS, ct->mark steering, and VyOS CLI.

Verifies:
  1. Source contracts in dpaa_flow_offload.h, dpaa_ceetm.c, ask_internal.h,
     ask_hw.c, ask_flow_offload.c, and data/vyos-1x-061-qos-ceetm-offload.patch.
  2. ASan/UBSan C behavioral harness covering:
     - 2-level HTB offload lifecycle: CREATE -> LEAF_ALLOC(root) ->
       LEAF_TO_INNER(1:1 -> 1:10 prio 0) -> LEAF_ALLOC(1:1 -> 1:20 prio 7) ->
       duplicate-prio -EEXIST -> depth-3 -EOPNOTSUPP -> NODE_MODIFY(inner) ->
       LEAF_DEL(sibling) -> LEAF_DEL_LAST(demote to leaf) -> DESTROY.
     - Exact SW (skb->priority / skb->mark) vs HW (ct->mark) classid decoding
       equivalence, including fallback to defcls on 0 / foreign major / unknown minor.
     - Multi-class dpaa_ceetm_fqid_is_live() with concurrent hw_lfqs per port.
"""

from pathlib import Path
import subprocess
import sys
import tempfile

REPO = Path(__file__).resolve().parent.parent
DPAA_CEETM_C = REPO / "kernel/common/files/drivers/net/ethernet/freescale/dpaa/dpaa_ceetm.c"
FLOW_OFF_H = REPO / "kernel/common/files/include/linux/fsl/dpaa_flow_offload.h"
ASK_INT_H = REPO / "kernel/ask/oot-modules/ask/include/ask_internal.h"
ASK_HW_C = REPO / "kernel/ask/oot-modules/ask/ask_hw.c"
ASK_FO_C = REPO / "kernel/ask/oot-modules/ask/ask_flow_offload.c"
VYOS_PATCH = REPO / "data/vyos-1x-061-qos-ceetm-offload.patch"


def require_contains(path: Path, needles: list[str], label: str) -> None:
    if not path.exists():
        raise AssertionError(f"{label}: missing file {path}")
    text = path.read_text()
    for n in needles:
        if n not in text:
            raise AssertionError(f"{label}: missing expected snippet in {path.name}: {n!r}")


def check_sources() -> None:
    require_contains(
        FLOW_OFF_H,
        [
            "bool dpaa_ceetm_fqid_is_live(struct net_device *dev, u32 fqid);",
        ],
        "dpaa_flow_offload.h",
    )

    require_contains(
        DPAA_CEETM_C,
        [
            "struct dpaa_ceetm_chan",
            "bool is_inner;",
            "u8 cq_prio;",
            "dpaa_ceetm_find_live_slot(",
            "ceetm->prev_real_txqs",
            "dpaa_ceetm_htb_leaf_to_inner(",
            "case TC_HTB_LEAF_TO_INNER:",
            "TC_HTB_LEAF_DEL_LAST",
            "skb->mark",
            "dpaa_ceetm_decode_classid(",
            "bool dpaa_ceetm_fqid_is_live(struct net_device *dev, u32 fqid)",
            "EXPORT_SYMBOL_GPL(dpaa_ceetm_fqid_is_live);",
        ],
        "dpaa_ceetm.c",
    )

    require_contains(
        ASK_INT_H,
        [
            "u32    ceetm_classid;",
        ],
        "ask_internal.h",
    )

    require_contains(
        ASK_HW_C,
        [
            "dpaa_ceetm_fqid_is_live(",
            "dpaa_ceetm_offload_egress_fqid(phys, classid,",
            "key->ceetm_classid",
        ],
        "ask_hw.c",
    )

    require_contains(
        ASK_FO_C,
        [
            "ask_flow_cookie_mark(",
            "CONFIG_NF_CONNTRACK_MARK",
            "key.ceetm_classid",
        ],
        "ask_flow_offload.c",
    )

    require_contains(
        VYOS_PATCH,
        [
            "interface-definitions/qos.xml.in",
            '<leafNode name="offload">',
            "python/vyos/qos/trafficshaper.py",
            "htb offload default",
            "src/conf_mode/qos.py",
            "int(default_cfg['priority']) <= 7",
            "str(raw_def_prio) != '20'",
        ],
        "vyos-1x-061-qos-ceetm-offload.patch",
    )
    if "tc filter replace" in VYOS_PATCH.read_text():
        raise AssertionError("vyos-1x-061-qos-ceetm-offload.patch: dead tc filter replace must not be emitted for offloaded HTB")


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

#define TC_H_MAJ(h) ((h) & 0xFFFF0000U)
#define TC_H_MIN(h) ((h) & 0x0000FFFFU)
#define TC_HTB_CLASSID_ROOT 0xFFFFFFFFU

#define MAX_CHANS 16
#define MAX_LEAVES 32
#define MAX_CQ_PRIO 8

struct ceetm_chan {
	bool used;
	bool is_inner;
	bool quarantined;
	u32 classid;
	u64 rate_bps;
	u64 ceil_bps;
	u8 cq_mask;
	u8 active_cqs;
};

struct ceetm_leaf {
	bool used;
	bool quarantined;
	struct ceetm_chan *chan;
	u8 cq_prio;
	u32 classid;
	u32 hw_lfqid;
};

struct ceetm_port {
	u16 htb_maj;
	u32 defcls;
	struct ceetm_chan chans[MAX_CHANS];
	struct ceetm_leaf leaves[MAX_LEAVES];
	u32 next_lfq;
};

static u32 decode_classid(const struct ceetm_port *p, u32 raw)
{
	if (!raw)
		return 0;
	if (TC_H_MAJ(raw)) {
		if ((TC_H_MAJ(raw) >> 16) != p->htb_maj)
			return 0;
		return TC_H_MIN(raw);
	}
	return TC_H_MIN(raw);
}

static int find_leaf(const struct ceetm_port *p, u32 minor)
{
	for (int i = 0; i < MAX_LEAVES; i++)
		if (p->leaves[i].used && !p->leaves[i].quarantined &&
		    p->leaves[i].classid == minor)
			return i;
	return -1;
}

static struct ceetm_chan *find_inner_chan(struct ceetm_port *p, u32 minor)
{
	for (int i = 0; i < MAX_CHANS; i++)
		if (p->chans[i].used && p->chans[i].is_inner &&
		    !p->chans[i].quarantined && p->chans[i].classid == minor)
			return &p->chans[i];
	return NULL;
}

static struct ceetm_chan *alloc_chan(struct ceetm_port *p, u32 minor, u64 rate, u64 ceil)
{
	for (int i = 0; i < MAX_CHANS; i++) {
		if (!p->chans[i].used && !p->chans[i].quarantined) {
			p->chans[i].used = true;
			p->chans[i].is_inner = false;
			p->chans[i].classid = minor;
			p->chans[i].rate_bps = rate;
			p->chans[i].ceil_bps = ceil;
			p->chans[i].cq_mask = 0;
			p->chans[i].active_cqs = 0;
			return &p->chans[i];
		}
	}
	return NULL;
}

static int alloc_leaf_slot(struct ceetm_port *p)
{
	for (int i = 0; i < MAX_LEAVES; i++)
		if (!p->leaves[i].used && !p->leaves[i].quarantined)
			return i;
	return -1;
}

static int htb_leaf_alloc(struct ceetm_port *p, u32 parent, u32 classid,
			  u64 rate, u64 ceil, u8 prio, int *out_slot)
{
	u32 minor = TC_H_MIN(classid);
	u8 cq = prio < MAX_CQ_PRIO ? prio : (MAX_CQ_PRIO - 1);
	struct ceetm_chan *ch;
	int slot = alloc_leaf_slot(p);

	if (slot < 0)
		return -ENOSPC;
	if (parent == TC_HTB_CLASSID_ROOT) {
		ch = alloc_chan(p, minor, rate, ceil);
		if (!ch)
			return -ENOSPC;
		cq = 0;
	} else {
		ch = find_inner_chan(p, TC_H_MIN(parent));
		if (!ch)
			return -EOPNOTSUPP;
		if (ch->cq_mask & (1U << cq))
			return -EEXIST;
	}
	ch->cq_mask |= (1U << cq);
	ch->active_cqs++;
	p->leaves[slot].used = true;
	p->leaves[slot].chan = ch;
	p->leaves[slot].cq_prio = cq;
	p->leaves[slot].classid = minor;
	p->leaves[slot].hw_lfqid = 0xF00000U + (++p->next_lfq);
	*out_slot = slot;
	return 0;
}

static int htb_leaf_to_inner(struct ceetm_port *p, u32 parent_minor,
			     u32 child_classid, u8 prio)
{
	int slot = find_leaf(p, TC_H_MIN(parent_minor));
	u8 cq = prio < MAX_CQ_PRIO ? prio : (MAX_CQ_PRIO - 1);
	struct ceetm_chan *ch;

	if (slot < 0)
		return -ENOENT;
	ch = p->leaves[slot].chan;
	if (ch->is_inner)
		return -EOPNOTSUPP; /* depth > 2 refused */
	if (cq != p->leaves[slot].cq_prio) {
		ch->cq_mask &= ~(1U << p->leaves[slot].cq_prio);
		ch->cq_mask |= (1U << cq);
		p->leaves[slot].cq_prio = cq;
		p->leaves[slot].hw_lfqid = 0xF00000U + (++p->next_lfq);
	}
	ch->is_inner = true;
	p->leaves[slot].classid = TC_H_MIN(child_classid);
	return 0;
}

static int htb_leaf_del(struct ceetm_port *p, u32 classid, bool del_last, bool force)
{
	int slot = find_leaf(p, TC_H_MIN(classid));
	struct ceetm_chan *ch;

	if (slot < 0)
		return -ENOENT;
	ch = p->leaves[slot].chan;
	if (del_last && !force && ch->is_inner && ch->active_cqs == 1) {
		/* Demote inner channel back to root-level leaf on same slot. */
		if (p->leaves[slot].cq_prio != 0) {
			ch->cq_mask &= ~(1U << p->leaves[slot].cq_prio);
			ch->cq_mask |= 1U;
			p->leaves[slot].cq_prio = 0;
			p->leaves[slot].hw_lfqid = 0xF00000U + (++p->next_lfq);
		}
		ch->is_inner = false;
		p->leaves[slot].classid = ch->classid;
		return 0;
	}
	ch->cq_mask &= ~(1U << p->leaves[slot].cq_prio);
	ch->active_cqs--;
	memset(&p->leaves[slot], 0, sizeof(p->leaves[slot]));
	if (ch->active_cqs == 0)
		memset(ch, 0, sizeof(*ch));
	return 0;
}

static int resolve_offload_fqid(const struct ceetm_port *p, u32 raw_classid, u32 *out)
{
	u32 want = decode_classid(p, raw_classid);
	int slot = want ? find_leaf(p, want) : -1;

	if (slot < 0 && p->defcls)
		slot = find_leaf(p, p->defcls);
	if (slot < 0)
		return -EAGAIN;
	*out = p->leaves[slot].hw_lfqid;
	return 1;
}

static int sw_select_slot(const struct ceetm_port *p, u32 skb_prio, u32 skb_mark)
{
	u32 want = decode_classid(p, skb_prio);
	int slot = want ? find_leaf(p, want) : -1;

	if (slot < 0) {
		want = decode_classid(p, skb_mark);
		slot = want ? find_leaf(p, want) : -1;
	}
	if (slot < 0 && p->defcls)
		slot = find_leaf(p, p->defcls);
	return slot;
}

static bool fqid_is_live(const struct ceetm_port *p, u32 fqid)
{
	for (int i = 0; i < MAX_LEAVES; i++)
		if (p->leaves[i].used && !p->leaves[i].quarantined &&
		    p->leaves[i].hw_lfqid == fqid)
			return true;
	return false;
}

int main(void)
{
	struct ceetm_port p = { .htb_maj = 1, .defcls = 0x14 };
	int s1 = -1, s2 = -1, s3 = -1;
	u32 fq_hi = 0, fq_def = 0, fq_probe = 0;

	/* 1. Root inner channel 1:1 -> LEAF_TO_INNER 1:a (prio 0) + 1:14 (prio 7). */
	assert(htb_leaf_alloc(&p, TC_HTB_CLASSID_ROOT, 0x10001, 500000000ULL, 500000000ULL, 0, &s1) == 0);
	assert(htb_leaf_to_inner(&p, 1, 0x1000a, 0) == 0);
	assert(htb_leaf_alloc(&p, 1, 0x10014, 400000000ULL, 500000000ULL, 7, &s2) == 0);
	assert(s1 != s2);

	/* 2. Duplicate prio on same channel rejected -EEXIST; depth-3 rejected -EOPNOTSUPP. */
	assert(htb_leaf_alloc(&p, 1, 0x1001e, 100000000ULL, 500000000ULL, 7, &s3) == -EEXIST);
	assert(htb_leaf_to_inner(&p, 0x14, 0x10020, 1) == -EOPNOTSUPP);

	/* 3. Exact SW/HW steering equivalence across ct->mark / skb->mark / skb->priority. */
	assert(resolve_offload_fqid(&p, 10, &fq_hi) == 1);
	assert(resolve_offload_fqid(&p, 0x0001000a, &fq_probe) == 1 && fq_probe == fq_hi);
	assert(resolve_offload_fqid(&p, 0, &fq_def) == 1 && fq_def != fq_hi);
	assert(resolve_offload_fqid(&p, 0x0002000a, &fq_probe) == 1 && fq_probe == fq_def);
	assert(resolve_offload_fqid(&p, 99, &fq_probe) == 1 && fq_probe == fq_def);

	assert(sw_select_slot(&p, 0x0001000a, 0) == s1);
	assert(sw_select_slot(&p, 0, 10) == s1);
	assert(sw_select_slot(&p, 0, 0x0001000a) == s1);
	assert(sw_select_slot(&p, 0, 0) == s2);
	assert(sw_select_slot(&p, 0, 0x0002000a) == s2);

	/* 4. Multi-class live FQID check. */
	assert(fqid_is_live(&p, fq_hi));
	assert(fqid_is_live(&p, fq_def));

	/* 5. Delete sibling 1:14, then LEAF_DEL_LAST 1:a demotes 1:1 back to leaf, then force del. */
	assert(htb_leaf_del(&p, 0x14, false, false) == 0);
	assert(!fqid_is_live(&p, fq_def));
	assert(fqid_is_live(&p, fq_hi));
	assert(htb_leaf_del(&p, 0x0a, true, false) == 0);
	assert(find_leaf(&p, 1) == s1 && !p.leaves[s1].chan->is_inner);
	assert(htb_leaf_del(&p, 1, true, true) == 0);
	for (int i = 0; i < MAX_CHANS; i++)
		assert(!p.chans[i].used);
	for (int i = 0; i < MAX_LEAVES; i++)
		assert(!p.leaves[i].used);

	puts("G10 Phase 2 multi-class + ct->mark C harness PASS");
	return 0;
}
"""


def run_c_harness() -> None:
    with tempfile.TemporaryDirectory() as td:
        src = Path(td) / "g10_p2_test.c"
        exe = Path(td) / "g10_p2_test"
        src.write_text(C_HARNESS)
        subprocess.run(
            ["cc", "-std=gnu11", "-Wall", "-Wextra", "-Werror",
             "-fsanitize=address,undefined", str(src), "-o", str(exe)],
            check=True,
        )
        subprocess.run([str(exe)], check=True)


def main() -> int:
    check_sources()
    run_c_harness()
    print("OK: G10 Phase 2 multi-class CEETM + ct->mark + VyOS CLI gate PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
