// SPDX-License-Identifier: GPL-2.0+
/* Maintain IPSec flow table for ASK fast path
 *
 * Copyright 2019 NXP
 */
#if defined(CONFIG_INET_IPSEC_OFFLOAD) || defined(CONFIG_INET_IPSEC_OFFLOAD_MODULE) || defined(CONFIG_INET6_IPSEC_OFFLOAD) || defined(CONFIG_INET6_IPSEC_OFFLOAD_MODULE)
#include <linux/kernel.h>
#include <linux/module.h>
#include <linux/list.h>
#include <linux/jhash.h>
#include <linux/interrupt.h>
#include <linux/mm.h>
#include <linux/random.h>
#include <linux/init.h>
#include <linux/slab.h>
#include <linux/smp.h>
#include <linux/completion.h>
#include <linux/percpu.h>
#include <linux/bitops.h>
#include <linux/notifier.h>
#include <linux/cpu.h>
#include <linux/cpumask.h>
#include <linux/mutex.h>
#include <net/xfrm.h>
#include <linux/atomic.h>
#include <linux/security.h>
#include <net/net_namespace.h>
#include "ipsec_flow.h"

/* Type for flow comparison (from removed flow cache) */
typedef unsigned long flow_compare_t;

/* Return the flow key size in units of flow_compare_t for a given address family */
static inline size_t flow_key_size(unsigned short family)
{
	switch (family) {
	case AF_INET:
		return sizeof(struct flowi4) / sizeof(flow_compare_t);
	case AF_INET6:
		return sizeof(struct flowi6) / sizeof(flow_compare_t);
	}
	return 0;
}

#define IPSEC_FLOW_TABLE_SIZE  1024
static struct kmem_cache *ipsec_flow_cachep __read_mostly;
struct flow_table ipsec_flow_table_global;

static u32 flow_hash_code(const struct flowi *flow, size_t keysize)
{
	const u32 *k = (const u32 *)flow;
	const u32 length = keysize * sizeof(flow_compare_t) / sizeof(u32);
	const u32 hash_random = 0; /*FIXME */


	return jhash2(k, length, hash_random);
}

static int ipsec_flow_compare(const struct flowi *key1, const struct flowi *key2,
       size_t keysize)
{
	const flow_compare_t *k1, *k1_lim, *k2;

	k1 = (const flow_compare_t *) key1;
	k1_lim = k1 + keysize;

	k2 = (const flow_compare_t *) key2;

	do {
		if (*k1++ != *k2++)
			return 1;
	} while (k1 < k1_lim);

	return 0;
}

