// SPDX-License-Identifier: GPL-2.0
/*
 * ASK 2.0 - xfrm subsystem (PR1 stub)
 *
 * Lifecycle hook only. Real implementation lands in a later PR per
 * plans/ASK-2.0-IMPLEMENTATION.md.
 */

#include <linux/kernel.h>
#include "include/ask_internal.h"

int ask_xfrm_init(void)
{
	ask_pr_dbg("xfrm: init (stub)\n");
	return 0;
}

void ask_xfrm_exit(void)
{
	ask_pr_dbg("xfrm: exit (stub)\n");
}
