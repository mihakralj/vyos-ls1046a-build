"""F-199 (T-M7-2 S4): per-egress no-confirm TX FQ for the FE hardware terminal.

S1 (F-198) forwards an ehash HIT direct-to-wire but targets the netdev's
shared queue-0 TX FQ (FQ_TYPE_TX), which has context_a 0x9a00000080000000 with
B0V=1 -> FMan enqueues a TX-confirmation FD for every frame -> per-CPU NAPI
skb-free work with no skb -> the ~2.2 Gbps TCP ceiling measured 2026-08-15.

This ports the corrected PR14z23 Option C to the 6.18.44 canonical tree:
  * new enum FQ_TYPE_TX_NO_CONFIRM,
  * new exported dpaa_alloc_offload_tx_fq(dev, *fqid) that allocates a
    dynamic-FQID dpaa_fq of the new type on the netdev's FMan TX direct-
    connect channel via dpaa_setup_egress(), with the normal ERN callback and
    TO_DCPORTAL flag, then splices into priv->dpaa_fq_list (so driver
    teardown frees it), and runs dpaa_fq_init(),
  * a dpaa_fq_init() branch programming context_a = 0x1c00000080000000
    (superseded by the silicon-validated NXP SDK FQ_FLAG_NO_TXCONFIRM value
    0x9a000000c0000000) so FMan emits NO TX-confirm FD while preserving the
    vendor-proven offload TX semantics, and NO confq association,
  * header declaration + non-FSL stub in dpaa_flow_offload.h.

The kernel's own TX path is untouched: priv->egress_fqs[] stay FQ_TYPE_TX
with confirmation, so dpaa_xmit/ARP/conntrack-promote continue to free skbs
normally. Only ask.ko's offloaded HIT path uses the no-confirm FQ.

A6-LOSS step 7 (2026-10-05): dpaa_alloc_offload_tx_fq() now calls
dpaa_fq_init(dpaa_fq, td_enable=True) instead of False. Live hardware
testing (cgr_probe, this session) proved the FQ's CGR membership
(QM_FQCTRL_CGE, cgid = priv->cgr_data.cgr.cgrid, 256 MiB CS threshold) never
reports congested=1 for this FQ's enqueues even when fq_probe shows the FQD's
own frm_cnt/byte_cnt backlogged to ~4.27 GB (17x over threshold) -- ASK2's FE
hardware ENQUEUE_PKT path updates the FQD's own counters but does not feed
the separate CG integrator the CGE path depends on. dpaa_fq_init()'s dormant
td_enable branch (never exercised by any caller in this tree before this
change) instead programs QM_FQCTRL_TDE + a FQD-local byte-count tail-drop
threshold (DPAA_FQ_TD, 4 MiB) that QMan evaluates directly against the same
per-FQD counters fq_probe already proved ARE updated correctly for this FQ --
independent of the CG integrator. Per arch/qman-ceetm.md's FQD field table,
CGE (bit 288) and TDE (bit 289) are independent bits, but this driver's
existing td_enable branch overwrites (not ORs) fq_ctrl, so enabling TDE here
also drops this FQ's CGE/cgid membership -- an acceptable trade since that
membership was confirmed non-functional for this producer. Kernel's own
confirm-enabled TX FQs (td_enable always False) are unaffected.

A6-LOSS step 7 fix-up (2026-10-05, same day): the first cut of this change
boot-faulted on board .185 (image 2026.10.05-2149) -- "qman_init_fq(698) =
-22" for fqid 0x2ba/0x2bb (698/699 decimal) on every boot. Root cause:
qman_init_fq() hard-rejects we_mask with BOTH QM_INITFQ_WE_OAC and
QM_INITFQ_WE_TDTHRESH set ("can't be set at the same time as TDTHRESH" in
qman.c). The CGE/OAC branch above unconditionally sets WE_OAC for
FQ_TYPE_TX_NO_CONFIRM regardless of td_enable, so with td_enable=true both
WE_OAC and WE_TDTHRESH ended up set together. Fix: that branch's condition
now reads `(dpaa_fq->fq_type == FQ_TYPE_TX_NO_CONFIRM && !td_enable)`, so
this FQ enters either the CGE/OAC branch or the TDE branch, never both. No
other caller passes td_enable=true, so no other FQ type is affected.

Count-gated, idempotent (marker "F-199"); hard-fail on any source drift.
"""

import sys

