/* Shared library add-on to iptables to add qosmark matching support. */
#include <stdbool.h>
#include <stdio.h>
#include <errno.h>
#include <inttypes.h>
#include <string.h>
#include <stdlib.h>

#include <xtables.h>
#include <linux/netfilter/xt_qosmark.h>

enum {
	O_MARK = 0,
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

static void qosmark_mt_help(void)
{
	printf(
"qosmark match options:\n"
"[!] --mark value[/mask]    Match qosmark value with optional mask\n");
}

static const struct xt_option_entry qosmark_mt_opts[] = {
	{.name = "mark", .id = O_MARK, .type = XTTYPE_STRING,
	 .flags = XTOPT_INVERT | XTOPT_MAND},
	XTOPT_TABLEEND,
};

static void qosmark_mt_parse(struct xt_option_call *cb)
{
	struct xt_qosmark_mtinfo1 *info = cb->data;
	uint64_t mark = 0, mask = UINT64_MAX;
	char *end = NULL;

	xtables_option_parse(cb);

	switch (cb->entry->id) {
	case O_MARK:
		if (!parse64(cb->arg, &end, &mark))
			xtables_param_act(XTF_BAD_VALUE, "qosmark",
			                  "--mark", cb->arg);
		if (*end == '/')
			if (!parse64(end + 1, &end, &mask))
				xtables_param_act(XTF_BAD_VALUE, "qosmark",
				                  "--mark", cb->arg);
		if (*end != '\0')
			xtables_param_act(XTF_BAD_VALUE, "qosmark",
			                  "--mark", cb->arg);
		if (cb->invert)
			info->invert = true;
		info->mark = mark;
		info->mask = mask;
		break;
	}
}

static void print_mark(uint64_t mark, uint64_t mask)
{
	if (mask != UINT64_MAX)
		printf("0x%" PRIx64 "/0x%" PRIx64 " ", mark, mask);
	else
		printf("0x%" PRIx64 " ", mark);
}

static void qosmark_mt_check(struct xt_fcheck_call *cb)
{
	if (cb->xflags == 0)
		xtables_error(PARAMETER_PROBLEM,
			   "qosmark match: The --mark option is required");
}

static void
qosmark_mt_print(const void *ip, const struct xt_entry_match *match, int numeric)
{
	const struct xt_qosmark_mtinfo1 *info = (const void *)match->data;

	printf(" qosmark match ");
	if (info->invert)
		printf("!");
	print_mark((uint64_t)info->mark, (uint64_t)info->mask);
}

static void qosmark_mt_save(const void *ip, const struct xt_entry_match *match)
{
	const struct xt_qosmark_mtinfo1 *info = (const void *)match->data;

	if (info->invert)
		printf(" !");

	printf(" --mark ");
	print_mark((uint64_t)info->mark, (uint64_t)info->mask);
}

static struct xtables_match qosmark_match = {
	.version       = XTABLES_VERSION,
	.name          = "qosmark",
	.revision      = 1,
	.family        = NFPROTO_UNSPEC,
	.size          = XT_ALIGN(sizeof(struct xt_qosmark_mtinfo1)),
	.userspacesize = XT_ALIGN(sizeof(struct xt_qosmark_mtinfo1)),
	.help          = qosmark_mt_help,
	.print         = qosmark_mt_print,
	.save          = qosmark_mt_save,
	.x6_parse      = qosmark_mt_parse,
	.x6_fcheck     = qosmark_mt_check,
	.x6_options    = qosmark_mt_opts,
};

static void __attribute__((constructor)) xt_init(void)
{
	xtables_register_match(&qosmark_match);
}
