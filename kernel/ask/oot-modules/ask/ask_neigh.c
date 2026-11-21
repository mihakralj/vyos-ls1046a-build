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
#include <linux/hashtable.h>
#include <linux/jhash.h>
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
	bool               force_walk;	/* coalesced MAC flap / post-purge */
	u64                cache_gen;	/* cache epoch at event capture */
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
#define ASK_NEIGH_CACHE_BITS 9
#define ASK_NEIGH_CACHE_MAX 2048

struct ask_neigh_cache_entry {
	struct hlist_node hnode;
	struct list_head  lru;
	int               ifindex;
	u8                l3_proto;
	u8                dst_ip[16];
	u8                mac[ETH_ALEN];
};

static LIST_HEAD(ask_neigh_ev_list);
static DEFINE_SPINLOCK(ask_neigh_ev_lock);
static unsigned int ask_neigh_ev_count;
static atomic_t ask_neigh_ev_coalesced = ATOMIC_INIT(0);
static atomic_t ask_neigh_ev_dropped   = ATOMIC_INIT(0);
static atomic_t ask_neigh_ev_same_mac  = ATOMIC_INIT(0);
static struct work_struct ask_neigh_work;
static bool ask_neigh_registered;
static bool ask_neigh_netdev_registered;

static DEFINE_SPINLOCK(ask_neigh_cache_lock);
static DECLARE_HASHTABLE(ask_neigh_cache_ht, ASK_NEIGH_CACHE_BITS);
static LIST_HEAD(ask_neigh_cache_lru);
static unsigned int ask_neigh_cache_count;
static u64 ask_neigh_cache_gen;

static u32 ask_neigh_cache_bucket(int ifindex, u8 l3_proto, const u8 *dst_ip)
{
	u32 h = jhash(dst_ip, 16, ((u32)ifindex << 8) ^ l3_proto);

	return h & (HASH_SIZE(ask_neigh_cache_ht) - 1);
}

/*
 * S5(1): check whether (ifindex, proto, ip) is already cached with @mac in
 * the current cache epoch. Read-only on miss so a failed or interrupted
 * stale-MAC walk never poisons the cache.
 */
static bool ask_neigh_cache_hit(int ifindex, u8 l3_proto, const u8 *dst_ip,
				const u8 *mac, u64 ev_gen)
{
	struct ask_neigh_cache_entry *e;
	u32 bkt = ask_neigh_cache_bucket(ifindex, l3_proto, dst_ip);
	bool hit = false;

	spin_lock_bh(&ask_neigh_cache_lock);
	if (ev_gen != ask_neigh_cache_gen)
		goto out;
	hlist_for_each_entry(e, &ask_neigh_cache_ht[bkt], hnode) {
		if (e->ifindex != ifindex || e->l3_proto != l3_proto ||
		    memcmp(e->dst_ip, dst_ip, sizeof(e->dst_ip)) != 0)
			continue;
		if (ether_addr_equal(e->mac, mac)) {
			list_move(&e->lru, &ask_neigh_cache_lru);
			hit = true;
		}
		break;
	}
out:
	spin_unlock_bh(&ask_neigh_cache_lock);
	return hit;
}

/*
 * Commit @mac after ask_flow_neigh_mac_changed() completes cleanly. Skips
 * committing if a device purge advanced ask_neigh_cache_gen since capture.
 */
