// SPDX-License-Identifier: GPL-2.0
/*
 * ASK2 - neigh subsystem (PR1 stub)
 *
 * Lifecycle hook only. Real implementation lands in a later PR per
 * plans/ASK2-IMPLEMENTATION.md.
 */

#include <linux/kernel.h>
#include "include/ask_internal.h"

int ask_neigh_init(void)
{
	ask_pr_dbg("neigh: init (stub)\n");
	return 0;
}

void ask_neigh_exit(void)
{
	ask_pr_dbg("neigh: exit (stub)\n");
}
