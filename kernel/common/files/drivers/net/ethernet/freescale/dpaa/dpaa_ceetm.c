// SPDX-License-Identifier: (GPL-2.0-only OR BSD-3-Clause)
/*
 * DPAA1 Ethernet -- QMan CEETM HTB-offload egress shaper consumer.
 *
 * Bridges the kernel TC_SETUP_QDISC_HTB offload contract (stock
 * "tc qdisc add ... htb offload") to the QMan CEETM hierarchical egress
 * traffic manager exported by <soc/fsl/qman.h>.
 *
 * G10 Phase 1 + Phase 2 (ASK2 + SW shared 2-level CEETM HQoS):
 *   - Supports both 1-level (root -> per-class CEETM channel) and 2-level
 *     (root -> inner CEETM channel -> up to 8 strict-priority CQs 0..7)
 *     HTB trees via TC_HTB_LEAF_ALLOC_QUEUE, TC_HTB_LEAF_TO_INNER,
 *     TC_HTB_NODE_MODIFY, and TC_HTB_LEAF_DEL / TC_HTB_LEAF_DEL_LAST.
 *   - Each CEETM CQ binds TWO LFQIDs via QMan's N:1 LFQMT/DCT mapping:
 *       * @lfq (context_a 0x1a00000080000000, ern=dpaa_ceetm_ern) for
 *         kernel software TX (dpaa_xmit, fd->bpid=0xff, confirmed).
 *       * @hw_lfq (context_a 0x9a000000c0000000, EBD=1, no TX-confirm)
 *         for ASK2 FMan FE ENQUEUE_PKT hardware forwarding.
 *     Both feed the exact same hardware CQ, frame-mode CCGR, channel shaper,
 *     and LNI, eliminating bypass, double shaping, and F-227 confirm/ERN
 *     confusion.
 *   - Symmetric SW/HW class steering: skb->priority / skb->mark (SW) and
 *     ct->mark (ASK2 HW via dpaa_ceetm_offload_egress_fqid) decode HTB minor
 *     classids identically, falling back to defcls when unmarked or unknown.
 *   - Frame-mode CCGR tail-drop bounded below half the port's RX BMan pool
 *     and below DPAA_CEETM_MAX_PORT_FRAMES (512), rebalanced on leaf add/del
 *     and MTU changes (closing byte-mode RX pool starvation, bug A337).
 *   - Synchronous ASK2 PRE/POST egress notifications under RTNL + SRCU
 *     (dpaa_fo_notify_egress_change), CQ CR/ER disable before FD-reclaiming
 *     CQ pop, qman_ceetm_drain_ern() + synchronize_rcu() before freeing fq,
 *     and retain-on-fault quarantine on quiesce/drain failure.
 */

#include <linux/kernel.h>
#include <linux/module.h>
#include <linux/netdevice.h>
#include <linux/rtnetlink.h>
#include <linux/slab.h>
#include <linux/delay.h>
#include <linux/jiffies.h>
#include <linux/ip.h>
#include <linux/ipv6.h>
#include <linux/if_vlan.h>
#include <net/dsfield.h>
#include <net/flow_dissector.h>
#include <net/flow_offload.h>
#include <net/pkt_cls.h>
#include <net/red.h>
#include <soc/fsl/qman.h>
#include <linux/fsl/fman_pcd.h>

#include "fman.h"
#include "fman_port.h"

void dpaa_wgfp_egress_deactivate(struct net_device *dev);
#include "mac.h"
#include "dpaa_eth.h"
#include "dpaa_fman_caps.h"
#include "dpaa_ceetm.h"

/* G10 Phase 3 DSCP->CEETM forward helpers (implemented at end of file). */
static u32 dpaa_ceetm_dscp_sw_minor_lookup(struct net_device *net_dev,
					   const struct sk_buff *skb);
static void dpaa_ceetm_dscp_resync(struct net_device *net_dev);
static void dpaa_ceetm_dscp_teardown_pre(struct net_device *net_dev);
static void dpaa_ceetm_dscp_teardown_post(struct net_device *net_dev,
					  bool quiesce_ok);

/* Sub-portal id is the low 5 bits of the FMan Tx port's QMan channel. */
#define DPAA_CEETM_SP_MASK		0x1f
/* QMan channels below this boundary belong to FMan DCP0 (LS1046A only). */
#define DPAA_CEETM_DCP0_MAX_CHANNEL	0x80f
/* Maximum CEETM channels per port (QMan Rev 3.2.1 exposes 32 SoC-wide). */
#define DPAA_CEETM_MAX_CHANS		32
/* Strict-priority CQs per CEETM channel (0 = highest, 7 = lowest). */
#define DPAA_CEETM_MAX_CQ_PRIO		8
/* Grouped WBFS CQs per CEETM channel (combined Group A: CQs 8..15). */
#define DPAA_CEETM_WBFS_START		8
#define DPAA_CEETM_MAX_CQ_TOTAL		16
/* Place combined 8-CQ WBFS Group A below strict CQs 0..6 and above CQ 7. */
#define DPAA_CEETM_WBFS_PRIO_A		6
#define DPAA_CEETM_WBFS_MASK		0xff00U

/*
 * Silicon-proven LFQ dequeue-context-A values:
 *   - DPAA_CEETM_CONTEXT_A: confirmed SW TX (dpaa_xmit, fd->bpid == 0xff).
 *   - DPAA_CEETM_HW_CONTEXT_A: no-confirm BMan-dealloc HW TX (F-199/F-316,
 *     OVOM=1, A2V=1, A0V=1, B0V=0, EBD=1) for ASK2 FE ENQUEUE_PKT.
 */
#define DPAA_CEETM_CONTEXT_A		0x1a00000080000000ULL
#define DPAA_CEETM_HW_CONTEXT_A		0x9a000000c0000000ULL

/* Frame-mode CCGR budgeting (G10 / A337 safety): cap per-port total CEETM
 * queue depth at <= min(half RX BMan pool at current MTU, 512 frames) so two
 * shaped ports cannot exhaust XSFDRs or starve the ingress BMan pool. */
#define DPAA_CEETM_MAX_PORT_FRAMES	512U
#define DPAA_CEETM_MIN_CQ_FRAMES	16U
#define DPAA_CEETM_PARKED_FRAMES	1U
/* Match dpaa_eth.c BMan constants (private to dpaa_eth.c). */
#ifndef FSL_DPAA_BPID_INV
#define FSL_DPAA_BPID_INV		0xff
#endif
#ifndef FSL_DPAA_ETH_MAX_BUF_COUNT
#define FSL_DPAA_ETH_MAX_BUF_COUNT	1280
#endif
/* Default strict-priority CQ index for single-level channel leaves. */
#define DPAA_CEETM_CQ_PRIO		0

/* LNI shaper overhead accounting length (Mono CDX CEETM_DEFA_OAL) */
#define DPAA_CEETM_LNI_OAL		24
/* Token bucket limit in bytes (Mono CDX CEETM_DEFA_BSIZE) */
#define DPAA_CEETM_BSIZE		0x2000
/*
 * Shaper rate for the default channel: 10 Gbps, >= every LS1046A port's
 * line rate so it imposes no real limit. A true CEETM "unshaped" channel
 * (CS=1, infinite CR/ER) never dequeues on this silicon, so the default
 * channel is built as a shaped channel at this rate (the proven leaf path).
 */
#define DPAA_CEETM_DEFAULT_RATE_BPS	10000000000ULL

/**
 * struct dpaa_ceetm_chan - one CEETM channel and its dual-rate shaper
 * @channel: the claimed QMan CEETM channel under the port LNI
 * @classid: HTB minor of the root-level class owning this channel (0 for def)
 * @cq_mask: bitmask of claimed CQs (bits 0..7 strict, bits 8..15 WBFS Group A)
 * @live_cq_mask: bitmask of live (non-quarantined) CQs on this channel
 * @active_cqs: number of active leaves attached to this channel
 * @shaped: true if the channel dual-rate shaper is enabled
 * @is_inner: true once TC_HTB_LEAF_TO_INNER promoted this channel to inner
 * @wbfs_group_cfg: true once combined WBFS Group A has been programmed on @channel
 * @quarantined: true if any CQ on this channel failed teardown/drain
 */
struct dpaa_ceetm_chan {
	struct qm_ceetm_channel *channel;
	u32 classid;
	u16 cq_mask;
	u16 live_cq_mask;
	u8 active_cqs;
	bool shaped;
	bool is_inner;
	bool wbfs_group_cfg;
	bool quarantined;
};

/* Frame-mode WRED constants (G10 Phase 5a; QMan CCGR Pn/SA wire bounds). */
#define DPAA_CEETM_WRED_MAXP_UNITS	256U
#define DPAA_CEETM_WRED_SA_MAX		127U
#define DPAA_CEETM_RED_FRAME_OVERHEAD	(ETH_HLEN + ETH_FCS_LEN + VLAN_HLEN)

/**
 * struct dpaa_ceetm_class - one CEETM class queue (CQ 0..7) and its two LFQs
 * @chan: owning CEETM channel (1-level own channel or 2-level parent channel)
 * @cq_prio: CQ index (0..7 strict-priority, or 8..15 WBFS Group A) on @chan
 * @is_wbfs: true if this leaf uses a grouped WBFS CQ (8..15) rather than strict
 * @wbfs_weight: configured WBFS scheduler weight (1..248 when @is_wbfs, else 0)
 * @ccg: frame-mode class congestion group (tail-drop / WRED) at @cq_prio
 * @cq: strict-priority class queue at @cq_prio
 * @lfq: logical frame queue for software TX (dpaa_xmit, confirmed)
 * @hw_lfq: logical frame queue for ASK2 HW FE ENQUEUE_PKT (no-confirm, EBD=1)
 * @fq: enqueue/ERN handle created on @lfq
 * @net_dev: owning netdev, recovered by the ERN callback via container_of
 * @classid: TC_H_MIN(classid) of this HTB leaf (0 for the default channel)
 * @used: published last; gates both dpaa_xmit and ASK2 offload lookups
 * @quarantined: true if teardown quiesce or CQ drain failed (retain-on-fault)
 * @red_active: true while an offloaded frame-mode RED qdisc owns this CQ's CCG
 * @red_displaced: true during tc qdisc replace while old RED awaits DESTROY
 * @red_handle: active offloaded RED qdisc handle
 * @red_displaced_handle: prior RED qdisc handle displaced by replace
 * @red_min: requested RED min threshold in bytes
 * @red_max: requested RED max threshold in bytes
 * @red_prob: requested RED max_P (0..2^32-1)
 * @red_limit: requested RED hard limit in bytes
 * @red_displaced_min: displaced RED min threshold in bytes
 * @red_displaced_max: displaced RED max threshold in bytes
 * @red_displaced_prob: displaced RED max_P
 * @red_displaced_limit: displaced RED hard limit in bytes
 * @red_soft_pending_handle: handle of a rejected leaf RED pending graft/destroy
 */
struct dpaa_ceetm_class {
	struct dpaa_ceetm_chan *chan;
	u8 cq_prio;
	bool is_wbfs;
	u32 wbfs_weight;
	struct qm_ceetm_ccg *ccg;
	struct qm_ceetm_cq *cq;
	struct qm_ceetm_lfq *lfq;
	struct qm_ceetm_lfq *hw_lfq;
	struct qman_fq fq;
	struct net_device *net_dev;
	u32 classid;
	bool used;
	bool quarantined;
	bool red_active;
	bool red_displaced;
	u32 red_handle;
	u32 red_displaced_handle;
	u32 red_min;
	u32 red_max;
	u32 red_prob;
	u32 red_limit;
	u32 red_displaced_min;
	u32 red_displaced_max;
	u32 red_displaced_prob;
	u32 red_displaced_limit;
	u32 red_soft_pending_handle;
};

/**
 * struct dpaa_ceetm - per-port CEETM HTB-offload state
 * @sp: the claimed CEETM sub-portal
 * @lni: the claimed CEETM logical network interface
 * @base_txq: dpaa_max_num_txqs() snapshot; leaf Tx queues start here
 * @prev_real_txqs: real_num_tx_queues before HTB create, restored on destroy
 * @htb_maj: major of the installed HTB qdisc handle (TC_H_MAJ(handle) >> 16)
 * @defcls: TC_H_MIN of the HTB default class; 0 when no default is configured
 * @def_chan: backing channel for the fallback default class
 * @def: fallback default CQ for unclassified / WQ-queue SW traffic
 * @chans: root-level CEETM channels (each either a 1-level leaf or 2-level inner)
 * @leaf: per-leaf-class CQs, indexed by (Tx queue - base_txq)
 */
struct dpaa_ceetm {
	struct qm_ceetm_sp *sp;
	struct qm_ceetm_lni *lni;
	unsigned int base_txq;
	unsigned int prev_real_txqs;
	u32 htb_maj;
	u32 defcls;
	struct dpaa_ceetm_chan def_chan;
	struct dpaa_ceetm_class def;
	struct dpaa_ceetm_chan chans[DPAA_CEETM_MAX_CHANS];
	struct dpaa_ceetm_class leaf[DPAA_CEETM_MAX_TXQS];
};

/*
 * Software-ERN handler for CEETM tail-drop rejections. Runs in QMan portal /
 * NAPI context (no sleeping). Mirrors egress_ern(): bump the per-CPU Tx drop
 * counters and release the frame's buffers.
 */
static void dpaa_ceetm_ern(struct qman_portal *portal, struct qman_fq *fq,
			   const union qm_mr_entry *msg)
{
	struct dpaa_ceetm_class *cls = container_of(fq, struct dpaa_ceetm_class,
						    fq);
	struct net_device *net_dev = cls->net_dev;
	const struct dpaa_priv *priv = netdev_priv(net_dev);
	struct dpaa_percpu_priv *percpu_priv;
	const struct qm_fd *fd = &msg->ern.fd;
	struct sk_buff *skb;

	percpu_priv = this_cpu_ptr(priv->percpu_priv);
	percpu_priv->stats.tx_dropped++;
	percpu_priv->stats.tx_fifo_errors++;
	count_ern(percpu_priv, msg);

	if (unlikely(fd->bpid != FSL_DPAA_BPID_INV)) {
		dpaa_fd_release(net_dev, fd);
		return;
	}
	skb = dpaa_cleanup_tx_fd(priv, fd, false);
	if (skb)
		dev_kfree_skb_any(skb);
}

/* Program the CEETM credit-update prescaler once per boot (rtnl-serialised). */
static void dpaa_ceetm_prescaler_once(enum qm_dc_portal dcp)
{
	static bool done;

	if (done)
		return;
	if (!qman_ceetm_set_prescaler(dcp))
		done = true;
}

/* Compute per-port frame-mode CEETM queue budget bounded by half the port's
 * RX BMan pool at @mtu (matching F-316 S/G buffer math) and capped at
 * DPAA_CEETM_MAX_PORT_FRAMES so concurrent shaped ports cannot exhaust XSFDRs. */
static u32 dpaa_ceetm_port_frame_budget(const struct dpaa_priv *priv,
					unsigned int mtu)
{
	u64 bp_size, first_cap, max_frame, bufs_per_frame, pool_bufs, pool_cap;
	u32 mtu_eff = max_t(u32, mtu, ETH_DATA_LEN);

	bp_size = priv->dpaa_bp ? priv->dpaa_bp->size : 0;
	if (!bp_size)
		bp_size = 3584;
	first_cap = bp_size > priv->rx_headroom ?
		    bp_size - priv->rx_headroom : bp_size;
	max_frame = (u64)mtu_eff + ETH_HLEN + ETH_FCS_LEN + 2 * VLAN_HLEN;
	if (max_frame <= first_cap)
		bufs_per_frame = 1;
	else
		bufs_per_frame = 2 + DIV_ROUND_UP(max_frame - first_cap, bp_size);

	pool_bufs = (u64)(priv->dpaa_bp && priv->dpaa_bp->config_count ?
			  priv->dpaa_bp->config_count :
			  FSL_DPAA_ETH_MAX_BUF_COUNT) * num_possible_cpus();
	pool_cap = max_t(u64, pool_bufs / 2 / bufs_per_frame, 1);

	return (u32)min_t(u64, pool_cap, DPAA_CEETM_MAX_PORT_FRAMES);
}

static unsigned int dpaa_ceetm_active_cqs(const struct dpaa_ceetm *ceetm)
{
	unsigned int n = 0;
	int i;

	if (ceetm->def.cq && !ceetm->def.quarantined)
		n++;
	for (i = 0; i < DPAA_CEETM_MAX_TXQS; i++)
		if (ceetm->leaf[i].cq && !ceetm->leaf[i].quarantined)
			n++;
	return n;
}

