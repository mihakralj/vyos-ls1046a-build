// SPDX-License-Identifier: GPL-2.0
#include <linux/delay.h>
#include <linux/errno.h>
#include <linux/if_ether.h>
#include <linux/if_vlan.h>
#include <linux/in.h>
#include <linux/module.h>
#include <linux/mutex.h>
#include <linux/net_namespace.h>
#include <linux/netdevice.h>
#include <linux/slab.h>
#include <linux/string.h>
#include <linux/types.h>

#include "ask_internal.h"
#include "ask_fman_caps.h"

struct ask_vlan_cc_port {
	struct fman_cc_key keys[FMAN_CC_MAX_STATIC_KEYS];
	u32 hm_handles[FMAN_CC_MAX_STATIC_KEYS];
	u16 nkeys;
	bool installed;
	/*
	 * Port aggregate stats: the CC-tree path has no per-key counters on
	 * this silicon, so dump-flows reports the egress-port HW-forwarded
	 * delta instead.  Snapshot the PHYSICAL port's dev stats (the vif's
	 * own stats only count SW-stack traffic) at the moment the port's
	 * first VLAN flow installs; clear when the last flow leaves.
	 */
	u64 agg_pkts_snap;
	u64 agg_bytes_snap;
	int phys_ifindex;
};

static struct ask_vlan_cc_port ports[64];
static DEFINE_MUTEX(ask_vlan_cc_lock);

static void ask_vlan_cc_fill_key(struct fman_cc_key *cc_key,
				 const struct ask_flow_key *key,
				 u32 tx_fqid, u32 hm_handle)
{
	__be32 src_ip;
	__be32 dst_ip;

	memcpy(&src_ip, key->src_ip, sizeof(src_ip));
	memcpy(&dst_ip, key->dst_ip, sizeof(dst_ip));

	memset(cc_key, 0, sizeof(*cc_key));
	cc_key->ethertype = FMAN_CC_ETHERTYPE_IPV4;
	cc_key->proto = key->l4_proto;
	cc_key->src_ip = ntohl(src_ip);
	cc_key->dst_ip = ntohl(dst_ip);
	cc_key->src_ip_mask = 0xffffffff;
	cc_key->dst_ip_mask = 0xffffffff;
	cc_key->src_port = ntohs(key->sport);
	cc_key->dst_port = ntohs(key->dport);
	cc_key->target_fqid = tx_fqid;
	cc_key->hm_handle = hm_handle;
}

static bool ask_vlan_cc_key_match(const struct fman_cc_key *a,
				  const struct fman_cc_key *b)
{
	return a->proto == b->proto &&
	       a->src_ip == b->src_ip &&
	       a->dst_ip == b->dst_ip &&
	       a->src_port == b->src_port &&
	       a->dst_port == b->dst_port;
}

static int ask_vlan_cc_rebuild_locked(struct fman *fm, u8 port_id,
				      struct ask_vlan_cc_port *port)
{
	struct fman_cc_static_tree *tree;
	unsigned long miss_fe_off;
	int rc;

	miss_fe_off = fman_pcd_fe_root_get_offset(fm);
	if (!miss_fe_off)
		return -EAGAIN;

	tree = kzalloc(sizeof(*tree), GFP_KERNEL);
	if (!tree)
		return -ENOMEM;

	tree->num_keys = port->nkeys;
	tree->miss_fe_off = (u32)miss_fe_off;
	memcpy(tree->keys, port->keys,
	       sizeof(*tree->keys) * port->nkeys);

	rc = fman_cc_tree_install(fm, port_id, tree);
	kfree(tree);
	if (!rc)
		port->installed = true;

	return rc;
}

int ask_vlan_cc_flow_add(const struct ask_flow_key *key, u32 tx_fqid,
			 struct net_device *egress_dev)
{
	struct ask_vlan_cc_port *port;
	struct rtnl_link_stats64 st;
	struct net_device *phys_dev;
	struct fman_cc_key cc_key;
	struct fman_cc_key old_key;
	struct fman *fm;
	u32 hm_handle;
	u32 old_handle;
	u16 tci;
	u16 vid;
	u16 tpid;
	u16 i;
	u8 port_id;
	u8 pcp;
	bool do_pop;
	bool do_push;
	int rc;

