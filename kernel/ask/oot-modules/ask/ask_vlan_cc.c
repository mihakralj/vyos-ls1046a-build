// SPDX-License-Identifier: GPL-2.0
#include <linux/errno.h>
#include <linux/module.h>
#include <linux/mutex.h>
#include <linux/net_namespace.h>
#include <linux/netdevice.h>
#include <linux/string.h>
#include <linux/types.h>

#include "ask_internal.h"
#include "ask_fman_caps.h"

/* Retained legacy state only; this module no longer publishes CC VLAN flows. */
struct ask_vlan_cc_port {
	struct fman_cc_key keys[FMAN_CC_MAX_STATIC_KEYS];
	u32 hm_handles[FMAN_CC_MAX_STATIC_KEYS];
	u16 nkeys;
	bool installed;
	u64 agg_pkts_snap;
	u64 agg_bytes_snap;
	int phys_ifindex;
};

static struct ask_vlan_cc_port ports[64];
static DEFINE_MUTEX(ask_vlan_cc_lock);

int ask_vlan_cc_teardown_port(u8 port_id)
{
	struct ask_vlan_cc_port *port;
	bool retained;
	u16 i;

	if (port_id >= ARRAY_SIZE(ports))
		return -EINVAL;

	mutex_lock(&ask_vlan_cc_lock);
	port = &ports[port_id];
	/* Legacy CC destruction does not propagate detach or quiescence failures. */
	retained = port->installed || port->nkeys;
	for (i = 0; i < ARRAY_SIZE(port->hm_handles); i++)
		retained |= port->hm_handles[i] != 0;
	if (retained) {
		mutex_unlock(&ask_vlan_cc_lock);
		ask_pr_err("vlan_cc: port 0x%02x has unresolved legacy CC owners; refusing teardown\n",
			   port_id);
		return -EOPNOTSUPP;
	}
	memset(port, 0, sizeof(*port));
	mutex_unlock(&ask_vlan_cc_lock);
	return 0;
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
	u64 p, b, pkts_snap, bytes_snap;
	int ifindex;

	if (port_id >= ARRAY_SIZE(ports) || !packets || !bytes)
		return -EINVAL;

	/*
	 * Lockless: the flow dump calls this inside an RCU read section
	 * (rhashtable walk), where taking ask_vlan_cc_lock (a mutex) is an
	 * invalid wait context (lockdep, 2026-10-22, .158 PPPoE gate). The
	 * fields are single words written under the lock; a stale read only
	 * skews an aggregate counter.
	 */
	port = &ports[port_id];
	ifindex = READ_ONCE(port->phys_ifindex);
	if (!READ_ONCE(port->nkeys) || !ifindex)
		return -ENODATA;
	pkts_snap = READ_ONCE(port->agg_pkts_snap);
	bytes_snap = READ_ONCE(port->agg_bytes_snap);

	dev = dev_get_by_index(&init_net, ifindex);
	if (!dev)
		return -ENODEV;
	memset(&st, 0, sizeof(st));
	dev_get_stats(dev, &st);
	dev_put(dev);

	p = (st.tx_packets > pkts_snap) ? st.tx_packets - pkts_snap : 0;
	b = (st.tx_bytes > bytes_snap) ? st.tx_bytes - bytes_snap : 0;
	*packets = p;
	*bytes = b;
	return 0;
}
EXPORT_SYMBOL_GPL(ask_vlan_cc_agg_stats);
