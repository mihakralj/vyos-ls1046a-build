// SPDX-License-Identifier: (GPL-2.0 OR BSD-3-Clause)
/*
 * Copyright 2008 - 2016 Freescale Semiconductor, Inc.
 * Copyright 2026 NXP
 *
 * QMan CEETM (Customer Edge Egress Traffic Management) hierarchical egress
 * shaper support.
 *
 * Ported from the legacy NXP SDK (qman_high.c CEETM section) and modernised to
 * mainline kernel style: the SDK big/little-endian bitfield wire structures
 * have been replaced with explicit __beN fields and byte arrays (see
 * qman_priv.h), and all management commands are issued through the single
 * generic transport helper qman_ceetm_mc_exec() in qman.c rather than touching
 * software-portal internals directly.
 *
 * This v1 implements strict-priority class scheduling, LNI/channel dual-rate
 * shapers and byte/frame tail-drop congestion groups. WBFS weighted groups,
 * CSCN congestion-state-change notifications and traffic-class flow-control
 * (tcfc) from the SDK are intentionally not ported (see 0111-NOTES.md).
 *
 * Hardware resources are hard-coded for the LS1046A CEETM0 instance (single
 * FMan -> DCP0): LFQID base 0xF00000 count 0x1000, 32 channels, 8 LNIs and 16
 * sub-portals, all on qm_dc_portal_fman0.
 */

#include <linux/kernel.h>
#include <linux/module.h>
#include <linux/slab.h>
#include <linux/spinlock.h>
#include <linux/bitmap.h>
#include <linux/math64.h>
#include <linux/string.h>

#include "qman_priv.h"

/* LS1046A CEETM0 hard-coded resource ranges (single FMan -> DCP0) */
#define CEETM_LFQID_BASE	0xF00000
#define CEETM_LFQID_COUNT	0x400
#define CEETM_LFQID_LSB_MASK	0x0003FF
#define CEETM_NR_CHANNELS	8
#define CEETM_NR_LNIS		8
#define CEETM_NR_SPS		16

/*
 * ROUNDING(n, d, r): divide n by d with rounding direction r (negative: down,
 * positive: up, zero: nearest). Ported verbatim from the SDK.
 */
#define ROUNDING(n, d, r) \
	(((r) < 0) ? div64_u64((n), (d)) : \
	(((r) > 0) ? div64_u64(((n) + (d) - 1), (d)) : \
	div64_u64(((n) + ((d) / 2)), (d))))

/* Helpers for the explicit big-endian byte-array wire fields */
static inline void be24_set(u8 *p, u32 v)
{
	p[0] = (v >> 16) & 0xff;
	p[1] = (v >> 8) & 0xff;
	p[2] = v & 0xff;
}

static inline u32 be24_get(const u8 *p)
{
	return ((u32)p[0] << 16) | ((u32)p[1] << 8) | p[2];
}

static inline u64 be40_get(const u8 *p)
{
	return ((u64)p[0] << 32) | ((u64)p[1] << 24) | ((u64)p[2] << 16) |
	       ((u64)p[3] << 8) | p[4];
}

static inline u64 be48_get(const u8 *p)
{
	return ((u64)p[0] << 40) | ((u64)p[1] << 32) | ((u64)p[2] << 24) |
	       ((u64)p[3] << 16) | ((u64)p[4] << 8) | p[5];
}

/* Simple bitmap allocators (DCP0 only) */
static DEFINE_SPINLOCK(ceetm_lock);
static DECLARE_BITMAP(ceetm_lfqid_map, CEETM_LFQID_COUNT);
static DECLARE_BITMAP(ceetm_channel_map, CEETM_NR_CHANNELS);
static DECLARE_BITMAP(ceetm_lni_map, CEETM_NR_LNIS);
static DECLARE_BITMAP(ceetm_sp_map, CEETM_NR_SPS);

static int ceetm_bitmap_claim(unsigned long *map, unsigned int nr,
			      unsigned int idx)
{
	int ret = -EBUSY;

	if (idx >= nr)
		return -EINVAL;
	spin_lock(&ceetm_lock);
	if (!test_bit(idx, map)) {
		set_bit(idx, map);
		ret = 0;
	}
	spin_unlock(&ceetm_lock);
	return ret;
}

static int ceetm_bitmap_alloc(unsigned long *map, unsigned int nr)
{
	unsigned int idx;

	spin_lock(&ceetm_lock);
	idx = find_next_zero_bit(map, nr, 2); /* skip idx 0 (cqid 0x000/lfqid-base/dctidx 0 = HW null set) and idx 1 (channel idx>=2 is the HW-validated allocation floor for leaves and the Option-B default; DEFECT B itself is closed as an LS1046A 8-channel CEETM silicon limitation, see 0112) */
	if (idx < nr)
		set_bit(idx, map);
	spin_unlock(&ceetm_lock);
	return (idx < nr) ? (int)idx : -ENOSPC;
}

static void ceetm_bitmap_free(unsigned long *map, unsigned int idx)
{
	spin_lock(&ceetm_lock);
	clear_bit(idx, map);
	spin_unlock(&ceetm_lock);
}

/* ----------------------------------------------------------------------
 * Management-command wrappers (all go through qman_ceetm_mc_exec())
 * ---------------------------------------------------------------------- */

static int ceetm_configure_lfqmt(struct qm_mcc_ceetm_lfqmt_config *opts)
{
	return qman_ceetm_mc_exec(QM_CEETM_VERB_LFQMT_CONFIG, opts,
				  sizeof(*opts), NULL, 0);
}

static int ceetm_configure_cq(struct qm_mcc_ceetm_cq_config *opts)
{
	return qman_ceetm_mc_exec(QM_CEETM_VERB_CQ_CONFIG, opts,
				  sizeof(*opts), NULL, 0);
}

static int ceetm_configure_dct(struct qm_mcc_ceetm_dct_config *opts)
{
	return qman_ceetm_mc_exec(QM_CEETM_VERB_DCT_CONFIG, opts,
				  sizeof(*opts), NULL, 0);
}

static int ceetm_configure_csch(struct qm_mcc_ceetm_class_scheduler_config *o)
{
	return qman_ceetm_mc_exec(QM_CEETM_VERB_CLASS_SCHEDULER_CONFIG, o,
				  sizeof(*o), NULL, 0);
}

static int ceetm_query_csch(struct qm_ceetm_channel *channel,
			    struct qm_mcr_ceetm_class_scheduler_query *res)
{
	struct qm_mcc_ceetm_class_scheduler_query q = {0};

	q.cqcid = cpu_to_be16(channel->idx);
	q.dcpid = channel->dcp_idx;
	return qman_ceetm_mc_exec(QM_CEETM_VERB_CLASS_SCHEDULER_QUERY, &q,
				  sizeof(q), res, sizeof(*res));
}

