/* Copyright (C) 2015 Freescale Semiconductor, Inc.
 *
 * Redistribution and use in source and binary forms, with or without
 * modification, are permitted provided that the following conditions are met:
 *     * Redistributions of source code must retain the above copyright
 *       notice, this list of conditions and the following disclaimer.
 *     * Redistributions in binary form must reproduce the above copyright
 *       notice, this list of conditions and the following disclaimer in the
 *       documentation and/or other materials provided with the distribution.
 *     * Neither the name of Freescale Semiconductor nor the
 *       names of its contributors may be used to endorse or promote products
 *       derived from this software without specific prior written permission.
 *
 *
 * ALTERNATIVELY, this software may be distributed under the terms of the
 * GNU General Public License ("GPL") as published by the Free Software
 * Foundation, either version 2 of that License or (at your option) any
 * later version.
 *
 * THIS SOFTWARE IS PROVIDED BY Freescale Semiconductor ``AS IS'' AND ANY
 * EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE IMPLIED
 * WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE
 * DISCLAIMED. IN NO EVENT SHALL Freescale Semiconductor BE LIABLE FOR ANY
 * DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES
 * (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES;
 * LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND
 * ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT
 * (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE OF THIS
 * SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
 */

#include <linux/module.h>
#include <linux/version.h>
#include <linux/kernel.h>
#include <linux/skbuff.h>
#include <linux/netfilter.h>
#include <net/netfilter/nf_conntrack.h>
#include <net/netfilter/nf_conntrack_ecache.h>
#include <net/xfrm.h>

#ifndef IPSEC_FLOW_CACHE
/* this function is used to fill the xfrm info in conntrack structure */
static void nf_ct_set_xfrm_in_fp(struct sk_buff *skb, struct xfrm_state *xfrm[MAX_SUPPORTED_XFRMS_PER_DIR],
	int num_xfrm, int xfrm_dir, int *rekey)
{
	struct nf_conn *ct;
	enum ip_conntrack_info ctinfo;
	struct comcerto_fp_info *fp_info;
	int dir, xfrm_ind, ii;

	if (num_xfrm > MAX_SUPPORTED_XFRMS_PER_DIR) {
		printk(KERN_ERR "%s(%d) num_xfrm %d > %d(MAX supported)\n",
			__FUNCTION__, __LINE__, num_xfrm, MAX_SUPPORTED_XFRMS_PER_DIR);
		return;
	}

	/* get ct info */
	ct = nf_ct_get(skb, &ctinfo);
	if (!ct)
		return;

	dir = CTINFO2DIR(ctinfo);
	if (dir == IP_CT_DIR_ORIGINAL)
		fp_info = &ct->fp_info[IP_CT_DIR_ORIGINAL];
	else
		fp_info = &ct->fp_info[IP_CT_DIR_REPLY];

	if (xfrm_dir == XFRM_POLICY_FWD)
		xfrm_dir = 0;
	/* xfrm_dir can be XFRM_POLICY_IN, XFRM_POLICY_OUT, XFRM_POLICY_FWD */
	/* fp_info->xfrm_id[4] has 4 instances, first 2 instances reserved for direction IN/FWD
	   next 2 instances for OUT direction */
	xfrm_ind = xfrm_dir << 1;

	for (ii = 0; ii < num_xfrm; ii++) {
		if (fp_info->xfrm_handle[xfrm_ind + ii] &&
		    fp_info->xfrm_handle[xfrm_ind + ii] != xfrm[ii]->handle)
			*rekey = 1;

		/* filling SA info and xfrm handle */
		fp_info->xfrm_handle[xfrm_ind + ii] = xfrm[ii]->handle;
	}
}

/* this function is used to get the inbound and outbound xfrm info corresponding to
 * skb, if exist fill in conntrack structure */
