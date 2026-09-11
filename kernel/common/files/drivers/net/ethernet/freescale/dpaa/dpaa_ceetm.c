// SPDX-License-Identifier: (GPL-2.0-only OR BSD-3-Clause)
/*
 * DPAA1 Ethernet -- QMan CEETM HTB-offload egress shaper consumer.
 *
 * This file implements the kernel TC_SETUP_QDISC_HTB offload contract on top
 * of the QMan CEETM (Customer Edge Egress Traffic Management) core API
 * exported by <soc/fsl/qman.h> (drivers/soc/fsl/qbman/qman_ceetm.c). It is the
 * modern equivalent of the legacy NXP SDK "ceetm" qdisc, but driven entirely
 * by the stock iproute2 HTB-offload commands ("tc qdisc add dev ethX root
 * handle 1: htb offload", "tc class add ... htb rate ... ceil ...").
 *
 * HTB-to-CEETM mapping (flat root->leaf only)
 * -------------------------------------------
 *   TC_HTB_CREATE          -> claim a CEETM sub-portal (SP) + logical network
 *                             interface (LNI), map SP->LNI (this enables CEETM
 *                             mode on the port and STOPS the conventional WQ
 *                             egress FQs), then build one unshaped "default"
 *                             channel for unclassified traffic.
 *   TC_HTB_LEAF_ALLOC_QUEUE-> one CEETM channel per HTB leaf class. The channel
 *                             dual-rate shaper is programmed with commit-rate =
 *                             class rate and excess-rate = ceil - rate. A single
 *                             strict-priority class queue (CQ index 0) with a
 *                             byte-mode tail-drop congestion group (CCG) sits on
 *                             the channel; an LFQ + qman_fq is bound for enqueue.
 *                             The leaf is exposed as netdev Tx queue
 *                             base + slot, returned to the HTB core in qid.
 *   TC_HTB_NODE_MODIFY      -> reprogram an existing channel's CR/ER rates.
 *   TC_HTB_LEAF_DEL/_LAST   -> tear the leaf channel down and stop steering to
 *                             its Tx queue.
 *   TC_HTB_LEAF_QUERY_QUEUE -> map a classid back to its leaf Tx queue index.
 *   TC_HTB_DESTROY          -> release the whole hierarchy; releasing the SP
 *                             disables CEETM mode so the conventional FQs run
 *                             again.
 *   TC_HTB_LEAF_TO_INNER    -> rejected (-EOPNOTSUPP): only a flat root->leaf
 *                             structure is supported.
 *
 * Full Tx steering
 * ----------------
 * Once qman_ceetm_sp_set_lni() runs, the FMan no longer dequeues this port's
 * conventional WQ egress FQs, so EVERY enqueue must go through CEETM until
 * teardown. dpaa_xmit() therefore diverts on priv->ceetm: leaf Tx queues hit
 * their own LFQ, and every other Tx queue (the conventional ones, plus the
 * XDP Tx path which enqueues on smp_processor_id()) is routed to the default
 * channel's LFQ. This is the only path that prevents silently black-holed
 * traffic on hardware.
 *
 * Congestion-group tail-drop rejections are delivered to dpaa_ceetm_ern() as
 * software ERNs and counted as Tx drops, mirroring egress_ern().
 *
 * Locking: all TC_SETUP_QDISC_HTB callbacks arrive under rtnl, so the install/
 * destroy/modify paths are mutually serialised. The xmit fast path reads
 * priv->ceetm with smp_load_acquire(); install publishes it with
 * smp_store_release(), then builds the default channel post-publish, and
 * teardown clears it then synchronize_net()s before releasing hardware.
 */

#include <linux/kernel.h>
#include <linux/module.h>
#include <linux/netdevice.h>
#include <linux/rtnetlink.h>
#include <linux/slab.h>
#include <net/pkt_cls.h>
#include <soc/fsl/qman.h>

#include "fman_port.h"
#include "mac.h"
#include "dpaa_eth.h"
#include "dpaa_ceetm.h"

/* Sub-portal id is the low 5 bits of the FMan Tx port's QMan channel. */
#define DPAA_CEETM_SP_MASK		0x1f
/* QMan channels below this boundary belong to FMan DCP0 (LS1046A only). */
#define DPAA_CEETM_DCP0_MAX_CHANNEL	0x80f
/* Number of CEETM LNIs the QMan core exposes; claimed by explicit index. */
#define DPAA_CEETM_NR_LNIS		8
/*
 * Silicon-proven LFQ dequeue-context-A value (from the NXP SDK CEETM
 * consumer): selects the FMan egress descriptor format used for CEETM LFQs.
 */