static int dpaa_ceetm_set_ccg_frames(struct qm_ceetm_ccg *ccg, u32 frames)
{
	struct qm_ceetm_ccg_params ccgp;

	memset(&ccgp, 0, sizeof(ccgp));
	ccgp.mode = 1;		/* count frames (A337 / G10) */
	ccgp.td_en = 1;		/* enable tail-drop */
	ccgp.td_mode = 1;	/* threshold-based tail-drop */
	ccgp.oal = 0;
	qm_cgr_cs_thres_set64(&ccgp.td_thres, frames, 0);
	/* Explicitly clear WRED enables so plain tail-drop / quarantine never
	 * retains a prior curve on this CCG. */
	return qman_ceetm_ccg_set(ccg,
				  QM_CCGR_WE_MODE | QM_CCGR_WE_TD_EN |
				  QM_CCGR_WE_TD_MODE | QM_CCGR_WE_TD_THRES |
				  QM_CCGR_WE_OAL |
				  QM_CCGR_WE_WR_EN_G | QM_CCGR_WE_WR_EN_Y |
				  QM_CCGR_WE_WR_EN_R, &ccgp);
}

static u32 dpaa_ceetm_wred_maxp(u32 probability)
{
	u32 maxp = (u32)(((u64)probability * DPAA_CEETM_WRED_MAXP_UNITS) >> 32);

	if (maxp < 4U)
		maxp = 4U;
	if (maxp > DPAA_CEETM_WRED_MAXP_UNITS)
		maxp = DPAA_CEETM_WRED_MAXP_UNITS;
	return maxp & ~3U;
}

static u32 dpaa_ceetm_wred_min_band(u32 probability)
{
	return max_t(u32, 2U,
		     DIV_ROUND_UP(dpaa_ceetm_wred_maxp(probability),
				  DPAA_CEETM_WRED_SA_MAX));
}

/* Encode QMan CCGR WR_PARM_{G,Y,R}:
 *   MA[24-31], Mn[19-23], SA[12-18], Sn[6-11], Pn[0-5] (big-endian wire word).
 *   MaxTH = MA * 2^Mn, Slope = SA / 2^Sn (SA in [64..127]), MaxP = 4*(Pn+1). */
static void dpaa_ceetm_wred_encode_parm(u32 min_frames, u32 max_frames,
					u32 probability,
					struct qm_cgr_wr_parm *parm)
{
	u32 maxp = dpaa_ceetm_wred_maxp(probability);
	u32 pn = (maxp / 4U) - 1U;
	u32 ma = max_frames;
	u32 mn = 0;
	u32 maxth, span;
	u64 num = maxp;
	u64 sa;
	u32 sn = 0;
	u32 host_word;

	while (ma > 0xffU && mn < 0x1fU) {
		ma >>= 1;
		mn++;
	}
	if (ma > 0xffU)
		ma = 0xffU;

	maxth = ma << mn;
	span = maxth > min_frames ? maxth - min_frames : 1U;

	while (sn < 40U && num < 64ULL * span) {
		num <<= 1;
		sn++;
	}
	sa = div64_u64(num, span);
	if (sa < 64ULL)
		sa = 64ULL;
	if (sa > DPAA_CEETM_WRED_SA_MAX)
		sa = DPAA_CEETM_WRED_SA_MAX;

	host_word = ((ma & 0xffU) << 24) |
		    ((mn & 0x1fU) << 19) |
		    (((u32)sa & 0x7fU) << 12) |
		    ((sn & 0x3fU) << 6) |
		    (pn & 0x3fU);
	parm->word = cpu_to_be32(host_word);
}

static u32 dpaa_ceetm_red_frame_bytes(unsigned int mtu)
{
	return min_t(unsigned int, mtu ? mtu : ETH_DATA_LEN, ETH_DATA_LEN) +
	       DPAA_CEETM_RED_FRAME_OVERHEAD;
}

static int dpaa_ceetm_set_ccg_wred(struct dpaa_ceetm_class *cls, u32 share,
				   unsigned int mtu)
{
	struct qm_ceetm_ccg_params ccgp;
	u32 frame_bytes = dpaa_ceetm_red_frame_bytes(mtu);
	u32 want = max_t(u32, cls->red_limit / frame_bytes, 1U);
	u32 depth = min_t(u32, want, max_t(u32, share, 1U));
	u64 lo = cls->red_min / frame_bytes;
	u64 hi = cls->red_max / frame_bytes;
	u32 min_band = dpaa_ceetm_wred_min_band(cls->red_prob);

	if (!cls->ccg || !cls->red_limit || !cls->red_max ||
	    cls->red_max <= cls->red_min)
		return -EINVAL;

	if (depth < want) {
		lo = div64_u64(lo * depth, want);
		hi = div64_u64(hi * depth, want);
	}
	if (lo >= depth)
		lo = depth - 1U;
	hi = max_t(u64, hi, lo + min_band);

	memset(&ccgp, 0, sizeof(ccgp));
	ccgp.mode = 1;		/* frame mode only (A337 safety) */
	ccgp.td_en = 1;
	ccgp.td_mode = 1;
	ccgp.oal = 0;
	qm_cgr_cs_thres_set64(&ccgp.td_thres, depth, 0);

	dpaa_ceetm_wred_encode_parm((u32)lo, (u32)hi, cls->red_prob,
				    &ccgp.wr_parm_g);
	ccgp.wr_parm_y = ccgp.wr_parm_g;
	ccgp.wr_parm_r = ccgp.wr_parm_g;
	ccgp.wr_en_g = 1;
	ccgp.wr_en_y = 1;
	ccgp.wr_en_r = 1;

	return qman_ceetm_ccg_set(cls->ccg,
				  QM_CCGR_WE_MODE | QM_CCGR_WE_TD_EN |
				  QM_CCGR_WE_TD_MODE | QM_CCGR_WE_TD_THRES |
				  QM_CCGR_WE_OAL |
				  QM_CCGR_WE_WR_EN_G | QM_CCGR_WE_WR_EN_Y |
				  QM_CCGR_WE_WR_EN_R |
				  QM_CCGR_WE_WR_PARM_G | QM_CCGR_WE_WR_PARM_Y |
				  QM_CCGR_WE_WR_PARM_R, &ccgp);
}

static void dpaa_ceetm_clear_red_state(struct dpaa_ceetm_class *cls)
{
	smp_store_release(&cls->red_active, false);
	WRITE_ONCE(cls->red_handle, 0);
	cls->red_displaced = false;
	cls->red_displaced_handle = 0;
}

static void dpaa_ceetm_red_sync_ask(struct net_device *net_dev,
				    bool withdraw_hw);

/* Rebalance frame-mode CCGR limits (and active WRED curves) across all active
 * (non-quarantined) CQs. Returns true if any RED leaf was demoted on HW error. */
static bool dpaa_ceetm_rebalance_ccgs(struct dpaa_priv *priv,
				      struct dpaa_ceetm *ceetm,
				      unsigned int mtu)
{
	unsigned int n = dpaa_ceetm_active_cqs(ceetm);
	bool demoted = false;
	u32 budget, share;
	int i;

	if (!n)
		return false;
	budget = dpaa_ceetm_port_frame_budget(priv, mtu);
	/* Clamp at 1 (not MIN_CQ_FRAMES) so an MTU increase never pushes the
	 * aggregate per-port depth above the half-RX-pool / XSFDR budget. */
	share = max_t(u32, budget / n, 1U);

	if (ceetm->def.ccg && !ceetm->def.quarantined)
		dpaa_ceetm_set_ccg_frames(ceetm->def.ccg, share);
	for (i = 0; i < DPAA_CEETM_MAX_TXQS; i++) {
		struct dpaa_ceetm_class *cls = &ceetm->leaf[i];

		if (!cls->ccg || cls->quarantined)
			continue;
		if (cls->red_active) {
			u32 old_handle = cls->red_handle;

			if (dpaa_ceetm_set_ccg_wred(cls, share, mtu)) {
				dpaa_ceetm_clear_red_state(cls);
				WRITE_ONCE(cls->red_soft_pending_handle,
					   old_handle);
				dpaa_ceetm_set_ccg_frames(cls->ccg, share);
				demoted = true;
				if (cls->net_dev)
					netdev_warn(cls->net_dev,
						    "CEETM: WRED rebalance failed on class 0x%x; reverted to tail-drop\n",
						    cls->classid);
			}
		} else {
			dpaa_ceetm_set_ccg_frames(cls->ccg, share);
		}
	}
	return demoted;
}

void dpaa_ceetm_update_mtu(struct dpaa_priv *priv, unsigned int mtu)
{
	struct dpaa_ceetm *ceetm;

	ASSERT_RTNL();
	ceetm = priv->ceetm;
	if (!ceetm)
		return;
	if (dpaa_ceetm_rebalance_ccgs(priv, ceetm, mtu))
		dpaa_ceetm_red_sync_ask(priv->net_dev, true);
}

/* Decode an HTB classid or mark (either major:minor matching ceetm->htb_maj,
 * or minor-only with upper 16 bits zero) into the HTB minor classid. */
static u32 dpaa_ceetm_decode_classid(const struct dpaa_ceetm *ceetm, u32 raw)
{
	if (!raw)
		return 0;
	if (TC_H_MAJ(raw)) {
		if ((TC_H_MAJ(raw) >> 16) != ceetm->htb_maj)
			return 0;
		return TC_H_MIN(raw);
	}
	return TC_H_MIN(raw);
}

/* Find the leaf slot owning @classid (TC_H_MIN form), or -1 (RTNL callers). */
static int dpaa_ceetm_find_slot(const struct dpaa_ceetm *ceetm, u32 classid)
{
	int i;

	for (i = 0; i < DPAA_CEETM_MAX_TXQS; i++)
		if (ceetm->leaf[i].cq && !ceetm->leaf[i].quarantined &&
		    ceetm->leaf[i].classid == classid)
			return i;
	return -1;
}

/* Lockless hot-path lookup: acquire @used FIRST so classid/quarantined/hw_lfq
 * reads are properly ordered against RTNL publication on ARM64. */
static int dpaa_ceetm_find_live_slot(const struct dpaa_ceetm *ceetm,
				     u32 classid)
{
	int i;

	for (i = 0; i < DPAA_CEETM_MAX_TXQS; i++) {
		if (!smp_load_acquire(&ceetm->leaf[i].used))
			continue;
		if (READ_ONCE(ceetm->leaf[i].quarantined))
			continue;
		if (READ_ONCE(ceetm->leaf[i].classid) == classid)
			return i;
	}
	return -1;
}

/* Find an inner (2-level) channel owning @classid (TC_H_MIN form), or NULL. */
static struct dpaa_ceetm_chan *dpaa_ceetm_find_inner_chan(struct dpaa_ceetm *ceetm,
							  u32 classid)
{
	int i;

	for (i = 0; i < DPAA_CEETM_MAX_CHANS; i++)
		if (ceetm->chans[i].channel && ceetm->chans[i].is_inner &&
		    ceetm->chans[i].classid == classid)
			return &ceetm->chans[i];
	return NULL;
}

/* Deterministic SW 5-tuple hash using the exact same dpaa_ceetm_flow_hash_5tuple()
 * helper as ask_hw_flow_key_hash(), so unmarked flows stay on the identical WBFS
 * CQ across SW->HW offload handoff (zero reordering on transition). */
static u32 dpaa_ceetm_skb_flow_hash(const struct sk_buff *skb)
{
	struct flow_keys keys;

	/* Use STOP_BEFORE_ENCAP (not STOP_AT_FLOW_LABEL) so IPv6 packets with
	 * non-zero flow labels still dissect L4 ports, matching ask_hw.c. */
	if (!skb_flow_dissect_flow_keys(skb, &keys,
					FLOW_DISSECTOR_F_STOP_BEFORE_ENCAP))
		return 0;

	if (keys.control.addr_type == FLOW_DISSECTOR_KEY_IPV4_ADDRS)
		return dpaa_ceetm_flow_hash_5tuple(4, keys.basic.ip_proto,
						   &keys.addrs.v4addrs.src,
						   &keys.addrs.v4addrs.dst,
						   keys.ports.src,
						   keys.ports.dst);
	if (keys.control.addr_type == FLOW_DISSECTOR_KEY_IPV6_ADDRS)
		return dpaa_ceetm_flow_hash_5tuple(6, keys.basic.ip_proto,
						   &keys.addrs.v6addrs.src,
						   &keys.addrs.v6addrs.dst,
						   keys.ports.src,
						   keys.ports.dst);
	return 0;
}

/* Lockless equal-weight WBFS sibling selector (G10 Phase 5b).
 * Opt-in only (priv->ceetm_wbfs_hash == true via sysfs ceetm_wbfs_hash):
 * by default equal-weight tenant WBFS classes stay strictly isolated.
 * When opted in and an unmarked flow falls back to a WBFS @def_slot on an
 * inner channel, spread deterministically across all live, non-quarantined
 * WBFS leaves on that channel matching @def_slot's weight. */
static int dpaa_ceetm_pick_wbfs_slot(const struct dpaa_priv *priv,
				     const struct dpaa_ceetm *ceetm,
				     int def_slot, u32 flow_hash)
{
	struct dpaa_ceetm_chan *target_chan;
	int by_cq[DPAA_CEETM_MAX_CQ_TOTAL - DPAA_CEETM_WBFS_START];
	int pool[DPAA_CEETM_MAX_CQ_TOTAL - DPAA_CEETM_WBFS_START];
	u32 weight;
	int i, n_pool = 0;

	if (!priv || !READ_ONCE(priv->ceetm_wbfs_hash))
		return def_slot;
	if (def_slot < 0 || def_slot >= DPAA_CEETM_MAX_TXQS)
		return def_slot;
	if (!READ_ONCE(ceetm->leaf[def_slot].is_wbfs))
		return def_slot;
	target_chan = READ_ONCE(ceetm->leaf[def_slot].chan);
	weight = READ_ONCE(ceetm->leaf[def_slot].wbfs_weight);
	if (!target_chan || !READ_ONCE(target_chan->is_inner) || !weight)
		return def_slot;

	for (i = 0; i < ARRAY_SIZE(by_cq); i++)
		by_cq[i] = -1;

	for (i = 0; i < DPAA_CEETM_MAX_TXQS; i++) {
		u8 cq;

		if (!smp_load_acquire(&ceetm->leaf[i].used))
			continue;
		if (READ_ONCE(ceetm->leaf[i].quarantined))
			continue;
		if (!READ_ONCE(ceetm->leaf[i].is_wbfs))
			continue;
		if (READ_ONCE(ceetm->leaf[i].chan) != target_chan)
			continue;
		if (READ_ONCE(ceetm->leaf[i].wbfs_weight) != weight)
			continue;
		cq = READ_ONCE(ceetm->leaf[i].cq_prio);
		if (cq >= DPAA_CEETM_WBFS_START && cq < DPAA_CEETM_MAX_CQ_TOTAL)
			by_cq[cq - DPAA_CEETM_WBFS_START] = i;
	}

	for (i = 0; i < ARRAY_SIZE(by_cq); i++)
		if (by_cq[i] >= 0)
			pool[n_pool++] = by_cq[i];

	if (n_pool <= 1)
		return def_slot;
	return pool[flow_hash % (u32)n_pool];
}

/*
 * ndo_select_queue: steer an HTB-offloaded class to its leaf Tx queue.
 * Matches skb->priority first (skbedit / nft meta priority), then skb->mark
 * (symmetric with ASK2 HW ct->mark steering), then DSCP map, then ceetm->defcls
 * (spreading across equal-weight WBFS siblings when ceetm_wbfs_hash is enabled,
 * using the exact same 5-tuple hash as ASK2 HW).
 */
u16 dpaa_ceetm_select_queue(struct net_device *net_dev, struct sk_buff *skb,
			    struct net_device *sb_dev)
{
	struct dpaa_priv *priv = netdev_priv(net_dev);
	struct dpaa_ceetm *ceetm = smp_load_acquire(&priv->ceetm);
	unsigned int direct;
	u32 classid;
	u16 txq;
	int slot = -1;

	if (likely(!ceetm))
		return netdev_pick_tx(net_dev, skb, NULL);

	classid = dpaa_ceetm_decode_classid(ceetm, skb->priority);
	if (classid)
		slot = dpaa_ceetm_find_live_slot(ceetm, classid);
	if (slot < 0 && skb->mark) {
		classid = dpaa_ceetm_decode_classid(ceetm, skb->mark);
		if (classid)
			slot = dpaa_ceetm_find_live_slot(ceetm, classid);
	}
	if (slot < 0) {
		classid = dpaa_ceetm_dscp_sw_minor_lookup(net_dev, skb);
		if (classid)
			slot = dpaa_ceetm_find_live_slot(ceetm, classid);
	}
	if (slot < 0 && ceetm->defcls) {
		slot = dpaa_ceetm_find_live_slot(ceetm, ceetm->defcls);
		if (slot >= 0 && READ_ONCE(priv->ceetm_wbfs_hash))
			slot = dpaa_ceetm_pick_wbfs_slot(
				priv, ceetm, slot,
				dpaa_ceetm_skb_flow_hash(skb));
	}

	if (slot >= 0)
		return ceetm->base_txq + slot;

