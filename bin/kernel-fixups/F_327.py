"""F-327 (G10 Phase 1): CEETM + ASK2 coexistence and QMan drain hooks.

Adds the kernel integration hooks needed by qman_ceetm.c and dpaa_ceetm.c
(G10 Phase 1):
  1. drivers/soc/fsl/qbman/qman_priv.h: mark qm_mcc_ceetm_cq_peek_pop_xsfdrread
     cqid/xsfdr big-endian (__be16) to match QMan wire order on ARM64.
  2. drivers/soc/fsl/qbman/qman.c:
       - fix F_102 NULL-fq MR branches (FQPN + SW-ERN) so an unexpected NULL fq
         advances qm_mr_next() instead of spinning forever under preempt_disable().
       - add and export qman_ceetm_drain_ern() to drain affine portal EQCRs and
         flush pending MR workqueues across online CPUs before destroying a
         CEETM LFQ's fq_table[] entry.
  3. include/soc/fsl/qman.h: declare qman_ceetm_cq_pop() and
     qman_ceetm_drain_ern().
  4. drivers/net/ethernet/freescale/dpaa/dpaa_eth.c:
       - add dpaa_fo_notify_egress_change() using F-326's sleepable
         dpaa_fo_srcu domain to synchronously notify ask.ko before/after CEETM
         sub-portal or leaf transitions.
       - de-static dpaa_fd_release() so dpaa_ceetm.c can release both
         contiguous and scatter-gather BMan FDs popped during CQ drain.
       - call dpaa_ceetm_update_mtu(priv, new_mtu) in dpaa_change_mtu() so
         frame-mode CEETM CCGR budgets track MTU changes alongside F-316's CGR.

Must run after F-102, F-316, and F-326. Idempotent; count-gated anchors.
"""

import sys

QMAN_PRIV_H = "drivers/soc/fsl/qbman/qman_priv.h"
QMAN_C = "drivers/soc/fsl/qbman/qman.c"
QMAN_H = "include/soc/fsl/qman.h"
DPAA_ETH_C = "drivers/net/ethernet/freescale/dpaa/dpaa_eth.c"
MARK = "F-327"

QMAN_PRIV_H_EDITS = [
    (
        "wire big-endian cqid/xsfdr in cq_peek_pop_xsfdrread",
        "struct qm_mcc_ceetm_cq_peek_pop_xsfdrread {\n"
        "\tu8 __reserved1;\n"
        "\t__le16 cqid;\t\t/* native order, see qman_ceetm.c */\n"
        "\tu8 dcpid;\n"
        "\tu8 ct;\n"
        "\t__le16 xsfdr;\n"
        "\tu8 __reserved2[56];\n"
        "} __packed;\n",
        "/* F-327: QMan CEETM management commands are big-endian on the wire. */\n"
        "struct qm_mcc_ceetm_cq_peek_pop_xsfdrread {\n"
        "\tu8 __reserved1;\n"
        "\t__be16 cqid;\n"
        "\tu8 dcpid;\n"
        "\tu8 ct;\n"
        "\t__be16 xsfdr;\n"
        "\tu8 __reserved2[56];\n"
        "} __packed;\n",
    ),
]