#define DPAA_CEETM_CONTEXT_A		0x1a00000080000000ULL
/* Default per-CQ byte-mode tail-drop threshold (~1 MiB). */
#define DPAA_CEETM_TD_THRESHOLD		(1U << 20)
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
 * @ccg: the channel's class congestion group (tail-drop)
 * @cq: the channel's strict-priority class queue (level 0)
 * @lfq: the logical frame queue bound to @cq
 * @fq: the enqueue/ERN handle created on @lfq
 * @net_dev: owning netdev, recovered by the ERN callback via container_of
 * @classid: TC_H_MIN(classid) of the HTB leaf (0 for the default channel)
 * @shaped: true if the channel dual-rate shaper is enabled
 * @used: published last; gates the xmit fast-path lookup of @fq
 */
struct dpaa_ceetm_class {
	struct qm_ceetm_channel *channel;
	struct qm_ceetm_ccg *ccg;
	struct qm_ceetm_cq *cq;
	struct qm_ceetm_lfq *lfq;
	struct qman_fq fq;
	struct net_device *net_dev;
	u32 classid;
	bool shaped;
	bool used;
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

	skb = dpaa_cleanup_tx_fd(priv, fd, false);
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

/* Find the leaf slot owning @classid (TC_H_MIN form), or -1. */
static int dpaa_ceetm_find_slot(struct dpaa_ceetm *ceetm, u32 classid)
{
	int i;

	for (i = 0; i < DPAA_CEETM_MAX_TXQS; i++)
		if (ceetm->leaf[i].channel && ceetm->leaf[i].classid == classid)
			return i;
	return -1;
}

/*
 * ndo_select_queue: steer an HTB-offloaded class to its leaf Tx queue.
 *
 * The tc HTB-offload core does not remap skbs onto leaf Tx queues itself, so
 * the driver derives the class from skb->priority here, mirroring
 * mlx5e_select_queue()/mlx5e_select_htb_queue(). A frame whose major matches
 * the installed HTB handle steers to TC_H_MIN(priority); otherwise the qdisc
 * default class applies. A frame resolving to a live leaf returns that leaf's
 * Tx queue (base_txq + slot); anything else falls back to a conventional Tx
 * queue (< base_txq), which dpaa_ceetm_egress_fq() routes to the default
 * channel. skb->priority is set by "action skbedit priority M:N" or
 * SO_PRIORITY; a bare flower "classid" does NOT set it and will not steer.
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

/* Release a fully or partially built channel; safe to call on a zeroed class. */
static void dpaa_ceetm_destroy_class(struct dpaa_ceetm_class *cls)
{
	if (!cls->channel)
		return;

	if (cls->lfq) {
		qman_ceetm_destroy_fq(&cls->fq);
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
}

/*
 * Build one CEETM channel: claim it under the LNI, optionally enable and
 * program the dual-rate shaper, then attach a byte-mode tail-drop CCG, a
 * strict-priority class queue, an LFQ and the enqueue/ERN frame queue. On any
 * failure everything claimed so far is released and @cls is left zeroed.
 */
static int dpaa_ceetm_build_channel(struct net_device *net_dev,
				    struct dpaa_ceetm *ceetm,
				    struct dpaa_ceetm_class *cls,
				    bool shaped, u64 rate_bps, u64 ceil_bps)
{
	struct qm_ceetm_ccg_params ccgp;
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
		/*
		 * A true CEETM "unshaped" channel (CS=1 at claim, shaper left
		 * at the infinite rate) never dequeues on this silicon: frames
		 * enqueue but starve in the CQ - a hard egress blackhole,
		 * hardware-proven 2026-06-13 (both the uncoupled CR=ER=infinite
		 * and the SDK uFQ recipes were tried and blackhole). The default
		 * channel is built as a shaped channel at line rate instead (see
		 * dpaa_ceetm_init), so shaped is always true here; reject an
		 * unshaped request rather than build a silent blackhole.
		 */
		netdev_err(net_dev,
			   "CEETM: unshaped channels unsupported on this silicon\n");
		err = -EINVAL;
		goto err_release_channel;
	}

	err = qman_ceetm_ccg_claim(&cls->ccg, cls->channel,
				   DPAA_CEETM_CQ_PRIO);
	if (err)
		goto err_disable_shaper;

	memset(&ccgp, 0, sizeof(ccgp));
	ccgp.mode = 0;		/* count bytes */
	ccgp.td_en = 1;		/* enable tail-drop */
	ccgp.td_mode = 1;	/* threshold-based tail-drop */
	ccgp.oal = 0;
	qm_cgr_cs_thres_set64(&ccgp.td_thres, DPAA_CEETM_TD_THRESHOLD, 1);
	err = qman_ceetm_ccg_set(cls->ccg,
				 QM_CCGR_WE_MODE | QM_CCGR_WE_TD_EN |
				 QM_CCGR_WE_TD_MODE | QM_CCGR_WE_TD_THRES |
				 QM_CCGR_WE_OAL, &ccgp);
	if (err)
		goto err_release_ccg;

	err = qman_ceetm_cq_claim(&cls->cq, cls->channel, DPAA_CEETM_CQ_PRIO,
				  cls->ccg);
	if (err)
		goto err_release_ccg;

	/*
	 * Every channel here is shaped: leaves at their rate/ceil, the default
	 * channel at line rate. Each CQ rides both the CR list (guaranteed
	 * rate, scheduled against the channel CR token bucket and the LNI CR
	 * pass) and the ER list (excess up to ceil - rate). The coupled shaper
	 * spills unused CR credit into ER, so a CR+ER eligible CQ on a shaped
	 * channel always dequeues - the hardware-proven path.
	 */
	err = qman_ceetm_channel_set_cq_cr_eligibility(cls->channel,
						       DPAA_CEETM_CQ_PRIO, 1);
	if (err)
		goto err_release_cq;
	err = qman_ceetm_channel_set_cq_er_eligibility(cls->channel,
						       DPAA_CEETM_CQ_PRIO, 1);
	if (err)
		goto err_release_cq;

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

	return 0;

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
	int i;

	ASSERT_RTNL();

	ceetm = priv->ceetm;
	if (!ceetm)
		return;

	/* Stop diverting new frames, then wait for in-flight xmit to finish. */
	smp_store_release(&priv->ceetm, NULL);
	synchronize_net();

	for (i = 0; i < DPAA_CEETM_MAX_TXQS; i++)
		dpaa_ceetm_destroy_class(&ceetm->leaf[i]);
	dpaa_ceetm_destroy_class(&ceetm->def);

	if (ceetm->lni) {
		qman_ceetm_lni_release(ceetm->lni);
		if (ceetm->sp)
			ceetm->sp->lni = NULL;
	}
	if (ceetm->sp)
		qman_ceetm_sp_release(ceetm->sp);

	prev_real = ceetm->prev_real_txqs;
	if (!prev_real)
		prev_real = priv->num_tc * dpaa_num_txqs_per_tc();
	netif_set_real_num_tx_queues(net_dev, prev_real);

	kfree(ceetm);
	netdev_info(net_dev, "CEETM HTB offload removed\n");
}

static int dpaa_ceetm_htb_create(struct net_device *net_dev,
				 struct tc_htb_qopt_offload *opt)
{
	struct dpaa_priv *priv = netdev_priv(net_dev);
	struct dpaa_ceetm *ceetm;
	enum qm_dc_portal dcp;
	unsigned int sp_id;
	u32 channel;
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