	/* sch_htb installs direct qdiscs only on [0 .. prev_real_txqs - 1]
	 * and noop_qdisc on [prev_real_txqs .. base_txq - 1]; fold within the
	 * direct range so fallback traffic is never silently dropped. */
	direct = ceetm->prev_real_txqs ? ceetm->prev_real_txqs : 1;
	txq = netdev_pick_tx(net_dev, skb, NULL);
	if (txq >= direct)
		txq %= direct;
	return txq;
}

/*
 * Pop every descriptor remaining in @cls->cq and explicitly release its
 * buffer (skb cleanup for bpid==0xff, dpaa_fd_release for real BMan pools).
 */
static int dpaa_ceetm_drain_and_release_cq(struct dpaa_ceetm_class *cls)
{
	struct net_device *net_dev = cls->net_dev;
	const struct dpaa_priv *priv = net_dev ? netdev_priv(net_dev) : NULL;
	unsigned long deadline = jiffies + msecs_to_jiffies(1000);
	struct qm_fd fd;
	int rc;

	if (!cls->cq)
		return 0;

	for (;;) {
		rc = qman_ceetm_cq_pop(cls->cq, &fd);
		if (rc == 0)
			return 0;
		if (rc == 1) {
			if (fd.bpid != FSL_DPAA_BPID_INV) {
				dpaa_fd_release(net_dev, &fd);
			} else if (priv) {
				struct sk_buff *skb;

				skb = dpaa_cleanup_tx_fd(priv, &fd, false);
				if (skb)
					dev_kfree_skb_any(skb);
			}
			if (time_after_eq(jiffies, deadline))
				return -ETIMEDOUT;
			continue;
		}
		if (time_after_eq(jiffies, deadline))
			return rc < 0 ? rc : -ETIMEDOUT;
		if (rc != -EAGAIN)
			return rc;
		usleep_range(1000, 2000);
	}
}

static void dpaa_ceetm_quarantine_class(struct dpaa_ceetm_class *cls,
					const char *reason)
{
	smp_store_release(&cls->used, false);
	WRITE_ONCE(cls->quarantined, true);
	dpaa_ceetm_clear_red_state(cls);
	WRITE_ONCE(cls->red_soft_pending_handle, 0);
	if (cls->chan) {
		cls->chan->live_cq_mask &= ~BIT(cls->cq_prio);
		cls->chan->quarantined = true;
	}
	if (cls->ccg)
		dpaa_ceetm_set_ccg_frames(cls->ccg, DPAA_CEETM_PARKED_FRAMES);
	if (cls->net_dev)
		netdev_err(cls->net_dev,
			   "CEETM: quarantining class 0x%x ch %u cq %u (%s); retaining channel/LFQIDs\n",
			   cls->classid,
			   (cls->chan && cls->chan->channel) ?
			   cls->chan->channel->idx : 0,
			   cls->cq_prio, reason);
}

static int dpaa_ceetm_chan_set_rates(struct net_device *net_dev,
				     struct dpaa_ceetm_chan *ch,
				     u64 rate_bps, u64 ceil_bps)
{
	u16 burst = net_dev->mtu;
	int err;

	err = qman_ceetm_channel_set_commit_rate_bps(ch->channel, rate_bps,
						     burst);
	if (err)
		return err;
	return qman_ceetm_channel_set_excess_rate_bps(ch->channel,
				ceil_bps > rate_bps ? ceil_bps - rate_bps : 0,
				burst);
}

static int dpaa_ceetm_chan_build(struct net_device *net_dev,
				 struct dpaa_ceetm *ceetm,
				 struct dpaa_ceetm_chan *ch,
				 u32 classid, u64 rate_bps, u64 ceil_bps)
{
	int err;

	err = qman_ceetm_channel_claim(&ch->channel, ceetm->lni);
	if (err) {
		netdev_err(net_dev, "CEETM channel claim failed: %d\n", err);
		return err;
	}

	err = qman_ceetm_channel_enable_shaper(ch->channel, 1);
	if (err)
		goto err_release_channel;
	ch->shaped = true;

	err = dpaa_ceetm_chan_set_rates(net_dev, ch, rate_bps, ceil_bps);
	if (err)
		goto err_disable_shaper;

	ch->classid = classid;
	ch->cq_mask = 0;
	ch->live_cq_mask = 0;
	ch->active_cqs = 0;
	ch->is_inner = false;
	ch->wbfs_group_cfg = false;
	ch->quarantined = false;
	return 0;

err_disable_shaper:
	qman_ceetm_channel_disable_shaper(ch->channel);
	ch->shaped = false;
err_release_channel:
	if (qman_ceetm_channel_release(ch->channel))
		ch->quarantined = true;
	else
		ch->channel = NULL;
	return err;
}

static void dpaa_ceetm_chan_destroy(struct dpaa_ceetm_chan *ch)
{
	if (!ch || !ch->channel || ch->quarantined || ch->active_cqs)
		return;
	if (ch->shaped) {
		qman_ceetm_channel_disable_shaper(ch->channel);
		ch->shaped = false;
	}
	if (qman_ceetm_channel_release(ch->channel)) {
		ch->quarantined = true;
		return;
	}
	ch->channel = NULL;
	ch->classid = 0;
	ch->cq_mask = 0;
	ch->live_cq_mask = 0;
	ch->is_inner = false;
	ch->wbfs_group_cfg = false;
}

static int dpaa_ceetm_alloc_wbfs_cq(const struct dpaa_ceetm_chan *ch)
{
	int cq;

	for (cq = DPAA_CEETM_WBFS_START; cq < DPAA_CEETM_MAX_CQ_TOTAL; cq++)
		if (!(ch->cq_mask & BIT(cq)))
			return cq;
	return -ENOSPC;
}

/*
 * Build one CQ (strict-priority 0..7 or WBFS Group A 8..15), frame-mode CCG,
 * and TWO LFQs (lfq for SW dpaa_xmit and hw_lfq for ASK2 FE ENQUEUE_PKT) on @ch.
 */
static int dpaa_ceetm_cq_build(struct net_device *net_dev,
			       struct dpaa_ceetm_chan *ch,
			       struct dpaa_ceetm_class *cls,
			       u8 cq_prio, bool is_wbfs, u32 wbfs_weight,
			       u32 ccg_frames)
{
	struct qm_ceetm_weight_code wc = { 0, 0 };
	bool had_wbfs = (ch->cq_mask & DPAA_CEETM_WBFS_MASK) != 0;
	int err;

	if (is_wbfs) {
		if (cq_prio < DPAA_CEETM_WBFS_START ||
		    cq_prio >= DPAA_CEETM_MAX_CQ_TOTAL)
			return -EINVAL;
		err = qman_ceetm_ratio2wbfs(wbfs_weight, 1, &wc, 0);
		if (err)
			return err;
	} else if (cq_prio >= DPAA_CEETM_MAX_CQ_PRIO) {
		return -EINVAL;
	}
	if (ch->cq_mask & BIT(cq_prio))
		return -EEXIST;

	if (is_wbfs && !ch->wbfs_group_cfg) {
		err = qman_ceetm_channel_set_group(ch->channel, 0,
						   DPAA_CEETM_WBFS_PRIO_A,
						   DPAA_CEETM_WBFS_PRIO_A);
		if (err)
			return err;
	}

	err = qman_ceetm_ccg_claim(&cls->ccg, ch->channel, cq_prio);
	if (err)
		return err;

	err = dpaa_ceetm_set_ccg_frames(cls->ccg, ccg_frames);
	if (err)
		goto err_release_ccg;

	if (is_wbfs)
		err = qman_ceetm_cq_claim_A(&cls->cq, ch->channel, cq_prio,
					    cls->ccg);
	else
		err = qman_ceetm_cq_claim(&cls->cq, ch->channel, cq_prio,
					  cls->ccg);
	if (err)
		goto err_release_ccg;

	if (is_wbfs) {
		err = qman_ceetm_set_queue_weight(cls->cq, &wc);
		if (err)
			goto err_release_cq;
		err = qman_ceetm_channel_set_group_cr_eligibility(ch->channel,
								  0, 1);
		if (err)
			goto err_release_cq;
		err = qman_ceetm_channel_set_group_er_eligibility(ch->channel,
								  0, 1);
		if (err)
			goto err_disable_elig;
	} else {
		err = qman_ceetm_channel_set_cq_cr_eligibility(ch->channel,
							       cq_prio, 1);
		if (err)
			goto err_release_cq;
		err = qman_ceetm_channel_set_cq_er_eligibility(ch->channel,
							       cq_prio, 1);
		if (err)
			goto err_disable_elig;
	}

	/* LFQ #1: software TX (dpaa_xmit, confirmed skb/XDP path). */
	err = qman_ceetm_lfq_claim(&cls->lfq, cls->cq);
	if (err)
		goto err_disable_elig;

	err = qman_ceetm_lfq_set_context(cls->lfq, DPAA_CEETM_CONTEXT_A, 0);
	if (err)
		goto err_release_lfq;

	cls->lfq->ern = dpaa_ceetm_ern;
	cls->net_dev = net_dev;

	err = qman_ceetm_create_fq(cls->lfq, &cls->fq);
	if (err)
		goto err_release_lfq;

	/* LFQ #2: ASK2 hardware offload TX (FMan FE ENQUEUE_PKT, no-confirm,
	 * EBD=1) feeding the exact same CQ, frame-mode CCGR, and shaper. */
	err = qman_ceetm_lfq_claim(&cls->hw_lfq, cls->cq);
	if (err)
		goto err_destroy_fq;

	err = qman_ceetm_lfq_set_context(cls->hw_lfq,
					 DPAA_CEETM_HW_CONTEXT_A, 0);
	if (err)
		goto err_release_hw_lfq;

	WRITE_ONCE(cls->chan, ch);
	WRITE_ONCE(cls->cq_prio, cq_prio);
	WRITE_ONCE(cls->is_wbfs, is_wbfs);
	WRITE_ONCE(cls->wbfs_weight, is_wbfs ? wbfs_weight : 0);
	if (is_wbfs)
		ch->wbfs_group_cfg = true;
	ch->cq_mask |= BIT(cq_prio);
	ch->live_cq_mask |= BIT(cq_prio);
	ch->active_cqs++;
	return 0;

err_release_hw_lfq:
	qman_ceetm_lfq_release(cls->hw_lfq);
	cls->hw_lfq = NULL;
err_destroy_fq:
	qman_ceetm_destroy_fq(&cls->fq);
err_release_lfq:
	qman_ceetm_lfq_release(cls->lfq);
	cls->lfq = NULL;
err_disable_elig:
	if (is_wbfs) {
		if (!had_wbfs) {
			qman_ceetm_channel_set_group_cr_eligibility(ch->channel,
								    0, 0);
			qman_ceetm_channel_set_group_er_eligibility(ch->channel,
								    0, 0);
		}
	} else {
		qman_ceetm_channel_set_cq_cr_eligibility(ch->channel,
							 cq_prio, 0);
		qman_ceetm_channel_set_cq_er_eligibility(ch->channel,
							 cq_prio, 0);
	}
err_release_cq:
	qman_ceetm_cq_release(cls->cq);
	cls->cq = NULL;
err_release_ccg:
	qman_ceetm_ccg_release(cls->ccg);
	cls->ccg = NULL;
	return err;
}

/* Wait briefly for DCP0 to drain a departing non-last WBFS CQ naturally so
 * sibling WBFS CQs are not stalled during normal leaf deletion. */
static int dpaa_ceetm_wait_cq_empty(struct qm_ceetm_cq *cq, unsigned int ms)
{
	unsigned long deadline = jiffies + msecs_to_jiffies(ms);
	u32 frm_cnt = 1;
	int err;

	for (;;) {
		err = qman_ceetm_cq_get_frm_cnt(cq, &frm_cnt);
		if (err)
			return err;
		if (!frm_cnt)
			return 0;
		if (time_after_eq(jiffies, deadline))
			return -ETIMEDOUT;
		usleep_range(50, 100);
	}
}

/* Release a CQ/LFQ pair (and optionally its owning channel if now empty).
 * Returns true if quarantined. */
static bool dpaa_ceetm_destroy_class(struct dpaa_ceetm_class *cls,
				     bool force_quarantine,
				     bool release_empty_chan,
				     bool tearing_down)
{
	struct dpaa_ceetm_chan *ch = cls->chan;
	bool is_wbfs = cls->is_wbfs;
	bool last_wbfs = is_wbfs && ch &&
			 (tearing_down ||
			  ((ch->live_cq_mask & DPAA_CEETM_WBFS_MASK) ==
			   BIT(cls->cq_prio)));
	int drain_rc = 0;

	if (!cls->cq && !ch)
		return false;
	if (cls->quarantined)
		return true;
	if (force_quarantine) {
		dpaa_ceetm_quarantine_class(cls, "offload quiesce incomplete");
		return true;
	}

	/* Flush remote-portal EQCRs + pending SW ERNs BEFORE checking/draining
	 * the CQ so no late in-flight EQCR enqueue lands in cls->cq after we
	 * observe it empty (P1-2 fix). */
	if (cls->lfq)
		qman_ceetm_drain_ern();

	/* Stop DCP0 dequeues on this CQ before popping remaining frames so
	 * CQ_PEEK_POP_XFDRREAD does not contend with active prefetch/dequeue.
	 * For a non-last WBFS CQ, Group A eligibility belongs to surviving
	 * siblings too: park CCG and let DCP0 drain naturally first, falling
	 * back to a brief Group A pause + pop only if frames remain. */
	if (cls->cq && ch && ch->channel) {
		if (!is_wbfs) {
			qman_ceetm_channel_set_cq_cr_eligibility(ch->channel,
								 cls->cq_prio,
								 0);
			qman_ceetm_channel_set_cq_er_eligibility(ch->channel,
								 cls->cq_prio,
								 0);
			drain_rc = dpaa_ceetm_drain_and_release_cq(cls);
		} else if (last_wbfs) {
			if (!tearing_down) {
				int er_rc = qman_ceetm_channel_set_group_cr_eligibility(
						ch->channel, 0, 0);
				er_rc |= qman_ceetm_channel_set_group_er_eligibility(
						ch->channel, 0, 0);
				if (er_rc)
					drain_rc = -EIO;
			}
			if (!drain_rc)
				drain_rc = dpaa_ceetm_drain_and_release_cq(cls);
		} else {
			if (cls->ccg)
				dpaa_ceetm_set_ccg_frames(cls->ccg,
						DPAA_CEETM_PARKED_FRAMES);
			if (dpaa_ceetm_wait_cq_empty(cls->cq, 50)) {
				int dis_rc, ena_rc;

				dis_rc = qman_ceetm_channel_set_group_cr_eligibility(
					ch->channel, 0, 0);
				dis_rc |= qman_ceetm_channel_set_group_er_eligibility(
					ch->channel, 0, 0);
				if (dis_rc) {
					drain_rc = -EIO;
				} else {
					drain_rc = dpaa_ceetm_drain_and_release_cq(cls);
				}
				ena_rc = qman_ceetm_channel_set_group_cr_eligibility(
					ch->channel, 0, 1);
				ena_rc |= qman_ceetm_channel_set_group_er_eligibility(
					ch->channel, 0, 1);
				if (ena_rc) {
					ch->quarantined = true;
					drain_rc = -EIO;
				}
			}
		}
	}

	if (drain_rc) {
		dpaa_ceetm_quarantine_class(cls, "CQ drain failed");
		return true;
	}

	if (is_wbfs && cls->cq) {
		struct qm_ceetm_weight_code def_wc = { 0, 0 };

		qman_ceetm_set_queue_weight(cls->cq, &def_wc);
	}

	if (cls->hw_lfq) {
		struct qm_ceetm_lfq *hw_lfq = cls->hw_lfq;

		WRITE_ONCE(cls->hw_lfq, NULL);
		qman_ceetm_lfq_release(hw_lfq);
	}
	if (cls->lfq) {
		/* Drain any residual MR/SW-ERNs before clearing fq_table[fq->idx],
		 * then wait an RCU grace period before freeing/reusing cls. */
		qman_ceetm_drain_ern();
		qman_ceetm_destroy_fq(&cls->fq);
		synchronize_rcu();
		qman_ceetm_lfq_release(cls->lfq);
		cls->lfq = NULL;
	}
	if (cls->cq) {
		/* CQ is already verified empty/drained above; never invoke
		 * unbounded CQ_PEEK_POP while Group A may be active on siblings. */
		qman_ceetm_cq_release_drained(cls->cq);
		cls->cq = NULL;
	}
	if (cls->ccg) {
		qman_ceetm_ccg_release(cls->ccg);
		cls->ccg = NULL;
	}
	if (ch) {
		ch->cq_mask &= ~BIT(cls->cq_prio);
		ch->live_cq_mask &= ~BIT(cls->cq_prio);
		if (!(ch->cq_mask & DPAA_CEETM_WBFS_MASK))
			ch->wbfs_group_cfg = false;
		if (ch->active_cqs)
			ch->active_cqs--;
		if (release_empty_chan && ch->active_cqs == 0 && !ch->quarantined)
			dpaa_ceetm_chan_destroy(ch);
	}
	WRITE_ONCE(cls->chan, NULL);
	WRITE_ONCE(cls->cq_prio, 0);
	WRITE_ONCE(cls->is_wbfs, false);
	WRITE_ONCE(cls->wbfs_weight, 0);
	dpaa_ceetm_clear_red_state(cls);
	WRITE_ONCE(cls->red_soft_pending_handle, 0);
	smp_store_release(&cls->used, false);
	WRITE_ONCE(cls->classid, 0);
	return false;
}

