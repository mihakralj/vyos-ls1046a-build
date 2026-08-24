"""F-193: hoist the production flow-add target FQID (structural prerequisite).

Originally a flow-add argument diagnostic, this is now a small STRUCTURAL
change kept because F-198's hardware TX terminal depends on it: it hoists the
own-port fallback FQID into a local `target_fqid` and passes that (instead of
the inline `fman_pcd_resolve_miss_fqid(pcd, hw_port_id)`) as the
`fman_pcd_ehash_add_key()` FQID argument. F-198 then computes
`hit_fqid = action->tx_fqid ? action->tx_fqid : target_fqid`, so the variable
must exist at the call site.

Functionally identical to the original inline resolve (same resolver, same
`hw_port_id`, computed once). The diagnostic logging that this fixup used to
carry has been removed after the flow-add path was validated (F-195/F-197);
what remains is only the variable hoist required by F-198/F-200/F-226/F-230.

It changes no action, table selection, descriptor, MURAM, DDR, KeyGen register,
or packet disposition. Count-gated, idempotent; hard-fail on source drift.
"""

import sys

path = "drivers/net/ethernet/freescale/fman/fman_pcd.c"
with open(path) as f:
    src = f.read()

marker = "F-193(prod-flow-add-target-fqid)"
if marker in src:
    print("### F-193: target-fqid hoist already applied")
    sys.exit(1)

# 1. Declare target_fqid and resolve it once, right after the table-0 lookup.
old = """\tstruct fman_pcd_ehash_table *t;\n\tstruct fman_pcd_fe_obj *enq_obj;\n\n\tif (!fm || !action || action->key_size == 0)\n\t\treturn -EINVAL;\n\tpcd = fman_get_pcd(fm);\n\tif (!pcd)\n\t\treturn -ENXIO;\n\n\tt = fman_pcd_ehash_table_by_index(pcd, 0);\n\tif (!t)\n\t\treturn -ENODEV;\n"""
new = """\tstruct fman_pcd_ehash_table *t;\n\tstruct fman_pcd_fe_obj *enq_obj;\n\tu32 target_fqid;\n\n\tif (!fm || !action || action->key_size == 0)\n\t\treturn -EINVAL;\n\tpcd = fman_get_pcd(fm);\n\tif (!pcd)\n\t\treturn -ENXIO;\n\n\tt = fman_pcd_ehash_table_by_index(pcd, 0);\n\tif (!t)\n\t\treturn -ENODEV;\n\n\t/* F-193(prod-flow-add-target-fqid): resolve the own-port fallback FQID\n\t * once so F-198's TX terminal can select tx_fqid || target_fqid. */\n\ttarget_fqid = fman_pcd_resolve_miss_fqid(pcd, hw_port_id);\n"""
if old not in src:
    print("### F-193: FATAL: flow-add declaration anchor not found")
    sys.exit(1)
src = src.replace(old, new, 1)

# 2. Pass the hoisted target_fqid to the ehash add-key call (F-198 anchors here).
old = """\t\tint err = fman_pcd_ehash_add_key(t, action->key, action->key_size,\n\t\t\t\t\t\t (u32)enq_obj->muram_off,\n\t\t\t\t\t\t fman_pcd_resolve_miss_fqid(pcd, hw_port_id),\n\t\t\t\t\t\t false /* F-189(genl-stats-false) */);\n"""
new = """\t\tint err = fman_pcd_ehash_add_key(t, action->key, action->key_size,\n\t\t\t\t\t\t (u32)enq_obj->muram_off,\n\t\t\t\t\t\t target_fqid,\n\t\t\t\t\t\t false /* F-189(genl-stats-false) */);\n"""
if old not in src:
    print("### F-193: FATAL: flow-add call anchor not found")
    sys.exit(1)
src = src.replace(old, new, 1)

with open(path, "w") as f:
    f.write(src)
print("### fman_pcd.c: F-193 target-fqid hoist applied (2 blocks)")
