"""F-272 (G2 PPPoE over VLAN, 2026-10-29): VLAN insert inner EtherType.

INSERT_VLAN_HDR (0x42) wrote (TCI << 16) | eth_type, the IP EtherType of the
flow. On a LAN->PPPoE-over-VLAN record INSERT_PPPoE_HDR (0x43) runs first and
the frame below the new tag is a PPPoE session frame, so the tag's inner
EtherType must be 0x8864. Vendor create_vlan_ins_hm() (cdx_ehash.c) takes
info->eth_type, which create_pppoe_ins_hm() has just set to ETHERTYPE_PPPOE.
Use the running l2_eth_type instead: it equals eth_type unless 0x43 ran, so
every existing VLAN record stays byte-identical.

Must run after F-261. Idempotent; exits non-zero unless the anchor matches
exactly once.
"""

import sys

MARK = "F-272"
PATH = "drivers/net/ethernet/freescale/fman/fman_pcd.c"

OLD = ("\t\t\t\t\tcpu_to_be32(((u32)vlan->push_tci << 16) |\n"
       "\t\t\t\t\t\t    eth_type);\n")
NEW = ("\t\t\t\t\tcpu_to_be32(((u32)vlan->push_tci << 16) |\n"
       "\t\t\t\t\t\t    l2_eth_type); /* F-272: 0x8864 after 0x43 */\n")

try:
    with open(PATH) as f:
        s = f.read()
except FileNotFoundError:
    print(f"### F-272: FATAL: {PATH} not found")
    sys.exit(1)

if MARK in s:
    print("### F-272: already applied")
    sys.exit(0)

n = s.count(OLD)
if n != 1:
    print(f"### F-272: FATAL: anchor in {PATH} found {n} times (expected 1)")
    sys.exit(1)

with open(PATH, "w") as f:
    f.write(s.replace(OLD, NEW))
print("### F-272: INSERT_VLAN_HDR inner EtherType follows INSERT_PPPoE_HDR")