static struct dpaa_ceetm_chan *dpaa_ceetm_alloc_chan(struct dpaa_ceetm *ceetm)
{
	int i;

	for (i = 0; i < DPAA_CEETM_MAX_CHANS; i++)
		if (!ceetm->chans[i].channel && !ceetm->chans[i].quarantined)
			return &ceetm->chans[i];
	return NULL;
}

void dpaa_ceetm_teardown(struct dpaa_priv *priv)
{
	struct net_device *net_dev = priv->net_dev;
	struct dpaa_ceetm *ceetm;
	unsigned int prev_real;
	bool any_quarantined = false;
	int quiesce_rc, i;

	ASSERT_RTNL();

	ceetm = priv->ceetm;
	if (!ceetm)
		return;

	/* Unpublish any DSCP map on this port BEFORE PRE_CHANGE so no new HW
	 * record gets DSCP_FQ_MAP_ENABLE during teardown. */
	dpaa_ceetm_dscp_teardown_pre(net_dev);

	/* Fence ASK2 admission and withdraw every HW flow targeting this
	 * port's CEETM LFQIDs BEFORE stopping CEETM mode on the sub-portal. */
	quiesce_rc = dpaa_fo_notify_egress_change(net_dev,
						  DPAA_EGRESS_CEETM_PRE_CHANGE,
						  0);

	/* Clear MURAM DSCP table if quiesce succeeded, or poison handoff if not. */
	dpaa_ceetm_dscp_teardown_post(net_dev, quiesce_rc == 0);

	/* Stop diverting new SW frames, then wait for in-flight xmit to finish. */
	smp_store_release(&priv->ceetm, NULL);
	synchronize_net();

	/* Disable WBFS Group A once per channel before popping all its CQs so
	 * teardown never stalls waiting on sibling WBFS leaves. Track disable
	 * failures locally so an earlier single-leaf quarantine on a channel
	 * never prevents healthy siblings from draining and releasing cleanly. */
	{
		bool group_dis_failed[DPAA_CEETM_MAX_CHANS] = { false };

		for (i = 0; i < DPAA_CEETM_MAX_CHANS; i++) {
			struct dpaa_ceetm_chan *ch = &ceetm->chans[i];

			if (ch->channel && ch->wbfs_group_cfg) {
				if (qman_ceetm_channel_set_group_cr_eligibility(
						ch->channel, 0, 0) ||
				    qman_ceetm_channel_set_group_er_eligibility(
						ch->channel, 0, 0)) {
					group_dis_failed[i] = true;
					ch->quarantined = true;
				}
			}
		}

		for (i = 0; i < DPAA_CEETM_MAX_TXQS; i++) {
			struct dpaa_ceetm_chan *ch = ceetm->leaf[i].chan;
			bool force_q = quiesce_rc != 0;

			if (!force_q && ceetm->leaf[i].is_wbfs &&
			    ch >= ceetm->chans &&
			    ch < ceetm->chans + DPAA_CEETM_MAX_CHANS &&
			    group_dis_failed[ch - ceetm->chans])
				force_q = true;
			any_quarantined |= dpaa_ceetm_destroy_class(
				&ceetm->leaf[i], force_q, true, true);
		}
	}
	any_quarantined |= dpaa_ceetm_destroy_class(&ceetm->def,
						    quiesce_rc != 0, true,
						    true);
	if (ceetm->def_chan.quarantined)
		any_quarantined = true;
	for (i = 0; i < DPAA_CEETM_MAX_CHANS; i++) {
		if (!ceetm->chans[i].quarantined)
			dpaa_ceetm_chan_destroy(&ceetm->chans[i]);
		if (ceetm->chans[i].quarantined)
			any_quarantined = true;
	}

	if (!any_quarantined && ceetm->lni &&
	    qman_ceetm_lni_release(ceetm->lni))
		any_quarantined = true;

	if (any_quarantined) {
		if (ceetm->sp)
			qman_sp_disable_ceetm_mode(ceetm->sp->dcp_idx,
						   ceetm->sp->idx);
		netdev_err(net_dev,
			   "CEETM teardown had quarantined class(es); LNI/SP retained\n");
	} else {
		if (ceetm->sp) {
			ceetm->sp->lni = NULL;
			qman_ceetm_sp_release(ceetm->sp);
		}
	}

	prev_real = ceetm->prev_real_txqs;
	if (!prev_real)
		prev_real = priv->num_tc * dpaa_num_txqs_per_tc();
	netif_set_real_num_tx_queues(net_dev, prev_real);

	if (!any_quarantined)
		kfree(ceetm);

	/* Re-open ASK2 egress admission (now back on conventional WQ6 FQs). */
	dpaa_fo_notify_egress_change(net_dev, DPAA_EGRESS_CEETM_POST_CHANGE, 0);
	netdev_info(net_dev, "CEETM HTB offload removed\n");
}

/* A458: Bounded drain of ALL conventional WQ TX FQs (both SW FQ_TYPE_TX and
 * ASK2 offload FQ_TYPE_TX_NO_CONFIRM on priv->dpaa_fq_list) before switching
 * the DCP0 sub-portal into CEETM mode (which stops conventional WQ dequeues).
 * Caller must have paused SW TX (ceetm_tx_paused + netif_tx_disable) and
 * quiesced ASK2 FE records so no new frames enter these FQs. */
static int dpaa_ceetm_drain_plain_tx_fqs(struct dpaa_priv *priv)
{
	unsigned long deadline = jiffies + msecs_to_jiffies(50);
	struct dpaa_fq *dpaa_fq;

	for (;;) {
		bool busy = false;

		list_for_each_entry(dpaa_fq, &priv->dpaa_fq_list, list) {
			struct qm_mcr_queryfq_np np;
			int err;

			if (dpaa_fq->fq_type != FQ_TYPE_TX &&
			    dpaa_fq->fq_type != FQ_TYPE_TX_NO_CONFIRM)
				continue;
			memset(&np, 0, sizeof(np));
			err = qman_query_fq_np(&dpaa_fq->fq_base, &np);
			if (err)
				return err;
			if (be32_to_cpu(np.frm_cnt) & qm_mcr_frm_cnt_mask) {
				busy = true;
				break;
			}
		}
		if (!busy)
			return 0;
		if (time_after_eq(jiffies, deadline))
			return -EBUSY;
		usleep_range(50, 100);
	}
}

static int dpaa_ceetm_htb_create(struct net_device *net_dev,
				 struct tc_htb_qopt_offload *opt)
{
	struct dpaa_priv *priv = netdev_priv(net_dev);
	struct dpaa_ceetm *ceetm;
	enum qm_dc_portal dcp;
	unsigned int sp_id;
	bool stopped_tx = false;
	u32 channel, budget;
	int err;

	if (priv->ceetm) {
		NL_SET_ERR_MSG_MOD(opt->extack,
				   "CEETM HTB offload already installed");
		return -EEXIST;
	}
	if (priv->num_tc > 1) {
		NL_SET_ERR_MSG_MOD(opt->extack,
				   "disable mqprio before installing HTB offload");
		return -EBUSY;
	}

	/* LS1046A is single-FMan: only DCP0 sub-portals are valid. */
	channel = fman_port_get_qman_channel_id(priv->mac_dev->port[TX]);
	if (channel > DPAA_CEETM_DCP0_MAX_CHANNEL) {
		NL_SET_ERR_MSG_MOD(opt->extack,
				   "Tx port is not on FMan DCP0; CEETM unsupported");
		return -EOPNOTSUPP;
	}
	sp_id = channel & DPAA_CEETM_SP_MASK;
	dcp = qm_dc_portal_fman0;

	ceetm = kzalloc(sizeof(*ceetm), GFP_KERNEL);
	if (!ceetm)
		return -ENOMEM;
	ceetm->base_txq = dpaa_max_num_txqs();
	ceetm->prev_real_txqs = net_dev->real_num_tx_queues;
	ceetm->htb_maj = opt->parent_classid;
	ceetm->defcls = opt->classid;

	dpaa_ceetm_prescaler_once(dcp);

	/* Fence and retire every ASK2 HW flow egressing this port BEFORE
	 * switching the sub-portal to CEETM mode (which stops WQ6 dequeues).
	 * Abort cleanly on -EBUSY so live WQ6 flows are never blackholed. */
	err = dpaa_fo_notify_egress_change(net_dev,
					   DPAA_EGRESS_CEETM_PRE_CHANGE, 0);
	if (err) {
		dpaa_fo_notify_egress_change(net_dev,
					     DPAA_EGRESS_CEETM_POST_CHANGE, 0);
		NL_SET_ERR_MSG_MOD(opt->extack,
				   "failed to quiesce ASK offload flows before CEETM enable");
		goto err_free;
	}

	/* Drain and deactivate any WGFP confirmed TX FQ on this port before
	 * CEETM claims the sub-portal. */
	dpaa_wgfp_egress_deactivate(net_dev);

	/* A458: Pause SW TX and drain conventional WQ TX + no-confirm FQs before
	 * switching the sub-portal to CEETM mode. Publish ceetm_tx_paused and
	 * synchronize_net() FIRST so any in-flight dpaa_eth_cgscn(0) that read
	 * false has already exited before netif_tx_disable() stops the queues. */
	if (netif_running(net_dev)) {
		WRITE_ONCE(priv->ceetm_tx_paused, true);
		synchronize_net();
		netif_tx_disable(net_dev);
		stopped_tx = true;
	}
	err = dpaa_ceetm_drain_plain_tx_fqs(priv);
	if (err) {
		NL_SET_ERR_MSG_MOD(opt->extack,
				   "plain TX queues did not drain before CEETM enable");
		goto err_wake_tx;
	}

	err = qman_ceetm_sp_claim(&ceetm->sp, dcp, sp_id);
	if (err) {
		NL_SET_ERR_MSG_MOD(opt->extack, "CEETM sub-portal claim failed");
		goto err_wake_tx;
	}

	err = qman_ceetm_lni_claim(&ceetm->lni, dcp, sp_id);
	if (err) {
		NL_SET_ERR_MSG_MOD(opt->extack, "CEETM LNI claim failed");
		goto err_release_sp;
	}

	err = qman_ceetm_sp_set_lni(ceetm->sp, ceetm->lni);
	if (err) {
		NL_SET_ERR_MSG_MOD(opt->extack, "CEETM sub-portal->LNI map failed");
		goto err_release_lni;
	}

	err = qman_ceetm_lni_enable_shaper(ceetm->lni, 1, DPAA_CEETM_LNI_OAL);
	if (err) {
		NL_SET_ERR_MSG_MOD(opt->extack, "CEETM LNI shaper enable failed");
		goto err_release_lni;
	}
	{
		struct qm_ceetm_rate inf = QM_CEETM_RATE_INFINITE;
		struct qm_ceetm_rate zero = { 0, 0 };

		err = qman_ceetm_lni_set_commit_rate(ceetm->lni, &inf,
						     DPAA_CEETM_BSIZE);
		if (!err)
			err = qman_ceetm_lni_set_excess_rate(ceetm->lni, &zero,
							     DPAA_CEETM_BSIZE);
		if (err) {
			NL_SET_ERR_MSG_MOD(opt->extack,
					   "CEETM LNI shaper rate config failed");
			goto err_release_lni;
		}
	}

	/* Publish BEFORE building the default channel (see DEFECT B notes). */
	smp_store_release(&priv->ceetm, ceetm);

	budget = dpaa_ceetm_port_frame_budget(priv, READ_ONCE(net_dev->mtu));
	err = dpaa_ceetm_chan_build(net_dev, ceetm, &ceetm->def_chan, 0,
				    DPAA_CEETM_DEFAULT_RATE_BPS,
				    DPAA_CEETM_DEFAULT_RATE_BPS);
	if (err) {
		NL_SET_ERR_MSG_MOD(opt->extack,
				   "CEETM default channel claim failed");
		goto err_unpublish;
	}
	err = dpaa_ceetm_cq_build(net_dev, &ceetm->def_chan, &ceetm->def,
				  DPAA_CEETM_CQ_PRIO, false, 0, budget);
	if (err) {
		dpaa_ceetm_chan_destroy(&ceetm->def_chan);
		NL_SET_ERR_MSG_MOD(opt->extack,
				   "CEETM default CQ build failed");
		goto err_unpublish;
	}
	smp_store_release(&ceetm->def.used, true);
	if (stopped_tx) {
		WRITE_ONCE(priv->ceetm_tx_paused, false);
		smp_mb();
		/* Plain-WQ TX FQs were drained to 0 and SW TX now targets CEETM
		 * LFQs governed by per-CQ CCGRs (not priv->cgr_data); only wake
		 * if plain CGR did not report congestion while paused. */
		if (!READ_ONCE(priv->ceetm_cgr_congested))
			netif_tx_wake_all_queues(net_dev);
		stopped_tx = false;
	}

	dpaa_fo_notify_egress_change(net_dev, DPAA_EGRESS_CEETM_POST_CHANGE, 0);

	netdev_info(net_dev,
		    "CEETM HTB offload installed (sp %u, lni %u, frame-budget %u)\n",
		    ceetm->sp->idx, ceetm->lni->idx, budget);
	qman_ceetm_dump_state("install", ceetm->sp, ceetm->lni,
			      ceetm->def_chan.channel, ceetm->def.cq,
			      ceetm->def.ccg);
	return 0;

err_unpublish:
	smp_store_release(&priv->ceetm, NULL);
	synchronize_net();
err_release_lni:
	qman_ceetm_lni_release(ceetm->lni);
	ceetm->sp->lni = NULL;
err_release_sp:
	qman_ceetm_sp_release(ceetm->sp);
err_wake_tx:
	if (stopped_tx) {
		WRITE_ONCE(priv->ceetm_tx_paused, false);
		smp_mb();
		if (!READ_ONCE(priv->ceetm_cgr_congested))
			netif_tx_wake_all_queues(net_dev);
	}
	dpaa_fo_notify_egress_change(net_dev, DPAA_EGRESS_CEETM_POST_CHANGE, 0);
err_free:
	kfree(ceetm);
	return err;
}

static int dpaa_ceetm_htb_destroy(struct net_device *net_dev,
				  struct tc_htb_qopt_offload *opt)
{
	struct dpaa_priv *priv = netdev_priv(net_dev);
	struct dpaa_ceetm *ceetm = priv->ceetm;
	int i;

	if (ceetm) {
		qman_ceetm_dump_state("destroy", ceetm->sp, ceetm->lni,
				      ceetm->def_chan.channel, ceetm->def.cq,
				      ceetm->def.ccg);
		for (i = 0; i < DPAA_CEETM_MAX_TXQS; i++)
			if (ceetm->leaf[i].used && ceetm->leaf[i].chan)
				qman_ceetm_dump_state("leaf",
						      NULL, NULL,
						      ceetm->leaf[i].chan->channel,
						      ceetm->leaf[i].cq,
						      ceetm->leaf[i].ccg);
	}

	dpaa_ceetm_teardown(priv);
	return 0;
}

static int dpaa_ceetm_htb_leaf_alloc(struct net_device *net_dev,
				     struct tc_htb_qopt_offload *opt)
{
	struct dpaa_priv *priv = netdev_priv(net_dev);
	struct dpaa_ceetm *ceetm = priv->ceetm;
	struct dpaa_ceetm_chan *ch = NULL;
	unsigned int next_active;
	bool own_chan = false, is_wbfs = false;
	u64 rate_bps, ceil_bps;
	u32 budget, share, hw_fqid, minor, wbfs_weight = 0;
	u8 cq_prio = DPAA_CEETM_CQ_PRIO;
	int slot, err;

	if (!ceetm) {
		NL_SET_ERR_MSG_MOD(opt->extack, "HTB offload not initialised");
		return -EINVAL;
	}

	minor = TC_H_MIN(opt->classid);
	if (!minor) {
		NL_SET_ERR_MSG_MOD(opt->extack,
				   "CEETM classid minor must be non-zero");
		return -EINVAL;
	}
	if (dpaa_ceetm_find_slot(ceetm, minor) >= 0) {
		NL_SET_ERR_MSG_MOD(opt->extack, "CEETM classid already exists");
		return -EEXIST;
	}

	for (slot = 0; slot < DPAA_CEETM_MAX_TXQS; slot++)
		if (!ceetm->leaf[slot].cq && !ceetm->leaf[slot].quarantined)
			break;
	if (slot == DPAA_CEETM_MAX_TXQS) {
		NL_SET_ERR_MSG_MOD(opt->extack, "no free CEETM class queue");
		return -ENOSPC;
	}

