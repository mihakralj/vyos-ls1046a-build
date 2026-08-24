"""F-233 (T-M6-8): FE-VM VLAN pop/push opcode emitter for the ehash HIT record.

Extends the routed/NAT FE opcode chain (F-198 INSERT_L2_HDR+ENQUEUE, F-200
UPDATE_TTL, F-226 UPDATE_HOPLIMIT, F-230 NAT) with the vendor VLAN opcodes so a
HIT can strip an ingress 802.1Q tag and/or insert one egress tag in silicon.
Vendor create/insert_remove_vlan_hm() opcode set, verified in fm_ehash.h:

    STRIP_ALL_VLAN_HDRS 0x12  param en_ehash_strip_all_vlan_hdrs  (12 B base)
                              (POP: removes ALL ingress tags; validate disabled)
    INSERT_VLAN_HDR     0x42  param en_ehash_insert_vlan_hdr      (4 B ctrl + 4/tag)
                              (PUSH: control word | u32 vlanhdr = (TCI<<16)|innerET)

Vendor emission order (fill_actions): PREEMPTIVE(skip) -> STRIP_ETH(skip) ->
STRIP_ALL_VLAN_HDRS(0x12) -> UPDATE_TTL/HOPLIMIT/NAT -> INSERT_VLAN_HDR(0x42) ->
INSERT_L2_HDR(0x41) -> ENQUEUE_PKT(0x01). Params pack sequentially in
opcode-emission order (no per-opcode offset fields), same rule F-198/F-200/F-230
use. When a tag is pushed, the outer TPID rides the INSERT_L2_HDR EtherType
(rolled to 0x8100) and the pushed word's low 16 bits carry the inner EtherType
(0x0800/0x86dd).

DESIGN — ZERO PERTURBATION OF SHIPPING PATHS: when vlan->flags == 0 the emitter
is not entered at all, so the routed and F-230 NAT records are BYTE-IDENTICAL to
today. Only when vlan->flags != 0 (default-off ask_vlan_offload gate) is a
self-contained, vendor-ordered linear builder taken; it re-emits any composed
NAT/TTL opcodes itself so it never depends on, and never disturbs, the proven
NAT branch. This trades a little duplication for guaranteed non-regression of
silicon-validated IPv4/IPv6/NAT while VLAN is unproven.

DORMANT / UNREACHABLE by default: ask.ko only populates the public
fman_pcd_fe_flow_action vlan_* fields when the ask_vlan_offload module param is
set (default 0); otherwise vlan_flags is 0 and this emitter is skipped.

UNPROVEN ON 210.10.1 SILICON: opcodes 0x12/0x42, the [TCI|EtherType] word
packing, the strip validating word, and hdr_xpnd_sz behaviour for pushed tags
have never been exercised. Do NOT advertise ASK_CAP_VLAN until S0-S4 pass.
Single 802.1Q tag only; 802.1ad and QinQ are rejected in ask.ko parse.

Count-gated, idempotent (marker "F-233"); hard-fail on drift. Runs after F-230.
"""

import sys

SRC = "drivers/net/ethernet/freescale/fman/fman_pcd.c"
HDR = "include/linux/fsl/fman_pcd.h"


def replace(path, desc, old, new):
    with open(path) as f:
        s = f.read()
    if new in s:
        print(f"### F-233: already applied ({desc})")
        return
    n = s.count(old)
    if n != 1:
        print(f"### F-233: FATAL: {desc}: expected 1 match, got {n}")
        sys.exit(1)
    with open(path, "w") as f:
        f.write(s.replace(old, new, 1))
    print(f"### F-233: {desc} applied")


# Guard: F-230 NAT block must be present (correct fixup order).
with open(SRC) as f:
    _src = f.read()
if "FMAN_EHASH_OPC_UPDATE_SPORT" not in _src:
    print("### F-233: FATAL: F-230 NAT block absent (F-230 must precede F-233)")
    sys.exit(1)
if "F-233" in _src:
    print("### F-233: already applied")
    sys.exit(0)

