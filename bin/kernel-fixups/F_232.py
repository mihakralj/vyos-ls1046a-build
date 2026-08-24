"""F-232: read-only HIT-terminal FQID diagnostic.

Emits one bounded info line at the F-198 add-key site printing the ingress
hw-port, raw action->tx_fqid, resolved target_fqid and the final hit_fqid
written into the record. Disambiguates the ASK2 TX-confirm CPU cost between
(H1) tx_fqid not propagating (hit_fqid falls back to the confirmed own-port
0x200/0x300) and (H2) the record targets the no-confirm FQ (0x2ba/0x2bb) but
F-199's context_a fails to suppress the confirm on 210.10.1. Diagnostics only:
no descriptor/FQID/MURAM/DDR/KeyGen/packet mutation. Runs after F-198/F-204.
Count-gated, idempotent marker "F-232(hit-fqid-diag)"; hard-fail on drift.
"""

import sys

path = "drivers/net/ethernet/freescale/fman/fman_pcd.c"
with open(path) as f:
    src = f.read()

marker = "F-232(hit-fqid-diag)"
if marker in src:
    print("### F-232: HIT-terminal FQID diagnostic already applied")
    sys.exit(0)

old = (
    "\t\tu32 hit_fqid = action->tx_fqid ? action->tx_fqid : target_fqid;\n"
    "\t\tconst u8 *l2_dst = action->tx_fqid ? action->next_hop_mac : NULL;\n"
)
new = (
    "\t\tu32 hit_fqid = action->tx_fqid ? action->tx_fqid : target_fqid;\n"
    "\t\tdev_info(fman_get_dev(pcd->fman),\n"
    "\t\t\t \"fe_flow: F-232(hit-fqid-diag) hw_port=0x%02x tx_fqid=0x%x target_fqid=0x%x hit_fqid=0x%x l2=%d\\n\",\n"
    "\t\t\t hw_port_id, action->tx_fqid, target_fqid, hit_fqid,\n"
    "\t\t\t action->tx_fqid ? 1 : 0);\n"
    "\t\tconst u8 *l2_dst = action->tx_fqid ? action->next_hop_mac : NULL;\n"
)

if src.count(old) != 1:
    print(f"### F-232: FATAL: hit_fqid anchor count is {src.count(old)}, expected 1")
    sys.exit(1)

src = src.replace(old, new, 1)

with open(path, "w") as f:
    f.write(src)

print("### fman_pcd.c: F-232 HIT-terminal FQID diagnostic applied (1 block)")
