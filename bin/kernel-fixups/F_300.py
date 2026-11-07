#!/usr/bin/env python3
"""Quarantine DPAA CGR callback owners when QMan deletion is unsettled."""

from pathlib import Path
import re
import sys


QMAN = Path("drivers/soc/fsl/qbman/qman.c")
QMAN_H = Path("include/soc/fsl/qman.h")
DPAA = Path("drivers/net/ethernet/freescale/dpaa/dpaa_eth.c")
DPAA_H = Path("drivers/net/ethernet/freescale/dpaa/dpaa_eth.h")
ETHTOOL = Path("drivers/net/ethernet/freescale/dpaa/dpaa_ethtool.c")
MARKER = "F-300: keep unsettled CGR owners alive"

OLD_DELETE = """\
static void qman_delete_cgr_smp_call(void *p)
{
\tqman_delete_cgr((struct qman_cgr *)p);
}

void qman_delete_cgr_safe(struct qman_cgr *cgr)
{
\tpreempt_disable();
\tif (cgr->portal_cpu != smp_processor_id()) {
\t\tsmp_call_function_single(cgr->portal_cpu,
\t\t\t\t\t qman_delete_cgr_smp_call, cgr, true);
\t\tpreempt_enable();
\t\treturn;
\t}

\tqman_delete_cgr(cgr);
\tpreempt_enable();
}
"""
NEW_DELETE = """\
void qman_cgr_disable_callback(struct qman_cgr *cgr)
{
\tstruct qman_portal *p = per_cpu_ptr(&qman_affine_portal,
\t\t\t\t\t    cgr->portal_cpu);
\tunsigned long irqflags;

\traw_spin_lock_irqsave(&p->cgr_lock, irqflags);
\tcgr->cb = NULL;
\traw_spin_unlock_irqrestore(&p->cgr_lock, irqflags);
}
EXPORT_SYMBOL(qman_cgr_disable_callback);

struct delete_cgr_params {
\tstruct qman_cgr *cgr;
\tint ret;
};

static void qman_delete_cgr_smp_call(void *p)
{
\tstruct delete_cgr_params *params = p;

\tparams->ret = qman_delete_cgr(params->cgr);
}

int qman_delete_cgr_safe(struct qman_cgr *cgr)
{
\tstruct delete_cgr_params params = { .cgr = cgr, .ret = -EIO };
\tint ret;

\tpreempt_disable();
\tif (cgr->portal_cpu != smp_processor_id()) {
\t\tret = smp_call_function_single(cgr->portal_cpu,
\t\t\t\t\t       qman_delete_cgr_smp_call,
\t\t\t\t\t       &params, true);
\t\tif (!ret)
\t\t\tret = params.ret;
\t} else {
\t\tret = qman_delete_cgr(cgr);
\t}
\tpreempt_enable();
\tif (ret)
\t\tpr_err_ratelimited("qman: CGR %u deletion failed: %d\\n",
\t\t\t\t   cgr->cgrid, ret);
\treturn ret;
}
"""

CONTEXT = """\
struct dpaa_cgr_context {
\tstruct qman_cgr cgr;
\tstruct dpaa_priv *priv;
};

"""

CALLBACK_COMMENT = """\
/* Congestion group state change notification callback.
 * Stops the device's egress queues while they are congested and
 * wakes them upon exiting congested state.
 * Also updates some CGR-related stats.
 */
"""