QMAN_C_EDITS = [
    (
        "fix NULL fq MR infinite loop on FQPN",
        "\t\t\t\t/* F_102 v3: ZC datapath can produce entries with invalid context_b */\n"
        "\t\t\t\tif (unlikely(!fq)) {\n"
        "\t\t\t\t\tpr_err_once(\"qman: NULL fq from MR context_b=0x%x, skipping\\n\",\n"
        "\t\t\t\t\t\t   be32_to_cpu(msg->fq.context_b));\n"
        "\t\t\t\t\tcontinue;\n"
        "\t\t\t\t}\n",
        "\t\t\t\t/* F-327: break (not continue) so num++ & qm_mr_next() advance. */\n"
        "\t\t\t\tif (unlikely(!fq)) {\n"
        "\t\t\t\t\tpr_err_once(\"qman: NULL fq from MR context_b=0x%x, skipping\\n\",\n"
        "\t\t\t\t\t\t   be32_to_cpu(msg->fq.context_b));\n"
        "\t\t\t\t\tbreak;\n"
        "\t\t\t\t}\n",
    ),
    (
        "fix NULL fq MR infinite loop on SW ERN",
        "\t\t\t/* F_102 v2: ZC datapath can produce entries with invalid context_b */\n"
        "\t\t\tif (unlikely(!fq)) {\n"
        "\t\t\t\tpr_err_once(\"qman: NULL fq from ERN tag=0x%x, skipping\\n\",\n"
        "\t\t\t\t\t   be32_to_cpu(msg->ern.tag));\n"
        "\t\t\t\tcontinue;\n"
        "\t\t\t}\n"
        "\t\t\tfq->cb.ern(p, fq, msg);\n",
        "\t\t\t/* F-327: advance MR entry on NULL fq instead of spinning under preempt_disable. */\n"
        "\t\t\tif (unlikely(!fq)) {\n"
        "\t\t\t\tpr_err_once(\"qman: NULL fq from ERN tag=0x%x, skipping\\n\",\n"
        "\t\t\t\t\t   be32_to_cpu(msg->ern.tag));\n"
        "\t\t\t} else {\n"
        "\t\t\t\tfq->cb.ern(p, fq, msg);\n"
        "\t\t\t}\n",
    ),
    (
        "add and export qman_ceetm_drain_ern",
        "\tgen_pool_free(qm_cgralloc, cgrid | DPAA_GENALLOC_OFF, 1);\n"
        "\treturn 0;\n"
        "}\n"
        "EXPORT_SYMBOL(qman_release_cgrid);\n",
        "\tgen_pool_free(qm_cgralloc, cgrid | DPAA_GENALLOC_OFF, 1);\n"
        "\treturn 0;\n"
        "}\n"
        "EXPORT_SYMBOL(qman_release_cgrid);\n"
        "\n"
        "/* F-327: wait for portal EQCRs to settle and drain pending MR/SW-ERNs\n"
        " * across all online affine portals before destroying a CEETM fq_table entry. */\n"
        "static void qman_ceetm_check_eqcr_cpu(void *arg)\n"
        "{\n"
        "\tbool *empty = arg;\n"
        "\tstruct qman_portal *p = affine_portals[smp_processor_id()];\n"
        "\tunsigned long irqflags;\n"
        "\tu8 ci, pi;\n"
        "\n"
        "\tif (!p) {\n"
        "\t\t*empty = true;\n"
        "\t\treturn;\n"
        "\t}\n"
        "\tlocal_irq_save(irqflags);\n"
        "\tci = qm_in(&p->p, QM_REG_EQCR_CI_CINH) & (QM_EQCR_SIZE - 1);\n"
        "\tpi = eqcr_ptr2idx(p->p.eqcr.cursor);\n"
        "\t*empty = (dpaa_cyc_diff(QM_EQCR_SIZE, ci, pi) == 0);\n"
        "\tlocal_irq_restore(irqflags);\n"
        "}\n"
        "\n"
        "void qman_ceetm_drain_ern(void)\n"
        "{\n"
        "\tunsigned int cpu, pass;\n"
        "\n"
        "\tfor_each_online_cpu(cpu) {\n"
        "\t\tunsigned long deadline = jiffies + msecs_to_jiffies(5);\n"
        "\t\tbool empty = false;\n"
        "\n"
        "\t\tif (!affine_portals[cpu])\n"
        "\t\t\tcontinue;\n"
        "\t\twhile (time_before(jiffies, deadline)) {\n"
        "\t\t\tif (smp_call_function_single(cpu, qman_ceetm_check_eqcr_cpu,\n"
        "\t\t\t\t\t\t     &empty, 1) || empty)\n"
        "\t\t\t\tbreak;\n"
        "\t\t\tusleep_range(50, 100);\n"
        "\t\t}\n"
        "\t}\n"
        "\t/* Two MR drain passes catch ERNs posted just as the last EQCR completed. */\n"
        "\tfor (pass = 0; pass < 2; pass++) {\n"
        "\t\tfor_each_online_cpu(cpu) {\n"
        "\t\t\tstruct qman_portal *p = affine_portals[cpu];\n"
        "\n"
        "\t\t\tif (p && qm_portal_wq)\n"
        "\t\t\t\tqueue_work_on(cpu, qm_portal_wq, &p->mr_work);\n"
        "\t\t}\n"
        "\t\tif (qm_portal_wq)\n"
        "\t\t\tflush_workqueue(qm_portal_wq);\n"
        "\t\tif (pass == 0)\n"
        "\t\t\tusleep_range(50, 100);\n"
        "\t}\n"
        "}\n"
        "EXPORT_SYMBOL_GPL(qman_ceetm_drain_ern);\n",
    ),
]

