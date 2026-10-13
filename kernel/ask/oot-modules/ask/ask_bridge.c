// SPDX-License-Identifier: GPL-2.0
/*
 * ASK2 - bridge subsystem: static FDB entries -> FMan L2_DA records
 * (T-M6-2 B3a, plans/ASK2-BRIDGE-OFFLOAD-PLAN.md §13).
 *
 * The kernel bridge stays the authority for learning, ageing, STP and
 * flooding. ask.ko mirrors only USER-ADDED, VID-0, non-local, unlocked FDB
 * entries (D4, D6): the CPU never sees the source MACs of hardware-forwarded
 * frames, so learned entries would go stale. Every switchdev event that can
 * touch such an entry carries added_by_user, so those are the only events
 * queued. The worker keeps a shadow of the entries and, under rtnl,
 * recomputes the wanted record set on every change (FDB, engage, bridge
 * join/leave, STP state, port flags, VLAN filtering), adding and deleting
 * records by diff (ask_bridge_resync()).
 *
 * switchdev reports the EGRESS port of an entry; records live in the
 * INGRESS port's table. An entry for MAC A on port E therefore fans out to
 * every other member P as (A, 0x0800) and (A, 0x86dd) -> E's no-confirm TX
 * FQ. Only IPv4/IPv6 EtherTypes: a short non-IP frame hitting a record
 * wedges the FE hit path (§13.16), and ARP etc. belong to the kernel (D5).
 * A pair (P, E) qualifies when both are DPAA ports of the same bridge,
 * engaged as L2_DA, FORWARDING, neither isolated nor locked; P has no VLAN
 * uppers (the key ignores the VID, §13.21: a hit forwards a tagged frame
 * unchanged); the bridge does not filter VLANs (D6); and E's TX FQ is
 * already cached (never allocated under rtnl, CR-012).
 *
 * Not covered (documented in the plan): bridge-family nftables rules (a hit
 * bypasses them), dynamic entries, MAC moves the CPU cannot see.
 *
 * Lock order: rtnl -> ask_bridge_lock -> ask_hw h->lock -> FMan fe_lock.
 */

#include <linux/kernel.h>
#include <linux/slab.h>
#include <linux/list.h>
#include <linux/mutex.h>
#include <linux/spinlock.h>
#include <linux/workqueue.h>
#include <linux/notifier.h>
#include <linux/netdevice.h>
#include <linux/etherdevice.h>
#include <linux/if_bridge.h>
#include <linux/if_vlan.h>
#include <linux/rtnetlink.h>
#include <linux/module.h>
#include <net/switchdev.h>
#include <linux/fsl/fman_pcd.h>

#include "include/ask_internal.h"

/* One coalesced FDB event. @dev is dev_hold()'d at capture, dev_put() in
 * the worker. Coalesced by (dev, addr): VID is always 0 here. */
struct ask_bridge_fdb_event {
	struct list_head   node;
	struct net_device *dev;
	u8                 addr[ETH_ALEN];
	bool               add;		/* true = ADD_TO_DEVICE, false = DEL */
};

/* One mirrored static FDB entry: MAC @addr behind port @port_ifindex. */
struct ask_bridge_entry {
	struct list_head node;
	int              br_ifindex;
	int              port_ifindex;
	u8               addr[ETH_ALEN];
};

/* One installed record in ingress port @port's L2_DA table. */
struct ask_bridge_rec {
	struct list_head node;
	u8               port;
	u8               addr[ETH_ALEN];
	__be16           eth_type;
	u32              fqid;
	bool             want;		/* resync mark */
};

#define ASK_BRIDGE_FDB_EV_MAX	1024
#define ASK_BRIDGE_ENTRY_MAX	512	/* vendor ETHERNET table cap */
#define ASK_BRIDGE_REC_MAX	2048

static LIST_HEAD(ask_bridge_fdb_ev_list);
static DEFINE_SPINLOCK(ask_bridge_fdb_ev_lock);
static unsigned int ask_bridge_fdb_ev_count;
static struct work_struct ask_bridge_fdb_work;
static bool ask_bridge_ready;
/* An event was lost (queue full / OOM): the shadow can no longer be trusted
 * and there is no FDB dump to resync from, so the worker drops it (D3, fail
 * closed: those entries stay in software until re-added). */
static bool ask_bridge_desync;

static DEFINE_MUTEX(ask_bridge_lock);	/* entries + recs */
static LIST_HEAD(ask_bridge_entries);
static LIST_HEAD(ask_bridge_recs);
static unsigned int ask_bridge_n_entries;
static unsigned int ask_bridge_n_recs;

