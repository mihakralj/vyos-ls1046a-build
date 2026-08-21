"""F-218 (T-M6-1 IPv6 flow delete): select the ehash table by key_size in
fman_pcd_fe_flow_del() so a v6 record (table 1, 38-byte key) is actually found
and unlinked instead of leaking.

BUG
---
fman_pcd_fe_flow_del() (F-117 body, F-202 locking) hardcodes
  t = fman_pcd_ehash_table_by_index(pcd, 0);
i.e. it always scans table 0. v4 records live in table 0 (14-byte key) but v6
records live in table 1 (38-byte key, F-140/F-204). ask_fe_flow_remove() already
builds the correct 38-byte v6 key and passes key_size=38, but the del path then
searches table 0, returns -ENOENT (mapped to 0), and the v6 record in table 1 is
never removed -> leak on every v6 FLOW_CLS_DESTROY.

FIX
---
Derive the table index from key_size, with no signature/ABI change (the OOT
header and all callers are untouched): key_size == ASK/38 -> table 1, else
table 0. The insert side (F-204) selects table via action->table_idx; this is
the symmetric delete-side selector keyed on the length the caller already sends.
A NULL/zero key still means clear-all (both tables via
fman_pcd_ehash_flow_clear_all), unchanged.

Table-size constants match F-140 (v6 table key_size = 38) and the v4 14-byte
key. Using key_size (not a new arg) keeps this a pure content change and avoids
threading table_idx through fman_pcd_fe_flow_del's exported prototype +
ask_fman_caps.h + the F-118 debugfs verb.

Gated implicitly: with v6 disabled no 38-byte delete ever arrives, so table 0 is
always chosen — byte-identical v4 behaviour. Must run AFTER F-117 (creates the
per-key body) and F-202 (adds fe_lock). Idempotent via the F-218 marker.
"""

import os
import sys

path = "drivers/net/ethernet/freescale/fman/fman_pcd.c"
if not os.path.exists(path):
    print("### F-218: fman_pcd.c not found")
    sys.exit(0)

with open(path) as f:
    src = f.read()

if "F-218" in src:
    print("### F-218 already applied")
    sys.exit(0)

# Anchor: the hardcoded table-0 select inside fman_pcd_fe_flow_del, right after
# the clear-all early return (F-117/F-202 final body).
anchor = (
    "\t/* No key => legacy clear-all (admin flush / disengage). */\n"
    "\tif (!key || key_size == 0) {\n"
    "\t\tfman_pcd_ehash_flow_clear_all(pcd);\n"
    "\t\tmutex_unlock(&pcd->fe_lock);\n"
    "\t\treturn 0;\n"
    "\t}\n"
    "\tt = fman_pcd_ehash_table_by_index(pcd, 0);\n"
)
if anchor not in src:
    print("### F-218: FATAL: fman_pcd_fe_flow_del table-0 select anchor not found "
          "(F-117/F-202 body changed) — refusing to guess.")
    sys.exit(1)

replacement = (
    "\t/* No key => legacy clear-all (admin flush / disengage). */\n"
    "\tif (!key || key_size == 0) {\n"
    "\t\tfman_pcd_ehash_flow_clear_all(pcd);\n"
    "\t\tmutex_unlock(&pcd->fe_lock);\n"
    "\t\treturn 0;\n"
    "\t}\n"
    "\t/* F-218: select the ehash table by key length. v6 records (38-byte\n"
    "\t * key) live in table 1 (F-140/F-204); v4 (14-byte) in table 0.\n"
    "\t * Without this, a v6 DESTROY scans table 0, misses, and leaks the\n"
    "\t * table-1 record. Symmetric with the insert-side action->table_idx.\n"
    "\t */\n"
    "\tt = fman_pcd_ehash_table_by_index(pcd, (key_size == 38) ? 1 : 0);\n"
)

src = src.replace(anchor, replacement, 1)
with open(path, "w") as f:
    f.write(src)
print("### fman_pcd.c: F-218 fe_flow_del selects ehash table by key_size (v6 table1)")
