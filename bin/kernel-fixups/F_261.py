"""F-261 (T-M6-SP4 PPPoE encap record, 2026-10-08): teach the inline ehash
record emitter the vendor INSERT_PPPoE_HDR (0x43) opcode.

Vendor cdx_ehash.c fill_actions() builds a LAN->PPPoE flow as
  [05] 04 11 12 (front half) | NAT or TTL | 43 INSERT_PPPoE_HDR | [42] |
  41 INSERT_L2_HDR (EtherType 0x8864, dst = concentrator MAC) | 01
with create_pppoe_ins_hm(): en_ehash_insert_pppoe_hdr { be32 stats_ptr;
be32 (ver 1 << 28) | (type 1 << 24) | (code 0 << 16) | session_id }, and
info->eth_type = ETHERTYPE_PPPOE so the L2 rebuild writes 0x8864. The
microcode fills in the PPPoE length and the PPP protocol.

A new L2-edit flag FMAN_PCD_VLANF_PPPOE_INSERT (bit 3) takes the same front
half as F-260 (04 11 12, VID 0: the LAN ingress is untagged), emits 0x43
after TTL/NAT with the session ID from the new pppoe_sid field (threaded
through fman_pcd_fe_flow_action and fman_pcd_vlan_params), and switches the
INSERT_L2_HDR EtherType to 0x8864. The stats pointer is the owned 0216 scratch
block (never 0).

MTU: the ENQUEUE param mtu is the microcode's fragmentation threshold
(vendor en_ehash_enqueue_param: "mtu size in bytes for fragmentation",
EN_EHASH_DISABLE_FRAG = 0xffff), and ASK2 has no fragmentation buffer pool
(bpid 0). An encap record grows the frame by 8 bytes, so it writes 0xffff
(fragmentation disabled) instead of 1500, keeping oversize frames out of an
unconfigured fragmentation path. There is no hardware MTU enforcement (the
vendor does it with 05 PREEMPTIVE_CHECKS + a frag pool, neither of which ASK2
emits): an inner packet above the PPPoE MTU leaves oversize. ask.ko gates
encap separately (ask.pppoe_encap_offload, default off) and the plan
documents the MSS-clamp requirement.

Dormant until ask.ko sets the flag. 0x43 has never run on this board.
Must run after F-260. Idempotent; exits non-zero unless every anchor matches
the expected number of times.
"""

import sys

MARK = "F-261"

EDITS = [
    ("include/linux/fsl/fman_pcd.h",
     "#define FMAN_PCD_VLANF_PPPOE_STRIP\t(1u << 2)\n",
     "#define FMAN_PCD_VLANF_PPPOE_STRIP\t(1u << 2)\n"
     "/* F-261: insert an egress PPPoE session header (INSERT_PPPoE_HDR 0x43). */\n"
     "#define FMAN_PCD_VLANF_PPPOE_INSERT\t(1u << 3)\n", 1),
    ("include/linux/fsl/fman_pcd.h",
     "\tu16 push_tpid;\n};\n",
     "\tu16 push_tpid;\n"
     "\tu16 pppoe_sid;\t\t/* F-261: INSERT_PPPoE_HDR session, host order */\n"
     "};\n", 1),
    ("include/linux/fsl/fman_pcd.h",
     "\tu16  vlan_push_tpid;\t/* host order; outer EtherType, e.g. 0x8100 */\n",
     "\tu16  vlan_push_tpid;\t/* host order; outer EtherType, e.g. 0x8100 */\n"
     "\tu16  pppoe_sid;\t\t/* F-261: egress PPPoE session, host order */\n", 1),
    # Both F-260 front-half guards also run for a PPPoE insert.
    ("drivers/net/ethernet/freescale/fman/fman_pcd.c",
     "\t\t\t\t      FMAN_PCD_VLANF_PPPOE_STRIP))) { /* F-260 */\n",
     "\t\t\t\t      FMAN_PCD_VLANF_PPPOE_STRIP |\n"
     "\t\t\t\t      FMAN_PCD_VLANF_PPPOE_INSERT))) { /* F-260/F-261 */\n", 2),
    ("drivers/net/ethernet/freescale/fman/fman_pcd.c",
     "\t\t\tif (vlan && (vlan->flags & FMAN_PCD_VLANF_PUSH)) {\n",
     "\t\t\t/* F-261: vendor create_pppoe_ins_hm(): INSERT_PPPoE_HDR after\n"
     "\t\t\t * TTL/NAT, before any VLAN insert and the L2 rebuild, which then\n"
     "\t\t\t * carries EtherType 0x8864. Param: be32 stats_ptr (owned\n"
     "\t\t\t * scratch, never 0), be32 ver 1 | type 1 | code 0 | session. */\n"
     "\t\t\tif (vlan && (vlan->flags & FMAN_PCD_VLANF_PPPOE_INSERT)) {\n"
     "\t\t\t\tr[opc_off + oi++] = 0x43;\t/* INSERT_PPPoE_HDR */\n"
     "\t\t\t\t*(__be32 *)(r + l2poff + 0) =\n"
     "\t\t\t\t\tcpu_to_be32(t->pcd->fe_stats_off & 0x00ffffff);\n"
     "\t\t\t\t*(__be32 *)(r + l2poff + 4) =\n"
     "\t\t\t\t\tcpu_to_be32((1u << 28) | (1u << 24) |\n"
     "\t\t\t\t\t\t    vlan->pppoe_sid);\n"
     "\t\t\t\tl2poff += 8;\n"
     "\t\t\t\tl2_eth_type = 0x8864;\t/* ETH_P_PPP_SES */\n"
     "\t\t\t}\n"
     "\t\t\tif (vlan && (vlan->flags & FMAN_PCD_VLANF_PUSH)) {\n", 1),
    ("drivers/net/ethernet/freescale/fman/fman_pcd.c",
     "\t\t*(__be16 *)(r + enqueue_off + 0) = cpu_to_be16(1500);\n",
     "\t\t/* F-261: a PPPoE insert grows the frame by 8; with no frag pool\n"
     "\t\t * (bpid 0) disable fragmentation (EN_EHASH_DISABLE_FRAG). */\n"
     "\t\t*(__be16 *)(r + enqueue_off + 0) =\n"
     "\t\t\tcpu_to_be16(vlan && (vlan->flags & FMAN_PCD_VLANF_PPPOE_INSERT) ?\n"
     "\t\t\t\t    0xffff : 1500);\n", 1),
    ("drivers/net/ethernet/freescale/fman/fman_pcd.c",
     "\t\t\t.push_tpid = action->vlan_push_tpid,\n",
     "\t\t\t.push_tpid = action->vlan_push_tpid,\n"
     "\t\t\t.pppoe_sid = action->pppoe_sid,\t/* F-261 */\n", 1),
]

srcs = {}
for path, _, _, _ in EDITS:
    if path not in srcs:
        try:
            with open(path) as f:
                srcs[path] = f.read()
        except FileNotFoundError:
            print(f"### F-261: FATAL: {path} not found")
            sys.exit(1)

if all(MARK in s for s in srcs.values()):
    print("### F-261: already applied")
    sys.exit(0)

for path, old, new, want in EDITS:
    n = srcs[path].count(old)
    if n != want:
        print(f"### F-261: FATAL: anchor in {path} found {n} times (expected {want})")
        sys.exit(1)
    srcs[path] = srcs[path].replace(old, new)

for path, s in srcs.items():
    with open(path, "w") as f:
        f.write(s)
print("### F-261: ehash emitter INSERT_PPPoE_HDR (0x43) behind FMAN_PCD_VLANF_PPPOE_INSERT")
