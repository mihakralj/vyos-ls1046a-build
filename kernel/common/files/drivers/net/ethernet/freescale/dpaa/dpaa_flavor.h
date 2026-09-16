/* SPDX-License-Identifier: BSD-3-Clause OR GPL-2.0-or-later */
/*
 * dpaa_flavor.h - flavor ops abstraction for fsl_dpa
 *
 * Two RCU-protected ops tables let an optional in-tree flavor module attach
 * extra behaviour to fsl_dpa without touching dpaa_eth core:
 *   - struct dpaa_pcd_ops   : probe-/remove-time PCD setup (CC trees, HM
 *                              nodes, Policer profiles, CEETM, etc.).
 *   - struct dpaa_qmgmt_ops : per-queue ingress hooks, XSK pool attach /
 *                              detach / wakeup.
 *
 * With no flavor module loaded both ops pointers are NULL and the core
 * driver behaves exactly as in mainline.
 *
 * Spec: specs/dpaa1-afxdp-modernization-spec.md sec 3 + sec 5.1 (M0).
 */

#ifndef __DPAA_FLAVOR_H
#define __DPAA_FLAVOR_H

#include <linux/types.h>
#include <linux/list.h>
#include <linux/seq_file.h>
#include <linux/netlink.h>

struct dpaa_priv;
struct qman_fq;
struct qm_dqrr_entry;
struct xsk_buff_pool;

/**
 * struct dpaa_pcd_ops - PCD lifecycle operations
 * @install:     Called from dpaa_eth_probe() AFTER FMan port setup and FQ
 *               init, BEFORE register_netdev(). Returning non-zero aborts
 *               probe.
 * @teardown:    Called from dpaa_remove() BEFORE unregister_netdev().
 * @reconfig:    Optional runtime reconfiguration (e.g. VyOS commit).
 * @dump_state:  Optional debugfs / ethtool dump helper.
 */
struct dpaa_pcd_ops {
	int  (*install)(struct dpaa_priv *priv);
	void (*teardown)(struct dpaa_priv *priv);
	int  (*reconfig)(struct dpaa_priv *priv, struct nlattr *params);
	int  (*dump_state)(struct dpaa_priv *priv, struct seq_file *m);
};

/**
 * struct dpaa_qmgmt_ops - per-queue management operations
 * @alloc_rx_fqs:     Optional: append flavor-specific RX FQs at probe time.
 * @rx_hook:          Optional per-frame hook called from the QMan DQRR
 *                    callback BEFORE the normal RX path. Return true to
 *                    consume the frame; return false to let the default
 *                    path handle it.
 * @xsk_pool_attach:  XDP_SETUP_XSK_POOL attach. NULL = AF_XDP ZC unsupported.
 * @xsk_pool_detach:  XDP_SETUP_XSK_POOL detach.
 * @xsk_wakeup:       ndo_xsk_wakeup backend.
 */
struct dpaa_qmgmt_ops {
	int  (*alloc_rx_fqs)(struct dpaa_priv *priv, struct list_head *list);
	bool (*rx_hook)(struct dpaa_priv *priv, struct qman_fq *fq,
			const struct qm_dqrr_entry *dq);
	int  (*xsk_pool_attach)(struct dpaa_priv *priv,
				struct xsk_buff_pool *pool, u16 queue_id);
	int  (*xsk_pool_detach)(struct dpaa_priv *priv, u16 queue_id);
	int  (*xsk_wakeup)(struct dpaa_priv *priv, u32 queue_id, u32 flags);
};

#ifdef CONFIG_DPAA_FLAVOR_OPS

int  dpaa_register_flavor_ops(const struct dpaa_pcd_ops *pcd_ops,
			      const struct dpaa_qmgmt_ops *qmgmt_ops);
void dpaa_unregister_flavor_ops(void);

void dpaa_priv_attach_flavor_ops(struct dpaa_priv *priv);
void dpaa_priv_detach_flavor_ops(struct dpaa_priv *priv);

#else  /* !CONFIG_DPAA_FLAVOR_OPS */

static inline void dpaa_priv_attach_flavor_ops(struct dpaa_priv *priv) {}
static inline void dpaa_priv_detach_flavor_ops(struct dpaa_priv *priv) {}

#endif /* CONFIG_DPAA_FLAVOR_OPS */

#endif /* __DPAA_FLAVOR_H */
