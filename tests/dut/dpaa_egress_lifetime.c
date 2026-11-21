// SPDX-License-Identifier: GPL-2.0
/* Host counterexamples for the extracted WGFP and ASK egress caches. */
#include <assert.h>
#include <errno.h>
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

typedef uint8_t u8;
typedef uint32_t u32;
typedef uint64_t u64;

struct net_device {
	int ifindex;
	const char *name;
	bool dpaa;
	bool running;
};

#define WGFP_MAX_EGRESS 8
#define EXPORT_SYMBOL_GPL(x)
#define __rcu
#define mutex_lock(p) ((void)(p))
#define mutex_unlock(p) ((void)(p))
#define lockdep_is_held(p) true
#define rcu_dereference_protected(p, c) (p)
#define rcu_assign_pointer(p, v) ((p) = (v))
#define rcu_access_pointer(p) (p)
#define smp_load_acquire(p) (*(p))
#define smp_store_release(p, v) (*(p) = (v))
#define pr_info(...) ((void)0)

static struct net_device *wgfp_eg_dev[WGFP_MAX_EGRESS];
static int wgfp_eg_ifindex[WGFP_MAX_EGRESS];
static int wgfp_neg, wgfp_lock;
static int allocations;

static void *dpaa_get_rx_fman_port(struct net_device *dev)
{
	return dev->dpaa ? dev : NULL;
}

static bool netif_running(const struct net_device *dev)
{
	return dev->running;
}

static int dpaa_alloc_offload_tx_fq(struct net_device *dev, u32 *fqid)
{
	*fqid = 100 + ++allocations;
	return 0;
}

#include "qman_wgfp_body.h"

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

static void reset_egress(void)
{
	memset(wgfp_eg_dev, 0, sizeof(wgfp_eg_dev));
	memset(wgfp_eg_ifindex, 0, sizeof(wgfp_eg_ifindex));
	wgfp_neg = 0;
	allocations = 0;
}

static int check_wgfp_reused_index(void)
{
	struct net_device old = { .ifindex = 5, .name = "old-dpaa", .dpaa = true, .running = true };
	struct net_device other = { .ifindex = 6, .name = "other-dpaa", .dpaa = true, .running = true };
	struct net_device reused = { .ifindex = 5, .name = "reused-dpaa", .dpaa = true, .running = true };
	int old_handle, other_handle, reused_handle;

	reset_egress();
	old_handle = dpaa_wgfp_egress(&old);
	other_handle = dpaa_wgfp_egress(&other);
	if (old_handle < 0 || other_handle < 0 || old_handle == other_handle) {
		puts("WGFP normal distinct-device control: FAILED");
		return 1;
	}
	if (wgfp_eg_dev[old_handle] != &old || wgfp_eg_dev[other_handle] != &other) {
		puts("WGFP normal device binding control: FAILED");
		return 1;
	}

	/* Model ifindex reuse by a distinct device while old slot still exists. */
	old.ifindex = 50;
#ifdef LIFETIME_ORACLE_CONTROLS
	wgfp_eg_dev[old_handle] = NULL;
	wgfp_eg_ifindex[old_handle] = -1;
#endif
	reused_handle = dpaa_wgfp_egress(&reused);
	if (reused_handle < 0) {
		puts("WGFP reused ifindex: refused");
		return 0;
	}
	if (wgfp_eg_dev[reused_handle] == &old) {
		puts("WGFP reused ifindex: NOT CONTAINED (old device selected)");
		return 1;
	}
	puts("WGFP reused ifindex: old device not selected");
	return 0;
}

static int check_ask_reused_index(void)
{
	struct net_device old = { .ifindex = 5, .name = "old-dpaa", .dpaa = true, .running = true };
	struct net_device virtual = { .ifindex = 5, .name = "veth", .dpaa = false, .running = true };
	u32 old_fqid, after_move;
	int rc;

	reset_egress();
	memset(&hardware, 0, sizeof(hardware));
	lookup_dev = &virtual;
	if (ask_hw_resolve_oif_fqid(5, &after_move, false, NULL) >= 0) {
		puts("ASK cold non-DPAA control: FAILED");
		return 1;
	}
	lookup_dev = &old;
	if (ask_hw_resolve_oif_fqid(5, &old_fqid, true, NULL)) {
		puts("ASK physical egress allocation control: FAILED");
		return 1;
	}

	old.ifindex = 50;
	lookup_dev = &virtual;
#ifdef LIFETIME_ORACLE_CONTROLS
	memset(hardware.noconf_tx, 0, sizeof(hardware.noconf_tx));
#endif
	rc = ask_hw_resolve_oif_fqid(5, &after_move, false, NULL);
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
