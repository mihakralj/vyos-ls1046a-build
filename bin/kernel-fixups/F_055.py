import re

path = "drivers/net/ethernet/freescale/fman/fman_pcd.c"
with open(path) as f:
    src = f.read()

# F-060 v3: Fix MUX context write target from AD+0 to AD+4 (word 1)
# F-055/F-056 wrote the MUX write across TWO lines.  Search for the
# partial unique marker then use regex to replace the 2-line block.
# AVOID \s in regex (bad escape through the 4-layer nesting pipeline).

trigger = "(u32)enq->muram_off,"
if trigger in src:
    # Match: iowrite32be((u32)enq->muram_off,\n<whitespace>mux);
    # Replace with single-line write to word 1 (AD+4)
    old_rx = re.compile(
        r"\t\t\t\tiowrite32be\(\(u32\)enq->muram_off,[ \t]*\n[ \t]*mux\);[ \t]*\n"
    )
    replacement = "\t\t\t\tiowrite32be((u32)enq->muram_off, (u32 __iomem *)mux + 1); /* F-060: SDK-compliant MUX context at AD+4 */\n"
    src, n = old_rx.subn(replacement, src, count=1)
    if n > 0:
        print(f"### fman_pcd.c: F-060 v3: MUX write fixed to AD+4 ({n} replacement)")
    else:
        print("### fman_pcd.c: F-060 v3: regex compiled but 0 matches (already applied?)")
else:
    print("### fman_pcd.c: F-060 v3: trigger not found (F-055/F-056 not applied?)")

with open(path, "w") as f:
    f.write(src)
