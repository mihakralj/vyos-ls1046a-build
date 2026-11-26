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
#include <net/flow_offload.h>
#include <net/pkt_cls.h>
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
 * @cq_mask: bitmask of strict-priority CQ levels (0..7) currently claimed
 * @active_cqs: number of active leaves attached to this channel
 * @shaped: true if the channel dual-rate shaper is enabled
 * @is_inner: true once TC_HTB_LEAF_TO_INNER promoted this channel to inner
 * @quarantined: true if any CQ on this channel failed teardown/drain
 */
struct dpaa_ceetm_chan {
	struct qm_ceetm_channel *channel;
	u32 classid;
	u8 cq_mask;
	u8 active_cqs;
	bool shaped;
	bool is_inner;
	bool quarantined;
};

/**
 * struct dpaa_ceetm_class - one CEETM class queue (CQ 0..7) and its two LFQs
 * @chan: owning CEETM channel (1-level own channel or 2-level parent channel)
 * @cq_prio: strict-priority CQ index (0..7) on @chan
 * @ccg: frame-mode class congestion group (tail-drop) at @cq_prio
 * @cq: strict-priority class queue at @cq_prio
 * @lfq: logical frame queue for software TX (dpaa_xmit, confirmed)
 * @hw_lfq: logical frame queue for ASK2 HW FE ENQUEUE_PKT (no-confirm, EBD=1)
 * @fq: enqueue/ERN handle created on @lfq
 * @net_dev: owning netdev, recovered by the ERN callback via container_of
 * @classid: TC_H_MIN(classid) of this HTB leaf (0 for the default channel)
 * @used: published last; gates both dpaa_xmit and ASK2 offload lookups
 * @quarantined: true if teardown quiesce or CQ drain failed (retain-on-fault)
 */
struct dpaa_ceetm_class {
	struct dpaa_ceetm_chan *chan;
	u8 cq_prio;
	struct qm_ceetm_ccg *ccg;
	struct qm_ceetm_cq *cq;
	struct qm_ceetm_lfq *lfq;
	struct qm_ceetm_lfq *hw_lfq;
	struct qman_fq fq;
	struct net_device *net_dev;
	u32 classid;
	bool used;
	bool quarantined;
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
	return qman_ceetm_ccg_set(ccg,
				  QM_CCGR_WE_MODE | QM_CCGR_WE_TD_EN |
				  QM_CCGR_WE_TD_MODE | QM_CCGR_WE_TD_THRES |
				  QM_CCGR_WE_OAL, &ccgp);
}

/* Rebalance frame-mode CCGR limits across all active (non-quarantined) CQs. */
static void dpaa_ceetm_rebalance_ccgs(struct dpaa_priv *priv,
				      struct dpaa_ceetm *ceetm,
				      unsigned int mtu)
{
	unsigned int n = dpaa_ceetm_active_cqs(ceetm);
	u32 budget, share;
	int i;

	if (!n)
		return;
	budget = dpaa_ceetm_port_frame_budget(priv, mtu);
	/* Clamp at 1 (not MIN_CQ_FRAMES) so an MTU increase never pushes the
	 * aggregate per-port depth above the half-RX-pool / XSFDR budget. */
	share = max_t(u32, budget / n, 1U);

	if (ceetm->def.ccg && !ceetm->def.quarantined)
		dpaa_ceetm_set_ccg_frames(ceetm->def.ccg, share);
	for (i = 0; i < DPAA_CEETM_MAX_TXQS; i++)
		if (ceetm->leaf[i].ccg && !ceetm->leaf[i].quarantined)
			dpaa_ceetm_set_ccg_frames(ceetm->leaf[i].ccg, share);
}

