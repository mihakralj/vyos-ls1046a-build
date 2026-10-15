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
struct dpaa_napi_portal;

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

/* Forward declaration -- struct qm_fd is defined in <soc/fsl/qman.h>
 * but we deliberately do NOT include that header here to keep this
 * inter-module ABI surface minimal. Callers and implementors of
 * tx_conf_zc must pull in qman.h themselves.
 */
struct qm_fd;

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
 * @xsk_set_rx_need_wakeup: arm XSK rx-need-wakeup at NAPI completion.
 * @napi_refill:      M3-3 step 4 - top up BMan pool from XSK fill ring
 *                    at NAPI completion. Spec sec 6.1.3.
 * @napi_tx_zc:       M3-3 step 5 - drain XSK Tx ring into the DPAA1
 *                    egress FQs at NAPI completion, with software
 *                    xsk_tx_inflight backpressure. Spec sec 6.1.4.
 * @xsk_set_tx_need_wakeup: arm XSK tx-need-wakeup when in-flight budget
 *                    is exhausted OR the Tx ring is drained.
 *                    Spec sec 6.1.4.
 * @tx_conf_zc:       M3-3 step 5 - claim XSK-bpid TxConf FDs in the
 *                    dpaa_tx_conf() path. Returns true to short-circuit
 *                    the skb cleanup path; calls xsk_tx_completed()
 *                    and atomic_dec(xsk_tx_inflight). Spec sec 6.1.4.
 */
struct dpaa_qmgmt_ops {
	int  (*alloc_rx_fqs)(struct dpaa_priv *priv, struct list_head *list);
	bool (*rx_hook)(struct dpaa_priv *priv, struct qman_fq *fq,
			const struct qm_dqrr_entry *dq);
	int  (*xsk_pool_attach)(struct dpaa_priv *priv,
				struct xsk_buff_pool *pool, u16 queue_id);
	int  (*xsk_pool_detach)(struct dpaa_priv *priv, u16 queue_id);
	int  (*xsk_wakeup)(struct dpaa_priv *priv, u32 queue_id, u32 flags);
void (*xsk_set_rx_need_wakeup)(struct dpaa_priv *priv,
       struct dpaa_napi_portal *np);
void (*napi_refill)(struct dpaa_priv *priv);
void (*napi_tx_zc)(struct dpaa_priv *priv);
void (*xsk_set_tx_need_wakeup)(struct dpaa_priv *priv,
       struct dpaa_napi_portal *np);
bool (*tx_conf_zc)(struct dpaa_priv *priv, const struct qm_fd *fd);
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
