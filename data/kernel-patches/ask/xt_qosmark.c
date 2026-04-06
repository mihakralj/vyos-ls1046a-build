/*
 * xt_mark - Netfilter module to match NFMARK value
 *
 * (C) 1999-2001 Marc Boucher <marc@mbsi.ca>
 * Copyright © CC Computer Consultants GmbH, 2007 - 2008
 * Jan Engelhardt <jengelh@medozas.de>
 *
 * This program is free software; you can redistribute it and/or modify
 * it under the terms of the GNU General Public License version 2 as
 * published by the Free Software Foundation.
 */

#include <linux/module.h>
#include <linux/skbuff.h>

#include <linux/netfilter/xt_qosmark.h>
#include <linux/netfilter/x_tables.h>

MODULE_LICENSE("GPL");
MODULE_AUTHOR("Marc Boucher <marc@mbsi.ca>");
MODULE_DESCRIPTION("Xtables: packet qosmark operations");
MODULE_ALIAS("ipt_qosmark");
MODULE_ALIAS("ip6t_qosmark");
MODULE_ALIAS("ipt_QOSMARK");
MODULE_ALIAS("ip6t_QOSMARK");

static unsigned int
qosmark_tg(struct sk_buff *skb, const struct xt_action_param *par)
{
	const struct xt_qosmark_tginfo2 *info = par->targinfo;

	skb->qosmark = (skb->qosmark & ~info->mask) ^ info->mark;
	return XT_CONTINUE;
}

static bool
qosmark_mt(const struct sk_buff *skb, struct xt_action_param *par)
{
	const struct xt_qosmark_mtinfo1 *info = par->matchinfo;

	return ((skb->qosmark & info->mask) == info->mark) ^ info->invert;
}
static struct xt_target qosmark_tg_reg __read_mostly = {
	.name           = "QOSMARK",
	.revision       = 2,
	.family         = NFPROTO_UNSPEC,
	.target         = qosmark_tg,
	.targetsize     = sizeof(struct xt_qosmark_tginfo2),
	.me             = THIS_MODULE,
};

static struct xt_match qosmark_mt_reg __read_mostly = {
	.name           = "qosmark",
	.revision       = 1,
	.family         = NFPROTO_UNSPEC,
	.match          = qosmark_mt,
	.matchsize      = sizeof(struct xt_qosmark_mtinfo1),
	.me             = THIS_MODULE,
};

static int __init qosmark_mt_init(void)
{
	int ret;

	ret = xt_register_target(&qosmark_tg_reg);
	if (ret < 0)
		return ret;
	ret = xt_register_match(&qosmark_mt_reg);
	if (ret < 0) {
		xt_unregister_target(&qosmark_tg_reg);
		return ret;
	}
	return 0;
}

static void __exit qosmark_mt_exit(void)
{
	xt_unregister_match(&qosmark_mt_reg);
	xt_unregister_target(&qosmark_tg_reg);
}

module_init(qosmark_mt_init);
module_exit(qosmark_mt_exit);