	budget = dpaa_ceetm_port_frame_budget(priv, READ_ONCE(net_dev->mtu));
	next_active = dpaa_ceetm_active_cqs(ceetm) + 1;
	share = budget / next_active;
	if (share < DPAA_CEETM_MIN_CQ_FRAMES) {
		NL_SET_ERR_MSG_MOD(opt->extack,
				   "CEETM frame budget exhausted for additional leaf");
		return -ENOSPC;
	}

	if (opt->parent_classid != TC_HTB_CLASSID_ROOT) {
		ch = dpaa_ceetm_find_inner_chan(ceetm,
						TC_H_MIN(opt->parent_classid));
		if (!ch) {
			NL_SET_ERR_MSG_MOD(opt->extack,
					   "DPAA CEETM supports at most 2 levels (root -> channel -> strict CQ 0..7 / WBFS CQ 8..15)");
			return -EOPNOTSUPP;
		}
		if (ch->quarantined) {
			NL_SET_ERR_MSG_MOD(opt->extack,
					   "CEETM parent channel is quarantined");
			return -EBUSY;
		}
		if (opt->quantum) {
			struct qm_ceetm_weight_code wc;
			int wbfs_cq;

			if (qman_ceetm_ratio2wbfs(opt->quantum, 1, &wc, 0)) {
				NL_SET_ERR_MSG_MOD(opt->extack,
						   "CEETM WBFS quantum must be a weight of 1..248");
				return -ERANGE;
			}
			wbfs_cq = dpaa_ceetm_alloc_wbfs_cq(ch);
			if (wbfs_cq < 0) {
				NL_SET_ERR_MSG_MOD(opt->extack,
						   "no free CEETM WBFS class queue on parent channel (max 8)");
				return -ENOSPC;
			}
			is_wbfs = true;
			wbfs_weight = opt->quantum;
			cq_prio = (u8)wbfs_cq;
		} else {
			cq_prio = min_t(u8, opt->prio,
					DPAA_CEETM_MAX_CQ_PRIO - 1);
			if (ch->cq_mask & BIT(cq_prio)) {
				NL_SET_ERR_MSG_MOD(opt->extack,
						   "CEETM strict-priority level already in use on parent channel; set distinct prio 0..7 or give quantum 1..248 for WBFS");
				return -EEXIST;
			}
		}
	}

	/* Pair PRE_CHANGE with POST_CHANGE so ASK2's transition fence stays
	 * balanced and any pre-existing HW flow carrying ct->mark == minor
	 * (previously on defcls) re-admits onto the new leaf class immediately. */
	err = dpaa_fo_notify_egress_change(net_dev,
					   DPAA_EGRESS_CEETM_PRE_CHANGE, 0);
	if (err) {
		dpaa_fo_notify_egress_change(net_dev,
					     DPAA_EGRESS_CEETM_POST_CHANGE, 0);
		NL_SET_ERR_MSG_MOD(opt->extack,
				   "failed to quiesce ASK flows before CEETM leaf_alloc");
		return -EBUSY;
	}

	if (opt->parent_classid == TC_HTB_CLASSID_ROOT) {
		/* 1-level root leaf: owns its own CEETM channel at CQ 0. */
		ch = dpaa_ceetm_alloc_chan(ceetm);
		if (!ch) {
			dpaa_fo_notify_egress_change(net_dev,
						     DPAA_EGRESS_CEETM_POST_CHANGE,
						     0);
			NL_SET_ERR_MSG_MOD(opt->extack,
					   "no free CEETM channel");
			return -ENOSPC;
		}
		rate_bps = opt->rate * 8;
		ceil_bps = opt->ceil * 8;
		err = dpaa_ceetm_chan_build(net_dev, ceetm, ch, minor,
					    rate_bps, ceil_bps);
		if (err) {
			dpaa_fo_notify_egress_change(net_dev,
						     DPAA_EGRESS_CEETM_POST_CHANGE,
						     0);
			NL_SET_ERR_MSG_MOD(opt->extack,
					   "CEETM leaf channel build failed");
			return err;
		}
		own_chan = true;
		cq_prio = DPAA_CEETM_CQ_PRIO;
	}

	err = dpaa_ceetm_cq_build(net_dev, ch, &ceetm->leaf[slot],
				  cq_prio, is_wbfs, wbfs_weight, share);
	if (err) {
		if (own_chan)
			dpaa_ceetm_chan_destroy(ch);
		dpaa_fo_notify_egress_change(net_dev,
					     DPAA_EGRESS_CEETM_POST_CHANGE, 0);
		NL_SET_ERR_MSG_MOD(opt->extack, "CEETM CQ build failed");
		return err;
	}
	WRITE_ONCE(ceetm->leaf[slot].classid, minor);

	err = netif_set_real_num_tx_queues(net_dev,
			max_t(unsigned int, net_dev->real_num_tx_queues,
			      ceetm->base_txq + slot + 1));
	if (err) {
		NL_SET_ERR_MSG_MOD(opt->extack, "failed to grow Tx queues");
		dpaa_ceetm_destroy_class(&ceetm->leaf[slot], false, own_chan,
					 false);
		dpaa_fo_notify_egress_change(net_dev,
					     DPAA_EGRESS_CEETM_POST_CHANGE, 0);
		return err;
	}

	/* Shrink sibling CQs to the new share before publishing this leaf. */
	dpaa_ceetm_rebalance_ccgs(priv, ceetm, READ_ONCE(net_dev->mtu));

	/* Publish the slot last so xmit and ASK2 see a complete class. */
	smp_store_release(&ceetm->leaf[slot].used, true);
	opt->qid = ceetm->base_txq + slot;
	dpaa_ceetm_dscp_resync(net_dev);

	hw_fqid = ceetm->leaf[slot].hw_lfq ?
		  (ceetm->leaf[slot].hw_lfq->idx & 0x00FFFFFFU) : 0;
	dpaa_fo_notify_egress_change(net_dev, DPAA_EGRESS_CEETM_POST_CHANGE,
				     (minor == ceetm->defcls) ? hw_fqid : 0);
	return 0;
}

/*
 * Promote a root-level leaf (parent_classid) into a 2-level inner CEETM
 * channel and assign its existing Tx queue slot to the first child leaf
 * (opt->classid) at strict-priority CQ min(opt->prio, 7).
 */
static int dpaa_ceetm_htb_leaf_to_inner(struct net_device *net_dev,
					struct tc_htb_qopt_offload *opt)
{
	struct dpaa_priv *priv = netdev_priv(net_dev);
	struct dpaa_ceetm *ceetm = priv->ceetm;
	struct dpaa_ceetm_class *cls;
	struct dpaa_ceetm_chan *ch;
	u32 parent_minor, child_minor, old_fqid, new_fqid, budget, share;
	u32 new_weight = 0;
	u8 old_prio, new_prio;
	bool new_wbfs = false;
	int slot, quiesce_rc, err;

	if (!ceetm) {
		NL_SET_ERR_MSG_MOD(opt->extack, "HTB offload not initialised");
		return -EINVAL;
	}

	parent_minor = TC_H_MIN(opt->parent_classid);
	child_minor = TC_H_MIN(opt->classid);
	slot = dpaa_ceetm_find_slot(ceetm, parent_minor);
	if (slot < 0) {
		NL_SET_ERR_MSG_MOD(opt->extack,
				   "parent class not found for CEETM promotion");
		return -ENOENT;
	}
	cls = &ceetm->leaf[slot];
	ch = cls->chan;
	if (!ch || ch->is_inner) {
		NL_SET_ERR_MSG_MOD(opt->extack,
				   "DPAA CEETM supports at most 2 levels (root -> channel -> strict CQ 0..7 / WBFS CQ 8..15)");
		return -EOPNOTSUPP;
	}

	old_prio = cls->cq_prio;
	if (opt->quantum) {
		struct qm_ceetm_weight_code wc;

		if (qman_ceetm_ratio2wbfs(opt->quantum, 1, &wc, 0)) {
			NL_SET_ERR_MSG_MOD(opt->extack,
					   "CEETM WBFS quantum must be a weight of 1..248");
			return -ERANGE;
		}
		new_wbfs = true;
		new_weight = opt->quantum;
		new_prio = DPAA_CEETM_WBFS_START;
	} else {
		new_prio = min_t(u8, opt->prio, DPAA_CEETM_MAX_CQ_PRIO - 1);
	}
	old_fqid = cls->hw_lfq ? (cls->hw_lfq->idx & 0x00FFFFFFU) : 0;

	quiesce_rc = dpaa_fo_notify_egress_change(net_dev,
						  DPAA_EGRESS_CEETM_PRE_CHANGE,
						  old_fqid);
	if (quiesce_rc) {
		dpaa_fo_notify_egress_change(net_dev,
					     DPAA_EGRESS_CEETM_POST_CHANGE, 0);
		NL_SET_ERR_MSG_MOD(opt->extack,
				   "failed to quiesce ASK flows before CEETM leaf_to_inner");
		return -EBUSY;
	}

	smp_store_release(&cls->used, false);
	synchronize_net();

	if (new_wbfs != cls->is_wbfs || new_prio != old_prio) {
		budget = dpaa_ceetm_port_frame_budget(priv,
						      READ_ONCE(net_dev->mtu));
		share = max_t(u32, budget / max_t(unsigned int,
						  dpaa_ceetm_active_cqs(ceetm),
						  1U), 1U);
		if (dpaa_ceetm_destroy_class(cls, false, false, false)) {
			dpaa_ceetm_dscp_resync(net_dev);
			dpaa_fo_notify_egress_change(net_dev,
						     DPAA_EGRESS_CEETM_POST_CHANGE,
						     0);
			NL_SET_ERR_MSG_MOD(opt->extack,
					   "CEETM CQ drain failed during promotion; channel quarantined");
			return -EBUSY;
		}
		err = dpaa_ceetm_cq_build(net_dev, ch, cls, new_prio,
					  new_wbfs, new_weight, share);
		if (err) {
			/* Attempt to restore parent at old_prio before failing. */
			if (!dpaa_ceetm_cq_build(net_dev, ch, cls, old_prio,
						 false, 0, share)) {
				WRITE_ONCE(cls->classid, parent_minor);
				smp_store_release(&cls->used, true);
			}
			dpaa_ceetm_dscp_resync(net_dev);
			dpaa_fo_notify_egress_change(net_dev,
						     DPAA_EGRESS_CEETM_POST_CHANGE,
						     0);
			NL_SET_ERR_MSG_MOD(opt->extack,
					   "CEETM child CQ build failed");
			return err;
		}
	}

	WRITE_ONCE(ch->is_inner, true);
	dpaa_ceetm_clear_red_state(cls);
	WRITE_ONCE(cls->red_soft_pending_handle, 0);
	WRITE_ONCE(cls->classid, child_minor);
	dpaa_ceetm_rebalance_ccgs(priv, ceetm, READ_ONCE(net_dev->mtu));
	smp_store_release(&cls->used, true);
	dpaa_ceetm_dscp_resync(net_dev);

	new_fqid = cls->hw_lfq ? (cls->hw_lfq->idx & 0x00FFFFFFU) : 0;
	dpaa_fo_notify_egress_change(net_dev, DPAA_EGRESS_CEETM_POST_CHANGE,
				     (child_minor == ceetm->defcls) ? new_fqid : 0);
	return 0;
}

static int dpaa_ceetm_htb_leaf_del(struct net_device *net_dev,
				   struct tc_htb_qopt_offload *opt)
{
	struct dpaa_priv *priv = netdev_priv(net_dev);
	struct dpaa_ceetm *ceetm = priv->ceetm;
	struct dpaa_ceetm_class *cls;
	struct dpaa_ceetm_chan *ch;
	u32 hw_fqid;
	int slot, quiesce_rc, err = 0;

	if (!ceetm)
		return -EINVAL;

	slot = dpaa_ceetm_find_slot(ceetm, TC_H_MIN(opt->classid));
	if (slot < 0)
		return -ENOENT;
	cls = &ceetm->leaf[slot];
	ch = cls->chan;

	hw_fqid = cls->hw_lfq ? (cls->hw_lfq->idx & 0x00FFFFFFU) : 0;

	/* Fence and retire every ASK2 flow targeting this leaf's hw_lfq BEFORE
	 * tearing down its CQ/channel. */
	quiesce_rc = dpaa_fo_notify_egress_change(net_dev,
						  DPAA_EGRESS_CEETM_PRE_CHANGE,
						  hw_fqid);
	if (quiesce_rc && opt->command != TC_HTB_LEAF_DEL_LAST_FORCE) {
		/* Non-forced delete/demote: abort cleanly BEFORE unpublishing
		 * cls->used or poisoning a shared 2-level parent channel. */
		dpaa_fo_notify_egress_change(net_dev,
					     DPAA_EGRESS_CEETM_POST_CHANGE, 0);
		NL_SET_ERR_MSG_MOD(opt->extack,
				   "failed to quiesce ASK flows before CEETM leaf_del");
		return -EBUSY;
	}

	smp_store_release(&cls->used, false);
	synchronize_net();

	if (opt->command == TC_HTB_LEAF_DEL_LAST && ch && ch->is_inner &&
	    hweight16(ch->live_cq_mask) == 1) {
		/* sch_htb demotes the inner parent channel back into a leaf on
		 * the SAME Tx queue slot when its last child is deleted. */
		if (cls->is_wbfs || cls->cq_prio != DPAA_CEETM_CQ_PRIO) {
			u8 old_prio = cls->cq_prio;
			bool old_wbfs = cls->is_wbfs;
			u32 old_weight = cls->wbfs_weight;
			u32 old_classid = cls->classid;
			u32 budget = dpaa_ceetm_port_frame_budget(priv,
							READ_ONCE(net_dev->mtu));
			u32 share = max_t(u32, budget / max_t(unsigned int,
						dpaa_ceetm_active_cqs(ceetm),
						1U), 1U);

			if (ch->cq_mask & BIT(DPAA_CEETM_CQ_PRIO)) {
				/* Strict CQ 0 is held by a quarantined sibling:
				 * cannot demote cleanly onto CQ 0. */
				smp_store_release(&cls->used, true);
				dpaa_fo_notify_egress_change(net_dev,
							     DPAA_EGRESS_CEETM_POST_CHANGE,
							     0);
				NL_SET_ERR_MSG_MOD(opt->extack,
						   "CEETM CQ 0 quarantined; cannot demote parent channel");
				return -EBUSY;
			}

			if (dpaa_ceetm_destroy_class(cls, false, false, false)) {
				dpaa_ceetm_dscp_resync(net_dev);
				dpaa_fo_notify_egress_change(net_dev,
							     DPAA_EGRESS_CEETM_POST_CHANGE,
							     0);
				NL_SET_ERR_MSG_MOD(opt->extack,
						   "CEETM CQ drain failed during demotion; channel quarantined");
				return -EBUSY;
			}
			err = dpaa_ceetm_cq_build(net_dev, ch, cls,
						  DPAA_CEETM_CQ_PRIO, false, 0,
						  share);
			if (err) {
				if (!dpaa_ceetm_cq_build(net_dev, ch, cls,
							 old_prio, old_wbfs,
							 old_weight, share)) {
					WRITE_ONCE(cls->classid, old_classid);
					smp_store_release(&cls->used, true);
				}
				dpaa_ceetm_dscp_resync(net_dev);
				dpaa_fo_notify_egress_change(net_dev,
							     DPAA_EGRESS_CEETM_POST_CHANGE,
							     0);
				NL_SET_ERR_MSG_MOD(opt->extack,
						   "CEETM parent CQ rebuild failed during demotion");
				return err;
			}
		}
		WRITE_ONCE(ch->is_inner, false);
		dpaa_ceetm_clear_red_state(cls);
		WRITE_ONCE(cls->red_soft_pending_handle, 0);
		WRITE_ONCE(cls->classid, ch->classid);
		smp_store_release(&cls->used, true);
		dpaa_ceetm_rebalance_ccgs(priv, ceetm, READ_ONCE(net_dev->mtu));
		dpaa_ceetm_dscp_resync(net_dev);
		dpaa_fo_notify_egress_change(net_dev,
					     DPAA_EGRESS_CEETM_POST_CHANGE, 0);
		return 0;
	}

	dpaa_ceetm_destroy_class(cls, quiesce_rc != 0, true, false);
	dpaa_ceetm_rebalance_ccgs(priv, ceetm, READ_ONCE(net_dev->mtu));
	dpaa_ceetm_dscp_resync(net_dev);

	dpaa_fo_notify_egress_change(net_dev, DPAA_EGRESS_CEETM_POST_CHANGE, 0);
	return 0;
}

