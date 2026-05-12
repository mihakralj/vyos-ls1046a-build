// SPDX-License-Identifier: GPL-2.0
/*
 * ASK 2.0 - flow_offload subsystem (PR1 stub)
 *
 * Lifecycle hook only. Real implementation lands in a later PR per
 * plans/ASK-2.0-IMPLEMENTATION.md.
 */

#include <linux/kernel.h>
#include "include/ask_internal.h"

int ask_flow_offload_init(void)
{
	ask_pr_dbg("flow_offload: init (stub)\n");
	return 0;
}

void ask_flow_offload_exit(void)
{
	ask_pr_dbg("flow_offload: exit (stub)\n");
}