static int ceetm_configure_mst(struct qm_mcc_ceetm_mapping_shaper_tcfc_config *o)
{
	return qman_ceetm_mc_exec(QM_CEETM_VERB_MAPPING_SHAPER_TCFC_CONFIG, o,
				  sizeof(*o), NULL, 0);
}

static int ceetm_query_mst(u16 cid, u8 dcpid,
			   struct qm_mcr_ceetm_mapping_shaper_tcfc_query *res)
{
	struct qm_mcc_ceetm_mapping_shaper_tcfc_query q = {0};

	q.cid = cpu_to_be16(cid);
	q.dcpid = dcpid;
	return qman_ceetm_mc_exec(QM_CEETM_VERB_MAPPING_SHAPER_TCFC_QUERY, &q,
				  sizeof(q), res, sizeof(*res));
}

static int ceetm_configure_ccgr(struct qm_mcc_ceetm_ccgr_config *opts)
{
	return qman_ceetm_mc_exec(QM_CEETM_VERB_CCGR_CONFIG, opts,
				  sizeof(*opts), NULL, 0);
}

static int ceetm_cq_peek_pop_xsfdrread(struct qm_ceetm_cq *cq, u8 ct,
			struct qm_mcr_ceetm_cq_peek_pop_xsfdrread *res)
{
	struct qm_mcc_ceetm_cq_peek_pop_xsfdrread q = {0};

	/*
	 * NOTE: matching the silicon-proven SDK exactly, the cqid field of this
	 * command is written in native (little-endian on LS1046A) order, not
	 * byte-swapped like the other CEETM commands.
	 */
	q.cqid = cpu_to_le16((cq->parent->idx << 4) | cq->idx);
	q.ct = ct;
	q.dcpid = cq->parent->dcp_idx;
	return qman_ceetm_mc_exec(QM_CEETM_VERB_CQ_PEEK_POP_XFDRREAD, &q,
				  sizeof(q), res, sizeof(*res));
}

static int ceetm_query_statistics(u16 cid, u8 dcpid, u8 ct,
				  struct qm_mcr_ceetm_statistics_query *res)
{
	struct qm_mcc_ceetm_statistics_query_write q = {0};

	q.cid = cpu_to_be16(cid);
	q.dcpid = dcpid;
	q.ct = ct;
	return qman_ceetm_mc_exec(QM_CEETM_VERB_STATISTICS_QUERY_WRITE, &q,
				  sizeof(q), res, sizeof(*res));
}

static int ceetm_query_cq(struct qm_ceetm_cq *cq,
			  struct qm_mcr_ceetm_cq_query *res)
{
	struct qm_mcc_ceetm_cq_query q = {0};

	q.cqid = cpu_to_be16((cq->parent->idx << 4) | cq->idx);
	q.dcpid = cq->parent->dcp_idx;
	return qman_ceetm_mc_exec(QM_CEETM_VERB_CQ_QUERY, &q, sizeof(q),
				  res, sizeof(*res));
}

/* ----------------------------------------------------------------------
 * bps <-> token-rate conversion
 * ---------------------------------------------------------------------- */

int qman_ceetm_bps2tokenrate(u64 bps, struct qm_ceetm_rate *token_rate,
			     u16 rounding)
{
	u16 pres;
	u64 temp;
	int ret;

	/* Compile-time check that the wire command/result layouts are exact */
	BUILD_BUG_ON(sizeof(struct qm_mcc_ceetm_lfqmt_config) != 63);
	BUILD_BUG_ON(sizeof(struct qm_mcc_ceetm_cq_config) != 63);
	BUILD_BUG_ON(sizeof(struct qm_mcc_ceetm_dct_config) != 63);
	BUILD_BUG_ON(sizeof(struct qm_mcc_ceetm_class_scheduler_config) != 63);
	BUILD_BUG_ON(sizeof(struct qm_mcr_ceetm_class_scheduler_query) != 64);
	BUILD_BUG_ON(sizeof(struct qm_mcc_ceetm_mapping_shaper_tcfc_config) != 63);
	BUILD_BUG_ON(sizeof(struct qm_mcr_ceetm_mapping_shaper_tcfc_query) != 64);
	BUILD_BUG_ON(sizeof(struct qm_mcc_ceetm_ccgr_config) != 63);
	BUILD_BUG_ON(sizeof(struct qm_mcc_ceetm_cq_peek_pop_xsfdrread) != 63);
	BUILD_BUG_ON(sizeof(struct qm_mcr_ceetm_cq_peek_pop_xsfdrread) != 64);
	BUILD_BUG_ON(sizeof(struct qm_mcc_ceetm_statistics_query_write) != 63);
	BUILD_BUG_ON(sizeof(struct qm_mcr_ceetm_statistics_query) != 64);
	BUILD_BUG_ON(sizeof(struct qm_mcc_ceetm_cq_query) != 63);
	BUILD_BUG_ON(sizeof(struct qm_mcr_ceetm_cq_query) != 64);

	ret = qman_ceetm_get_prescaler(&pres);
	if (ret)
		return ret;
	if (!qman_clk)
		return -EINVAL;

	/*
	 *	N = (((bps*2^16)/PRES)*2^16)/QHz
	 * Two 16-bit shifts (instead of one 32-bit) keep the math overflow-safe
	 * for rates up to and beyond 10Gbps.
	 */
	temp = ROUNDING((bps << 16), pres, rounding);
	temp = ROUNDING((temp << 16), qman_clk, rounding);
	token_rate->whole = temp >> 13;
	token_rate->fraction = temp & (((u64)1 << 13) - 1);
	return 0;
}
EXPORT_SYMBOL_GPL(qman_ceetm_bps2tokenrate);

int qman_ceetm_tokenrate2bps(const struct qm_ceetm_rate *token_rate, u64 *bps,
			     u16 rounding)
{
	u16 pres;
	u64 temp;
	int ret;

	ret = qman_ceetm_get_prescaler(&pres);
	if (ret)
		return ret;
	if (!qman_clk)
		return -EINVAL;

	/*
	 *	bps = N*PRES*QHZ / (2^32)
	 * Split into two /2^16 steps to avoid 64-bit overflow.
	 */
	temp = ROUNDING((u64)qman_clk * pres, (u64)1 << 16, rounding);
	temp *= (((u64)token_rate->whole << 13) + token_rate->fraction);
	*bps = ROUNDING(temp, (u64)1 << 16, rounding);
	return 0;
}
EXPORT_SYMBOL_GPL(qman_ceetm_tokenrate2bps);

/* ----------------------------------------------------------------------
 * Sub-portals
 * ---------------------------------------------------------------------- */

