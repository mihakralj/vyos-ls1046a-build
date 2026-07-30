"""F-145: Fix EXT_HASH FE contextSize to 256 (DDR record size, not key size).

The NXP SDK's FmPcdExternalHashTableSet passes contextSize=256 (the DDR flow
record size = MAX_EN_EHASH_ENTRY_SIZE), NOT the key size.  Our code was using
contextSize=key_size=13, which tells the microcode the DDR record is only 13
bytes.  The microcode uses contextSize for internal buffer management — it
needs to know the full record size to DMA-read the complete en_ehash_entry
(256 bytes: 8-byte header + key + next-FE pointer + padding).

The microcode reference §7.2 was wrong about contextSize needing to equal
key_size.  The SDK (authoritative) uses 256.  The F-063 "fix" that changed
contextSize from 256 to key_size was incorrect — it fixed a BMI stall that
was actually caused by the word1 byte order bug (F-144), not by contextSize.

With the word1 byte order now correct (F-144), we can restore contextSize=256
to match the SDK.

Fix: Change contextSize from (t->key_size - 1) to (FMAN_EHASH_FLOW_REC_SIZE - 1)
= 255 in fman_pcd_fe_hash_encode().

Must run AFTER F-144 (which fixes the word1 byte order).
"""

import sys, os

kroot = "drivers/net/ethernet/freescale/fman"
pcd_c = os.path.join(kroot, "fman_pcd.c")

if not os.path.exists(pcd_c):
    print("### F-145: fman_pcd.c not found — skipping")
    sys.exit(0)

with open(pcd_c) as f:
    src = f.read()

changes = 0

# Fix contextSize in word1 encoding
# Old: ((u32)((t->key_size - 1) & 0xff) << 16)
# New: ((u32)((FMAN_EHASH_FLOW_REC_SIZE - 1) & 0xff) << 16) = 255

old_ctx = "((u32)((t->key_size - 1) & 0xff) << 16)"
new_ctx = "((u32)((FMAN_EHASH_FLOW_REC_SIZE - 1) & 0xff) << 16)\t/* F-145: DDR record size, not key size */"

if old_ctx in src:
    src = src.replace(old_ctx, new_ctx, 1)
    changes += 1
    print("### F-145: changed contextSize from key_size-1 to FMAN_EHASH_FLOW_REC_SIZE-1 (255)")
else:
    print("### F-145: contextSize encoding not found — may already be fixed")

if changes:
    with open(pcd_c, "w") as f:
        f.write(src)
    print(f"### F-145: {changes} change(s) applied")
else:
    print("### F-145: no changes — may already be present")
    sys.exit(0)