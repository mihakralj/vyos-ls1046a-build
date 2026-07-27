// SPDX-License-Identifier: GPL-2.0
/*
 * ASK2 - xfrm subsystem (PR1 stub)
 *
 * Lifecycle hook only. Real implementation lands in a later PR per
 * plans/ASK2-IMPLEMENTATION.md.
 */

#include <linux/kernel.h>
#include <net/xfrm.h>
#include "include/ask_internal.h"

int ask_xfrm_state_add(struct xfrm_state *x)
{
	(void)x;
	/* XFRM offload programming is not implemented yet: fail closed. */
	return -EOPNOTSUPP;
}

int ask_xfrm_init(void)
{
	ask_pr_dbg("xfrm: init (stub)\n");
	return 0;
}

void ask_xfrm_exit(void)
{
	ask_pr_dbg("xfrm: exit (stub)\n");
}