OLD_CALLBACK = """\
static void dpaa_eth_cgscn(struct qman_portal *qm, struct qman_cgr *cgr,
\t\t\t   int congested)
{
\tstruct dpaa_priv *priv = (struct dpaa_priv *)container_of(cgr,
\t\tstruct dpaa_priv, cgr_data.cgr);

\tif (congested) {
\t\tpriv->cgr_data.congestion_start_jiffies = jiffies;
\t\tnetif_tx_stop_all_queues(priv->net_dev);
\t\tpriv->cgr_data.cgr_congested_count++;
\t} else {
\t\tpriv->cgr_data.congested_jiffies +=
\t\t\t(jiffies - priv->cgr_data.congestion_start_jiffies);
\t\tnetif_tx_wake_all_queues(priv->net_dev);
\t}
}
"""
NEW_CALLBACK = """\
static void dpaa_eth_cgscn(struct qman_portal *qm, struct qman_cgr *cgr,
\t\t\t   int congested)
{
\tstruct dpaa_cgr_context *ctx = container_of(cgr, struct dpaa_cgr_context,
\t\t\t\t\t\t    cgr);
\tstruct dpaa_priv *priv;

\tpriv = ctx->priv;
\tif (priv && congested) {
\t\tpriv->cgr_data.congestion_start_jiffies = jiffies;
\t\tnetif_tx_stop_all_queues(priv->net_dev);
\t\tpriv->cgr_data.cgr_congested_count++;
\t} else if (priv) {
\t\tpriv->cgr_data.congested_jiffies +=
\t\t\t(jiffies - priv->cgr_data.congestion_start_jiffies);
\t\tnetif_tx_wake_all_queues(priv->net_dev);
\t}
}
"""

HELPERS = """\
/* F-300: keep unsettled CGR owners alive, without retaining their netdev. */
static struct dpaa_cgr_context *dpaa_cgr_alloc(struct dpaa_priv *priv)
{
\tstruct dpaa_cgr_context *ctx;

\tctx = kzalloc(sizeof(*ctx), GFP_KERNEL);
\tif (!ctx)
\t\treturn NULL;
\tctx->priv = priv;
\treturn ctx;
}

static void dpaa_cgr_deactivate(struct dpaa_cgr_context *ctx)
{
\tif (!ctx)
\t\treturn;
\tqman_cgr_disable_callback(&ctx->cgr);
\tctx->priv = NULL;
}

static void dpaa_cgr_create_failed(struct device *dev,
\t\t\t\t   struct dpaa_cgr_context **owner, int err)
{
\tstruct dpaa_cgr_context *ctx = *owner;

\tdev_err(dev, "CGR %u creation failed (%d); retaining ID for this kernel; "
\t\t"cold reset required before reuse\\n",
\t\tctx->cgr.cgrid, err);
\tkfree(ctx);
\t*owner = NULL;
}

static void dpaa_cgr_quarantine(struct device *dev,
\t\t\t\tstruct dpaa_cgr_context **owner, int err)
{
\tstruct dpaa_cgr_context *ctx = *owner;

\tdpaa_cgr_deactivate(ctx);
\tdev_err(dev, "CGR %u unsettled (%d); retaining owner and ID for this kernel; "
\t\t"cold reset required before reuse\\n",
\t\tctx->cgr.cgrid, err);
\t*owner = NULL;
}

static void dpaa_cgr_release(struct device *dev,
\t\t\t     struct dpaa_cgr_context **owner)
{
\tstruct dpaa_cgr_context *ctx = *owner;
\tint err;

\tif (!ctx)
\t\treturn;
\tdpaa_cgr_deactivate(ctx);
\terr = qman_delete_cgr_safe(&ctx->cgr);
\tif (err) {
\t\tdpaa_cgr_quarantine(dev, owner, err);
\t\treturn;
\t}
\tdev_warn(dev, "CGR %u removed; retaining ID for this kernel; "
\t\t "cold reset required before reuse\\n", ctx->cgr.cgrid);
\tkfree(ctx);
\t*owner = NULL;
}

"""

LINKED_CREATE = """\
\tlist_add(&cgr->node, &p->cgr_cbs);

\t/* Determine if newly added object requires its callback to be called */
\tret = qman_query_cgr(cgr, &cgr_state);
\tif (ret) {
\t\t/* we can't go back, so proceed and return success */
\t\tdev_err(p->config->dev, "CGR HW state partially modified\\n");
\t\tret = 0;
\t\tgoto out;
\t}
"""


def replace(source, old, new, label, expected=1):
    count = source.count(old)
    if count != expected:
        raise ValueError(f"{label}: found {count} anchors, expected {expected}")
    return source.replace(old, new)


def replace_pattern(source, pattern, replacement, label, expected):
    source, count = re.subn(pattern, replacement, source)
    if count != expected:
        raise ValueError(f"{label}: found {count} anchors, expected {expected}")
    return source


