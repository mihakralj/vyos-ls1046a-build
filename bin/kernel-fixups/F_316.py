#!/usr/bin/env python3
"""F-316: shared frame-mode QMan CGR for F-199 offload no-confirm TX FQs.

Replaces per-FQ FQD byte tail-drop (TDE) on FQ_TYPE_TX_NO_CONFIRM with a
dedicated per-port frame-mode QMan CGR (priv->offload_cgr, CSTD enabled,
CSCN disabled, F-300 lifecycle containment), preserving QM_FQCTRL_PREFERINCACHE
and keeping WQ6 parity with kernel TC0 TX.

Why:
  * Per-FQ 256 KiB TDE on a 16-FQ set dropped bulk bursts whenever a single
    flow exceeded 256 KiB (vendor bulk FQs reach 338-523 KiB), causing
    ~50k-150k FMan RX frame-drop (rfdc) + MAC rdrp drops and 3x-22x TCP
    retransmits vs vendor on 1500-MTU bidirectional BBR.
  * TDE also clobbered fq_ctrl = QM_FQCTRL_TDE, clearing PREFERINCACHE.
  * Byte-mode 4 MiB TDE on a single FQ exhausted the 5,120-buffer RX pool
    (rodc) on mixed data + small IPv6 ACK traffic (vendor bug A341).
  * A shared frame-mode CGR (1,624 frames at 10G/1500 MTU, 276 frames at
    10G/9000 MTU; capped at half the port's RX pool accounting for exact S/G
    buffer cost) lets individual flows burst freely while bounding aggregate
    egress occupancy below the RX buffer pool for all packet sizes.

Adds:
  * struct dpaa_priv.{offload_cgr,offload_cgr_lock,offload_cgr_speed} and
    boot-time module param fsl_dpa.offload_queue_us (default 2000 us, 0444),
  * dpaa_offload_cgr_frames/init/update() hooked into probe (after
    rx_headroom), remove/unwind (after dpaa_fq_free via F-300 release),
    dpaa_eth_cgr_set_speed() and dpaa_change_mtu(),
  * struct dpaa_fq.ics_cred (0..0x7fff) and exported
    dpaa_alloc_offload_tx_fq_ics(dev, ics_cred, *fqid) with
    dpaa_alloc_offload_tx_fq(dev, *fqid) kept as the ics_cred = 0 wrapper,
  * dpaa_fq_init() attaches FQ_TYPE_TX_NO_CONFIRM to priv->offload_cgr
    (PREFERINCACHE | CGE, no OAC, no per-FQ TDE, unconditional WE_ICSCRED to
    clear stale DRR credit on recycled dynamic FQIDs) and fixes td_enable to
    OR QM_FQCTRL_TDE instead of clobbering fq_ctrl.

Needs F-199, F-295 and F-300 (preserves idempotency of F-295 and F-300).
Idempotent (marker); every anchor must match exactly once. Host checks:
bin/test-dpaa-offload-fq.py and
bin/test-qman-cgr-containment.py --require-offload-cgr.
"""

from pathlib import Path
import sys

ETH_H = Path("drivers/net/ethernet/freescale/dpaa/dpaa_eth.h")
ETH_C = Path("drivers/net/ethernet/freescale/dpaa/dpaa_eth.c")
OFF_H = Path("include/linux/fsl/dpaa_flow_offload.h")
MARK = "F-316"

