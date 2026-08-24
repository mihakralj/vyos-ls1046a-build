"""F-230 (T-M6-7.1): FE-VM NAT/PAT opcode emitter for the ehash HIT record.

Extends the routed-forward FE opcode chain (F-198 INSERT_L2_HDR+ENQUEUE,
F-200 UPDATE_TTL, F-226 UPDATE_HOPLIMIT) with the in-place L3/L4 rewrite
opcodes so a HIT can perform SNAT/DNAT/NAPT in silicon. Vendor create_nat_hm()
(cdx_ehash.c) opcode set, verified in fm_ehash.h:

    UPDATE_SIP_V4  0x22  param en_ehash_update_ipv4_ip {be32 ip}          (4 B)
    UPDATE_DIP_V4  0x24  param en_ehash_update_ipv4_ip {be32 ip}          (4 B)
    UPDATE_SIP_V6  0x2A  param en_ehash_update_ipv6_ip {u8 ip[16]}        (16 B)
    UPDATE_DIP_V6  0x2C  param en_ehash_update_ipv6_ip {u8 ip[16]}        (16 B)
    UPDATE_SPORT   0x31  } shared en_ehash_update_port {be16 dport; be16  (4 B)
    UPDATE_DPORT   0x32  }        sport}  -- DPORT FIRST in the struct

Emission order (vendor fill_actions / create_nat_hm): after UPDATE_TTL/
UPDATE_HOPLIMIT and before INSERT_L2_HDR. Params pack sequentially in
opcode-emission order (no per-opcode offset fields), same rule F-198/F-200 use.
Silicon auto-recomputes IP and L4 checksums as a side effect of these opcodes;
there is no separate checksum opcode.

DORMANT / UNREACHABLE: the emitter only writes NAT opcodes when the caller
passes a non-NULL nat params block with flags != 0. The T-M6-7.0 ask.ko code
stores NAT intent in ask_flow_key but does NOT copy it into the public
fman_pcd_fe_flow_action NAT fields yet; that struct is zero-initialized, so
nat_flags is always 0 in every production call. The production record is
therefore BYTE-IDENTICAL to F-200/F-226 today. The separate arming increment
will add ask.nat_offload (default 0), lift the preflight gate only when set,
and populate action.nat_* immediately before the S0/S1 silicon session. This
fixup adds only the dormant mechanism; S0..S3 prove it before advertising.

UNPROVEN ON 210.10.1 SILICON: opcodes 0x22/0x24/0x2A/0x2C/0x31/0x32 have never
been exercised; the fused-opcode OR encoding and auto-checksum are assumed from
vendor source, not measured. Do NOT advertise NAT capability until S1..S3 pass.

Count-gated, idempotent (marker "F-230"); hard-fail on drift. Runs after F-226.
"""

import sys

SRC = "drivers/net/ethernet/freescale/fman/fman_pcd.c"
HDR = "include/linux/fsl/fman_pcd.h"


def replace(path, desc, old, new):
    with open(path) as f:
        s = f.read()
    if new in s:
        print(f"### F-230: already applied ({desc})")
        return
    n = s.count(old)
    if n != 1:
        print(f"### F-230: FATAL: {desc}: expected 1 match, got {n}")
        sys.exit(1)
    with open(path, "w") as f:
        f.write(s.replace(old, new, 1))
    print(f"### F-230: {desc} applied")


# Guard: F-200 UPDATE_TTL block must be present (correct fixup order).
with open(SRC) as f:
    _src = f.read()
if "FMAN_EHASH_OPC_UPDATE_TTL" not in _src:
    print("### F-230: FATAL: F-200 UPDATE_TTL block absent (F-200 must precede F-230)")
    sys.exit(1)
if "fman_pcd_ehash_add_key" in _src and "F-230" in _src:
    print("### F-230: already applied")
    sys.exit(0)

