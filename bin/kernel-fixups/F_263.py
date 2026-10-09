"""F-263 (A6 churn stall root cause, 2026-10-09): stop writing the
en_exthash_node template into bucket 0 of every ehash table.

F-143 (2026-07-30, folded into patch 0169) copies the 16-byte node template
t->ad[] to the start of the DDR table allocation, on the premise that the
FE-VM reads the node from the table base. It does not: the walker reads the
node from MURAM through IC.CCBASE (the vendor node F-186/F-190 write at the
RCCB target), and the DDR allocation is only the bucket array, so the copy
lands on bucket 0. Its first 8 bytes, read big-endian by the microcode, are
(key_size << 32) | byteswap(table_base_lo): 0x32_000008f7 for the 50-byte key
on the eth4 table at 0xf7080000 (0x2e_000008f7 with the old 46-byte key).

Any flow whose hash selects bucket 0 ((crc64 >> 48) & 0x7fff == 0, one flow
in 32768) makes the walker take that value as a record pointer and DMA it:
'FMan[0] DMA bus error: addr 0x32000008f7 port_id 9', FMFP_EXTC SYNC stuck,
eth3/eth4 RX dead until a power cycle. Proven on .185 (image 0415, stall at
cycle 97, 2026-10-09): the stuck task's IC (MURAM 0x3100) had KS 0x32, the
key of a port-port-v4 ACK flow whose crc64_raw 0x8000a6f507c29f55 selects
bucket 0, and IC+0x90 (the DMA'd bucket head) = 00000032 000008f7, equal to
bucket 0 of table 0xf7080000; buckets 0 of all four tables hold the byte-
reversed templates. This is the A6 stall #3-#6 signature.

The template stays in t->ad (fe_ehash debugfs and the MURAM node writers use
it); dma_alloc_coherent already zeroes the array, so bucket 0 starts empty.
Nothing reads the DDR copy back. Idempotent; exits non-zero unless the anchor
matches exactly once.
"""

import sys

MARK = "F-263"
PCD = "drivers/net/ethernet/freescale/fman/fman_pcd.c"

OLD = ("\t/* F-143: Write en_exthash_node descriptor to first 16 bytes of DDR allocation.\n"
       "\t * The FE-VM reads this to get hash_bytes_offset, key_size, hash_mask_bits, etc.\n"
       "\t */\n"
       "\tmemcpy(t->table_base, t->ad, FMAN_EHASH_NODE_SIZE);\n"
       "\n")
NEW = ("\t/* F-263: the node template lives only in t->ad. The walker reads the\n"
       "\t * node from MURAM (IC.CCBASE); the DDR allocation is the bucket array,\n"
       "\t * and copying the template here (old F-143) made bucket 0 a wild\n"
       "\t * record pointer (FMan DMA bus error, A6 churn stall). */\n"
       "\n")

try:
    with open(PCD) as f:
        src = f.read()
except FileNotFoundError:
    print(f"### F-263: FATAL: {PCD} not found")
    sys.exit(1)

if MARK in src:
    print("### F-263: already applied")
    sys.exit(0)

n = src.count(OLD)
if n != 1:
    print(f"### F-263: FATAL: anchor in {PCD} found {n} times (expected 1)")
    sys.exit(1)

with open(PCD, "w") as f:
    f.write(src.replace(OLD, NEW))
print("### F-263: ehash table no longer copies the node template over bucket 0")