# ---- 1. VLAN params struct + action ABI fields (header) ---------------------
replace(
    HDR, "VLAN params struct + action fields",
    "\t__be16 nat_sport;\n"
    "\t__be16 nat_dport;\n"
    "};",
    "\t__be16 nat_sport;\n"
    "\t__be16 nat_dport;\n"
    "\t/* F-233 (T-M6-8): single-tag 802.1Q pop/push. vlan_flags==0 =>\n"
    "\t * no VLAN edit (record byte-identical to the routed/NAT path).\n"
    "\t * vlan_tci is the 16-bit TCI (PCP<<13|DEI<<12|VID) to push;\n"
    "\t * vlan_tpid is the outer EtherType (0x8100). */\n"
    "\tu8   vlan_flags;\n"
    "\t__be16 vlan_tci;\n"
    "\t__be16 vlan_tpid;\n"
    "};",
)
replace(
    HDR, "VLAN params struct definition",
    "struct fman_pcd_nat_params {\n"
    "\tu8     flags;\n"
    "\tbool   is_v6;\n"
    "\tu8     sip[16];\n"
    "\tu8     dip[16];\n"
    "\t__be16 sport;\n"
    "\t__be16 dport;\n"
    "};",
    "struct fman_pcd_nat_params {\n"
    "\tu8     flags;\n"
    "\tbool   is_v6;\n"
    "\tu8     sip[16];\n"
    "\tu8     dip[16];\n"
    "\t__be16 sport;\n"
    "\t__be16 dport;\n"
    "};\n"
    "\n"
    "/* F-233: VLAN edit parameters threaded into the ehash record emitter.\n"
    " * flags bits mirror ask.ko ASK_VLANF_*: POP=1, PUSH=2. */\n"
    "#define FMAN_PCD_VLANF_POP\t(1u << 0)\n"
    "#define FMAN_PCD_VLANF_PUSH\t(1u << 1)\n"
    "struct fman_pcd_vlan_params {\n"
    "\tu8     flags;\n"
    "\t__be16 push_tci;\n"
    "\t__be16 push_tpid;\n"
    "};",
)

# ---- 2. add_key signature (real fn + forward decl): trailing vlan param -----
# Definition ends in "{", forward declaration in ";" — keep each exact/count=1.
replace(
    SRC, "ehash_add_key definition +vlan",
    "\t\t\t\t  u16 eth_type,\n"
    "\t\t\t\t  const struct fman_pcd_nat_params *nat)\n"
    "{",
    "\t\t\t\t  u16 eth_type,\n"
    "\t\t\t\t  const struct fman_pcd_nat_params *nat,\n"
    "\t\t\t\t  const struct fman_pcd_vlan_params *vlan)\n"
    "{",
)
# The forward declaration (ends ";" not "{") — separate anchor.
replace(
    SRC, "ehash_add_key forward decl +vlan",
    "\t\t\t\t  u16 eth_type,\n"
    "\t\t\t\t  const struct fman_pcd_nat_params *nat);",
    "\t\t\t\t  u16 eth_type,\n"
    "\t\t\t\t  const struct fman_pcd_nat_params *nat,\n"
    "\t\t\t\t  const struct fman_pcd_vlan_params *vlan);",
)

# ---- 3. Debugfs natwrap: pass NULL vlan to the real function ----------------
replace(
    SRC, "debugfs natwrap NULL vlan",
    "\treturn fman_pcd_ehash_add_key(t, key, key_size, enq_off, fqid, stats,\n"
    "\t\t\t\t      l2_dst, l2_src, eth_type, NULL);\n",
    "\treturn fman_pcd_ehash_add_key(t, key, key_size, enq_off, fqid, stats,\n"
    "\t\t\t\t      l2_dst, l2_src, eth_type, NULL, NULL);\n",
)