int qman_ceetm_sp_claim(struct qm_ceetm_sp **sp, enum qm_dc_portal dcp_idx,
			unsigned int sp_idx)
{
	struct qm_ceetm_sp *p;
	int ret;

	if (dcp_idx != qm_dc_portal_fman0)
		return -EINVAL;

	ret = ceetm_bitmap_claim(ceetm_sp_map, CEETM_NR_SPS, sp_idx);
	if (ret) {
		pr_err("CEETM: sub-portal %u not available\n", sp_idx);
		return ret;
	}

	p = kzalloc(sizeof(*p), GFP_KERNEL);
	if (!p) {
		ceetm_bitmap_free(ceetm_sp_map, sp_idx);
		return -ENOMEM;
	}
	p->idx = sp_idx;
	p->dcp_idx = dcp_idx;
	p->is_claimed = 1;
	*sp = p;
	return 0;
}
EXPORT_SYMBOL_GPL(qman_ceetm_sp_claim);

int qman_ceetm_sp_release(struct qm_ceetm_sp *sp)
{
	if (sp->lni && sp->lni->is_claimed) {
		pr_err("CEETM: sub-portal %u dependency not released\n",
		       sp->idx);
		return -EBUSY;
	}
	/* Disable CEETM mode of this sub-portal */
	qman_sp_disable_ceetm_mode(sp->dcp_idx, sp->idx);
	ceetm_bitmap_free(ceetm_sp_map, sp->idx);
	kfree(sp);
	return 0;
}
EXPORT_SYMBOL_GPL(qman_ceetm_sp_release);

/* ----------------------------------------------------------------------
 * Logical Network Interfaces
 * ---------------------------------------------------------------------- */

int qman_ceetm_lni_claim(struct qm_ceetm_lni **lni, enum qm_dc_portal dcp_idx,
			 unsigned int lni_idx)
{
	struct qm_ceetm_lni *p;
	int ret;

	if (dcp_idx != qm_dc_portal_fman0)
		return -EINVAL;

	ret = ceetm_bitmap_claim(ceetm_lni_map, CEETM_NR_LNIS, lni_idx);
	if (ret) {
		pr_err("CEETM: LNI %u not available\n", lni_idx);
		return ret;
	}

	p = kzalloc(sizeof(*p), GFP_KERNEL);
	if (!p) {
		ceetm_bitmap_free(ceetm_lni_map, lni_idx);
		return -ENOMEM;
	}
	p->idx = lni_idx;
	p->dcp_idx = dcp_idx;
	p->is_claimed = 1;
	INIT_LIST_HEAD(&p->channels);
	*lni = p;
	return 0;
}
EXPORT_SYMBOL_GPL(qman_ceetm_lni_claim);

int qman_ceetm_lni_release(struct qm_ceetm_lni *lni)
{
	struct qm_mcc_ceetm_mapping_shaper_tcfc_config opts = {0};

	if (!list_empty(&lni->channels)) {
		pr_err("CEETM: LNI %u dependencies not released\n", lni->idx);
		return -EBUSY;
	}

	/* Reset the LNI shaper record */
	opts.cid = cpu_to_be16(CEETM_COMMAND_LNI_SHAPER | lni->idx);
	opts.dcpid = lni->dcp_idx;
	ceetm_configure_mst(&opts);

	ceetm_bitmap_free(ceetm_lni_map, lni->idx);
	kfree(lni);
	return 0;
}
EXPORT_SYMBOL_GPL(qman_ceetm_lni_release);

int qman_ceetm_sp_set_lni(struct qm_ceetm_sp *sp, struct qm_ceetm_lni *lni)
{
	struct qm_mcc_ceetm_mapping_shaper_tcfc_config opts = {0};

	opts.cid = cpu_to_be16(CEETM_COMMAND_SP_MAPPING | sp->idx);
	opts.dcpid = sp->dcp_idx;
	opts.sp_mapping.map_ctl = lni->idx & CEETM_MAP_LNI_ID_MASK;
	sp->lni = lni;

	if (ceetm_configure_mst(&opts))
		return -EINVAL;

	/* Enable CEETM mode for this sub-portal */
	return qman_sp_enable_ceetm_mode(sp->dcp_idx, sp->idx);
}
EXPORT_SYMBOL_GPL(qman_ceetm_sp_set_lni);

int qman_ceetm_lni_enable_shaper(struct qm_ceetm_lni *lni, int coupled, int oal)
{
	struct qm_mcc_ceetm_mapping_shaper_tcfc_config opts = {0};

	if (lni->shaper_enable) {
		pr_err("CEETM: LNI %u shaper already enabled\n", lni->idx);
		return -EINVAL;
	}
	lni->shaper_enable = 1;
	lni->shaper_couple = coupled;
	lni->oal = oal;

	opts.cid = cpu_to_be16(CEETM_COMMAND_LNI_SHAPER | lni->idx);
	opts.dcpid = lni->dcp_idx;
	opts.shaper_config.ctl = (coupled ? CEETM_SHAPER_CPL : 0) |
				 (oal & CEETM_SHAPER_OAL_MASK);
	be24_set(opts.shaper_config.crtcr,
		 ((u32)lni->cr_token_rate.whole << 13) |
		 lni->cr_token_rate.fraction);
	be24_set(opts.shaper_config.ertcr,
		 ((u32)lni->er_token_rate.whole << 13) |
		 lni->er_token_rate.fraction);
	opts.shaper_config.crtbl = cpu_to_be16(lni->cr_token_bucket_limit);
	opts.shaper_config.ertbl = cpu_to_be16(lni->er_token_bucket_limit);
	/* Erratum A-010383: do not use both OAL and MPS on an LNI shaper */
	opts.shaper_config.mps = oal ? 0 : 60;

	return ceetm_configure_mst(&opts);
}
EXPORT_SYMBOL_GPL(qman_ceetm_lni_enable_shaper);

int qman_ceetm_lni_disable_shaper(struct qm_ceetm_lni *lni)
{
	struct qm_mcc_ceetm_mapping_shaper_tcfc_config opts = {0};

	if (!lni->shaper_enable) {
		pr_err("CEETM: LNI %u shaper already disabled\n", lni->idx);
		return -EINVAL;
	}

	opts.cid = cpu_to_be16(CEETM_COMMAND_LNI_SHAPER | lni->idx);
	opts.dcpid = lni->dcp_idx;
	opts.shaper_config.ctl = (lni->shaper_couple ? CEETM_SHAPER_CPL : 0) |
				 (lni->oal & CEETM_SHAPER_OAL_MASK);
	opts.shaper_config.crtbl = cpu_to_be16(lni->cr_token_bucket_limit);
	opts.shaper_config.ertbl = cpu_to_be16(lni->er_token_bucket_limit);
	/* All-ones token rate configures an infinite rate (shaping disabled) */
	be24_set(opts.shaper_config.crtcr, 0xFFFFFF);
	be24_set(opts.shaper_config.ertcr, 0xFFFFFF);
	opts.shaper_config.mps = lni->oal ? 0 : 60;

	lni->shaper_enable = 0;
	return ceetm_configure_mst(&opts);
}
EXPORT_SYMBOL_GPL(qman_ceetm_lni_disable_shaper);