	if (!key)
		return -EINVAL;
	/* Per-port gate: armed on this flow's ingress port (key->port_id). */
	if (!ask_hw_vlan_offload_armed_port(key->port_id))
		return -EOPNOTSUPP;
	if (key->l3_proto != ASK_FLOW_L3_IPV4)
		return -EOPNOTSUPP;

	fm = ask_hw_get_fman();
	if (!fm)
		return -ENODEV;

	port_id = key->port_id;
	if (port_id >= ARRAY_SIZE(ports))
		return -EINVAL;

	/*
	 * POP and PUSH are independent bits (ask_flow_offload.c ORs them in
	 * separately per FLOW_ACTION_VLAN_POP/_PUSH). Both set means a
	 * same-port VID-to-VID TRANSLATE (e.g. two 802.1Q vifs on one
	 * physical port routing to each other) -- thread both through so
	 * fman_hm_vlan_route_get() builds a combined strip+insert HMTD
	 * instead of silently dropping the strip half (T-M6-8 VLAN
	 * throughput investigation, 2026-08-31: collapsing this to a single
	 * is_push bool meant translate flows only ever got a PUSH-only HMTD
	 * that never stripped the ingress tag).
	 */
	do_pop = !!(key->vlan_edit_flags & ASK_VLANF_POP);
	do_push = !!(key->vlan_edit_flags & ASK_VLANF_PUSH);
	if (!do_pop && !do_push)
		return -EOPNOTSUPP;

	if (do_push) {
		tci = ntohs(key->vlan_push_tci);
		vid = tci & 0x0fff;
		pcp = (tci >> 13) & 0x7;
		tpid = ntohs(key->vlan_push_tpid);
	} else {
		vid = 0;
		pcp = 0;
		tpid = 0;
	}

	rc = fman_hm_vlan_route_get(fm, port_id, do_pop, do_push, vid, tpid, pcp,
				    key->egress_mac, key->next_hop_mac,
				    tx_fqid, &hm_handle);
	if (rc)
		return rc;

	ask_vlan_cc_fill_key(&cc_key, key, tx_fqid, hm_handle);

	mutex_lock(&ask_vlan_cc_lock);
	port = &ports[port_id];

	/*
	 * Idempotent re-install: nf_flowtable calls ->replace again for a
	 * flow that is already offloaded (periodic refresh, or a retry
	 * after a REPLACE that failed for an unrelated reason) using the
	 * SAME 5-tuple. port->keys[] is a flat array with no dedup by
	 * itself -- unlike the ehash path, which is a hash table keyed by
	 * the flow tuple and naturally upserts. Without this check every
	 * such re-REPLACE appended a brand-new duplicate CC key for the
	 * same logical flow, so nkeys grew unbounded on any retry storm
	 * and ran the port's FMAN_CC_MAX_STATIC_KEYS (32) static tree out
	 * of space within about a second under real concurrent multi-flow
	 * load, permanently failing every subsequent install with -ENOSPC
	 * and falling the flow back to kernel software forwarding (T-M6-8
	 * VLAN throughput investigation, 2026-08-31 board evidence: 61/65
	 * REPLACEs failed -ENOSPC, nkeys churned 0->31->0 within 1s, an
	 * 8-stream bidirectional iperf3 test never exceeded software
	 * throughput). Match first and update/no-op in place; never grow
	 * nkeys for a flow this port already tracks.
	 */
	for (i = 0; i < port->nkeys; i++) {
		if (!ask_vlan_cc_key_match(&port->keys[i], &cc_key))
			continue;

		if (port->hm_handles[i] == hm_handle) {
			/* Unchanged refresh: drop the redundant ref this
			 * call just took and leave the live tree alone. */
			mutex_unlock(&ask_vlan_cc_lock);
			fman_hm_vlan_route_put(fm, port_id, hm_handle);
			return 0;
		}

		/* Egress adjacency changed (route/neigh update): swap the
		 * HMTD in place and rebuild once, same slot. */
		old_handle = port->hm_handles[i];
		old_key = port->keys[i];
		port->keys[i] = cc_key;
		port->hm_handles[i] = hm_handle;
		rc = ask_vlan_cc_rebuild_locked(fm, port_id, port);
		if (rc) {
			/* Install failed; the live tree still reflects the
			 * old entry (fman_cc_tree_install leaves it in place
			 * on error). Restore both fields so the software
			 * shadow keeps matching what silicon actually has. */
			port->keys[i] = old_key;
			port->hm_handles[i] = old_handle;
			mutex_unlock(&ask_vlan_cc_lock);
			fman_hm_vlan_route_put(fm, port_id, hm_handle);
			return rc;
		}
		mutex_unlock(&ask_vlan_cc_lock);
		fman_hm_vlan_route_put(fm, port_id, old_handle);
		return 0;
	}

