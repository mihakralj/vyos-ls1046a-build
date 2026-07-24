// SPDX-License-Identifier: GPL-2.0
/*
 * ASK2 - op subsystem (lifecycle hook only)
 *
 * The operator-facing netlink receiver is NOT here: it is the generic
 * netlink family in ask_genl.c, which serves the query surface defined by
 * kernel/flavors/ask/uapi/ask.yaml (get-info, dump-flows, get-flow,
 * flush-flows, engage, disengage). Op-mode `show interfaces ethernet
 * eth<n> offload ask flows` drives ASK_CMD_DUMP_FLOWS there;
 * ask_genl_fill_one_flow() emits the full 5-tuple + iif/oif + stats
 * (T-M7-2). This TU keeps only the module init/exit lifecycle hooks.
 */

#include <linux/kernel.h>
#include "include/ask_internal.h"

int ask_op_init(void)
{
	ask_pr_dbg("op: init (stub)\n");
	return 0;
}

void ask_op_exit(void)
{
	ask_pr_dbg("op: exit (stub)\n");
}