# ---- 1. NAT params struct + action ABI fields (header) ----------------------
replace(
    HDR, "NAT params struct + action fields",
    "\tu8   table_idx;\n"
    "\tu8   _rsvd_204[3];\n"
    "};",
    "\tu8   table_idx;\n"
    "\tu8   _rsvd_204[3];\n"
    "\t/* F-230 (T-M6-7.1): NAT/PAT rewrite. nat_flags==0 => no NAT (record\n"
    "\t * byte-identical to the plain routed path). Addresses are network\n"
    "\t * byte order; v4 uses the low 4 bytes of nat_sip/dip. */\n"
    "\tu8   nat_flags;\n"
    "\tu8   nat_sip[16];\n"
    "\tu8   nat_dip[16];\n"
    "\t__be16 nat_sport;\n"
    "\t__be16 nat_dport;\n"
    "};\n"
    "\n"
    "/* F-230: NAT rewrite parameters threaded into the ehash record emitter.\n"
    " * flags bits mirror ask.ko ASK_NATF_*: SNAT=1, DNAT=2, SPAT=4, DPAT=8. */\n"
    "#define FMAN_PCD_NATF_SNAT\t(1u << 0)\n"
    "#define FMAN_PCD_NATF_DNAT\t(1u << 1)\n"
    "#define FMAN_PCD_NATF_SPAT\t(1u << 2)\n"
    "#define FMAN_PCD_NATF_DPAT\t(1u << 3)\n"
    "struct fman_pcd_nat_params {\n"
    "\tu8     flags;\n"
    "\tbool   is_v6;\n"
    "\tu8     sip[16];\n"
    "\tu8     dip[16];\n"
    "\t__be16 sport;\n"
    "\t__be16 dport;\n"
    "};",
)

# ---- 2. add_key signature: trailing NAT params pointer ----------------------
replace(
    SRC, "ehash_add_key signature +nat",
    "\t\t\t\t  u32 enq_off, u32 fqid, bool stats,\n"
    "\t\t\t\t  const u8 *l2_dst, const u8 *l2_src,\n"
    "\t\t\t\t  u16 eth_type)\n"
    "{",
    "\t\t\t\t  u32 enq_off, u32 fqid, bool stats,\n"
    "\t\t\t\t  const u8 *l2_dst, const u8 *l2_src,\n"
    "\t\t\t\t  u16 eth_type,\n"
    "\t\t\t\t  const struct fman_pcd_nat_params *nat)\n"
    "{",
)

