// SPDX-License-Identifier: GPL-2.0
/*
 * ASK2 - neigh subsystem: NETEVENT_NEIGH_UPDATE notifier (T-M6-3)
 *
 * Single owner of neighbour events for the ASK offload, mirroring the mainline
 * HW-offload pattern (mlx5e_rep_neigh, nfp): the notifier lives here, the flow
 * layer is a consumer driven through two entry points.
 *
 * Covers both neighbour tables that can back an offloaded next-hop: arp_tbl
 * (IPv4) and, since T-M6-1 piece 4, nd_tbl (IPv6/NDISC).  The v6 half stays
 * inert until T-M6-1 pieces 2-3 give v6 flows a HW insert path — today they
 * are parsed and SW-tracked but rejected at the HW gate, so no installed v6
 * flow can match the stale-MAC walk.
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
#include <net/ndisc.h>
#include <net/neighbour.h>
#include <net/netevent.h>

#include "include/ask_internal.h"

/*
 * One captured neigh event.  @dev is dev_hold()'d in the atomic notifier and
 * dev_put() in the worker so it stays valid across the deferral.
 *
 * T-M6-1 piece 4: @dst_ip is a 16-byte buffer carrying either a 4-byte arp_tbl
 * key (IPv4, remaining bytes zero) or a 16-byte nd_tbl key (IPv6); @l3_proto
 * says which.  This mirrors struct ask_flow_key, whose dst_ip is already 16
 * bytes, so no separate v6 event type is needed.
 */
struct ask_neigh_event {
	struct list_head   node;
	struct net_device *dev;
	u8                 dst_ip[16];
	u8                 l3_proto;	/* ASK_FLOW_L3_IPV4 / _IPV6 */
	u8                 mac[ETH_ALEN];
};

/*
 * Bound + coalesce (2026-07-26).
 *
 * The queue used to be an unbounded list with an unconditional enqueue: a
 * neighbour-update storm (ARP/ND churn on a busy segment, or a scan against a
 * populated /24) could pin memory with only GFP_ATOMIC failure as a backstop,
 * and each queued event costs a full flow-table walk in the worker.
 *
 * Two mitigations, mirroring the deferred-insert pending queue in
 * ask_flow_offload.c:
 *
 *   COALESCE — a neighbour is identified by (dev ifindex, l3_proto, dst_ip).
 *   Repeated updates for the same neighbour are idempotent from our side:
 *   both consumers act on the LATEST lladdr, so an already-queued event is
 *   simply refreshed in place with the newer MAC rather than appended. This
 *   is what actually tames a storm — flapping one neighbour stays O(1).
 *
 *   CAP — distinct neighbours are still bounded. Beyond the cap new events
 *   are dropped and counted; dropping is safe because it degrades to the
 *   pre-existing behaviour for that neighbour (SW path keeps forwarding, and
 *   the PR14z9 active-poll fallback in ask_flow_offload.c still drains
 *   pending inserts).
 *
 * Sized well above the realistic distinct-next-hop count for this box (a
 * /24 of directly-attached peers is 254) while costing ~40 B/entry.
 */
#define ASK_NEIGH_EV_MAX 1024

