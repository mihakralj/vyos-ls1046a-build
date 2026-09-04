"""F-242 (T-M6-8 VLAN-v6 dig, 2026-09-04): fix kgse_bmcl (FMKG_SE_BMCL, RM
5.10.3.12.5) so F-183's CC-dispatch write to kgse_bmch (FMKG_SE_BMCH,
0x10C) no longer corrupts dual-lane key bytes.

ROOT CAUSE (RM Sec5.10.3.12.4/.5 bit-field table, obtained 2026-09-04):
kgse_bmch packs FOUR independent "mask command" selectors, not a single
opaque 24-bit value:

  bits 0-5    MCS0  (selects an EKFC/GEC field to mask, byte 0)
  bits 6-11   MCS1  (selects an EKFC/GEC field to mask, byte 1)
  bits 12-15  MO0   (byte offset within the MCS0 field)
  bits 16-21  MCS2
  bits 22-27  MCS3
  bits 28-31  MO1

The actual byte-mask VALUE for each of the four commands lives in
kgse_bmcl (FMKG_SE_BMCL, 0x110): BM0[0:7], BM1[8:15], BM2[16:23],
BM3[24:31] -- output_byte = extracted_byte & BM (a bitwise AND: BM=0x00
forces the selected byte to zero; BM=0xFF is a true no-op, preserving it
unchanged).

F-183 (2026-08-10) empirically found that writing the CC-tree's MURAM
group offset into kgse_bmch is what fires CC-tree dispatch on this
silicon (a real, load-bearing, silicon-proven mechanism -- NOT reverted
by this fixup). What F-183 didn't know: that same write ALSO,
unavoidably, populates MCS0/MCS1/MO0/MCS2/MCS3/MO1 with whatever bit
pattern the MURAM offset happens to contain, arming up to four mask
commands whose TARGET bytes are essentially incidental to the offset
value. Combined with kgse_bmcl staying at its hard-coded 0 (set twice in
this function, F-051 and the "features not used" block, and never
written again afterward for ANY scheme), every one of those incidentally
armed commands forces its target byte to 0x00. The 16-byte v4-only
key's shipped, production CC-tree path apparently never had a byte
position collide with an armed command's target; the wider dual-lane key
does (byte 42, the sport high byte, confirmed via live F-241 probe3
capture on 2026-09-03).

FIX: kgse_bmcl = 0xFFFFFFFF (all four BM fields = 0xFF) makes every mask
command's AND a true no-op regardless of which bytes MCS0-3/MO0-1
happen to select -- neutralizing the unwanted masking while leaving
F-183's kgse_bmch CC-dispatch write completely untouched. Safe
unconditionally (a no-op mask can't break anything that was already
"successfully" avoiding disaster only by incidental byte-target luck),
so this changes both occurrences of `scheme_regs.kgse_bmcl = 0;`, not
just the next_engine==2 path.

Must run after whatever fixup/patch last touches these two exact lines
(none currently do -- this is the first). Idempotent.
"""

import os
import sys

path = "drivers/net/ethernet/freescale/fman/fman_keygen.c"
if not os.path.exists(path):
    print("### F-242: fman_keygen.c not found")
    sys.exit(0)

with open(path) as f:
    src = f.read()

marker = "F-242(bmcl-noop-mask)"
if marker in src:
    print("### F-242: already applied")
    sys.exit(0)

old = "\tscheme_regs.kgse_bmcl = 0;\n"
count = src.count(old)
if count != 2:
    print(f"### F-242: FATAL: expected exactly 2 occurrences of the kgse_bmcl=0 anchor, found {count}")
    sys.exit(1)

new = (
    "\t/* " + marker + ": RM 5.10.3.12.5 BM0-3=0xFF is a no-op AND-mask on\n"
    "\t * the 4 mask commands F-183's kgse_bmch write arms incidentally --\n"
    "\t * BMCL=0 was forcing their incidentally-selected target bytes (e.g.\n"
    "\t * dual-lane key byte 42) to zero. See the F-183 comment below. */\n"
    "\tscheme_regs.kgse_bmcl = 0xFFFFFFFF;\n"
)

src = src.replace(old, new)

with open(path, "w") as f:
    f.write(src)
print("### fman_keygen.c: F-242 kgse_bmcl no-op mask (both occurrences) applied")
