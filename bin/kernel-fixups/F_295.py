#!/usr/bin/env python3
"""Quarantine a partially initialized F-199 offload FQ after an init error."""

from pathlib import Path
import sys


PATH = Path("drivers/net/ethernet/freescale/dpaa/dpaa_eth.c")
MARKER = "F-295: retain an uncertain offload FQ"

OLD_SIGNATURE = "static int dpaa_fq_init(struct dpaa_fq *dpaa_fq, bool td_enable)\n"
NEW_SIGNATURE = (
    "static int dpaa_fq_init(struct dpaa_fq *dpaa_fq, bool td_enable,\n"
    "\t\t\tbool *retain_failed_fq)\n"
)
OLD_INIT_ERROR = (
    '\t\t\tdev_err(dev, "qman_init_fq(%u) = %d\\n",\n'
    "\t\t\t\tqman_fq_fqid(fq), err);\n"
    "\t\t\tqman_destroy_fq(fq);\n"
    "\t\t\treturn err;\n"
)
NEW_INIT_ERROR = (
    '\t\t\tdev_err(dev, "qman_init_fq(%u) = %d\\n",\n'
    "\t\t\t\tqman_fq_fqid(fq), err);\n"
    "\t\t\tif (retain_failed_fq)\n"
    "\t\t\t\t*retain_failed_fq = true;\n"
    "\t\t\telse\n"
    "\t\t\t\tqman_destroy_fq(fq);\n"
    "\t\t\treturn err;\n"
)
OLD_DECL = (
    "\tstruct dpaa_priv *priv;\n"
    "\tstruct dpaa_fq *dpaa_fq;\n"
    "\tint ret;\n"
)
NEW_DECL = (
    "\tstatic bool offload_fq_faulted;\n"
    "\tstatic netdevice_tracker offload_fq_tracker;\n"
    "\tstruct dpaa_priv *priv;\n"
    "\tstruct dpaa_fq *dpaa_fq;\n"
    "\tbool retain_failed_fq = false;\n"
    "\tint ret;\n"
)
OLD_CHECK = (
    "\tif (dev->netdev_ops != &dpaa_ops) {\n"
    "\t\tret = -ENODEV;\n"
    "\t\tgoto out_unlock;\n"
    "\t}\n"
    "\tpriv = netdev_priv(dev);\n"
)
NEW_CHECK = (
    "\tif (READ_ONCE(dev->reg_state) != NETREG_REGISTERED ||\n"
    "\t    dev->netdev_ops != &dpaa_ops) {\n"
    "\t\tret = -ENODEV;\n"
    "\t\tgoto out_unlock;\n"
    "\t}\n"
    "\tif (offload_fq_faulted) {\n"
    "\t\tret = -ESHUTDOWN;\n"
    "\t\tgoto out_unlock;\n"
    "\t}\n"
    "\tpriv = netdev_priv(dev);\n"
)
OLD_ALLOC_ERROR = (
    "\tret = dpaa_fq_init(dpaa_fq, true);\n"
    "\tif (ret < 0) {\n"
    "\t\tlist_del(&dpaa_fq->list);\n"
    "\t\tdevm_kfree(dev->dev.parent, dpaa_fq);\n"
    "\t\tgoto out_unlock;\n"
    "\t}\n"
)
NEW_ALLOC_ERROR = (
    "\tret = dpaa_fq_init(dpaa_fq, true, &retain_failed_fq);\n"
    "\tif (ret < 0) {\n"
    "\t\tif (retain_failed_fq) {\n"
    "\t\t\t/*\n"
    "\t\t\t * F-295: retain an uncertain offload FQ and its netdev.\n"
    "\t\t\t * RTNL serializes the boot-lifetime fence and reference.\n"
    "\t\t\t */\n"
    "\t\t\tnetdev_hold(dev, &offload_fq_tracker, GFP_KERNEL);\n"
    "\t\t\toffload_fq_faulted = true;\n"
    "\t\t\tnetdev_err(dev,\n"
    '\t\t\t\t   "offload FQ init failed (%d); retaining FQ and netdev, "\n'
    '\t\t\t\t   "further allocations disabled for this kernel boot\\n", ret);\n'
    "\t\t} else {\n"
    "\t\t\tlist_del(&dpaa_fq->list);\n"
    "\t\t\tdevm_kfree(dev->dev.parent, dpaa_fq);\n"
    "\t\t}\n"
    "\t\tgoto out_unlock;\n"
    "\t}\n"
)
OLD_PROBE = "\t\terr = dpaa_fq_init(dpaa_fq, false);\n"
NEW_PROBE = "\t\terr = dpaa_fq_init(dpaa_fq, false, NULL);\n"


def replace_once(source, old, new, label):
    count = source.count(old)
    if count != 1:
        raise ValueError(f"{label}: found {count} anchors, expected 1")
    return source.replace(old, new, 1)


def main():
    try:
        source = PATH.read_text()
        if MARKER in source:
            for label, fragment in (
                ("signature", NEW_SIGNATURE),
                ("init error", NEW_INIT_ERROR),
                ("allocator declarations", NEW_DECL),
                ("allocator checks", NEW_CHECK),
                ("allocator error", NEW_ALLOC_ERROR),
                ("probe call", NEW_PROBE),
            ):
                if source.count(fragment) != 1:
                    raise ValueError(f"partially applied fix: {label}")
            print("### F-295: already applied")
            return 0

        source = replace_once(source, OLD_SIGNATURE, NEW_SIGNATURE, "signature")
        source = replace_once(source, OLD_INIT_ERROR, NEW_INIT_ERROR, "init error")
        source = replace_once(source, OLD_PROBE, NEW_PROBE, "probe call")
        start = source.index("int dpaa_alloc_offload_tx_fq(struct net_device *dev, u32 *fqid)\n")
        end = source.index("\n}\n", start) + 3
        allocator = source[start:end]
        allocator = replace_once(allocator, OLD_DECL, NEW_DECL, "allocator declarations")
        allocator = replace_once(allocator, OLD_CHECK, NEW_CHECK, "allocator checks")
        allocator = replace_once(allocator, OLD_ALLOC_ERROR, NEW_ALLOC_ERROR, "allocator error")
        source = source[:start] + allocator + source[end:]
        PATH.write_text(source)
    except (OSError, ValueError) as error:
        print(f"### F-295: FATAL: {error}", file=sys.stderr)
        return 1
    print("### F-295: applied")
    return 0


if __name__ == "__main__":
    sys.exit(main())
