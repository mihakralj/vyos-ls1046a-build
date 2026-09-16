/* SPDX-License-Identifier: (GPL-2.0-only OR BSD-3-Clause) */
/*
 * DPAA1 Ethernet -- QMan CEETM HTB-offload egress shaper consumer.
 *
 * Bridges the kernel TC_SETUP_QDISC_HTB offload contract (stock
 * "tc qdisc add ... htb offload") to the QMan CEETM hierarchical egress
 * traffic manager exported by <soc/fsl/qman.h>. See dpaa_ceetm.c for the
 * full mapping description.
 */
#ifndef __DPAA_CEETM_H
#define __DPAA_CEETM_H

#include <linux/netdevice.h>

/*
 * Number of extra Tx queues reserved on the netdev for HTB-offload leaf
 * classes. CEETM exposes up to 32 class-queue channels per LNI, so we
 * allocate one netdev Tx queue per potential leaf class on top of the
 * conventional WQ Tx queues (dpaa_max_num_txqs()). The leaf Tx queues
 * occupy the index range [dpaa_max_num_txqs(), dpaa_max_num_txqs() + 32).
 */
#define DPAA_CEETM_MAX_TXQS	32

struct dpaa_priv;
struct dpaa_ceetm;
struct qman_fq;

#if IS_ENABLED(CONFIG_DPAA_HW_CEETM)

/**
 * dpaa_ceetm_setup_htb - handle a TC_SETUP_QDISC_HTB offload command
 * @net_dev: the DPAA Ethernet netdev
 * @type_data: a struct tc_htb_qopt_offload from the tc core
 *
 * Return: 0 on success or a negative errno (with an extack message set).
 */
int dpaa_ceetm_setup_htb(struct net_device *net_dev, void *type_data);

/**
 * dpaa_ceetm_egress_fq - resolve the CEETM enqueue FQ for a Tx queue
 * @ceetm: the live CEETM state (already loaded from priv->ceetm)
 * @queue_mapping: the skb/xdpf Tx queue index
 *
 * Leaf Tx queues (>= base) map to their per-class LFQ; every other Tx
 * queue (conventional WQ queues, which the hardware no longer dequeues
 * once CEETM mode is enabled) maps to the default channel's LFQ so that
 * unclassified traffic still egresses. Returns NULL until def is built.
 */
struct qman_fq *dpaa_ceetm_egress_fq(struct dpaa_ceetm *ceetm,
				     int queue_mapping);

/**
 * dpaa_ceetm_teardown - release a port's CEETM hierarchy unconditionally
 * @priv: the DPAA Ethernet private data
 *
 * Idempotent. Used on the netdev stop/remove path to guarantee the
 * hierarchy is released even if the HTB qdisc was not torn down cleanly.
 */
void dpaa_ceetm_teardown(struct dpaa_priv *priv);

/**
 * dpaa_ceetm_select_queue - ndo_select_queue: steer to an HTB leaf Tx queue
 * @net_dev: the DPAA Ethernet netdev
 * @skb: the frame being transmitted; skb->priority selects the HTB class
 * @sb_dev: subordinate device (unused)
 *
 * Return: the netdev Tx queue index backing the resolved HTB class, or a
 * conventional Tx queue for unclassified traffic.
 */
u16 dpaa_ceetm_select_queue(struct net_device *net_dev, struct sk_buff *skb,
			    struct net_device *sb_dev);

#else /* !CONFIG_DPAA_HW_CEETM */

static inline int dpaa_ceetm_setup_htb(struct net_device *net_dev,
				       void *type_data)
{
	return -EOPNOTSUPP;
}

static inline struct qman_fq *dpaa_ceetm_egress_fq(struct dpaa_ceetm *ceetm,
						   int queue_mapping)
{
	return NULL;
}

static inline void dpaa_ceetm_teardown(struct dpaa_priv *priv) { }

#endif /* CONFIG_DPAA_HW_CEETM */

#endif /* __DPAA_CEETM_H */
