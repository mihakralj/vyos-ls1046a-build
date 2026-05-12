// SPDX-License-Identifier: GPL-2.0
/*
 * ASK 2.0 - hostcmd subsystem (PR1 stub)
 *
 * Lifecycle hook only. Real implementation lands in a later PR per
 * plans/ASK-2.0-IMPLEMENTATION.md.
 */

#include <linux/kernel.h>
#include "include/ask_internal.h"

int ask_hostcmd_init(void)
{
	ask_pr_dbg("hostcmd: init (stub)\n");
	return 0;
}

void ask_hostcmd_exit(void)
{
	ask_pr_dbg("hostcmd: exit (stub)\n");
}