QMAN_H_EDITS = [
    (
        "declare qman_ceetm_cq_pop and qman_ceetm_drain_ern",
        "int qman_ceetm_drain_cq(struct qm_ceetm_cq *cq);\n",
        "/* F-327: pop one descriptor from a CEETM CQ and drain pending SW ERNs. */\n"
        "int qman_ceetm_cq_pop(struct qm_ceetm_cq *cq, struct qm_fd *fd);\n"
        "void qman_ceetm_drain_ern(void);\n"
        "int qman_ceetm_drain_cq(struct qm_ceetm_cq *cq);\n",
    ),
]

DPAA_ETH_EDITS = [
    (
        "add dpaa_fo_notify_egress_change using dpaa_fo_srcu",
        "\terr = ops->setup_tc_block(net_dev, f);\n"
        "\tsrcu_read_unlock(&dpaa_fo_srcu, idx);\n"
        "\n"
        "\treturn err;\n"
        "}\n",
        "\terr = ops->setup_tc_block(net_dev, f);\n"
        "\tsrcu_read_unlock(&dpaa_fo_srcu, idx);\n"
        "\n"
        "\treturn err;\n"
        "}\n"
        "\n"
        "/* F-327: sleepable CEETM egress-change callout under RTNL + dpaa_fo_srcu. */\n"
        "int dpaa_fo_notify_egress_change(struct net_device *net_dev,\n"
        "\t\t\t\t enum dpaa_egress_event ev, u32 fqid)\n"
        "{\n"
        "\tconst struct dpaa_flow_offload_ops *ops;\n"
        "\tint idx, err = 0;\n"
        "\n"
        "\tidx = srcu_read_lock(&dpaa_fo_srcu);\n"
        "\tops = srcu_dereference(dpaa_fo_ops_slot, &dpaa_fo_srcu);\n"
        "\tif (ops && ops->egress_change)\n"
        "\t\terr = ops->egress_change(net_dev, ev, fqid);\n"
        "\tsrcu_read_unlock(&dpaa_fo_srcu, idx);\n"
        "\treturn err;\n"
        "}\n",
    ),
    (
        "de-static dpaa_fd_release for CEETM CQ drain",
        "static void dpaa_fd_release(const struct net_device *net_dev,\n"
        "\t\t\t    const struct qm_fd *fd)\n",
        "/* F-327: shared with dpaa_ceetm.c for CQ drain FD reclamation. */\n"
        "void dpaa_fd_release(const struct net_device *net_dev,\n"
        "\t\t     const struct qm_fd *fd)\n",
    ),
    (
        "update CEETM frame-mode CCGR budget on MTU change",
        "\tWRITE_ONCE(net_dev->mtu, new_mtu);\n"
        "\tdpaa_offload_cgr_update(priv, SPEED_UNKNOWN, new_mtu);\n"
        "\treturn 0;\n",
        "\tWRITE_ONCE(net_dev->mtu, new_mtu);\n"
        "\tdpaa_offload_cgr_update(priv, SPEED_UNKNOWN, new_mtu);\n"
        "\tdpaa_ceetm_update_mtu(priv, new_mtu);\n"
        "\treturn 0;\n",
    ),
]


def apply_one(path: str, edits: list[tuple[str, str, str]]) -> None:
    try:
        with open(path) as f:
            s = f.read()
    except FileNotFoundError:
        print(f"### {MARK}: FATAL: {path} not found")
        sys.exit(1)

    if MARK in s:
        for name, _old, new in edits:
            if new not in s:
                print(f"### {MARK}: FATAL: marker present in {path} but '{name}' missing")
                sys.exit(1)
        print(f"### {MARK}: {path} already applied")
        return

    for name, old, _new in edits:
        n = s.count(old)
        if n != 1:
            print(f"### {MARK}: FATAL: {path} anchor '{name}' found {n} times (expected 1)")
            sys.exit(1)

    for _name, old, new in edits:
        s = s.replace(old, new, 1)

    with open(path, "w") as f:
        f.write(s)
    print(f"### {MARK}: patched {path} ({len(edits)} anchor(s))")


apply_one(QMAN_PRIV_H, QMAN_PRIV_H_EDITS)
apply_one(QMAN_C, QMAN_C_EDITS)
apply_one(QMAN_H, QMAN_H_EDITS)
apply_one(DPAA_ETH_C, DPAA_ETH_EDITS)
