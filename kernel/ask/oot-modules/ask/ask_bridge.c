// SPDX-License-Identifier: GPL-2.0
/*
 * ASK2 - bridge subsystem: forwarded bridge flows -> FMan L2 records
 * (T-M6-2 B6/B7a, plans/ASK2-BRIDGE-OFFLOAD-PLAN.md §13.30, §13.32).
 *
 * A record is one flow: (ingress port, DA, SA, TCI, EtherType) -> the egress
 * port's no-confirm TX FQ, in the ingress port's L2_DA table (F-255 16-byte
 * key; TCI 0 = untagged). A bridge NF_BR_FORWARD hook, after the bridge
 * filter like the vendor's auto_bridge, notes IPv4/IPv6 unicast frames
 * between two L2 ports. The worker (rtnl) installs a record when the live
 * FDB puts DA behind the egress port and SA behind the ingress port in the
 * frame's VLAN, so flooded frames never qualify. Only IPv4/IPv6: a short
 * non-IP frame hitting a record wedges the FE hit path (§13.16), and ARP etc.
 * belong to the kernel (D5). A hit forwards the frame unchanged when its
 * ingress and egress tagging agree (B7a); on a VLAN-filtering bridge an
 * access <-> trunk pair pops or pushes the 802.1Q tag in hardware (B7b,
 * F-268: 04 11 12 [42] 41 01 with the frame's own DA/SA, no TTL change,
 * under the ask.vlan_offload kill switch).
 *
 * VLANs: the hook sees the egress VLAN state (__br_forward() runs
 * br_handle_vlan() first), so the ingress form is inferred. A tagged egress
 * frame whose VID is the ingress port's PVID (PCP 0) is taken as untagged
 * ingress (a push), any other as the same tag on ingress. An untagged
 * egress frame is tried as untagged ingress in the ingress PVID and as
 * tagged ingress in the egress port's PVID (a pop); the live FDB in each
 * VLAN keeps only the true one. The action follows from the record's
 * ingress tag and the egress port's untagged flag for the VLAN. A wrong
 * guess cannot misforward: the record is validated against what the bridge
 * does with frames carrying exactly its key, so at worst it never hits and
 * ages out. A pop record's key has PCP 0: prioritized frames stay in
 * software.
 *
 * The CPU no longer sees a hardware-forwarded source, so a learned SA is
 * taken over as externally learned: the bridge does not age those and takes
 * them back by itself when the MAC moves (br_fdb_update()). ask.ko deletes
 * records idle for the bridge's ageing time (hardware hit counts), and an SA
 * whose last record goes is handed back by deleting its entry. User-added
 * and local entries are never taken over: br_fdb_external_learn_del() would
 * delete them.
 *
 * The live FDB, VLAN and port state are the authority. Every FDB event,
 * port or VLAN change, engage or kill-switch change revalidates all records
 * and deletes the stale ones; traffic re-creates records.
 *
 * A pair (in, out) qualifies when both are DPAA ports of the same bridge,
 * engaged as L2_DA, FORWARDING, neither isolated nor locked; out's TX FQ is
 * already cached (never allocated under rtnl, CR-012); in's `offload
 * ipv4|ipv6` mask allows the EtherType; and a tagged record's VID has no
 * VLAN device on in (frames for a vif never reach the bridge).
 *
 * Not covered: frames the bridge filter would drop later in the same flow
 * (the record bypasses it, as on the vendor), 802.1ad, stacked tags.
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
#include <linux/unaligned.h>
#include <net/switchdev.h>
#include <linux/fsl/fman_pcd.h>
#include <uapi/linux/ask/ask.h>	/* ASK_FAM_V4/V6 */

#include "include/ask_internal.h"

/* F-255 L2 key layout (MSB-first EKFC extraction). */
#define ASK_BRIDGE_K_DA		0
#define ASK_BRIDGE_K_SA		ETH_ALEN
#define ASK_BRIDGE_K_TCI	(2 * ETH_ALEN)
#define ASK_BRIDGE_K_ET		(2 * ETH_ALEN + 2)