static int lni_set_rate(struct qm_ceetm_lni *lni, bool commit,
			const struct qm_ceetm_rate *token_rate, u16 token_limit)
{
	struct qm_mcc_ceetm_mapping_shaper_tcfc_config opts = {0};
	struct qm_mcr_ceetm_mapping_shaper_tcfc_query res = {0};
	int ret;

	if (commit) {
		lni->cr_token_rate = *token_rate;
		lni->cr_token_bucket_limit = token_limit;
	} else {
		lni->er_token_rate = *token_rate;
		lni->er_token_bucket_limit = token_limit;
	}
	if (!lni->shaper_enable)
		return 0;

	ret = ceetm_query_mst(CEETM_COMMAND_LNI_SHAPER | lni->idx,
			      lni->dcp_idx, &res);
	if (ret) {
		pr_err("CEETM: failed to query LNI %u shaper\n", lni->idx);
		return ret;
	}

	opts.cid = cpu_to_be16(CEETM_COMMAND_LNI_SHAPER | lni->idx);
	opts.dcpid = lni->dcp_idx;
	opts.shaper_config.ctl = res.shaper_query.ctl;
	opts.shaper_config.mps = res.shaper_query.mps;
	if (commit) {
		be24_set(opts.shaper_config.crtcr,
			 ((u32)token_rate->whole << 13) | token_rate->fraction);
		opts.shaper_config.crtbl = cpu_to_be16(token_limit);
		memcpy(opts.shaper_config.ertcr, res.shaper_query.ertcr, 3);
		opts.shaper_config.ertbl = res.shaper_query.ertbl;
	} else {
		be24_set(opts.shaper_config.ertcr,
			 ((u32)token_rate->whole << 13) | token_rate->fraction);
		opts.shaper_config.ertbl = cpu_to_be16(token_limit);
		memcpy(opts.shaper_config.crtcr, res.shaper_query.crtcr, 3);
		opts.shaper_config.crtbl = res.shaper_query.crtbl;
	}
	return ceetm_configure_mst(&opts);
}

int qman_ceetm_lni_set_commit_rate(struct qm_ceetm_lni *lni,
				   const struct qm_ceetm_rate *token_rate,
				   u16 max_burst_size)
{
	return lni_set_rate(lni, true, token_rate, max_burst_size);
}
EXPORT_SYMBOL_GPL(qman_ceetm_lni_set_commit_rate);

int qman_ceetm_lni_set_excess_rate(struct qm_ceetm_lni *lni,
				   const struct qm_ceetm_rate *token_rate,
				   u16 max_burst_size)
{
	return lni_set_rate(lni, false, token_rate, max_burst_size);
}
EXPORT_SYMBOL_GPL(qman_ceetm_lni_set_excess_rate);

int qman_ceetm_lni_set_commit_rate_bps(struct qm_ceetm_lni *lni, u64 bps,
				       u16 max_burst_size)
{
	struct qm_ceetm_rate token_rate;
	int ret;

	ret = qman_ceetm_bps2tokenrate(bps, &token_rate, 0);
	if (ret)
		return ret;
	return lni_set_rate(lni, true, &token_rate, max_burst_size);
}
EXPORT_SYMBOL_GPL(qman_ceetm_lni_set_commit_rate_bps);

int qman_ceetm_lni_set_excess_rate_bps(struct qm_ceetm_lni *lni, u64 bps,
				       u16 max_burst_size)
{
	struct qm_ceetm_rate token_rate;
	int ret;

	ret = qman_ceetm_bps2tokenrate(bps, &token_rate, 0);
	if (ret)
		return ret;
	return lni_set_rate(lni, false, &token_rate, max_burst_size);
}
EXPORT_SYMBOL_GPL(qman_ceetm_lni_set_excess_rate_bps);

/* ----------------------------------------------------------------------
 * Class Queue Channels
 * ---------------------------------------------------------------------- */

int qman_ceetm_channel_claim(struct qm_ceetm_channel **channel,
			     struct qm_ceetm_lni *lni)
{
	struct qm_ceetm_channel *p;
	struct qm_mcc_ceetm_mapping_shaper_tcfc_config opts = {0};
	int idx;

	if (lni->dcp_idx != qm_dc_portal_fman0)
		return -EINVAL;

	idx = ceetm_bitmap_alloc(ceetm_channel_map, CEETM_NR_CHANNELS);
	if (idx < 0) {
		pr_err("CEETM: no channel available for LNI %u\n", lni->idx);
		return -ENODEV;
	}

	p = kzalloc(sizeof(*p), GFP_KERNEL);
	if (!p) {
		ceetm_bitmap_free(ceetm_channel_map, idx);
		return -ENOMEM;
	}
	p->idx = idx;
	p->dcp_idx = lni->dcp_idx;
	p->lni_idx = lni->idx;
	INIT_LIST_HEAD(&p->class_queues);
	INIT_LIST_HEAD(&p->ccgs);
	list_add_tail(&p->node, &lni->channels);

	opts.cid = cpu_to_be16(CEETM_COMMAND_CHANNEL_MAPPING | idx);
	opts.dcpid = lni->dcp_idx;
	opts.channel_mapping.map_ctl = CEETM_MAP_SHAPED |
				       (lni->idx & CEETM_MAP_LNI_ID_MASK);
	if (ceetm_configure_mst(&opts)) {
		pr_err("CEETM: can't map channel %d to LNI %u\n", idx,
		       lni->idx);
		list_del(&p->node);
		ceetm_bitmap_free(ceetm_channel_map, idx);
		kfree(p);
		return -EINVAL;
	}
	*channel = p;
	return 0;
}
EXPORT_SYMBOL_GPL(qman_ceetm_channel_claim);

int qman_ceetm_channel_release(struct qm_ceetm_channel *channel)
{
	struct qm_mcc_ceetm_mapping_shaper_tcfc_config opts = {0};

	if (!list_empty(&channel->class_queues)) {
		pr_err("CEETM: channel %u has unreleased class queues\n",
		       channel->idx);
		return -EBUSY;
	}
	if (!list_empty(&channel->ccgs)) {
		pr_err("CEETM: channel %u has unreleased CCGs\n", channel->idx);
		return -EBUSY;
	}

	/* Reset the channel shaper record */
	opts.cid = cpu_to_be16(CEETM_COMMAND_CHANNEL_SHAPER | channel->idx);
	opts.dcpid = channel->dcp_idx;
	if (ceetm_configure_mst(&opts)) {
		pr_err("CEETM: can't reset channel %u shaper\n", channel->idx);
		return -EINVAL;
	}

	ceetm_bitmap_free(ceetm_channel_map, channel->idx);
	list_del(&channel->node);
	kfree(channel);
	return 0;
}
EXPORT_SYMBOL_GPL(qman_ceetm_channel_release);

