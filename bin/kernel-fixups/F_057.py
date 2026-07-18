import re

path = "drivers/net/ethernet/freescale/fman/fman_pcd.c"
with open(path) as f:
    src = f.read()

# Remove the per-record next-FE pointer write (lines 204-206 of 0128).
# The SDK's en_ehash_entry has NO per-record next-FE -- the HIT dispatch
# target is in the hash FE descriptor's word 5 (nextFEPtr = MUX -> ENQ).
# Our extra write at offset 24 corrupts the DDR record, causing the
# hardware to read garbage and crash.

old_code = (
    '\t/* next-FE pointer (ENQ FE MURAM offset) after the 8-byte-aligned key. */\n'
    '\tfe_ptr_off = FMAN_EHASH_FLOW_KEY_OFF + ((key_size + 7U) & ~7U);\n'
    '\t*(__be32 *)(r + fe_ptr_off) = cpu_to_be32((u32)enq_fe_off);\n'
)
if old_code not in src:
    print("### fman_pcd.c: F-057 next-FE pattern not found")
else:
    src = src.replace(old_code, '')
    # Also clean up the unused fe_ptr_off variable
    src = src.replace('size_t fe_ptr_off;\n\n', '')
    print("### fman_pcd.c: F-057 removed per-record next-FE from DDR (SDK-compliant)")

with open(path, "w") as f:
    f.write(src)
