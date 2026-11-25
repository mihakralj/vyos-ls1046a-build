// SPDX-License-Identifier: (GPL-2.0-only OR BSD-3-Clause)
/*
 * DPAA1 Ethernet -- QMan CEETM HTB-offload egress shaper consumer.
 *
 * This file implements the kernel TC_SETUP_QDISC_HTB offload contract on top
 * of the QMan CEETM (Customer Edge Egress Traffic Management) core API
 * exported by <soc/fsl/qman.h> (drivers/soc/fsl/qbman/qman_ceetm.c). It is the
 * modern equivalent of the legacy NXP SDK "ceetm" qdisc, but driven entirely
 * by the stock iproute2 HTB-offload commands ("tc qdisc add dev ethX root
 * handle 1: htb offload default 10", "tc class add ... htb rate ... ceil ...").
 *
 * G10 (ASK2 + SW shared CEETM shaping):
 *   - Each CEETM CQ binds TWO LFQIDs via QMan's N:1 LFQMT/DCT mapping:
 *       * @lfq (context_a 0x1a00000080000000, ern=dpaa_ceetm_ern) for
 *         kernel software TX (dpaa_xmit, fd->bpid=0xff, confirmed).
 *       * @hw_lfq (context_a 0x9a000000c0000000, EBD=1, no TX-confirm)
 *         for ASK2 FMan FE ENQUEUE_PKT hardware forwarding.
 *     Both feed the exact same hardware CQ, frame-mode CCGR, channel shaper,
 *     and LNI, eliminating bypass, double shaping, and F-227 confirm/ERN
 *     confusion.
 *   - Frame-mode CCGR tail-drop bounded below half the port's RX BMan pool
 *     and below DPAA_CEETM_MAX_PORT_FRAMES (512), rebalanced on leaf add/del
 *     and MTU changes (closing byte-mode RX pool starvation, bug A337).
 *   - Synchronous ASK2 PRE/POST egress notifications under RTNL + SRCU
 *     (dpaa_fo_notify_egress_change). Create aborts -EBUSY if ASK2 cannot
 *     quiesce existing WQ6 flows; leaf delete / teardown quarantines any
 *     channel whose ASK2 retirement or CQ drain failed (CCGR parked at 1
 *     frame, channel/LFQID kept claimed so no other port can inherit it).
 *   - CQ drain pops and explicitly releases leftover FDs (skb cleanup for
 *     bpid==0xff, dpaa_fd_release for real BMan pool IDs).
 */

#include <linux/kernel.h>
#include <linux/module.h>
#include <linux/netdevice.h>
#include <linux/rtnetlink.h>
#include <linux/slab.h>
#include <linux/delay.h>
#include <linux/jiffies.h>
#include <net/pkt_cls.h>
#include <soc/fsl/qman.h>

#include "fman_port.h"

void dpaa_wgfp_egress_deactivate(struct net_device *dev);
#include "mac.h"
#include "dpaa_eth.h"
#include "dpaa_fman_caps.h"
#include "dpaa_ceetm.h"

/* Sub-portal id is the low 5 bits of the FMan Tx port's QMan channel. */
#define DPAA_CEETM_SP_MASK		0x1f
/* QMan channels below this boundary belong to FMan DCP0 (LS1046A only). */
#define DPAA_CEETM_DCP0_MAX_CHANNEL	0x80f
/* Number of CEETM LNIs the QMan core exposes; claimed by explicit index. */
#define DPAA_CEETM_NR_LNIS		8
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
/* Single strict-priority class-queue level used per channel. */
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
 * struct dpaa_ceetm_class - one CEETM channel and its single class queue
 * @channel: the claimed CEETM class-queue channel
 * @ccg: the channel's frame-mode class congestion group (tail-drop)
 * @cq: the channel's strict-priority class queue (level 0)
 * @lfq: logical frame queue for software TX (dpaa_xmit, confirmed)
 * @hw_lfq: logical frame queue for ASK2 HW FE ENQUEUE_PKT (no-confirm, EBD=1)
 * @fq: the enqueue/ERN handle created on @lfq
 * @net_dev: owning netdev, recovered by the ERN callback via container_of
 * @classid: TC_H_MIN(classid) of the HTB leaf (0 for the default channel)
 * @shaped: true if the channel dual-rate shaper is enabled
 * @used: published last; gates both dpaa_xmit and ASK2 offload lookups
 * @quarantined: true if teardown quiesce or CQ drain failed; channel/LFQs
 *	stay claimed and CCGR is parked at 1 frame (retain-on-fault)
 */