	if (port->nkeys >= FMAN_CC_MAX_STATIC_KEYS) {
		mutex_unlock(&ask_vlan_cc_lock);
		fman_hm_vlan_route_put(fm, port_id, hm_handle);
		return -ENOSPC;
	}

	/*
	 * First flow on this port: snapshot the PHYSICAL egress port's
	 * tx stats so dump-flows can report the HW-forwarded aggregate
	 * (vif stats only count SW-stack traffic; HW bypass lands in the
	 * parent's dpaa stats).
	 */
	if (port->nkeys == 0 && egress_dev) {
		phys_dev = egress_dev;
		if (is_vlan_dev(phys_dev))
			phys_dev = vlan_dev_priv(phys_dev)->real_dev;
		if (phys_dev) {
			memset(&st, 0, sizeof(st));
			dev_get_stats(phys_dev, &st);
			port->agg_pkts_snap = st.tx_packets;
			port->agg_bytes_snap = st.tx_bytes;
			port->phys_ifindex = phys_dev->ifindex;
		}
	}

	port->keys[port->nkeys] = cc_key;
	port->hm_handles[port->nkeys] = hm_handle;
	port->nkeys++;

	rc = ask_vlan_cc_rebuild_locked(fm, port_id, port);
	if (rc) {
		port->nkeys--;
		memset(&port->keys[port->nkeys], 0,
		       sizeof(port->keys[port->nkeys]));
		port->hm_handles[port->nkeys] = 0;
		if (!port->nkeys) {
			port->agg_pkts_snap = 0;
			port->agg_bytes_snap = 0;
			port->phys_ifindex = 0;
		}
	}
	mutex_unlock(&ask_vlan_cc_lock);

	if (rc)
		fman_hm_vlan_route_put(fm, port_id, hm_handle);

	return rc;
}

void ask_vlan_cc_flow_del(const struct ask_flow_key *key)
{
	struct ask_vlan_cc_port *port;
	struct fman_cc_key cc_key;
	struct fman *fm;
	u32 hm_handle;
	u8 port_id;
	u16 i;
	bool tree_destroyed = false;

	if (!key)
		return;

	fm = ask_hw_get_fman();
	if (!fm)
		return;

	port_id = key->port_id;
	if (port_id >= ARRAY_SIZE(ports))
		return;

	ask_vlan_cc_fill_key(&cc_key, key, 0, 0);

	mutex_lock(&ask_vlan_cc_lock);
	port = &ports[port_id];
	for (i = 0; i < port->nkeys; i++)
		if (ask_vlan_cc_key_match(&port->keys[i], &cc_key))
			break;

	if (i == port->nkeys) {
		mutex_unlock(&ask_vlan_cc_lock);
		return;
	}

	hm_handle = port->hm_handles[i];
	for (; i + 1 < port->nkeys; i++) {
		port->keys[i] = port->keys[i + 1];
		port->hm_handles[i] = port->hm_handles[i + 1];
	}
	port->nkeys--;
	memset(&port->keys[port->nkeys], 0,
	       sizeof(port->keys[port->nkeys]));
	port->hm_handles[port->nkeys] = 0;

	/*
	 * TEARDOWN ORDERING (2026-08-26 vif-delete wedge fix, F-134 rule):
	 * the removed flow's HMTD (hm_handle) is still referenced by the
	 * LIVE CC leaf AD until we detach/rebuild the CC tree. fman_hm_vlan_
	 * route_put() can free that HMTD MURAM, so it MUST run AFTER the CC
	 * graft no longer points at it and after in-flight frames have
	 * drained -- otherwise the HM engine walks freed MURAM and faults the
	 * FMan controller (cold-boot-only wedge, observed on `delete vif`
	 * under live nft-DESTROY traffic). Both fman_cc_tree_destroy (last
	 * key) and fman_cc_tree_install/rebuild (remaining keys) already
	 * detach->clear-RCCB->5ms-drain before freeing/swapping MURAM, so by
	 * the time they return the old leaf AD is no longer walked. Do the CC
	 * operation FIRST, then put the HMTD.
	 */
	if (!port->nkeys) {
		ask_pr_info("vlan_cc: port 0x%02x last-flow detach+drain before HMTD put 0x%x\n",
			    port_id, hm_handle);
		fman_cc_tree_destroy(fm, port_id);
		port->installed = false;
		port->agg_pkts_snap = 0;
		port->agg_bytes_snap = 0;
		port->phys_ifindex = 0;
		tree_destroyed = true;
	} else {
		int rc;

		ask_pr_info("vlan_cc: port 0x%02x rebuild %u remaining keys before HMTD put 0x%x\n",
			    port_id, port->nkeys, hm_handle);
		rc = ask_vlan_cc_rebuild_locked(fm, port_id, port);

		if (rc)
			ask_pr_warn("vlan_cc: port 0x%02x rebuild failed: %d\n",
				    port_id, rc);
		/*
		 * Defensive quiesce: the rebuild re-grafts a NEW CC tree whose
		 * leaves no longer reference the removed HMTD, but give any
		 * frame that entered the OLD tree before the swap time to drain
		 * out of the HM pipeline before we free the removed HMTD MURAM
		 * just below. Mirrors the F-135 ~5ms DPAA1 drain that
		 * fman_cc_tree_destroy applies on the last-key path.
		 */
		usleep_range(5000, 6000);
	}

	/* Old leaf AD is detached/replaced and drained: safe to free HMTD. */
	fman_hm_vlan_route_put(fm, port_id, hm_handle);
	mutex_unlock(&ask_vlan_cc_lock);

	/*
	 * R4c-3: the last VLAN flow left this port, so fman_cc_tree_destroy
	 * reverted it to bare RSS. Re-assert the FE-VM ehash graft (RCCB ->
	 * FE_ENTER) OUTSIDE ask_vlan_cc_lock so routed/NAT stays HW-offloaded
	 * (ask_hw_fe_reengage takes h->lock; never nest it under our lock).
	 * No-op on a port ASK has not engaged.
	 */
	if (tree_destroyed)
		(void)ask_hw_fe_reengage(port_id);
}