int qman_ceetm_channel_enable_shaper(struct qm_ceetm_channel *channel,
				     int coupled)
{
	struct qm_mcr_ceetm_mapping_shaper_tcfc_query res = {0};
	struct qm_mcc_ceetm_mapping_shaper_tcfc_config opts = {0};

	if (channel->shaper_enable) {
		pr_err("CEETM: channel %u shaper already enabled\n",
		       channel->idx);
		return -EINVAL;
	}
	channel->shaper_enable = 1;
	channel->shaper_couple = coupled;

	if (ceetm_query_mst(CEETM_COMMAND_CHANNEL_MAPPING | channel->idx,
			    channel->dcp_idx, &res)) {
		pr_err("CEETM: can't query channel %u mapping\n", channel->idx);
		return -EINVAL;
	}

	opts.cid = cpu_to_be16(CEETM_COMMAND_CHANNEL_MAPPING | channel->idx);
	opts.dcpid = channel->dcp_idx;
	opts.channel_mapping.map_ctl = CEETM_MAP_SHAPED |
		(res.channel_mapping_query.map_ctl & CEETM_MAP_LNI_ID_MASK);
	if (ceetm_configure_mst(&opts)) {
		pr_err("CEETM: can't enable shaper for channel %u\n",
		       channel->idx);
		return -EINVAL;
	}

	memset(&opts, 0, sizeof(opts));
	opts.cid = cpu_to_be16(CEETM_COMMAND_CHANNEL_SHAPER | channel->idx);
	opts.dcpid = channel->dcp_idx;
	opts.shaper_config.ctl = coupled ? CEETM_SHAPER_CPL : 0;
	be24_set(opts.shaper_config.crtcr,
		 ((u32)channel->cr_token_rate.whole << 13) |
		 channel->cr_token_rate.fraction);
	be24_set(opts.shaper_config.ertcr,
		 ((u32)channel->er_token_rate.whole << 13) |
		 channel->er_token_rate.fraction);
	opts.shaper_config.crtbl = cpu_to_be16(channel->cr_token_bucket_limit);
	opts.shaper_config.ertbl = cpu_to_be16(channel->er_token_bucket_limit);
	return ceetm_configure_mst(&opts);
}
EXPORT_SYMBOL_GPL(qman_ceetm_channel_enable_shaper);

int qman_ceetm_channel_disable_shaper(struct qm_ceetm_channel *channel)
{
	struct qm_mcr_ceetm_mapping_shaper_tcfc_query res = {0};
	struct qm_mcc_ceetm_mapping_shaper_tcfc_config opts = {0};

	if (ceetm_query_mst(CEETM_COMMAND_CHANNEL_MAPPING | channel->idx,
			    channel->dcp_idx, &res)) {
		pr_err("CEETM: can't query channel %u mapping\n", channel->idx);
		return -EINVAL;
	}

	opts.cid = cpu_to_be16(CEETM_COMMAND_CHANNEL_MAPPING | channel->idx);
	opts.dcpid = channel->dcp_idx;
	opts.channel_mapping.map_ctl =
		res.channel_mapping_query.map_ctl & CEETM_MAP_LNI_ID_MASK;
	channel->shaper_enable = 0;
	return ceetm_configure_mst(&opts);
}
EXPORT_SYMBOL_GPL(qman_ceetm_channel_disable_shaper);

static int channel_set_rate(struct qm_ceetm_channel *channel, bool commit,
			    const struct qm_ceetm_rate *token_rate,
			    u16 token_limit)
{
	struct qm_mcc_ceetm_mapping_shaper_tcfc_config opts = {0};
	struct qm_mcr_ceetm_mapping_shaper_tcfc_query res = {0};
	int ret;

	ret = ceetm_query_mst(CEETM_COMMAND_CHANNEL_SHAPER | channel->idx,
			      channel->dcp_idx, &res);
	if (ret) {
		pr_err("CEETM: can't query channel %u shaper\n", channel->idx);
		return ret;
	}

	if (commit) {
		channel->cr_token_rate = *token_rate;
		channel->cr_token_bucket_limit = token_limit;
	} else {
		channel->er_token_rate = *token_rate;
		channel->er_token_bucket_limit = token_limit;
	}

	opts.cid = cpu_to_be16(CEETM_COMMAND_CHANNEL_SHAPER | channel->idx);
	opts.dcpid = channel->dcp_idx;
	opts.shaper_config.ctl = res.shaper_query.ctl;
	if (commit) {
		be24_set(opts.shaper_config.crtcr,
			 ((u32)token_rate->whole << 13) | token_rate->fraction);
		opts.shaper_config.crtbl = cpu_to_be16(token_limit);
		memcpy(opts.shaper_config.ertcr, res.shaper_query.ertcr, 3);
		opts.shaper_config.ertbl = res.shaper_query.ertbl;
	} else {
		be24_set(opts.shaper_config.ertcr,
			 ((u32)token_rate->whole << 13) | token_rate->fraction);
		opts.shaper_config.ertbl = cpu_to_be16(token_limit);
		memcpy(opts.shaper_config.crtcr, res.shaper_query.crtcr, 3);
		opts.shaper_config.crtbl = res.shaper_query.crtbl;
	}
	return ceetm_configure_mst(&opts);
}

int qman_ceetm_channel_set_commit_rate(struct qm_ceetm_channel *channel,
				       const struct qm_ceetm_rate *token_rate,
				       u16 max_burst_size)
{
	return channel_set_rate(channel, true, token_rate, max_burst_size);
}
EXPORT_SYMBOL_GPL(qman_ceetm_channel_set_commit_rate);

int qman_ceetm_channel_set_excess_rate(struct qm_ceetm_channel *channel,
				       const struct qm_ceetm_rate *token_rate,
				       u16 max_burst_size)
{
	return channel_set_rate(channel, false, token_rate, max_burst_size);
}
EXPORT_SYMBOL_GPL(qman_ceetm_channel_set_excess_rate);

int qman_ceetm_channel_set_commit_rate_bps(struct qm_ceetm_channel *channel,
					   u64 bps, u16 max_burst_size)
{
	struct qm_ceetm_rate token_rate;
	int ret;

	ret = qman_ceetm_bps2tokenrate(bps, &token_rate, 0);
	if (ret)
		return ret;
	return channel_set_rate(channel, true, &token_rate, max_burst_size);
}
EXPORT_SYMBOL_GPL(qman_ceetm_channel_set_commit_rate_bps);

