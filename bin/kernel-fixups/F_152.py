"""F-152: Revert F-144 — restore original EXT_HASH FE word1 formula.

F-144 (this session) changed word1 from the original patch 0131 formula
  (hashMask << 16) | ((contextSize-1) << 8) | hashShift
to
  (hashShift << 24) | (contextSize << 16) | hashMask

based on an unverified "SDK _Packed struct" theory, WITHOUT first checking
arch/fman-microcode-210-programming-reference.md — which already documented
(since 2026-07-17, before this session) that the ORIGINAL formula is correct:

  Section 7.2 line 358: "(hashMask << 16) | ((contextSize-1) << 8) | hashShift"
  Section 7.2 line 638 [SPEC] verification: "EXT_HASH FE word1 = 0x7fff0c00 ->
    hashMask=0x7fff, contextSize-1=0x0c (12), hashShift=0x00.
    contextSize = 13 = EKFC key length (correct)."

F-144's formula produces 0x000C7FFF instead of the correct 0x7FFF0C00 --
hashMask, contextSize, and hashShift all land in the wrong bit positions.
This has been corrupting the EXT_HASH FE's bucket-index/comparison config
for the entire ehash-HIT debugging portion of this session.

Uses a regex match (not exact-string) because F-145 and F-149 both touched
the middle term of F-144's 3-line expression, and the exact intermediate
text depends on which of those actually fired.  Matching on the reordered
term SHAPE (hash_shift...24 | key_size...16 | hash_mask, in that order,
regardless of embedded comments) is robust to all combinations.

F-149's contextSize fix (key_size-1, i.e. numeric value 12 for a 13-byte
key) is CORRECT and is preserved -- it operates on the VALUE, not the
bit-position FORMULA that F-144 broke and this fixup restores.

Must run AFTER F-149 (contextSize value fix) since it also touches word1.
"""

import sys, os, re

kroot = "drivers/net/ethernet/freescale/fman"
pcd_c = os.path.join(kroot, "fman_pcd.c")

if not os.path.exists(pcd_c):
    print("### F-152: fman_pcd.c not found — skipping")
    sys.exit(0)

with open(pcd_c) as f:
    src = f.read()

changes = 0

# Match F-144's shape regardless of embedded comments or exact whitespace:
#   ((u32)(t->hash_shift & 0xff) << 24) |  <ws>  ((u32)((t->key_size - 1) & 0xff) << 16)  <maybe comment>  |  <ws>  (u32)(t->hash_mask & 0xffff)
f144_pattern = re.compile(
    r"\(\(u32\)\(t->hash_shift\s*&\s*0xff\)\s*<<\s*24\)\s*\|"
    r"\s*\(\(u32\)\(\(t->key_size\s*-\s*1\)\s*&\s*0xff\)\s*<<\s*16\)"
    r"(?:\s*/\*[^*]*\*/)?"
    r"\s*\|"
    r"\s*\(u32\)\(t->hash_mask\s*&\s*0xffff\)",
    re.DOTALL,
)

new_orig = ("((u32)(t->hash_mask & 0xffff) << 16) |\n"
            "\t\t    ((u32)((t->key_size - 1) & 0xff) << 8) |\n"
            "\t\t    (u32)(t->hash_shift & 0xff)")

m = f144_pattern.search(src)
if m:
    src = src[:m.start()] + new_orig + src[m.end():]
    changes += 1
    print("### F-152: reverted F-144 (regex match) — restored (hashMask<<16)|((contextSize-1)<<8)|hashShift")
elif "(u32)(t->hash_mask & 0xffff) << 16" in src:
    print("### F-152: original formula already present — F-144 not applied or already reverted")
else:
    print("### F-152: F-144's pattern not found — check fman_pcd_fe_hash_encode() manually")

if changes:
    with open(pcd_c, "w") as f:
        f.write(src)
    print(f"### F-152: {changes} change(s) applied")
else:
    print("### F-152: no changes — verify word1 formula manually")
    sys.exit(0)