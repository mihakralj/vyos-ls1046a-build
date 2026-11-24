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

struct device { int unused; };
struct dpaa_priv {
	struct device *tx_dma_dev;
	void *ceetm;
};
enum { NETREG_REGISTERED = 1 };
#define GFP_KERNEL 0
struct net_device {
	int ifindex;
	const char *name;
	bool dpaa;
	bool running;
	int reg_state;
	struct dpaa_priv priv;
};

#define WGFP_MAX_EGRESS 8
#define WGFP_TX_SLOTS_PER_CPU 512
#define EXPORT_SYMBOL_GPL(x)
#define __rcu
#define IS_ENABLED(x) 0
#define WRITE_ONCE(x, v) ((x) = (v))
#define rtnl_lock() ((void)0)
#define rtnl_unlock() ((void)0)
#define mutex_lock(p) ((void)(p))
#define mutex_unlock(p) ((void)(p))
#define lockdep_is_held(p) true
#define rcu_dereference_protected(p, c) (p)
#define rcu_assign_pointer(p, v) ((p) = (v))
#define rcu_access_pointer(p) (p)
#define smp_load_acquire(p) (*(p))
#define smp_store_release(p, v) (*(p) = (v))
#define pr_info(...) ((void)0)

struct wgfp_egress {
	struct net_device *dev;
	struct device *tx_dma_dev;
	bool tx_dma_need_sync;
	int ifindex;
	u32 tx_fqid;
	bool active;
	bool retired;
	bool quarantined;
};

static bool dma_dev_need_sync(struct device *dev)
{
	(void)dev;
	return false;
}

static struct net_device *wgfp_eg_dev[WGFP_MAX_EGRESS];
static struct wgfp_egress wgfp_egresses[WGFP_MAX_EGRESS];
static int wgfp_neg, wgfp_lock;
static int allocations;

static void *netdev_priv(struct net_device *dev)
{
	return &dev->priv;
}

static void *dpaa_get_rx_fman_port(struct net_device *dev)
{
	return dev->dpaa ? dev : NULL;
}

static bool netif_running(const struct net_device *dev)
{
	return dev->running;
}

static int wgfp_tx_infra_ensure_locked(void) { return 0; }
static int wgfp_eg_alloc_slots(struct wgfp_egress *eg, struct device *d) { (void)eg; (void)d; return 0; }
static void wgfp_eg_free_slots_unpublished(struct wgfp_egress *eg, struct device *d) { (void)eg; (void)d; }
static void wgfp_eg_publish_slots(struct wgfp_egress *eg) { (void)eg; }

static int dpaa_alloc_wgfp_tx_fq(struct net_device *dev, u32 *fqid, struct device **tx_dma_dev)
{
	*fqid = 100 + ++allocations;
	*tx_dma_dev = dev->priv.tx_dma_dev;
	return 0;
}

static int dpaa_alloc_offload_tx_fq(struct net_device *dev, u32 *fqid)
{
	(void)dev;
	*fqid = 100 + ++allocations;
	return 0;
}

#include "qman_wgfp_body.h"

#define ASK_HW_NOCONF_SLOTS 8
#define ask_pr_info(...) ((void)0)
#define ask_pr_warn(...) ((void)0)

struct ask_hw_pcd {
	int lock;
	struct {
		u32 ifindex;
		u32 fqid;
		bool unregistered;
		bool retained;
		int dev_tracker;
		struct net_device *dev;
	} noconf_tx[ASK_HW_NOCONF_SLOTS];
};
static void netdev_hold(struct net_device *dev, int *tracker, int gfp)
{
	(void)dev; (void)tracker; (void)gfp;
}
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
	memset(wgfp_egresses, 0, sizeof(wgfp_egresses));
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
	wgfp_egresses[old_handle].active = false;
	wgfp_egresses[old_handle].ifindex = -1;
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
	struct net_device old = { .ifindex = 5, .name = "old-dpaa", .dpaa = true, .running = true, .reg_state = NETREG_REGISTERED };
	struct net_device virtual = { .ifindex = 5, .name = "veth", .dpaa = false, .running = true, .reg_state = NETREG_REGISTERED };
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