static void ask_neigh_cache_commit(int ifindex, u8 l3_proto, const u8 *dst_ip,
				   const u8 *mac, u64 ev_gen)
{
	struct ask_neigh_cache_entry *e, *fresh;
	u32 bkt = ask_neigh_cache_bucket(ifindex, l3_proto, dst_ip);

	spin_lock_bh(&ask_neigh_cache_lock);
	if (ev_gen != ask_neigh_cache_gen) {
		spin_unlock_bh(&ask_neigh_cache_lock);
		return;
	}
	hlist_for_each_entry(e, &ask_neigh_cache_ht[bkt], hnode) {
		if (e->ifindex != ifindex || e->l3_proto != l3_proto ||
		    memcmp(e->dst_ip, dst_ip, sizeof(e->dst_ip)) != 0)
			continue;
		ether_addr_copy(e->mac, mac);
		list_move(&e->lru, &ask_neigh_cache_lru);
		spin_unlock_bh(&ask_neigh_cache_lock);
		return;
	}
	spin_unlock_bh(&ask_neigh_cache_lock);

	fresh = kzalloc(sizeof(*fresh), GFP_KERNEL);
	if (!fresh)
		return;
	fresh->ifindex = ifindex;
	fresh->l3_proto = l3_proto;
	memcpy(fresh->dst_ip, dst_ip, sizeof(fresh->dst_ip));
	ether_addr_copy(fresh->mac, mac);

	spin_lock_bh(&ask_neigh_cache_lock);
	if (ev_gen != ask_neigh_cache_gen) {
		spin_unlock_bh(&ask_neigh_cache_lock);
		kfree(fresh);
		return;
	}
	hlist_for_each_entry(e, &ask_neigh_cache_ht[bkt], hnode) {
		if (e->ifindex != ifindex || e->l3_proto != l3_proto ||
		    memcmp(e->dst_ip, dst_ip, sizeof(e->dst_ip)) != 0)
			continue;
		ether_addr_copy(e->mac, mac);
		list_move(&e->lru, &ask_neigh_cache_lru);
		spin_unlock_bh(&ask_neigh_cache_lock);
		kfree(fresh);
		return;
	}
	if (ask_neigh_cache_count >= ASK_NEIGH_CACHE_MAX &&
	    !list_empty(&ask_neigh_cache_lru)) {
		e = list_last_entry(&ask_neigh_cache_lru,
				    struct ask_neigh_cache_entry, lru);
		hlist_del(&e->hnode);
		list_del(&e->lru);
		ask_neigh_cache_count--;
		kfree(e);
	}
	hlist_add_head(&fresh->hnode, &ask_neigh_cache_ht[bkt]);
	list_add(&fresh->lru, &ask_neigh_cache_lru);
	ask_neigh_cache_count++;
	spin_unlock_bh(&ask_neigh_cache_lock);
}

static void ask_neigh_cache_invalidate(int ifindex, u8 l3_proto,
				       const u8 *dst_ip)
{
	struct ask_neigh_cache_entry *e;
	u32 bkt = ask_neigh_cache_bucket(ifindex, l3_proto, dst_ip);

	spin_lock_bh(&ask_neigh_cache_lock);
	hlist_for_each_entry(e, &ask_neigh_cache_ht[bkt], hnode) {
		if (e->ifindex != ifindex || e->l3_proto != l3_proto ||
		    memcmp(e->dst_ip, dst_ip, sizeof(e->dst_ip)) != 0)
			continue;
		hlist_del(&e->hnode);
		list_del(&e->lru);
		ask_neigh_cache_count--;
		kfree(e);
		break;
	}
	spin_unlock_bh(&ask_neigh_cache_lock);
}

static void ask_neigh_cache_purge_dev(int ifindex)
{
	struct ask_neigh_cache_entry *e, *tmp;

	spin_lock_bh(&ask_neigh_cache_lock);
	WRITE_ONCE(ask_neigh_cache_gen, ask_neigh_cache_gen + 1);
	list_for_each_entry_safe(e, tmp, &ask_neigh_cache_lru, lru) {
		if (e->ifindex != ifindex)
			continue;
		hlist_del(&e->hnode);
		list_del(&e->lru);
		ask_neigh_cache_count--;
		kfree(e);
	}
	spin_unlock_bh(&ask_neigh_cache_lock);
}

