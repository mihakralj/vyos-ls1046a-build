/* Shared library add-on to iptables to add QOSCONNMARK target support.
 *
 * (C) 2002,2004 MARA Systems AB <http://www.marasystems.com>
 * by Henrik Nordstrom <hno@marasystems.com>
 *
 * This program is free software; you can redistribute it and/or modify
 * it under the terms of the GNU General Public License as published by
 * the Free Software Foundation; either version 2 of the License, or
 * (at your option) any later version.
 */
#include <stdbool.h>
#include <stdio.h>
#include <errno.h>
#include <inttypes.h>
#include <string.h>
#include <stdlib.h>

#include <xtables.h>
#include <linux/netfilter/x_tables.h>
#include <linux/netfilter/xt_QOSCONNMARK.h>

enum {
	O_SET_XMARK = 0,
	O_SET_MARK,
	O_AND_MARK,
	O_OR_MARK,
	O_XOR_MARK,
	O_SAVE_MARK,
	O_RESTORE_MARK,
	O_CTMASK,
	O_NFMASK,
	O_MASK,
	F_OP    = (1 << O_SET_XMARK) | (1 << O_SET_MARK) | (1 << O_AND_MARK) |
	          (1 << O_OR_MARK) | (1 << O_XOR_MARK) |
	          (1 << O_SAVE_MARK) | (1 << O_RESTORE_MARK),
	F_SR    = (1 << O_SAVE_MARK) | (1 << O_RESTORE_MARK),
};

static int parse64(const char *s, char **end, uint64_t *value)
{
	uint64_t v;
	char *my_end;

	errno = 0;
	v = (uint64_t)strtoull(s, &my_end, 0);

	if (my_end == s)
		return false;
	if (end != NULL)
		*end = my_end;

	if (errno != ERANGE) {
		if (value != NULL)
			*value = v;
		if (end == NULL)
			return *my_end == '\0';
		return true;
	}

	return false;
}

static void qosconnmark_tg_help(void)
{
	printf(
"QOSCONNMARK target options:\n"
"  --set-xmark value[/ctmask]    Zero mask bits and XOR qosconnmark with value\n"
"  --save-mark [--ctmask mask] [--nfmask mask]\n"
"                                Copy qosconnmark to qosmark using masks\n"
"  --restore-mark [--ctmask mask] [--nfmask mask]\n"
"                                Copy qosmark to qosconnmark using masks\n"
"  --set-mark value[/mask]       Set qosconntrack mark value\n"
"  --save-mark [--mask mask]     Save the packet qosmark in the connection\n"
"  --restore-mark [--mask mask]  Restore saved qosmark value\n"
"  --and-mark value              Binary AND the qosconnmark with bits\n"
"  --or-mark value               Binary OR  the qosconnmark with bits\n"
"  --xor-mark value              Binary XOR the qosconnmark with bits\n"
);
}

static const struct xt_option_entry qosconnmark_tg_opts[] = {
	{.name = "set-xmark",    .id = O_SET_XMARK,    .type = XTTYPE_STRING,
	 .excl = F_OP},
	{.name = "set-mark",     .id = O_SET_MARK,     .type = XTTYPE_STRING,
	 .excl = F_OP},
	{.name = "and-mark",     .id = O_AND_MARK,     .type = XTTYPE_STRING,
	 .excl = F_OP},
	{.name = "or-mark",      .id = O_OR_MARK,      .type = XTTYPE_STRING,
	 .excl = F_OP},
	{.name = "xor-mark",     .id = O_XOR_MARK,     .type = XTTYPE_STRING,
	 .excl = F_OP},
	{.name = "save-mark",    .id = O_SAVE_MARK,    .type = XTTYPE_NONE,
	 .excl = F_OP},
	{.name = "restore-mark", .id = O_RESTORE_MARK, .type = XTTYPE_NONE,
	 .excl = F_OP},
	{.name = "ctmask",       .id = O_CTMASK,       .type = XTTYPE_STRING,
	 .excl = (1 << O_MASK)},
	{.name = "nfmask",       .id = O_NFMASK,       .type = XTTYPE_STRING,
	 .excl = (1 << O_MASK)},
	{.name = "mask",         .id = O_MASK,          .type = XTTYPE_STRING,
	 .excl = (1 << O_CTMASK) | (1 << O_NFMASK)},
	XTOPT_TABLEEND,
};

