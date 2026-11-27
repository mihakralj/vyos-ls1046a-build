#!/usr/bin/env python3
"""G10 (T-M6-QOS) Phase 3 host gate: single-port per-packet MURAM DSCP->CEETM map.

Verifies:
  1. Source contracts in F_328.py, dpaa_flow_offload.h, dpaa_ceetm.h,
     dpaa_ceetm.c, ask_flow_offload.c, and bin/ci-setup-kernel.sh.
  2. ASan/UBSan C behavioral harness covering:
     - Exact microcode 210.10.1 MURAM layout (28 B frag_info + 256 B dscp_fqid_map[64]).
     - Single-port ownership & safe retirement handoff (second port refused -EBUSY
       while port A owns or is retiring; handoff succeeds only after quiesce).
     - Precedence: ct->mark / skb->priority / skb->mark strictly outrank DSCP.
     - Duplicate DSCP rejection (-EEXIST) vs same-key replacement.
     - HTB tree-change re-resolution (deleted leaf clears DSCP slot to 0; re-added
       leaf restores it automatically).
"""

from pathlib import Path
import subprocess
import sys
import tempfile

REPO = Path(__file__).resolve().parent.parent
F328 = REPO / "bin/kernel-fixups/F_328.py"
CI_SETUP = REPO / "bin/ci-setup-kernel.sh"
FLOW_OFF_H = REPO / "kernel/common/files/include/linux/fsl/dpaa_flow_offload.h"
DPAA_CEETM_H = REPO / "kernel/common/files/drivers/net/ethernet/freescale/dpaa/dpaa_ceetm.h"
DPAA_CEETM_C = REPO / "kernel/common/files/drivers/net/ethernet/freescale/dpaa/dpaa_ceetm.c"
ASK_FO_C = REPO / "kernel/ask/oot-modules/ask/ask_flow_offload.c"


def require_contains(path: Path, needles: list[str], label: str) -> None:
    if not path.exists():
        raise AssertionError(f"{label}: missing file {path}")
    text = path.read_text()
    for n in needles:
        if n not in text:
            raise AssertionError(f"{label}: missing expected snippet in {path.name}: {n!r}")


def check_sources() -> None:
    require_contains(
        F328,
        [
            "FMAN_EHASH_DSCP_MAP_OFF",
            "fman_pcd_dscp_map_set",
            "fman_pcd_dscp_map_clear_all",
            "dscp_fq_map",
            "FLOW_BLOCK_BINDER_TYPE_CLSACT_EGRESS",
            "dpaa_ceetm_setup_dscp_block",
            "dpaa_ceetm_dscp_netdev_unregister",
        ],
        "F_328.py",
    )

    require_contains(
        CI_SETUP,
        [
            "bin/kernel-fixups/F_328.py",
            "bin/test-g10-phase3-dscp.py",
        ],
        "ci-setup-kernel.sh",
    )

    require_contains(
        FLOW_OFF_H,
        [
            "bool dpaa_ceetm_dscp_map_active(struct net_device *dev);",
            "bool dpaa_ceetm_dscp_cookie_offloaded(struct net_device *dev,",
            "bool dpaa_ceetm_classid_is_live(struct net_device *dev, u32 classid);",
        ],
        "dpaa_flow_offload.h",
    )

    require_contains(
        DPAA_CEETM_H,
        [
            "int dpaa_ceetm_setup_dscp_block(struct net_device *net_dev,",
            "void dpaa_ceetm_dscp_netdev_unregister(struct net_device *net_dev);",
        ],
        "dpaa_ceetm.h",
    )

    require_contains(
        DPAA_CEETM_C,
        [
            "dpaa_ceetm_setup_dscp_block(",
            "dpaa_ceetm_dscp_netdev_unregister(",
            "dpaa_ceetm_dscp_map_active(",
            "dpaa_ceetm_dscp_cookie_offloaded(",
            "dpaa_ceetm_classid_is_live(",
            "dpaa_ceetm_dscp_dual_stack_pair(",
            "f->common.skip_sw",
            "was_retiring",
            "smp_store_release(&dpaa_ceetm_dscp_state",
            "smp_load_acquire(&dpaa_ceetm_dscp_state)",
            "fman_pcd_dscp_map_set(",
            "fman_pcd_dscp_map_clear_all(",
            "FLOW_ACTION_PRIORITY",
            "EXPORT_SYMBOL_GPL(dpaa_ceetm_dscp_map_active);",
            "EXPORT_SYMBOL_GPL(dpaa_ceetm_dscp_cookie_offloaded);",
            "EXPORT_SYMBOL_GPL(dpaa_ceetm_classid_is_live);",
        ],
        "dpaa_ceetm.c",
    )

    require_contains(
        ASK_FO_C,
        [
            "dpaa_ceetm_dscp_map_active(",
            "dpaa_ceetm_dscp_cookie_offloaded(",
            "dpaa_ceetm_classid_is_live(",
            "tcf_get_next_chain(",
            "tcf_get_next_proto(",
            "oif_phys_ifindex",
            "action.dscp_fq_map",
        ],
        "ask_flow_offload.c",
    )