ETH_H = "drivers/net/ethernet/freescale/dpaa/dpaa_eth.h"
ETH_C = "drivers/net/ethernet/freescale/dpaa/dpaa_eth.c"
OFF_H = "include/linux/fsl/dpaa_flow_offload.h"
changes = 0


def replace(path, name, old, new):
    global changes
    with open(path) as f:
        src = f.read()
    n = src.count(old)
    if n != 1:
        print(f"### F-199: FATAL: '{name}' expected 1 match in {path}, got {n}")
        sys.exit(1)
    with open(path, "w") as f:
        f.write(src.replace(old, new, 1))
    changes += 1
    print(f"### F-199 {name} applied ({path})")


with open(ETH_C) as f:
    if "F-199" in f.read():
        print("### F-199 already applied")
        sys.exit(0)

# 1. New FQ type in the enum (appended last, preserving existing values).
replace(
    ETH_H, "FQ_TYPE_TX_NO_CONFIRM enum",
    "\tFQ_TYPE_TX_ERROR,	/* Tx Error FQs (these are actually Rx FQs) */\n"
    "};",
    "\tFQ_TYPE_TX_ERROR,	/* Tx Error FQs (these are actually Rx FQs) */\n"
    "\tFQ_TYPE_TX_NO_CONFIRM,	/* F-199: offload TX FQ, no TX-confirm FD */\n"
    "};",
)

# 2. dpaa_fq_init(): CGR membership must include the new type so its footprint
#    is congestion-accounted like the other egress FQs.
replace(
    ETH_C, "CGR membership for no-confirm FQ",
    "\t\tif (dpaa_fq->fq_type == FQ_TYPE_TX ||\n"
    "\t\t    dpaa_fq->fq_type == FQ_TYPE_TX_CONFIRM ||\n"
    "\t\t    dpaa_fq->fq_type == FQ_TYPE_TX_CONF_MQ) {\n"
    "\t\t\tinitfq.we_mask |= cpu_to_be16(QM_INITFQ_WE_CGID);",
    "\t\tif (dpaa_fq->fq_type == FQ_TYPE_TX ||\n"
    "\t\t    dpaa_fq->fq_type == FQ_TYPE_TX_CONFIRM ||\n"
    "\t\t    dpaa_fq->fq_type == FQ_TYPE_TX_CONF_MQ ||\n"
    "\t\t    dpaa_fq->fq_type == FQ_TYPE_TX_NO_CONFIRM) {\n"
    "\t\t\tinitfq.we_mask |= cpu_to_be16(QM_INITFQ_WE_CGID);",
)

# 2b. A6-LOSS step 7 (2026-10-05): qman_init_fq() hard-rejects we_mask with
#     BOTH QM_INITFQ_WE_OAC and QM_INITFQ_WE_TDTHRESH set (qman.c: "can't be
#     set at the same time as TDTHRESH" -> -EINVAL). Block 2 above
#     unconditionally enters the CGE/OAC branch for FQ_TYPE_TX_NO_CONFIRM,
#     which sets WE_OAC; the td_enable branch (block 4's dpaa_fq_init(...,
#     true) call) sets WE_TDTHRESH -- together these hard-faulted
#     qman_init_fq() for fqid 0x2ba/0x2bb at every boot ("qman_init_fq(698)
#     = -22"), confirmed on board .185 image 2026.10.05-2149. Exclude this
#     FQ from the CGE/OAC branch whenever td_enable is set, since td_enable
#     is the signal this FQ wants FQD-local tail-drop instead of CGR
#     membership for the same budget. No other caller in this tree ever
#     passes td_enable=true, so this is a no-op for every other FQ type.
replace(
    ETH_C, "no-confirm FQ skips CGE/OAC when td_enable",
    "\t\t    dpaa_fq->fq_type == FQ_TYPE_TX_NO_CONFIRM) {\n"
    "\t\t\tinitfq.we_mask |= cpu_to_be16(QM_INITFQ_WE_CGID);",
    "\t\t    (dpaa_fq->fq_type == FQ_TYPE_TX_NO_CONFIRM && !td_enable)) {\n"
    "\t\t\tinitfq.we_mask |= cpu_to_be16(QM_INITFQ_WE_CGID);",
)