# ---- 3. Emit NAT opcodes between UPDATE_TTL/HOPLIMIT and INSERT_L2_HDR -------
# The l2poff cursor already points just past the TTL/hoplimit DSCP param (or at
# param_off when neither was emitted). Insert NAT opcodes+params there, then
# advance l2poff past them so INSERT_L2_HDR/ENQUEUE params follow contiguously.
replace(
    SRC, "NAT opcode emission",
    "\t\t\tr[opc_off + oi++] = FMAN_EHASH_OPC_INSERT_L2_HDR;\n"
    "\t\t\tr[opc_off + oi++] = FMAN_EHASH_OPC_ENQUEUE_PKT;\n"
    "\t\t\t*(__be32 *)(r + l2poff + 0) = cpu_to_be32(0x4000000e);\n",
    "\t\t\t/* F-230 (T-M6-7.1): vendor create_nat_hm encoding.\n"
    "\t\t\t * NAT opcodes are BIT-FUSED (OR'd into one byte per operation\n"
    "\t\t\t * group), not emitted as separate bytes:\n"
    "\t\t\t *   ports: UPDATE_SPORT(0x31)|UPDATE_DPORT(0x32)\n"
    "\t\t\t *   L3:    TTL/HOPLIMIT | UPDATE_SIP | UPDATE_DIP\n"
    "\t\t\t * Opcode order is ports first, then fused L3, then L2+enqueue.\n"
    "\t\t\t * Params follow that same order: {dport,sport}, DSCP(4),\n"
    "\t\t\t * SIP, DIP, L2, enqueue. Silicon auto-fixes IP+L4 checksums.\n"
    "\t\t\t * DORMANT unless nat->flags != 0; UNPROVEN on 210.10.1. */\n"
    "\t\t\tif (nat && nat->flags) {\n"
    "\t\t\t\t#define FMAN_EHASH_OPC_UPDATE_SIP_V4\t0x22\n"
    "\t\t\t\t#define FMAN_EHASH_OPC_UPDATE_DIP_V4\t0x24\n"
    "\t\t\t\t#define FMAN_EHASH_OPC_UPDATE_SIP_V6\t0x2a\n"
    "\t\t\t\t#define FMAN_EHASH_OPC_UPDATE_DIP_V6\t0x2c\n"
    "\t\t\t\t#define FMAN_EHASH_OPC_UPDATE_SPORT\t0x31\n"
    "\t\t\t\t#define FMAN_EHASH_OPC_UPDATE_DPORT\t0x32\n"
    "\t\t\t\tu8 port_opc = 0;\n"
    "\t\t\t\tu8 l3_opc;\n"
    "\t\t\t\tbool pv6 = nat->is_v6;\n"
    "\n"
    "\t\t\t\t/* Rebuild the opcode list + params from param_off in the\n"
    "\t\t\t\t * vendor NAT order. The no-NAT branch below never executes\n"
    "\t\t\t\t * this reset, preserving F-200/F-226 byte identity. */\n"
    "\t\t\t\toi = 0;\n"
    "\t\t\t\tl2poff = param_off;\n"
    "\t\t\t\tif (nat->flags & FMAN_PCD_NATF_SPAT)\n"
    "\t\t\t\t\tport_opc |= FMAN_EHASH_OPC_UPDATE_SPORT;\n"
    "\t\t\t\tif (nat->flags & FMAN_PCD_NATF_DPAT)\n"
    "\t\t\t\t\tport_opc |= FMAN_EHASH_OPC_UPDATE_DPORT;\n"
    "\t\t\t\tif (port_opc) {\n"
    "\t\t\t\t\tr[opc_off + oi++] = port_opc;\n"
    "\t\t\t\t\t*(__be16 *)(r + l2poff + 0) = nat->dport;\n"
    "\t\t\t\t\t*(__be16 *)(r + l2poff + 2) = nat->sport;\n"
    "\t\t\t\t\tl2poff += 4;\n"
    "\t\t\t\t}\n"
    "\n"
    "\t\t\t\t/* One fused L3 opcode: TTL/hoplimit plus selected addr\n"
    "\t\t\t\t * rewrites. DSCP param always first for TTL/hoplimit. */\n"
    "\t\t\t\tl3_opc = pv6 ? FMAN_EHASH_OPC_UPDATE_HOPLIMIT :\n"
    "\t\t\t\t\t\t  FMAN_EHASH_OPC_UPDATE_TTL;\n"
    "\t\t\t\tif (nat->flags & FMAN_PCD_NATF_SNAT)\n"
    "\t\t\t\t\tl3_opc |= pv6 ? FMAN_EHASH_OPC_UPDATE_SIP_V6 :\n"
    "\t\t\t\t\t\t       FMAN_EHASH_OPC_UPDATE_SIP_V4;\n"
    "\t\t\t\tif (nat->flags & FMAN_PCD_NATF_DNAT)\n"
    "\t\t\t\t\tl3_opc |= pv6 ? FMAN_EHASH_OPC_UPDATE_DIP_V6 :\n"
    "\t\t\t\t\t\t       FMAN_EHASH_OPC_UPDATE_DIP_V4;\n"
    "\t\t\t\tr[opc_off + oi++] = l3_opc;\n"
    "\t\t\t\t*(__be32 *)(r + l2poff) = cpu_to_be32(0); /* DSCP */\n"
    "\t\t\t\tl2poff += 4;\n"
    "\t\t\t\tif (nat->flags & FMAN_PCD_NATF_SNAT) {\n"
    "\t\t\t\t\tmemcpy(r + l2poff, nat->sip, pv6 ? 16 : 4);\n"
    "\t\t\t\t\tl2poff += pv6 ? 16 : 4;\n"
    "\t\t\t\t}\n"
    "\t\t\t\tif (nat->flags & FMAN_PCD_NATF_DNAT) {\n"
    "\t\t\t\t\tmemcpy(r + l2poff, nat->dip, pv6 ? 16 : 4);\n"
    "\t\t\t\t\tl2poff += pv6 ? 16 : 4;\n"
    "\t\t\t\t}\n"
    "\t\t\t}\n"
    "\t\t\tr[opc_off + oi++] = FMAN_EHASH_OPC_INSERT_L2_HDR;\n"
    "\t\t\tr[opc_off + oi++] = FMAN_EHASH_OPC_ENQUEUE_PKT;\n"
    "\t\t\t*(__be32 *)(r + l2poff + 0) = cpu_to_be32(0x4000000e);\n",
)