def check_qman_contract(qman):
    create_start = qman.index("int qman_create_cgr(")
    create_end = qman.index("EXPORT_SYMBOL(qman_create_cgr);", create_start)
    create = qman[create_start:create_end]
    congestion_start = qman.index("static void qm_congestion_task(struct work_struct *work)\n{")
    congestion_end = qman.index("\n}\n", congestion_start) + 3
    congestion = qman[congestion_start:congestion_end]
    if (qman.count("static DEFINE_PER_CPU(struct qman_portal, qman_affine_portal);") != 1
            or create.count(LINKED_CREATE) != 1
            or create.count("list_add(&cgr->node, &p->cgr_cbs);") != 1
            or len(re.findall(r"\bcgr->cb\s*\(", qman)) != 2):
        raise ValueError("QMan CGR registration or callback contract changed")
    tail = create[create.index(LINKED_CREATE):]
    if (tail.count("goto out;") != 1
            or len(re.findall(r"(?m)^\s*return\b", tail)) != 1
            or len(re.findall(r"\bret\s*=", tail)) != 2):
        raise ValueError("QMan CGR can fail after callback linkage")
    for label, body in (("create", create), ("congestion", congestion)):
        lock = body.index("raw_spin_lock_irq(&p->cgr_lock);")
        callback = body.index("cgr->cb(", lock)
        unlock = body.index("raw_spin_unlock_irq(&p->cgr_lock);", callback)
        if not lock < callback < unlock:
            raise ValueError(f"{label} callback is not protected by cgr_lock")


def verify(sources):
    qman, header, dpaa, dpaa_header, ethtool = sources
    check_qman_contract(qman)
    return (
        qman.count(NEW_DELETE) == 1
        and "void qman_delete_cgr_safe(" not in qman
        and header.count("int qman_delete_cgr_safe(struct qman_cgr *cgr);") == 1
        and header.count("void qman_cgr_disable_callback(struct qman_cgr *cgr);") == 1
        and dpaa.count(MARKER) == 1
        and dpaa.count(HELPERS) == 1
        and dpaa.count(NEW_CALLBACK) == 1
        and dpaa.count(HELPERS + CALLBACK_COMMENT + NEW_CALLBACK) == 1
        and dpaa_header.count(CONTEXT) == 1
        and dpaa_header.count("struct dpaa_cgr_context *ctx;") == 1
        and dpaa_header.count("struct dpaa_cgr_context *ingress_cgr;") == 1
        and dpaa.count("dpaa_cgr_release(dev, &priv->ingress_cgr);") == 2
        and dpaa.count("dpaa_cgr_release(dev, &priv->cgr_data.ctx);") == 2
        and dpaa.count("dpaa_cgr_deactivate(priv->cgr_data.ctx);") == 1
        and dpaa.count("dpaa_cgr_create_failed(priv->net_dev->dev.parent,") == 2
        and "priv->cgr_data.cgr." not in dpaa
        and "priv->ingress_cgr." not in dpaa
        and "priv->cgr_data.cgr." not in ethtool
    )