static unsigned int fp_netfilter_get_xfrm_info(struct sk_buff *skb)
{
	struct nf_conn *ct;
	enum ip_conntrack_info ctinfo;
	struct sec_path *sp;
	struct xfrm_state *x[2] = {}, *tmp;
	int num_xfrms = 0, i, dir, rekey = 0;
	struct dst_entry *dst1 = skb_dst(skb);

	/* get ct info */
	ct = nf_ct_get(skb, &ctinfo);
	if (!ct)
		return NF_ACCEPT;

	/* ctinfo direction [originator/replier] */
	dir = CTINFO2DIR(ctinfo);

	/* extract the inbound IPSec information if exist */
	sp = skb_sec_path(skb);
	if (sp) {
		for (i = sp->len - 1; (i >= 0) && (num_xfrms < MAX_SUPPORTED_XFRMS_PER_DIR); i--) {
			x[num_xfrms] = sp->xvec[i];
			num_xfrms++;
		}
		if (num_xfrms)
			nf_ct_set_xfrm_in_fp(skb, x, num_xfrms, XFRM_POLICY_FWD, &rekey);
	}

	/* extract the outbound IPSec information if exist */
	if (dst1 && dst1->xfrm) {
		for (i = 0; i < MAX_SUPPORTED_XFRMS_PER_DIR; i++)
			x[i] = 0;
		num_xfrms = 0;

		while (((tmp = dst1->xfrm) != NULL) && (num_xfrms < MAX_SUPPORTED_XFRMS_PER_DIR)) {
			x[num_xfrms] = tmp;
			dst1 = xfrm_dst_child(dst1);
			num_xfrms++;
			if (dst1 == NULL) {
				pr_warn("%s(%d) DST is null\n", __FUNCTION__, __LINE__);
				break;
			}
		}
		if (num_xfrms)
			nf_ct_set_xfrm_in_fp(skb, x, num_xfrms, XFRM_POLICY_OUT, &rekey);
	}

	/* if there is a change in ipsec info, send rekey conntrack event */
	if (rekey)
		nf_conntrack_event_cache(IPCT_PROTOINFO, ct);

	return NF_ACCEPT;
}
#endif /* IPSEC_FLOW_CACHE */

#if LINUX_VERSION_CODE >= KERNEL_VERSION(4,4,0)
static unsigned int fp_netfilter_pre_routing(int family, const struct nf_hook_state *state, struct sk_buff *skb)
#else
static unsigned int fp_netfilter_pre_routing(int family, const struct nf_hook_ops *ops, struct sk_buff *skb)
#endif
{
	struct nf_conn *ct;
	u_int8_t protonum;
	enum ip_conntrack_info ctinfo;
	struct comcerto_fp_info *fp_info;
	int dir;

	ct = nf_ct_get(skb, &ctinfo);
	if (!ct)
		goto done;

	protonum = nf_ct_protonum(ct);
	if ((protonum != IPPROTO_TCP) && (protonum != IPPROTO_UDP) &&
	    (protonum != IPPROTO_IPIP) && (protonum != IPPROTO_IPV6) &&
#ifdef CONFIG_CPE_ETHERIP
	    (protonum != IPPROTO_ETHERIP) &&
#endif
	    (protonum != IPPROTO_GRE) && (protonum != IPPROTO_ESP) && (protonum != IPPROTO_AH))
		goto done;

	dir = CTINFO2DIR(ctinfo);

	if (dir == IP_CT_DIR_ORIGINAL)
		fp_info = &ct->fp_info[IP_CT_DIR_ORIGINAL];
	else
		fp_info = &ct->fp_info[IP_CT_DIR_REPLY];

	/* Log changes via dynamic debug (enable with dyndbg) */
	if (fp_info->mark && (fp_info->mark != skb->mark))
		pr_debug("fp_pre: mark changed %x -> %x\n", fp_info->mark, skb->mark);

	if (fp_info->ifindex && (fp_info->ifindex != skb->dev->ifindex))
		pr_debug("fp_pre: ifindex changed %d -> %d\n", fp_info->ifindex, skb->dev->ifindex);

if (fp_info->iif_index && (fp_info->iif_index != skb->skb_iif))
pr_debug("fp_pre: iif changed %d -> %d\n", fp_info->iif_index, skb->skb_iif);

fp_info->mark = skb->mark;
fp_info->ifindex = skb->dev->ifindex;
/* skb->skb_iif is set by netif_receive_skb() to the original input interface.
 * For forwarded packets iif_index != 0; for locally generated packets it's 0 or
 * equals the output device depending on routing, but local_out hook forces it to 0. */
fp_info->iif_index = skb->skb_iif;

done:
	return NF_ACCEPT;
}