struct dpaa_ceetm_class {
	struct qm_ceetm_channel *channel;
	struct qm_ceetm_ccg *ccg;
	struct qm_ceetm_cq *cq;
	struct qm_ceetm_lfq *lfq;
	struct qm_ceetm_lfq *hw_lfq;
	struct qman_fq fq;
	struct net_device *net_dev;
	u32 classid;
	bool shaped;
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
 * @def: the default channel for unclassified / WQ-queue traffic
 * @leaf: per-leaf-class channels, indexed by (Tx queue - base_txq)
 */
struct dpaa_ceetm {
	struct qm_ceetm_sp *sp;
	struct qm_ceetm_lni *lni;
	unsigned int base_txq;
	unsigned int prev_real_txqs;
	u32 htb_maj;
	u32 defcls;
	struct dpaa_ceetm_class def;
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

	if (ceetm->def.channel && !ceetm->def.quarantined)
		n++;
	for (i = 0; i < DPAA_CEETM_MAX_TXQS; i++)
		if (ceetm->leaf[i].channel && !ceetm->leaf[i].quarantined)
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

/* Find the leaf slot owning @classid (TC_H_MIN form), or -1. */
static int dpaa_ceetm_find_slot(struct dpaa_ceetm *ceetm, u32 classid)
{
	int i;

	for (i = 0; i < DPAA_CEETM_MAX_TXQS; i++)
		if (ceetm->leaf[i].channel && !ceetm->leaf[i].quarantined &&
		    ceetm->leaf[i].classid == classid)
			return i;
	return -1;
}

/*
 * ndo_select_queue: steer an HTB-offloaded class to its leaf Tx queue.
 */
u16 dpaa_ceetm_select_queue(struct net_device *net_dev, struct sk_buff *skb,
			    struct net_device *sb_dev)
{
	struct dpaa_priv *priv = netdev_priv(net_dev);
	struct dpaa_ceetm *ceetm = smp_load_acquire(&priv->ceetm);
	u32 classid;
	u16 txq;
	int slot;

	if (likely(!ceetm))
		return netdev_pick_tx(net_dev, skb, NULL);

	if ((TC_H_MAJ(skb->priority) >> 16) == ceetm->htb_maj)
		classid = TC_H_MIN(skb->priority);
	else
		classid = ceetm->defcls;

	if (classid) {
		slot = dpaa_ceetm_find_slot(ceetm, classid);
		if (slot >= 0 && smp_load_acquire(&ceetm->leaf[slot].used))
			return ceetm->base_txq + slot;
	}