static bool ask_bridge_notifiers_registered;
static bool ask_bridge_blocking_registered;
static bool ask_bridge_netdev_registered;

/*
 * T-M6-2 B1: one FDB entry -> one FE record on the ingress port's L2_DA
 * table (F-255 profile: the key is the frame's 6-byte destination MAC
 * followed by its 2-byte EtherType, 8 bytes total, plan §13.18). Folding
 * EtherType into the key means a record installed for @eth_type structurally
 * cannot be hit by a frame of a different EtherType sharing the same DA:
 * ARP/other non-IP frames miss the table and fall to the kernel FQ by
 * construction, instead of wedging the FE-VM hit path (plan §13.16).
 * HIT = plain ENQUEUE_PKT to @egress_tx_fqid (the egress port's no-confirm
 * TX FQ): rx_fqid carries it, tx_fqid stays 0 so no L2 rewrite is emitted.
 * Only a unicast, non-zero DA is accepted: broadcast/multicast/unknown must
 * keep missing to the kernel bridge.
 */
int ask_bridge_fe_action(const u8 *da, __be16 eth_type, u32 egress_tx_fqid,
			 unsigned long enq_off,
			 struct fman_pcd_fe_flow_action *a)
{
	BUILD_BUG_ON(FMAN_PCD_FE_L2_DA_KEY_SIZE != ETH_ALEN + sizeof(__be16));

	if (!da || !a || !is_valid_ether_addr(da) || !egress_tx_fqid ||
	    egress_tx_fqid > 0xffffff || !enq_off)
		return -EINVAL;
	memset(a, 0, sizeof(*a));
	ether_addr_copy(a->key, da);
	memcpy(a->key + ETH_ALEN, &eth_type, sizeof(eth_type));
	a->key_size  = FMAN_PCD_FE_L2_DA_KEY_SIZE;
	a->enq_off   = enq_off;
	a->table_idx = 0;		/* the ingress port's own table */
	a->rx_fqid   = egress_tx_fqid;
	return 0;
}
EXPORT_SYMBOL_GPL(ask_bridge_fe_action);

void ask_bridge_kick(void)
{
	if (READ_ONCE(ask_bridge_ready))
		schedule_work(&ask_bridge_fdb_work);
}

/* D1: an engaged bridge member is L2_DA while the kill switch is on. */
static enum fman_pcd_fe_profile ask_bridge_profile_for(struct net_device *br)
{
	return br && ask_hw_bridge_offload_armed() ?
		FMAN_PCD_FE_PROFILE_L2_DA : FMAN_PCD_FE_PROFILE_ROUTED;
}

/* Role for an engage of @dev from its current membership (rtnl held). */
enum fman_pcd_fe_profile ask_bridge_port_profile(struct net_device *dev)
{
	ASSERT_RTNL();
	return ask_bridge_profile_for(netif_is_bridge_port(dev) ?
				      netdev_master_upper_dev_get(dev) : NULL);
}

/* @dev is a usable, L2_DA-engaged member of @br; returns its port id. */
static bool ask_bridge_member_ok(struct net_device *dev,
				 struct net_device *br, u8 *port)
{
	return netif_is_bridge_port(dev) &&
	       netdev_master_upper_dev_get(dev) == br &&
	       !ask_hw_dev_port(dev, port) && ask_hw_port_is_l2(*port) &&
	       br_port_get_stp_state(dev) == BR_STATE_FORWARDING &&
	       !br_port_flag_is_set(dev, BR_ISOLATED | BR_PORT_LOCKED);
}

static struct ask_bridge_rec *ask_bridge_rec_find(u8 port, const u8 *addr,
						  __be16 eth_type)
{
	struct ask_bridge_rec *r;

	list_for_each_entry(r, &ask_bridge_recs, node)
		if (r->port == port && r->eth_type == eth_type &&
		    ether_addr_equal(r->addr, addr))
			return r;
	return NULL;
}

