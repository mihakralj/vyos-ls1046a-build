// SPDX-License-Identifier: GPL-2.0
/*
 * ASK2 - bridge subsystem: switchdev FDB observer (T-M6-2, B0)
 *
 * plans/ASK2-BRIDGE-OFFLOAD-PLAN.md stages the L2 bridge HW-offload build
 * B0-B5, each silicon-gated. This file is B0: "dormant host plumbing, zero
 * datapath change" — register the switchdev notifier chains, defer FDB
 * events to a workqueue (the atomic chain cannot sleep; a future CC install
 * will), apply the plan's §4 admission pre-filter, and LOG what would be
 * installed/removed. No CC-tree call is made from here: the DA-match key
 * builder (B1) and the silicon de-risk proof (B2) haven't landed yet, so
 * there is nothing safe to install into hardware yet. Kernel stays
 * authoritative for learning/ageing/STP/flooding throughout.
 *
 * FDB event queue mirrors ask_neigh.c's bounded+coalesced pattern: a
 * flapping/churning MAC collapses to one queued event instead of one per
 * notification (plan §12 point 3, "MAC-move churn" — coalescing this early
 * means B3's real installer inherits the debounce behaviour for free
 * instead of needing to add it later under time pressure).
 */

#include <linux/kernel.h>
#include <linux/slab.h>
#include <linux/list.h>
#include <linux/spinlock.h>
#include <linux/workqueue.h>
#include <linux/notifier.h>
#include <linux/netdevice.h>
#include <linux/etherdevice.h>
#include <linux/module.h>
#include <net/switchdev.h>

#include "include/ask_internal.h"

/*
 * T-M6-2 gate: no standalone module param here (unlike ask_vlan_offload).
 * Arming is per-port only, via ask_hw_offload_set_bridge() / genl
 * ASK_ATTR_BRIDGE (kernel/ask_hw.c), driven automatically by VyOS's
 * `interfaces bridge` conf_mode for a member port that already has
 * `offload ipv4`/`offload ipv6` armed — no separate opt-in, and no CLI
 * leafNode a user sets directly. Forcing bridge offload on regardless of
 * a port's family engagement wouldn't mean anything (there is no dispatch
 * for it to ride on), so a master override doesn't make sense here the way
 * it does for VLAN.
 */

/* One coalesced FDB event. @dev is dev_hold()'d at capture, dev_put() in
 * the worker. Identified for coalescing by (dev, addr, vid) — the same
 * triple the kernel bridge itself keys an FDB entry by. */
struct ask_bridge_fdb_event {
	struct list_head   node;
	struct net_device *dev;
	u8                 addr[ETH_ALEN];
	u16                vid;
	bool               add;		/* true = ADD_TO_DEVICE, false = DEL */
	bool               added_by_user;
	bool               is_local;
	bool               locked;
};

#define ASK_BRIDGE_FDB_EV_MAX 1024

static LIST_HEAD(ask_bridge_fdb_ev_list);
static DEFINE_SPINLOCK(ask_bridge_fdb_ev_lock);
static unsigned int ask_bridge_fdb_ev_count;
static atomic_t ask_bridge_fdb_ev_coalesced = ATOMIC_INIT(0);
static atomic_t ask_bridge_fdb_ev_dropped   = ATOMIC_INIT(0);
static struct work_struct ask_bridge_fdb_work;

static bool ask_bridge_notifiers_registered;
static bool ask_bridge_blocking_registered;
static bool ask_bridge_netdev_registered;

/* Process context: safe to log (and, once B1-B3 land, to call the sleeping
 * CC-install path) here. */
static void ask_bridge_fdb_work_fn(struct work_struct *w)
{
	struct ask_bridge_fdb_event *ev;

	for (;;) {
		spin_lock_bh(&ask_bridge_fdb_ev_lock);
		ev = list_first_entry_or_null(&ask_bridge_fdb_ev_list,
					      struct ask_bridge_fdb_event, node);
		if (ev) {
			list_del(&ev->node);
			ask_bridge_fdb_ev_count--;
		}
		spin_unlock_bh(&ask_bridge_fdb_ev_lock);
		if (!ev)
			break;

		/*
		 * §4 admission filter (plan): is_local and locked entries are
		 * never offload candidates. static (added_by_user) vs dynamic
		 * is logged since plan §12 point 1 defaults the eventual B3
		 * install scope to static-only (dynamic entries need the
		 * ageing-refresh mechanism §8 point 4 first).
		 */
		if (ev->is_local || ev->locked) {
			ask_pr_dbg("bridge: FDB %s %pM vid=%u dev=%s skip (is_local=%d locked=%d)\n",
				   ev->add ? "add" : "del", ev->addr, ev->vid,
				   netdev_name(ev->dev), ev->is_local, ev->locked);
		} else {
			ask_pr_info("bridge: FDB %s %pM vid=%u dev=%s (%s) — %s\n",
				    ev->add ? "add" : "del", ev->addr, ev->vid,
				    netdev_name(ev->dev),
				    ev->added_by_user ? "static" : "dynamic",
				    ask_hw_bridge_offload_armed() ?
				    "bridge offload armed on at least one port, no B1/B2 installer yet (B0)" :
				    "bridge offload not armed on any port, observer only");
		}

		dev_put(ev->dev);
		kfree(ev);
	}
}