static int dpaa_ceetm_htb_node_modify(struct net_device *net_dev,
				      struct tc_htb_qopt_offload *opt)
{
	struct dpaa_priv *priv = netdev_priv(net_dev);
	struct dpaa_ceetm *ceetm = priv->ceetm;
	struct dpaa_ceetm_chan *ch;
	u32 minor = TC_H_MIN(opt->classid);
	u64 rate_bps, ceil_bps;
	int slot, err;

	if (!ceetm)
		return -EINVAL;

	/* Inner 2-level channel rate/ceil modify (live via QMan MC, 0 drops). */
	ch = dpaa_ceetm_find_inner_chan(ceetm, minor);
	if (ch) {
		rate_bps = opt->rate * 8;
		ceil_bps = opt->ceil * 8;
		err = dpaa_ceetm_chan_set_rates(net_dev, ch, rate_bps, ceil_bps);
		if (err) {
			NL_SET_ERR_MSG_MOD(opt->extack,
					   "failed to update CEETM channel shaper rate");
			return err;
		}
		return 0;
	}

	slot = dpaa_ceetm_find_slot(ceetm, minor);
	if (slot < 0)
		return -ENOENT;
	ch = ceetm->leaf[slot].chan;
	if (!ch || !ch->shaped) {
		NL_SET_ERR_MSG_MOD(opt->extack, "class is not shaped");
		return -EINVAL;
	}
	if (ch->is_inner) {
		struct dpaa_ceetm_class *cls = &ceetm->leaf[slot];

		if (cls->is_wbfs) {
			struct qm_ceetm_weight_code wc;

			/* sch_htb passes hopt->quantum == 0 when a rate/ceil
			 * change omits quantum; keep the existing WBFS weight. */
			if (!opt->quantum)
				return 0;
			if (opt->quantum != cls->wbfs_weight) {
				int qrc;

				if (qman_ceetm_ratio2wbfs(opt->quantum, 1,
							  &wc, 0)) {
					NL_SET_ERR_MSG_MOD(opt->extack,
							   "CEETM WBFS quantum must be a weight of 1..248");
					return -ERANGE;
				}
				/* Quiesce ASK2 HW flows so equal-weight pool
				 * membership changes never leave unmarked bulk
				 * HW flows pinned on an unequal-weight leaf
				 * (P1-3 fix). */
				qrc = dpaa_fo_notify_egress_change(
					net_dev, DPAA_EGRESS_CEETM_PRE_CHANGE,
					0);
				if (qrc) {
					dpaa_fo_notify_egress_change(
						net_dev,
						DPAA_EGRESS_CEETM_POST_CHANGE,
						0);
					NL_SET_ERR_MSG_MOD(opt->extack,
							   "failed to quiesce ASK flows before WBFS weight change");
					return -EBUSY;
				}
				err = qman_ceetm_set_queue_weight(cls->cq, &wc);
				if (!err)
					WRITE_ONCE(cls->wbfs_weight,
						   opt->quantum);
				dpaa_fo_notify_egress_change(
					net_dev, DPAA_EGRESS_CEETM_POST_CHANGE,
					0);
				if (err) {
					NL_SET_ERR_MSG_MOD(opt->extack,
							   "failed to update CEETM WBFS queue weight");
					return err;
				}
			}
			return 0;
		}

		/* Child strict CQs share their parent inner channel's shaper
		 * and cannot change CQ index or switch to WBFS in place. */
		if (opt->quantum) {
			NL_SET_ERR_MSG_MOD(opt->extack,
					   "delete and re-add class to switch between strict priority and WBFS");
			return -EOPNOTSUPP;
		}
		if (min_t(u8, opt->prio, DPAA_CEETM_MAX_CQ_PRIO - 1) !=
		    cls->cq_prio) {
			NL_SET_ERR_MSG_MOD(opt->extack,
					   "delete and re-add class to change CEETM priority");
			return -EOPNOTSUPP;
		}
		return 0;
	}

	rate_bps = opt->rate * 8;
	ceil_bps = opt->ceil * 8;
	err = dpaa_ceetm_chan_set_rates(net_dev, ch, rate_bps, ceil_bps);
	if (err) {
		NL_SET_ERR_MSG_MOD(opt->extack,
				   "failed to update CEETM shaper rate");
		return err;
	}
	return 0;
}

static int dpaa_ceetm_htb_query(struct net_device *net_dev,
				struct tc_htb_qopt_offload *opt)
{
	struct dpaa_priv *priv = netdev_priv(net_dev);
	struct dpaa_ceetm *ceetm = priv->ceetm;
	int slot;

	if (!ceetm)
		return -EINVAL;

	slot = dpaa_ceetm_find_slot(ceetm, TC_H_MIN(opt->classid));
	if (slot < 0)
		return -ENOENT;
	opt->qid = ceetm->base_txq + slot;
	return 0;
}

int dpaa_ceetm_setup_htb(struct net_device *net_dev, void *type_data)
{
	struct tc_htb_qopt_offload *opt = type_data;

	ASSERT_RTNL();

	switch (opt->command) {
	case TC_HTB_CREATE:
		return dpaa_ceetm_htb_create(net_dev, opt);
	case TC_HTB_DESTROY:
		return dpaa_ceetm_htb_destroy(net_dev, opt);
	case TC_HTB_LEAF_ALLOC_QUEUE:
		return dpaa_ceetm_htb_leaf_alloc(net_dev, opt);
	case TC_HTB_LEAF_TO_INNER:
		return dpaa_ceetm_htb_leaf_to_inner(net_dev, opt);
	case TC_HTB_LEAF_DEL:
	case TC_HTB_LEAF_DEL_LAST:
	case TC_HTB_LEAF_DEL_LAST_FORCE:
		return dpaa_ceetm_htb_leaf_del(net_dev, opt);
	case TC_HTB_NODE_MODIFY:
		return dpaa_ceetm_htb_node_modify(net_dev, opt);
	case TC_HTB_LEAF_QUERY_QUEUE:
		return dpaa_ceetm_htb_query(net_dev, opt);
	default:
		return -EOPNOTSUPP;
	}
}

/* Synchronously refresh ASK2 soft-qdisc state when a leaf RED qdisc changes
 * offload status (withdraw HW flows immediately if falling back to SW). */
static void dpaa_ceetm_red_sync_ask(struct net_device *net_dev,
				    bool withdraw_hw)
{
	if (withdraw_hw)
		dpaa_fo_notify_egress_change(net_dev,
					     DPAA_EGRESS_CEETM_PRE_CHANGE, 0);
	dpaa_fo_notify_egress_change(net_dev,
				     DPAA_EGRESS_CEETM_POST_CHANGE, 0);
}

static u32 dpaa_ceetm_current_share(const struct dpaa_priv *priv,
				    const struct dpaa_ceetm *ceetm,
				    unsigned int mtu)
{
	unsigned int n = max_t(unsigned int, dpaa_ceetm_active_cqs(ceetm), 1U);
	u32 budget = dpaa_ceetm_port_frame_budget(priv, mtu);

	return max_t(u32, budget / n, 1U);
}

int dpaa_ceetm_setup_red(struct net_device *net_dev, void *type_data)
{
	struct tc_red_qopt_offload *opt = type_data;
	struct dpaa_priv *priv = netdev_priv(net_dev);
	struct dpaa_ceetm *ceetm;
	struct dpaa_ceetm_class *cls = NULL;
	const char *refused = NULL;
	unsigned int mtu;
	bool running;
	u32 share;
	int slot, rc = -EOPNOTSUPP;

	ASSERT_RTNL();
	ceetm = priv->ceetm;
	if (!ceetm) {
		if (opt->command == TC_RED_REPLACE)
			netdev_warn(net_dev,
				    "RED qdisc %x: not offloaded: CEETM HTB offload is not installed\n",
				    TC_H_MAJ(opt->handle) >> 16);
		return -EOPNOTSUPP;
	}

	mtu = READ_ONCE(net_dev->mtu);
	share = dpaa_ceetm_current_share(priv, ceetm, mtu);

	if (opt->parent == TC_H_ROOT) {
		refused = "root qdisc has no single CEETM class queue";
	} else if ((TC_H_MAJ(opt->parent) >> 16) != ceetm->htb_maj) {
		refused = "not grafted directly on an offloaded HTB class";
	} else if (dpaa_ceetm_find_inner_chan(ceetm, TC_H_MIN(opt->parent))) {
		refused = "inner CEETM channel has no congestion group";
	} else {
		slot = dpaa_ceetm_find_slot(ceetm, TC_H_MIN(opt->parent));
		if (slot < 0)
			refused = "no such offloaded HTB leaf class";
		else
			cls = &ceetm->leaf[slot];
	}

	running = cls && cls->red_active && cls->red_handle == opt->handle;

	switch (opt->command) {
	case TC_RED_REPLACE: {
		bool had;
		u32 had_handle, had_min, had_max, had_prob, had_limit;

		if (!refused && opt->set.is_ecn) {
			refused = "CEETM hardware drops and cannot mark ECN";
		} else if (!refused && opt->set.is_nodrop) {
			refused = "CEETM hardware cannot offload nodrop";
		} else if (!refused &&
			   (!opt->set.limit || !opt->set.max ||
			    opt->set.max <= opt->set.min)) {
			refused = "RED requires limit > 0 and min < max";
			rc = -EINVAL;
		} else if (!refused && cls) {
			had = cls->red_active;
			had_handle = cls->red_handle;
			had_min = cls->red_min;
			had_max = cls->red_max;
			had_prob = cls->red_prob;
			had_limit = cls->red_limit;

			cls->red_min = opt->set.min;
			cls->red_max = opt->set.max;
			cls->red_prob = opt->set.probability;
			cls->red_limit = opt->set.limit;
			if (!dpaa_ceetm_set_ccg_wred(cls, share, mtu)) {
				if (had && !running) {
					cls->red_displaced = true;
					cls->red_displaced_handle = had_handle;
					cls->red_displaced_min = had_min;
					cls->red_displaced_max = had_max;
					cls->red_displaced_prob = had_prob;
					cls->red_displaced_limit = had_limit;
				}
				WRITE_ONCE(cls->red_soft_pending_handle, 0);
				WRITE_ONCE(cls->red_handle, opt->handle);
				smp_store_release(&cls->red_active, true);
				dpaa_ceetm_red_sync_ask(net_dev, false);
				return 0;
			}
			refused = "QMan CEETM rejected WRED CCGR config";
			rc = -EIO;
			cls->red_min = had_min;
			cls->red_max = had_max;
			cls->red_prob = had_prob;
			cls->red_limit = had_limit;
			if (had && !running) {
				if (dpaa_ceetm_set_ccg_wred(cls, share, mtu)) {
					dpaa_ceetm_clear_red_state(cls);
					dpaa_ceetm_set_ccg_frames(cls->ccg,
								  share);
				}
			}
		}

		if (running && cls) {
			dpaa_ceetm_clear_red_state(cls);
			dpaa_ceetm_set_ccg_frames(cls->ccg, share);
		}
		if (cls) {
			/* Latch pending soft-RED handle BEFORE POST_CHANGE so
			 * ask_tc_qdisc_is_soft() sees the rejected RED even
			 * though qdisc_create() runs prior to qdisc_graft()
			 * (P0-2 fix). */
			WRITE_ONCE(cls->red_soft_pending_handle, opt->handle);
			dpaa_ceetm_red_sync_ask(net_dev, true);
		}
		netdev_warn(net_dev,
			    "RED qdisc %x: not offloaded: %s\n",
			    TC_H_MAJ(opt->handle) >> 16,
			    refused ? refused : "unsupported");
		return rc;
	}

	case TC_RED_DESTROY:
		if (!cls)
			return -EOPNOTSUPP;
		if (cls->red_soft_pending_handle == opt->handle)
			WRITE_ONCE(cls->red_soft_pending_handle, 0);
		if (cls->red_displaced &&
		    cls->red_displaced_handle == opt->handle) {
			cls->red_displaced = false;
			cls->red_displaced_handle = 0;
			dpaa_ceetm_red_sync_ask(net_dev, false);
			return 0;
		}
		if (!running) {
			dpaa_ceetm_red_sync_ask(net_dev, false);
			return 0;
		}
		if (cls->red_displaced) {
			cls->red_displaced = false;
			cls->red_min = cls->red_displaced_min;
			cls->red_max = cls->red_displaced_max;
			cls->red_prob = cls->red_displaced_prob;
			cls->red_limit = cls->red_displaced_limit;
			WRITE_ONCE(cls->red_handle, cls->red_displaced_handle);
			cls->red_displaced_handle = 0;
			if (dpaa_ceetm_set_ccg_wred(cls, share, mtu)) {
				dpaa_ceetm_clear_red_state(cls);
				dpaa_ceetm_set_ccg_frames(cls->ccg, share);
			}
			dpaa_ceetm_red_sync_ask(net_dev, false);
			return 0;
		}
		dpaa_ceetm_clear_red_state(cls);
		dpaa_ceetm_set_ccg_frames(cls->ccg, share);
		dpaa_ceetm_red_sync_ask(net_dev, false);
		return 0;

	case TC_RED_STATS:
		/* Returning 0 sets TCQ_F_OFFLOADED in qdisc_offload_dump_helper.
		 * Keep SW bstats/qstats untouched; HW rejects live in CEETM CCG. */
		return running ? 0 : -EOPNOTSUPP;

	case TC_RED_XSTATS:
		/* Do not mutate q->stats unlocked against red_enqueue() (P1-3). */
		return running ? 0 : -EOPNOTSUPP;

	case TC_RED_GRAFT:
		/* Grafting a user child qdisc under RED cannot be offloaded;
		 * demote to tail-drop and immediately withdraw HW flows so the
		 * software child qdisc is never bypassed. */
		if (opt->child_handle && running && cls) {
			dpaa_ceetm_clear_red_state(cls);
			WRITE_ONCE(cls->red_soft_pending_handle, opt->handle);
			dpaa_ceetm_set_ccg_frames(cls->ccg, share);
			dpaa_ceetm_red_sync_ask(net_dev, true);
		}
		return -EOPNOTSUPP;

	default:
		return -EOPNOTSUPP;
	}
}

bool dpaa_ceetm_red_soft_pending(struct net_device *dev)
{
	struct dpaa_priv *priv;
	struct dpaa_ceetm *ceetm;
	bool pending = false;
	int i;

	if (!dev || !dpaa_get_rx_fman_port(dev))
		return false;

	priv = netdev_priv(dev);
	rcu_read_lock();
	ceetm = smp_load_acquire(&priv->ceetm);
	if (ceetm) {
		for (i = 0; i < DPAA_CEETM_MAX_TXQS; i++) {
			if (!smp_load_acquire(&ceetm->leaf[i].used) ||
			    READ_ONCE(ceetm->leaf[i].quarantined))
				continue;
			if (READ_ONCE(ceetm->leaf[i].red_soft_pending_handle)) {
				pending = true;
				break;
			}
		}
	}
	rcu_read_unlock();
	return pending;
}
EXPORT_SYMBOL_GPL(dpaa_ceetm_red_soft_pending);

bool dpaa_ceetm_red_qdisc_offloaded(struct net_device *dev, u32 handle)
{
	struct dpaa_priv *priv;
	struct dpaa_ceetm *ceetm;
	bool offloaded = false;
	int i;

	if (!dev || !handle || !dpaa_get_rx_fman_port(dev))
		return false;

	priv = netdev_priv(dev);
	rcu_read_lock();
	ceetm = smp_load_acquire(&priv->ceetm);
	if (ceetm) {
		for (i = 0; i < DPAA_CEETM_MAX_TXQS; i++) {
			if (!smp_load_acquire(&ceetm->leaf[i].used) ||
			    READ_ONCE(ceetm->leaf[i].quarantined))
				continue;
			if (smp_load_acquire(&ceetm->leaf[i].red_active) &&
			    READ_ONCE(ceetm->leaf[i].red_handle) == handle) {
				offloaded = true;
				break;
			}
		}
	}
	rcu_read_unlock();
	return offloaded;
}
EXPORT_SYMBOL_GPL(dpaa_ceetm_red_qdisc_offloaded);

struct qman_fq *dpaa_ceetm_egress_fq(struct dpaa_ceetm *ceetm,
				     int queue_mapping)
{
	unsigned int idx;

	if (queue_mapping >= (int)ceetm->base_txq) {
		idx = queue_mapping - ceetm->base_txq;
		if (idx < DPAA_CEETM_MAX_TXQS &&
		    smp_load_acquire(&ceetm->leaf[idx].used))
			return &ceetm->leaf[idx].fq;
	}
	if (smp_load_acquire(&ceetm->def.used))
		return &ceetm->def.fq;
	return NULL;
}

/**
 * dpaa_ceetm_is_active - report whether CEETM HTB offload owns @dev's egress
 * @dev: DPAA Ethernet netdev
 */
bool dpaa_ceetm_is_active(struct net_device *dev)
{
	struct dpaa_priv *priv;

	if (!dev || !dpaa_get_rx_fman_port(dev))
		return false;
	priv = netdev_priv(dev);
	return smp_load_acquire(&priv->ceetm) != NULL;
}
EXPORT_SYMBOL_GPL(dpaa_ceetm_is_active);

