/* xt_connmark - Netfilter module to operate on connection marks
 *
 * Copyright (C) 2002,2004 MARA Systems AB <http://www.marasystems.com>
 * by Henrik Nordstrom <hno@marasystems.com>
 * Copyright © CC Computer Consultants GmbH, 2007 - 2008
 * Jan Engelhardt <jengelh@medozas.de>
 *
 * This program is free software; you can redistribute it and/or modify
 * it under the terms of the GNU General Public License as published by
 * the Free Software Foundation; either version 2 of the License, or
 * (at your option) any later version.
 *
 * This program is distributed in the hope that it will be useful,
 * but WITHOUT ANY WARRANTY; without even the implied warranty of
 * MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
 * GNU General Public License for more details.
 *
 * You should have received a copy of the GNU General Public License
 * along with this program; if not, write to the Free Software
 * Foundation, Inc., 59 Temple Place, Suite 330, Boston, MA  02111-1307  USA
 */

#include <linux/module.h>
#include <linux/skbuff.h>
#include <net/netfilter/nf_conntrack.h>
#include <net/netfilter/nf_conntrack_ecache.h>
#include <linux/netfilter/x_tables.h>
#include <linux/netfilter/xt_qosconnmark.h>

MODULE_AUTHOR("Henrik Nordstrom <hno@marasystems.com>");
MODULE_DESCRIPTION("Xtables: QOS connection mark operations");
MODULE_LICENSE("GPL");
MODULE_ALIAS("ipt_QOSCONNMARK");
MODULE_ALIAS("ip6t_QOSCONNMARK");
MODULE_ALIAS("ipt_qosconnmark");
MODULE_ALIAS("ip6t_qosconnmark");

static unsigned int
qosconnmark_tg(struct sk_buff *skb, const struct xt_action_param *par)
{
	const struct xt_qosconnmark_tginfo1 *info = par->targinfo;
	enum ip_conntrack_info ctinfo;
	struct nf_conn *ct;
	u_int64_t newmark;

	ct = nf_ct_get(skb, &ctinfo);
	if (ct == NULL)
		return XT_CONTINUE;

	switch (info->mode) {
		case XT_QOSCONNMARK_SET:
			newmark = (ct->qosconnmark & ~info->ctmask) ^ info->mark;
			if (ct->qosconnmark != newmark) {
				ct->qosconnmark = newmark;
				nf_conntrack_event_cache(IPCT_QOSCONNMARK, ct);
			}
			break;
		case XT_QOSCONNMARK_SAVE_QOSMARK:
			newmark = (ct->qosconnmark & ~info->ctmask) ^
				(skb->qosmark & info->nfmask);
			if (ct->qosconnmark != newmark) {
				ct->qosconnmark = newmark;
				nf_conntrack_event_cache(IPCT_QOSCONNMARK, ct);
			}
			break;
		case XT_QOSCONNMARK_RESTORE_QOSMARK:
			newmark = (skb->qosmark & ~info->nfmask) ^
				(ct->qosconnmark & info->ctmask);
			skb->qosmark = newmark;
			break;
	}

	return XT_CONTINUE;
}

static int qosconnmark_tg_check(const struct xt_tgchk_param *par)
{
	int ret;

	ret = nf_ct_netns_get(par->net, par->family);
	if (ret < 0)
		pr_info("cannot load conntrack support for proto=%u\n",
			par->family);
	return ret;
}

 
static void qosconnmark_tg_destroy(const struct xt_tgdtor_param *par)
{
	nf_ct_netns_put(par->net, par->family);
}

static bool
qosconnmark_mt(const struct sk_buff *skb, struct xt_action_param *par)
{
	const struct xt_qosconnmark_mtinfo1 *info = par->matchinfo;
	enum ip_conntrack_info ctinfo;
	const struct nf_conn *ct;

	ct = nf_ct_get(skb, &ctinfo);
	if (ct == NULL)
		return false;

	return ((ct->qosconnmark & info->mask) == info->mark) ^ info->invert;
}

static int qosconnmark_mt_check(const struct xt_mtchk_param *par)
{
	int ret;

	ret = nf_ct_netns_get(par->net, par->family);
	if (ret < 0)
		pr_info("cannot load conntrack support for proto=%u\n",
			par->family);
	return ret;
}

static void qosconnmark_mt_destroy(const struct xt_mtdtor_param *par)
{
	nf_ct_netns_put(par->net, par->family);
}

static struct xt_target qosconnmark_tg_reg __read_mostly = {
	.name           = "QOSCONNMARK",
	.revision       = 1,
	.family         = NFPROTO_UNSPEC,
	.checkentry     = qosconnmark_tg_check,
	.target         = qosconnmark_tg,
	.targetsize     = sizeof(struct xt_qosconnmark_tginfo1),
	.destroy        = qosconnmark_tg_destroy,
	.me             = THIS_MODULE,
};

static struct xt_match qosconnmark_mt_reg __read_mostly = {
	.name           = "qosconnmark",
	.revision       = 1,
	.family         = NFPROTO_UNSPEC,
	.checkentry     = qosconnmark_mt_check,
	.match          = qosconnmark_mt,
	.matchsize      = sizeof(struct xt_qosconnmark_mtinfo1),
	.destroy        = qosconnmark_mt_destroy,
	.me             = THIS_MODULE,
};

static int __init qosconnmark_mt_init(void)
{
	int ret;

	ret = xt_register_target(&qosconnmark_tg_reg);
	if (ret < 0)
		return ret;
	ret = xt_register_match(&qosconnmark_mt_reg);
	if (ret < 0) {
		xt_unregister_target(&qosconnmark_tg_reg);
		return ret;
	}
	return 0;
}

static void __exit qosconnmark_mt_exit(void)
{
	xt_unregister_match(&qosconnmark_mt_reg);
	xt_unregister_target(&qosconnmark_tg_reg);
}

module_init(qosconnmark_mt_init);
module_exit(qosconnmark_mt_exit);

             