static int ask_bridge_fdb_notifier(struct notifier_block *nb,
				   unsigned long event, void *ptr)
{
	struct switchdev_notifier_fdb_info *fdb_info;
	struct net_device *dev = switchdev_notifier_info_to_dev(ptr);
	struct ask_bridge_fdb_event *ev;
	bool add;

	switch (event) {
	case SWITCHDEV_FDB_ADD_TO_DEVICE:
		add = true;
		break;
	case SWITCHDEV_FDB_DEL_TO_DEVICE:
		add = false;
		break;
	default:
		return NOTIFY_DONE;
	}

	fdb_info = container_of(ptr, struct switchdev_notifier_fdb_info, info);

	/* Coalesce first, under the lock, without allocating: a MAC that is
	 * already queued just gets its add/flag state refreshed in place
	 * (plan §12 point 3). */
	spin_lock_bh(&ask_bridge_fdb_ev_lock);
	list_for_each_entry(ev, &ask_bridge_fdb_ev_list, node) {
		if (ev->dev == dev && ev->vid == fdb_info->vid &&
		    ether_addr_equal(ev->addr, fdb_info->addr)) {
			ev->add = add;
			ev->added_by_user = fdb_info->added_by_user;
			ev->is_local = fdb_info->is_local;
			ev->locked = fdb_info->locked;
			spin_unlock_bh(&ask_bridge_fdb_ev_lock);
			atomic_inc(&ask_bridge_fdb_ev_coalesced);
			schedule_work(&ask_bridge_fdb_work);
			return NOTIFY_DONE;
		}
	}
	if (ask_bridge_fdb_ev_count >= ASK_BRIDGE_FDB_EV_MAX) {
		spin_unlock_bh(&ask_bridge_fdb_ev_lock);
		/* Dropping is safe here: B0 has no install path to fall out
		 * of sync with, this only loses a log line. */
		if (atomic_inc_return(&ask_bridge_fdb_ev_dropped) == 1 ||
		    net_ratelimit())
			ask_pr_warn("bridge: FDB event queue full (%u) — dropping (total dropped=%d)\n",
				    ASK_BRIDGE_FDB_EV_MAX,
				    atomic_read(&ask_bridge_fdb_ev_dropped));
		return NOTIFY_DONE;
	}
	spin_unlock_bh(&ask_bridge_fdb_ev_lock);

	/* Atomic context (switchdev's atomic notifier chain): capture only. */
	ev = kzalloc(sizeof(*ev), GFP_ATOMIC);
	if (!ev)
		return NOTIFY_DONE;
	dev_hold(dev);
	ev->dev = dev;
	ether_addr_copy(ev->addr, fdb_info->addr);
	ev->vid = fdb_info->vid;
	ev->add = add;
	ev->added_by_user = fdb_info->added_by_user;
	ev->is_local = fdb_info->is_local;
	ev->locked = fdb_info->locked;

	spin_lock_bh(&ask_bridge_fdb_ev_lock);
	if (ask_bridge_fdb_ev_count >= ASK_BRIDGE_FDB_EV_MAX) {
		spin_unlock_bh(&ask_bridge_fdb_ev_lock);
		dev_put(dev);
		kfree(ev);
		return NOTIFY_DONE;
	}
	list_add_tail(&ev->node, &ask_bridge_fdb_ev_list);
	ask_bridge_fdb_ev_count++;
	spin_unlock_bh(&ask_bridge_fdb_ev_lock);

	schedule_work(&ask_bridge_fdb_work);
	return NOTIFY_DONE;
}

static struct notifier_block ask_bridge_fdb_nb = {
	.notifier_call = ask_bridge_fdb_notifier,
};

/*
 * Blocking chain: port attributes (STP state, bridge flags, ageing).
 * Runs in process context — sleeping here is fine — but B0 only logs.
 */
