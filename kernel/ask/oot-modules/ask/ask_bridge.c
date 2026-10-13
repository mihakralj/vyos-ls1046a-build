// SPDX-License-Identifier: GPL-2.0
/*
 * ASK2 - bridge subsystem: forwarded bridge flows -> FMan L2 records
 * (T-M6-2 B6, plans/ASK2-BRIDGE-OFFLOAD-PLAN.md §13.30).
 *
 * A record is one flow: (ingress port, DA, SA, EtherType) -> the egress
 * port's no-confirm TX FQ, in the ingress port's L2_DA table (F-255 14-byte
 * key). A bridge NF_BR_FORWARD hook, after the bridge filter like the
 * vendor's auto_bridge, notes untagged IPv4/IPv6 unicast frames between two
 * L2 ports. The worker (rtnl) installs a record when the live FDB puts DA
 * behind the egress port and SA behind the ingress port, so flooded frames
 * never qualify. Only IPv4/IPv6: a short non-IP frame hitting a record
 * wedges the FE hit path (§13.16), and ARP etc. belong to the kernel (D5).
 *
 * The CPU no longer sees a hardware-forwarded source, so a learned SA is
 * taken over as externally learned: the bridge does not age those and takes
 * them back by itself when the MAC moves (br_fdb_update()). ask.ko deletes
 * records idle for the bridge's ageing time (hardware hit counts), and an SA
 * whose last record goes is handed back by deleting its entry. User-added
 * entries are never taken over: br_fdb_external_learn_del() would delete
 * them.
 *
 * The live FDB and port state are the authority. Every FDB event, port
 * change, engage or kill-switch change revalidates all records and deletes
 * the stale ones; traffic re-creates records.
 *
 * A pair (in, out) qualifies when both are DPAA ports of the same bridge,
 * engaged as L2_DA, FORWARDING, neither isolated nor locked; in has no VLAN
 * uppers (a hit forwards a tagged frame unchanged); the bridge does not
 * filter VLANs (D6); out's TX FQ is already cached (never allocated under
 * rtnl, CR-012); and in's `offload ipv4|ipv6` mask allows the EtherType.
 *
 * Not covered: frames the bridge filter would drop later in the same flow
 * (the record bypasses it, as on the vendor), VLAN-aware bridges.
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
#include <linux/jhash.h>
#include <linux/netfilter.h>
#include <linux/netfilter_bridge.h>
#include <linux/rtnetlink.h>
#include <linux/module.h>
#include <net/switchdev.h>
#include <linux/fsl/fman_pcd.h>
#include <uapi/linux/ask/ask.h>	/* ASK_FAM_V4/V6 */

#include "include/ask_internal.h"

/* One installed record in ingress port @port's L2_DA table. */
struct ask_bridge_rec {
	struct list_head node;
	int              br_ifindex;
	int              in_ifindex;
	int              out_ifindex;
	u8               port;		/* ingress hw port: the record's table */
	u8               da[ETH_ALEN];
	u8               sa[ETH_ALEN];
	__be16           eth_type;
	u32              fqid;		/* out's no-confirm TX FQ */
	u64              pkts;		/* last hardware hit count read */
	unsigned long    last_hit;	/* jiffies of the last count change */
	bool             dead;		/* table freed by a disengage */
};

/* A learned SA taken over as externally learned; ask.ko hands it back. */
struct ask_bridge_own {
	struct list_head node;
	int              br_ifindex;
	int              port_ifindex;
	u8               addr[ETH_ALEN];
};

/* A bridged frame the hook saw; admitted by the worker. */
struct ask_bridge_cand {
	int              in_ifindex;
	int              out_ifindex;
	u8               da[ETH_ALEN];
	u8               sa[ETH_ALEN];
	__be16           eth_type;
};

#define ASK_BRIDGE_REC_MAX	2048
#define ASK_BRIDGE_CAND_MAX	256
#define ASK_BRIDGE_SEEN		1024	/* hook dedup slots, power of 2 */
#define ASK_BRIDGE_AGE_PERIOD	(5 * HZ)

static struct work_struct ask_bridge_work;
static struct delayed_work ask_bridge_age_work;
static bool ask_bridge_ready;