# ---- 4. Convert the NAT `if` into `else if` under a new VLAN branch ---------
# The VLAN branch is a self-contained vendor-ordered linear builder taken only
# when vlan->flags is set; it re-emits composed NAT/TTL so it never touches the
# proven NAT branch. When vlan->flags==0 the existing NAT/routed code runs
# unchanged (byte-identical).
replace(
    SRC, "VLAN emitter branch before INSERT_L2_HDR",
    "\t\t\tif (nat && nat->flags) {\n"
    "\t\t\t\t#define FMAN_EHASH_OPC_UPDATE_SIP_V4\t0x22\n",
    "\t\t\t/* F-233 (T-M6-8): vendor VLAN opcodes. DORMANT unless\n"
    "\t\t\t * vlan->flags; UNPROVEN on 210.10.1. Self-contained linear\n"
    "\t\t\t * builder in vendor order: STRIP_ALL_VLAN_HDRS(0x12) ->\n"
    "\t\t\t * [ports] -> [fused L3 / TTL] -> INSERT_VLAN_HDR(0x42) ->\n"
    "\t\t\t * (shared INSERT_L2_HDR/ENQUEUE). Re-emits any composed NAT so\n"
    "\t\t\t * the proven F-230 branch below is never entered for VLAN\n"
    "\t\t\t * flows and stays byte-identical when vlan->flags==0. */\n"
    "\t\t\tif (vlan && vlan->flags) {\n"
    "\t\t\t\t#define FMAN_EHASH_OPC_STRIP_ALL_VLAN\t0x12\n"
    "\t\t\t\t#define FMAN_EHASH_OPC_INSERT_VLAN_HDR\t0x42\n"
    "\t\t\t\t#define FMAN_EHASH_OPC_UPDATE_SIP_V4\t0x22\n"
    "\t\t\t\t#define FMAN_EHASH_OPC_UPDATE_DIP_V4\t0x24\n"
    "\t\t\t\t#define FMAN_EHASH_OPC_UPDATE_SIP_V6\t0x2a\n"
    "\t\t\t\t#define FMAN_EHASH_OPC_UPDATE_DIP_V6\t0x2c\n"
    "\t\t\t\t#define FMAN_EHASH_OPC_UPDATE_SPORT\t0x31\n"
    "\t\t\t\t#define FMAN_EHASH_OPC_UPDATE_DPORT\t0x32\n"
    "\t\t\t\tbool pv6 = (eth_type == 0x86dd);\n"
    "\t\t\t\tu8 port_opc = 0;\n"
    "\t\t\t\tu8 l3_opc;\n"
    "\n"
    "\t\t\t\toi = 0;\n"
    "\t\t\t\tl2poff = param_off;\n"
    "\n"
    "\t\t\t\t/* POP: strip all ingress VLAN tags. en_ehash_strip_all_vlan_hdrs\n"
    "\t\t\t\t * base = 12 B: u16 vlan_id[2] (bytes 0-3), u32 word (bytes 4-7,\n"
    "\t\t\t\t * ifstats disabled => 0), u8 op_flags (byte 8), pad. We set\n"
    "\t\t\t\t * op_flags = OP_SKIP_VLAN_VALIDATE (0x01): the classification key\n"
    "\t\t\t\t * excludes VLAN TCI, so the strip must NOT VID-match (a zeroed\n"
    "\t\t\t\t * vlan_id would otherwise reject every non-zero ingress VID). */\n"
    "\t\t\t\tif (vlan->flags & FMAN_PCD_VLANF_POP) {\n"
    "\t\t\t\t\t#define FMAN_EHASH_OP_SKIP_VLAN_VALIDATE\t0x01\n"
    "\t\t\t\t\tr[opc_off + oi++] = FMAN_EHASH_OPC_STRIP_ALL_VLAN;\n"
    "\t\t\t\t\tmemset(r + l2poff, 0, 12);\n"
    "\t\t\t\t\tr[l2poff + 8] = FMAN_EHASH_OP_SKIP_VLAN_VALIDATE;\n"
    "\t\t\t\t\tl2poff += 12;\n"
    "\t\t\t\t}\n"
    "\n"
    "\t\t\t\t/* Composed NAT ports (fused), if any. */\n"
    "\t\t\t\tif (nat && (nat->flags & FMAN_PCD_NATF_SPAT))\n"
    "\t\t\t\t\tport_opc |= FMAN_EHASH_OPC_UPDATE_SPORT;\n"
    "\t\t\t\tif (nat && (nat->flags & FMAN_PCD_NATF_DPAT))\n"
    "\t\t\t\t\tport_opc |= FMAN_EHASH_OPC_UPDATE_DPORT;\n"
    "\t\t\t\tif (port_opc) {\n"
    "\t\t\t\t\tr[opc_off + oi++] = port_opc;\n"
    "\t\t\t\t\t*(__be16 *)(r + l2poff + 0) = nat->dport;\n"
    "\t\t\t\t\t*(__be16 *)(r + l2poff + 2) = nat->sport;\n"
    "\t\t\t\t\tl2poff += 4;\n"
    "\t\t\t\t}\n"
    "\n"
    "\t\t\t\t/* Fused L3: TTL/HOPLIMIT (always, routed) plus any NAT\n"
    "\t\t\t\t * address rewrites. DSCP param first. */\n"
    "\t\t\t\tl3_opc = pv6 ? FMAN_EHASH_OPC_UPDATE_HOPLIMIT :\n"
    "\t\t\t\t\t\t  FMAN_EHASH_OPC_UPDATE_TTL;\n"
    "\t\t\t\tif (nat && (nat->flags & FMAN_PCD_NATF_SNAT))\n"
    "\t\t\t\t\tl3_opc |= pv6 ? FMAN_EHASH_OPC_UPDATE_SIP_V6 :\n"
    "\t\t\t\t\t\t       FMAN_EHASH_OPC_UPDATE_SIP_V4;\n"
    "\t\t\t\tif (nat && (nat->flags & FMAN_PCD_NATF_DNAT))\n"
    "\t\t\t\t\tl3_opc |= pv6 ? FMAN_EHASH_OPC_UPDATE_DIP_V6 :\n"
    "\t\t\t\t\t\t       FMAN_EHASH_OPC_UPDATE_DIP_V4;\n"
    "\t\t\t\tr[opc_off + oi++] = l3_opc;\n"
    "\t\t\t\t*(__be32 *)(r + l2poff) = cpu_to_be32(0); /* DSCP */\n"
    "\t\t\t\tl2poff += 4;\n"
    "\t\t\t\tif (nat && (nat->flags & FMAN_PCD_NATF_SNAT)) {\n"
    "\t\t\t\t\tmemcpy(r + l2poff, nat->sip, pv6 ? 16 : 4);\n"
    "\t\t\t\t\tl2poff += pv6 ? 16 : 4;\n"
    "\t\t\t\t}\n"
    "\t\t\t\tif (nat && (nat->flags & FMAN_PCD_NATF_DNAT)) {\n"
    "\t\t\t\t\tmemcpy(r + l2poff, nat->dip, pv6 ? 16 : 4);\n"
    "\t\t\t\t\tl2poff += pv6 ? 16 : 4;\n"
    "\t\t\t\t}\n"
    "\n"
    "\t\t\t\t/* PUSH: insert one egress 802.1Q tag. Control word =\n"
    "\t\t\t\t * num_hdrs(1)<<24; one vlanhdr word = (TCI<<16)|innerET.\n"
    "\t\t\t\t * The outer TPID is carried by the INSERT_L2_HDR EtherType\n"
    "\t\t\t\t * (rolled to vlan_tpid below). */\n"
    "\t\t\t\tif (vlan->flags & FMAN_PCD_VLANF_PUSH) {\n"
    "\t\t\t\t\tu16 inner_et = eth_type;\n"
    "\t\t\t\t\tu16 tci = be16_to_cpu(vlan->push_tci);\n"
    "\n"
    "\t\t\t\t\tr[opc_off + oi++] = FMAN_EHASH_OPC_INSERT_VLAN_HDR;\n"
    "\t\t\t\t\t*(__be32 *)(r + l2poff + 0) =\n"
    "\t\t\t\t\t\tcpu_to_be32(1u << 24);\n"
    "\t\t\t\t\t*(__be32 *)(r + l2poff + 4) =\n"
    "\t\t\t\t\t\tcpu_to_be32(((u32)tci << 16) | inner_et);\n"
    "\t\t\t\t\tl2poff += 8;\n"
    "\t\t\t\t\t/* Outer L2 EtherType becomes the tag TPID. */\n"
    "\t\t\t\t\teth_type = be16_to_cpu(vlan->push_tpid);\n"
    "\t\t\t\t}\n"
    "\t\t\t} else if (nat && nat->flags) {\n"
    "\t\t\t\t#define FMAN_EHASH_OPC_UPDATE_SIP_V4\t0x22\n",
)