static int ask_bridge_blocking_notifier(struct notifier_block *nb,
					unsigned long event, void *ptr)
{
	struct switchdev_notifier_port_attr_info *info;
	struct net_device *dev = switchdev_notifier_info_to_dev(ptr);

	if (event != SWITCHDEV_PORT_ATTR_SET)
		return NOTIFY_DONE;

	info = container_of(ptr, struct switchdev_notifier_port_attr_info, info);
	if (info->attr->id == SWITCHDEV_ATTR_ID_PORT_STP_STATE)
		ask_pr_info("bridge: dev=%s STP state -> %u (B0 observer, no HW action)\n",
			    netdev_name(dev), info->attr->u.stp_state);

	return NOTIFY_DONE;
}

static struct notifier_block ask_bridge_blocking_nb = {
	.notifier_call = ask_bridge_blocking_notifier,
};

/*
 * netdevice chain: bridge join/leave (NETDEV_CHANGEUPPER to a bridge
 * master). B3 will call switchdev_bridge_port_offload()/_unoffload() from
 * here; B0 only logs the transition.
 */
static int ask_bridge_netdev_notifier(struct notifier_block *nb,
				      unsigned long event, void *ptr)
{
	struct net_device *dev = netdev_notifier_info_to_dev(ptr);
	struct netdev_notifier_changeupper_info *info = ptr;

	if (event != NETDEV_CHANGEUPPER)
		return NOTIFY_DONE;
	if (!info->upper_dev || !netif_is_bridge_master(info->upper_dev))
		return NOTIFY_DONE;

	ask_pr_info("bridge: dev=%s %s bridge %s (B0 observer, no HW action)\n",
		    netdev_name(dev), info->linking ? "joined" : "left",
		    netdev_name(info->upper_dev));

	return NOTIFY_DONE;
}

static struct notifier_block ask_bridge_netdev_nb = {
	.notifier_call = ask_bridge_netdev_notifier,
};

int ask_bridge_init(void)
{
	int rc;

	INIT_WORK(&ask_bridge_fdb_work, ask_bridge_fdb_work_fn);

	rc = register_switchdev_notifier(&ask_bridge_fdb_nb);
	if (rc) {
		ask_pr_err("bridge: register_switchdev_notifier failed: %d\n", rc);
		return rc;
	}
	ask_bridge_notifiers_registered = true;

	rc = register_switchdev_blocking_notifier(&ask_bridge_blocking_nb);
	if (rc) {
		ask_pr_err("bridge: register_switchdev_blocking_notifier failed: %d\n", rc);
		goto err_blocking;
	}
	ask_bridge_blocking_registered = true;

	rc = register_netdevice_notifier(&ask_bridge_netdev_nb);
	if (rc) {
		ask_pr_err("bridge: register_netdevice_notifier failed: %d\n", rc);
		goto err_netdev;
	}
	ask_bridge_netdev_registered = true;

	ask_pr_info("bridge: switchdev FDB observer active (T-M6-2 B0 — logs only, no HW install path yet)\n");
	return 0;

err_netdev:
	unregister_switchdev_blocking_notifier(&ask_bridge_blocking_nb);
	ask_bridge_blocking_registered = false;
err_blocking:
	unregister_switchdev_notifier(&ask_bridge_fdb_nb);
	ask_bridge_notifiers_registered = false;
	return rc;
}

void ask_bridge_exit(void)
{
	struct ask_bridge_fdb_event *ev, *tmp;

	if (ask_bridge_netdev_registered) {
		unregister_netdevice_notifier(&ask_bridge_netdev_nb);
		ask_bridge_netdev_registered = false;
	}
	if (ask_bridge_blocking_registered) {
		unregister_switchdev_blocking_notifier(&ask_bridge_blocking_nb);
		ask_bridge_blocking_registered = false;
	}
	if (ask_bridge_notifiers_registered) {
		unregister_switchdev_notifier(&ask_bridge_fdb_nb);
		ask_bridge_notifiers_registered = false;
	}

	/* No notifier can queue new work now; flush what's in flight. */
	cancel_work_sync(&ask_bridge_fdb_work);
	spin_lock_bh(&ask_bridge_fdb_ev_lock);
	list_for_each_entry_safe(ev, tmp, &ask_bridge_fdb_ev_list, node) {
		list_del(&ev->node);
		ask_bridge_fdb_ev_count--;
		dev_put(ev->dev);
		kfree(ev);
	}
	spin_unlock_bh(&ask_bridge_fdb_ev_lock);

	ask_pr_dbg("bridge: exit\n");
}