static DEFINE_MUTEX(ask_bridge_lock);	/* recs, owns, cand_work */
static LIST_HEAD(ask_bridge_recs);
static LIST_HEAD(ask_bridge_owns);
static unsigned int ask_bridge_n_recs;

static DEFINE_SPINLOCK(ask_bridge_cand_lock);
static struct ask_bridge_cand ask_bridge_cand[ASK_BRIDGE_CAND_MAX];
static unsigned int ask_bridge_n_cand;
static struct ask_bridge_cand ask_bridge_cand_work[ASK_BRIDGE_CAND_MAX];

/* Best-effort hook dedup: one slot per hash, retried after a second. */
static struct {
	u32           hash;
	unsigned long at;
} ask_bridge_seen[ASK_BRIDGE_SEEN];

/*
 * MACs ever seen as user-added or local FDB entries, hashed, never cleared: a set
 * bit only means "not taken over", so a collision is safe and nothing can
 * be lost (no allocation in the atomic notifier).
 * ponytail: sticky 4096-bit filter; a churn of many static MACs slowly
 * stops takeovers for colliding learned MACs (those then age in the bridge).
 */
static DECLARE_BITMAP(ask_bridge_user_macs, 4096);

static bool ask_bridge_notifiers_registered;
static bool ask_bridge_blocking_registered;
static bool ask_bridge_netdev_registered;
static bool ask_bridge_hook_registered;

/* F-255 L2 key: DA(6) | SA(6) | EtherType(2), MSB-first (plan §13.30). */
void ask_bridge_key(u8 *key, const u8 *da, const u8 *sa, __be16 eth_type)
{
	BUILD_BUG_ON(FMAN_PCD_FE_L2_DA_KEY_SIZE != 2 * ETH_ALEN + sizeof(__be16));
	ether_addr_copy(key, da);
	ether_addr_copy(key + ETH_ALEN, sa);
	memcpy(key + 2 * ETH_ALEN, &eth_type, sizeof(eth_type));
}

/*
 * One bridge flow -> one FE record on the ingress port's L2_DA table.
 * Folding EtherType into the key means a record installed for @eth_type
 * cannot be hit by a frame of another EtherType between the same MACs:
 * ARP/other non-IP frames miss to the kernel FQ by construction instead of
 * wedging the FE-VM hit path (plan §13.16). HIT = plain ENQUEUE_PKT to
 * @egress_tx_fqid: rx_fqid carries it, tx_fqid stays 0 so no L2 rewrite is
 * emitted. Only unicast, non-zero MACs: BUM keeps missing to the bridge.
 */
