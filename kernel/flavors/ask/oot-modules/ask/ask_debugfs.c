// SPDX-License-Identifier: GPL-2.0
/*
 * ASK 2.0 - debugfs subsystem (PR1 stub)
 *
 * Lifecycle hook only. Real implementation lands in a later PR per
 * plans/ASK-2.0-IMPLEMENTATION.md.
 */

#include <linux/kernel.h>
#include "include/ask_internal.h"

int ask_debugfs_init(void)
{
	ask_pr_dbg("debugfs: init (stub)\n");
	return 0;
}

void ask_debugfs_exit(void)
{
	ask_pr_dbg("debugfs: exit (stub)\n");
}