void ask_vlan_cc_teardown_port(u8 port_id)
{
	struct ask_vlan_cc_port *port;
	struct fman *fm;
	u16 i;

	if (port_id >= ARRAY_SIZE(ports))
		return;

	fm = ask_hw_get_fman();
	if (!fm)
		return;

	mutex_lock(&ask_vlan_cc_lock);
	port = &ports[port_id];
	if (port->installed)
		fman_cc_tree_destroy(fm, port_id);
	for (i = 0; i < port->nkeys; i++)
		fman_hm_vlan_route_put(fm, port_id, port->hm_handles[i]);
	memset(port, 0, sizeof(*port));
	mutex_unlock(&ask_vlan_cc_lock);
}
EXPORT_SYMBOL_GPL(ask_vlan_cc_teardown_port);

/*
 * Port aggregate stats for dump-flows: the CC-tree path has no per-key
 * counters (no stats AD exists for CC nodes on this microcode), so report
 * the HW-forwarded delta on the flow's physical egress port since the
 * first VLAN flow was installed there.  Honest aggregate, not a per-flow
 * attribution.
 */
int ask_vlan_cc_agg_stats(u8 port_id, u64 *packets, u64 *bytes)
{
	struct ask_vlan_cc_port *port;
	struct rtnl_link_stats64 st;
	struct net_device *dev;
	u64 p, b;
	int ret = -ENODATA;

	if (port_id >= ARRAY_SIZE(ports) || !packets || !bytes)
		return -EINVAL;

	mutex_lock(&ask_vlan_cc_lock);
	port = &ports[port_id];
	if (!port->nkeys || !port->phys_ifindex)
		goto out;

	dev = dev_get_by_index(&init_net, port->phys_ifindex);
	if (!dev) {
		ret = -ENODEV;
		goto out;
	}
	memset(&st, 0, sizeof(st));
	dev_get_stats(dev, &st);
	dev_put(dev);

	p = (st.tx_packets > port->agg_pkts_snap) ?
	    st.tx_packets - port->agg_pkts_snap : 0;
	b = (st.tx_bytes > port->agg_bytes_snap) ?
	    st.tx_bytes - port->agg_bytes_snap : 0;
	*packets = p;
	*bytes = b;
	ret = 0;
out:
	mutex_unlock(&ask_vlan_cc_lock);
	return ret;
}
EXPORT_SYMBOL_GPL(ask_vlan_cc_agg_stats);
