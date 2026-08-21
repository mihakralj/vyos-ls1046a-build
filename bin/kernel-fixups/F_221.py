"""F-221 (Phase 1: repoint routed IPv4 to the per-port table from F-220).

F-220 added per-port routed-IPv4 table instances (pcd->fe_arming_v4_table at arm
time, fp->table_v4 after fe_port_set) plus the helper
fman_pcd_ehash_table_for_port(pcd, hw_port_id). This fixup makes the three v4
table-resolution sites use the per-port instance instead of the single global
table index 0. IPv6 (table_idx 1 / 38-byte key) is unchanged.

EDITS
-----
1. F-185 node writer (__fman_pcd_fe_arm_engage): the en_exthash_node written at
   gro+0 for the arming port must point at THAT port's table_dma. At arm time
   fp does not exist yet, so use pcd->fe_arming_v4_table (the table F-220 just
   allocated for this engage), falling back to the global first table if the
   per-port alloc failed.
2. F-204 flow_add: table_idx 0 (IPv4) -> table_for_port(pcd, hw_port_id); keep
   by_index(table_idx) for table_idx 1 (IPv6). hw_port_id is already the arg.
3. F-218 flow_del: v4 (key_size != 38) -> table_for_port(pcd, hw_port_id); v6
   (key_size == 38) keeps the global table index 1. Un-void hw_port_id (F-218
   discarded it); the ask.ko caller must pass key->port_id on delete (a
   companion OOT edit sets that, replacing the literal 0).

WHY THIS IS SAFE
----------------
- v4 key stays 14 bytes; the per-port table uses identical mask/key_size/shift,
  so node encoding, CRC bucket index, and record layout are byte-identical.
- Fallbacks preserve the pre-F-220 behavior when no per-port table exists
  (debugfs/diagnostic paths, or an alloc failure), so nothing regresses to a
  NULL table.
- v6 path untouched; default-OFF.

Must run AFTER F-185, F-204, F-218 (anchors on their emitted code) and AFTER
F-220 (uses fe_arming_v4_table / table_for_port). Idempotent via F-221 markers.
"""

import os
import sys

path = "drivers/net/ethernet/freescale/fman/fman_pcd.c"
if not os.path.exists(path):
    print("### F-221: fman_pcd.c not found")
    sys.exit(0)

with open(path) as f:
    src = f.read()

changes = 0


def fatal(msg):
    print(f"### F-221: FATAL: {msg}")
    sys.exit(1)


def apply_one(name, marker, old, new):
    global src, changes
    if marker not in new:
        fatal(f"marker {marker} missing from replacement '{name}'")
    if marker in src:
        print(f"### F-221: {name} already applied")
        return
    if old not in src:
        fatal(f"'{name}' anchor not found verbatim — source drifted / F-220 order.")
    if src.count(old) != 1:
        fatal(f"'{name}' anchor not unique ({src.count(old)} matches).")
    src = src.replace(old, new, 1)
    changes += 1
    print(f"### fman_pcd.c: F-221 {name} applied")


# ── 1. F-185 node writer -> per-port pending table (fe_arming_v4_table). ──
# After F-220, the block is: [et decl] [F-220 alloc if] [if (et && int_buf)].
# Repoint `et` to the pending per-port table; keep the list_first_entry as the
# fallback expression.
apply_one(
    "F-185 node uses per-port table",
    "F-221(node-v4-perport)",
    "\t\t\t\t\t}\n"
    "\n"
    "\t\t\t\t\tif (et && pcd->fe_int_buf_off) {\n",
    "\t\t\t\t\t}\n"
    "\n"
    "\t\t\t\t\t/* F-221(node-v4-perport): write THIS port's own table into\n"
    "\t\t\t\t\t * its gro+0 node so per-port v4 tables are addressed, not the\n"
    "\t\t\t\t\t * global template. Fall back to the first table if the\n"
    "\t\t\t\t\t * per-port alloc failed. */\n"
    "\t\t\t\t\tif (pcd->fe_arming_v4_table)\n"
    "\t\t\t\t\t\tet = pcd->fe_arming_v4_table;\n"
    "\n"
    "\t\t\t\t\tif (et && pcd->fe_int_buf_off) {\n",
)

# ── 2. F-204 flow_add: table_idx 0 -> per-port. ──
apply_one(
    "F-204 add uses per-port table",
    "F-221(add-v4-perport)",
    "\tt = fman_pcd_ehash_table_by_index(pcd, action->table_idx);\n",
    "\t/* F-221(add-v4-perport): route IPv4 (table_idx 0) to this ingress\n"
    "\t * port's own table instance; IPv6 (table_idx 1) stays the global v6\n"
    "\t * table. hw_port_id is the ingress port passed by ask.ko. */\n"
    "\tif (action->table_idx == 0)\n"
    "\t\tt = fman_pcd_ehash_table_for_port(pcd, hw_port_id);\n"
    "\telse\n"
    "\t\tt = fman_pcd_ehash_table_by_index(pcd, action->table_idx);\n",
)

# ── 3. F-218 flow_del: v4 -> per-port; un-void hw_port_id. ──
apply_one(
    "F-218 del un-void hw_port_id",
    "F-221(del-unvoid)",
    "\tstatic bool f117_announced;\n"
    "\n"
    "\t(void)hw_port_id;\n",
    "\tstatic bool f117_announced;\n"
    "\n"
    "\t/* F-221(del-unvoid): hw_port_id now selects the per-port v4 table. */\n",
)

apply_one(
    "F-218 del uses per-port table",
    "F-221(del-v4-perport)",
    "\tt = fman_pcd_ehash_table_by_index(pcd, (key_size == 38) ? 1 : 0);\n",
    "\t/* F-221(del-v4-perport): v6 (38-byte key) -> global table 1; v4 ->\n"
    "\t * this ingress port's own table instance. */\n"
    "\tif (key_size == 38)\n"
    "\t\tt = fman_pcd_ehash_table_by_index(pcd, 1);\n"
    "\telse\n"
    "\t\tt = fman_pcd_ehash_table_for_port(pcd, hw_port_id);\n",
)

with open(path, "w") as f:
    f.write(src)

if changes:
    print(f"### F-221 complete ({changes} change(s))")
else:
    print("### F-221 no changes (already present)")
    sys.exit(0)
