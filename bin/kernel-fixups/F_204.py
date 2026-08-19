"""F-204 (T-M6-1 Phase 2a): add an explicit ehash table selector to the
FE flow-add ABI, WITHOUT touching the F-195 own-port-FQID semantics.

Background / S0 gate (2026-08-19): fman_pcd_fe_flow_add() historically pinned
`fman_pcd_ehash_table_by_index(pcd, 0)` and used its `hw_port_id` argument
ONLY to resolve the record's own-port miss FQID (eth3=0x200, eth4=0x300) via
fman_pcd_resolve_miss_fqid() (F-193/F-195). An earlier OOT attempt to pass the
table index THROUGH hw_port_id misrouted eth4 ingress to eth3's FQID and
dropped it cross-port (F-195 fixed that by passing key->port_id instead).

So the table selector MUST be a SEPARATE field, never overloaded onto
hw_port_id. This fixup:

  1. Adds `u8 table_idx` (+3 reserved bytes for stable 4-byte layout) to
     struct fman_pcd_fe_flow_action, after F-198's TX fields.
  2. Changes the ONE add-path table lookup (the F-194 guarded block) from a
     hardcoded index 0 to action->table_idx. hw_port_id and the
     resolve_miss_fqid() own-port logic are left exactly as F-195 set them.

Byte-identical for IPv4: ask.ko sets action.table_idx = 0 for v4, so
fman_pcd_ehash_table_by_index(pcd, 0) is selected exactly as before. v6 is
still failed to software (ask_hw_flow_preflight), so table_idx=1 is never
actually exercised on silicon yet; the dormant v6 table (F-140) now merely has
a way to be addressed once Phase 3 enables v6 dispatch. No node-binding
(F-185/F-190 list_first_entry) or UPDATE_HOPLIMIT change here — those are later
phases gated on the dispatch experiment.

Must run AFTER F-198 (struct TX fields) and AFTER F-194 (the add-path guard
block this repoints). Idempotent ('F-204' markers). CI-only build.
"""

import os
import sys

kroot = "drivers/net/ethernet/freescale/fman"
pcd_c = os.path.join(kroot, "fman_pcd.c")
hdr = "include/linux/fsl/fman_pcd.h"

changes = 0


def patch(path, name, old, new, marker):
    global changes
    if not os.path.exists(path):
        print(f"### F-204: FATAL: {path} not found")
        sys.exit(1)
    with open(path) as f:
        src = f.read()
    if marker in src:
        print(f"### F-204: {name} already applied")
        return
    if old not in src:
        print(f"### F-204: FATAL: '{name}' anchor not found verbatim in "
              f"{path} -- source drifted. Refusing to guess.")
        sys.exit(1)
    src = src.replace(old, new, 1)
    with open(path, "w") as f:
        f.write(src)
    changes += 1
    print(f"### {path}: F-204 {name} applied")


# 1. Add table_idx to the action struct (patches F-198's final struct form).
struct_old = (
    "\tu32  tx_fqid;\n"
    "\tu8   next_hop_mac[6];\n"
    "\tu8   egress_mac[6];\n"
    "\tu16  eth_type;\n"
    "};"
)
struct_new = (
    "\tu32  tx_fqid;\n"
    "\tu8   next_hop_mac[6];\n"
    "\tu8   egress_mac[6];\n"
    "\tu16  eth_type;\n"
    "\t/* F-204 (T-M6-1 Phase 2a): ehash table selector. 0 = IPv4 (the\n"
    "\t * only silicon-dispatched table today); 1 = the dormant IPv6\n"
    "\t * table (F-140). SEPARATE from hw_port_id, which stays the ingress\n"
    "\t * FMan port for own-port miss-FQID resolution (F-195). */\n"
    "\tu8   table_idx;\n"
    "\tu8   _rsvd_204[3];\n"
    "};"
)
# The struct is declared in both the in-tree header and (via F-094) possibly
# duplicated; patch every occurrence in the header.
if os.path.exists(hdr):
    with open(hdr) as f:
        hsrc = f.read()
    if "F-204" in hsrc:
        print("### F-204: header table_idx already applied")
    elif struct_old in hsrc:
        hsrc = hsrc.replace(struct_old, struct_new)
        with open(hdr, "w") as f:
            f.write(hsrc)
        changes += 1
        print(f"### {hdr}: F-204 struct table_idx applied")
    else:
        print(f"### F-204: FATAL: action-struct TX-fields anchor not found in "
              f"{hdr} (F-198 must run first). Refusing to guess.")
        sys.exit(1)
else:
    print(f"### F-204: FATAL: {hdr} not found")
    sys.exit(1)

# 2. Repoint the add-path table lookup from index 0 to action->table_idx.
#    By the time F-204 runs, F-202 has wrapped the add path in fe_lock, so the
#    add-path lookup is uniquely preceded by F-202's lock comment+mutex_lock
#    (the del-path lookup has a different preceding comment). Anchor on that
#    unique prefix so only the ADD path is repointed, never the del path.
add_old = (
    "\t/* F-202(flow-api-lock): serialize production add/delete/clear.\n"
    "\t * fman_pcd_ehash_{add,del}_key require pcd->fe_lock. */\n"
    "\tmutex_lock(&pcd->fe_lock);\n\n"
    "\tt = fman_pcd_ehash_table_by_index(pcd, 0);"
)
add_new = (
    "\t/* F-202(flow-api-lock): serialize production add/delete/clear.\n"
    "\t * fman_pcd_ehash_{add,del}_key require pcd->fe_lock. */\n"
    "\tmutex_lock(&pcd->fe_lock);\n\n"
    "\t/* F-204: select the ehash table by the explicit action->table_idx\n"
    "\t * (0=IPv4, 1=dormant IPv6). NOT hw_port_id -- that stays the ingress\n"
    "\t * FMan port for own-port miss-FQID resolution (F-195). */\n"
    "\tt = fman_pcd_ehash_table_by_index(pcd, action->table_idx);"
)
patch(pcd_c, "add-path table selector", add_old, add_new, "F-204: select the ehash table")

if changes:
    print(f"### F-204: {changes} change(s) applied")
else:
    print("### F-204: no changes -- already present")
