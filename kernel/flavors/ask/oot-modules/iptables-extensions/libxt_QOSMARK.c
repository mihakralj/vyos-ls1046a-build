/* Shared library add-on to iptables to add QOSMARK target support. */
#include <stdbool.h>
#include <stdio.h>
#include <errno.h>
#include <inttypes.h>
#include <string.h>
#include <stdlib.h>

#include <xtables.h>
#include <linux/netfilter/x_tables.h>
#include <linux/netfilter/xt_QOSMARK.h>

enum {
	O_SET_XMARK = 0,
	O_SET_MARK,
	O_AND_MARK,
	O_OR_MARK,
	O_XOR_MARK,
	F_ANY = (1 << O_SET_XMARK) | (1 << O_SET_MARK) | (1 << O_AND_MARK) |
	        (1 << O_OR_MARK) | (1 << O_XOR_MARK),
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

static void qosmark_tg_help(void)
{
	printf(
"QOSMARK target options:\n"
"  --set-xmark value[/mask]  Clear bits in mask and XOR value into qosmark\n"
"  --set-mark value[/mask]   Clear bits in mask and OR value into qosmark\n"
"  --and-mark bits           Binary AND the qosmark with bits\n"
"  --or-mark bits            Binary OR the qosmark with bits\n"
"  --xor-mask bits           Binary XOR the qosmark with bits\n"
"\n");
}

static const struct xt_option_entry qosmark_tg_opts[] = {
	{.name = "set-xmark", .id = O_SET_XMARK, .type = XTTYPE_STRING,
	 .excl = F_ANY},
	{.name = "set-mark", .id = O_SET_MARK, .type = XTTYPE_STRING,
	 .excl = F_ANY},
	{.name = "and-mark", .id = O_AND_MARK, .type = XTTYPE_STRING,
	 .excl = F_ANY},
	{.name = "or-mark", .id = O_OR_MARK, .type = XTTYPE_STRING,
	 .excl = F_ANY},
	{.name = "xor-mark", .id = O_XOR_MARK, .type = XTTYPE_STRING,
	 .excl = F_ANY},
	XTOPT_TABLEEND,
};

static void qosmark_tg_parse(struct xt_option_call *cb)
{
	struct xt_qosmark_tginfo2 *info = cb->data;
	uint64_t value = 0, mask = UINT64_MAX;
	char *end = NULL;

	xtables_option_parse(cb);

	switch (cb->entry->id) {
	case O_SET_XMARK:
	case O_SET_MARK:
		if (!parse64(cb->arg, &end, &value))
			xtables_param_act(XTF_BAD_VALUE, "QOSMARK",
			                  "--set-xmark/--set-mark", cb->arg);
		if (*end == '/')
			if (!parse64(end + 1, &end, &mask))
				xtables_param_act(XTF_BAD_VALUE, "QOSMARK",
				                  "--set-xmark/--set-mark", cb->arg);
		if (*end != '\0')
			xtables_param_act(XTF_BAD_VALUE, "QOSMARK",
			                  "--set-xmark/--set-mark", cb->arg);
		info->mark = value;
		info->mask = mask;
		if (cb->entry->id == O_SET_MARK)
			info->mask = value | mask;
		break;

	case O_AND_MARK:
		if (!parse64(cb->arg, NULL, &mask))
			xtables_param_act(XTF_BAD_VALUE, "QOSMARK",
			                  "--and-mark", cb->arg);
		info->mark = 0;
		info->mask = ~mask;
		break;

	case O_OR_MARK:
		if (!parse64(cb->arg, NULL, &value))
			xtables_param_act(XTF_BAD_VALUE, "QOSMARK",
			                  "--or-mark", cb->arg);
		info->mark = value;
		info->mask = value;
		break;

	case O_XOR_MARK:
		if (!parse64(cb->arg, NULL, &value))
			xtables_param_act(XTF_BAD_VALUE, "QOSMARK",
			                  "--xor-mark", cb->arg);
		info->mark = value;
		info->mask = 0;
		break;
	}
}

static void qosmark_tg_check(struct xt_fcheck_call *cb)
{
	if (cb->xflags == 0)
		xtables_error(PARAMETER_PROBLEM, "QOSMARK: One of the --set-xmark, "
		           "--{and,or,xor,set}-mark options is required");
}

static void qosmark_tg_print(const void *ip, const struct xt_entry_target *target,
                          int numeric)
{
	const struct xt_qosmark_tginfo2 *info = (const void *)target->data;

	if (info->mark == 0)
		printf(" QOSMARK and 0x%" PRIx64, (uint64_t)~info->mask);
	else if (info->mark == info->mask)
		printf(" QOSMARK or 0x%" PRIx64, (uint64_t)info->mark);
	else if (info->mask == 0)
		printf(" QOSMARK xor 0x%" PRIx64, (uint64_t)info->mark);
	else if (info->mask == UINT64_MAX)
		printf(" QOSMARK set 0x%" PRIx64, (uint64_t)info->mark);
	else
		printf(" QOSMARK xset 0x%" PRIx64 "/0x%" PRIx64, (uint64_t)info->mark, (uint64_t)info->mask);
}

static void qosmark_tg_save(const void *ip, const struct xt_entry_target *target)
{
	const struct xt_qosmark_tginfo2 *info = (const void *)target->data;

	printf(" --set-xmark 0x%" PRIx64 "/0x%" PRIx64, (uint64_t)info->mark, (uint64_t)info->mask);
}

static struct xtables_target qosmark_target = {
	.version       = XTABLES_VERSION,
	.name          = "QOSMARK",
	.revision      = 2,
	.family        = NFPROTO_UNSPEC,
	.size          = XT_ALIGN(sizeof(struct xt_qosmark_tginfo2)),
	.userspacesize = XT_ALIGN(sizeof(struct xt_qosmark_tginfo2)),
	.help          = qosmark_tg_help,
	.print         = qosmark_tg_print,
	.save          = qosmark_tg_save,
	.x6_parse      = qosmark_tg_parse,
	.x6_fcheck     = qosmark_tg_check,
	.x6_options    = qosmark_tg_opts,
};

static void __attribute__((constructor)) xt_init(void)
{
	xtables_register_target(&qosmark_target);
}