int ask_bridge_fe_action(const u8 *da, const u8 *sa, __be16 eth_type,
			 u32 egress_tx_fqid, unsigned long enq_off,
			 struct fman_pcd_fe_flow_action *a)
{
	if (!da || !sa || !a || !is_valid_ether_addr(da) ||
	    !is_valid_ether_addr(sa) || !egress_tx_fqid ||
	    egress_tx_fqid > 0xffffff || !enq_off)
		return -EINVAL;
	memset(a, 0, sizeof(*a));
	ask_bridge_key(a->key, da, sa, eth_type);
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
		schedule_work(&ask_bridge_work);
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

static bool ask_bridge_user_mac(const u8 *addr)
{
	return test_bit(jhash(addr, ETH_ALEN, 0) & 4095, ask_bridge_user_macs);
}

/*
 * Would a record (@in -> @out, @da, @sa, @eth_type) be correct now? Fills
 * the ingress port and the egress FQ. rtnl held.
 */
static bool ask_bridge_flow_ok(int br_ifindex, int in_ifindex, int out_ifindex,
			       const u8 *da, const u8 *sa, __be16 eth_type,
			       u8 *iport, u32 *fqid)
{
	struct net_device *br = __dev_get_by_index(&init_net, br_ifindex);
	struct net_device *in = __dev_get_by_index(&init_net, in_ifindex);
	struct net_device *out = __dev_get_by_index(&init_net, out_ifindex);
	u8 fam = eth_type == htons(ETH_P_IP) ? ASK_FAM_V4 : ASK_FAM_V6;
	u8 eport;

	return ask_hw_bridge_offload_armed() &&
	       br && in && out && in != out && netif_is_bridge_master(br) &&
	       !br_vlan_enabled(br) && !vlan_uses_dev(in) &&
	       ask_bridge_member_ok(in, br, iport) &&
	       ask_bridge_member_ok(out, br, &eport) &&
	       (ask_hw_port_family_mask(*iport) & fam) &&
	       !ask_hw_oif_fqid_cached(out_ifindex, fqid) &&
	       br_fdb_find_port(br, da, 0) == out &&
	       br_fdb_find_port(br, sa, 0) == in;
}

static struct ask_bridge_own *ask_bridge_own_find(int br_ifindex,
						  int port_ifindex,
						  const u8 *addr)
{
	struct ask_bridge_own *o;

	list_for_each_entry(o, &ask_bridge_owns, node)
		if (o->br_ifindex == br_ifindex &&
		    o->port_ifindex == port_ifindex &&
		    ether_addr_equal(o->addr, addr))
			return o;
	return NULL;
}

static bool ask_bridge_sa_in_use(int br_ifindex, int in_ifindex, const u8 *sa)
{
	struct ask_bridge_rec *r;

	list_for_each_entry(r, &ask_bridge_recs, node)
		if (!r->dead && r->br_ifindex == br_ifindex &&
		    r->in_ifindex == in_ifindex && ether_addr_equal(r->sa, sa))
			return true;
	return false;
}

/* Send one FDB notification for @addr on @port_ifindex to the bridge
 * (rtnl held). */
static int ask_bridge_fdb_call(unsigned long event, int port_ifindex,
			       const u8 *addr, bool offloaded)
{
	struct net_device *dev = __dev_get_by_index(&init_net, port_ifindex);
	struct switchdev_notifier_fdb_info info = {
		.addr = addr,
		.offloaded = offloaded,
	};

	if (!dev)
		return -ENODEV;
	return notifier_to_errno(call_switchdev_notifiers(event, dev,
							  &info.info, NULL));
}

/* The port @addr is behind in @br_ifindex's FDB, or 0. rtnl held. */
static int ask_bridge_fdb_port(int br_ifindex, const u8 *addr)
{
	struct net_device *br = __dev_get_by_index(&init_net, br_ifindex);
	struct net_device *port;

	if (!br || !netif_is_bridge_master(br))
		return 0;
	port = br_fdb_find_port(br, addr, 0);
	return port ? port->ifindex : 0;
}

/*
 * @sa on @in_ifindex lost its last record: a taken-over entry is handed
 * back by deleting it (the bridge relearns the host from its next frame);
 * any other entry just loses its "offload" mark.
 */
static void ask_bridge_sa_release(int br_ifindex, int in_ifindex, const u8 *sa)
{
	struct ask_bridge_own *o = ask_bridge_own_find(br_ifindex, in_ifindex, sa);
	int now_on = ask_bridge_fdb_port(br_ifindex, sa);

	/* br_fdb_external_learn_del() finds the entry by MAC alone: only touch
	 * it while it is still ours, on this port, and never user-added. A
	 * moved entry keeps our "offload" bit (the bridge never clears it):
	 * clear it unless records use the MAC on its new port. */
	if (now_on == in_ifindex && o && !ask_bridge_user_mac(sa))
		ask_bridge_fdb_call(SWITCHDEV_FDB_DEL_TO_BRIDGE, in_ifindex, sa,
				    false);
	else if (now_on && !ask_bridge_sa_in_use(br_ifindex, now_on, sa))
		ask_bridge_fdb_call(SWITCHDEV_FDB_OFFLOADED, now_on, sa, false);
	if (o) {
		list_del(&o->node);
		kfree(o);
	}
}

/* Remove @r from the hardware (unless its table is already gone) and the
 * list, then release its SA if nothing else uses it. */
static void ask_bridge_rec_del(struct ask_bridge_rec *r)
{
	int rc;

	if (!r->dead) {
		/* A failed delete has already unlinked the record (F-254/F-256
		 * keep only the memory), so tracking goes either way. */
		rc = ask_hw_l2_record(r->port, r->da, r->sa, r->eth_type, 0,
				      false);
		if (rc)
			pr_warn_ratelimited("ask: bridge: delete %pM>%pM/0x%04x on port 0x%02x: %d\n",
					    r->sa, r->da, ntohs(r->eth_type),
					    r->port, rc);
	}
	list_del(&r->node);
	ask_bridge_n_recs--;
	if (!ask_bridge_sa_in_use(r->br_ifindex, r->in_ifindex, r->sa))
		ask_bridge_sa_release(r->br_ifindex, r->in_ifindex, r->sa);
	kfree(r);
}

/* Revalidate every record against the live FDB and port state. */
static void ask_bridge_resync(void)
{
	struct ask_bridge_rec *r, *tmp;
	u32 fqid;
	u8 iport;

	ASSERT_RTNL();
	lockdep_assert_held(&ask_bridge_lock);

	list_for_each_entry_safe(r, tmp, &ask_bridge_recs, node)
		if (r->dead ||
		    !ask_bridge_flow_ok(r->br_ifindex, r->in_ifindex,
					r->out_ifindex, r->da, r->sa,
					r->eth_type, &iport, &fqid) ||
		    iport != r->port || fqid != r->fqid)
			ask_bridge_rec_del(r);
}

static struct ask_bridge_rec *ask_bridge_rec_find(u8 port, const u8 *da,
						  const u8 *sa, __be16 eth_type)
{
	struct ask_bridge_rec *r;

	list_for_each_entry(r, &ask_bridge_recs, node)
		if (!r->dead && r->port == port && r->eth_type == eth_type &&
		    ether_addr_equal(r->da, da) && ether_addr_equal(r->sa, sa))
			return r;
	return NULL;
}

/* Install a record for a candidate the hook saw, if it qualifies now. */
static void ask_bridge_admit(const struct ask_bridge_cand *c)
{
	struct net_device *in = __dev_get_by_index(&init_net, c->in_ifindex);
	struct net_device *br = in && netif_is_bridge_port(in) ?
				netdev_master_upper_dev_get(in) : NULL;
	struct ask_bridge_rec *r;
	struct ask_bridge_own *o;
	u32 fqid;
	u8 iport;
	int rc;

	if (!br || !ask_bridge_flow_ok(br->ifindex, c->in_ifindex,
				       c->out_ifindex, c->da, c->sa,
				       c->eth_type, &iport, &fqid) ||
	    ask_bridge_rec_find(iport, c->da, c->sa, c->eth_type))
		return;
	if (ask_bridge_n_recs >= ASK_BRIDGE_REC_MAX) {
		pr_warn_ratelimited("ask: bridge: %u records, %pM>%pM stays in software\n",
				    ask_bridge_n_recs, c->sa, c->da);
		return;
	}
	r = kzalloc(sizeof(*r), GFP_KERNEL);
	if (!r)
		return;
	rc = ask_hw_l2_record(iport, c->da, c->sa, c->eth_type, fqid, true);
	if (rc) {
		kfree(r);
		pr_warn_ratelimited("ask: bridge: add %pM>%pM/0x%04x on port 0x%02x failed: %d\n",
				    c->sa, c->da, ntohs(c->eth_type), iport, rc);
		return;
	}
	r->br_ifindex = br->ifindex;
	r->in_ifindex = c->in_ifindex;
	r->out_ifindex = c->out_ifindex;
	r->port = iport;
	ether_addr_copy(r->da, c->da);
	ether_addr_copy(r->sa, c->sa);
	r->eth_type = c->eth_type;
	r->fqid = fqid;
	r->last_hit = jiffies;
	list_add_tail(&r->node, &ask_bridge_recs);
	ask_bridge_n_recs++;

	/* The SA's frames no longer reach the bridge: take a learned entry
	 * over so it does not age out, or just mark a user-added one. */
	if (ask_bridge_own_find(br->ifindex, c->in_ifindex, c->sa))
		return;
	if (ask_bridge_user_mac(c->sa)) {
		ask_bridge_fdb_call(SWITCHDEV_FDB_OFFLOADED, c->in_ifindex,
				    c->sa, true);
		return;
	}
	o = kzalloc(sizeof(*o), GFP_KERNEL);
	if (!o)
		return;		/* the bridge ages it; the record goes with it */
	rc = ask_bridge_fdb_call(SWITCHDEV_FDB_ADD_TO_BRIDGE, c->in_ifindex,
				 c->sa, true);
	if (rc) {
		kfree(o);
		return;
	}
	o->br_ifindex = br->ifindex;
	o->port_ifindex = c->in_ifindex;
	ether_addr_copy(o->addr, c->sa);
	list_add_tail(&o->node, &ask_bridge_owns);
}

static void ask_bridge_work_fn(struct work_struct *w)
{
	unsigned int i, n;

	rtnl_lock();
	mutex_lock(&ask_bridge_lock);
	spin_lock_bh(&ask_bridge_cand_lock);
	n = ask_bridge_n_cand;
	memcpy(ask_bridge_cand_work, ask_bridge_cand, n * sizeof(*ask_bridge_cand));
	ask_bridge_n_cand = 0;
	spin_unlock_bh(&ask_bridge_cand_lock);

	ask_bridge_resync();
	for (i = 0; i < n; i++)
		ask_bridge_admit(&ask_bridge_cand_work[i]);
	if (ask_bridge_n_recs)	/* no-op while a pass is pending */
		queue_delayed_work(system_wq, &ask_bridge_age_work,
				   ASK_BRIDGE_AGE_PERIOD);
	mutex_unlock(&ask_bridge_lock);
	rtnl_unlock();
}

/* Delete records idle for their bridge's ageing time (0 = never).
 * ponytail: reads every record's count under rtnl, O(R x records per port)
 * memcmp (~tens of ms at the 2048 cap) every 5 s; read counts outside rtnl
 * or cache the record pointer if rtnl latency matters. */
static void ask_bridge_age_fn(struct work_struct *w)
{
	struct ask_bridge_rec *r, *tmp;
	unsigned long now = jiffies;
	u64 pkts;

	rtnl_lock();
	mutex_lock(&ask_bridge_lock);
	list_for_each_entry_safe(r, tmp, &ask_bridge_recs, node) {
		struct net_device *br = __dev_get_by_index(&init_net,
							   r->br_ifindex);
		unsigned long age = br ?
			clock_t_to_jiffies(br_get_ageing_time(br)) : 0;

		if (!r->dead && !ask_hw_l2_record_hits(r->port, r->da, r->sa,
							r->eth_type, &pkts) &&
		    pkts != r->pkts) {
			r->pkts = pkts;
			r->last_hit = now;
		} else if (age && time_after_eq(now, r->last_hit + age)) {
			ask_bridge_rec_del(r);
		}
	}
	ask_bridge_resync();
	if (ask_bridge_n_recs)	/* no-op while a pass is pending */
		queue_delayed_work(system_wq, &ask_bridge_age_work,
				   ASK_BRIDGE_AGE_PERIOD);
	mutex_unlock(&ask_bridge_lock);
	rtnl_unlock();
}

/* @hw_port_id was disengaged: its table and records are gone. The worker
 * releases the SAs (needs rtnl); dead records never match a lookup, so a
 * quick re-engage installs afresh. */
void ask_bridge_forget_port(u8 hw_port_id)
{
	struct ask_bridge_rec *r;

	mutex_lock(&ask_bridge_lock);
	list_for_each_entry(r, &ask_bridge_recs, node)
		if (r->port == hw_port_id)
			r->dead = true;
	mutex_unlock(&ask_bridge_lock);
	ask_bridge_kick();
}

/*
 * NF_BR_FORWARD, after the bridge filter: note untagged IPv4/IPv6 unicast
 * between two L2 ports. Softirq; never blocks, drops candidates when full.
 */
static unsigned int ask_bridge_hook(void *priv, struct sk_buff *skb,
				    const struct nf_hook_state *state)
{
	const struct ethhdr *eth = eth_hdr(skb);
	struct ask_bridge_cand *c;
	unsigned long now = jiffies;
	unsigned int slot;
	u32 h;
	u8 port;

	if (!ask_hw_bridge_offload_armed() || skb_vlan_tag_present(skb) ||
	    (eth->h_proto != htons(ETH_P_IP) &&
	     eth->h_proto != htons(ETH_P_IPV6)) ||
	    !is_valid_ether_addr(eth->h_dest) ||
	    !is_valid_ether_addr(eth->h_source) ||
	    ask_hw_dev_port(state->in, &port) || !ask_hw_port_is_l2(port) ||
	    ask_hw_dev_port(state->out, &port) || !ask_hw_port_is_l2(port))
		return NF_ACCEPT;

	/* h_dest and h_source are adjacent: hash both MACs at once. */
	h = jhash(eth->h_dest, 2 * ETH_ALEN,
		  (u32)state->in->ifindex << 16 ^ state->out->ifindex ^
		  (__force u32)eth->h_proto);
	slot = h & (ASK_BRIDGE_SEEN - 1);
	if (READ_ONCE(ask_bridge_seen[slot].hash) == h &&
	    time_before(now, READ_ONCE(ask_bridge_seen[slot].at) + HZ))
		return NF_ACCEPT;
	WRITE_ONCE(ask_bridge_seen[slot].hash, h);
	WRITE_ONCE(ask_bridge_seen[slot].at, now);

	spin_lock(&ask_bridge_cand_lock);
	if (ask_bridge_n_cand < ASK_BRIDGE_CAND_MAX) {
		c = &ask_bridge_cand[ask_bridge_n_cand++];
		c->in_ifindex = state->in->ifindex;
		c->out_ifindex = state->out->ifindex;
		ether_addr_copy(c->da, eth->h_dest);
		ether_addr_copy(c->sa, eth->h_source);
		c->eth_type = eth->h_proto;
	}
	spin_unlock(&ask_bridge_cand_lock);
	ask_bridge_kick();
	return NF_ACCEPT;
}

static const struct nf_hook_ops ask_bridge_nf_ops = {
	.hook     = ask_bridge_hook,
	.pf       = NFPROTO_BRIDGE,
	.hooknum  = NF_BR_FORWARD,
	.priority = NF_BR_PRI_LAST,
};

/* FDB changes: remember user-added and local MACs (never taken over), then
 * let the worker revalidate. Atomic context. */
static int ask_bridge_fdb_notifier(struct notifier_block *nb,
				   unsigned long event, void *ptr)
{
	struct switchdev_notifier_fdb_info *fdb_info;

	if (event != SWITCHDEV_FDB_ADD_TO_DEVICE &&
	    event != SWITCHDEV_FDB_DEL_TO_DEVICE)
		return NOTIFY_DONE;
	fdb_info = container_of(ptr, struct switchdev_notifier_fdb_info, info);
	if (fdb_info->added_by_user || fdb_info->is_local)
		set_bit(jhash(fdb_info->addr, ETH_ALEN, 0) & 4095,
			ask_bridge_user_macs);
	ask_bridge_kick();
	return NOTIFY_DONE;
}

static struct notifier_block ask_bridge_fdb_nb = {
	.notifier_call = ask_bridge_fdb_notifier,
};

/*
 * Blocking chain: STP state, port flags and VLAN filtering change which
 * records are valid. Port flags only reach us as PRE_BRIDGE_FLAGS (no
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
 * records are valid.
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

	INIT_WORK(&ask_bridge_work, ask_bridge_work_fn);
	INIT_DELAYED_WORK(&ask_bridge_age_work, ask_bridge_age_fn);
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

	rc = nf_register_net_hook(&init_net, &ask_bridge_nf_ops);
	if (rc) {
		ask_pr_err("bridge: nf_register_net_hook failed: %d\n", rc);
		goto err_hook;
	}
	ask_bridge_hook_registered = true;

	ask_pr_info("bridge: flow offload ready (T-M6-2 B6, bridge_offload=%d)\n",
		    ask_hw_bridge_offload_armed());
	return 0;

err_hook:
	unregister_netdevice_notifier(&ask_bridge_netdev_nb);
	ask_bridge_netdev_registered = false;
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
	struct ask_bridge_rec *r, *tmp;

	if (ask_bridge_hook_registered) {
		nf_unregister_net_hook(&init_net, &ask_bridge_nf_ops);
		ask_bridge_hook_registered = false;
	}
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

	/* Nothing can queue new work now; flush what's in flight. */
	WRITE_ONCE(ask_bridge_ready, false);
	cancel_work_sync(&ask_bridge_work);
	cancel_delayed_work_sync(&ask_bridge_age_work);

	/* Hand every taken-over entry back; records go with the port tables
	 * when ask_hw tears the ports down. */
	rtnl_lock();
	mutex_lock(&ask_bridge_lock);
	list_for_each_entry_safe(r, tmp, &ask_bridge_recs, node) {
		r->dead = true;
		ask_bridge_rec_del(r);
	}
	ask_bridge_n_cand = 0;
	mutex_unlock(&ask_bridge_lock);
	rtnl_unlock();

	ask_pr_dbg("bridge: exit\n");
}