/* One installed record in ingress port @port's L2_DA table. */
struct ask_bridge_rec {
	struct list_head node;
	int              br_ifindex;
	int              in_ifindex;
	int              out_ifindex;
	u8               port;		/* ingress hw port: the record's table */
	u8               key[FMAN_PCD_FE_L2_DA_KEY_SIZE];
	u16              vid;		/* bridge VLAN (0: not filtering) */
	bool             filtering;	/* learned on a VLAN-filtering bridge */
	u8               act;		/* enum ask_bridge_act */
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
	u16              vid;
};

/* A bridged frame the hook saw; admitted by the worker. */
struct ask_bridge_cand {
	int              in_ifindex;
	int              out_ifindex;
	u8               key[FMAN_PCD_FE_L2_DA_KEY_SIZE];
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
 * MACs ever seen as user-added or local FDB entries, hashed, never cleared:
 * a set bit only means "not taken over", so a collision is safe and nothing
 * can be lost (no allocation in the atomic notifier).
 * ponytail: sticky 4096-bit filter; a churn of many static MACs slowly
 * stops takeovers for colliding learned MACs (those then age in the bridge).
 */
static DECLARE_BITMAP(ask_bridge_user_macs, 4096);

static bool ask_bridge_notifiers_registered;
static bool ask_bridge_blocking_registered;
static bool ask_bridge_netdev_registered;
static bool ask_bridge_hook_registered;

/* F-255 L2 key: DA(6) | SA(6) | TCI(2) | EtherType(2), MSB-first (plan
 * §13.32). @tci is the frame's first VLAN TCI, 0 when untagged. */
void ask_bridge_key(u8 *key, const u8 *da, const u8 *sa, u16 tci,
		    __be16 eth_type)
{
	BUILD_BUG_ON(FMAN_PCD_FE_L2_DA_KEY_SIZE != ASK_BRIDGE_K_ET + 2);
	ether_addr_copy(key + ASK_BRIDGE_K_DA, da);
	ether_addr_copy(key + ASK_BRIDGE_K_SA, sa);
	put_unaligned_be16(tci, key + ASK_BRIDGE_K_TCI);
	memcpy(key + ASK_BRIDGE_K_ET, &eth_type, sizeof(eth_type));
}
#ifdef ASK_KUNIT_EXPORTS
EXPORT_SYMBOL_GPL(ask_bridge_key);
#endif

/*
 * One bridge flow -> one FE record on the ingress port's L2_DA table.
 * Folding EtherType into the key means a record installed for one EtherType
 * cannot be hit by a frame of another EtherType between the same MACs:
 * ARP/other non-IP frames miss to the kernel FQ by construction instead of
 * wedging the FE-VM hit path (plan §13.16). Only unicast, non-zero MACs:
 * BUM keeps missing to the bridge.
 *
 * ASK_BRIDGE_ACT_SAME (B7a): HIT = plain ENQUEUE_PKT to @egress_tx_fqid;
 * rx_fqid carries it, tx_fqid stays 0 so no L2 rewrite is emitted.
 * ASK_BRIDGE_ACT_POP/PUSH (B7b): the L2/TX terminal with the frame's own
 * DA/SA and FMAN_PCD_VLANF_BRIDGE (F-268: no TTL change, mtu 0xffff). A pop
 * needs a tagged key in bridge VLAN @vid; a push an untagged key, and tags
 * the frame with @vid, PCP 0 (as br_allowed_ingress() does). IPv4/IPv6 only.
 */
int ask_bridge_fe_action(const u8 *key, u32 egress_tx_fqid,
			 unsigned long enq_off, u8 act, u16 vid,
			 struct fman_pcd_fe_flow_action *a)
{
	u16 tci, et;