void dpaa_ceetm_update_mtu(struct dpaa_priv *priv, unsigned int mtu)
{
	struct dpaa_ceetm *ceetm;

	ASSERT_RTNL();
	ceetm = priv->ceetm;
	if (!ceetm)
		return;
	dpaa_ceetm_rebalance_ccgs(priv, ceetm, mtu);
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

/*
 * ndo_select_queue: steer an HTB-offloaded class to its leaf Tx queue.
 * Matches skb->priority first (skbedit / nft meta priority), then skb->mark
 * (symmetric with ASK2 HW ct->mark steering), then ceetm->defcls.
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
	if (slot < 0 && ceetm->defcls)
		slot = dpaa_ceetm_find_live_slot(ceetm, ceetm->defcls);

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
	if (cls->chan)
		cls->chan->quarantined = true;
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
	ch->active_cqs = 0;
	ch->is_inner = false;
	ch->quarantined = false;
	return 0;

err_disable_shaper:
	qman_ceetm_channel_disable_shaper(ch->channel);
	ch->shaped = false;
err_release_channel:
	qman_ceetm_channel_release(ch->channel);
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
	qman_ceetm_channel_release(ch->channel);
	ch->channel = NULL;
	ch->classid = 0;
	ch->cq_mask = 0;
	ch->is_inner = false;
}

/*
 * Build one strict-priority CQ (0..7), frame-mode CCG, and TWO LFQs
 * (lfq for SW dpaa_xmit and hw_lfq for ASK2 FE ENQUEUE_PKT) on @ch.
 */
static int dpaa_ceetm_cq_build(struct net_device *net_dev,
			       struct dpaa_ceetm_chan *ch,
			       struct dpaa_ceetm_class *cls,
			       u8 cq_prio, u32 ccg_frames)
{
	int err;

	if (cq_prio >= DPAA_CEETM_MAX_CQ_PRIO)
		return -EINVAL;
	if (ch->cq_mask & BIT(cq_prio))
		return -EEXIST;

	err = qman_ceetm_ccg_claim(&cls->ccg, ch->channel, cq_prio);
	if (err)
		return err;

	err = dpaa_ceetm_set_ccg_frames(cls->ccg, ccg_frames);
	if (err)
		goto err_release_ccg;

	err = qman_ceetm_cq_claim(&cls->cq, ch->channel, cq_prio, cls->ccg);
	if (err)
		goto err_release_ccg;

	err = qman_ceetm_channel_set_cq_cr_eligibility(ch->channel, cq_prio, 1);
	if (err)
		goto err_release_cq;
	err = qman_ceetm_channel_set_cq_er_eligibility(ch->channel, cq_prio, 1);
	if (err)
		goto err_disable_elig;

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

	cls->chan = ch;
	cls->cq_prio = cq_prio;
	ch->cq_mask |= BIT(cq_prio);
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
	qman_ceetm_channel_set_cq_cr_eligibility(ch->channel, cq_prio, 0);
	qman_ceetm_channel_set_cq_er_eligibility(ch->channel, cq_prio, 0);
err_release_cq:
	qman_ceetm_cq_release(cls->cq);
	cls->cq = NULL;
err_release_ccg:
	qman_ceetm_ccg_release(cls->ccg);
	cls->ccg = NULL;
	return err;
}

/* Release a CQ/LFQ pair (and optionally its owning channel if now empty).
 * Returns true if quarantined. */
static bool dpaa_ceetm_destroy_class(struct dpaa_ceetm_class *cls,
				     bool force_quarantine,
				     bool release_empty_chan)
{
	struct dpaa_ceetm_chan *ch = cls->chan;
	int drain_rc;

	if (!cls->cq && !ch)
		return false;
	if (cls->quarantined)
		return true;
	if (force_quarantine) {
		dpaa_ceetm_quarantine_class(cls, "offload quiesce incomplete");
		return true;
	}

	/* Stop DCP0 dequeues on this CQ before popping remaining frames so
	 * CQ_PEEK_POP_XFDRREAD does not contend with active prefetch/dequeue. */
	if (cls->cq && ch && ch->channel) {
		qman_ceetm_channel_set_cq_cr_eligibility(ch->channel,
							 cls->cq_prio, 0);
		qman_ceetm_channel_set_cq_er_eligibility(ch->channel,
							 cls->cq_prio, 0);
	}

	/* Drain and free every frame still queued on the CQ BEFORE releasing
	 * LFQs/CQ/channel. If drain fails, quarantine rather than leak/reuse. */
	drain_rc = dpaa_ceetm_drain_and_release_cq(cls);
	if (drain_rc) {
		dpaa_ceetm_quarantine_class(cls, "CQ drain failed");
		return true;
	}

	if (cls->hw_lfq) {
		struct qm_ceetm_lfq *hw_lfq = cls->hw_lfq;

		WRITE_ONCE(cls->hw_lfq, NULL);
		qman_ceetm_lfq_release(hw_lfq);
	}
	if (cls->lfq) {
		/* Drain pending portal EQCRs and MR/SW-ERNs across all CPUs
		 * before clearing fq_table[fq->idx], then wait an RCU grace
		 * period so any in-flight ERN handler completes before cls or
		 * ceetm can be reused or freed. */
		qman_ceetm_drain_ern();
		qman_ceetm_destroy_fq(&cls->fq);
		synchronize_rcu();
		qman_ceetm_lfq_release(cls->lfq);
		cls->lfq = NULL;
	}
	if (cls->cq) {
		qman_ceetm_cq_release(cls->cq);
		cls->cq = NULL;
	}
	if (cls->ccg) {
		qman_ceetm_ccg_release(cls->ccg);
		cls->ccg = NULL;
	}
	if (ch) {
		ch->cq_mask &= ~BIT(cls->cq_prio);
		if (ch->active_cqs)
			ch->active_cqs--;
		if (release_empty_chan && ch->active_cqs == 0 && !ch->quarantined)
			dpaa_ceetm_chan_destroy(ch);
	}
	cls->chan = NULL;
	cls->cq_prio = 0;
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

	for (i = 0; i < DPAA_CEETM_MAX_TXQS; i++)
		any_quarantined |= dpaa_ceetm_destroy_class(&ceetm->leaf[i],
							    quiesce_rc != 0,
							    true);
	any_quarantined |= dpaa_ceetm_destroy_class(&ceetm->def,
						    quiesce_rc != 0, true);
	for (i = 0; i < DPAA_CEETM_MAX_CHANS; i++) {
		if (ceetm->chans[i].quarantined)
			any_quarantined = true;
		else
			dpaa_ceetm_chan_destroy(&ceetm->chans[i]);
	}

	if (any_quarantined) {
		if (ceetm->sp)
			qman_sp_disable_ceetm_mode(ceetm->sp->dcp_idx,
						   ceetm->sp->idx);
		netdev_err(net_dev,
			   "CEETM teardown had quarantined class(es); LNI/SP retained\n");
	} else {
		if (ceetm->lni) {
			qman_ceetm_lni_release(ceetm->lni);
			if (ceetm->sp)
				ceetm->sp->lni = NULL;
		}
		if (ceetm->sp)
			qman_ceetm_sp_release(ceetm->sp);
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

static int dpaa_ceetm_htb_create(struct net_device *net_dev,
				 struct tc_htb_qopt_offload *opt)
{
	struct dpaa_priv *priv = netdev_priv(net_dev);
	struct dpaa_ceetm *ceetm;
	enum qm_dc_portal dcp;
	unsigned int sp_id;
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

	err = qman_ceetm_sp_claim(&ceetm->sp, dcp, sp_id);
	if (err) {
		NL_SET_ERR_MSG_MOD(opt->extack, "CEETM sub-portal claim failed");
		goto err_post_abort;
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
				  DPAA_CEETM_CQ_PRIO, budget);
	if (err) {
		dpaa_ceetm_chan_destroy(&ceetm->def_chan);
		NL_SET_ERR_MSG_MOD(opt->extack,
				   "CEETM default CQ build failed");
		goto err_unpublish;
	}
	smp_store_release(&ceetm->def.used, true);

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
err_post_abort:
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
	bool own_chan = false;
	u64 rate_bps, ceil_bps;
	u32 budget, share, hw_fqid, minor;
	u8 cq_prio;
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
					   "DPAA CEETM supports at most 2 levels (root -> channel -> prio CQ 0..7)");
			return -EOPNOTSUPP;
		}
		if (ch->quarantined) {
			NL_SET_ERR_MSG_MOD(opt->extack,
					   "CEETM parent channel is quarantined");
			return -EBUSY;
		}
		cq_prio = min_t(u8, opt->prio, DPAA_CEETM_MAX_CQ_PRIO - 1);
		if (ch->cq_mask & BIT(cq_prio)) {
			NL_SET_ERR_MSG_MOD(opt->extack,
					   "CEETM strict-priority level already in use on parent channel; set distinct prio 0..7");
			return -EEXIST;
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
				  cq_prio, share);
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
		dpaa_ceetm_destroy_class(&ceetm->leaf[slot], false, own_chan);
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
	u8 old_prio, new_prio;
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
				   "DPAA CEETM supports at most 2 levels (root -> channel -> prio CQ 0..7)");
		return -EOPNOTSUPP;
	}

	old_prio = cls->cq_prio;
	new_prio = min_t(u8, opt->prio, DPAA_CEETM_MAX_CQ_PRIO - 1);
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

	if (new_prio != old_prio) {
		budget = dpaa_ceetm_port_frame_budget(priv,
						      READ_ONCE(net_dev->mtu));
		share = max_t(u32, budget / max_t(unsigned int,
						  dpaa_ceetm_active_cqs(ceetm),
						  1U), 1U);
		if (dpaa_ceetm_destroy_class(cls, false, false)) {
			dpaa_ceetm_dscp_resync(net_dev);
			dpaa_fo_notify_egress_change(net_dev,
						     DPAA_EGRESS_CEETM_POST_CHANGE,
						     0);
			NL_SET_ERR_MSG_MOD(opt->extack,
					   "CEETM CQ drain failed during promotion; channel quarantined");
			return -EBUSY;
		}
		err = dpaa_ceetm_cq_build(net_dev, ch, cls, new_prio, share);
		if (err) {
			/* Attempt to restore parent at old_prio before failing. */
			if (!dpaa_ceetm_cq_build(net_dev, ch, cls, old_prio,
						 share)) {
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

	ch->is_inner = true;
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
	    ch->active_cqs == 1) {
		/* sch_htb demotes the inner parent channel back into a leaf on
		 * the SAME Tx queue slot when its last child is deleted. */
		if (cls->cq_prio != DPAA_CEETM_CQ_PRIO) {
			u8 old_prio = cls->cq_prio;
			u32 old_classid = cls->classid;
			u32 budget = dpaa_ceetm_port_frame_budget(priv,
							READ_ONCE(net_dev->mtu));
			u32 share = max_t(u32, budget / max_t(unsigned int,
						dpaa_ceetm_active_cqs(ceetm),
						1U), 1U);

			if (dpaa_ceetm_destroy_class(cls, false, false)) {
				dpaa_ceetm_dscp_resync(net_dev);
				dpaa_fo_notify_egress_change(net_dev,
							     DPAA_EGRESS_CEETM_POST_CHANGE,
							     0);
				NL_SET_ERR_MSG_MOD(opt->extack,
						   "CEETM CQ drain failed during demotion; channel quarantined");
				return -EBUSY;
			}
			err = dpaa_ceetm_cq_build(net_dev, ch, cls,
						  DPAA_CEETM_CQ_PRIO, share);
			if (err) {
				if (!dpaa_ceetm_cq_build(net_dev, ch, cls,
							 old_prio, share)) {
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
		ch->is_inner = false;
		WRITE_ONCE(cls->classid, ch->classid);
		smp_store_release(&cls->used, true);
		dpaa_ceetm_rebalance_ccgs(priv, ceetm, READ_ONCE(net_dev->mtu));
		dpaa_ceetm_dscp_resync(net_dev);
		dpaa_fo_notify_egress_change(net_dev,
					     DPAA_EGRESS_CEETM_POST_CHANGE, 0);
		return 0;
	}

	dpaa_ceetm_destroy_class(cls, quiesce_rc != 0, true);
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
		/* Child CQs share their parent inner channel's shaper and
		 * cannot change CQ strict-priority index in place. */
		u8 req_prio = min_t(u8, opt->prio, DPAA_CEETM_MAX_CQ_PRIO - 1);

		if (req_prio != ceetm->leaf[slot].cq_prio) {
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
int dpaa_ceetm_offload_egress_fqid(struct net_device *dev, u32 classid,
				   u32 *fqid)
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
	if (slot < 0 && ceetm->defcls)
		slot = dpaa_ceetm_find_live_slot(ceetm, ceetm->defcls);
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
EXPORT_SYMBOL_GPL(dpaa_ceetm_offload_egress_fqid);

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