static LIST_HEAD(ask_neigh_ev_list);
static DEFINE_SPINLOCK(ask_neigh_ev_lock);
static unsigned int ask_neigh_ev_count;
static atomic_t ask_neigh_ev_coalesced = ATOMIC_INIT(0);
static atomic_t ask_neigh_ev_dropped   = ATOMIC_INIT(0);
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
		if (ev) {
			list_del(&ev->node);
			ask_neigh_ev_count--;
		}
		spin_unlock_bh(&ask_neigh_ev_lock);
		if (!ev)
			break;

		/* Deferred-insert drain, then stale-MAC rebuild, for this
		 * now-resolved next-hop.
		 *
		 * The drain is IPv4-only: the pending queue is keyed by __be32
		 * and only the v4 HW-insert path ever parks entries on it (v6
		 * flows are rejected at the HW gate with -EOPNOTSUPP until
		 * T-M6-1 pieces 2-3 land), so a v6 event has nothing to drain.
		 * The stale-MAC rebuild is family-generic and runs for both.
		 */
		if (ev->l3_proto == ASK_FLOW_L3_IPV4) {
			__be32 v4;

			memcpy(&v4, ev->dst_ip, sizeof(v4));
			ask_flow_neigh_resolved(ev->dev, v4);
		}
		ask_flow_neigh_mac_changed(ev->dev, ev->dst_ip, ev->l3_proto,
					   ev->mac);

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
	u8 dst_ip[16] = { 0 };
	unsigned int key_len;
	u8 l3_proto;
	u8 mac[ETH_ALEN];

	if (event != NETEVENT_NEIGH_UPDATE)
		return NOTIFY_DONE;
	if (!n)
		return NOTIFY_DONE;

	/* T-M6-1 piece 4: arp_tbl (IPv4) and nd_tbl (IPv6/NDISC) are the two
	 * tables whose entries can back an offloaded next-hop.  Everything
	 * else (e.g. DECnet-style or bridge tables) is not ours.
	 */
	if (n->tbl == &arp_tbl)
		l3_proto = ASK_FLOW_L3_IPV4;
	else if (n->tbl == &nd_tbl)
		l3_proto = ASK_FLOW_L3_IPV6;
	else
		return NOTIFY_DONE;

	/* Take the length from the table itself rather than assuming 4/16, and
	 * refuse anything that would overflow the capture buffer.
	 */
	key_len = n->tbl->key_len;
	if (key_len != ask_flow_l3_addr_len(l3_proto) || key_len > sizeof(dst_ip))
		return NOTIFY_DONE;

	/* Act only on transitions into NUD_VALID (n->ha is now meaningful). */
	read_lock_bh(&n->lock);
	if (!(n->nud_state & NUD_VALID) || !n->dev) {
		read_unlock_bh(&n->lock);
		return NOTIFY_DONE;
	}
	dev = n->dev;
	memcpy(dst_ip, n->primary_key, key_len);
	ether_addr_copy(mac, n->ha);
	read_unlock_bh(&n->lock);

	/*
	 * Coalesce first, under the lock, without allocating: if this
	 * neighbour is already queued just refresh its lladdr in place. Both
	 * consumers act on the latest MAC, so collapsing N updates for one
	 * neighbour into one event is semantically identical and keeps a
	 * flapping neighbour O(1) in both memory and worker cost.
	 */
	spin_lock_bh(&ask_neigh_ev_lock);
	list_for_each_entry(ev, &ask_neigh_ev_list, node) {
		if (ev->dev == dev && ev->l3_proto == l3_proto &&
		    memcmp(ev->dst_ip, dst_ip, key_len) == 0) {
			ether_addr_copy(ev->mac, mac);
			spin_unlock_bh(&ask_neigh_ev_lock);
			atomic_inc(&ask_neigh_ev_coalesced);
			schedule_work(&ask_neigh_work);
			return NOTIFY_DONE;
		}
	}
	if (ask_neigh_ev_count >= ASK_NEIGH_EV_MAX) {
		spin_unlock_bh(&ask_neigh_ev_lock);
		/* Dropping degrades to pre-notifier behaviour for this
		 * neighbour: the SW path keeps forwarding and the PR14z9
		 * active-poll fallback still drains deferred inserts. */
		if (atomic_inc_return(&ask_neigh_ev_dropped) == 1 ||
		    net_ratelimit())
			ask_pr_warn("neigh: event queue full (%u) — dropping (total dropped=%d)\n",
				    ASK_NEIGH_EV_MAX,
				    atomic_read(&ask_neigh_ev_dropped));
		return NOTIFY_DONE;
	}
	spin_unlock_bh(&ask_neigh_ev_lock);

	/* Atomic context: capture only, defer to the workqueue. */
	ev = kzalloc(sizeof(*ev), GFP_ATOMIC);
	if (!ev)
		return NOTIFY_DONE;	/* best-effort; PR14z9 poll still drains */
	dev_hold(dev);
	ev->dev = dev;
	ev->l3_proto = l3_proto;
	memcpy(ev->dst_ip, dst_ip, sizeof(ev->dst_ip));
	ether_addr_copy(ev->mac, mac);

	/*
	 * Re-check the cap after the unlocked kzalloc: a concurrent notifier
	 * may have filled the queue in the window. Racing to one over the cap
	 * would be harmless, but re-checking keeps the bound exact.
	 */
	spin_lock_bh(&ask_neigh_ev_lock);
	if (ask_neigh_ev_count >= ASK_NEIGH_EV_MAX) {
		spin_unlock_bh(&ask_neigh_ev_lock);
		dev_put(dev);
		kfree(ev);
		atomic_inc(&ask_neigh_ev_dropped);
		return NOTIFY_DONE;
	}
	list_add_tail(&ev->node, &ask_neigh_ev_list);
	ask_neigh_ev_count++;
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
	ask_pr_info("neigh: netevent notifier active (arp_tbl + nd_tbl; deferred-insert drain + stale-MAC rebuild)\n");
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
	ask_neigh_ev_count = 0;
	spin_unlock_bh(&ask_neigh_ev_lock);

	ask_pr_info("neigh: exit (coalesced=%d dropped=%d)\n",
		    atomic_read(&ask_neigh_ev_coalesced),
		    atomic_read(&ask_neigh_ev_dropped));
}