/* Mark pass (@add false) or add pass (@add true) for one wanted record. */
static void ask_bridge_one(u8 port, const u8 *addr, __be16 eth_type, u32 fqid,
			   bool add)
{
	struct ask_bridge_rec *r = ask_bridge_rec_find(port, addr, eth_type);
	int rc;

	if (!add) {
		if (r && r->fqid == fqid)
			r->want = true;
		return;
	}
	if (r)
		return;
	if (ask_bridge_n_recs >= ASK_BRIDGE_REC_MAX) {
		pr_warn_ratelimited("ask: bridge: %u records, %pM stays in software\n",
				    ask_bridge_n_recs, addr);
		return;
	}
	r = kzalloc(sizeof(*r), GFP_KERNEL);
	if (!r)
		return;
	rc = ask_hw_l2_record(port, addr, eth_type, fqid, true);
	if (rc) {
		kfree(r);
		pr_warn_ratelimited("ask: bridge: add %pM/0x%04x on port 0x%02x failed: %d\n",
				    addr, ntohs(eth_type), port, rc);
		return;
	}
	r->port = port;
	ether_addr_copy(r->addr, addr);
	r->eth_type = eth_type;
	r->fqid = fqid;
	r->want = true;
	list_add_tail(&r->node, &ask_bridge_recs);
	ask_bridge_n_recs++;
}

static void ask_bridge_walk(bool add)
{
	struct ask_bridge_entry *e;

	list_for_each_entry(e, &ask_bridge_entries, node) {
		struct net_device *br = __dev_get_by_index(&init_net, e->br_ifindex);
		struct net_device *egr = __dev_get_by_index(&init_net, e->port_ifindex);
		struct net_device *in;
		struct list_head *iter;
		u8 eport, iport;
		u32 fqid;

		if (!br || !egr || !netif_is_bridge_master(br) ||
		    br_vlan_enabled(br) ||
		    !ask_bridge_member_ok(egr, br, &eport) ||
		    ask_hw_oif_fqid_cached(egr->ifindex, &fqid))
			continue;
		netdev_for_each_lower_dev(br, in, iter) {
			if (in == egr || vlan_uses_dev(in) ||
			    !ask_bridge_member_ok(in, br, &iport))
				continue;
			ask_bridge_one(iport, e->addr, htons(ETH_P_IP), fqid, add);
			ask_bridge_one(iport, e->addr, htons(ETH_P_IPV6), fqid, add);
		}
	}
}

/* Diff the installed records against the wanted set. rtnl + ask_bridge_lock. */
static void ask_bridge_resync(void)
{
	bool armed = ask_hw_bridge_offload_armed();
	struct ask_bridge_rec *r, *tmp;
	int rc;

	ASSERT_RTNL();
	lockdep_assert_held(&ask_bridge_lock);

	list_for_each_entry(r, &ask_bridge_recs, node)
		r->want = false;
	if (armed)
		ask_bridge_walk(false);
	list_for_each_entry_safe(r, tmp, &ask_bridge_recs, node) {
		if (r->want)
			continue;
		/* A failed delete has already unlinked the record (F-254/F-256
		 * keep only the memory), so tracking goes either way. */
		rc = ask_hw_l2_record(r->port, r->addr, r->eth_type, 0, false);
		if (rc)
			pr_warn_ratelimited("ask: bridge: delete %pM/0x%04x on port 0x%02x: %d\n",
					    r->addr, ntohs(r->eth_type), r->port, rc);
		list_del(&r->node);
		kfree(r);
		ask_bridge_n_recs--;
	}
	if (armed)
		ask_bridge_walk(true);
}

static void ask_bridge_entries_drop(bool (*match)(struct ask_bridge_entry *,
						  const void *),
				    const void *arg)
{
	struct ask_bridge_entry *e, *tmp;

	list_for_each_entry_safe(e, tmp, &ask_bridge_entries, node) {
		if (match && !match(e, arg))
			continue;
		list_del(&e->node);
		kfree(e);
		ask_bridge_n_entries--;
	}
}

struct ask_bridge_key {
	int        ifindex;	/* bridge (ADD) or port (DEL) */
	const u8  *addr;
};

static bool ask_bridge_match_br(struct ask_bridge_entry *e, const void *arg)
{
	const struct ask_bridge_key *k = arg;

	return e->br_ifindex == k->ifindex && ether_addr_equal(e->addr, k->addr);
}

static bool ask_bridge_match_port(struct ask_bridge_entry *e, const void *arg)
{
	const struct ask_bridge_key *k = arg;

	return e->port_ifindex == k->ifindex && ether_addr_equal(e->addr, k->addr);
}