# 3. dpaa_fq_init(): program no-confirm context_a for the new type. Placed
#    right after the existing FQ_TYPE_TX confq block so both are adjacent.
replace(
    ETH_C, "no-confirm context_a",
    "\t\t\t\tqm_fqd_context_a_set64(&initfq.fqd,\n"
    "\t\t\t\t\t\t       0x9a00000080000000ULL);\n"
    "\t\t\t}\n"
    "\t\t}\n",
    "\t\t\t\tqm_fqd_context_a_set64(&initfq.fqd,\n"
    "\t\t\t\t\t\t       0x9a00000080000000ULL);\n"
    "\t\t\t}\n"
    "\t\t}\n"
    "\n"
    "\t\t/* F-199 (T-M7-2 S4): offload TX FQ with NO TX confirmation.\n"
    "\t\t * context_a = vendor-proven no-TX-confirm value 0x9a000000c0000000\n"
    "\t\t * (NXP SDK sdk_dpaa FQ_FLAG_NO_TXCONFIRM; devman.c hi=0x9a000000\n"
    "\t\t * lo=0xc0000000). FMan deallocates the buffer in hardware and\n"
    "\t\t * enqueues NO TX-confirm FD, so no per-frame NAPI skb-free runs.\n"
    "\t\t * The kernel's own TX FQs (FQ_TYPE_TX, 0x1e00000080000000) keep\n"
    "\t\t * B0V=1 confirmation for skb reclaim.\n"
    "\t\t * SILICON-VALIDATED 2026-08-24 (board .185, image 0500, 7.76 Gbit/s\n"
    "\t\t * bidirectional HIT, 13.6 GB / ~9.7M frames): 'tx confirm [TOTAL]'\n"
    "\t\t * advanced only +9/+5 (control-plane) and F-227 dropped-confirm\n"
    "\t\t * count stayed 0, board idle. F-232 (now retired) first DISPROVED\n"
    "\t\t * the earlier guess 0x1c00000080000000 (mainline value with B0V\n"
    "\t\t * cleared): the record correctly targeted this FQ\n"
    "\t\t * (hit_fqid=0x2ba/0x2bb) yet F-227 still dropped ~600K confirms/s,\n"
    "\t\t * proving clearing B0V alone does NOT suppress the confirm FD on\n"
    "\t\t * 210.10.1; the full vendor value 0x9a000000c0000000 does. */\n"
    "\t\tif (dpaa_fq->fq_type == FQ_TYPE_TX_NO_CONFIRM) {\n"
    "\t\t\tinitfq.we_mask |= cpu_to_be16(QM_INITFQ_WE_CONTEXTA);\n"
    "\t\t\tqm_fqd_context_a_set64(&initfq.fqd,\n"
    "\t\t\t\t\t       0x9a000000c0000000ULL);\n"
    "\t\t}\n",
)

