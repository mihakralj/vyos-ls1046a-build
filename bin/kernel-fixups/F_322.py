#!/usr/bin/env python3
"""F-322: WGFP-v2 confirmed TX FQ on offload_cgr, batched BMan release, and bpid=0xff guards.

Supports the WGFP-v2 drain-safe zero-copy QMan TX path in drivers/net/ethernet/freescale/dpaa/:
  * Adds FQ_TYPE_TX_WGFP in dpaa_eth.h (after FQ_TYPE_TX_NO_CONFIRM).
  * Adds dpaa_wgfp_bman_release_batch(bpid, addrs, n) next to dpaa_wgfp_fd_release()
    so WGFP can release up to 8 RX buffers in a single BMan RCR command.
  * Adds a separate FQ_TYPE_TX_WGFP branch in dpaa_fq_init() (without modifying F-316's
    FQ_TYPE_TX_NO_CONFIRM or td_enable anchors):
      - context_a = 0x9a00000080000000 (OVFQ=1, OVOM=1, A2V=1, B0V=0, A0V=1, EBD=1)
      - QM_INITFQ_WE_CONTEXTB with context_b = 0 (no recycled-FQID staleness)
      - priv->offload_cgr (F-316 frame-mode CSTD CGR, PREFERINCACHE, no OAC, no TDE)
      - QM_INITFQ_WE_ICSCRED with ics_cred = 0.
  * Adds dpaa_alloc_wgfp_tx_fq(dev, fqid, tx_dma_dev) requiring ASSERT_RTNL() (no internal
    rtnl_lock, avoiding AB-BA deadlock with wgfp_netdev_event()), verifying NETREG_REGISTERED,
    dpaa_ops, priv->offload_cgr, !priv->ceetm, and F-295-style fault retention.
  * Guards all 3 DPAA bpid=0xff fallback paths (dpaa_tx_error, dpaa_tx_conf, egress_ern)
    with dpaa_wgfp_tx_error_claim(fd) before dpaa_cleanup_tx_fd() so a WGFP FD never gets
    dma_unmap'd or interpreted as a struct dpaa_eth_swbp.

Needs F-199, F-227, F-295, F-300, F-316, F-320. Idempotent (marker F-322).
"""

from pathlib import Path
import sys

ETH_H = Path("drivers/net/ethernet/freescale/dpaa/dpaa_eth.h")
ETH_C = Path("drivers/net/ethernet/freescale/dpaa/dpaa_eth.c")
MARK = "F-322"