	if (!key || !a || !is_valid_ether_addr(key + ASK_BRIDGE_K_DA) ||
	    !is_valid_ether_addr(key + ASK_BRIDGE_K_SA) || !egress_tx_fqid ||
	    egress_tx_fqid > 0xffffff || !enq_off)
		return -EINVAL;
	tci = get_unaligned_be16(key + ASK_BRIDGE_K_TCI);
	et = get_unaligned_be16(key + ASK_BRIDGE_K_ET);
	switch (act) {
	case ASK_BRIDGE_ACT_SAME:
		break;
	case ASK_BRIDGE_ACT_POP:
		if (!tci || (tci & VLAN_VID_MASK) != vid)
			return -EINVAL;
		fallthrough;
	case ASK_BRIDGE_ACT_PUSH:
		if ((act == ASK_BRIDGE_ACT_PUSH && tci) || !vid ||
		    vid >= VLAN_VID_MASK ||
		    (et != ETH_P_IP && et != ETH_P_IPV6))
			return -EINVAL;
		break;
	default:
		return -EINVAL;
	}
	memset(a, 0, sizeof(*a));
	memcpy(a->key, key, FMAN_PCD_FE_L2_DA_KEY_SIZE);
	a->key_size  = FMAN_PCD_FE_L2_DA_KEY_SIZE;
	a->enq_off   = enq_off;
	a->table_idx = 0;		/* the ingress port's own table */
	if (act == ASK_BRIDGE_ACT_SAME) {
		a->rx_fqid = egress_tx_fqid;
		return 0;
	}
	a->tx_fqid = egress_tx_fqid;
	ether_addr_copy(a->next_hop_mac, key + ASK_BRIDGE_K_DA);
	ether_addr_copy(a->egress_mac, key + ASK_BRIDGE_K_SA);
	a->eth_type = et;
	a->vlan_flags = FMAN_PCD_VLANF_BRIDGE;
	if (act == ASK_BRIDGE_ACT_POP) {
		a->vlan_flags |= FMAN_PCD_VLANF_POP;
		a->vlan_ingress_vid = vid;
	} else {
		a->vlan_flags |= FMAN_PCD_VLANF_PUSH;
		a->vlan_push_tci = vid;
		a->vlan_push_tpid = ETH_P_8021Q;
	}
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
 * The bridge VLAN a frame with @tci arriving on @in belongs to: its VID,
 * or @in's PVID when untagged; false if unsupported (802.1ad, priority
 * tag, no PVID). rtnl held.
 */
static bool ask_bridge_vid(struct net_device *br, struct net_device *in,
			   u16 tci, u16 *vid)
{
	u16 pvid = 0, proto;

	if (!br_vlan_enabled(br)) {
		*vid = 0;
		return true;
	}
	if (br_vlan_get_proto(br, &proto) || proto != ETH_P_8021Q)
		return false;
	br_vlan_get_pvid(in, &pvid);
	*vid = tci ? tci & VLAN_VID_MASK : pvid;
	return *vid;
}

/* On a VLAN-filtering bridge: is @in still a member of VLAN @vid (the PVID
 * already is for an untagged record) and @out too? @out_tagged: @out sends
 * @vid tagged. */
static bool ask_bridge_vlan_ok(struct net_device *in, struct net_device *out,
			       u16 vid, u16 tci, bool *out_tagged)
{
	struct bridge_vlan_info info;

	if ((tci && br_vlan_get_info(in, vid, &info)) ||
	    br_vlan_get_info(out, vid, &info))
		return false;
	*out_tagged = !(info.flags & BRIDGE_VLAN_INFO_UNTAGGED);
	return true;
}

/*
 * Would a record with @key (in -> out, bridge VLAN @vid) be correct now?
 * Fills the ingress port, the egress FQ and the action (what the bridge
 * does to the tag). rtnl held.
 */
static bool ask_bridge_flow_ok(int br_ifindex, int in_ifindex, int out_ifindex,
			       const u8 *key, u16 vid, bool filtering,
			       u8 *iport, u32 *fqid, u8 *act)
{
	struct net_device *br = __dev_get_by_index(&init_net, br_ifindex);
	struct net_device *in = __dev_get_by_index(&init_net, in_ifindex);
	struct net_device *out = __dev_get_by_index(&init_net, out_ifindex);
	u16 tci = get_unaligned_be16(key + ASK_BRIDGE_K_TCI), v;
	u8 fam = !memcmp(key + ASK_BRIDGE_K_ET, "\x08\x00", 2) ?
		 ASK_FAM_V4 : ASK_FAM_V6;
	bool out_tagged = tci;
	bool vif = false;
	u8 eport;

	if (!ask_hw_bridge_offload_armed() ||
	    !br || !in || !out || in == out || !netif_is_bridge_master(br) ||
	    br_vlan_enabled(br) != filtering ||
	    !ask_bridge_member_ok(in, br, iport) ||
	    !ask_bridge_member_ok(out, br, &eport) ||
	    !(ask_hw_port_family_mask(*iport) & fam) ||
	    ask_hw_oif_fqid_cached(out_ifindex, fqid) ||
	    !ask_bridge_vid(br, in, tci, &v) || v != vid ||
	    (filtering && !ask_bridge_vlan_ok(in, out, vid, tci, &out_tagged)))
		return false;
	*act = out_tagged == !!tci ? ASK_BRIDGE_ACT_SAME :
	       tci ? ASK_BRIDGE_ACT_POP : ASK_BRIDGE_ACT_PUSH;
	if (*act != ASK_BRIDGE_ACT_SAME &&
	    !ask_hw_vlan_offload_armed_port(*iport))
		return false;
	if (tci) {
		rcu_read_lock();
		vif = __vlan_find_dev_deep_rcu(in, htons(ETH_P_8021Q),
					       tci & VLAN_VID_MASK);
		rcu_read_unlock();
	}
	return !vif &&
	       br_fdb_find_port(br, key + ASK_BRIDGE_K_DA, vid) == out &&
	       br_fdb_find_port(br, key + ASK_BRIDGE_K_SA, vid) == in;
}

static struct ask_bridge_own *ask_bridge_own_find(int br_ifindex,
						  int port_ifindex,
						  const u8 *addr, u16 vid)
{
	struct ask_bridge_own *o;

	list_for_each_entry(o, &ask_bridge_owns, node)
		if (o->br_ifindex == br_ifindex &&
		    o->port_ifindex == port_ifindex && o->vid == vid &&
		    ether_addr_equal(o->addr, addr))
			return o;
	return NULL;
}

static bool ask_bridge_sa_in_use(int br_ifindex, int in_ifindex, const u8 *sa,
				 u16 vid)
{
	struct ask_bridge_rec *r;

	list_for_each_entry(r, &ask_bridge_recs, node)
		if (!r->dead && r->br_ifindex == br_ifindex &&
		    r->in_ifindex == in_ifindex && r->vid == vid &&
		    ether_addr_equal(r->key + ASK_BRIDGE_K_SA, sa))
			return true;
	return false;
}

/* Send one FDB notification for (@addr, @vid) on @port_ifindex to the
 * bridge (rtnl held). */
static int ask_bridge_fdb_call(unsigned long event, int port_ifindex,
			       const u8 *addr, u16 vid, bool offloaded)
{
	struct net_device *dev = __dev_get_by_index(&init_net, port_ifindex);
	struct switchdev_notifier_fdb_info info = {
		.addr = addr,
		.vid = vid,
		.offloaded = offloaded,
	};

	if (!dev)
		return -ENODEV;
	return notifier_to_errno(call_switchdev_notifiers(event, dev,
							  &info.info, NULL));
}

/* The port (@addr, @vid) is behind in @br_ifindex's FDB, or 0. rtnl held. */
static int ask_bridge_fdb_port(int br_ifindex, const u8 *addr, u16 vid)
{
	struct net_device *br = __dev_get_by_index(&init_net, br_ifindex);
	struct net_device *port;

	if (!br || !netif_is_bridge_master(br))
		return 0;
	port = br_fdb_find_port(br, addr, vid);
	return port ? port->ifindex : 0;
}

/*
 * @sa on @in_ifindex lost its last record: a taken-over entry is handed
 * back by deleting it (the bridge relearns the host from its next frame);
 * any other entry just loses its "offload" mark.
 */
static void ask_bridge_sa_release(int br_ifindex, int in_ifindex, const u8 *sa,
				  u16 vid)
{
	struct ask_bridge_own *o = ask_bridge_own_find(br_ifindex, in_ifindex,
						       sa, vid);
	int now_on = ask_bridge_fdb_port(br_ifindex, sa, vid);

	/* br_fdb_external_learn_del() finds the entry by (MAC, VID) alone:
	 * only touch it while it is still ours, on this port, and never
	 * user-added. A moved entry keeps our "offload" bit (the bridge never
	 * clears it): clear it unless records use the MAC on its new port. */
	if (now_on == in_ifindex && o && !ask_bridge_user_mac(sa))
		ask_bridge_fdb_call(SWITCHDEV_FDB_DEL_TO_BRIDGE, in_ifindex, sa,
				    vid, false);
	else if (now_on && !ask_bridge_sa_in_use(br_ifindex, now_on, sa, vid))
		ask_bridge_fdb_call(SWITCHDEV_FDB_OFFLOADED, now_on, sa, vid,
				    false);
	if (o) {
		list_del(&o->node);
		kfree(o);
	}
}

/* Remove @r from the hardware (unless its table is already gone) and the
 * list, then release its SA if nothing else uses it. */
static void ask_bridge_rec_del(struct ask_bridge_rec *r)
{
	const u8 *sa = r->key + ASK_BRIDGE_K_SA;
	int rc;

	if (!r->dead) {
		/* A failed delete has already unlinked the record (F-254/F-256
		 * keep only the memory), so tracking goes either way. */
		rc = ask_hw_l2_record(r->port, r->key, 0, r->act, r->vid,
				      false);
		if (rc)
			pr_warn_ratelimited("ask: bridge: delete %pM>%pM tci 0x%04x on port 0x%02x: %d\n",
					    sa, r->key + ASK_BRIDGE_K_DA,
					    get_unaligned_be16(r->key + ASK_BRIDGE_K_TCI),
					    r->port, rc);
	}
	list_del(&r->node);
	ask_bridge_n_recs--;
	if (!ask_bridge_sa_in_use(r->br_ifindex, r->in_ifindex, sa, r->vid))
		ask_bridge_sa_release(r->br_ifindex, r->in_ifindex, sa, r->vid);
	kfree(r);
}

/* Revalidate every record against the live FDB, VLAN and port state. */
static void ask_bridge_resync(void)
{
	struct ask_bridge_rec *r, *tmp;
	u32 fqid;
	u8 iport, act;

	ASSERT_RTNL();
	lockdep_assert_held(&ask_bridge_lock);

	list_for_each_entry_safe(r, tmp, &ask_bridge_recs, node)
		if (r->dead ||
		    !ask_bridge_flow_ok(r->br_ifindex, r->in_ifindex,
					r->out_ifindex, r->key, r->vid,
					r->filtering, &iport, &fqid, &act) ||
		    iport != r->port || fqid != r->fqid || act != r->act)
			ask_bridge_rec_del(r);
}

static struct ask_bridge_rec *ask_bridge_rec_find(u8 port, const u8 *key)
{
	struct ask_bridge_rec *r;

