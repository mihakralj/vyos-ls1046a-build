"""F-144: Fix EXT_HASH FE word1 byte order to match SDK's packed struct layout.

The NXP SDK's t_ExtHashFe struct is _Packed, meaning the fields are laid out
in CPU-native little-endian order:
  Byte 0-1: hashMask (uint16_t, little-endian)
  Byte 2:   contextSize (uint8_t)
  Byte 3:   hashShift (uint8_t)

When the FMan reads this as a big-endian u32, it sees:
  bits [31:24] = hashShift
  bits [23:16] = contextSize
  bits [15:0]  = hashMask

Our fman_pcd_fe_hash_encode() was writing word1 as a single big-endian u32:
  (hashMask << 16) | (contextSize << 8) | hashShift

This puts hashMask in bits [31:16] and hashShift in bits [7:0] — the OPPOSITE
of what the microcode expects.  The microcode was reading hashShift=0x7F (the
high byte of hashMask=0x7FFF) and hashMask=0x0C00 (contextSize=12 and
hashShift=0 packed into the low bits).

This is the root cause of the ehash HIT failure (F-141).  The bucket index
computation was using completely wrong hashShift and hashMask values.

Fix: encode word1 as the SDK does:
  (hashShift << 24) | (contextSize << 16) | hashMask

Must run AFTER 0131 (which defines fman_pcd_fe_hash_encode).
"""

import sys, os

kroot = "drivers/net/ethernet/freescale/fman"
pcd_c = os.path.join(kroot, "fman_pcd.c")

if not os.path.exists(pcd_c):
    print("### F-144: fman_pcd.c not found — skipping")
    sys.exit(0)

with open(pcd_c) as f:
    src = f.read()

changes = 0

# Fix word1 encoding in fman_pcd_fe_hash_encode()
# Old: ((u32)(t->hash_mask & 0xffff) << 16) | ((u32)((t->key_size - 1) & 0xff) << 8) | (u32)(t->hash_shift & 0xff)
# New: ((u32)(t->hash_shift & 0xff) << 24) | ((u32)((t->key_size - 1) & 0xff) << 16) | (u32)(t->hash_mask & 0xffff)

old_word1 = "((u32)(t->hash_mask & 0xffff) << 16) |\n\t\t    ((u32)((t->key_size - 1) & 0xff) << 8) |\n\t\t    (u32)(t->hash_shift & 0xff)"
new_word1 = "((u32)(t->hash_shift & 0xff) << 24) |\n\t\t    ((u32)((t->key_size - 1) & 0xff) << 16) |\n\t\t    (u32)(t->hash_mask & 0xffff)"

if old_word1 in src:
    src = src.replace(old_word1, new_word1, 1)
    changes += 1
    print("### F-144: fixed word1 byte order (hashShift<<24 | contextSize<<16 | hashMask)")
else:
    # Try single-line variant
    old_word1b = "((u32)(t->hash_mask & 0xffff) << 16) | ((u32)((t->key_size - 1) & 0xff) << 8) | (u32)(t->hash_shift & 0xff)"
    if old_word1b in src:
        src = src.replace(old_word1b, new_word1.replace('\n\t\t    ', ' '), 1)
        changes += 1
        print("### F-144: fixed word1 byte order (single-line variant)")
    else:
        print("### F-144: word1 encoding not found — may already be fixed or different format")

if changes:
    with open(pcd_c, "w") as f:
        f.write(src)
    print(f"### F-144: {changes} change(s) applied")
else:
    print("### F-144: no changes — may already be present")
    sys.exit(0)