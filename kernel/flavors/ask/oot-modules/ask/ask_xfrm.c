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
	/* 
	 * Security Hardening: Refuse GCM/GMAC offload due to "A24a wire-seq dupes".
	 * The CAAM hardware produces duplicate sequence numbers on the wire for GCM,
	 * causing anti-replay validation failures at the peer.
	 * We return -EOPNOTSUPP so the kernel gracefully falls back to the software path.
	 */
	if (x->aead && !strcmp(x->aead->alg_name, "rfc4106(gcm(aes))")) {
		ask_pr_dbg("xfrm: refusing GCM offload due to hardware wire-seq dupe bug\n");
		return -EOPNOTSUPP;
	}

	return 0; /* Stub implementation for other algos */
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