static void ask_bridge_apply_event(const struct ask_bridge_fdb_event *ev)
{
	struct ask_bridge_key k = { .addr = ev->addr };
	struct ask_bridge_entry *e;
	struct net_device *br;

	if (!ev->add) {
		k.ifindex = ev->dev->ifindex;
		ask_bridge_entries_drop(ask_bridge_match_port, &k);
		return;
	}
	br = netif_is_bridge_port(ev->dev) ?
		netdev_master_upper_dev_get(ev->dev) : NULL;
	if (!br)
		return;		/* the port left; its DEL is queued too */
	k.ifindex = br->ifindex;	/* (bridge, MAC) is unique: a move */
	ask_bridge_entries_drop(ask_bridge_match_br, &k);
	if (ask_bridge_n_entries >= ASK_BRIDGE_ENTRY_MAX) {
		pr_warn_ratelimited("ask: bridge: %u static entries, %pM stays in software\n",
				    ask_bridge_n_entries, ev->addr);
		return;
	}
	e = kzalloc(sizeof(*e), GFP_KERNEL);
	if (!e)
		return;
	e->br_ifindex = br->ifindex;
	e->port_ifindex = ev->dev->ifindex;
	ether_addr_copy(e->addr, ev->addr);
	list_add_tail(&e->node, &ask_bridge_entries);
	ask_bridge_n_entries++;
}

static void ask_bridge_fdb_work_fn(struct work_struct *w)
{
	struct ask_bridge_fdb_event *ev;

	rtnl_lock();
	mutex_lock(&ask_bridge_lock);
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
		ask_bridge_apply_event(ev);
		dev_put(ev->dev);
		kfree(ev);
	}
	if (READ_ONCE(ask_bridge_desync)) {
		WRITE_ONCE(ask_bridge_desync, false);
		ask_bridge_entries_drop(NULL, NULL);
		pr_warn("ask: bridge: FDB event lost, static entries dropped to software until re-added\n");
	}
	ask_bridge_resync();
	mutex_unlock(&ask_bridge_lock);
	rtnl_unlock();
}

/* @hw_port_id was disengaged: its table and records are gone. */
void ask_bridge_forget_port(u8 hw_port_id)
{
	struct ask_bridge_rec *r, *tmp;

	mutex_lock(&ask_bridge_lock);
	list_for_each_entry_safe(r, tmp, &ask_bridge_recs, node) {
		if (r->port != hw_port_id)
			continue;
		list_del(&r->node);
		kfree(r);
		ask_bridge_n_recs--;
	}
	mutex_unlock(&ask_bridge_lock);
	ask_bridge_kick();	/* records elsewhere may target this port */
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
	if (!fdb_info->added_by_user || fdb_info->is_local ||
	    fdb_info->locked || fdb_info->vid)
		return NOTIFY_DONE;

	/* Coalesce first, under the lock, without allocating. */
	spin_lock_bh(&ask_bridge_fdb_ev_lock);
	list_for_each_entry(ev, &ask_bridge_fdb_ev_list, node) {
		if (ev->dev == dev && ether_addr_equal(ev->addr, fdb_info->addr)) {
			ev->add = add;
			spin_unlock_bh(&ask_bridge_fdb_ev_lock);
			ask_bridge_kick();
			return NOTIFY_DONE;
		}
	}
	spin_unlock_bh(&ask_bridge_fdb_ev_lock);

	/* Atomic context (switchdev's atomic notifier chain): capture only. */
	ev = kzalloc(sizeof(*ev), GFP_ATOMIC);
	if (!ev)
		goto lost;
	dev_hold(dev);
	ev->dev = dev;
	ether_addr_copy(ev->addr, fdb_info->addr);
	ev->add = add;

	spin_lock_bh(&ask_bridge_fdb_ev_lock);
	if (ask_bridge_fdb_ev_count >= ASK_BRIDGE_FDB_EV_MAX) {
		spin_unlock_bh(&ask_bridge_fdb_ev_lock);
		dev_put(dev);
		kfree(ev);
		goto lost;
	}
	list_add_tail(&ev->node, &ask_bridge_fdb_ev_list);
	ask_bridge_fdb_ev_count++;
	spin_unlock_bh(&ask_bridge_fdb_ev_lock);
	ask_bridge_kick();
	return NOTIFY_DONE;

lost:
	WRITE_ONCE(ask_bridge_desync, true);
	ask_bridge_kick();
	return NOTIFY_DONE;
}

static struct notifier_block ask_bridge_fdb_nb = {
	.notifier_call = ask_bridge_fdb_notifier,
};

/*
 * Blocking chain: STP state, port flags and VLAN filtering change which
 * records are wanted. Port flags only reach us as PRE_BRIDGE_FLAGS (no
 * driver claims the attribute, so the bridge never sends BRIDGE_FLAGS); the
 * flags are applied before the worker gets rtnl.
 */