static void qosconnmark_tg_init(struct xt_entry_target *target)
{
	struct xt_qosconnmark_tginfo1 *info = (void *)target->data;

	info->ctmask = UINT64_MAX;
	info->nfmask = UINT64_MAX;
}

static void qosconnmark_tg_parse(struct xt_option_call *cb)
{
	struct xt_qosconnmark_tginfo1 *info = cb->data;
	uint64_t value = 0, mask = UINT64_MAX;
	char *end = NULL;

	xtables_option_parse(cb);

	switch (cb->entry->id) {
	case O_SET_XMARK:
	case O_SET_MARK:
		if (!parse64(cb->arg, &end, &value))
			xtables_param_act(XTF_BAD_VALUE, "QOSCONNMARK",
			                  "--set-xmark/--set-mark", cb->arg);
		if (*end == '/')
			if (!parse64(end + 1, &end, &mask))
				xtables_param_act(XTF_BAD_VALUE, "QOSCONNMARK",
				                  "--set-xmark/--set-mark", cb->arg);
		if (*end != '\0')
			xtables_param_act(XTF_BAD_VALUE, "QOSCONNMARK",
			                  "--set-xmark/--set-mark", cb->arg);
		info->mode   = XT_QOSCONNMARK_SET;
		info->mark   = value;
		info->ctmask = mask;
		if (cb->entry->id == O_SET_MARK)
			info->ctmask |= value;
		break;

	case O_AND_MARK:
		if (!parse64(cb->arg, NULL, &mask))
			xtables_param_act(XTF_BAD_VALUE, "QOSCONNMARK",
			                  "--and-mark", cb->arg);
		info->mode   = XT_QOSCONNMARK_SET;
		info->mark   = 0;
		info->ctmask = ~mask;
		break;

	case O_OR_MARK:
		if (!parse64(cb->arg, NULL, &value))
			xtables_param_act(XTF_BAD_VALUE, "QOSCONNMARK",
			                  "--or-mark", cb->arg);
		info->mode   = XT_QOSCONNMARK_SET;
		info->mark   = value;
		info->ctmask = value;
		break;

	case O_XOR_MARK:
		if (!parse64(cb->arg, NULL, &value))
			xtables_param_act(XTF_BAD_VALUE, "QOSCONNMARK",
			                  "--xor-mark", cb->arg);
		info->mode   = XT_QOSCONNMARK_SET;
		info->mark   = value;
		info->ctmask = 0;
		break;

	case O_SAVE_MARK:
		info->mode = XT_QOSCONNMARK_SAVE_QOSMARK;
		break;

	case O_RESTORE_MARK:
		info->mode = XT_QOSCONNMARK_RESTORE_QOSMARK;
		break;

	case O_NFMASK:
		if (!parse64(cb->arg, NULL, &value))
			xtables_param_act(XTF_BAD_VALUE, "QOSCONNMARK",
			                  "--nfmask", cb->arg);
		info->nfmask = value;
		break;

	case O_CTMASK:
		if (!parse64(cb->arg, NULL, &value))
			xtables_param_act(XTF_BAD_VALUE, "QOSCONNMARK",
			                  "--ctmask", cb->arg);
		info->ctmask = value;
		break;

	case O_MASK:
		if (!parse64(cb->arg, NULL, &value))
			xtables_param_act(XTF_BAD_VALUE, "QOSCONNMARK",
			                  "--mask", cb->arg);
		info->nfmask = info->ctmask = value;
		break;
	}
}

static void qosconnmark_tg_check(struct xt_fcheck_call *cb)
{
	unsigned int mask_opts = (1 << O_CTMASK) | (1 << O_NFMASK) | (1 << O_MASK);

	if (cb->xflags == 0)
		xtables_error(PARAMETER_PROBLEM,
		           "QOSCONNMARK target: No operation specified");
	if ((cb->xflags & mask_opts) && !(cb->xflags & F_SR))
		xtables_error(PARAMETER_PROBLEM, "QOSCONNMARK: --save-mark "
		           "or --restore-mark is required for "
		           "--ctmask/--nfmask/--mask");
}

