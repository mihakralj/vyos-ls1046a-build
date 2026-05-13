// SPDX-License-Identifier: GPL-2.0
/*
 * ASK2 - op subsystem (PR1 stub)
 *
 * Lifecycle hook only. Real implementation lands in a later PR per
 * plans/ASK2-IMPLEMENTATION.md.
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