def transform(sources):
    qman, header, dpaa, dpaa_header, ethtool = sources
    qman = replace(qman, OLD_DELETE, NEW_DELETE, "checked CGR deletion")
    header = replace(
        header,
        "void qman_delete_cgr_safe(struct qman_cgr *cgr);",
        "int qman_delete_cgr_safe(struct qman_cgr *cgr);",
        "CGR deletion declaration",
    )
    header = replace(
        header,
        " * This will select the proper CPU and run there qman_delete_cgr().\n",
        " * This will select the proper CPU and run there qman_delete_cgr().\n"
        " * Returns the deletion or CPU-dispatch error, or 0 on success.\n",
        "CGR deletion contract",
    )
    header = replace(
        header,
        "/**\n * qman_delete_cgr - Deregisters a congestion group object\n",
        "/**\n"
        " * qman_cgr_disable_callback - Stop callbacks for a registered CGR\n"
        " * @cgr: successfully registered CGR, with its owner portal alive\n"
        " *\n"
        " * Waits for an active callback and prevents future callbacks. The CGR\n"
        " * remains registered and its hardware and ID ownership are unchanged.\n"
        " */\n"
        "void qman_cgr_disable_callback(struct qman_cgr *cgr);\n\n"
        "/**\n * qman_delete_cgr - Deregisters a congestion group object\n",
        "CGR callback-disable declaration",
    )
    dpaa_header = replace(
        dpaa_header, "struct dpaa_priv;\n",
        "struct dpaa_priv;\n\n" + CONTEXT, "CGR context",
    )
    dpaa_header = replace(
        dpaa_header, "\t\tstruct qman_cgr cgr;\n",
        "\t\tstruct dpaa_cgr_context *ctx;\n", "egress CGR owner",
    )
    dpaa_header = replace(
        dpaa_header, "\tstruct qman_cgr ingress_cgr;\n",
        "\tstruct dpaa_cgr_context *ingress_cgr;\n", "ingress CGR owner",
    )
    dpaa = replace_pattern(
        dpaa, r"priv->cgr_data\.cgr(?!\w)", "priv->cgr_data.ctx->cgr",
        "egress CGR references", 14,
    )
    dpaa = replace_pattern(
        dpaa, r"priv->ingress_cgr(?!\w)", "priv->ingress_cgr->cgr",
        "ingress CGR references", 10,
    )
    ethtool = replace_pattern(
        ethtool, r"priv->cgr_data\.cgr(?!\w)", "priv->cgr_data.ctx->cgr",
        "ethtool CGR reference", 1,
    )
    dpaa = replace(
        dpaa, CALLBACK_COMMENT + OLD_CALLBACK,
        HELPERS + CALLBACK_COMMENT + NEW_CALLBACK, "CGR callback",
    )

    for member in ("cgr_data.ctx", "ingress_cgr"):
        cgr = f"priv->{member}->cgr"
        alloc = f"\terr = qman_alloc_cgrid(&{cgr}.cgrid);\n"
        dpaa = replace(
            dpaa, alloc,
            f"\tpriv->{member} = dpaa_cgr_alloc(priv);\n"
            f"\tif (!priv->{member})\n"
            "\t\treturn -ENOMEM;\n\n" + alloc,
            f"{member} allocation",
        )
        alloc_failure = (
            f"\terr = qman_alloc_cgrid(&{cgr}.cgrid);\n"
            "\tif (err < 0) {\n"
        )
        dpaa = replace(
            dpaa, alloc_failure,
            alloc_failure +
            f"\t\tkfree(priv->{member});\n"
            f"\t\tpriv->{member} = NULL;\n",
            f"{member} allocation failure",
        )
        create_failure = f"\t\tqman_release_cgrid({cgr}.cgrid);\n\t\tgoto out_error;\n"
        dpaa = replace(
            dpaa, create_failure,
            "\t\tdpaa_cgr_create_failed(priv->net_dev->dev.parent,\n"
            f"\t\t\t\t    &priv->{member}, err);\n"
            "\t\tgoto out_error;\n",
            f"{member} creation failure",
        )
        release = (
            f"\tqman_delete_cgr_safe(&{cgr});\n"
            f"\tqman_release_cgrid({cgr}.cgrid);\n"
        )
        dpaa = replace(
            dpaa, release, f"\tdpaa_cgr_release(dev, &priv->{member});\n",
            f"{member} release", 2,
        )
    dpaa = replace(
        dpaa,
        "\tdev_set_drvdata(dev, NULL);\n\tunregister_netdev(net_dev);\n",
        "\tdev_set_drvdata(dev, NULL);\n"
        "\tdpaa_cgr_deactivate(priv->cgr_data.ctx);\n"
        "\tunregister_netdev(net_dev);\n",
        "callback deactivation before unregister",
    )
    return qman, header, dpaa, dpaa_header, ethtool


def main():
    paths = (QMAN, QMAN_H, DPAA, DPAA_H, ETHTOOL)
    try:
        sources = tuple(path.read_text() for path in paths)
        if verify(sources):
            print("### F-300: already applied")
            return 0
        if any(MARKER in source for source in sources):
            raise ValueError("partial or ambiguous CGR containment")
        updated = transform(sources)
        if not verify(updated):
            raise ValueError("CGR containment invariant failed")
        for path, source in zip(paths, updated):
            path.write_text(source)
    except (OSError, ValueError) as error:
        print(f"### F-300: FATAL: {error}", file=sys.stderr)
        return 1
    print("### F-300: applied")
    return 0


if __name__ == "__main__":
    sys.exit(main())
