"""F-149: Revert F-145 — restore contextSize = key_size (not DDR record size).

F-145 changed contextSize from key_size-1 to FMAN_EHASH_FLOW_REC_SIZE-1 (255),
based on a misunderstanding of the NXP SDK's FmPcdExternalHashTableSet.  The
SDK passes 256 as the DDR record ALLOCATION size, not the EXT_HASH FE comparison
size.

The microcode reference (arch/fman-microcode-210-programming-reference.md §7.2)
is explicit: contextSize MUST equal the EKFC extracted key length, NOT the DDR
record size.  With contextSize=256, the EXT_HASH FE compares 256 bytes per DDR
entry instead of the actual 13-byte key.  Bytes 21-255 of the DDR record are
uninitialized/padding, so the comparison can never match.

This is the root cause of F-141 (ehash HIT failure).  The word1 byte order fix
(F-144) was correct and necessary, but F-145 introduced a new bug by changing
contextSize to the wrong value.

Fix: restore contextSize = key_size - 1 (12 for 13-byte 5-tuple key).

The microcode reference also documents this exact bug:
  "Known bug in patch 0131: fman_pcd_fe_hash_encode() hardcodes
   FMAN_FE_HASH_CONTEXT_SIZE=256 (the DDR record size) in the contextSize
   field rather than deriving it from t->key_size.  This causes the EXT_HASH
   FE to compare 256 bytes per DDR entry instead of the actual key length.
   Fix: replace the constant with t->key_size."

Must run AFTER F-144 (word1 byte order) and F-145 (which this reverts).
"""

import sys, os

kroot = "drivers/net/ethernet/freescale/fman"
pcd_c = os.path.join(kroot, "fman_pcd.c")

if not os.path.exists(pcd_c):
    print("### F-149: fman_pcd.c not found — skipping")
    sys.exit(0)

with open(pcd_c) as f:
    src = f.read()

changes = 0

# Revert F-145: change contextSize from FMAN_EHASH_FLOW_REC_SIZE-1 back to key_size-1
# F-145 wrote: ((u32)((FMAN_EHASH_FLOW_REC_SIZE - 1) & 0xff) << 16)  /* F-145: DDR record size, not key size */
# We need:     ((u32)((t->key_size - 1) & 0xff) << 16)

old_ctx = "((u32)((FMAN_EHASH_FLOW_REC_SIZE - 1) & 0xff) << 16)\t/* F-145: DDR record size, not key size */"
new_ctx = "((u32)((t->key_size - 1) & 0xff) << 16)\t/* F-149: contextSize = key_size (microcode §7.2) */"

if old_ctx in src:
    src = src.replace(old_ctx, new_ctx, 1)
    changes += 1
    print("### F-149: reverted F-145 — contextSize = key_size (not DDR record size)")
else:
    # Try without the tab comment
    old_ctx2 = "((u32)((FMAN_EHASH_FLOW_REC_SIZE - 1) & 0xff) << 16)"
    if old_ctx2 in src:
        src = src.replace(old_ctx2, new_ctx.replace('\t/* F-149: contextSize = key_size (microcode §7.2) */', ''), 1)
        changes += 1
        print("### F-149: reverted F-145 (alternate pattern)")
    else:
        print("### F-149: F-145 contextSize not found — may already be reverted")

if changes:
    with open(pcd_c, "w") as f:
        f.write(src)
    print(f"### F-149: {changes} change(s) applied")
else:
    print("### F-149: no changes — may already be present")
    sys.exit(0)