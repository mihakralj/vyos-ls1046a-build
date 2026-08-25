"""F-233 (T-M6-8): FE-VM VLAN pop/push opcode emitter for the ehash HIT record.

Extends the routed/NAT FE opcode chain (F-198 INSERT_L2_HDR+ENQUEUE, F-200
UPDATE_TTL, F-226 UPDATE_HOPLIMIT, F-230 NAT) with the vendor VLAN opcodes so a
HIT can strip an ingress 802.1Q tag and/or insert one egress tag in silicon.
Vendor create/insert_remove_vlan_hm() opcode set, verified in fm_ehash.h:

    STRIP_ALL_VLAN_HDRS 0x12  param en_ehash_strip_all_vlan_hdrs  (12 B base)
                              (POP: vlan_id[0]=ingress VID + op_flags=0 VALIDATE)
    INSERT_VLAN_HDR     0x42  param en_ehash_insert_vlan_hdr      (4 B ctrl + 4/tag)
                              (PUSH: control word | u32 vlanhdr = (TCI<<16)|innerET)

VENDOR-FAITHFUL to fill_bridge_actions() (the VLAN forwarding generator,
cdx_ehash.c:1196-1299), NOT fill_actions() (routed/QoS). Byte-diff 2026-08-25
established fill_bridge_actions is the correct model and it does NOT emit
PREEMPTIVE_CHECKS(0x05). Emission order:
STRIP_ETH_HDR(0x11) -> STRIP_ALL_VLAN_HDRS(0x12) -> UPDATE_TTL/HOPLIMIT/NAT ->
INSERT_VLAN_HDR(0x42) -> INSERT_L2_HDR(0x41) -> ENQUEUE_PKT(0x01).

STRIP_ETH_HDR(0x11) is MANDATORY on every VLAN flow (vendor gates it on
rebuild_l2_hdr = any VLAN present/pushed). Two 2026-08-25 corrections vs the
first (still-broken) VLAN builds, both from the vendor byte-diff:
  1. STRIP_ALL_VLAN param: write the REAL ingress VID into vlan_id[0] (be16) and
     leave op_flags=0 (VALIDATE) on the POP direction, exactly as
     insert_remove_vlan_hm() does for a routed tagged flow (cdx_ehash.c:
     2004-2020). The earlier zero-VID + OP_SKIP_VLAN_VALIDATE left the 0x12
     strip's parse geometry inconsistent and silently dropped BULK frames
     (POP-bulk=0 even though POP shrinks the frame — which falsified every
     bpid/hdr_xpnd_sz/headroom theory). SKIP is kept only for the pure-PUSH
     (untagged ingress) case where there is no ingress tag to validate.
  2. Do NOT emit PREEMPTIVE_CHECKS(0x05): fill_bridge_actions never does; only
     the routed fill_actions() emits+seals it. Dropping it keeps the record
     byte-faithful to the vendor VLAN path.
Params pack sequentially in opcode-emission order (no per-opcode offset fields),
same rule F-198/F-200/F-230 use. When a tag is pushed, the outer TPID rides the
INSERT_L2_HDR EtherType (rolled to 0x8100) and the pushed word's low 16 bits
carry the inner EtherType (0x0800/0x86dd).

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
    "\t * vlan_tpid is the outer EtherType (0x8100). vlan_ingress_vid is the\n"
    "\t * VID (host order) the STRIP_ALL_VLAN opcode validates on POP. */\n"
    "\tu8   vlan_flags;\n"
    "\t__be16 vlan_tci;\n"
    "\t__be16 vlan_tpid;\n"
    "\tu16  vlan_ingress_vid;\n"
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
    "\tu16    ingress_vid;\t/* host order; STRIP_ALL_VLAN validate VID */\n"
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
    "\t\t\t\t#define FMAN_EHASH_OPC_STRIP_ETH_HDR\t0x11\n"
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
    "\t\t\t\t/* Vendor fill_bridge_actions() (the VLAN forwarding\n"
    "\t\t\t\t * generator) starts with STRIP_ETH_HDR and emits NO\n"
    "\t\t\t\t * PREEMPTIVE_CHECKS(0x05); only fill_actions()'s routed/QoS\n"
    "\t\t\t\t * path emits and seals 0x05. Keep this record byte-faithful to\n"
    "\t\t\t\t * the vendor VLAN path. */\n"
    "\n"
    "\t\t\t\t/* STRIP_ETH_HDR (opcode-only, no param): mandatory on any\n"
    "\t\t\t\t * VLAN (L2-manipulating) flow. Vendor cdx_ehash.c fill_actions()\n"
    "\t\t\t\t * sets rebuild_l2_hdr=1 for L2_HDR_OPS (any vlan present/pushed)\n"
    "\t\t\t\t * and emits STRIP_ETH_HDR(0x11) BEFORE STRIP_ALL_VLAN_HDRS(0x12)\n"
    "\t\t\t\t * and INSERT_VLAN_HDR(0x42). Without it the FE-VM VLAN opcodes are\n"
    "\t\t\t\t * present but INERT for forwarding (records correct, 0 throughput,\n"
    "\t\t\t\t * plain-routed fine) because the ingress Ethernet header is never\n"
    "\t\t\t\t * torn down so the tag pop/push has no valid L2 geometry to act on.\n"
    "\t\t\t\t * The trailing INSERT_L2_HDR(0x41) rebuilds the full L2 header. */\n"
    "\t\t\t\tr[opc_off + oi++] = FMAN_EHASH_OPC_STRIP_ETH_HDR;\n"
    "\n"
    "\t\t\t\t/* STRIP_ALL_VLAN_HDRS(0x12) is MANDATORY on EVERY L2-rebuild flow\n"
    "\t\t\t\t * (POP *and* PUSH), matching the vendor's unconditional\n"
    "\t\t\t\t * insert_remove_vlan_hm() (cdx_ehash.c:729 fill_actions, :1277\n"
    "\t\t\t\t * fill_bridge_actions; the vendor comment: 'strip vlan hdrs is\n"
    "\t\t\t\t * called mandatorily to validate the vlan ids ... also to strip\n"
    "\t\t\t\t * the vlan header for vlan-0 packets'). After STRIP_ETH_HDR(0x11)\n"
    "\t\t\t\t * tears down the L2 header, 0x12 NORMALIZES/validates the VLAN\n"
    "\t\t\t\t * layer of the internal parse context so INSERT_VLAN/INSERT_L2\n"
    "\t\t\t\t * rebuild onto a consistent parse state. OMITTING it on the PUSH\n"
    "\t\t\t\t * direction (untagged ingress) left a STALE parse result -> the\n"
    "\t\t\t\t * hard parser rejected the rebuilt frame with\n"
    "\t\t\t\t * FM_FD_ERR_PRS_HDR_ERR(0x20)|EXTRACTION(0x8000) = 0x8020 on the\n"
    "\t\t\t\t * PUSH ingress port and bulk forwarding stalled to 0 (2026-08-25).\n"
    "\t\t\t\t *\n"
    "\t\t\t\t * en_ehash_strip_all_vlan_hdrs = 12 B: u16 vlan_id[2] (0-3), u32\n"
    "\t\t\t\t * word (4-7, ifstats off => 0), u8 op_flags (8).\n"
    "\t\t\t\t *\n"
    "\t\t\t\t * op_flags + vlan_id (VENDOR-FAITHFUL, 2026-08-25): the vendor\n"
    "\t\t\t\t * insert_remove_vlan_hm() writes the REAL ingress VID(s) into\n"
    "\t\t\t\t * param->vlan_id[i] (outer-first, be16) whenever ingress tags are\n"
    "\t\t\t\t * present (cdx_ehash.c:2004-2012) and leaves op_flags=0 (VALIDATE)\n"
    "\t\t\t\t * for a ROUTED tagged flow; it sets OP_SKIP_VLAN_VALIDATE ONLY for\n"
    "\t\t\t\t * a BRIDGE flow whose ingress has NO tag (:2016-2020). The 0x12\n"
    "\t\t\t\t * microcode uses vlan_id[] to know which tag(s) to validate/strip,\n"
    "\t\t\t\t * so a zeroed VID leaves the strip's parse geometry inconsistent\n"
    "\t\t\t\t * and silently drops bulk frames (the 2026-08-25 POP-bulk=0 /\n"
    "\t\t\t\t * PUSH-data-drop signature — falsified the earlier bpid/hdr_xpnd\n"
    "\t\t\t\t * theories since POP shrinks the frame).\n"
    "\t\t\t\t *   - POP (tagged ingress): vlan_id[0] = ingress VID, op_flags=0\n"
    "\t\t\t\t *     => validate & strip the real tag. vlan->ingress_vid is\n"
    "\t\t\t\t *     sourced from the ingress VLAN vif by ask.ko.\n"
    "\t\t\t\t *   - PUSH-only (untagged ingress, no VID): vlan_id=0 +\n"
    "\t\t\t\t *     OP_SKIP_VLAN_VALIDATE so the strip does not reject the\n"
    "\t\t\t\t *     untagged frame (there is no ingress tag to validate).\n"
    "\t\t\t\t * If a POP flow somehow arrives with ingress_vid==0 (no vif VID\n"
    "\t\t\t\t * resolved) fall back to SKIP rather than validate-against-zero. */\n"
    "\t\t\t\t#define FMAN_EHASH_OP_SKIP_VLAN_VALIDATE\t0x01\n"
    "\t\t\t\tr[opc_off + oi++] = FMAN_EHASH_OPC_STRIP_ALL_VLAN;\n"
    "\t\t\t\tmemset(r + l2poff, 0, 12);\n"
    "\t\t\t\tif ((vlan->flags & FMAN_PCD_VLANF_POP) &&\n"
    "\t\t\t\t    vlan->ingress_vid) {\n"
    "\t\t\t\t\t/* validate & strip the real ingress tag */\n"
    "\t\t\t\t\t*(__be16 *)(r + l2poff + 0) =\n"
    "\t\t\t\t\t\tcpu_to_be16(vlan->ingress_vid);\n"
    "\t\t\t\t} else {\n"
    "\t\t\t\t\t/* untagged ingress (pure PUSH) or unresolved VID */\n"
    "\t\t\t\t\tr[l2poff + 8] = FMAN_EHASH_OP_SKIP_VLAN_VALIDATE;\n"
    "\t\t\t\t}\n"
    "\t\t\t\tl2poff += 12;\n"
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
    "\t\t\t.flags       = action->vlan_flags,\n"
    "\t\t\t.push_tci    = action->vlan_tci,\n"
    "\t\t\t.push_tpid   = action->vlan_tpid,\n"
    "\t\t\t.ingress_vid = action->vlan_ingress_vid,\n"
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
