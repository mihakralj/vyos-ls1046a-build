"""F-193: production FE flow-add argument diagnostic.

The production flowtable callback currently reports only
`REPLACE rollback ... fe_flow_insert=-22`.  That loses the three values needed
to distinguish the remaining validation sites in fman_pcd_fe_flow_add():

* the value the OOT caller passed as `hw_port_id`;
* the action key width versus the active external-hash table width; and
* the own-port fallback FQID resolved for the supplied port identifier.

F-193 is diagnostics only.  It does not alter an action, table selection,
FQID selection, descriptor, MURAM, DDR, KeyGen, or packet disposition.  It
emits one bounded info line per production add attempt and one warning line
on failure.  The logs establish whether `ask_fe_flow_insert()` is passing its
v4/v6 table index in the API's hw-port-id slot, and which `-EINVAL` guard
actually fires before a corrective ABI change is considered.
"""

import sys

path = "drivers/net/ethernet/freescale/fman/fman_pcd.c"
with open(path) as f:
    src = f.read()

marker = "F-193(prod-flow-add-diag)"
if marker in src:
    print("### F-193: production flow-add diagnostic already applied")
    sys.exit(1)

old = """\tstruct fman_pcd_ehash_table *t;\n\tstruct fman_pcd_fe_obj *enq_obj;\n\n\tif (!fm || !action || action->key_size == 0)\n\t\treturn -EINVAL;\n\tpcd = fman_get_pcd(fm);\n\tif (!pcd)\n\t\treturn -ENXIO;\n\n\tt = fman_pcd_ehash_table_by_index(pcd, 0);\n\tif (!t)\n\t\treturn -ENODEV;\n"""
new = """\tstruct fman_pcd_ehash_table *t;\n\tstruct fman_pcd_fe_obj *enq_obj;\n\tu32 target_fqid;\n\n\tif (!fm || !action || action->key_size == 0)\n\t\treturn -EINVAL;\n\tpcd = fman_get_pcd(fm);\n\tif (!pcd)\n\t\treturn -ENXIO;\n\n\tt = fman_pcd_ehash_table_by_index(pcd, 0);\n\tif (!t)\n\t\treturn -ENODEV;\n\n\t/* F-193(prod-flow-add-diag): read-only production-path argument trace.\n\t * `hw_port_id` must name the ingress FMan RX port for own-port FQID\n\t * resolution; it is not an ehash table selector.  Keep the existing\n\t * table-0 behavior intact while this log establishes the live values.\n\t */\n\ttarget_fqid = fman_pcd_resolve_miss_fqid(pcd, hw_port_id);\n\tdev_info(fman_get_dev(pcd->fman),\n\t\t "fe_flow: F-193 add hw_port=0x%02x key_size=%u table0_key_size=%u target_fqid=0x%x\\n",\n\t\t hw_port_id, action->key_size, t->key_size, target_fqid);\n"""
if old not in src:
    print("### F-193: FATAL: flow-add validation anchor not found")
    sys.exit(1)
src = src.replace(old, new, 1)

old = """\t\tint err = fman_pcd_ehash_add_key(t, action->key, action->key_size,\n\t\t\t\t\t\t (u32)enq_obj->muram_off,\n\t\t\t\t\t\t fman_pcd_resolve_miss_fqid(pcd, hw_port_id),\n\t\t\t\t\t\t false /* F-189(genl-stats-false) */);\n\n\tif (!err) {\n"""
new = """\t\tint err = fman_pcd_ehash_add_key(t, action->key, action->key_size,\n\t\t\t\t\t\t (u32)enq_obj->muram_off,\n\t\t\t\t\t\t target_fqid,\n\t\t\t\t\t\t false /* F-189(genl-stats-false) */);\n\n\t\tif (err)\n\t\t\tdev_warn(fman_get_dev(pcd->fman),\n\t\t\t\t "fe_flow: F-193 add failed=%d hw_port=0x%02x key_size=%u table0_key_size=%u\\n",\n\t\t\t\t err, hw_port_id, action->key_size, t->key_size);\n\n\tif (!err) {\n"""
if old not in src:
    print("### F-193: FATAL: flow-add call anchor not found")
    sys.exit(1)
src = src.replace(old, new, 1)

with open(path, "w") as f:
    f.write(src)
print("### fman_pcd.c: F-193 production flow-add diagnostic applied (2 blocks)")