	txq = netdev_pick_tx(net_dev, skb, NULL);
	if (txq >= ceetm->base_txq)
		txq %= ceetm->base_txq;
	return txq;
}

/*
 * Pop every descriptor remaining in @cls->cq and explicitly release its
 * buffer (skb cleanup for bpid==0xff, dpaa_fd_release for real BMan pools).
 * Returns 0 once empty or negative errno on timeout/MC error (caller must
 * quarantine the channel).
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

/* Quarantine a channel/CQ whose ASK2 retirement or CQ drain failed: park
 * its CCGR at 1 frame and keep channel/LFQIDs claimed so no other port can
 * inherit them while FMan might still reference the LFQID. */
static void dpaa_ceetm_quarantine_class(struct dpaa_ceetm_class *cls,
					const char *reason)
{
	cls->used = false;
	cls->quarantined = true;
	if (cls->ccg)
		dpaa_ceetm_set_ccg_frames(cls->ccg, DPAA_CEETM_PARKED_FRAMES);
	if (cls->net_dev)
		netdev_err(cls->net_dev,
			   "CEETM: quarantining class 0x%x ch %u (%s); retaining channel/LFQIDs\n",
			   cls->classid,
			   cls->channel ? cls->channel->idx : 0, reason);
}

/* Release a fully or partially built channel; returns true if quarantined. */
static bool dpaa_ceetm_destroy_class(struct dpaa_ceetm_class *cls,
				     bool force_quarantine)
{
	int drain_rc;

	if (!cls->channel)
		return false;
	if (cls->quarantined)
		return true;
	if (force_quarantine) {
		dpaa_ceetm_quarantine_class(cls, "offload quiesce incomplete");
		return true;
	}

	/* Stop DCP0 dequeues on this CQ before popping remaining frames so
	 * CQ_PEEK_POP_XFDRREAD does not contend with active prefetch/dequeue. */
	if (cls->cq) {
		qman_ceetm_channel_set_cq_cr_eligibility(cls->channel,
							 DPAA_CEETM_CQ_PRIO, 0);
		qman_ceetm_channel_set_cq_er_eligibility(cls->channel,
							 DPAA_CEETM_CQ_PRIO, 0);
	}

	/* Drain and free every frame still queued on the CQ BEFORE releasing
	 * LFQs/CQ/channel. If drain fails, quarantine rather than leak/reuse. */
	drain_rc = dpaa_ceetm_drain_and_release_cq(cls);
	if (drain_rc) {
		dpaa_ceetm_quarantine_class(cls, "CQ drain failed");
		return true;
	}

	if (cls->hw_lfq) {
		qman_ceetm_lfq_release(cls->hw_lfq);
		cls->hw_lfq = NULL;
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
	/* The channel shaper is enabled on every built channel. */
	qman_ceetm_channel_disable_shaper(cls->channel);
	cls->shaped = false;
	qman_ceetm_channel_release(cls->channel);
	cls->channel = NULL;
	cls->used = false;
	cls->classid = 0;
	return false;
}

/*
 * Build one CEETM channel: claim it under the LNI, enable and program the
 * dual-rate shaper, attach a frame-mode tail-drop CCG, a strict-priority
 * class queue, and TWO LFQs bound to that same CQ (lfq for SW dpaa_xmit and
 * hw_lfq for ASK2 FE ENQUEUE_PKT). On failure, releases everything claimed.
 */
static int dpaa_ceetm_build_channel(struct net_device *net_dev,
				    struct dpaa_ceetm *ceetm,
				    struct dpaa_ceetm_class *cls,
				    bool shaped, u64 rate_bps, u64 ceil_bps,
				    u32 ccg_frames)
{
	u16 burst = net_dev->mtu;
	int err;

	err = qman_ceetm_channel_claim(&cls->channel, ceetm->lni);
	if (err) {
		netdev_err(net_dev, "CEETM channel claim failed: %d\n", err);
		return err;
	}

	if (shaped) {
		err = qman_ceetm_channel_enable_shaper(cls->channel, 1);
		if (err)
			goto err_release_channel;
		cls->shaped = true;

		err = qman_ceetm_channel_set_commit_rate_bps(cls->channel,
							     rate_bps, burst);
		if (err)
			goto err_disable_shaper;
		err = qman_ceetm_channel_set_excess_rate_bps(cls->channel,
					ceil_bps > rate_bps ? ceil_bps - rate_bps : 0, burst);
		if (err)
			goto err_disable_shaper;
	} else {
		netdev_err(net_dev,
			   "CEETM: unshaped channels unsupported on this silicon\n");
		err = -EINVAL;
		goto err_release_channel;
	}

	err = qman_ceetm_ccg_claim(&cls->ccg, cls->channel,
				   DPAA_CEETM_CQ_PRIO);
	if (err)
		goto err_disable_shaper;

	err = dpaa_ceetm_set_ccg_frames(cls->ccg, ccg_frames);
	if (err)
		goto err_release_ccg;

	err = qman_ceetm_cq_claim(&cls->cq, cls->channel, DPAA_CEETM_CQ_PRIO,
				  cls->ccg);
	if (err)
		goto err_release_ccg;

	err = qman_ceetm_channel_set_cq_cr_eligibility(cls->channel,
						       DPAA_CEETM_CQ_PRIO, 1);
	if (err)
		goto err_release_cq;
	err = qman_ceetm_channel_set_cq_er_eligibility(cls->channel,
						       DPAA_CEETM_CQ_PRIO, 1);
	if (err)
		goto err_release_cq;

	/* LFQ #1: software TX (dpaa_xmit, confirmed skb/XDP path). */
	err = qman_ceetm_lfq_claim(&cls->lfq, cls->cq);
	if (err)
		goto err_release_cq;

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

	return 0;

err_release_hw_lfq:
	qman_ceetm_lfq_release(cls->hw_lfq);
	cls->hw_lfq = NULL;
err_destroy_fq:
	qman_ceetm_destroy_fq(&cls->fq);
err_release_lfq:
	qman_ceetm_lfq_release(cls->lfq);
	cls->lfq = NULL;
err_release_cq:
	qman_ceetm_cq_release(cls->cq);
	cls->cq = NULL;
err_release_ccg:
	qman_ceetm_ccg_release(cls->ccg);
	cls->ccg = NULL;
err_disable_shaper:
	qman_ceetm_channel_disable_shaper(cls->channel);
	cls->shaped = false;
err_release_channel:
	qman_ceetm_channel_release(cls->channel);
	cls->channel = NULL;
	return err;
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

	/* Fence ASK2 admission and withdraw every HW flow targeting this
	 * port's CEETM LFQIDs BEFORE stopping CEETM mode on the sub-portal. */
	quiesce_rc = dpaa_fo_notify_egress_change(net_dev,
						  DPAA_EGRESS_CEETM_PRE_CHANGE,
						  0);

	/* Stop diverting new SW frames, then wait for in-flight xmit to finish. */
	smp_store_release(&priv->ceetm, NULL);
	synchronize_net();

	for (i = 0; i < DPAA_CEETM_MAX_TXQS; i++)
		any_quarantined |= dpaa_ceetm_destroy_class(&ceetm->leaf[i],
							    quiesce_rc != 0);
	any_quarantined |= dpaa_ceetm_destroy_class(&ceetm->def,
						    quiesce_rc != 0);

	if (any_quarantined) {
		/* Return the sub-portal to conventional WQ mode so software
		 * dpaa_xmit() keeps working, while retaining SP/LNI/channel
		 * bitmaps and struct dpaa_ceetm so quarantined channels and
		 * LFQIDs can never be reused by another port. */
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
	err = dpaa_ceetm_build_channel(net_dev, ceetm, &ceetm->def, true,
				       DPAA_CEETM_DEFAULT_RATE_BPS,
				       DPAA_CEETM_DEFAULT_RATE_BPS,
				       budget);
	if (err) {
		NL_SET_ERR_MSG_MOD(opt->extack,
				   "CEETM default channel build failed");
		goto err_unpublish;
	}
	smp_store_release(&ceetm->def.used, true);

	dpaa_fo_notify_egress_change(net_dev, DPAA_EGRESS_CEETM_POST_CHANGE, 0);

	netdev_info(net_dev,
		    "CEETM HTB offload installed (sp %u, lni %u, frame-budget %u)\n",
		    ceetm->sp->idx, ceetm->lni->idx, budget);
	qman_ceetm_dump_state("install", ceetm->sp, ceetm->lni,
			      ceetm->def.channel, ceetm->def.cq, ceetm->def.ccg);
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
				      ceetm->def.channel, ceetm->def.cq,
				      ceetm->def.ccg);
		for (i = 0; i < DPAA_CEETM_MAX_TXQS; i++)
			if (ceetm->leaf[i].used)
				qman_ceetm_dump_state("leaf",
						      NULL, NULL,
						      ceetm->leaf[i].channel,
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
	unsigned int next_active;
	u64 rate_bps, ceil_bps;
	u32 budget, share, hw_fqid;
	int slot, err;

	if (!ceetm) {
		NL_SET_ERR_MSG_MOD(opt->extack, "HTB offload not initialised");
		return -EINVAL;
	}

	if (opt->parent_classid != TC_HTB_CLASSID_ROOT) {
		NL_SET_ERR_MSG_MOD(opt->extack,
				   "DPAA CEETM offload supports a flat root->leaf class structure only");
		return -EOPNOTSUPP;
	}

	for (slot = 0; slot < DPAA_CEETM_MAX_TXQS; slot++)
		if (!ceetm->leaf[slot].channel && !ceetm->leaf[slot].quarantined)
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

	/* tc passes rate/ceil in bytes/s; the CEETM shaper API wants bits/s. */
	rate_bps = opt->rate * 8;
	ceil_bps = opt->ceil * 8;

	err = dpaa_ceetm_build_channel(net_dev, ceetm, &ceetm->leaf[slot],
				       true, rate_bps, ceil_bps, share);
	if (err) {
		NL_SET_ERR_MSG_MOD(opt->extack, "CEETM leaf channel build failed");
		return err;
	}
	ceetm->leaf[slot].classid = TC_H_MIN(opt->classid);

	err = netif_set_real_num_tx_queues(net_dev,
			max_t(unsigned int, net_dev->real_num_tx_queues,
			      ceetm->base_txq + slot + 1));
	if (err) {
		NL_SET_ERR_MSG_MOD(opt->extack, "failed to grow Tx queues");
		dpaa_ceetm_destroy_class(&ceetm->leaf[slot], false);
		return err;
	}

	/* Shrink sibling CQs to the new share before publishing this leaf. */
	dpaa_ceetm_rebalance_ccgs(priv, ceetm, READ_ONCE(net_dev->mtu));

	/* Publish the slot last so xmit and ASK2 see a complete class. */
	smp_store_release(&ceetm->leaf[slot].used, true);
	opt->qid = ceetm->base_txq + slot;

	hw_fqid = ceetm->leaf[slot].hw_lfq ?
		  (ceetm->leaf[slot].hw_lfq->idx & 0x00FFFFFFU) : 0;
	if (ceetm->leaf[slot].classid == ceetm->defcls && hw_fqid)
		dpaa_fo_notify_egress_change(net_dev,
					     DPAA_EGRESS_CEETM_POST_CHANGE,
					     hw_fqid);
	return 0;
}

static int dpaa_ceetm_htb_leaf_del(struct net_device *net_dev,
				   struct tc_htb_qopt_offload *opt)
{
	struct dpaa_priv *priv = netdev_priv(net_dev);
	struct dpaa_ceetm *ceetm = priv->ceetm;
	u32 hw_fqid;
	int slot, quiesce_rc;

	if (!ceetm)
		return -EINVAL;

	slot = dpaa_ceetm_find_slot(ceetm, TC_H_MIN(opt->classid));
	if (slot < 0)
		return -ENOENT;

	hw_fqid = ceetm->leaf[slot].hw_lfq ?
		  (ceetm->leaf[slot].hw_lfq->idx & 0x00FFFFFFU) : 0;

	/* Fence and retire every ASK2 flow targeting this leaf's hw_lfq BEFORE
	 * tearing down its CQ/channel. */
	quiesce_rc = dpaa_fo_notify_egress_change(net_dev,
						  DPAA_EGRESS_CEETM_PRE_CHANGE,
						  hw_fqid);

	smp_store_release(&ceetm->leaf[slot].used, false);
	synchronize_net();
	dpaa_ceetm_destroy_class(&ceetm->leaf[slot], quiesce_rc != 0);
	dpaa_ceetm_rebalance_ccgs(priv, ceetm, READ_ONCE(net_dev->mtu));

	dpaa_fo_notify_egress_change(net_dev, DPAA_EGRESS_CEETM_POST_CHANGE, 0);
	return 0;
}

static int dpaa_ceetm_htb_node_modify(struct net_device *net_dev,
				      struct tc_htb_qopt_offload *opt)
{
	struct dpaa_priv *priv = netdev_priv(net_dev);
	struct dpaa_ceetm *ceetm = priv->ceetm;
	struct dpaa_ceetm_class *cls;
	u64 rate_bps, ceil_bps;
	u16 burst = net_dev->mtu;
	int slot, err;

	if (!ceetm)
		return -EINVAL;

	slot = dpaa_ceetm_find_slot(ceetm, TC_H_MIN(opt->classid));
	if (slot < 0)
		return -ENOENT;
	cls = &ceetm->leaf[slot];
	if (!cls->shaped) {
		NL_SET_ERR_MSG_MOD(opt->extack, "class is not shaped");
		return -EINVAL;
	}

	rate_bps = opt->rate * 8;
	ceil_bps = opt->ceil * 8;
	err = qman_ceetm_channel_set_commit_rate_bps(cls->channel, rate_bps,
						     burst);
	if (err)
		goto err;
	err = qman_ceetm_channel_set_excess_rate_bps(cls->channel,
						     ceil_bps > rate_bps ? ceil_bps - rate_bps : 0,
						     burst);
	if (err)
		goto err;
	return 0;

err:
	NL_SET_ERR_MSG_MOD(opt->extack, "failed to update CEETM shaper rate");
	return err;
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
	case TC_HTB_LEAF_DEL:
	case TC_HTB_LEAF_DEL_LAST:
	case TC_HTB_LEAF_DEL_LAST_FORCE:
		return dpaa_ceetm_htb_leaf_del(net_dev, opt);
	case TC_HTB_NODE_MODIFY:
		return dpaa_ceetm_htb_node_modify(net_dev, opt);
	case TC_HTB_LEAF_QUERY_QUEUE:
		return dpaa_ceetm_htb_query(net_dev, opt);
	case TC_HTB_LEAF_TO_INNER:
		NL_SET_ERR_MSG_MOD(opt->extack,
				   "DPAA CEETM offload supports a flat root->leaf class structure only");
		return -EOPNOTSUPP;
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
 * dpaa_ceetm_offload_egress_fqid - resolve ASK2's no-confirm CEETM hw_lfq FQID
 * @dev: physical DPAA Ethernet netdev
 * @classid: HTB minor classid, or 0 for the qdisc's default leaf class
 * @fqid: receives the 24-bit CEETM LFQID (0xF00xxx) when CEETM is active,
 *	or 0 when CEETM is inactive
 *
 * Return:
 *   0 if CEETM is inactive on @dev (caller uses conventional WQ6 TX FQs),
 *   1 if CEETM is active and @fqid was populated with the target leaf's hw_lfq,
 *   -EAGAIN if CEETM is active but no live leaf backs @classid / default class
 *     (fail closed to software; never target DEFECT-B raw default or WQ6).
 */
int dpaa_ceetm_offload_egress_fqid(struct net_device *dev, u32 classid,
				   u32 *fqid)
{
	struct dpaa_priv *priv;
	struct dpaa_ceetm *ceetm;
	u32 want;
	int i, ret = -EAGAIN;

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

	want = classid ? TC_H_MIN(classid) : ceetm->defcls;
	if (!want)
		goto out_unlock;

	for (i = 0; i < DPAA_CEETM_MAX_TXQS; i++) {
		struct qm_ceetm_lfq *hw_lfq;

		if (!smp_load_acquire(&ceetm->leaf[i].used))
			continue;
		if (ceetm->leaf[i].quarantined ||
		    ceetm->leaf[i].classid != want)
			continue;
		hw_lfq = READ_ONCE(ceetm->leaf[i].hw_lfq);
		if (!hw_lfq)
			goto out_unlock;
		*fqid = hw_lfq->idx & 0x00FFFFFFU;
		ret = 1;
		goto out_unlock;
	}

out_unlock:
	rcu_read_unlock();
	return ret;
}
EXPORT_SYMBOL_GPL(dpaa_ceetm_offload_egress_fqid);