int qman_ceetm_channel_set_excess_rate_bps(struct qm_ceetm_channel *channel,
					   u64 bps, u16 max_burst_size)
{
	struct qm_ceetm_rate token_rate;
	int ret;

	ret = qman_ceetm_bps2tokenrate(bps, &token_rate, 0);
	if (ret)
		return ret;
	return channel_set_rate(channel, false, &token_rate, max_burst_size);
}
EXPORT_SYMBOL_GPL(qman_ceetm_channel_set_excess_rate_bps);

static int channel_set_cq_eligibility(struct qm_ceetm_channel *channel,
				      unsigned int cq_level, int set, bool commit)
{
	struct qm_mcc_ceetm_class_scheduler_config cfg = {0};
	struct qm_mcr_ceetm_class_scheduler_query res = {0};
	u16 mask;
	int i;

	if (cq_level > 7)
		return -EINVAL;
	if (ceetm_query_csch(channel, &res)) {
		pr_err("CEETM: can't query channel %u scheduler\n",
		       channel->idx);
		return -EINVAL;
	}

	cfg.cqcid = cpu_to_be16(channel->idx);
	cfg.dcpid = channel->dcp_idx;
	cfg.gpc = res.gpc;
	for (i = 0; i < 8; i++)
		cfg.w[i] = res.w[i];

	if (commit) {
		mask = be16_to_cpu(res.crem);
		mask = (mask & ~CQ_ELIGIBILITY_SET(cq_level)) |
		       (set ? CQ_ELIGIBILITY_SET(cq_level) : 0);
		cfg.crem = cpu_to_be16(mask);
		cfg.erem = res.erem;
	} else {
		mask = be16_to_cpu(res.erem);
		mask = (mask & ~CQ_ELIGIBILITY_SET(cq_level)) |
		       (set ? CQ_ELIGIBILITY_SET(cq_level) : 0);
		cfg.erem = cpu_to_be16(mask);
		cfg.crem = res.crem;
	}

	if (ceetm_configure_csch(&cfg)) {
		pr_err("CEETM: can't set CQ %u eligibility on channel %u\n",
		       cq_level, channel->idx);
		return -EINVAL;
	}
	return 0;
}

int qman_ceetm_channel_set_cq_cr_eligibility(struct qm_ceetm_channel *channel,
					     unsigned int cq_level, int set)
{
	return channel_set_cq_eligibility(channel, cq_level, set, true);
}
EXPORT_SYMBOL_GPL(qman_ceetm_channel_set_cq_cr_eligibility);

int qman_ceetm_channel_set_cq_er_eligibility(struct qm_ceetm_channel *channel,
					     unsigned int cq_level, int set)
{
	return channel_set_cq_eligibility(channel, cq_level, set, false);
}
EXPORT_SYMBOL_GPL(qman_ceetm_channel_set_cq_er_eligibility);

/* ----------------------------------------------------------------------
 * Class Queues
 * ---------------------------------------------------------------------- */

int qman_ceetm_cq_claim(struct qm_ceetm_cq **cq,
			struct qm_ceetm_channel *channel, unsigned int idx,
			struct qm_ceetm_ccg *ccg)
{
	struct qm_ceetm_cq *p;
	struct qm_mcc_ceetm_cq_config cfg = {0};

	if (idx > 7) {
		pr_err("CEETM: CQ index %u out of range (strict-prio 0-7)\n",
		       idx);
		return -EINVAL;
	}

	list_for_each_entry(p, &channel->class_queues, node) {
		if (p->idx == idx) {
			pr_err("CEETM: CQ %u already claimed\n", idx);
			return -EINVAL;
		}
	}

	p = kzalloc(sizeof(*p), GFP_KERNEL);
	if (!p)
		return -ENOMEM;
	p->idx = idx;
	p->is_claimed = 1;
	p->parent = channel;
	p->ccg = ccg;
	INIT_LIST_HEAD(&p->bound_lfqids);
	list_add_tail(&p->node, &channel->class_queues);

	if (ccg) {
		cfg.cqid = cpu_to_be16((channel->idx << 4) | idx);
		cfg.dcpid = channel->dcp_idx;
		cfg.ccgid = cpu_to_be16(ccg->idx);
		if (ceetm_configure_cq(&cfg)) {
			pr_err("CEETM: can't configure CQ %u with CCG %u\n",
			       idx, ccg->idx);
			list_del(&p->node);
			kfree(p);
			return -EINVAL;
		}
	}

	*cq = p;
	return 0;
}
EXPORT_SYMBOL_GPL(qman_ceetm_cq_claim);

int qman_ceetm_cq_release(struct qm_ceetm_cq *cq)
{
	if (!list_empty(&cq->bound_lfqids)) {
		pr_err("CEETM: CQ %u has unreleased LFQIDs\n", cq->idx);
		return -EBUSY;
	}
	list_del(&cq->node);
	qman_ceetm_drain_cq(cq);
	kfree(cq);
	return 0;
}
EXPORT_SYMBOL_GPL(qman_ceetm_cq_release);

int qman_ceetm_drain_cq(struct qm_ceetm_cq *cq)
{
	struct qm_mcr_ceetm_cq_peek_pop_xsfdrread ppxr;
	int ret;

	do {
		ret = ceetm_cq_peek_pop_xsfdrread(cq, 1, &ppxr);
		if (ret) {
			pr_err("CEETM: failed to pop frame from CQ %u\n",
			       cq->idx);
			return -EINVAL;
		}
	} while (!(ppxr.stat & 0x2));

	return 0;
}
EXPORT_SYMBOL_GPL(qman_ceetm_drain_cq);

int qman_ceetm_cq_get_dequeue_statistics(struct qm_ceetm_cq *cq, u32 flags,
					 u64 *frame_count, u64 *byte_count)
{
	struct qm_mcr_ceetm_statistics_query res;
	u8 ct;
	int ret;

	if (flags == QMAN_CEETM_FLAG_CLEAR_STATISTICS_COUNTER)
		ct = CEETM_QUERY_DEQUEUE_CLEAR_STATISTICS;
	else
		ct = CEETM_QUERY_DEQUEUE_STATISTICS;

	ret = ceetm_query_statistics((cq->parent->idx << 4) | cq->idx,
				     cq->parent->dcp_idx, ct, &res);
	if (ret)
		return ret;

	*frame_count = be40_get(res.frm_cnt);
	*byte_count = be48_get(res.byte_cnt);
	return 0;
}
EXPORT_SYMBOL_GPL(qman_ceetm_cq_get_dequeue_statistics);

/* ----------------------------------------------------------------------
 * Class Congestion Groups (tail-drop only in v1)
 * ---------------------------------------------------------------------- */

#define CEETM_MAX_CCG_IDX 0x0F

int qman_ceetm_ccg_claim(struct qm_ceetm_ccg **ccg,
			 struct qm_ceetm_channel *channel, unsigned int idx)
{
	struct qm_ceetm_ccg *p;

