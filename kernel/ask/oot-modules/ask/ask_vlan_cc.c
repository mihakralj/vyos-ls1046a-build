// SPDX-License-Identifier: GPL-2.0
#include <linux/errno.h>
#include <linux/if_ether.h>
#include <linux/in.h>
#include <linux/module.h>
#include <linux/mutex.h>
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
	struct fman_cc_key cc_key;
	struct fman *fm;
	u32 hm_handle;
	u16 tci;
	u16 vid;
	u16 tpid;
	u8 port_id;
	u8 pcp;
	bool is_push;
	int rc;

	(void)egress_dev;

	if (!key)
		return -EINVAL;
	if (!ask_hw_vlan_offload_armed())
		return -EOPNOTSUPP;
	if (key->l3_proto != ASK_FLOW_L3_IPV4)
		return -EOPNOTSUPP;

	fm = ask_hw_get_fman();
	if (!fm)
		return -ENODEV;

	port_id = key->port_id;
	if (port_id >= ARRAY_SIZE(ports))
		return -EINVAL;

	is_push = !!(key->vlan_edit_flags & ASK_VLANF_PUSH);
	if (is_push) {
		tci = ntohs(key->vlan_push_tci);
		vid = tci & 0x0fff;
		pcp = (tci >> 13) & 0x7;
		tpid = ntohs(key->vlan_push_tpid);
	} else {
		vid = key->vlan_ingress_vid;
		pcp = 0;
		tpid = ETH_P_8021Q;
	}

	rc = fman_hm_vlan_route_get(fm, port_id, is_push, vid, tpid, pcp,
				    key->egress_mac, key->next_hop_mac,
				    tx_fqid, &hm_handle);
	if (rc)
		return rc;

	ask_vlan_cc_fill_key(&cc_key, key, tx_fqid, hm_handle);

	mutex_lock(&ask_vlan_cc_lock);
	port = &ports[port_id];
	if (port->nkeys >= FMAN_CC_MAX_STATIC_KEYS) {
		mutex_unlock(&ask_vlan_cc_lock);
		fman_hm_vlan_route_put(fm, port_id, hm_handle);
		return -ENOSPC;
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

	fman_hm_vlan_route_put(fm, port_id, hm_handle);
	if (!port->nkeys) {
		fman_cc_tree_destroy(fm, port_id);
		port->installed = false;
		tree_destroyed = true;
	} else {
		int rc = ask_vlan_cc_rebuild_locked(fm, port_id, port);

		if (rc)
			ask_pr_warn("vlan_cc: port 0x%02x rebuild failed: %d\n",
				    port_id, rc);
	}
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
