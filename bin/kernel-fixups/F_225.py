"""F-225 (Phase A): v4 ehash key_size 14 -> 46 for the dual-lane GEC key.

Companion to F-224 (which programs the 46-byte all-GEC dual-lane key on the
AC_CC scheme). The ehash table + node comparator must use the same 46-byte width
or every insert fails the key_size==table->key_size check.

Two emitted v4 sites (both must move together with F-224's scheme change):
  1. __fman_pcd_fe_build_vm_chain(): ehash_key_sz 14 -> 46 (the global template
     table; F-188 set it to 14).
  2. F-220 per-port table alloc: fman_pcd_ehash_table_set(pcd, 0x7FFF, 14, 0)
     -> (..., 46, 0) (each engaged port's own routed table).

All node/hashfe encoders derive contextSize/key_size from t->key_size, so they
follow automatically. fman_pcd_ehash_table_set validates key_size <= 0x3f (63)
and the flow buffers cap at 56 — 46 passes both.

v6 remains gated OFF in Phase A; the dormant 38-byte v6 table (F-140) is
untouched. Retire/rework in Phase B/C when the per-port table becomes the single
dual-stack routed table.

Must run AFTER F-188 (emits ehash_key_sz=14) and F-220 (emits the per-port
alloc). Idempotent via marker.
"""

import os
import sys

path = "drivers/net/ethernet/freescale/fman/fman_pcd.c"
if not os.path.exists(path):
    print("### F-225: fman_pcd.c not found")
    sys.exit(0)

with open(path) as f:
    src = f.read()

changes = 0


def one(name, marker, old, new):
    global src, changes
    if marker not in new:
        print(f"### F-225 FATAL: marker missing in '{name}'"); sys.exit(1)
    if marker in src:
        print(f"### F-225: {name} already applied"); return
    if old not in src:
        print(f"### F-225 FATAL: '{name}' anchor not found (run after F-188/F-220)"); sys.exit(1)
    if src.count(old) != 1:
        print(f"### F-225 FATAL: '{name}' anchor not unique ({src.count(old)})"); sys.exit(1)
    src = src.replace(old, new, 1); changes += 1
    print(f"### fman_pcd.c: F-225 {name} applied")


# 1. template table key size
one(
    "vm-chain ehash_key_sz 14->46",
    "F-225(key-sz-46)",
    "\tconst u8  ehash_key_sz  = 14;\n",
    "\tconst u8  ehash_key_sz  = 46;\t/* F-225(key-sz-46): dual-lane GEC key */\n",
)

# 2. per-port table alloc key size
one(
    "per-port table keysize 14->46",
    "F-225(perport-46)",
    "\t\t\t\t\t    fman_pcd_ehash_table_set(pcd, 0x7FFF, 14, 0) == 0) {\n",
    "\t\t\t\t\t    fman_pcd_ehash_table_set(pcd, 0x7FFF, 46, 0) == 0) {"
    " /* F-225(perport-46) */\n",
)

with open(path, "w") as f:
    f.write(src)

if changes:
    print(f"### F-225 complete ({changes} change(s))")
else:
    print("### F-225 no changes")
    sys.exit(0)