	if (idx > CEETM_MAX_CCG_IDX) {
		pr_err("CEETM: CCG index %u out of range\n", idx);
		return -EINVAL;
	}

	list_for_each_entry(p, &channel->ccgs, node) {
		if (p->idx == idx) {
			pr_err("CEETM: CCG %u already claimed\n", idx);
			return -EINVAL;
		}
	}

	p = kzalloc(sizeof(*p), GFP_KERNEL);
	if (!p)
		return -ENOMEM;
	p->idx = idx;
	p->parent = channel;
	list_add_tail(&p->node, &channel->ccgs);

	*ccg = p;
	return 0;
}
EXPORT_SYMBOL_GPL(qman_ceetm_ccg_claim);

int qman_ceetm_ccg_release(struct qm_ceetm_ccg *ccg)
{
	struct qm_mcc_ceetm_ccgr_config opts = {0};

	/* Disable tail-drop so a re-claimed CCG starts clean */
	opts.ccgrid = cpu_to_be16(CEETM_CCGR_CM_CONFIGURE |
				  (ccg->parent->idx << 4) | ccg->idx);
	opts.dcpid = ccg->parent->dcp_idx;
	opts.we_mask = cpu_to_be16(QM_CCGR_WE_TD_EN | QM_CCGR_WE_MODE);
	ceetm_configure_ccgr(&opts);

	list_del(&ccg->node);
	kfree(ccg);
	return 0;
}
EXPORT_SYMBOL_GPL(qman_ceetm_ccg_release);

int qman_ceetm_ccg_set(struct qm_ceetm_ccg *ccg, u16 we_mask,
		       const struct qm_ceetm_ccg_params *params)
{
	struct qm_mcc_ceetm_ccgr_config opts = {0};
	u8 ctl = 0;

	/*
	 * v1 supports tail-drop only: the CSCN congestion-state-change
	 * notification path of the SDK is not ported, so although the
	 * QM_CCGR_WE_CSCN_* write-enable bits and the ctl_cscn_en field are
	 * still honoured for the raw register write, no software-portal CSCN
	 * callback is registered.
	 */
	opts.ccgrid = cpu_to_be16(CEETM_CCGR_CM_CONFIGURE |
				  (ccg->parent->idx << 4) | ccg->idx);
	opts.dcpid = ccg->parent->dcp_idx;
	opts.we_mask = cpu_to_be16(we_mask);

	if (params->mode)
		ctl |= CEETM_CCGR_CTL_MODE;
	if (params->cscn_en)
		ctl |= CEETM_CCGR_CTL_CSCN_EN;
	if (params->td_mode)
		ctl |= CEETM_CCGR_CTL_TD_MODE;
	if (params->td_en)
		ctl |= CEETM_CCGR_CTL_TD_EN;
	if (params->wr_en_r)
		ctl |= CEETM_CCGR_CTL_WR_EN_R;
	if (params->wr_en_y)
		ctl |= CEETM_CCGR_CTL_WR_EN_Y;
	if (params->wr_en_g)
		ctl |= CEETM_CCGR_CTL_WR_EN_G;
	opts.cm_config.ctl = ctl;
	opts.cm_config.oal = params->oal;
	opts.cm_config.cs_thres = params->cs_thres_in;
	opts.cm_config.cs_thres_x = params->cs_thres_out;
	opts.cm_config.td_thres = params->td_thres;
	opts.cm_config.wr_parm_g = params->wr_parm_g;
	opts.cm_config.wr_parm_y = params->wr_parm_y;
	opts.cm_config.wr_parm_r = params->wr_parm_r;

	if (ceetm_configure_ccgr(&opts)) {
		pr_err("CEETM: configure CCG %u failed\n", ccg->idx);
		return -EIO;
	}
	return 0;
}
EXPORT_SYMBOL_GPL(qman_ceetm_ccg_set);

int qman_ceetm_ccg_get_reject_statistics(struct qm_ceetm_ccg *ccg, u32 flags,
					 u64 *frame_count, u64 *byte_count)
{
	struct qm_mcr_ceetm_statistics_query res;
	u8 ct;
	int ret;

	if (flags == QMAN_CEETM_FLAG_CLEAR_STATISTICS_COUNTER)
		ct = CEETM_QUERY_REJECT_CLEAR_STATISTICS;
	else
		ct = CEETM_QUERY_REJECT_STATISTICS;

	ret = ceetm_query_statistics((ccg->parent->idx << 4) | ccg->idx,
				     ccg->parent->dcp_idx, ct, &res);
	if (ret)
		return ret;

	*frame_count = be40_get(res.frm_cnt);
	*byte_count = be48_get(res.byte_cnt);
	return 0;
}
EXPORT_SYMBOL_GPL(qman_ceetm_ccg_get_reject_statistics);

/* ----------------------------------------------------------------------
 * Logical Frame Queues
 * ---------------------------------------------------------------------- */

int qman_ceetm_lfq_claim(struct qm_ceetm_lfq **lfq, struct qm_ceetm_cq *cq)
{
	struct qm_ceetm_lfq *p;
	struct qm_mcc_ceetm_lfqmt_config cfg = {0};
	u32 lfqid, lsb;
	int idx;

	if (cq->parent->dcp_idx != qm_dc_portal_fman0)
		return -EINVAL;

	idx = ceetm_bitmap_alloc(ceetm_lfqid_map, CEETM_LFQID_COUNT);
	if (idx < 0) {
		pr_err("CEETM: no LFQID available for CQ %u\n", cq->idx);
		return -ENODEV;
	}
	lsb = (u32)idx & CEETM_LFQID_LSB_MASK;
	lfqid = CEETM_LFQID_BASE | (cq->parent->dcp_idx << 16) | lsb;

	p = kzalloc(sizeof(*p), GFP_KERNEL);
	if (!p) {
		ceetm_bitmap_free(ceetm_lfqid_map, idx);
		return -ENOMEM;
	}
	p->idx = lfqid;
	p->dctidx = (u16)lsb;
	p->parent = cq->parent;
	list_add_tail(&p->node, &cq->bound_lfqids);

	be24_set(cfg.lfqid, lfqid);
	cfg.cqid = cpu_to_be16((cq->parent->idx << 4) | cq->idx);
	cfg.dctidx = cpu_to_be16(p->dctidx);
	if (ceetm_configure_lfqmt(&cfg)) {
		pr_err("CEETM: can't configure LFQMT for LFQID 0x%x\n", lfqid);
		list_del(&p->node);
		ceetm_bitmap_free(ceetm_lfqid_map, idx);
		kfree(p);
		return -EINVAL;
	}
	*lfq = p;
	return 0;
}
EXPORT_SYMBOL_GPL(qman_ceetm_lfq_claim);