	err = qman_ceetm_sp_claim(&ceetm->sp, dcp, sp_id);
	if (err) {
		NL_SET_ERR_MSG_MOD(opt->extack, "CEETM sub-portal claim failed");
		goto err_free;
	}

	/*
	 * Give each Tx sub-portal its OWN dedicated LNI (lni_idx == sp_id)
	 * instead of sharing the first-free LNI. On LS1046A every FMan Tx
	 * port's QMan channel low-5-bits (sp_id) is unique and < 8
	 * (10GEC1/2 -> sp 0/1, MAC1..6 -> sp 2..7), so the index is always
	 * in range and never collides. Two distinct sub-portals sharing one
	 * LNI corrupted the second port's default channel: sp_release()
	 * disables CEETM mode but the shared LNI keeps the first port's stale
	 * binding, so the second port (different sub-portal, same LNI)
	 * blackholed all unclassified traffic. A per-port LNI removes the
	 * collision and matches the NXP SDK 1:1 sub-portal<->LNI model; a
	 * same-port rebuild already worked because it re-asserts the identical
	 * sp<->lni pair every time.
	 */
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

	/*
	 * The LNI shaper must always be enabled: a never-enabled LNI shaper
	 * register is all-zeros, which paces the aggregate at zero rate and
	 * starves every channel beneath it. CR is set to the infinite rate
	 * (HTB offload carries no root rate; classes shape per-channel) and
	 * ER stays zero, mirroring the silicon-proven Mono CDX bring-up
	 * (coupled shaper, OAL 24).
	 */
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