# 4. Exported allocator, placed immediately AFTER dpaa_get_tx_fqid() (which is
#    after dpaa_fq_init, satisfying C ordering) and before dpaa_load().
replace(
    ETH_C, "dpaa_alloc_offload_tx_fq helper",
    "EXPORT_SYMBOL_GPL(dpaa_get_tx_fqid);\n"
    "\n"
    "static int __init dpaa_load(void)\n",
    "EXPORT_SYMBOL_GPL(dpaa_get_tx_fqid);\n"
    "\n"
    "/* F-199 (T-M7-2 S4): allocate a dedicated no-confirm TX FQ on @dev's QMan\n"
    " * channel for the ASK2 FE hardware offload terminal. The FQ is spliced\n"
    " * into priv->dpaa_fq_list so it is torn down with the netdev. The nft\n"
    " * flow-offload REPLACE callback runs on a workqueue WITHOUT RTNL (S4\n"
    " * board proof, 2026-08-16), so this helper acquires RTNL internally to\n"
    " * serialize list mutation against netdev teardown. Returns 0 and the\n"
    " * dynamic FQID in *fqid, or a negative errno. */\n"
    "int dpaa_alloc_offload_tx_fq(struct net_device *dev, u32 *fqid)\n"
    "{\n"
    "\tstruct dpaa_priv *priv;\n"
    "\tstruct dpaa_fq *dpaa_fq;\n"
    "\tint ret;\n"
    "\n"
    "\tif (!dev || !fqid)\n"
    "\t\treturn -EINVAL;\n"
    "\n"
    "\t/* Serialize priv->dpaa_fq_list mutation + FQ init against netdev\n"
    "\t * teardown. The caller (nft flow-offload workqueue) does NOT hold\n"
    "\t * RTNL, so take it here; no ASK caller holds it, so no deadlock. */\n"
    "\trtnl_lock();\n"
    "\n"
    "\tif (dev->netdev_ops != &dpaa_ops) {\n"
    "\t\tret = -ENODEV;\n"
    "\t\tgoto out_unlock;\n"
    "\t}\n"
    "\tpriv = netdev_priv(dev);\n"
    "\n"
    "\tdpaa_fq = devm_kzalloc(dev->dev.parent, sizeof(*dpaa_fq), GFP_KERNEL);\n"
    "\tif (!dpaa_fq) {\n"
    "\t\tret = -ENOMEM;\n"
    "\t\tgoto out_unlock;\n"
    "\t}\n"
    "\n"
    "\tdpaa_fq->fq_type = FQ_TYPE_TX_NO_CONFIRM;\n"
    "\tdpaa_fq->fqid = 0;\t\t/* dynamic */\n"
    "\t/* Reuse the driver's egress setup so the FQ lands on the FMan TX\n"
    "\t * DC-portal channel with the ERN callback and TO_DCPORTAL flag,\n"
    "\t * exactly like FQ_TYPE_TX; dpaa_fq_init() then applies the\n"
    "\t * no-confirm context_a for FQ_TYPE_TX_NO_CONFIRM. */\n"
    "\tdpaa_setup_egress(priv, dpaa_fq, priv->mac_dev->port[TX],\n"
    "\t\t\t  &dpaa_fq_cbs.egress_ern);\n"
    "\tlist_add_tail(&dpaa_fq->list, &priv->dpaa_fq_list);\n"
    "\n"
    "\t/* A6-LOSS step 7 (2026-10-05): td_enable=true, not false. CGE/cgid\n"
    "\t * membership in priv->cgr_data.cgr.cgrid was live-proven (cgr_probe)\n"
    "\t * to never report congested for this FQ's FE-hardware enqueues even\n"
    "\t * with a multi-GB backlog; the FQD-local tail-drop threshold this\n"
    "\t * enables instead is evaluated directly against the same per-FQD\n"
    "\t * frm_cnt/byte_cnt counters fq_probe already proved DO update\n"
    "\t * correctly for this FQ. */\n"
    "\tret = dpaa_fq_init(dpaa_fq, true);\n"
    "\tif (ret < 0) {\n"
    "\t\tlist_del(&dpaa_fq->list);\n"
    "\t\tdevm_kfree(dev->dev.parent, dpaa_fq);\n"
    "\t\tgoto out_unlock;\n"
    "\t}\n"
    "\n"
    "\t*fqid = dpaa_fq->fqid;\n"
    "\tnetdev_info(dev, \"F-199: offload no-confirm TX FQ 0x%x on channel %u\\n\",\n"
    "\t\t    dpaa_fq->fqid, dpaa_fq->channel);\n"
    "\tret = 0;\n"
    "out_unlock:\n"
    "\trtnl_unlock();\n"
    "\treturn ret;\n"
    "}\n"
    "EXPORT_SYMBOL_GPL(dpaa_alloc_offload_tx_fq);\n"
    "\n"
    "static int __init dpaa_load(void)\n",
)

# 5. Public declaration + non-FSL stub.
replace(
    OFF_H, "dpaa_alloc_offload_tx_fq decl",
    "#if IS_ENABLED(CONFIG_FSL_DPAA)\n"
    "int dpaa_register_flow_offload_handler(const struct dpaa_flow_offload_ops *ops);\n"
    "int dpaa_unregister_flow_offload_handler(const struct dpaa_flow_offload_ops *ops);\n"
    "#else\n",
    "#if IS_ENABLED(CONFIG_FSL_DPAA)\n"
    "int dpaa_register_flow_offload_handler(const struct dpaa_flow_offload_ops *ops);\n"
    "int dpaa_unregister_flow_offload_handler(const struct dpaa_flow_offload_ops *ops);\n"
    "/* F-199 (T-M7-2 S4): allocate a no-confirm TX FQ for the offload terminal. */\n"
    "int dpaa_alloc_offload_tx_fq(struct net_device *dev, u32 *fqid);\n"
    "#else\n"
    "static inline int dpaa_alloc_offload_tx_fq(struct net_device *dev, u32 *fqid)\n"
    "{\n"
    "\treturn -ENODEV;\n"
    "}\n",
)

print(f"### dpaa_eth + dpaa_flow_offload.h: F-199 complete ({changes} blocks)")
