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
#include <linux/fsl/dpaa_flow_offload.h>

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
struct qm_fd;
struct flow_block_offload;

/* Shared with dpaa_eth.c (F-327). */
int dpaa_fo_notify_egress_change(struct net_device *net_dev,
				 enum dpaa_egress_event ev, u32 fqid);
void dpaa_fd_release(const struct net_device *net_dev,
		     const struct qm_fd *fd);

#if IS_ENABLED(CONFIG_DPAA_HW_CEETM)

int dpaa_ceetm_setup_htb(struct net_device *net_dev, void *type_data);

int dpaa_ceetm_setup_dscp_block(struct net_device *net_dev,
				struct flow_block_offload *f);

void dpaa_ceetm_dscp_netdev_unregister(struct net_device *net_dev);

struct qman_fq *dpaa_ceetm_egress_fq(struct dpaa_ceetm *ceetm,
				     int queue_mapping);

void dpaa_ceetm_teardown(struct dpaa_priv *priv);

u16 dpaa_ceetm_select_queue(struct net_device *net_dev, struct sk_buff *skb,
			    struct net_device *sb_dev);

void dpaa_ceetm_update_mtu(struct dpaa_priv *priv, unsigned int mtu);

#else /* !CONFIG_DPAA_HW_CEETM */

static inline int dpaa_ceetm_setup_htb(struct net_device *net_dev,
				       void *type_data)
{
	return -EOPNOTSUPP;
}

static inline int dpaa_ceetm_setup_dscp_block(struct net_device *net_dev,
					      struct flow_block_offload *f)
{
	return -EOPNOTSUPP;
}

static inline void dpaa_ceetm_dscp_netdev_unregister(struct net_device *net_dev)
{
}

static inline struct qman_fq *dpaa_ceetm_egress_fq(struct dpaa_ceetm *ceetm,
						   int queue_mapping)
{
	return NULL;
}

static inline void dpaa_ceetm_teardown(struct dpaa_priv *priv) { }

static inline void dpaa_ceetm_update_mtu(struct dpaa_priv *priv,
					 unsigned int mtu) { }

#endif /* CONFIG_DPAA_HW_CEETM */

#endif /* __DPAA_CEETM_H */