	/*
	 * Publish BEFORE building the default channel. The default channel is
	 * built post-publish - the same way every leaf class is built - so it
	 * is created while the port is already live in CEETM mode. The special
	 * pre-publish default channel black-holed unclassified traffic on this
	 * silicon at every channel index and LNI ordinal (hardware-proven
	 * 2026-06-13/14: skip-index-0/1 and a sacrificial warm-up channel all
	 * left the pre-publish default at frm_cnt 0), while every post-publish
	 * leaf forwarded at 0% loss. Until def.used is set, dpaa_ceetm_egress_fq()
	 * returns NULL for unclassified traffic and dpaa_xmit() falls back to the
	 * (now-undequeued) conventional FQ - a sub-millisecond install window.
	 */
	smp_store_release(&priv->ceetm, ceetm);

	/*
	 * Default channel: catches unclassified and WQ-queue frames. Built as
	 * a shaped channel at line rate (DPAA_CEETM_DEFAULT_RATE_BPS), not a
	 * CEETM "unshaped" channel - the latter never dequeues on this silicon
	 * (egress blackhole, hardware-proven 2026-06-13). 10 Gbps is >= every
	 * port's line rate, so it imposes no real limit.
	 */
	err = dpaa_ceetm_build_channel(net_dev, ceetm, &ceetm->def, true,
				       DPAA_CEETM_DEFAULT_RATE_BPS,
				       DPAA_CEETM_DEFAULT_RATE_BPS);
	if (err) {
		NL_SET_ERR_MSG_MOD(opt->extack,
				   "CEETM default channel build failed");
		goto err_unpublish;
	}
	smp_store_release(&ceetm->def.used, true);

	netdev_info(net_dev, "CEETM HTB offload installed (sp %u, lni %u)\n",
		    ceetm->sp->idx, ceetm->lni->idx);
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

	/* Forensic snapshot before teardown (see qman_ceetm_dump_state). */
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
	u64 rate_bps, ceil_bps;
	int slot, err;

	if (!ceetm) {
		NL_SET_ERR_MSG_MOD(opt->extack, "HTB offload not initialised");
		return -EINVAL;
	}

	/* Flat hierarchy only: every leaf hangs directly off the root. */
	if (opt->parent_classid != TC_HTB_CLASSID_ROOT) {
		NL_SET_ERR_MSG_MOD(opt->extack,
				   "DPAA CEETM offload supports a flat root->leaf class structure only");
		return -EOPNOTSUPP;
	}

	for (slot = 0; slot < DPAA_CEETM_MAX_TXQS; slot++)
		if (!ceetm->leaf[slot].channel)
			break;
	if (slot == DPAA_CEETM_MAX_TXQS) {
		NL_SET_ERR_MSG_MOD(opt->extack, "no free CEETM class queue");
		return -ENOSPC;
	}

	/* tc passes rate/ceil in bytes/s; the CEETM shaper API wants bits/s. */
	rate_bps = opt->rate * 8;
	ceil_bps = opt->ceil * 8;

	err = dpaa_ceetm_build_channel(net_dev, ceetm, &ceetm->leaf[slot],
				       true, rate_bps, ceil_bps);
	if (err) {
		NL_SET_ERR_MSG_MOD(opt->extack, "CEETM leaf channel build failed");
		return err;
	}
	ceetm->leaf[slot].classid = TC_H_MIN(opt->classid);

	/* Grow the real Tx queue count so the leaf queue becomes selectable. */
	err = netif_set_real_num_tx_queues(net_dev,
			max_t(unsigned int, net_dev->real_num_tx_queues,
			      ceetm->base_txq + slot + 1));
	if (err) {
		NL_SET_ERR_MSG_MOD(opt->extack, "failed to grow Tx queues");
		dpaa_ceetm_destroy_class(&ceetm->leaf[slot]);
		return err;
	}

	/* Publish the slot last so the xmit fast path sees a complete class. */
	smp_store_release(&ceetm->leaf[slot].used, true);
	opt->qid = ceetm->base_txq + slot;
	return 0;
}

static int dpaa_ceetm_htb_leaf_del(struct net_device *net_dev,
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

	/*
	 * Stop steering to this leaf first; the slot then falls back to the
	 * default channel in dpaa_ceetm_egress_fq(). synchronize_net() waits
	 * for in-flight xmit before the FQ is destroyed.
	 */
	smp_store_release(&ceetm->leaf[slot].used, false);
	synchronize_net();
	dpaa_ceetm_destroy_class(&ceetm->leaf[slot]);
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
	/*
	 * WQ queues and unclassified traffic egress via the default channel.
	 * NULL until the default channel is built post-publish; the caller then
	 * falls back to the conventional FQ for that sub-millisecond window.
	 */
	if (smp_load_acquire(&ceetm->def.used))
		return &ceetm->def.fq;
	return NULL;
}