/**
 * dpaa_ceetm_fqid_is_live - check if @fqid belongs to an active CEETM leaf
 * @dev: physical DPAA Ethernet netdev
 * @fqid: 24-bit CEETM hw_lfq FQID (0xF00xxx)
 */
bool dpaa_ceetm_fqid_is_live(struct net_device *dev, u32 fqid)
{
	struct dpaa_priv *priv;
	struct dpaa_ceetm *ceetm;
	bool live = false;
	int i;

	if (!dev || !fqid || !dpaa_get_rx_fman_port(dev))
		return false;

	priv = netdev_priv(dev);
	rcu_read_lock();
	ceetm = smp_load_acquire(&priv->ceetm);
	if (ceetm) {
		for (i = 0; i < DPAA_CEETM_MAX_TXQS; i++) {
			struct qm_ceetm_lfq *hw_lfq;

			if (!smp_load_acquire(&ceetm->leaf[i].used) ||
			    READ_ONCE(ceetm->leaf[i].quarantined))
				continue;
			hw_lfq = READ_ONCE(ceetm->leaf[i].hw_lfq);
			if (hw_lfq && ((hw_lfq->idx & 0x00FFFFFFU) == fqid)) {
				live = true;
				break;
			}
		}
	}
	rcu_read_unlock();
	return live;
}
EXPORT_SYMBOL_GPL(dpaa_ceetm_fqid_is_live);

/**
 * dpaa_ceetm_offload_egress_fqid - resolve ASK2's no-confirm CEETM hw_lfq FQID
 * @dev: physical DPAA Ethernet netdev
 * @classid: HTB major:minor or minor-only classid (from ct->mark), or 0
 * @fqid: receives the 24-bit CEETM LFQID (0xF00xxx) when CEETM is active,
 *	or 0 when CEETM is inactive
 *
 * Return:
 *   0 if CEETM is inactive on @dev (caller uses conventional WQ6 TX FQs),
 *   1 if CEETM is active and @fqid was populated with the target leaf's hw_lfq
 *     (matching @classid, or falling back to @ceetm->defcls when unmarked or
 *     not matching any live leaf),
 *   -EAGAIN if CEETM is active but no live leaf backs @classid or defcls
 *     (fail closed to software; never target DEFECT-B raw default or WQ6).
 */
int dpaa_ceetm_offload_egress_fqid_hashed(struct net_device *dev, u32 classid,
					  u32 flow_hash, u32 *fqid)
{
	struct dpaa_priv *priv;
	struct dpaa_ceetm *ceetm;
	struct qm_ceetm_lfq *hw_lfq;
	u32 want;
	int slot = -1, ret = -EAGAIN;

	if (!fqid)
		return -EINVAL;
	*fqid = 0;

	if (!dev || !dpaa_get_rx_fman_port(dev))
		return 0;

	priv = netdev_priv(dev);
	/* RCU read section pairs with synchronize_net() in teardown/leaf_del
	 * so ceetm and leaf->hw_lfq cannot be freed while we inspect them. */
	rcu_read_lock();
	ceetm = smp_load_acquire(&priv->ceetm);
	if (!ceetm) {
		ret = 0;
		goto out_unlock;
	}

	want = dpaa_ceetm_decode_classid(ceetm, classid);
	if (want)
		slot = dpaa_ceetm_find_live_slot(ceetm, want);
	if (slot < 0 && ceetm->defcls) {
		slot = dpaa_ceetm_find_live_slot(ceetm, ceetm->defcls);
		if (slot >= 0)
			slot = dpaa_ceetm_pick_wbfs_slot(priv, ceetm, slot,
							 flow_hash);
	}
	if (slot < 0)
		goto out_unlock;

	hw_lfq = READ_ONCE(ceetm->leaf[slot].hw_lfq);
	if (!hw_lfq)
		goto out_unlock;
	*fqid = hw_lfq->idx & 0x00FFFFFFU;
	ret = 1;

out_unlock:
	rcu_read_unlock();
	return ret;
}
EXPORT_SYMBOL_GPL(dpaa_ceetm_offload_egress_fqid_hashed);

int dpaa_ceetm_offload_egress_fqid(struct net_device *dev, u32 classid,
				   u32 *fqid)
{
	return dpaa_ceetm_offload_egress_fqid_hashed(dev, classid, 0, fqid);
}
EXPORT_SYMBOL_GPL(dpaa_ceetm_offload_egress_fqid);

ssize_t dpaa_ceetm_wbfs_hash_show(struct net_device *net_dev, char *buf)
{
	struct dpaa_priv *priv = netdev_priv(net_dev);

	return sysfs_emit(buf, "%u\n", READ_ONCE(priv->ceetm_wbfs_hash) ? 1 : 0);
}

ssize_t dpaa_ceetm_wbfs_hash_store(struct net_device *net_dev,
				   const char *buf, size_t count)
{
	struct dpaa_priv *priv = netdev_priv(net_dev);
	bool enable;
	int err;

	err = kstrtobool(buf, &enable);
	if (err)
		return err;

	/* Avoid kernfs kn->active vs rtnl_lock ABBA deadlock during
	 * unregister_netdev() (matches net/core/net-sysfs.c). */
	if (!rtnl_trylock())
		return restart_syscall();
	if (READ_ONCE(net_dev->reg_state) != NETREG_REGISTERED) {
		rtnl_unlock();
		return -ENODEV;
	}
	if (enable != READ_ONCE(priv->ceetm_wbfs_hash)) {
		if (priv->ceetm) {
			err = dpaa_fo_notify_egress_change(
				net_dev, DPAA_EGRESS_CEETM_PRE_CHANGE, 0);
			if (err) {
				dpaa_fo_notify_egress_change(
					net_dev, DPAA_EGRESS_CEETM_POST_CHANGE,
					0);
				rtnl_unlock();
				return -EBUSY;
			}
			WRITE_ONCE(priv->ceetm_wbfs_hash, enable);
			dpaa_fo_notify_egress_change(
				net_dev, DPAA_EGRESS_CEETM_POST_CHANGE, 0);
		} else {
			WRITE_ONCE(priv->ceetm_wbfs_hash, enable);
		}
	}
	rtnl_unlock();
	return count;
}

/* -------------------------------------------------------------------------
 * G10 Phase 3: Single-port per-packet MURAM DSCP -> CEETM CQ map (F-328).
 *
 * Bridges standard Linux clsact egress flower rules:
 *   tc qdisc add dev ethX clsact
 *   tc filter add dev ethX egress protocol ip flower ip_dscp <d> \
 *       action skbedit priority <maj:min>
 * onto microcode 210.10.1's 64-entry MURAM dscp_fqid_map (at fe_frag_off+28)
 * and a symmetric lockless SW array consulted by dpaa_ceetm_select_queue().
 *
 * Precedence: ct->mark / skb->priority / skb->mark strictly outranks DSCP.
 * Singleton: only one port per FMan may hold the MURAM table at a time;
 * turning off drains all HW records on the port via PRE_CHANGE before another
 * port may claim it (or holds it in RETIRING/poisoned if quiesce fails).
 * ------------------------------------------------------------------------- */

#define DPAA_CEETM_DSCP_MASK		0xfcU
#define DPAA_CEETM_DSCP_SHIFT		2
#define DPAA_CEETM_DSCP_COUNT		64

enum dpaa_ceetm_dscp_state {
	DPAA_DSCP_OFF = 0,
	DPAA_DSCP_CLAIMED,
	DPAA_DSCP_ON,
	DPAA_DSCP_RETIRING,
};

struct dpaa_ceetm_dscp_filter {
	struct list_head list;
	struct rcu_head rcu;
	unsigned long cookie;
	u32 classid;		/* raw HTB handle from action skbedit priority */
	u32 prio;
	u32 chain;
	__be16 n_proto;
	u8 dscp;
};

/* All mutations run under RTNL; lockless readers use RCU / acquire-release. */
static struct net_device *dpaa_ceetm_dscp_owner;
static enum dpaa_ceetm_dscp_state dpaa_ceetm_dscp_state;
static bool dpaa_ceetm_dscp_poisoned;
static LIST_HEAD(dpaa_ceetm_dscp_filters);
static LIST_HEAD(dpaa_ceetm_dscp_block_cb_list);
static u32 dpaa_ceetm_dscp_sw_minor[DPAA_CEETM_DSCP_COUNT];

static struct fman *dpaa_ceetm_get_fman(struct net_device *net_dev)
{
	struct dpaa_priv *priv;

	if (!net_dev || !dpaa_get_rx_fman_port(net_dev))
		return NULL;
	priv = netdev_priv(net_dev);
	if (!priv->mac_dev || !priv->mac_dev->fman_dev)
		return NULL;
	/* Avoid fman_bind() here: it takes a get_device() ref on every call. */
	return dev_get_drvdata(priv->mac_dev->fman_dev);
}

static struct dpaa_ceetm_dscp_filter *
dpaa_ceetm_dscp_find_cookie(unsigned long cookie)
{
	struct dpaa_ceetm_dscp_filter *f;

	ASSERT_RTNL();
	list_for_each_entry(f, &dpaa_ceetm_dscp_filters, list)
		if (f->cookie == cookie)
			return f;
	return NULL;
}

static bool dpaa_ceetm_dscp_replaces(const struct dpaa_ceetm_dscp_filter *a,
				     const struct dpaa_ceetm_dscp_filter *b)
{
	return a->prio == b->prio && a->chain == b->chain &&
	       a->n_proto == b->n_proto && a->dscp == b->dscp;
}

static bool dpaa_ceetm_dscp_dual_stack_pair(const struct dpaa_ceetm_dscp_filter *a,
					    const struct dpaa_ceetm_dscp_filter *b)
{
	return a->dscp == b->dscp && a->classid == b->classid &&
	       ((a->n_proto == htons(ETH_P_IP) &&
		 b->n_proto == htons(ETH_P_IPV6)) ||
		(a->n_proto == htons(ETH_P_IPV6) &&
		 b->n_proto == htons(ETH_P_IP)));
}

static int dpaa_ceetm_dscp_resolve_leaf(struct net_device *net_dev,
					u32 raw_classid, u32 *out_minor,
					u32 *out_hw_fqid)
{
	struct dpaa_priv *priv = netdev_priv(net_dev);
	struct dpaa_ceetm *ceetm = priv->ceetm;
	u32 minor;
	int slot;

	if (!ceetm)
		return -EOPNOTSUPP;
	minor = dpaa_ceetm_decode_classid(ceetm, raw_classid);
	if (!minor)
		return -EINVAL;
	slot = dpaa_ceetm_find_slot(ceetm, minor);
	if (slot < 0 || !ceetm->leaf[slot].used || !ceetm->leaf[slot].hw_lfq)
		return -ENOENT;
	if (out_minor)
		*out_minor = minor;
	if (out_hw_fqid)
		*out_hw_fqid = ceetm->leaf[slot].hw_lfq->idx & 0x00FFFFFFU;
	return 0;
}

/* Re-resolve every installed DSCP filter into both MURAM and the lockless SW
 * lookup array. Called under RTNL after any filter or HTB leaf mutation. */
static void dpaa_ceetm_dscp_resync(struct net_device *net_dev)
{
	struct dpaa_ceetm_dscp_filter *f;
	struct fman *fm;
	u32 sw_next[DPAA_CEETM_DSCP_COUNT];
	u32 hw_next[DPAA_CEETM_DSCP_COUNT];
	unsigned int d;

	ASSERT_RTNL();
	if (dpaa_ceetm_dscp_owner != net_dev ||
	    (dpaa_ceetm_dscp_state != DPAA_DSCP_CLAIMED &&
	     dpaa_ceetm_dscp_state != DPAA_DSCP_ON))
		return;

	memset(sw_next, 0, sizeof(sw_next));
	memset(hw_next, 0, sizeof(hw_next));

	list_for_each_entry(f, &dpaa_ceetm_dscp_filters, list) {
		u32 minor = 0, hw_fqid = 0;

		if (!dpaa_ceetm_dscp_resolve_leaf(net_dev, f->classid,
						  &minor, &hw_fqid)) {
			sw_next[f->dscp] = minor;
			hw_next[f->dscp] = hw_fqid;
		}
	}

	fm = dpaa_ceetm_get_fman(net_dev);
	for (d = 0; d < DPAA_CEETM_DSCP_COUNT; d++) {
		WRITE_ONCE(dpaa_ceetm_dscp_sw_minor[d], sw_next[d]);
		if (fm)
			fman_pcd_dscp_map_set(fm, (u8)d, hw_next[d]);
	}
}

static int dpaa_ceetm_dscp_finish_retiring(void)
{
	struct net_device *retiring = dpaa_ceetm_dscp_owner;
	struct fman *fm;
	int quiesce_rc;

	ASSERT_RTNL();
	if (dpaa_ceetm_dscp_poisoned)
		return -EBUSY;
	if (!retiring || dpaa_ceetm_dscp_state != DPAA_DSCP_RETIRING)
		return 0;

	quiesce_rc = dpaa_fo_notify_egress_change(retiring,
						  DPAA_EGRESS_CEETM_PRE_CHANGE,
						  0);
	if (quiesce_rc) {
		dpaa_fo_notify_egress_change(retiring,
					     DPAA_EGRESS_CEETM_POST_CHANGE, 0);
		return -EBUSY;
	}

	fm = dpaa_ceetm_get_fman(retiring);
	if (fm)
		fman_pcd_dscp_map_clear_all(fm);
	smp_store_release(&dpaa_ceetm_dscp_state, DPAA_DSCP_OFF);
	smp_store_release(&dpaa_ceetm_dscp_owner, NULL);
	dpaa_fo_notify_egress_change(retiring,
				     DPAA_EGRESS_CEETM_POST_CHANGE, 0);
	return 0;
}

static void dpaa_ceetm_dscp_teardown_pre(struct net_device *net_dev)
{
	ASSERT_RTNL();
	if (dpaa_ceetm_dscp_owner != net_dev)
		return;
	smp_store_release(&dpaa_ceetm_dscp_state, DPAA_DSCP_RETIRING);
}

static void dpaa_ceetm_dscp_teardown_post(struct net_device *net_dev,
					  bool quiesce_ok)
{
	struct dpaa_ceetm_dscp_filter *f, *tmp;
	struct fman *fm;
	unsigned int d;

	ASSERT_RTNL();
	if (dpaa_ceetm_dscp_owner != net_dev)
		return;

	list_for_each_entry_safe(f, tmp, &dpaa_ceetm_dscp_filters, list) {
		list_del_rcu(&f->list);
		kfree_rcu(f, rcu);
	}
	for (d = 0; d < DPAA_CEETM_DSCP_COUNT; d++)
		WRITE_ONCE(dpaa_ceetm_dscp_sw_minor[d], 0);

	if (quiesce_ok) {
		fm = dpaa_ceetm_get_fman(net_dev);
		if (fm)
			fman_pcd_dscp_map_clear_all(fm);
		smp_store_release(&dpaa_ceetm_dscp_state, DPAA_DSCP_OFF);
		smp_store_release(&dpaa_ceetm_dscp_owner, NULL);
	} else {
		/* Retained HW records may still read MURAM dscp_fqid_map;
		 * block any future port from overwriting it, and drop the raw
		 * net_device pointer so it can never dangle after free_netdev. */
		dpaa_ceetm_dscp_poisoned = true;
		smp_store_release(&dpaa_ceetm_dscp_state, DPAA_DSCP_OFF);
		smp_store_release(&dpaa_ceetm_dscp_owner, NULL);
		netdev_err(net_dev,
			   "CEETM: DSCP map retained after failed teardown quiesce\n");
	}
}

static void dpaa_ceetm_dscp_flush_port(struct net_device *net_dev)
{
	struct dpaa_ceetm_dscp_filter *f, *tmp;
	unsigned int d;

	ASSERT_RTNL();
	if (dpaa_ceetm_dscp_owner != net_dev)
		return;

	list_for_each_entry_safe(f, tmp, &dpaa_ceetm_dscp_filters, list) {
		list_del_rcu(&f->list);
		kfree_rcu(f, rcu);
	}
	for (d = 0; d < DPAA_CEETM_DSCP_COUNT; d++)
		WRITE_ONCE(dpaa_ceetm_dscp_sw_minor[d], 0);

	if (dpaa_ceetm_dscp_state == DPAA_DSCP_ON ||
	    dpaa_ceetm_dscp_state == DPAA_DSCP_RETIRING) {
		smp_store_release(&dpaa_ceetm_dscp_state, DPAA_DSCP_RETIRING);
		if (dpaa_ceetm_dscp_finish_retiring() &&
		    net_dev->reg_state >= NETREG_UNREGISTERING) {
			/* Netdev is going away before flows quiesced: poison
			 * the MURAM singleton and drop the raw netdev pointer. */
			dpaa_ceetm_dscp_poisoned = true;
			smp_store_release(&dpaa_ceetm_dscp_state, DPAA_DSCP_OFF);
			smp_store_release(&dpaa_ceetm_dscp_owner, NULL);
		}
	} else if (dpaa_ceetm_dscp_state == DPAA_DSCP_CLAIMED) {
		struct fman *fm = dpaa_ceetm_get_fman(net_dev);

		if (fm)
			fman_pcd_dscp_map_clear_all(fm);
		smp_store_release(&dpaa_ceetm_dscp_state, DPAA_DSCP_OFF);
		smp_store_release(&dpaa_ceetm_dscp_owner, NULL);
	}
}