EDITS = (
    (ETH_H, "FQ_TYPE_TX_WGFP enum",
     "\tFQ_TYPE_TX_NO_CONFIRM,\t/* F-199: offload TX FQ, no TX-confirm FD */\n"
     "};",
     "\tFQ_TYPE_TX_NO_CONFIRM,\t/* F-199: offload TX FQ, no TX-confirm FD */\n"
     "\tFQ_TYPE_TX_WGFP,\t/* F-322: WGFP confirmed TX FQ on offload_cgr */\n"
     "};"),

    (ETH_H, "dpaa_alloc_wgfp_tx_fq prototype",
     "struct dpaa_ceetm;\n",
     "struct dpaa_ceetm;\n"
     "/* F-322: WGFP confirmed TX FQ allocator & BMan batch release (built-in fsl_dpa). */\n"
     "int dpaa_alloc_wgfp_tx_fq(struct net_device *dev, u32 *fqid,\n"
     "\t\t\t  struct device **tx_dma_dev);\n"
     "void dpaa_wgfp_bman_release_batch(u8 bpid, const dma_addr_t *addrs, int n);\n"),

    (ETH_C, "WGFP fallback claim declarations & BMan batch release",
     "void dpaa_wgfp_fd_release(const struct qm_fd *fd)\n"
     "{\n"
     "\tstruct dpaa_bp *dpaa_bp = dpaa_bpid2pool(fd->bpid);\n"
     "\tstruct bm_buffer bmb;\n"
     "\n"
     "\tif (!dpaa_bp)\n"
     "\t\treturn;\n"
     "\tbmb.data = 0;\n"
     "\tbm_buffer_set64(&bmb, qm_fd_addr(fd));\n"
     "\tdpaa_bman_release(dpaa_bp, &bmb, 1);\n"
     "}\n",
     "bool dpaa_wgfp_has_tx_slots(void);\n"
     "bool dpaa_wgfp_tx_error_claim(const struct qm_fd *fd);\n"
     "\n"
     "void dpaa_wgfp_fd_release(const struct qm_fd *fd)\n"
     "{\n"
     "\tstruct dpaa_bp *dpaa_bp = dpaa_bpid2pool(fd->bpid);\n"
     "\tstruct bm_buffer bmb;\n"
     "\n"
     "\tif (!dpaa_bp)\n"
     "\t\treturn;\n"
     "\tbmb.data = 0;\n"
     "\tbm_buffer_set64(&bmb, qm_fd_addr(fd));\n"
     "\tdpaa_bman_release(dpaa_bp, &bmb, 1);\n"
     "}\n"
     "\n"
     "/* F-322: release up to 8 RX buffers in a single BMan RCR command. */\n"
     "void dpaa_wgfp_bman_release_batch(u8 bpid, const dma_addr_t *addrs, int n)\n"
     "{\n"
     "\tstruct dpaa_bp *dpaa_bp = dpaa_bpid2pool(bpid);\n"
     "\tstruct bm_buffer bmb[8];\n"
     "\tint i;\n"
     "\n"
     "\tif (unlikely(!dpaa_bp || n <= 0))\n"
     "\t\treturn;\n"
     "\tif (unlikely(n > 8))\n"
     "\t\tn = 8;\n"
     "\tfor (i = 0; i < n; i++) {\n"
     "\t\tbmb[i].data = 0;\n"
     "\t\tbm_buffer_set64(&bmb[i], addrs[i]);\n"
     "\t}\n"
     "\tdpaa_bman_release(dpaa_bp, bmb, n);\n"
     "}\n"),

    (ETH_C, "FQ_TYPE_TX_WGFP init branch",
     "\t\t\tinitfq.fqd.cgid = (u8)priv->offload_cgr->cgr.cgrid;\n"
     "\t\t\tinitfq.fqd.ics_cred = cpu_to_be16(dpaa_fq->ics_cred);\n"
     "\t\t}\n",
     "\t\t\tinitfq.fqd.cgid = (u8)priv->offload_cgr->cgr.cgrid;\n"
     "\t\t\tinitfq.fqd.ics_cred = cpu_to_be16(dpaa_fq->ics_cred);\n"
     "\t\t}\n"
     "\n"
     "\t\tif (dpaa_fq->fq_type == FQ_TYPE_TX_WGFP) {\n"
     "\t\t\t/* F-322: confirmed WGFP TX FQ on priv->offload_cgr.\n"
     "\t\t\t * context_a 0x9a00000080000000 honors FM_FD_CMD_FCO\n"
     "\t\t\t * override to per-CPU WGFP confirm FQ for bpid=0xff;\n"
     "\t\t\t * clear context_b and ics_cred on recycled FQIDs. */\n"
     "\t\t\tinitfq.we_mask |= cpu_to_be16(QM_INITFQ_WE_CONTEXTA |\n"
     "\t\t\t\t\t\t      QM_INITFQ_WE_CONTEXTB |\n"
     "\t\t\t\t\t\t      QM_INITFQ_WE_CGID |\n"
     "\t\t\t\t\t\t      QM_INITFQ_WE_ICSCRED);\n"
     "\t\t\tqm_fqd_context_a_set64(&initfq.fqd,\n"
     "\t\t\t\t\t       0x9a00000080000000ULL);\n"
     "\t\t\tinitfq.fqd.context_b = 0;\n"
     "\t\t\tinitfq.fqd.fq_ctrl |= cpu_to_be16(QM_FQCTRL_CGE);\n"
     "\t\t\tinitfq.fqd.cgid = (u8)priv->offload_cgr->cgr.cgrid;\n"
     "\t\t\tinitfq.fqd.ics_cred = 0;\n"
     "\t\t}\n"),

    (ETH_C, "dpaa_tx_error WGFP claim guard",
     "\tif (dpaa_tx_zc_fail(priv, fd))\n"
     "\t\treturn;\n"
     "\n"
     "\tskb = dpaa_cleanup_tx_fd(priv, fd, false);\n"
     "\tdev_kfree_skb(skb);\n",
     "\tif (dpaa_tx_zc_fail(priv, fd))\n"
     "\t\treturn;\n"
     "\n"
     "\t/* F-322: claim WGFP-owned bpid=0xff TX error FD before skb cleanup. */\n"
     "\tif (unlikely(dpaa_wgfp_has_tx_slots() && dpaa_wgfp_tx_error_claim(fd)))\n"
     "\t\treturn;\n"
     "\n"
     "\tskb = dpaa_cleanup_tx_fd(priv, fd, false);\n"
     "\tdev_kfree_skb(skb);\n"),

    (ETH_C, "dpaa_tx_conf WGFP defense-in-depth guard",
     "\tpercpu_priv->tx_confirm++;\n"
     "\n"
     "\tskb = dpaa_cleanup_tx_fd(priv, fd, true);\n",
     "\tpercpu_priv->tx_confirm++;\n"
     "\n"
     "\t/* F-322: guard tx_defq if a WGFP bpid=0xff FD ever misses FCO. */\n"
     "\tif (unlikely(dpaa_wgfp_has_tx_slots() &&\n"
     "\t\t     !(qm_fd_addr(fd) & ~PAGE_MASK) &&\n"
     "\t\t     dpaa_wgfp_tx_error_claim(fd)))\n"
     "\t\treturn;\n"
     "\n"
     "\tskb = dpaa_cleanup_tx_fd(priv, fd, true);\n"),

    (ETH_C, "egress_ern WGFP claim guard",
     "\tif (dpaa_tx_zc_fail(priv, fd))\n"
     "\t\treturn;\n"
     "\n"
     "\tskb = dpaa_cleanup_tx_fd(priv, fd, false);\n"
     "\tdev_kfree_skb_any(skb);\n",
     "\tif (dpaa_tx_zc_fail(priv, fd))\n"
     "\t\treturn;\n"
     "\n"
     "\t/* F-322: claim WGFP-owned bpid=0xff ERN FD before skb cleanup. */\n"
     "\tif (unlikely(dpaa_wgfp_has_tx_slots() && dpaa_wgfp_tx_error_claim(fd)))\n"
     "\t\treturn;\n"
     "\n"
     "\tskb = dpaa_cleanup_tx_fd(priv, fd, false);\n"
     "\tdev_kfree_skb_any(skb);\n"),

    (ETH_C, "dpaa_alloc_wgfp_tx_fq allocator",
     "EXPORT_SYMBOL_GPL(dpaa_alloc_offload_tx_fq);\n",
     "EXPORT_SYMBOL_GPL(dpaa_alloc_offload_tx_fq);\n"
     "\n"
     "/* F-322: allocate a dedicated confirmed TX FQ on priv->offload_cgr for WGFP.\n"
     " * Caller MUST hold RTNL (avoids AB-BA lock inversion with wgfp_netdev_event).\n"
     " * Replicates F-295 fault retention on uncertain qman_init_fq() failure and\n"
     " * refuses ports with active CEETM (which bypasses conventional WQ FQs). */\n"
     "int dpaa_alloc_wgfp_tx_fq(struct net_device *dev, u32 *fqid,\n"
     "\t\t\t  struct device **tx_dma_dev)\n"
     "{\n"
     "\tstatic bool wgfp_fq_faulted;\n"
     "\tstatic netdevice_tracker wgfp_fq_tracker;\n"
     "\tstruct dpaa_priv *priv;\n"
     "\tstruct dpaa_fq *dpaa_fq;\n"
     "\tbool retain_failed_fq = false;\n"
     "\tint ret;\n"
     "\n"
     "\tASSERT_RTNL();\n"
     "\tif (!dev || !fqid || !tx_dma_dev)\n"
     "\t\treturn -EINVAL;\n"
     "\tif (READ_ONCE(dev->reg_state) != NETREG_REGISTERED ||\n"
     "\t    dev->netdev_ops != &dpaa_ops)\n"
     "\t\treturn -ENODEV;\n"
     "\tif (wgfp_fq_faulted)\n"
     "\t\treturn -ESHUTDOWN;\n"
     "\tpriv = netdev_priv(dev);\n"
     "\tif (!priv->offload_cgr)\n"
     "\t\treturn -ENODEV;\n"
     "#if IS_ENABLED(CONFIG_DPAA_HW_CEETM)\n"
     "\tif (smp_load_acquire(&priv->ceetm))\n"
     "\t\treturn -EBUSY;\n"
     "#endif\n"
     "\tdpaa_fq = devm_kzalloc(dev->dev.parent, sizeof(*dpaa_fq), GFP_KERNEL);\n"
     "\tif (!dpaa_fq)\n"
     "\t\treturn -ENOMEM;\n"
     "\tdpaa_fq->fq_type = FQ_TYPE_TX_WGFP;\n"
     "\tdpaa_fq->fqid = 0;\n"
     "\tdpaa_setup_egress(priv, dpaa_fq, priv->mac_dev->port[TX],\n"
     "\t\t\t  &dpaa_fq_cbs.egress_ern);\n"
     "\tdpaa_fq->wq = 6;\n"
     "\tlist_add_tail(&dpaa_fq->list, &priv->dpaa_fq_list);\n"
     "\tret = dpaa_fq_init(dpaa_fq, false, &retain_failed_fq);\n"
     "\tif (ret < 0) {\n"
     "\t\tif (retain_failed_fq) {\n"
     "\t\t\tnetdev_hold(dev, &wgfp_fq_tracker, GFP_KERNEL);\n"
     "\t\t\twgfp_fq_faulted = true;\n"
     "\t\t\tnetdev_err(dev,\n"
     "\t\t\t\t   \"F-322: WGFP TX FQ init failed (%d); retaining FQ and netdev\\n\",\n"
     "\t\t\t\t   ret);\n"
     "\t\t} else {\n"
     "\t\t\tlist_del(&dpaa_fq->list);\n"
     "\t\t\tdevm_kfree(dev->dev.parent, dpaa_fq);\n"
     "\t\t}\n"
     "\t\treturn ret;\n"
     "\t}\n"
     "\t*fqid = dpaa_fq->fqid;\n"
     "\t*tx_dma_dev = priv->tx_dma_dev;\n"
     "\tnetdev_info(dev,\n"
     "\t\t    \"F-322: WGFP confirmed TX FQ 0x%x on channel %u (cgrid %u)\\n\",\n"
     "\t\t    dpaa_fq->fqid, dpaa_fq->channel,\n"
     "\t\t    priv->offload_cgr->cgr.cgrid);\n"
     "\treturn 0;\n"
     "}\n"),
)


def main():
    try:
        files = {p: p.read_text() for p in (ETH_H, ETH_C)}
        if all(MARK in files[p] for p in files):
            for path, label, _, new in EDITS:
                if files[path].count(new) != 1:
                    raise ValueError(f"partially applied: {label}")
            print("### F-322: already applied")
            return 0
        if any(MARK in files[p] for p in files):
            raise ValueError("partially applied: marker in some files only")
        for path, label, old, new in EDITS:
            count = files[path].count(old)
            if count != 1:
                raise ValueError(f"{label}: found {count} anchors, expected 1")
            files[path] = files[path].replace(old, new, 1)
        for path, text in files.items():
            path.write_text(text)
    except (OSError, ValueError) as error:
        print(f"### F-322: FATAL: {error}", file=sys.stderr)
        return 1
    print(f"### F-322: WGFP confirmed TX FQ, batch BMan release, and fallback guards applied ({len(EDITS)} blocks)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
