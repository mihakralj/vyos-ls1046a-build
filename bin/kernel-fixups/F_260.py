"""F-260 (T-M6-SP4 PPPoE decap record, 2026-10-08): teach the inline ehash
record emitter the vendor STRIP_PPPoE_HDR (0x14) opcode.

Vendor cdx_ehash.c fill_actions() strips an ingress PPPoE session header with
STRIP_PPPoE_HDR after the L2/VLAN strip front half:
  05 PREEMPTIVE_CHECKS, 04 UPDATE_ETH_RX_STATS, 11 STRIP_ETH_HDR,
  12 STRIP_ALL_VLAN_HDRS (always emitted), 14 STRIP_PPPoE_HDR, NAT or TTL,
  41 INSERT_L2_HDR, 01 ENQUEUE_PKT
with param struct en_ehash_strip_pppoe_hdr { be32 stats_ptr } (opcode value
from the vendor 010-ask-fman-dpaa-ehash.patch table: STRIP_PPPoE_HDR 0x14).

ASK2 already emits that front half (04 11 12) for VLAN edits (0209/0215/0216,
silicon line rate). A new L2-edit flag FMAN_PCD_VLANF_PPPOE_STRIP (bit 2 of
the existing vlan_flags) makes the emitter take the same front half (12 with
VID 0: the PPPoE ingress is untagged) and then emit 0x14. Its stats pointer
points at the owned 0216 scratch block like 04 and 12 do: on untagged
ingress a zero stats word sent 0x12 down a bad microcode branch (2026-10-05),
so no strip opcode here gets a zero pointer. The parameter lands before the
TTL/NAT parameters (ttl_param_off), and vlan_oi includes the 0x14, so the
NAT rebuild (oi = vlan_oi) keeps it.

The record key already carries the PPPoE session ID (F-259), so a record can
only match frames of its session. Dormant until ask.ko sets the flag (it does
so only behind its pppoe_offload gate). 0x14 has never run on this board:
bring it up on one flow, cold-booted, the way 0215 was.

Must run after F-259 and the 0209/0215/0216 emitter patches. Idempotent;
exits non-zero unless every anchor matches exactly once.
"""

import sys

MARK = "F-260"

FRONT_OLD = ("\t\t\tif (vlan && (vlan->flags &\n"
             "\t\t\t\t     (FMAN_PCD_VLANF_POP | FMAN_PCD_VLANF_PUSH))) {\n")
FRONT_NEW = ("\t\t\tif (vlan && (vlan->flags &\n"
             "\t\t\t\t     (FMAN_PCD_VLANF_POP | FMAN_PCD_VLANF_PUSH |\n"
             "\t\t\t\t      FMAN_PCD_VLANF_PPPOE_STRIP))) { /* F-260 */\n")

EDITS = [
    ("include/linux/fsl/fman_pcd.h",
     "#define FMAN_PCD_VLANF_PUSH\t(1u << 1)\n",
     "#define FMAN_PCD_VLANF_PUSH\t(1u << 1)\n"
     "/* F-260: strip an ingress PPPoE session header (STRIP_PPPoE_HDR 0x14). */\n"
     "#define FMAN_PCD_VLANF_PPPOE_STRIP\t(1u << 2)\n", 1),
    # Both front-half guards (04 11, then 12) also run for a PPPoE strip.
    ("drivers/net/ethernet/freescale/fman/fman_pcd.c", FRONT_OLD, FRONT_NEW, 2),
    ("drivers/net/ethernet/freescale/fman/fman_pcd.c",
     "\t\t\tvlan_oi = oi;\t/* VLAN prefix opcodes (04 11 12), 0 if none */\n",
     "\t\t\t/* F-260: vendor STRIP_PPPoE_HDR after STRIP_ALL_VLAN, param\n"
     "\t\t\t * en_ehash_strip_pppoe_hdr { be32 stats_ptr } -> the owned\n"
     "\t\t\t * scratch block (never 0, see 0216). */\n"
     "\t\t\tif (vlan && (vlan->flags & FMAN_PCD_VLANF_PPPOE_STRIP)) {\n"
     "\t\t\t\tr[opc_off + oi++] = 0x14;\t/* STRIP_PPPoE_HDR */\n"
     "\t\t\t\t*(__be32 *)(r + ttl_param_off) =\n"
     "\t\t\t\t\tcpu_to_be32(t->pcd->fe_stats_off & 0x00ffffff);\n"
     "\t\t\t\tttl_param_off += 4;\n"
     "\t\t\t}\n"
     "\t\t\tvlan_oi = oi;\t/* prefix opcodes (04 11 12 [14]), 0 if none */\n", 1),
]

srcs = {}
for path, _, _, _ in EDITS:
    if path not in srcs:
        try:
            with open(path) as f:
                srcs[path] = f.read()
        except FileNotFoundError:
            print(f"### F-260: FATAL: {path} not found")
            sys.exit(1)

if all(MARK in s for s in srcs.values()):
    print("### F-260: already applied")
    sys.exit(0)

for path, old, new, want in EDITS:
    n = srcs[path].count(old)
    if n != want:
        print(f"### F-260: FATAL: anchor in {path} found {n} times (expected {want})")
        sys.exit(1)
    srcs[path] = srcs[path].replace(old, new)

for path, s in srcs.items():
    with open(path, "w") as f:
        f.write(s)
print("### F-260: ehash emitter STRIP_PPPoE_HDR (0x14) behind FMAN_PCD_VLANF_PPPOE_STRIP")