static void ask_neigh_cache_clear(void)
{
	struct ask_neigh_cache_entry *e, *tmp;

	spin_lock_bh(&ask_neigh_cache_lock);
	WRITE_ONCE(ask_neigh_cache_gen, ask_neigh_cache_gen + 1);
	list_for_each_entry_safe(e, tmp, &ask_neigh_cache_lru, lru) {
		hlist_del(&e->hnode);
		list_del(&e->lru);
		kfree(e);
	}
	ask_neigh_cache_count = 0;
	spin_unlock_bh(&ask_neigh_cache_lock);
}

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
		 * The stale-MAC rebuild is family-generic and runs only when
		 * the neighbour's MAC is new, changed, or force-walked (S5(1)).
		 */
		if (ev->l3_proto == ASK_FLOW_L3_IPV4) {
			__be32 v4;

			memcpy(&v4, ev->dst_ip, sizeof(v4));
			ask_flow_neigh_resolved(ev->dev, v4);
		}
		if (!ev->force_walk &&
		    ask_neigh_cache_hit(ev->dev->ifindex, ev->l3_proto,
					ev->dst_ip, ev->mac, ev->cache_gen)) {
			atomic_inc(&ask_neigh_ev_same_mac);
		} else if (ask_flow_neigh_mac_changed(ev->dev, ev->dst_ip,
						      ev->l3_proto, ev->mac)) {
			ask_neigh_cache_commit(ev->dev->ifindex, ev->l3_proto,
					       ev->dst_ip, ev->mac,
					       ev->cache_gen);
		} else {
			ask_neigh_cache_invalidate(ev->dev->ifindex,
						   ev->l3_proto, ev->dst_ip);
		}

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
	u64 cur_gen;
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
	if (!(n->nud_state & NUD_VALID) || !n->dev ||
	    !net_eq(dev_net(n->dev), &init_net)) {
		read_unlock_bh(&n->lock);
		return NOTIFY_DONE;
	}
	dev = n->dev;
	cur_gen = READ_ONCE(ask_neigh_cache_gen);
	memcpy(dst_ip, n->primary_key, key_len);
	ether_addr_copy(mac, n->ha);
	read_unlock_bh(&n->lock);

	/*
	 * Coalesce first, under the lock, without allocating: if this
	 * neighbour is already queued just refresh its lladdr in place. Both
	 * consumers act on the latest MAC, so collapsing N updates for one
	 * neighbour into one event is semantically identical and keeps a
	 * flapping neighbour O(1) in both memory and worker cost. If the MAC
	 * changed in-flight (e.g. A -> B -> A) or a purge intervened, force
	 * the stale-MAC walk so a cached A cannot mask flows installed with B.
	 */
	spin_lock_bh(&ask_neigh_ev_lock);
	list_for_each_entry(ev, &ask_neigh_ev_list, node) {
		if (ev->dev == dev && ev->l3_proto == l3_proto &&
		    memcmp(ev->dst_ip, dst_ip, key_len) == 0) {
			if (!ether_addr_equal(ev->mac, mac) ||
			    ev->cache_gen != cur_gen)
				ev->force_walk = true;
			ether_addr_copy(ev->mac, mac);
			ev->cache_gen = cur_gen;
			spin_unlock_bh(&ask_neigh_ev_lock);
			atomic_inc(&ask_neigh_ev_coalesced);
			schedule_work(&ask_neigh_work);
			return NOTIFY_DONE;
		}
	}
	if (ask_neigh_ev_count >= ASK_NEIGH_EV_MAX) {
		ask_neigh_cache_invalidate(dev->ifindex, l3_proto, dst_ip);
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
	if (!ev) {
		ask_neigh_cache_invalidate(dev->ifindex, l3_proto, dst_ip);
		return NOTIFY_DONE;	/* best-effort; PR14z9 poll still drains */
	}
	dev_hold(dev);
	ev->dev = dev;
	ev->l3_proto = l3_proto;
	ev->cache_gen = cur_gen;
	memcpy(ev->dst_ip, dst_ip, sizeof(ev->dst_ip));
	ether_addr_copy(ev->mac, mac);

	/*
	 * Re-check the cap after the unlocked kzalloc: a concurrent notifier
	 * may have filled the queue in the window. Racing to one over the cap
	 * would be harmless, but re-checking keeps the bound exact.
	 */
	spin_lock_bh(&ask_neigh_ev_lock);
	if (ask_neigh_ev_count >= ASK_NEIGH_EV_MAX) {
		ask_neigh_cache_invalidate(dev->ifindex, l3_proto, dst_ip);
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

static int ask_neigh_netdev_event(struct notifier_block *nb,
				  unsigned long event, void *ptr)
{
	struct net_device *dev = netdev_notifier_info_to_dev(ptr);

	if (dev && net_eq(dev_net(dev), &init_net) &&
	    (event == NETDEV_DOWN || event == NETDEV_UNREGISTER))
		ask_neigh_cache_purge_dev(dev->ifindex);
	return NOTIFY_DONE;
}

static struct notifier_block ask_neigh_netdev_nb = {
	.notifier_call = ask_neigh_netdev_event,
};

int ask_neigh_init(void)
{
	int rc;

	INIT_WORK(&ask_neigh_work, ask_neigh_work_fn);
	hash_init(ask_neigh_cache_ht);

	rc = register_netdevice_notifier(&ask_neigh_netdev_nb);
	if (rc) {
		ask_pr_err("neigh: register_netdevice_notifier failed: %d\n", rc);
		return rc;
	}
	ask_neigh_netdev_registered = true;

	rc = register_netevent_notifier(&ask_neigh_nb);
	if (rc) {
		unregister_netdevice_notifier(&ask_neigh_netdev_nb);
		ask_neigh_netdev_registered = false;
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
	if (ask_neigh_netdev_registered) {
		unregister_netdevice_notifier(&ask_neigh_netdev_nb);
		ask_neigh_netdev_registered = false;
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

	ask_neigh_cache_clear();

	ask_pr_info("neigh: exit (coalesced=%d dropped=%d same_mac=%d)\n",
		    atomic_read(&ask_neigh_ev_coalesced),
		    atomic_read(&ask_neigh_ev_dropped),
		    atomic_read(&ask_neigh_ev_same_mac));
}