static int ask_bridge_blocking_notifier(struct notifier_block *nb,
					unsigned long event, void *ptr)
{
	struct switchdev_notifier_port_attr_info *info = ptr;

	if (event != SWITCHDEV_PORT_ATTR_SET)
		return NOTIFY_DONE;
	switch (info->attr->id) {
	case SWITCHDEV_ATTR_ID_PORT_STP_STATE:
	case SWITCHDEV_ATTR_ID_PORT_PRE_BRIDGE_FLAGS:
	case SWITCHDEV_ATTR_ID_PORT_BRIDGE_FLAGS:
	case SWITCHDEV_ATTR_ID_BRIDGE_VLAN_FILTERING:
		ask_bridge_kick();
		break;
	default:
		break;
	}
	return NOTIFY_DONE;
}

static struct notifier_block ask_bridge_blocking_nb = {
	.notifier_call = ask_bridge_blocking_notifier,
};

/*
 * netdevice chain (rtnl held): a bridge join or leave switches an engaged
 * port's role (D1); any upper change (VLAN vifs included) can change which
 * records are wanted.
 */
static int ask_bridge_netdev_notifier(struct notifier_block *nb,
				      unsigned long event, void *ptr)
{
	struct net_device *dev = netdev_notifier_info_to_dev(ptr);
	struct netdev_notifier_changeupper_info *info = ptr;
	u8 port;
	int rc;

	if (event != NETDEV_CHANGEUPPER)
		return NOTIFY_DONE;
	if (info->upper_dev && netif_is_bridge_master(info->upper_dev) &&
	    !ask_hw_dev_port(dev, &port)) {
		rc = ask_hw_offload_engage_role(port,
				ask_bridge_profile_for(info->linking ?
						       info->upper_dev : NULL),
				true);
		if (rc)
			ask_pr_err("bridge: %s %s %s: re-engage failed: %d\n",
				   netdev_name(dev),
				   info->linking ? "joined" : "left",
				   netdev_name(info->upper_dev), rc);
	}
	ask_bridge_kick();
	return NOTIFY_DONE;
}

static struct notifier_block ask_bridge_netdev_nb = {
	.notifier_call = ask_bridge_netdev_notifier,
};

int ask_bridge_init(void)
{
	int rc;

	INIT_WORK(&ask_bridge_fdb_work, ask_bridge_fdb_work_fn);
	WRITE_ONCE(ask_bridge_ready, true);

	rc = register_switchdev_notifier(&ask_bridge_fdb_nb);
	if (rc) {
		ask_pr_err("bridge: register_switchdev_notifier failed: %d\n", rc);
		goto err;
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

	ask_pr_info("bridge: static FDB offload ready (T-M6-2 B3a, bridge_offload=%d)\n",
		    ask_hw_bridge_offload_armed());
	return 0;

err_netdev:
	unregister_switchdev_blocking_notifier(&ask_bridge_blocking_nb);
	ask_bridge_blocking_registered = false;
err_blocking:
	unregister_switchdev_notifier(&ask_bridge_fdb_nb);
	ask_bridge_notifiers_registered = false;
err:
	WRITE_ONCE(ask_bridge_ready, false);
	return rc;
}

void ask_bridge_exit(void)
{
	struct ask_bridge_fdb_event *ev, *tmp;
	struct ask_bridge_rec *r, *rtmp;

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
	WRITE_ONCE(ask_bridge_ready, false);
	cancel_work_sync(&ask_bridge_fdb_work);
	spin_lock_bh(&ask_bridge_fdb_ev_lock);
	list_for_each_entry_safe(ev, tmp, &ask_bridge_fdb_ev_list, node) {
		list_del(&ev->node);
		ask_bridge_fdb_ev_count--;
		dev_put(ev->dev);
		kfree(ev);
	}
	spin_unlock_bh(&ask_bridge_fdb_ev_lock);

	/* Records go with the port tables when ask_hw tears the ports down. */
	mutex_lock(&ask_bridge_lock);
	ask_bridge_entries_drop(NULL, NULL);
	list_for_each_entry_safe(r, rtmp, &ask_bridge_recs, node) {
		list_del(&r->node);
		kfree(r);
	}
	ask_bridge_n_recs = 0;
	mutex_unlock(&ask_bridge_lock);

	ask_pr_dbg("bridge: exit\n");
}
