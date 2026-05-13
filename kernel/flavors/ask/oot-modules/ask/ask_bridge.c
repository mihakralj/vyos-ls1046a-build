// SPDX-License-Identifier: GPL-2.0
/*
 * ASK2 - bridge subsystem (PR1 stub)
 *
 * Lifecycle hook only. Real implementation lands in a later PR per
 * plans/ASK2-IMPLEMENTATION.md.
 */

#include <linux/kernel.h>
#include "include/ask_internal.h"

int ask_bridge_init(void)
{
	ask_pr_dbg("bridge: init (stub)\n");
	return 0;
}

void ask_bridge_exit(void)
{
	ask_pr_dbg("bridge: exit (stub)\n");
}