int qman_ceetm_lfq_release(struct qm_ceetm_lfq *lfq)
{
	if (lfq->parent->dcp_idx != qm_dc_portal_fman0)
		return -EINVAL;
	ceetm_bitmap_free(ceetm_lfqid_map, lfq->idx & CEETM_LFQID_LSB_MASK);
	list_del(&lfq->node);
	kfree(lfq);
	return 0;
}
EXPORT_SYMBOL_GPL(qman_ceetm_lfq_release);

int qman_ceetm_lfq_set_context(struct qm_ceetm_lfq *lfq, u64 context_a,
			       u32 context_b)
{
	struct qm_mcc_ceetm_dct_config cfg = {0};

	lfq->context_a = context_a;
	lfq->context_b = context_b;
	cfg.dctidx = cpu_to_be16(lfq->dctidx);
	cfg.dcpid = lfq->parent->dcp_idx;
	cfg.context_b = cpu_to_be32(context_b);
	cfg.context_a = cpu_to_be64(context_a);
	return ceetm_configure_dct(&cfg);
}
EXPORT_SYMBOL_GPL(qman_ceetm_lfq_set_context);


/**
 * qman_ceetm_dump_state - log a forensic snapshot of a CEETM sub-hierarchy
 * @tag: label prefixed to every log line
 * @sp: sub-portal to query, or NULL
 * @lni: LNI to query, or NULL
 * @channel: class-queue channel to query, or NULL
 * @cq: class queue to query, or NULL
 * @ccg: class congestion group to query, or NULL
 *
 * Pure-query diagnostic used to bisect egress blackholes: a CQ frame count
 * stuck above zero means frames sit in QMan scheduling (mapping/prescaler);
 * growing dequeue statistics without wire traffic implicate the FMan TX
 * side (DCT context / port); growing CCG reject statistics mean congestion
 * tail-drops. All commands are non-destructive queries.
 */
void qman_ceetm_dump_state(const char *tag, struct qm_ceetm_sp *sp,
			   struct qm_ceetm_lni *lni,
			   struct qm_ceetm_channel *channel,
			   struct qm_ceetm_cq *cq, struct qm_ceetm_ccg *ccg)
{
	struct qm_mcr_ceetm_mapping_shaper_tcfc_query mst = {0};
	struct qm_mcr_ceetm_class_scheduler_query csch = {0};
	struct qm_mcr_ceetm_statistics_query stats = {0};
	struct qm_mcr_ceetm_cq_query cqq = {0};
	u16 pres = 0;
	int ret;

	qman_ceetm_get_prescaler(&pres);
	pr_debug("ceetm-dump[%s]: qman_clk %u pres 0x%04x\n", tag, qman_clk, pres);

	if (sp) {
		ret = ceetm_query_mst(CEETM_COMMAND_SP_MAPPING | sp->idx,
				      sp->dcp_idx, &mst);
		pr_debug("ceetm-dump[%s]: sp %u map_ctl 0x%02x (ret %d)\n", tag,
			sp->idx, mst.sp_mapping_query.map_ctl, ret);
	}
	if (lni) {
		ret = ceetm_query_mst(CEETM_COMMAND_LNI_SHAPER | lni->idx,
				      lni->dcp_idx, &mst);
		pr_debug("ceetm-dump[%s]: lni %u shaper ctl 0x%02x mps %u crtcr 0x%06x crtbl %u ertcr 0x%06x ertbl %u (ret %d)\n",
			tag, lni->idx, mst.shaper_query.ctl, mst.shaper_query.mps,
			be24_get(mst.shaper_query.crtcr),
			be16_to_cpu(mst.shaper_query.crtbl),
			be24_get(mst.shaper_query.ertcr),
			be16_to_cpu(mst.shaper_query.ertbl), ret);
	}
	if (channel) {
		ret = ceetm_query_mst(CEETM_COMMAND_CHANNEL_MAPPING |
				      channel->idx, channel->dcp_idx, &mst);
		pr_debug("ceetm-dump[%s]: ch %u map_ctl 0x%02x (ret %d)\n", tag,
			channel->idx, mst.channel_mapping_query.map_ctl, ret);
		ret = ceetm_query_mst(CEETM_COMMAND_CHANNEL_SHAPER |
				      channel->idx, channel->dcp_idx, &mst);
		pr_debug("ceetm-dump[%s]: ch %u shaper ctl 0x%02x crtcr 0x%06x crtbl %u ertcr 0x%06x ertbl %u (ret %d)\n",
			tag, channel->idx, mst.shaper_query.ctl,
			be24_get(mst.shaper_query.crtcr),
			be16_to_cpu(mst.shaper_query.crtbl),
			be24_get(mst.shaper_query.ertcr),
			be16_to_cpu(mst.shaper_query.ertbl), ret);
		ret = ceetm_query_csch(channel, &csch);
		pr_debug("ceetm-dump[%s]: ch %u csch gpc 0x%02x crem 0x%04x erem 0x%04x w %u %u %u %u %u %u %u %u (ret %d)\n",
			tag, channel->idx, csch.gpc, be16_to_cpu(csch.crem),
			be16_to_cpu(csch.erem), csch.w[0], csch.w[1], csch.w[2],
			csch.w[3], csch.w[4], csch.w[5], csch.w[6], csch.w[7],
			ret);
	}
	if (cq) {
		u16 cqid = (cq->parent->idx << 4) | cq->idx;

		ret = ceetm_query_cq(cq, &cqq);
		pr_debug("ceetm-dump[%s]: cq 0x%03x state 0x%04x ccgid %u frm_cnt %u (ret %d)\n",
			tag, cqid, be16_to_cpu(cqq.state), be16_to_cpu(cqq.ccgid),
			be24_get(cqq.frm_cnt), ret);
		ret = ceetm_query_statistics(cqid, cq->parent->dcp_idx,
					     CEETM_QUERY_DEQUEUE_STATISTICS,
					     &stats);
		pr_debug("ceetm-dump[%s]: cq 0x%03x dequeued %llu frames %llu bytes (ret %d)\n",
			tag, cqid, be40_get(stats.frm_cnt), be48_get(stats.byte_cnt),
			ret);
	}
	if (ccg) {
		ret = ceetm_query_statistics((ccg->parent->idx << 4) | ccg->idx,
					     ccg->parent->dcp_idx,
					     CEETM_QUERY_REJECT_STATISTICS, &stats);
		pr_debug("ceetm-dump[%s]: ccg %u rejected %llu frames %llu bytes (ret %d)\n",
			tag, ccg->idx, be40_get(stats.frm_cnt),
			be48_get(stats.byte_cnt), ret);
	}
}
EXPORT_SYMBOL_GPL(qman_ceetm_dump_state);

MODULE_LICENSE("Dual BSD/GPL");
MODULE_DESCRIPTION("QMan CEETM hierarchical egress shaper support");