# ---- 4. Debugfs caller (fe_flow add) passes NULL nat ------------------------
replace(
    SRC, "debugfs add_key caller NULL nat",
    "\terr = fman_pcd_ehash_add_key(t, key, key_size,\n",
    "\terr = fman_pcd_ehash_add_key_natwrap_dbgfs(t, key, key_size,\n",
)

# ---- 5. Production caller threads action NAT into a params block ------------
replace(
    SRC, "production add_key caller +nat",
    "\t\tint err = fman_pcd_ehash_add_key(t, action->key, action->key_size,\n",
    "\t\tstruct fman_pcd_nat_params _nat = {\n"
    "\t\t\t.flags  = action->nat_flags,\n"
    "\t\t\t.is_v6  = (action->eth_type == 0x86dd),\n"
    "\t\t\t.sport  = action->nat_sport,\n"
    "\t\t\t.dport  = action->nat_dport,\n"
    "\t\t};\n"
    "\t\tint err;\n"
    "\n"
    "\t\tmemcpy(_nat.sip, action->nat_sip, 16);\n"
    "\t\tmemcpy(_nat.dip, action->nat_dip, 16);\n"
    "\t\terr = fman_pcd_ehash_add_key(t, action->key, action->key_size,\n",
)

# ---- 6. Fix production caller's closing arg to pass &_nat -------------------
replace(
    SRC, "production add_key trailing arg",
    "\t\t\t\t\t\t l2_dst, l2_src, l2_eth);\n",
    "\t\t\t\t\t\t l2_dst, l2_src, l2_eth,\n"
    "\t\t\t\t\t\t &_nat);\n",
)

# ---- 7. Debugfs shim: preserve the old 9-arg debugfs call as a NULL-nat wrap-
# Provide a tiny static wrapper so the debugfs path keeps its original argument
# list (no NAT from debugfs) while the real function gains the nat param.
replace(
    SRC, "debugfs natwrap definition",
    "static int fman_pcd_ehash_add_key(struct fman_pcd_ehash_table *t,\n"
    "\t\t\t\t  const u8 *key, u8 key_size,\n"
    "\t\t\t\t  u32 enq_off, u32 fqid, bool stats,\n"
    "\t\t\t\t  const u8 *l2_dst, const u8 *l2_src,\n"
    "\t\t\t\t  u16 eth_type,\n"
    "\t\t\t\t  const struct fman_pcd_nat_params *nat)\n"
    "{",
    "static int fman_pcd_ehash_add_key(struct fman_pcd_ehash_table *t,\n"
    "\t\t\t\t  const u8 *key, u8 key_size,\n"
    "\t\t\t\t  u32 enq_off, u32 fqid, bool stats,\n"
    "\t\t\t\t  const u8 *l2_dst, const u8 *l2_src,\n"
    "\t\t\t\t  u16 eth_type,\n"
    "\t\t\t\t  const struct fman_pcd_nat_params *nat);\n"
    "/* F-230: debugfs fe_flow add path never performs NAT. */\n"
    "static int fman_pcd_ehash_add_key_natwrap_dbgfs(\n"
    "\t\t\t\t  struct fman_pcd_ehash_table *t,\n"
    "\t\t\t\t  const u8 *key, u8 key_size,\n"
    "\t\t\t\t  u32 enq_off, u32 fqid, bool stats,\n"
    "\t\t\t\t  const u8 *l2_dst, const u8 *l2_src,\n"
    "\t\t\t\t  u16 eth_type)\n"
    "{\n"
    "\treturn fman_pcd_ehash_add_key(t, key, key_size, enq_off, fqid, stats,\n"
    "\t\t\t\t      l2_dst, l2_src, eth_type, NULL);\n"
    "}\n"
    "static int fman_pcd_ehash_add_key(struct fman_pcd_ehash_table *t,\n"
    "\t\t\t\t  const u8 *key, u8 key_size,\n"
    "\t\t\t\t  u32 enq_off, u32 fqid, bool stats,\n"
    "\t\t\t\t  const u8 *l2_dst, const u8 *l2_src,\n"
    "\t\t\t\t  u16 eth_type,\n"
    "\t\t\t\t  const struct fman_pcd_nat_params *nat)\n"
    "{",
)

print("### F-230: FE-VM NAT/PAT opcode emitter applied (dormant unless nat->flags)")