void dpaa_ceetm_dscp_netdev_unregister(struct net_device *net_dev)
{
	ASSERT_RTNL();
	if (!net_dev || dpaa_ceetm_dscp_owner != net_dev)
		return;
	dpaa_ceetm_dscp_flush_port(net_dev);
	if (dpaa_ceetm_dscp_owner == net_dev) {
		dpaa_ceetm_dscp_poisoned = true;
		smp_store_release(&dpaa_ceetm_dscp_state, DPAA_DSCP_OFF);
		smp_store_release(&dpaa_ceetm_dscp_owner, NULL);
	}
}

static int dpaa_ceetm_dscp_parse(struct flow_cls_offload *f, u8 *dscp,
				 __be16 *n_proto, u32 *classid)
{
	struct netlink_ext_ack *extack = f->common.extack;
	struct flow_rule *rule = flow_cls_offload_flow_rule(f);
	const struct flow_action_entry *act;
	struct flow_match_control control;
	struct flow_match_basic basic;
	struct flow_match_ip ip;
	const unsigned long long allowed =
		BIT_ULL(FLOW_DISSECTOR_KEY_CONTROL) |
		BIT_ULL(FLOW_DISSECTOR_KEY_BASIC) |
		BIT_ULL(FLOW_DISSECTOR_KEY_IP);

	if (rule->match.dissector->used_keys & ~allowed) {
		NL_SET_ERR_MSG_MOD(extack,
				   "CEETM egress flower supports only ip_dscp");
		return -EOPNOTSUPP;
	}
	if (flow_rule_match_key(rule, FLOW_DISSECTOR_KEY_CONTROL)) {
		flow_rule_match_control(rule, &control);
		if (control.mask->flags || control.mask->addr_type ||
		    control.mask->thoff) {
			NL_SET_ERR_MSG_MOD(extack,
					   "CEETM egress flower supports only ip_dscp");
			return -EOPNOTSUPP;
		}
	}
	*n_proto = 0;
	if (flow_rule_match_key(rule, FLOW_DISSECTOR_KEY_BASIC)) {
		flow_rule_match_basic(rule, &basic);
		if (basic.mask->ip_proto) {
			NL_SET_ERR_MSG_MOD(extack,
					   "CEETM egress flower supports only ip_dscp");
			return -EOPNOTSUPP;
		}
		*n_proto = basic.key->n_proto & basic.mask->n_proto;
	}
	if (!flow_rule_match_key(rule, FLOW_DISSECTOR_KEY_IP)) {
		NL_SET_ERR_MSG_MOD(extack,
				   "CEETM egress flower requires an ip_dscp match");
		return -EOPNOTSUPP;
	}
	flow_rule_match_ip(rule, &ip);
	if (ip.mask->ttl) {
		NL_SET_ERR_MSG_MOD(extack,
				   "CEETM egress flower does not support ttl match");
		return -EOPNOTSUPP;
	}
	if (ip.mask->tos != DPAA_CEETM_DSCP_MASK) {
		NL_SET_ERR_MSG_MOD(extack,
				   "CEETM egress flower must match full 6-bit DSCP without ECN bits");
		return -EOPNOTSUPP;
	}
	if (!flow_offload_has_one_action(&rule->action)) {
		NL_SET_ERR_MSG_MOD(extack,
				   "CEETM egress flower supports exactly one action");
		return -EOPNOTSUPP;
	}
	act = &rule->action.entries[0];
	if (act->id != FLOW_ACTION_PRIORITY || !act->priority) {
		NL_SET_ERR_MSG_MOD(extack,
				   "CEETM egress flower supports only skbedit priority <classid>");
		return -EOPNOTSUPP;
	}

	*dscp = (ip.key->tos & DPAA_CEETM_DSCP_MASK) >> DPAA_CEETM_DSCP_SHIFT;
	*classid = act->priority;
	return 0;
}

static int dpaa_ceetm_dscp_replace(struct net_device *net_dev,
				   struct flow_cls_offload *f)
{
	struct netlink_ext_ack *extack = f->common.extack;
	struct dpaa_priv *priv = netdev_priv(net_dev);
	struct dpaa_ceetm_dscp_filter *flt, *existing;
	struct fman *fm;
	__be16 n_proto;
	u32 classid, minor = 0, hw_fqid = 0;
	u8 dscp;
	bool was_on, was_retiring;
	int err;

	ASSERT_RTNL();
	if (!f->common.skip_sw) {
		NL_SET_ERR_MSG_MOD(extack,
				   "CEETM egress DSCP offload requires skip_sw to preserve mark precedence");
		return -EOPNOTSUPP;
	}
	if (!priv->ceetm) {
		NL_SET_ERR_MSG_MOD(extack,
				   "CEETM HTB offload is not installed on this interface");
		return -EOPNOTSUPP;
	}
	fm = dpaa_ceetm_get_fman(net_dev);
	if (!fm)
		return -EOPNOTSUPP;

	err = dpaa_ceetm_dscp_parse(f, &dscp, &n_proto, &classid);
	if (err)
		return err;

	err = dpaa_ceetm_dscp_resolve_leaf(net_dev, classid, &minor, &hw_fqid);
	if (err) {
		NL_SET_ERR_MSG_MOD(extack,
				   "target HTB offload class not found on this interface");
		return err;
	}

	if (dpaa_ceetm_dscp_poisoned) {
		NL_SET_ERR_MSG_MOD(extack,
				   "hardware DSCP map is quarantined after prior quiesce failure");
		return -EBUSY;
	}
	if (dpaa_ceetm_dscp_owner && dpaa_ceetm_dscp_owner != net_dev) {
		if (dpaa_ceetm_dscp_finish_retiring()) {
			NL_SET_ERR_MSG_MOD(extack,
					   "hardware DSCP map serves one port at a time; another port holds it");
			return -EBUSY;
		}
		if (dpaa_ceetm_dscp_owner && dpaa_ceetm_dscp_owner != net_dev) {
			NL_SET_ERR_MSG_MOD(extack,
					   "hardware DSCP map serves one port at a time; another port holds it");
			return -EBUSY;
		}
	}

	flt = kzalloc(sizeof(*flt), GFP_KERNEL);
	if (!flt)
		return -ENOMEM;
	flt->cookie = f->cookie;
	flt->classid = classid;
	flt->prio = f->common.prio;
	flt->chain = f->common.chain_index;
	flt->n_proto = n_proto;
	flt->dscp = dscp;

	list_for_each_entry(existing, &dpaa_ceetm_dscp_filters, list) {
		if (existing->dscp == dscp && existing->cookie != f->cookie &&
		    !dpaa_ceetm_dscp_replaces(flt, existing) &&
		    !dpaa_ceetm_dscp_dual_stack_pair(flt, existing)) {
			kfree(flt);
			NL_SET_ERR_MSG_MOD(extack,
					   "another egress filter already claims that DSCP");
			return -EEXIST;
		}
	}

	was_on = (dpaa_ceetm_dscp_owner == net_dev &&
		  dpaa_ceetm_dscp_state == DPAA_DSCP_ON);
	was_retiring = (dpaa_ceetm_dscp_owner == net_dev &&
			dpaa_ceetm_dscp_state == DPAA_DSCP_RETIRING);
	if (!was_on) {
		if (!dpaa_ceetm_dscp_owner) {
			fman_pcd_dscp_map_clear_all(fm);
			smp_store_release(&dpaa_ceetm_dscp_owner, net_dev);
		}
		smp_store_release(&dpaa_ceetm_dscp_state, DPAA_DSCP_CLAIMED);
	}

	existing = dpaa_ceetm_dscp_find_cookie(f->cookie);
	if (existing) {
		existing->classid = classid;
		existing->dscp = dscp;
		kfree(flt);
		flt = existing;
	} else {
		list_add_tail_rcu(&flt->list, &dpaa_ceetm_dscp_filters);
	}
	dpaa_ceetm_dscp_resync(net_dev);

	if (!was_on) {
		int qrc;

		/* Retire pre-existing unmarked HW flows on this port so they
		 * re-admit with DSCP_FQ_MAP_ENABLE set in ENQUEUE word2. */
		qrc = dpaa_fo_notify_egress_change(net_dev,
						   DPAA_EGRESS_CEETM_PRE_CHANGE,
						   0);
		if (qrc) {
			if (!existing) {
				list_del_rcu(&flt->list);
				kfree_rcu(flt, rcu);
			}
			WRITE_ONCE(dpaa_ceetm_dscp_sw_minor[dscp], 0);
			if (was_retiring) {
				/* Preserve RETIRING ownership so no other port
				 * can overwrite MURAM while unquiesced flows
				 * on @net_dev may still read it. */
				smp_store_release(&dpaa_ceetm_dscp_state,
						  DPAA_DSCP_RETIRING);
			} else {
				fman_pcd_dscp_map_clear_all(fm);
				smp_store_release(&dpaa_ceetm_dscp_state,
						  DPAA_DSCP_OFF);
				smp_store_release(&dpaa_ceetm_dscp_owner, NULL);
			}
			dpaa_fo_notify_egress_change(net_dev,
						     DPAA_EGRESS_CEETM_POST_CHANGE,
						     0);
			NL_SET_ERR_MSG_MOD(extack,
					   "failed to quiesce ASK flows before enabling DSCP map");
			return -EBUSY;
		}
		smp_store_release(&dpaa_ceetm_dscp_state, DPAA_DSCP_ON);
		dpaa_fo_notify_egress_change(net_dev,
					     DPAA_EGRESS_CEETM_POST_CHANGE, 0);
	}
	return 0;
}

static int dpaa_ceetm_dscp_destroy(struct net_device *net_dev,
				   struct flow_cls_offload *f)
{
	struct dpaa_ceetm_dscp_filter *flt;

	ASSERT_RTNL();
	if (dpaa_ceetm_dscp_owner != net_dev)
		return -ENOENT;
	flt = dpaa_ceetm_dscp_find_cookie(f->cookie);
	if (!flt)
		return -ENOENT;

	list_del_rcu(&flt->list);
	dpaa_ceetm_dscp_resync(net_dev);
	kfree_rcu(flt, rcu);

	if (list_empty(&dpaa_ceetm_dscp_filters) &&
	    dpaa_ceetm_dscp_state == DPAA_DSCP_ON) {
		smp_store_release(&dpaa_ceetm_dscp_state, DPAA_DSCP_RETIRING);
		dpaa_ceetm_dscp_finish_retiring();
	}
	return 0;
}

static int dpaa_ceetm_dscp_block_cb(enum tc_setup_type type, void *type_data,
				    void *cb_priv)
{
	struct net_device *net_dev = cb_priv;
	struct flow_cls_offload *f = type_data;

	if (!tc_cls_can_offload_and_chain0(net_dev, type_data))
		return -EOPNOTSUPP;
	if (type != TC_SETUP_CLSFLOWER)
		return -EOPNOTSUPP;

	switch (f->command) {
	case FLOW_CLS_REPLACE:
		return dpaa_ceetm_dscp_replace(net_dev, f);
	case FLOW_CLS_DESTROY:
		return dpaa_ceetm_dscp_destroy(net_dev, f);
	default:
		return -EOPNOTSUPP;
	}
}

static void dpaa_ceetm_dscp_block_release(void *cb_priv)
{
	struct net_device *net_dev = cb_priv;

	dpaa_ceetm_dscp_flush_port(net_dev);
}

int dpaa_ceetm_setup_dscp_block(struct net_device *net_dev,
				struct flow_block_offload *f)
{
	struct flow_block_cb *block_cb;

	ASSERT_RTNL();
	if (f->binder_type != FLOW_BLOCK_BINDER_TYPE_CLSACT_EGRESS)
		return -EOPNOTSUPP;

	f->driver_block_list = &dpaa_ceetm_dscp_block_cb_list;

	switch (f->command) {
	case FLOW_BLOCK_BIND:
		if (flow_block_cb_is_busy(dpaa_ceetm_dscp_block_cb, net_dev,
					  &dpaa_ceetm_dscp_block_cb_list))
			return -EBUSY;
		block_cb = flow_block_cb_alloc(dpaa_ceetm_dscp_block_cb,
					       net_dev, net_dev,
					       dpaa_ceetm_dscp_block_release);
		if (IS_ERR(block_cb))
			return PTR_ERR(block_cb);
		flow_block_cb_add(block_cb, f);
		list_add_tail(&block_cb->driver_list,
			      &dpaa_ceetm_dscp_block_cb_list);
		return 0;
	case FLOW_BLOCK_UNBIND:
		block_cb = flow_block_cb_lookup(f->block,
						dpaa_ceetm_dscp_block_cb,
						net_dev);
		if (!block_cb)
			return -ENOENT;
		flow_block_cb_remove(block_cb, f);
		list_del(&block_cb->driver_list);
		return 0;
	default:
		return -EOPNOTSUPP;
	}
}

/* Lockless SW TX lookup (called from dpaa_ceetm_select_queue): extract IPv4 or
 * IPv6 DSCP (including VLAN-tagged frames) without mutating @skb. */
static u32 dpaa_ceetm_dscp_sw_minor_lookup(struct net_device *net_dev,
					   const struct sk_buff *skb)
{
	__be16 proto;
	int nh_off;
	u8 dscp;

	if (smp_load_acquire(&dpaa_ceetm_dscp_state) != DPAA_DSCP_ON ||
	    smp_load_acquire(&dpaa_ceetm_dscp_owner) != net_dev)
		return 0;
	nh_off = skb_network_offset(skb);
	if (nh_off < (int)ETH_HLEN)
		return 0;
	proto = vlan_get_protocol(skb);
	if (proto == htons(ETH_P_IP)) {
		if (skb_headlen(skb) < (unsigned int)nh_off + sizeof(struct iphdr))
			return 0;
		dscp = ipv4_get_dsfield(ip_hdr(skb)) >> DPAA_CEETM_DSCP_SHIFT;
	} else if (proto == htons(ETH_P_IPV6)) {
		if (skb_headlen(skb) < (unsigned int)nh_off + sizeof(struct ipv6hdr))
			return 0;
		dscp = ipv6_get_dsfield(ipv6_hdr(skb)) >> DPAA_CEETM_DSCP_SHIFT;
	} else {
		return 0;
	}
	if (dscp >= DPAA_CEETM_DSCP_COUNT)
		return 0;
	return READ_ONCE(dpaa_ceetm_dscp_sw_minor[dscp]);
}

bool dpaa_ceetm_dscp_map_active(struct net_device *dev)
{
	if (!dev)
		return false;
	return smp_load_acquire(&dpaa_ceetm_dscp_state) == DPAA_DSCP_ON &&
	       smp_load_acquire(&dpaa_ceetm_dscp_owner) == dev;
}
EXPORT_SYMBOL_GPL(dpaa_ceetm_dscp_map_active);

bool dpaa_ceetm_dscp_cookie_offloaded(struct net_device *dev,
				      unsigned long cookie)
{
	struct dpaa_ceetm_dscp_filter *f;
	bool found = false;

	if (!dev)
		return false;
	rcu_read_lock();
	if (smp_load_acquire(&dpaa_ceetm_dscp_state) == DPAA_DSCP_ON &&
	    smp_load_acquire(&dpaa_ceetm_dscp_owner) == dev) {
		list_for_each_entry_rcu(f, &dpaa_ceetm_dscp_filters, list) {
			if (f->cookie == cookie) {
				found = true;
				break;
			}
		}
	}
	rcu_read_unlock();
	return found;
}
EXPORT_SYMBOL_GPL(dpaa_ceetm_dscp_cookie_offloaded);

/* Lockless check: true iff @classid matches a live HTB offload leaf on @dev.
 * Used by ASK2 so unrelated non-HTB ct->mark values still consult DSCP map,
 * matching dpaa_ceetm_select_queue() SW precedence. */
bool dpaa_ceetm_classid_is_live(struct net_device *dev, u32 classid)
{
	struct dpaa_priv *priv;
	struct dpaa_ceetm *ceetm;
	u32 minor;

	if (!dev || !classid || !dpaa_get_rx_fman_port(dev))
		return false;
	priv = netdev_priv(dev);
	ceetm = READ_ONCE(priv->ceetm);
	if (!ceetm)
		return false;
	minor = dpaa_ceetm_decode_classid(ceetm, classid);
	if (!minor)
		return false;
	return dpaa_ceetm_find_live_slot(ceetm, minor) >= 0;
}
EXPORT_SYMBOL_GPL(dpaa_ceetm_classid_is_live);