#if LINUX_VERSION_CODE >= KERNEL_VERSION(4,4,0)
static unsigned int fp_netfilter_local_out(int family, const struct nf_hook_state *state, struct sk_buff *skb)
#else
static unsigned int fp_netfilter_local_out(int family, const struct nf_hook_ops *ops, struct sk_buff *skb)
#endif
{
	struct nf_conn *ct;
	u_int8_t protonum;
	enum ip_conntrack_info ctinfo;
	struct comcerto_fp_info *fp_info;
	int dir, update_event = 0;

	ct = nf_ct_get(skb, &ctinfo);
	if (!ct)
		goto done;

	protonum = nf_ct_protonum(ct);
#ifdef CONFIG_CPE_ETHERIP
	if ((protonum != IPPROTO_ETHERIP) && (protonum != IPPROTO_IPIP) &&
	    (protonum != IPPROTO_IPV6) && (protonum != IPPROTO_GRE))
#else
	if ((protonum != IPPROTO_IPIP) && (protonum != IPPROTO_IPV6) && (protonum != IPPROTO_GRE))
		goto done;
#endif

	dir = CTINFO2DIR(ctinfo);

	if (dir == IP_CT_DIR_ORIGINAL)
		fp_info = &ct->fp_info[IP_CT_DIR_ORIGINAL];
	else
		fp_info = &ct->fp_info[IP_CT_DIR_REPLY];

	if (fp_info->mark && (fp_info->mark != skb->mark))
		pr_debug("fp_out: mark changed %x -> %x\n", fp_info->mark, skb->mark);

	if ((fp_info->ifindex) && (skb->dev) && (fp_info->ifindex != skb->dev->ifindex)) {
		pr_debug("fp_out: ifindex changed %d -> %d\n", fp_info->ifindex, skb->dev->ifindex);
		update_event = 1;
	}

	fp_info->mark = skb->mark;
	if (skb->dev)
		fp_info->ifindex = skb->dev->ifindex;

	if (update_event == 1)
		nf_conntrack_event_cache(IPCT_PROTOINFO, ct);

	fp_info->iif_index = 0; /* To identify the connection as local connection */

#ifndef IPSEC_FLOW_CACHE
	/* fill xfrm info in conntrack structure */
	return (fp_netfilter_get_xfrm_info(skb));
#endif /* IPSEC_FLOW_CACHE */
done:
	return NF_ACCEPT;
}

#ifndef IPSEC_FLOW_CACHE
/* this post routing hook is introduced to gather the xfrm information
 * corresponding to ctinfo of skb and fill it in conntrack structure.
 * This is required to take the xfrm info of ctinfo part of conntrack message
 * to user space
 */
static unsigned int fp_ip_netfilter_post_routing(void *priv,
	struct sk_buff *skb,
	const struct nf_hook_state *state)
{
	return (fp_netfilter_get_xfrm_info(skb));
}
#endif /* IPSEC_FLOW_CACHE */

#if LINUX_VERSION_CODE >= KERNEL_VERSION(4,4,0)
static unsigned int fp_ipv4_netfilter_pre_routing(void *priv,
	struct sk_buff *skb,
	const struct nf_hook_state *state)
#elif LINUX_VERSION_CODE >= KERNEL_VERSION(4,0,0)
static unsigned int fp_ipv4_netfilter_pre_routing(const struct nf_hook_ops *ops,
	struct sk_buff *skb,
	const struct nf_hook_state *state)
#else
static unsigned int fp_ipv4_netfilter_pre_routing(const struct nf_hook_ops *ops,
	struct sk_buff *skb,
	const struct net_device *in,
	const struct net_device *out,
	int (*okfn)(struct sk_buff *))
#endif
{
#if LINUX_VERSION_CODE >= KERNEL_VERSION(4,4,0)
	return fp_netfilter_pre_routing(PF_INET, state, skb);
#else
	return fp_netfilter_pre_routing(PF_INET, ops, skb);
#endif
}

#if LINUX_VERSION_CODE >= KERNEL_VERSION(4,4,0)
static unsigned int fp_ipv6_netfilter_pre_routing(void *priv,
	struct sk_buff *skb,
	const struct nf_hook_state *state)
#elif LINUX_VERSION_CODE >= KERNEL_VERSION(4,0,0)
static unsigned int fp_ipv6_netfilter_pre_routing(const struct nf_hook_ops *ops,
	struct sk_buff *skb,
	const struct nf_hook_state *state)
#else
static unsigned int fp_ipv6_netfilter_pre_routing(const struct nf_hook_ops *ops,
	struct sk_buff *skb,
	const struct net_device *in,
	const struct net_device *out,
	int (*okfn)(struct sk_buff *))
#endif
{
#if LINUX_VERSION_CODE >= KERNEL_VERSION(4,4,0)
	return fp_netfilter_pre_routing(PF_INET6, state, skb);
#else
	return fp_netfilter_pre_routing(PF_INET6, ops, skb);
#endif
}

#if LINUX_VERSION_CODE >= KERNEL_VERSION(4,4,0)
static unsigned int fp_ipv4_netfilter_local_out(void *priv,
	struct sk_buff *skb,
	const struct nf_hook_state *state)
#elif LINUX_VERSION_CODE >= KERNEL_VERSION(4,0,0)
static unsigned int fp_ipv4_netfilter_local_out(const struct nf_hook_ops *ops,
	struct sk_buff *skb,
	const struct nf_hook_state *state)
#else
static unsigned int fp_ipv4_netfilter_local_out(const struct nf_hook_ops *ops,
	struct sk_buff *skb,
	const struct net_device *in,
	const struct net_device *out,
	int (*okfn)(struct sk_buff *))
#endif
{
#if LINUX_VERSION_CODE >= KERNEL_VERSION(4,4,0)
	return fp_netfilter_local_out(PF_INET, state, skb);
#else
	return fp_netfilter_local_out(PF_INET, ops, skb);
#endif
}