static void
qosconnmark_tg_print(const void *ip, const struct xt_entry_target *target,
                  int numeric)
{
	const struct xt_qosconnmark_tginfo1 *info = (const void *)target->data;

	switch (info->mode) {
	case XT_QOSCONNMARK_SET:
		if (info->mark == 0)
			printf(" QOSCONNMARK and 0x%" PRIx64, (uint64_t)~info->ctmask);
		else if (info->mark == info->ctmask)
			printf(" QOSCONNMARK or 0x%" PRIx64, (uint64_t)info->mark);
		else if (info->ctmask == 0)
			printf(" QOSCONNMARK xor 0x%" PRIx64, (uint64_t)info->mark);
		else if (info->ctmask == UINT64_MAX)
			printf(" QOSCONNMARK set 0x%" PRIx64, (uint64_t)info->mark);
		else
			printf(" QOSCONNMARK xset 0x%" PRIx64 "/0x%" PRIx64,
			       (uint64_t)info->mark, (uint64_t)info->ctmask);
		break;
	case XT_QOSCONNMARK_SAVE_QOSMARK:
		if (info->nfmask == UINT64_MAX && info->ctmask == UINT64_MAX)
			printf(" QOSCONNMARK save");
		else if (info->nfmask == info->ctmask)
			printf(" QOSCONNMARK save mask 0x%" PRIx64, (uint64_t)info->nfmask);
		else
			printf(" QOSCONNMARK save nfmask 0x%" PRIx64 " ctmask ~0x%" PRIx64,
			       (uint64_t)info->nfmask, (uint64_t)info->ctmask);
		break;
	case XT_QOSCONNMARK_RESTORE_QOSMARK:
		if (info->ctmask == UINT64_MAX && info->nfmask == UINT64_MAX)
			printf(" QOSCONNMARK restore");
		else if (info->ctmask == info->nfmask)
			printf(" QOSCONNMARK restore mask 0x%" PRIx64, (uint64_t)info->ctmask);
		else
			printf(" QOSCONNMARK restore ctmask 0x%" PRIx64 " nfmask ~0x%" PRIx64,
			       (uint64_t)info->ctmask, (uint64_t)info->nfmask);
		break;
	default:
		printf(" ERROR: UNKNOWN QOSCONNMARK MODE");
		break;
	}
}

static void
qosconnmark_tg_save(const void *ip, const struct xt_entry_target *target)
{
	const struct xt_qosconnmark_tginfo1 *info = (const void *)target->data;

	switch (info->mode) {
	case XT_QOSCONNMARK_SET:
		printf(" --set-xmark 0x%" PRIx64 "/0x%" PRIx64, (uint64_t)info->mark, (uint64_t)info->ctmask);
		break;
	case XT_QOSCONNMARK_SAVE_QOSMARK:
		printf(" --save-mark --nfmask 0x%" PRIx64 " --ctmask 0x%" PRIx64,
		       (uint64_t)info->nfmask, (uint64_t)info->ctmask);
		break;
	case XT_QOSCONNMARK_RESTORE_QOSMARK:
		printf(" --restore-mark --nfmask 0x%" PRIx64 " --ctmask 0x%" PRIx64,
		       (uint64_t)info->nfmask, (uint64_t)info->ctmask);
		break;
	default:
		printf(" ERROR: UNKNOWN QOSCONNMARK MODE");
		break;
	}
}

static struct xtables_target qosconnmark_target = {
	.version       = XTABLES_VERSION,
	.name          = "QOSCONNMARK",
	.revision      = 1,
	.family        = NFPROTO_UNSPEC,
	.size          = XT_ALIGN(sizeof(struct xt_qosconnmark_tginfo1)),
	.userspacesize = XT_ALIGN(sizeof(struct xt_qosconnmark_tginfo1)),
	.help          = qosconnmark_tg_help,
	.init          = qosconnmark_tg_init,
	.print         = qosconnmark_tg_print,
	.save          = qosconnmark_tg_save,
	.x6_parse      = qosconnmark_tg_parse,
	.x6_fcheck     = qosconnmark_tg_check,
	.x6_options    = qosconnmark_tg_opts,
};

static void __attribute__((constructor)) xt_init(void)
{
	xtables_register_target(&qosconnmark_target);
}