# ---- 5. Production caller: build _vlan and pass it --------------------------
replace(
    SRC, "production add_key caller +vlan build",
    "\t\tmemcpy(_nat.sip, action->nat_sip, 16);\n"
    "\t\tmemcpy(_nat.dip, action->nat_dip, 16);\n"
    "\t\terr = fman_pcd_ehash_add_key(t, action->key, action->key_size,\n",
    "\t\tstruct fman_pcd_vlan_params _vlan = {\n"
    "\t\t\t.flags     = action->vlan_flags,\n"
    "\t\t\t.push_tci  = action->vlan_tci,\n"
    "\t\t\t.push_tpid = action->vlan_tpid,\n"
    "\t\t};\n"
    "\n"
    "\t\tmemcpy(_nat.sip, action->nat_sip, 16);\n"
    "\t\tmemcpy(_nat.dip, action->nat_dip, 16);\n"
    "\t\terr = fman_pcd_ehash_add_key(t, action->key, action->key_size,\n",
)
replace(
    SRC, "production add_key trailing arg +vlan",
    "\t\t\t\t\t\t l2_dst, l2_src, l2_eth,\n"
    "\t\t\t\t\t\t &_nat);\n",
    "\t\t\t\t\t\t l2_dst, l2_src, l2_eth,\n"
    "\t\t\t\t\t\t &_nat, &_vlan);\n",
)

print("### F-233: FE-VM VLAN pop/push opcode emitter applied (dormant unless vlan->flags)")
