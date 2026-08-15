"""F-194: trace the FE flow-add early -EINVAL guard.

F-193 is present in the deployed FMan binary but emits no line before a
production `fe_flow_insert=-22`.  The only -EINVAL return preceding F-193's
first trace is `!fm || !action || action->key_size == 0`.  This diagnostic
keeps the flow-add ABI and all datapath behaviour unchanged while logging the
three guard inputs and the kernel-side action layout.
"""

import sys

path = "drivers/net/ethernet/freescale/fman/fman_pcd.c"
with open(path) as f:
    src = f.read()

marker = "F-194(flow-add-early-guard)"
if marker in src:
    print("### F-194: flow-add early-return diagnostic already applied")
    sys.exit(1)

old = """\tif (!fm || !action || action->key_size == 0)\n\t\treturn -EINVAL;\n\tpcd = fman_get_pcd(fm);\n\tif (!pcd)\n\t\treturn -ENXIO;\n\n\tt = fman_pcd_ehash_table_by_index(pcd, 0);\n\tif (!t)\n\t\treturn -ENODEV;\n"""
new = """\t/* F-194(flow-add-early-guard): F-193 is below these guards. Keep\n\t * every return semantic intact while naming the failed prerequisite.\n\t */\n\tif (!fm || !action || action->key_size == 0) {\n\t\tpr_warn_ratelimited("fman_pcd: F-194 flow-add early -EINVAL fm=%px action=%px key_size=%u sizeof_action=%zu key_size_off=%zu\\n",\n\t\t\t\t    fm, action, action ? action->key_size : 0,\n\t\t\t\t    sizeof(*action), offsetof(struct fman_pcd_fe_flow_action, key_size));\n\t\treturn -EINVAL;\n\t}\n\tpcd = fman_get_pcd(fm);\n\tif (!pcd) {\n\t\tpr_warn_ratelimited("fman_pcd: F-194 flow-add no-pcd fm=%px hw_port=0x%02x key_size=%u\\n",\n\t\t\t\t    fm, hw_port_id, action->key_size);\n\t\treturn -ENXIO;\n\t}\n\n\tt = fman_pcd_ehash_table_by_index(pcd, 0);\n\tif (!t) {\n\t\tdev_warn_ratelimited(fman_get_dev(pcd->fman),\n\t\t\t\t     "fe_flow: F-194 no-table0 hw_port=0x%02x key_size=%u\\n",\n\t\t\t\t     hw_port_id, action->key_size);\n\t\treturn -ENODEV;\n\t}\n"""
if old not in src:
    print("### F-194: FATAL: flow-add early-return anchor not found")
    sys.exit(1)

src = src.replace(old, new, 1)
with open(path, "w") as f:
    f.write(src)
print("### fman_pcd.c: F-194 flow-add early-return diagnostics applied (1 block)")