/*
+* return 1 for new flow 0 for existing flow
+*/
int ipsec_flow_add(struct net *net, const struct flowi *flow, u16 family, u8 dir, u16 *xfrm_handle)
{
	size_t keysize;
	struct flow_entry *tfle, *fle;
	struct flow_table *ft;
	u32 hash;
	u16 index, update = 0;

	ft = &ipsec_flow_table_global;


	keysize = flow_key_size(family);
	if(!keysize)
		return 0;

	hash = (flow_hash_code(flow, keysize) &  (IPSEC_FLOW_TABLE_SIZE - 1));

	spin_lock_bh(&ft->ipsec_flow_lock);
	hlist_for_each_entry(tfle, &ft->hash_table[hash], hlist) {
		if(tfle->net ==  net &&
				tfle->family == family &&
				tfle->dir == dir &&
				(ipsec_flow_compare(flow, &tfle->flow, keysize) == 0)) {
			/*Flow found */
			for (index = 0; index < XFRM_POLICY_TYPE_MAX; index++) {
				if (tfle->xfrm_handle[index] != xfrm_handle[index]) {
					/*pr_info("%s()::%d flow handle updating from 0x%x to 0x%x\n",
					  __func__, __LINE__, tfle->xfrm_handle[index], xfrm_handle[index]);*/
					tfle->xfrm_handle[index] = xfrm_handle[index];
					update = 1;
				}
			}
			spin_unlock_bh(&ft->ipsec_flow_lock);
			if (update) {
				return 1;
			}
			else
				return 0;

		}
	}
	spin_unlock_bh(&ft->ipsec_flow_lock);
	/* Insert flow into flow table */
	fle = kmem_cache_alloc(ipsec_flow_cachep, GFP_ATOMIC);
	if(fle) {
		fle->net = net;
		fle->family = family;
		fle->dir = dir;
		memcpy(&fle->flow, flow, keysize * sizeof(flow_compare_t));
		memcpy(fle->xfrm_handle, xfrm_handle, XFRM_POLICY_TYPE_MAX*sizeof(u16));
		spin_lock_bh(&ft->ipsec_flow_lock);
		hlist_add_head(&fle->hlist, &ft->hash_table[hash]);
		ft->flow_cnt++;
		spin_unlock_bh(&ft->ipsec_flow_lock);
		/*pr_info("%s flow added, xfrm_handle <0x%x 0x%x> fle->xfrm_handle <0x%x 0x%x>\n",
		  __func__, xfrm_handle[0], xfrm_handle[1], fle->xfrm_handle[0], fle->xfrm_handle[1]);*/

	}else {
		pr_err("%s:  Failed to alloc memory, flow is not pushed\n", __func__);
		return 0;
	}

	return 1;
}
EXPORT_SYMBOL(ipsec_flow_add);

static int ipsec_flow_remove(const struct flowi *flow, u16 family, u8 dir)
{
	size_t keysize;
	struct flow_entry *tfle;
	struct flow_table *ft;
	u32 hash;

	ft = &ipsec_flow_table_global;


	keysize = flow_key_size(family);
	if(!keysize)
		goto ignore_flow;

	hash = (flow_hash_code(flow, keysize) &  (IPSEC_FLOW_TABLE_SIZE - 1));

	spin_lock_bh(&ft->ipsec_flow_lock);
	hlist_for_each_entry(tfle, &ft->hash_table[hash], hlist) {
		if(tfle->family == family &&
				tfle->dir == dir &&
				(ipsec_flow_compare(flow, &tfle->flow, keysize) == 0)) {
			/*Flow found */
			hlist_del(&tfle->hlist);
			kmem_cache_free(ipsec_flow_cachep, tfle);
			ft->flow_cnt--;
			spin_unlock_bh(&ft->ipsec_flow_lock);
			return 1;
		}
	}

	spin_unlock_bh(&ft->ipsec_flow_lock);
ignore_flow:
	pr_err("%s: Failed to remove flow\n", __func__);
	return 0;
}

void flow_cache_remove(const struct flowi *key,    unsigned short family,
       unsigned short dir)
{

	ipsec_flow_remove(key, family, dir);
}

int ipsec_flow_init(struct net *net)
{
	struct flow_table *ft;

	pr_info("%s \n",__func__);
	ft = &ipsec_flow_table_global;

	if (!ipsec_flow_cachep)
		ipsec_flow_cachep = kmem_cache_create("ipsec_flow_cache",
				sizeof(struct flow_entry),
				0, SLAB_PANIC, NULL);
	ft->hash_table = kzalloc(sizeof(struct hlist_head) * IPSEC_FLOW_TABLE_SIZE, GFP_KERNEL);
	if(!ft->hash_table) {
		pr_err("%s: failed to allocate memory\n", __func__);
		return -ENOMEM;
	}
	ft->flow_cnt = 0;
	spin_lock_init(&ft->ipsec_flow_lock);
	return 0;
}
EXPORT_SYMBOL(ipsec_flow_init);

void ipsec_flow_fini(struct net *net)
{
	struct flow_table *ft;

	pr_info("%s \n",__func__);
	ft = &ipsec_flow_table_global;
	kfree(ft->hash_table);
}
EXPORT_SYMBOL(ipsec_flow_fini);

MODULE_LICENSE("GPL");
#endif

