// SPDX-License-Identifier: GPL-2.0+
/* Maintain IPSec flow table for ASK fast path
 *
 * Copyright 2019 NXP
 */
#if defined(CONFIG_INET_IPSEC_OFFLOAD) || defined(CONFIG_INET_IPSEC_OFFLOAD_MODULE) || defined(CONFIG_INET6_IPSEC_OFFLOAD) || defined(CONFIG_INET6_IPSEC_OFFLOAD_MODULE)
#include <linux/kernel.h>
#include <linux/list.h>
#include <linux/mutex.h>
#include <net/xfrm.h>
#include <net/net_namespace.h>

struct flow_table {
	struct hlist_head   *hash_table;
	spinlock_t      ipsec_flow_lock;
	int             flow_cnt;
};

struct flow_entry {
	struct hlist_node   hlist;
	struct flowi        flow;
	struct net      *net;
	u16         family;
	u16             xfrm_handle[XFRM_POLICY_TYPE_MAX];
	u8          dir;

};

int ipsec_flow_add(struct net *net, const struct flowi *flow, u16 family, u8 dir, u16 *xfrm_handle);
#endif

