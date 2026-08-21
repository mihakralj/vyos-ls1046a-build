"""F-224 (Phase A): program the 46-byte dual-lane GEC key on the AC_CC FE scheme.

Silicon-proven design (specs/ask2-ipv6-dual-lane-key-design.md, Stage-1/2 exact
crc64 matches 2026-08-21): replace the 14-byte EKFC key (0x801c0006) on the
engaged AC_CC scheme with a single all-GEC 46-byte dual-lane key that carries
BOTH IPv4 and IPv6 in one fixed width, so ONE match-all scheme + ONE per-port
table serve both families with NO parser LCV split (the LCV split is the
D-1-proven dual-v6 wedge).

46-byte key layout (GEC concatenation order):
  [0]    FAMILY   parse-result L3R byte4 (0x80 v4 / 0x40 v6)   gec=0x80FF2004
  [1..8] IPv4 src+dst @ IP hdr +12, VALIDATED code 0x0b        gec=0x87FF0B0C
  [9..24]  IPv6 src @ IP6 hdr +8,  VALIDATED code 0x1b         gec=0x8FFF1B08
  [25..40] IPv6 dst @ IP6 hdr +24, VALIDATED code 0x1b         gec=0x8FFF1B18
  [41]   proto/nexthdr  IP_PID_NO_V 0x72                        gec=0x80FF7200
  [42..45] L4 src+dst   L4_NO_V 0x7e                            gec=0x83FF7E00

VALIDATED address codes (0x0b/0x1b) are mandatory: when the frame's family does
NOT match, the extract substitutes the default register (reset 0), full width,
so the absent family's lane is a deterministic zero fill (vendor fm_kg.c +
silicon-confirmed: v4 frame -> v6 lanes 16+16 zero; v6 frame -> v4 lane 8 zero).
The no-validate L3 code (0x7b) would overread instead — do NOT use it here.

WHERE: keygen_scheme_setup(), the `if (scheme->ekfc)` override block. Gated to
the FE scheme only (scheme->next_engine == 3) so RSS (0), policer (1), and the
CCBS implicit-walk (2) schemes keep EKFC/GEC untouched. For the FE scheme we
force kgse_ekfc = 0 (all-GEC key, no known/EKFC fields) and write kgse_gec[0..5]
with the six constants; gec[6..7] stay 0 (memset). kgse_dv0/dv1/ekdv are already
zeroed in this block (F-179), which is exactly the zero default the absent-lane
substitution reads.

This CHANGES THE PROVEN v4 KEY FORMAT (14-byte EKFC -> 46-byte GEC). Phase A ships
this v4-only (v6 gate OFF) and is REGRESSION-GATED: v4 must still HIT at
7.29 Gbit/s with the 46-byte key before v6 is enabled or this ships. Companion
edits: F-188 ehash_key_sz 14->46, F-220 per-port table keysize 14->46, ask.ko
46-byte dual-lane serializer.

Must run AFTER F-209 (CCOBASE) and any fixup that edits this EKFC block. Idempotent.
"""

import os
import sys

path = "drivers/net/ethernet/freescale/fman/fman_keygen.c"
if not os.path.exists(path):
    print("### F-224: fman_keygen.c not found")
    sys.exit(0)

with open(path) as f:
    src = f.read()

marker = "F-224(dual-lane-gec)"
if marker in src:
    print("### F-224: already applied")
    sys.exit(0)

old = (
    "\tif (scheme->ekfc) {\n"
    "\t\tscheme_regs.kgse_ekfc = scheme->ekfc;\n"
)

new = (
    "\tif (scheme->ekfc) {\n"
    "\t\tscheme_regs.kgse_ekfc = scheme->ekfc;\n"
    "\n"
    "\t\t/* F-224(dual-lane-gec): on the AC_CC FE scheme (next_engine==3)\n"
    "\t\t * replace the EKFC key with the silicon-proven 46-byte all-GEC\n"
    "\t\t * dual-lane key so ONE match-all scheme carries IPv4 AND IPv6 in\n"
    "\t\t * one per-port table (no parser LCV split). EKFC=0 => no known\n"
    "\t\t * fields, key is purely the concatenated GEC outputs. VALIDATED\n"
    "\t\t * IPv4(0x0b)/IPv6(0x1b) address codes zero-fill the absent family's\n"
    "\t\t * lane from the (zeroed) default register. gec: [0]=family PR byte4,\n"
    "\t\t * [1]=v4 src+dst@+12, [2]=v6 src@+8, [3]=v6 dst@+24, [4]=proto,\n"
    "\t\t * [5]=L4 ports. RSS/policer/CCBS schemes are untouched. */\n"
    "\t\tif (scheme->next_engine == 3) {\n"
    "\t\t\tscheme_regs.kgse_ekfc = 0;\n"
    "\t\t\tscheme_regs.kgse_gec[0] = 0x80FF2004;\n"
    "\t\t\tscheme_regs.kgse_gec[1] = 0x87FF0B0C;\n"
    "\t\t\tscheme_regs.kgse_gec[2] = 0x8FFF1B08;\n"
    "\t\t\tscheme_regs.kgse_gec[3] = 0x8FFF1B18;\n"
    "\t\t\tscheme_regs.kgse_gec[4] = 0x80FF7200;\n"
    "\t\t\tscheme_regs.kgse_gec[5] = 0x83FF7E00;\n"
    "\t\t}\n"
)

if old not in src:
    print("### F-224: FATAL: EKFC override block not found — source drifted / run after F-179")
    sys.exit(1)
if src.count(old) != 1:
    print(f"### F-224: FATAL: EKFC override anchor not unique ({src.count(old)})")
    sys.exit(1)

src = src.replace(old, new, 1)

with open(path, "w") as f:
    f.write(src)

print("### fman_keygen.c: F-224 46-byte dual-lane GEC key on AC_CC FE scheme")
