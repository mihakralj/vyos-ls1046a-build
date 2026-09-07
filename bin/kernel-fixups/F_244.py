"""F-244 (T-M6-8 VLAN-v6 dig, 2026-09-04): soft-parser magic-byte PoC --
replaces F-243's OR_IV_LCV bytecode with STORE_IV_TO_RA.

WHY: F-243's own verification (specs/ask2-soft-parser-lcv-scheme-select.md
Sec6h) was mathematically incapable of detecting success -- pmda[].lcv
defaults to 0xFFFFFFFF for every HXS slot under mainline init_hwp(), so
OR_IV_LCV into an already-all-ones field is undetectable regardless of
whether the soft-sequence actually ran. This swaps in an unambiguous,
non-saturated write instead: STORE_IV_TO_RA writes a literal immediate
straight into the 32-byte Parse Result ("RA") that F-239/F-241's probe2/
probe3 already capture and hex-dump -- no working-register load needed
(simpler than the originally-planned LOAD_BITS_IV_TO_WR + STORE_WR_TO_RA
pair), and cheaper/lower-risk as a result (2 instructions instead of 3).

Target byte: struct fman_prs_result (drivers/.../fman/fman.h) offset 14,
`route_type` ("Routing type field of a IPV6 routing extension header") --
IPv6-specific, absent-by-default (no routing header) on ordinary transit
traffic, and not re-written by any later (L4) hard-parse stage, so a
successful injection persists to the point probe2/probe3 capture it. This
offset falls inside the same probe2 window this project's own capture
already labeled "byte 12-15 / LCV" in earlier sessions -- every real
capture to date has shown 0xff there, which makes 0xC3 an unambiguous,
easy-to-spot result if (and only if) the soft-sequence actually executes.

Bytecode encoding verified directly against the real FMC Soft Parser
Assembler source (/tmp/kilo/fmc/source/spa/fm_sp_assembler.tab.c,
_fmsp_store_iv_to_ra_action(), _FMSP_INSTR_CODE_STORE_IV_TO_RA=0x0800):
  hw_words[0] = 0x0800 | ((num_bytes-1) << 7) | range_end
  hw_words[1] = immediate value (low 16 bits, unshifted)
For num_bytes=1, range_end=14, imm=0xC3: hw_words[0]=0x080E,
hw_words[1]=0x00C3. JMP HXS RETURN_HXS (0x1FFE) is unchanged from F-243,
same ground truth (_FMSP_INSTR_CODE_JUMP=0x1800 |
_FMSP_INSTR_MOD_JMP_HXS=0x0400 | _FMSP_RETURN_HXS=0x3fe).

  08 0E            STORE_IV_TO_RA, 1 byte, RA[14]
  00 C3            imm (0xC3, the magic byte)
  1F FE            JMP HXS RETURN_HXS
  00 00            pad (unreached, keeps the 32-bit write pair even)

Same code offset (0x040), same sp_load/sp_arm/sp_disarm verbs, same
pmda[6].ssa=0x00000420 arm value as F-243 -- only the loaded bytecode
content changes. Must run after F-243 (replaces its
cc_test_sp_poc_code32 initializer). Idempotent.
"""

import os
import sys

cc_test_c = "drivers/net/ethernet/freescale/fman/fman_pcd_cc_test.c"

if not os.path.exists(cc_test_c):
    print(f"### F-244: {cc_test_c} not found")
    sys.exit(0)

marker = "F-244(sp-magic-byte)"

with open(cc_test_c) as f:
    src = f.read()

if marker in src:
    print("### F-244: already applied")
    sys.exit(0)

old_array = (
    "/* Minimal LCV-injection PoC, ground-truth-verified against the real FMC\n"
    " * Soft Parser Assembler source (see file header comment): OR_IV_LCV\n"
    " * 0x80000000 (opcode 0x0003, imm low16, imm high16), then JMP HXS\n"
    " * RETURN_HXS (0x1800 | 0x0400 | 0x3fe = 0x1FFE) to resume normal\n"
    " * hard-parse flow. Packed as 2 32-bit big-endian words (each covering\n"
    " * a pair of consecutive 16-bit instruction words -- (a<<16)|b is\n"
    " * byte-identical to two sequential 16-bit BE writes of a then b). */\n"
    "static const u32 cc_test_sp_poc_code32[2] = {\n"
    "\t0x00030000U, /* OR_IV_LCV (0x0003), imm low16 (0x0000) */\n"
    "\t0x80001FFEU, /* imm high16 (0x8000), JMP HXS RETURN_HXS (0x1FFE) */\n"
    "};\n"
)
if old_array not in src:
    print("### F-244: FATAL: F-243 cc_test_sp_poc_code32 array not found "
          "-- F-243 must run first")
    sys.exit(1)
if src.count(old_array) != 1:
    print(f"### F-244: FATAL: array anchor not unique ({src.count(old_array)})")
    sys.exit(1)

new_array = (
    f"/* {marker}: magic-byte PoC, replacing F-243's LCV-based sequence\n"
    " * (see this file's own F-243 header for why that verification was\n"
    " * unusable). STORE_IV_TO_RA writes an unambiguous literal (0xC3)\n"
    " * straight into Parse Result byte 14 (struct fman_prs_result's\n"
    " * route_type -- IPv6-only, absent-by-default, never rewritten by a\n"
    " * later hard-parse stage), then JMP HXS RETURN_HXS resumes normal\n"
    " * parsing. Ground-truth-verified against\n"
    " * /tmp/kilo/fmc/source/spa/fm_sp_assembler.tab.c\n"
    " * (_fmsp_store_iv_to_ra_action(): hw_words[0] = 0x0800 |\n"
    " * ((num_bytes-1)<<7) | range_end, hw_words[1] = imm & 0xffff). */\n"
    "static const u32 cc_test_sp_poc_code32[2] = {\n"
    "\t0x080E00C3U, /* STORE_IV_TO_RA (0x0800|0<<7|14=0x080E), imm 0x00C3 */\n"
    "\t0x1FFE0000U, /* JMP HXS RETURN_HXS (0x1FFE), pad */\n"
    "};\n"
)
src = src.replace(old_array, new_array, 1)

with open(cc_test_c, "w") as f:
    f.write(src)
print("### fman_pcd_cc_test.c: F-244 magic-byte bytecode swapped in")
