// SPDX-License-Identifier: GPL-2.0-or-later
/*
 * af_xdp_pool_main.c - DPAA1 AF_XDP zero-copy pool flavor module (skeleton)
 *
 * M3.1 deliverable per specs/dpaa1-afxdp-modernization-spec.md sec 5.4.
 *
 * This module registers a dpaa_qmgmt_ops table with the fsl_dpa core driver
 * via the M0 flavor-ops abstraction (CONFIG_DPAA_FLAVOR_OPS).  At this
 * skeleton stage every callback returns -EOPNOTSUPP so behaviour with the
 * module loaded is byte-identical to behaviour with it absent: every XSK
 * setsockopt(XDP_BIND, XDP_ZEROCOPY) falls back to copy-mode exactly as
 * mainline does today.
 *
 * Subsequent patches (0074-0080) replace each stub with the real
 * implementation:
 *   0074 - xsk_wakeup        : NAPI kick
 *   0075 - xsk_pool_attach   : UMEM-backed BMan pool + PAMU window
 *   0076 - xsk_pool_detach   : FMan BMI quiescence
 *   0077 - rx_hook           : ZC RX with xdp_do_redirect
 *   0078 - (TX is core-driver, not a flavor hook) - strict backpressure
 *   0079 - xsk_set_rx_need_wakeup
 *   0080 - alloc_rx_fqs      : dedicated XSK BMan channels
 */

#include <linux/init.h>
#include <linux/kernel.h>
#include <linux/module.h>
#include <linux/types.h>
#include <linux/errno.h>
#include <linux/printk.h>

#include "dpaa_flavor.h"

struct dpaa_priv;
struct qman_fq;
struct qm_dqrr_entry;
struct xsk_buff_pool;
struct dpaa_napi_portal;
struct list_head;

/* ---------- Stub callbacks: every entry returns -EOPNOTSUPP ---------- */

static int af_xdp_pool_alloc_rx_fqs(struct dpaa_priv *priv,
struct list_head *list)
{
return -EOPNOTSUPP;
}

static bool af_xdp_pool_rx_hook(struct dpaa_priv *priv,
struct qman_fq *fq,
const struct qm_dqrr_entry *dq)
{
/* false = do not consume the frame; let the default RX path handle it. */
return false;
}

static int af_xdp_pool_xsk_pool_attach(struct dpaa_priv *priv,
       struct xsk_buff_pool *pool,
       u16 queue_id)
{
return -EOPNOTSUPP;
}

static int af_xdp_pool_xsk_pool_detach(struct dpaa_priv *priv, u16 queue_id)
{
return -EOPNOTSUPP;
}

static int af_xdp_pool_xsk_wakeup(struct dpaa_priv *priv, u32 queue_id,
  u32 flags)
{
return -EOPNOTSUPP;
}

static void af_xdp_pool_xsk_set_rx_need_wakeup(struct dpaa_priv *priv,
       struct dpaa_napi_portal *np)
{
/* Nothing to do until the ZC datapath lands in patch 0079. */
}

/* ---------- Ops tables ---------- */

static const struct dpaa_qmgmt_ops af_xdp_pool_qmgmt_ops = {
.alloc_rx_fqs           = af_xdp_pool_alloc_rx_fqs,
.rx_hook                = af_xdp_pool_rx_hook,
.xsk_pool_attach        = af_xdp_pool_xsk_pool_attach,
.xsk_pool_detach        = af_xdp_pool_xsk_pool_detach,
.xsk_wakeup             = af_xdp_pool_xsk_wakeup,
.xsk_set_rx_need_wakeup = af_xdp_pool_xsk_set_rx_need_wakeup,
};

/* ---------- Module init / exit ---------- */

static int __init af_xdp_pool_init(void)
{
int err;

err = dpaa_register_flavor_ops(NULL, &af_xdp_pool_qmgmt_ops);
if (err) {
pr_err("af_xdp_pool: dpaa_register_flavor_ops() failed: %d\n", err);
return err;
}

pr_info("af_xdp_pool: registered (skeleton, all callbacks stubbed -EOPNOTSUPP)\n");
return 0;
}

static void __exit af_xdp_pool_exit(void)
{
dpaa_unregister_flavor_ops();
pr_info("af_xdp_pool: unregistered\n");
}

module_init(af_xdp_pool_init);
module_exit(af_xdp_pool_exit);

MODULE_AUTHOR("VyOS LS1046A maintainers");
MODULE_DESCRIPTION("DPAA1 AF_XDP zero-copy pool flavor module (skeleton)");
MODULE_LICENSE("GPL v2");