#if LINUX_VERSION_CODE >= KERNEL_VERSION(4,4,0)
static unsigned int fp_ipv6_netfilter_local_out(void *priv,
	struct sk_buff *skb,
	const struct nf_hook_state *state)
#elif LINUX_VERSION_CODE >= KERNEL_VERSION(4,0,0)
static unsigned int fp_ipv6_netfilter_local_out(const struct nf_hook_ops *ops,
	struct sk_buff *skb,
	const struct nf_hook_state *state)
#else
static unsigned int fp_ipv6_netfilter_local_out(const struct nf_hook_ops *ops,
	struct sk_buff *skb,
	const struct net_device *in,
	const struct net_device *out,
	int (*okfn)(struct sk_buff *))
#endif
{
#if LINUX_VERSION_CODE >= KERNEL_VERSION(4,4,0)
	return fp_netfilter_local_out(PF_INET6, state, skb);
#else
	return fp_netfilter_local_out(PF_INET6, ops, skb);
#endif
}

static struct nf_hook_ops fp_netfilter_ops[] __read_mostly = {
	{
		.hook		= fp_ipv4_netfilter_pre_routing,
		.pf		= NFPROTO_IPV4,
		.hooknum	= NF_INET_PRE_ROUTING,
		.priority	= NF_IP_PRI_LAST,
	},
	{
		.hook		= fp_ipv6_netfilter_pre_routing,
		.pf		= NFPROTO_IPV6,
		.hooknum	= NF_INET_PRE_ROUTING,
		.priority	= NF_IP_PRI_LAST,
	},
	/* For local_out packets, routing will be done
	   1. before entering the LOCAL_OUT hook
	   2. and at the completion of all mangle rules,
	   if there are changes to the packet like mark etc

	   So NF_IP_PRI_LAST priority is used here to receive
	   the mark value of the packet, at the end of all changes.
	 */
	{
		.hook		= fp_ipv4_netfilter_local_out,
		.pf		= NFPROTO_IPV4,
		.hooknum	= NF_INET_LOCAL_OUT,
		.priority	= NF_IP_PRI_LAST - 1,
	},
	{
		.hook		= fp_ipv6_netfilter_local_out,
		.pf		= NFPROTO_IPV6,
		.hooknum	= NF_INET_LOCAL_OUT,
		.priority	= NF_IP_PRI_LAST - 1,
	},
#ifndef IPSEC_FLOW_CACHE
	{
		.hook		= fp_ip_netfilter_post_routing,
		.pf		= NFPROTO_IPV4,
		.hooknum	= NF_INET_POST_ROUTING,
		.priority	= NF_IP_PRI_LAST - 1,
	},
	{
		.hook		= fp_ip_netfilter_post_routing,
		.pf		= NFPROTO_IPV6,
		.hooknum	= NF_INET_POST_ROUTING,
		.priority	= NF_IP_PRI_LAST - 1,
	},
#endif
};

static int __init fp_netfilter_init(void)
{
	int rc;

	/*
	 * Force-register conntrack hooks in init_net.
	 * Kernel 6.6 lazy-loads conntrack hooks — they only activate when an
	 * nftables chain uses a 'ct' expression (e.g., ct state new).  ASK
	 * needs conntrack active to populate fp_info on every tracked flow.
	 * Calling nf_ct_netns_get() increments the conntrack refcount for this
	 * netns, which registers the PREROUTING/OUTPUT conntrack hooks if they
	 * haven't been registered yet.  This ensures conntrack tracking works
	 * even on TFTP dev boot where nftables modules may not be available.
	 */
	rc = nf_ct_netns_get(&init_net, NFPROTO_INET);
	if (rc < 0) {
		pr_warn("ASK fp_netfilter: nf_ct_netns_get failed (%d), conntrack may not track\n", rc);
		/* Non-fatal: continue with hook registration */
	}

	rc = nf_register_net_hooks(&init_net, fp_netfilter_ops, ARRAY_SIZE(fp_netfilter_ops));
	if (rc < 0) {
		printk(KERN_ERR "fp_netfilter_ops: can't register hooks.\n");
		nf_ct_netns_put(&init_net, NFPROTO_INET);
		goto err0;
	}

	pr_info("ASK fp_netfilter: hooks registered + conntrack force-enabled\n");
	return 0;

err0:
	return rc;
}

static void __exit fp_netfilter_exit(void)
{
	nf_unregister_net_hooks(&init_net, fp_netfilter_ops, ARRAY_SIZE(fp_netfilter_ops));
	nf_ct_netns_put(&init_net, NFPROTO_INET);
}

module_init(fp_netfilter_init);
module_exit(fp_netfilter_exit);