	list_for_each_entry(r, &ask_bridge_recs, node)
		if (!r->dead && r->port == port &&
		    !memcmp(r->key, key, FMAN_PCD_FE_L2_DA_KEY_SIZE))
			return r;
	return NULL;
}

/* Install a record with ingress-form @key (in -> out via @br), if it
 * qualifies now. */
static void ask_bridge_admit_key(struct net_device *br, struct net_device *in,
				 int out_ifindex, const u8 *key)
{
	const u8 *sa = key + ASK_BRIDGE_K_SA;
	struct ask_bridge_rec *r;
	struct ask_bridge_own *o;
	bool filtering;
	u32 fqid;
	u16 vid;
	u8 iport, act;
	int rc;

	if (!ask_bridge_vid(br, in, get_unaligned_be16(key + ASK_BRIDGE_K_TCI),
			    &vid))
		return;
	filtering = br_vlan_enabled(br);
	if (!ask_bridge_flow_ok(br->ifindex, in->ifindex, out_ifindex, key,
				vid, filtering, &iport, &fqid, &act) ||
	    ask_bridge_rec_find(iport, key))
		return;
	if (ask_bridge_n_recs >= ASK_BRIDGE_REC_MAX) {
		pr_warn_ratelimited("ask: bridge: %u records, %pM>%pM stays in software\n",
				    ask_bridge_n_recs, sa, key + ASK_BRIDGE_K_DA);
		return;
	}
	r = kzalloc(sizeof(*r), GFP_KERNEL);
	if (!r)
		return;
	rc = ask_hw_l2_record(iport, key, fqid, act, vid, true);
	if (rc) {
		kfree(r);
		pr_warn_ratelimited("ask: bridge: add %pM>%pM tci 0x%04x act %u on port 0x%02x failed: %d\n",
				    sa, key + ASK_BRIDGE_K_DA,
				    get_unaligned_be16(key + ASK_BRIDGE_K_TCI),
				    act, iport, rc);
		return;
	}
	r->br_ifindex = br->ifindex;
	r->in_ifindex = in->ifindex;
	r->out_ifindex = out_ifindex;
	r->port = iport;
	memcpy(r->key, key, sizeof(r->key));
	r->vid = vid;
	r->filtering = filtering;
	r->act = act;
	r->fqid = fqid;
	r->last_hit = jiffies;
	list_add_tail(&r->node, &ask_bridge_recs);
	ask_bridge_n_recs++;

	/* The SA's frames no longer reach the bridge: take a learned entry
	 * over so it does not age out, or just mark a user-added one. */
	if (ask_bridge_own_find(br->ifindex, in->ifindex, sa, vid))
		return;
	if (ask_bridge_user_mac(sa)) {
		ask_bridge_fdb_call(SWITCHDEV_FDB_OFFLOADED, in->ifindex, sa,
				    vid, true);
		return;
	}
	o = kzalloc(sizeof(*o), GFP_KERNEL);
	if (!o)
		return;		/* the bridge ages it; the record goes with it */
	rc = ask_bridge_fdb_call(SWITCHDEV_FDB_ADD_TO_BRIDGE, in->ifindex, sa,
				 vid, true);
	if (rc) {
		kfree(o);
		return;
	}
	o->br_ifindex = br->ifindex;
	o->port_ifindex = in->ifindex;
	ether_addr_copy(o->addr, sa);
	o->vid = vid;
	list_add_tail(&o->node, &ask_bridge_owns);
}

/*
 * A candidate carries the egress tag; install the ingress form(s) it can
 * come from (header comment). The FDB check in each VLAN rejects a wrong
 * guess, and a record that is still wrong just never hits.
 */
static void ask_bridge_admit(const struct ask_bridge_cand *c)
{
	struct net_device *in = __dev_get_by_index(&init_net, c->in_ifindex);
	struct net_device *br = in && netif_is_bridge_port(in) ?
				netdev_master_upper_dev_get(in) : NULL;
	struct net_device *out;
	u16 etci = get_unaligned_be16(c->key + ASK_BRIDGE_K_TCI);
	u16 pin = 0, pout = 0;
	u8 key[FMAN_PCD_FE_L2_DA_KEY_SIZE];

	if (!br || !netif_is_bridge_master(br))
		return;
	if (br_vlan_enabled(br)) {
		out = __dev_get_by_index(&init_net, c->out_ifindex);
		br_vlan_get_pvid(in, &pin);
		if (out)
			br_vlan_get_pvid(out, &pout);
	}
	memcpy(key, c->key, sizeof(key));
	if (etci && etci == pin) {
		/* untagged on in, tagged by the bridge with PVID, PCP 0 */
		put_unaligned_be16(0, key + ASK_BRIDGE_K_TCI);
		ask_bridge_admit_key(br, in, c->out_ifindex, key);
		return;
	}
	ask_bridge_admit_key(br, in, c->out_ifindex, key);
	if (!etci && pout && pout != pin) {
		/* tagged with out's PVID on in, untagged by the bridge */
		put_unaligned_be16(pout, key + ASK_BRIDGE_K_TCI);
		ask_bridge_admit_key(br, in, c->out_ifindex, key);
	}
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

		if (!r->dead && !ask_hw_l2_record_hits(r->port, r->key, &pkts) &&
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
 * NF_BR_FORWARD, after the bridge filter: note IPv4/IPv6 unicast between
 * two L2 ports, with its egress 802.1Q tag (hwaccel; in-band tagged frames
 * carry 0x8100 as EtherType and are skipped). Softirq; never blocks, drops
 * candidates when full.
 */
static unsigned int ask_bridge_hook(void *priv, struct sk_buff *skb,
				    const struct nf_hook_state *state)
{
	const struct ethhdr *eth = eth_hdr(skb);
	struct ask_bridge_cand *c;
	unsigned long now = jiffies;
	unsigned int slot;
	u16 tci = 0;
	u32 h;
	u8 port;

	if (skb_vlan_tag_present(skb)) {
		if (skb->vlan_proto != htons(ETH_P_8021Q))
			return NF_ACCEPT;
		tci = skb_vlan_tag_get(skb);
	}
	if (!ask_hw_bridge_offload_armed() ||
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
		  (u32)tci << 8 ^ (__force u32)eth->h_proto);
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
		ask_bridge_key(c->key, eth->h_dest, eth->h_source, tci,
			       eth->h_proto);
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
 * Blocking chain: STP state, port flags, VLAN filtering/protocol and port
 * VLAN membership/PVID change which records are valid. Port flags only
 * reach us as PRE_BRIDGE_FLAGS (no driver claims the attribute, so the
 * bridge never sends BRIDGE_FLAGS); the flags are applied before the worker
 * gets rtnl.
 */
static int ask_bridge_blocking_notifier(struct notifier_block *nb,
					unsigned long event, void *ptr)
{
	struct switchdev_notifier_port_attr_info *attr = ptr;
	struct switchdev_notifier_port_obj_info *obj = ptr;

	switch (event) {
	case SWITCHDEV_PORT_ATTR_SET:
		switch (attr->attr->id) {
		case SWITCHDEV_ATTR_ID_PORT_STP_STATE:
		case SWITCHDEV_ATTR_ID_PORT_PRE_BRIDGE_FLAGS:
		case SWITCHDEV_ATTR_ID_PORT_BRIDGE_FLAGS:
		case SWITCHDEV_ATTR_ID_BRIDGE_VLAN_FILTERING:
		case SWITCHDEV_ATTR_ID_BRIDGE_VLAN_PROTOCOL:
			ask_bridge_kick();
			break;
		default:
			break;
		}
		break;
	case SWITCHDEV_PORT_OBJ_ADD:
	case SWITCHDEV_PORT_OBJ_DEL:
		if (obj->obj->id == SWITCHDEV_OBJ_ID_PORT_VLAN)
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

	ask_pr_info("bridge: flow offload ready (T-M6-2 B7a, bridge_offload=%d)\n",
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
