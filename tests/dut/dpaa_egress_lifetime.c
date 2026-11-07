// SPDX-License-Identifier: GPL-2.0
/* Host counterexamples for the extracted WGFP and ASK egress caches. */
#define main existing_enqueue_tests
#include "qman_wgfp_enqueue.c"
#undef main

#define ASK_HW_NOCONF_SLOTS 8
#define ask_pr_info(...) ((void)0)
#define ask_pr_warn(...) ((void)0)

struct ask_hw_pcd {
	struct {
		u32 ifindex;
		u32 fqid;
	} noconf_tx[ASK_HW_NOCONF_SLOTS];
};
struct net { int unused; };
static struct net init_net;
static struct net_device *lookup_dev;
static struct ask_hw_pcd hardware;
static struct ask_hw_pcd *ask_hw_pcd_inst = &hardware;

static struct net_device *dev_get_by_index(struct net *net, u32 index)
{
	return lookup_dev && lookup_dev->ifindex == (int)index ? lookup_dev : NULL;
}

static void dev_put(struct net_device *dev) {}
static bool is_vlan_dev(struct net_device *dev) { return false; }
static struct net_device *vlan_dev_real_dev(struct net_device *dev) { return dev; }

#include "ask_oif_body.h"

static int check_wgfp_reused_index(void)
{
	struct net_device old = { .ifindex = 5, .name = "old-dpaa", .dpaa = true };
	struct net_device other = { .ifindex = 6, .name = "other-dpaa", .dpaa = true };
	struct qm_fd fd = { .bpid = 3, .addr = 0x1234, .off = 64, .len = 99 };
	struct device dma_dev = { 0 };
	struct dpaa_wgfp_frame frame = { .fd = &fd, .dev = &dma_dev, .addr = 0x1234 };
	u32 old_fqid;
	int old_handle, other_handle, reused_handle;

	reset();
	old_handle = dpaa_wgfp_egress(&old);
	other_handle = dpaa_wgfp_egress(&other);
	if (old_handle < 0 || other_handle < 0 || old_handle == other_handle) {
		puts("WGFP normal distinct-device control: FAILED");
		return 1;
	}
	old_fqid = wgfp_eg_fqid[old_handle];
	if (old_fqid == wgfp_eg_fqid[other_handle]) {
		puts("WGFP normal distinct-FQ control: FAILED");
		return 1;
	}

	/* Model ifindex reuse after actual unregister; namespace moves are blocked. */
	old.ifindex = 50;
	other.ifindex = 5;
#ifdef LIFETIME_ORACLE_CONTROLS
	wgfp_eg_ifindex[old_handle] = -1;
	wgfp_eg_ifindex[other_handle] = -1;
#endif
	reused_handle = dpaa_wgfp_egress(&other);
	if (reused_handle < 0) {
		puts("WGFP reused ifindex: refused");
		return 0;
	}
	dpaa_wgfp_xmit(&frame, reused_handle, 64, 99);
	if (portal.p.entry.fqid == old_fqid) {
		puts("WGFP reused ifindex: NOT CONTAINED (old device FQ selected)");
		return 1;
	}
	puts("WGFP reused ifindex: old device FQ not selected");
	return 0;
}

static int check_ask_reused_index(void)
{
	struct net_device old = { .ifindex = 5, .name = "old-dpaa", .dpaa = true };
	struct net_device virtual = { .ifindex = 5, .name = "veth", .dpaa = false };
	u32 old_fqid, after_move;
	int rc;

	reset();
	memset(&hardware, 0, sizeof(hardware));
	lookup_dev = &virtual;
	if (ask_hw_resolve_oif_fqid(5, &after_move, false) >= 0) {
		puts("ASK cold non-DPAA control: FAILED");
		return 1;
	}
	lookup_dev = &old;
	if (ask_hw_resolve_oif_fqid(5, &old_fqid, true)) {
		puts("ASK physical egress allocation control: FAILED");
		return 1;
	}

	old.ifindex = 50;
	lookup_dev = &virtual;
#ifdef LIFETIME_ORACLE_CONTROLS
	memset(hardware.noconf_tx, 0, sizeof(hardware.noconf_tx));
#endif
	rc = ask_hw_resolve_oif_fqid(5, &after_move, false);
	if (!rc) {
		printf("ASK reused ifindex: NOT CONTAINED (non-DPAA device returned FQ %u%s)\n",
		       after_move, after_move == old_fqid ? " from old device" : "");
		return 1;
	}
	puts("ASK reused ifindex: non-DPAA device refused");
	return 0;
}

int main(void)
{
	int failures = check_wgfp_reused_index() + check_ask_reused_index();

	printf("Egress characterization: %d uncontained case(s)\n", failures);
	return failures ? 1 : 0;
}