C_HARNESS = r"""
#include <assert.h>
#include <errno.h>
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

typedef uint8_t  u8;
typedef uint16_t u16;
typedef uint32_t u32;

/* Verify exact microcode 210.10.1 cdx_muram_memory_cmn_db_t layout */
struct __attribute__((packed)) ucode_frag_info {
	u16 frag_options;
	u16 pad;
	u32 alloc_buff_failures;
	u32 v4_frames;
	u32 v6_frames;
	u32 v4_frags;
	u32 v6_frags;
	u32 v6_ident;
};

struct muram_cmn_db {
	struct ucode_frag_info frag;
	u32 dscp_fqid[64];
};

_Static_assert(sizeof(struct ucode_frag_info) == 28, "frag_info must be 28B");
_Static_assert(offsetof(struct muram_cmn_db, dscp_fqid) == 28, "dscp_fqid_map must start at +28");
_Static_assert(sizeof(struct muram_cmn_db) == 284, "cmn_db must be 284B");

#define DSCP_COUNT 64
#define MAX_FILTERS 16

enum dscp_state { DSCP_OFF = 0, DSCP_ON = 1, DSCP_RETIRING = 2 };

struct dscp_filter {
	bool used;
	u32 cookie;
	u32 prio;
	u32 chain;
	u16 n_proto;
	u8  dscp;
	u32 minor;
};

struct port_ctx {
	int id;
	bool ceetm_on;
	u32 defcls;
	u32 leaf_minor[8];
	u32 leaf_fqid[8];
	enum dscp_state state;
	struct dscp_filter filters[MAX_FILTERS];
	u32 sw_dscp_minor[DSCP_COUNT];
};

static struct port_ctx *owner;
static u32 muram_dscp[DSCP_COUNT];
static int inject_quiesce_rc;

static u32 lookup_fqid(const struct port_ctx *p, u32 minor)
{
	if (!p->ceetm_on || !minor)
		return 0;
	for (int i = 0; i < 8; i++)
		if (p->leaf_minor[i] == minor)
			return p->leaf_fqid[i];
	return 0;
}

static void sync_port(struct port_ctx *p)
{
	memset(p->sw_dscp_minor, 0, sizeof(p->sw_dscp_minor));
	if (owner == p)
		memset(muram_dscp, 0, sizeof(muram_dscp));
	for (int i = 0; i < MAX_FILTERS; i++) {
		if (!p->filters[i].used)
			continue;
		u32 fqid = lookup_fqid(p, p->filters[i].minor);
		if (fqid) {
			p->sw_dscp_minor[p->filters[i].dscp] = p->filters[i].minor;
			if (owner == p)
				muram_dscp[p->filters[i].dscp] = fqid;
		}
	}
}

static int finish_retiring(void)
{
	if (!owner || owner->state != DSCP_RETIRING)
		return 0;
	if (inject_quiesce_rc)
		return -EBUSY;
	memset(muram_dscp, 0, sizeof(muram_dscp));
	owner->state = DSCP_OFF;
	owner = NULL;
	return 0;
}

static int dscp_replace(struct port_ctx *p, u32 cookie, u32 prio, u32 chain,
			u16 n_proto, u8 dscp, u32 minor)
{
	if (!p->ceetm_on || dscp >= DSCP_COUNT || !minor)
		return -EINVAL;
	if (owner && owner != p) {
		if (finish_retiring())
			return -EBUSY;
	}
	if (owner && owner != p)
		return -EBUSY;
	if (!lookup_fqid(p, minor))
		return -ENOENT;

	for (int i = 0; i < MAX_FILTERS; i++) {
		struct dscp_filter *f = &p->filters[i];
		if (!f->used)
			continue;
		if (f->dscp == dscp && f->cookie != cookie) {
			bool replaces = (f->prio == prio && f->chain == chain &&
					 f->n_proto == n_proto);
			if (!replaces)
				return -EEXIST;
		}
	}
	int slot = -1;
	for (int i = 0; i < MAX_FILTERS; i++) {
		if (p->filters[i].used && p->filters[i].cookie == cookie) {
			slot = i;
			break;
		}
		if (!p->filters[i].used && slot < 0)
			slot = i;
	}
	if (slot < 0)
		return -ENOSPC;
	p->filters[slot] = (struct dscp_filter){
		.used = true, .cookie = cookie, .prio = prio, .chain = chain,
		.n_proto = n_proto, .dscp = dscp, .minor = minor
	};
	owner = p;
	p->state = DSCP_ON;
	sync_port(p);
	return 0;
}

static int dscp_destroy(struct port_ctx *p, u32 cookie)
{
	bool found = false, any = false;
	for (int i = 0; i < MAX_FILTERS; i++) {
		if (p->filters[i].used && p->filters[i].cookie == cookie) {
			p->filters[i].used = false;
			found = true;
		}
		if (p->filters[i].used)
			any = true;
	}
	if (!found)
		return -ENOENT;
	sync_port(p);
	if (!any && p->state == DSCP_ON) {
		p->state = DSCP_RETIRING;
		finish_retiring();
	}
	return 0;
}

/* Precedence check: mark/priority beats DSCP, DSCP beats defcls. */
static u32 sw_pick_minor(const struct port_ctx *p, u32 skb_prio, u32 skb_mark, u8 dscp)
{
	if (skb_prio && lookup_fqid(p, skb_prio))
		return skb_prio;
	if (skb_mark && lookup_fqid(p, skb_mark))
		return skb_mark;
	if (p->state == DSCP_ON && dscp < DSCP_COUNT && p->sw_dscp_minor[dscp])
		return p->sw_dscp_minor[dscp];
	return p->defcls;
}

static bool hw_record_enables_dscp(const struct port_ctx *p, u32 ct_mark)
{
	return (ct_mark == 0) && (p->state == DSCP_ON);
}

int main(void)
{
	struct port_ctx pa = {
		.id = 0x10, .ceetm_on = true, .defcls = 0x15,
		.leaf_minor = { 0x0a, 0x14, 0x15 },
		.leaf_fqid  = { 0xF00001, 0xF00002, 0xF00003 },
	};
	struct port_ctx pb = {
		.id = 0x11, .ceetm_on = true, .defcls = 0x15,
		.leaf_minor = { 0x0a, 0x15 },
		.leaf_fqid  = { 0xF00011, 0xF00012 },
	};

	/* 1. Port A claims DSCP 46 -> 1:a (EF -> CQ 0). */
	assert(dscp_replace(&pa, 1, 10, 0, 0x0800, 46, 0x0a) == 0);
	assert(muram_dscp[46] == 0xF00001);
	assert(muram_dscp[0] == 0);

	/* 2. Port B refused -EBUSY while Port A holds singleton. */
	assert(dscp_replace(&pb, 100, 10, 0, 0x0800, 46, 0x0a) == -EBUSY);

	/* 3. Duplicate DSCP at different prio refused -EEXIST; same-key replacement allowed. */
	assert(dscp_replace(&pa, 2, 20, 0, 0x0800, 46, 0x14) == -EEXIST);
	assert(dscp_replace(&pa, 3, 10, 0, 0x0800, 46, 0x14) == 0);
	assert(muram_dscp[46] == 0xF00002);
	assert(dscp_destroy(&pa, 1) == 0);
	assert(muram_dscp[46] == 0xF00002);

	/* 4. Precedence: mark/priority outranks DSCP; DSCP outranks defcls. */
	assert(sw_pick_minor(&pa, 0x0a, 0, 46) == 0x0a);
	assert(sw_pick_minor(&pa, 0, 0x0a, 46) == 0x0a);
	assert(sw_pick_minor(&pa, 0, 0, 46) == 0x14);
	assert(sw_pick_minor(&pa, 0, 0, 0) == 0x15);
	assert(!hw_record_enables_dscp(&pa, 0x0a));
	assert(hw_record_enables_dscp(&pa, 0));

	/* 5. HTB leaf deletion zeroes DSCP entry; leaf re-add restores it. */
	pa.leaf_minor[1] = 0;
	sync_port(&pa);
	assert(muram_dscp[46] == 0 && pa.sw_dscp_minor[46] == 0);
	pa.leaf_minor[1] = 0x14;
	pa.leaf_fqid[1] = 0xF00009;
	sync_port(&pa);
	assert(muram_dscp[46] == 0xF00009 && pa.sw_dscp_minor[46] == 0x14);

	/* 6. Safe retirement: failed quiesce holds table against Port B until drain succeeds. */
	inject_quiesce_rc = -EBUSY;
	assert(dscp_destroy(&pa, 3) == 0);
	assert(pa.state == DSCP_RETIRING && owner == &pa);
	assert(dscp_replace(&pb, 100, 10, 0, 0x0800, 46, 0x0a) == -EBUSY);

	inject_quiesce_rc = 0;
	assert(dscp_replace(&pb, 100, 10, 0, 0x0800, 46, 0x0a) == 0);
	assert(pa.state == DSCP_OFF && pb.state == DSCP_ON && owner == &pb);
	assert(muram_dscp[46] == 0xF00011);

	assert(dscp_destroy(&pb, 100) == 0);
	assert(owner == NULL && muram_dscp[46] == 0);

	puts("G10 Phase 3 MURAM DSCP->CEETM C harness PASS");
	return 0;
}
"""


def run_c_harness() -> None:
    with tempfile.TemporaryDirectory() as td:
        src = Path(td) / "g10_p3_test.c"
        exe = Path(td) / "g10_p3_test"
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
    print("OK: G10 Phase 3 MURAM DSCP->CEETM gate PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
