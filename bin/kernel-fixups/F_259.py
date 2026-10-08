"""F-259 (T-M6-SP4 logical-ingress key, 2026-10-08): extend the routed FE
ehash key from 46 to 50 bytes with the ingress L2 context: the outer VLAN ID
and the PPPoE session ID.

WHY: the hardware flow key must carry the same identity the kernel flowtable
does (ingress port + encapsulation + inner 5-tuple). The per-port table gives
the port; the 46-byte dual-lane key (F-224/F-243) gives the 5-tuple, but
nothing distinguished the encapsulation. Proven on silicon 2026-10-08 (F-258
probe, plans/ASK2-PPPOE-OFFLOAD-PLAN.md section 3.1): a PPPoE session frame
produces the identical 46-byte key to a plain frame with the same 5-tuple, and
the same holds for a tagged frame vs an untagged one. A VLAN-pop or PPPoE-decap
record could therefore be hit by a frame that has no tag/session to strip.

KEY (50 bytes, GEC concatenation order; [0..45] unchanged):
  [46..47] VLAN1 TCI & 0x0FFF = outer VID   gec[6]=0x810F0502
           (KG_SCH_GEN_VLAN1 0x05, VALIDATED, header +2, 2 B, first-byte
           mask 0x0F drops PCP/DEI)
  [48..49] PPPoE session ID                  gec[7]=0x81FF0802
           (KG_SCH_GEN_PPP 0x08, VALIDATED, header +2, 2 B)
Validated codes substitute the zeroed default register when the header is
absent (the F-224 zero-fill mechanism, silicon-proven for the v4/v6 lanes),
so plain frames carry 0/0. Header offsets come from the parse result
(vlan_off = TPID position, pppoe_off = PPPoE header start), so TCI and SID are
both at +2. GEC encoding per vendor fm_kg.c: VALID|(size-1)<<24|mask<<16|
code<<8|offset; the first-byte mask mechanism is the one F-243 already uses
for the family byte.

Only the ehash FE scheme (next_engine == 3, routed profile) gets gec[6..7];
the dormant CC-tree dual-lane experiments (next_engine == 2) keep 46 bytes.
One constant, FMAN_PCD_FE_ROUTED_KEY_SIZE in include/linux/fsl/fman_pcd.h,
now sizes every routed table and key buffer (spec section 10.3); ask.ko
static_asserts its ASK_FE_KEY_SIZE_DUAL against it. Record layout impact:
opcodes move from +56 to +60; worst-case parameter end (NAT66 + VLAN
translate) is +184, under the +256 stats block.

Must run after F-224 (keygen anchor), F-255 (profile table) and the
0194/0198 ACL bridges. Idempotent; exits non-zero unless every anchor
matches exactly once.
"""

import sys

MARK = "F-259"

EDITS = [
    ("include/linux/fsl/fman_pcd.h",
     "#define FMAN_FE_FLOW_KEY_MAX   56   /* fits 256B DDR record minus header */\n",
     "#define FMAN_FE_FLOW_KEY_MAX   56   /* fits 256B DDR record minus header */\n"
     "/* F-259: routed FE key = 46-byte dual-lane 5-tuple (F-224) + outer VID (2)\n"
     " * + PPPoE session ID (2). The single size for routed tables and keys. */\n"
     "#define FMAN_PCD_FE_ROUTED_KEY_SIZE 50\n"),
    ("drivers/net/ethernet/freescale/fman/fman_keygen.c",
     "\t\t\tscheme_regs.kgse_gec[5] = 0x83FF7E00;\n\t\t}\n",
     "\t\t\tscheme_regs.kgse_gec[5] = 0x83FF7E00;\n"
     "\t\t\t/* F-259: ingress L2 context for the routed ehash key:\n"
     "\t\t\t * [46..47] outer VID (VLAN1 TCI & 0x0FFF), [48..49] PPPoE\n"
     "\t\t\t * session ID. Validated codes: 0 when the header is absent. */\n"
     "\t\t\tif (scheme->next_engine == 3) {\n"
     "\t\t\t\tscheme_regs.kgse_gec[6] = 0x810F0502;\n"
     "\t\t\t\tscheme_regs.kgse_gec[7] = 0x81FF0802;\n"
     "\t\t\t}\n"
     "\t\t}\n"),
    ("drivers/net/ethernet/freescale/fman/fman_pcd.c",
     "\t[FMAN_PCD_FE_PROFILE_ROUTED] = { 0x801C0006, false, 46 },\n",
     "\t[FMAN_PCD_FE_PROFILE_ROUTED] = { 0x801C0006, false,\n"
     "\t\t\t\t\t FMAN_PCD_FE_ROUTED_KEY_SIZE }, /* F-259 */\n"),
    ("drivers/net/ethernet/freescale/fman/fman_pcd.c",
     "\tconst u8  ehash_key_sz  = 46;\t/* F-225(key-sz-46): dual-lane GEC key */\n",
     "\tconst u8  ehash_key_sz  = FMAN_PCD_FE_ROUTED_KEY_SIZE; /* F-259 */\n"),
    ("drivers/net/ethernet/freescale/dpaa/dpaa_ethtool.c",
     "#define DPAA_CLS_KEY_MAX 46\t/* dual-lane family format (ask.ko) */\n",
     "#define DPAA_CLS_KEY_MAX FMAN_PCD_FE_ROUTED_KEY_SIZE\t/* F-259; L2 ctx 0 */\n"),
    ("drivers/net/ethernet/freescale/dpaa/dpaa_eth.c",
     "\tu8 key[46];\t/* the dual-lane family key (the table size variant) */\n",
     "\tu8 key[FMAN_PCD_FE_ROUTED_KEY_SIZE];\t/* F-259 routed key */\n"),
    ("drivers/net/ethernet/freescale/dpaa/dpaa_eth.c",
     "#define DPAA_CLS_FLOWER_KEY_MAX\t46\n",
     "#define DPAA_CLS_FLOWER_KEY_MAX\tFMAN_PCD_FE_ROUTED_KEY_SIZE /* F-259 */\n"),
]

srcs = {}
for path, _, _ in EDITS:
    if path not in srcs:
        try:
            with open(path) as f:
                srcs[path] = f.read()
        except FileNotFoundError:
            print(f"### F-259: FATAL: {path} not found")
            sys.exit(1)

if all(MARK in s for s in srcs.values()):
    print("### F-259: already applied")
    sys.exit(0)

for path, old, new in EDITS:
    n = srcs[path].count(old)
    if n != 1:
        print(f"### F-259: FATAL: anchor in {path} found {n} times (expected 1)")
        sys.exit(1)
    srcs[path] = srcs[path].replace(old, new)

for path, s in srcs.items():
    with open(path, "w") as f:
        f.write(s)
print("### F-259: routed FE key 46 -> 50 bytes (outer VID + PPPoE SID)")
