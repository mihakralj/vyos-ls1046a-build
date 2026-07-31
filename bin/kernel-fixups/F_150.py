"""F-150: Fix CONT_LOOKUP group table key_size to match EKFC extraction length.

The scaffold in patch 0132 hardcodes group table word2 = 0x4F000000, which
encodes key_size=16 (0x4F = 0x40 | (16-1)).  This was correct for the old
EKFC=0x00180206 (16-byte extraction: SIP+DIP+SPI+SPORT+DPORT).

Our EKFC=0x001C0006 extracts only 13 bytes (SIP+DIP+PROTO+SPORT+DPORT, no SPI).
The CC engine compares key_size bytes from the match table against the KG-
extracted key.  With key_size=16 but only 13 bytes extracted, the comparison
reads 3 bytes past the key into garbage → never matches.

Fix: Change group table word2 from 0x4F000000 (key_size=16) to 0x4C000000
(key_size=13).  0x4C = 0x40 | (13-1) = 0x40 | 0x0C.

Must run AFTER F-091 (which creates the scaffold with the hardcoded value).
"""

import sys, os

kroot = "drivers/net/ethernet/freescale/fman"
pcd_c = os.path.join(kroot, "fman_pcd.c")

if not os.path.exists(pcd_c):
    print("### F-150: fman_pcd.c not found — skipping")
    sys.exit(0)

with open(pcd_c) as f:
    src = f.read()

changes = 0

# Fix group table word2: 0x4F000000 (key_size=16) → 0x4C000000 (key_size=13)
old_w2 = "iowrite32be(0x4F000000, c + 8);"
new_w2 = "iowrite32be(0x4C000000, c + 8);\t/* F-150: key_size=13 (EKFC=0x001C0006, no SPI) */"

if old_w2 in src:
    src = src.replace(old_w2, new_w2, 1)
    changes += 1
    print("### F-150: fixed group table key_size 16→13 (0x4F000000→0x4C000000)")
else:
    # Try variant without the comment
    old_w2b = "iowrite32be(0x4F000000  /* M2-proven: keySize=16 */, c + 8);"
    if old_w2b in src:
        new_w2b = "iowrite32be(0x4C000000  /* F-150: keySize=13 (EKFC=0x001C0006) */, c + 8);"
        src = src.replace(old_w2b, new_w2b, 1)
        changes += 1
        print("### F-150: fixed group table key_size (commented variant)")
    else:
        print("### F-150: 0x4F000000 not found in scaffold")

if changes:
    with open(pcd_c, "w") as f:
        f.write(src)
    print(f"### F-150: {changes} change(s) applied")
else:
    print("### F-150: no changes — may already be present")
    sys.exit(0)