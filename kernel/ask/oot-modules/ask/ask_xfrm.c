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

/*
 * T-M6-IP1 eligibility (plans/ASK2-IPSEC-OFFLOAD-PLAN.md section 3a, step 8):
 * IPv4/IPv6 tunnel-mode ESP with AES-CBC + HMAC-SHA256 only. AEAD (GCM/CCM)
 * is refused for the CAAM A24a erratum. Returns 0 if eligible, else -errno.
 */
int ask_xfrm_state_eligible(const struct xfrm_state *x)
{
	const struct xfrm_algo *ealg = x->ealg;
	const struct xfrm_algo_auth *aalg = x->aalg;

	if (x->id.proto != IPPROTO_ESP)
		return -EOPNOTSUPP;
	if (x->props.mode != XFRM_MODE_TUNNEL)
		return -EOPNOTSUPP;
	if (x->props.family != AF_INET && x->props.family != AF_INET6)
		return -EAFNOSUPPORT;
	if (x->aead || !ealg || !aalg)
		return -EOPNOTSUPP;
	if (strcmp(ealg->alg_name, "cbc(aes)"))
		return -EOPNOTSUPP;
	if (strcmp(aalg->alg_name, "hmac(sha256)") ||
	    aalg->alg_trunc_len != 128)
		return -EOPNOTSUPP;
	if (x->encap || x->props.flags & XFRM_STATE_ESN)
		return -EOPNOTSUPP; /* NAT-T and ESN land with the IP2 gates */
	if (x->xso.flags & XFRM_DEV_OFFLOAD_FLAG_ACQ)
		return -EOPNOTSUPP;
	return 0;
}

int ask_xfrm_state_add(struct xfrm_state *x)
{
	int rc = ask_xfrm_state_eligible(x);

	if (rc)
		return rc;
	/* The CAAM descriptor and FMan record are not wired yet: fail closed. */
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
