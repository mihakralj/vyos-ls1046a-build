// SPDX-License-Identifier: GPL-2.0
/*
 * ASK2 - neigh subsystem: NETEVENT_NEIGH_UPDATE notifier (T-M6-3)
 *
 * Single owner of neighbour events for the ASK offload, mirroring the mainline
 * HW-offload pattern (mlx5e_rep_neigh, nfp): the notifier lives here, the flow
 * layer is a consumer driven through two entry points.
 *
 * The netevent chain is ATOMIC (net/core/netevent.c ATOMIC_NOTIFIER_HEAD), so
 * the notifier callback runs in atomic context and MUST NOT sleep.  The flow
 * entry points it needs — ask_flow_neigh_resolved() (deferred-insert drain) and
 * ask_flow_neigh_mac_changed() (stale-MAC rebuild) — both replay GFP_KERNEL HW
 * inserts and therefore sleep.  So the notifier only captures (dev, dst_ip,
 * new_mac) and defers the work to a workqueue that runs in process context.
 * This also closes the historical PR14z8 "deferred-insert OK=0" gap, where the
 * old inline call from the atomic notifier could never complete the insert.
 */

#include <linux/kernel.h>
#include <linux/slab.h>
#include <linux/list.h>
#include <linux/spinlock.h>
#include <linux/workqueue.h>
#include <linux/notifier.h>
#include <linux/netdevice.h>
#include <linux/etherdevice.h>
#include <net/arp.h>
#include <net/neighbour.h>
#include <net/netevent.h>

#include "include/ask_internal.h"

/*
 * One captured neigh event.  @dev is dev_hold()'d in the atomic notifier and
 * dev_put() in the worker so it stays valid across the deferral.
 */
struct ask_neigh_event {
	struct list_head   node;
	struct net_device *dev;
	__be32             dst_ip;
	u8                 mac[ETH_ALEN];
};

static LIST_HEAD(ask_neigh_ev_list);
static DEFINE_SPINLOCK(ask_neigh_ev_lock);
static struct work_struct ask_neigh_work;
static bool ask_neigh_registered;

/* Process context: safe to run the sleeping (GFP_KERNEL) flow entry points. */
static void ask_neigh_work_fn(struct work_struct *w)
{
	struct ask_neigh_event *ev;

	for (;;) {
		spin_lock_bh(&ask_neigh_ev_lock);
		ev = list_first_entry_or_null(&ask_neigh_ev_list,
					      struct ask_neigh_event, node);
		if (ev)
			list_del(&ev->node);
		spin_unlock_bh(&ask_neigh_ev_lock);
		if (!ev)
			break;

		/* Deferred-insert drain, then stale-MAC rebuild, for this
		 * now-resolved next-hop. */
		ask_flow_neigh_resolved(ev->dev, ev->dst_ip);
		ask_flow_neigh_mac_changed(ev->dev, ev->dst_ip, ev->mac);

		dev_put(ev->dev);
		kfree(ev);
	}
}

static int ask_neigh_netevent(struct notifier_block *nb,
			      unsigned long event, void *ptr)
{
	struct neighbour *n = ptr;
	struct ask_neigh_event *ev;
	struct net_device *dev;
	__be32 dst_ip;
	u8 mac[ETH_ALEN];

	if (event != NETEVENT_NEIGH_UPDATE)
		return NOTIFY_DONE;
	if (!n || n->tbl != &arp_tbl)
		return NOTIFY_DONE;

	/* Act only on transitions into NUD_VALID (n->ha is now meaningful). */
	read_lock_bh(&n->lock);
	if (!(n->nud_state & NUD_VALID) || !n->dev) {
		read_unlock_bh(&n->lock);
		return NOTIFY_DONE;
	}
	dev = n->dev;
	memcpy(&dst_ip, n->primary_key, sizeof(dst_ip));
	ether_addr_copy(mac, n->ha);
	read_unlock_bh(&n->lock);

	/* Atomic context: capture only, defer to the workqueue. */
	ev = kzalloc(sizeof(*ev), GFP_ATOMIC);
	if (!ev)
		return NOTIFY_DONE;	/* best-effort; PR14z9 poll still drains */
	dev_hold(dev);
	ev->dev = dev;
	ev->dst_ip = dst_ip;
	ether_addr_copy(ev->mac, mac);

	spin_lock_bh(&ask_neigh_ev_lock);
	list_add_tail(&ev->node, &ask_neigh_ev_list);
	spin_unlock_bh(&ask_neigh_ev_lock);

	schedule_work(&ask_neigh_work);
	return NOTIFY_DONE;
}

static struct notifier_block ask_neigh_nb = {
	.notifier_call = ask_neigh_netevent,
};

int ask_neigh_init(void)
{
	int rc;

	INIT_WORK(&ask_neigh_work, ask_neigh_work_fn);

	rc = register_netevent_notifier(&ask_neigh_nb);
	if (rc) {
		ask_pr_err("neigh: register_netevent_notifier failed: %d\n", rc);
		return rc;
	}
	ask_neigh_registered = true;
	ask_pr_info("neigh: netevent notifier active (deferred-insert drain + stale-MAC rebuild)\n");
	return 0;
}

void ask_neigh_exit(void)
{
	struct ask_neigh_event *ev, *tmp;

	if (ask_neigh_registered) {
		unregister_netevent_notifier(&ask_neigh_nb);
		ask_neigh_registered = false;
	}
	/* No notifier can queue new work now; flush what's in flight. */
	cancel_work_sync(&ask_neigh_work);

	/* Drain anything queued between unregister and cancel. */
	spin_lock_bh(&ask_neigh_ev_lock);
	list_for_each_entry_safe(ev, tmp, &ask_neigh_ev_list, node) {
		list_del(&ev->node);
		dev_put(ev->dev);
		kfree(ev);
	}
	spin_unlock_bh(&ask_neigh_ev_lock);

	ask_pr_dbg("neigh: exit\n");
}