OFFLOAD_CGR_HELPERS = """\
/* F-316: compute shared frame-mode offload TX CGR threshold for speed/MTU. */
static u32 dpaa_offload_cgr_frames(const struct dpaa_priv *priv, int speed,
\t\t\t\t   unsigned int mtu)
{
\tu64 rate_mbps, queue_us, wire_bytes, target;
\tu64 bp_size, first_cap, max_frame, bufs_per_frame, pool_bufs, pool_cap;
\tu32 mtu_eff;

\tif (speed > 0 && speed != SPEED_UNKNOWN)
\t\trate_mbps = speed;
\telse if (priv->mac_dev->phylink_config.mac_capabilities & MAC_10000FD)
\t\trate_mbps = SPEED_10000;
\telse
\t\trate_mbps = SPEED_1000;

\tmtu_eff = max_t(u32, mtu, ETH_DATA_LEN);
\tqueue_us = clamp_t(u64, dpaa_offload_queue_us, 100, 10000);
\twire_bytes = (u64)mtu_eff + ETH_HLEN + ETH_FCS_LEN + 20;
\ttarget = div64_u64(rate_mbps * queue_us, wire_bytes * 8);
\ttarget = max_t(u64, target, 32);

\tbp_size = priv->dpaa_bp ? priv->dpaa_bp->size : 0;
\tif (!bp_size)
\t\tbp_size = 3584;
\tfirst_cap = bp_size > priv->rx_headroom ?
\t\t    bp_size - priv->rx_headroom : bp_size;
\tmax_frame = (u64)mtu_eff + ETH_HLEN + ETH_FCS_LEN + 2 * VLAN_HLEN;
\tif (max_frame <= first_cap)
\t\tbufs_per_frame = 1;
\telse
\t\tbufs_per_frame = 2 + DIV_ROUND_UP(max_frame - first_cap, bp_size);

\tpool_bufs = (u64)(priv->dpaa_bp && priv->dpaa_bp->config_count ?
\t\t\t  priv->dpaa_bp->config_count :
\t\t\t  FSL_DPAA_ETH_MAX_BUF_COUNT) * num_possible_cpus();
\tpool_cap = max_t(u64, pool_bufs / 2 / bufs_per_frame, 1);

\treturn (u32)min_t(u64, target, pool_cap);
}

static int dpaa_offload_cgr_init(struct dpaa_priv *priv)
{
\tstruct device *dev = priv->net_dev->dev.parent;
\tstruct qm_mcc_initcgr initcgr;
\tu32 cs_th;
\tint err;

\tpriv->offload_cgr = dpaa_cgr_alloc(priv);
\tif (!priv->offload_cgr)
\t\treturn -ENOMEM;

\terr = qman_alloc_cgrid(&priv->offload_cgr->cgr.cgrid);
\tif (err < 0) {
\t\tkfree(priv->offload_cgr);
\t\tpriv->offload_cgr = NULL;
\t\tif (netif_msg_drv(priv))
\t\t\tpr_err("Error %d allocating offload CGR ID\\n", err);
\t\tgoto out_error;
\t}

\tpriv->offload_cgr_speed = SPEED_UNKNOWN;
\tmemset(&initcgr, 0, sizeof(initcgr));
\tinitcgr.we_mask = cpu_to_be16(QM_CGR_WE_MODE | QM_CGR_WE_CS_THRES |
\t\t\t\t      QM_CGR_WE_CSCN_EN | QM_CGR_WE_CSTD_EN);
\tinitcgr.cgr.mode = QMAN_CGR_MODE_FRAME;
\tinitcgr.cgr.cscn_en = 0;
\tinitcgr.cgr.cstd_en = QM_CGR_EN;
\tcs_th = dpaa_offload_cgr_frames(priv, SPEED_UNKNOWN, priv->net_dev->mtu);
\tqm_cgr_cs_thres_set64(&initcgr.cgr.cs_thres, cs_th, 0);

\terr = qman_create_cgr(&priv->offload_cgr->cgr, QMAN_CGR_FLAG_USE_INIT,
\t\t\t      &initcgr);
\tif (err < 0) {
\t\tif (netif_msg_drv(priv))
\t\t\tpr_err("Error %d creating offload CGR with ID %d\\n",
\t\t\t       err, priv->offload_cgr->cgr.cgrid);
\t\tdpaa_cgr_create_failed(dev, &priv->offload_cgr, err);
\t\tgoto out_error;
\t}

\tdev_info(dev,
\t\t "F-316: offload TX CGR %u frame-mode cs_thres=%llu (target %u, mtu %u)\\n",
\t\t priv->offload_cgr->cgr.cgrid,
\t\t (unsigned long long)qm_cgr_cs_thres_get64(&initcgr.cgr.cs_thres),
\t\t cs_th, priv->net_dev->mtu);

out_error:
\treturn err;
}

static void dpaa_offload_cgr_update(struct dpaa_priv *priv, int speed,
\t\t\t\t    unsigned int mtu)
{
\tstruct qm_mcc_initcgr opts = { };
\tunsigned int eff_mtu;
\tint eff_speed, err;
\tu32 cs_th;

\tif (!priv->offload_cgr)
\t\treturn;

\tmutex_lock(&priv->offload_cgr_lock);
\teff_speed = (speed > 0 && speed != SPEED_UNKNOWN) ?
\t\t    speed : priv->offload_cgr_speed;
\teff_mtu = mtu ? mtu : READ_ONCE(priv->net_dev->mtu);
\tcs_th = dpaa_offload_cgr_frames(priv, eff_speed, eff_mtu);
\topts.we_mask = cpu_to_be16(QM_CGR_WE_CS_THRES);
\tqm_cgr_cs_thres_set64(&opts.cgr.cs_thres, cs_th, 0);
\terr = qman_update_cgr_safe(&priv->offload_cgr->cgr, &opts);
\tif (!err)
\t\tpriv->offload_cgr_speed = eff_speed;
\tmutex_unlock(&priv->offload_cgr_lock);

\tif (err)
\t\tnetdev_err(priv->net_dev,
\t\t\t   "could not update offload CGR %u: %d\\n",
\t\t\t   priv->offload_cgr->cgr.cgrid, err);
\telse
\t\tnetdev_info(priv->net_dev,
\t\t\t    "F-316: offload TX CGR %u cs_thres=%llu (speed %d, mtu %u)\\n",
\t\t\t    priv->offload_cgr->cgr.cgrid,
\t\t\t    (unsigned long long)qm_cgr_cs_thres_get64(&opts.cgr.cs_thres),
\t\t\t    eff_speed, eff_mtu);
}

"""

EDITS = (
    (ETH_H, "fq ics_cred field",
     "\tu8 wq;\n\tenum dpaa_fq_type fq_type;\n",
     "\tu8 wq;\n"
     "\tu16 ics_cred;\t/* F-316: intra-WQ DRR credit for offload TX FQs */\n"
     "\tenum dpaa_fq_type fq_type;\n"),
    (ETH_H, "priv offload_cgr fields",
     "\tbool use_ingress_cgr;\n\tstruct dpaa_cgr_context *ingress_cgr;\n",
     "\tbool use_ingress_cgr;\n"
     "\tstruct dpaa_cgr_context *ingress_cgr;\n"
     "\t/* F-316: shared frame-mode CGR for offload no-confirm TX FQs */\n"
     "\tstruct dpaa_cgr_context *offload_cgr;\n"
     "\tstruct mutex offload_cgr_lock;\n"
     "\tint offload_cgr_speed;\n"),
    (ETH_C, "offload_queue_us param",
     "static uint dpaa_ingress_quarters = 2;\n"
     "module_param_named(ingress_quarters, dpaa_ingress_quarters, uint, 0444);\n"
     "MODULE_PARM_DESC(ingress_quarters,\n"
     "\t\t \"Quarters of a port's pool seed its queues to the CPU may hold (1-4)\");\n",
     "static uint dpaa_ingress_quarters = 2;\n"
     "module_param_named(ingress_quarters, dpaa_ingress_quarters, uint, 0444);\n"
     "MODULE_PARM_DESC(ingress_quarters,\n"
     "\t\t \"Quarters of a port's pool seed its queues to the CPU may hold (1-4)\");\n"
     "\n"
     "static uint dpaa_offload_queue_us = 2000;\n"
     "module_param_named(offload_queue_us, dpaa_offload_queue_us, uint, 0444);\n"
     "MODULE_PARM_DESC(offload_queue_us,\n"
     "\t\t \"Target microseconds of line-rate buffering for offload TX CGR (100-10000; capped at half RX pool)\");\n"),
    (ETH_C, "offload CGR helpers and speed hook",
     "static void dpaa_eth_cgr_set_speed(struct mac_device *mac_dev, int speed)\n{\n",
     OFFLOAD_CGR_HELPERS +
     "static void dpaa_eth_cgr_set_speed(struct mac_device *mac_dev, int speed)\n{\n"),
    (ETH_C, "speed update call",
     "\terr = qman_update_cgr_safe(&priv->cgr_data.ctx->cgr, &opts);\n"
     "\tif (err)\n"
     "\t\tnetdev_err(net_dev, \"could not update speed: %d\\n\", err);\n"
     "}\n",
     "\terr = qman_update_cgr_safe(&priv->cgr_data.ctx->cgr, &opts);\n"
     "\tif (err)\n"
     "\t\tnetdev_err(net_dev, \"could not update speed: %d\\n\", err);\n"
     "\tdpaa_offload_cgr_update(priv, speed, 0);\n"
     "}\n"),
    (ETH_C, "exclude no-confirm from kernel egress CGR",
     "\t\tif (dpaa_fq->fq_type == FQ_TYPE_TX ||\n"
     "\t\t    dpaa_fq->fq_type == FQ_TYPE_TX_CONFIRM ||\n"
     "\t\t    dpaa_fq->fq_type == FQ_TYPE_TX_CONF_MQ ||\n"
     "\t\t    (dpaa_fq->fq_type == FQ_TYPE_TX_NO_CONFIRM && !td_enable)) {\n",
     "\t\tif (dpaa_fq->fq_type == FQ_TYPE_TX ||\n"
     "\t\t    dpaa_fq->fq_type == FQ_TYPE_TX_CONFIRM ||\n"
     "\t\t    dpaa_fq->fq_type == FQ_TYPE_TX_CONF_MQ) {\n"),
    (ETH_C, "skip TDE for offload FQs and preserve fq_ctrl",
     "\t\tif (td_enable) {\n"
     "\t\t\tinitfq.we_mask |= cpu_to_be16(QM_INITFQ_WE_TDTHRESH);\n"
     "\t\t\tqm_fqd_set_taildrop(&initfq.fqd, DPAA_FQ_TD, 1);\n"
     "\t\t\tinitfq.fqd.fq_ctrl = cpu_to_be16(QM_FQCTRL_TDE);\n"
     "\t\t}\n",
     "\t\tif (td_enable && dpaa_fq->fq_type != FQ_TYPE_TX_NO_CONFIRM) {\n"
     "\t\t\tinitfq.we_mask |= cpu_to_be16(QM_INITFQ_WE_TDTHRESH);\n"
     "\t\t\tqm_fqd_set_taildrop(&initfq.fqd, DPAA_FQ_TD, 1);\n"
     "\t\t\tinitfq.fqd.fq_ctrl |= cpu_to_be16(QM_FQCTRL_TDE);\n"
     "\t\t}\n"),
    (ETH_C, "attach no-confirm FQ to offload CGR",
     "\t\tif (dpaa_fq->fq_type == FQ_TYPE_TX_NO_CONFIRM) {\n"
     "\t\t\tinitfq.we_mask |= cpu_to_be16(QM_INITFQ_WE_CONTEXTA);\n"
     "\t\t\tqm_fqd_context_a_set64(&initfq.fqd,\n"
     "\t\t\t\t\t       0x9a000000c0000000ULL);\n"
     "\t\t}\n",
     "\t\tif (dpaa_fq->fq_type == FQ_TYPE_TX_NO_CONFIRM) {\n"
     "\t\t\tinitfq.we_mask |= cpu_to_be16(QM_INITFQ_WE_CONTEXTA);\n"
     "\t\t\tqm_fqd_context_a_set64(&initfq.fqd,\n"
     "\t\t\t\t\t       0x9a000000c0000000ULL);\n"
     "\t\t\t/* F-316: shared frame-mode offload CGR (no per-FQ TDE,\n"
     "\t\t\t * no byte-mode OAC; preserves PREFERINCACHE; always\n"
     "\t\t\t * write ICS_CRED so recycled FQIDs do not keep stale\n"
     "\t\t\t * DRR credits). */\n"
     "\t\t\tinitfq.we_mask |= cpu_to_be16(QM_INITFQ_WE_CGID |\n"
     "\t\t\t\t\t\t      QM_INITFQ_WE_ICSCRED);\n"
     "\t\t\tinitfq.fqd.fq_ctrl |= cpu_to_be16(QM_FQCTRL_CGE);\n"
     "\t\t\tinitfq.fqd.cgid = (u8)priv->offload_cgr->cgr.cgrid;\n"
     "\t\t\tinitfq.fqd.ics_cred = cpu_to_be16(dpaa_fq->ics_cred);\n"
     "\t\t}\n"),
    (ETH_C, "mtu change update hook",
     "\terr = dpaa_apply_max_frame(priv, new_mtu);\n"
     "\tif (err)\n"
     "\t\treturn err;\n"
     "\n"
     "\tWRITE_ONCE(net_dev->mtu, new_mtu);\n"
     "\treturn 0;\n",
     "\terr = dpaa_apply_max_frame(priv, new_mtu);\n"
     "\tif (err)\n"
     "\t\treturn err;\n"
     "\n"
     "\tWRITE_ONCE(net_dev->mtu, new_mtu);\n"
     "\tdpaa_offload_cgr_update(priv, SPEED_UNKNOWN, new_mtu);\n"
     "\treturn 0;\n"),
    (ETH_C, "probe offload CGR init",
     "\tpriv->tx_headroom = dpaa_get_headroom(priv->buf_layout, TX);\n"
     "\tpriv->rx_headroom = dpaa_get_headroom(priv->buf_layout, RX);\n",
     "\tpriv->tx_headroom = dpaa_get_headroom(priv->buf_layout, TX);\n"
     "\tpriv->rx_headroom = dpaa_get_headroom(priv->buf_layout, RX);\n"
     "\n"
     "\tmutex_init(&priv->offload_cgr_lock);\n"
     "\terr = dpaa_offload_cgr_init(priv);\n"
     "\tif (err < 0) {\n"
     "\t\tdev_err(dev, \"Error initializing offload CGR\\n\");\n"
     "\t\tgoto free_dpaa_fqs;\n"
     "\t}\n"),
    (ETH_C, "probe unwind release",
     "free_dpaa_fqs:\n"
     "\tdpaa_fq_free(dev, &priv->dpaa_fq_list);\n"
     "\tdpaa_cgr_release(dev, &priv->ingress_cgr);\n",
     "free_dpaa_fqs:\n"
     "\tdpaa_fq_free(dev, &priv->dpaa_fq_list);\n"
     "\tdpaa_cgr_release(dev, &priv->offload_cgr);\n"
     "\tdpaa_cgr_release(dev, &priv->ingress_cgr);\n"),
    (ETH_C, "remove release",
     "\terr = dpaa_fq_free(dev, &priv->dpaa_fq_list);\n"
     "\tif (err)\n"
     "\t\tdev_err(dev, \"Failed to free FQs on remove (%pE)\\n\",\n"
     "\t\t\tERR_PTR(err));\n"
     "\n"
     "\tdpaa_cgr_release(dev, &priv->ingress_cgr);\n",
     "\terr = dpaa_fq_free(dev, &priv->dpaa_fq_list);\n"
     "\tif (err)\n"
     "\t\tdev_err(dev, \"Failed to free FQs on remove (%pE)\\n\",\n"
     "\t\t\tERR_PTR(err));\n"
     "\n"
     "\tdpaa_cgr_release(dev, &priv->offload_cgr);\n"
     "\tdpaa_cgr_release(dev, &priv->ingress_cgr);\n"),
    (ETH_C, "allocator signature",
     "int dpaa_alloc_offload_tx_fq(struct net_device *dev, u32 *fqid)\n{\n",
     "int dpaa_alloc_offload_tx_fq_ics(struct net_device *dev, u16 ics_cred,\n"
     "\t\t\t\t u32 *fqid)\n{\n"),
    (ETH_C, "ics_cred validation",
     "\tif (!dev || !fqid)\n\t\treturn -EINVAL;\n\n\t/* Serialize priv->dpaa_fq_list",
     "\t/* F-316: QMan FQD ics_cred is a 15-bit field (0..0x7fff) */\n"
     "\tif (!dev || !fqid || ics_cred > 0x7fff)\n\t\treturn -EINVAL;\n\n"
     "\t/* Serialize priv->dpaa_fq_list"),
    (ETH_C, "fail-closed offload_cgr check",
     "\tpriv = netdev_priv(dev);\n\n\tdpaa_fq = devm_kzalloc(",
     "\tpriv = netdev_priv(dev);\n"
     "\tif (!priv->offload_cgr) {\n"
     "\t\tret = -ENODEV;\n"
     "\t\tgoto out_unlock;\n"
     "\t}\n\n"
     "\tdpaa_fq = devm_kzalloc("),
    (ETH_C, "allocator ics_cred",
     "\tdpaa_fq->fq_type = FQ_TYPE_TX_NO_CONFIRM;\n",
     "\tdpaa_fq->fq_type = FQ_TYPE_TX_NO_CONFIRM;\n"
     "\tdpaa_fq->ics_cred = ics_cred;\t/* F-316 */\n"),
    (ETH_C, "allocator comment",
     "\t/* A6-LOSS step 7 (2026-10-05): td_enable=true, not false. CGE/cgid\n"
     "\t * membership in priv->cgr_data.ctx->cgr.cgrid was live-proven (cgr_probe)\n"
     "\t * to never report congested for this FQ's FE-hardware enqueues even\n"
     "\t * with a multi-GB backlog; the FQD-local tail-drop threshold this\n"
     "\t * enables instead is evaluated directly against the same per-FQD\n"
     "\t * frm_cnt/byte_cnt counters fq_probe already proved DO update\n"
     "\t * correctly for this FQ. */\n",
     "\t/* F-316: dpaa_fq_init() ignores td_enable for FQ_TYPE_TX_NO_CONFIRM and\n"
     "\t * attaches the FQ to priv->offload_cgr (shared frame-mode CGR, CSTD\n"
     "\t * enabled, PREFERINCACHE preserved, no per-FQ TDE or byte-mode OAC). */\n"),
    (ETH_C, "allocator log",
     "\tnetdev_info(dev, \"F-199: offload no-confirm TX FQ 0x%x on channel %u\\n\",\n"
     "\t\t    dpaa_fq->fqid, dpaa_fq->channel);\n",
     "\tnetdev_info(dev, \"F-199: offload no-confirm TX FQ 0x%x on channel %u (cgrid %u, ics 0x%x)\\n\",\n"
     "\t\t    dpaa_fq->fqid, dpaa_fq->channel,\n"
     "\t\t    priv->offload_cgr->cgr.cgrid, dpaa_fq->ics_cred);\n"),
    (ETH_C, "wrapper",
     "EXPORT_SYMBOL_GPL(dpaa_alloc_offload_tx_fq);\n",
     "EXPORT_SYMBOL_GPL(dpaa_alloc_offload_tx_fq_ics);\n"
     "\n"
     "/* F-316: single per-port FQ on priv->offload_cgr with default DRR credit. */\n"
     "int dpaa_alloc_offload_tx_fq(struct net_device *dev, u32 *fqid)\n"
     "{\n"
     "\treturn dpaa_alloc_offload_tx_fq_ics(dev, 0, fqid);\n"
     "}\n"
     "EXPORT_SYMBOL_GPL(dpaa_alloc_offload_tx_fq);\n"),
    (OFF_H, "declaration",
     "int dpaa_alloc_offload_tx_fq(struct net_device *dev, u32 *fqid);\n#else\n",
     "int dpaa_alloc_offload_tx_fq(struct net_device *dev, u32 *fqid);\n"
     "/* F-316: same, with an optional intra-WQ DRR credit (0..0x7fff). */\n"
     "int dpaa_alloc_offload_tx_fq_ics(struct net_device *dev, u16 ics_cred,\n"
     "\t\t\t\t u32 *fqid);\n"
     "#else\n"
     "static inline int dpaa_alloc_offload_tx_fq_ics(struct net_device *dev,\n"
     "\t\t\t\t\t       u16 ics_cred, u32 *fqid)\n"
     "{\n"
     "\treturn -ENODEV;\n"
     "}\n"),
)


def main():
    try:
        files = {p: p.read_text() for p in (ETH_H, ETH_C, OFF_H)}
        if all(MARK in files[p] for p in files):
            has_f320 = "F-320: release non-devm parent allocations" in files[ETH_C]
            for path, label, _, new in EDITS:
                if has_f320 and label in ("probe unwind release", "remove release"):
                    if files[ETH_C].count("dpaa_cgr_release(dev, &priv->offload_cgr);") != 2:
                        raise ValueError(f"partially applied: {label}")
                    continue
                if files[path].count(new) != 1:
                    raise ValueError(f"partially applied: {label}")
            print("### F-316: already applied")
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
        print(f"### F-316: FATAL: {error}", file=sys.stderr)
        return 1
    print(f"### F-316: shared frame-mode offload CGR applied ({len(EDITS)} blocks)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
