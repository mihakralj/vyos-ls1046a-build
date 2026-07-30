"""F-143: Place en_exthash_node descriptor at start of DDR table allocation.

The FE-VM EXT_HASH FE reads the en_exthash_node descriptor from DDR at the
table_base address to get hash_bytes_offset, key_size, hash_mask_bits, and
other configuration.  Our code allocated DDR only for the bucket array and
never wrote the en_exthash_node — the FE-VM was reading garbage.

The SDK's FmPcdExternalHashTableSet places the en_exthash_node (16 bytes)
at the start of the DDR allocation, followed by the bucket array.  The
table_base pointer points to the en_exthash_node.

Fix:
1. Increase DDR allocation by 16 bytes (sizeof en_exthash_node)
2. Write ad[0..3] to the first 16 bytes of the allocation
3. Offset the bucket array by 16 bytes (table_base + 16)
4. Update the EXT_HASH FE descriptor w2/w3 to point to table_base (the
   en_exthash_node location, not the bucket array)

Must run AFTER 0130 (which converts to dma_alloc_coherent).
"""

import sys, os

kroot = "drivers/net/ethernet/freescale/fman"
pcd_c = os.path.join(kroot, "fman_pcd.c")

if not os.path.exists(pcd_c):
    print("### F-143: fman_pcd.c not found — skipping")
    sys.exit(0)

with open(pcd_c) as f:
    src = f.read()

changes = 0

# ── 1. Add EN_EHASH_NODE_SIZE constant ──
node_size_def = "#define FMAN_EHASH_BUCKET_SIZE\t\t16"
if node_size_def in src:
    new_def = "#define FMAN_EHASH_NODE_SIZE\t\t16\t/* en_exthash_node descriptor */\n" + node_size_def
    if "FMAN_EHASH_NODE_SIZE" not in src:
        src = src.replace(node_size_def, new_def, 1)
        changes += 1
        print("### F-143: added FMAN_EHASH_NODE_SIZE constant")
    else:
        print("### F-143: FMAN_EHASH_NODE_SIZE already present")
else:
    print("### F-143: FMAN_EHASH_BUCKET_SIZE not found")

# ── 2. Increase DDR allocation size by 16 bytes ──
# Find: tablesize = (size_t)(mask + 1) * FMAN_EHASH_BUCKET_SIZE;
old_size = "\ttablesize = (size_t)(mask + 1) * FMAN_EHASH_BUCKET_SIZE;"
if old_size in src:
    new_size = "\ttablesize = FMAN_EHASH_NODE_SIZE + (size_t)(mask + 1) * FMAN_EHASH_BUCKET_SIZE;\t/* F-143: room for en_exthash_node */"
    if "FMAN_EHASH_NODE_SIZE" not in src:
        src = src.replace(old_size, new_size, 1)
        changes += 1
        print("### F-143: increased DDR allocation for en_exthash_node")
    else:
        print("### F-143: DDR allocation already includes node size")
else:
    print("### F-143: tablesize calculation not found")

# ── 3. Write ad[0..3] to the first 16 bytes of the DDR allocation ──
# Find where encode_node is called and add memcpy after it
encode_call = "\tfman_pcd_ehash_encode_node(t, tblphys, pcd->fe_int_buf_off);"
if encode_call in src:
    write_node = "\tfman_pcd_ehash_encode_node(t, tblphys, pcd->fe_int_buf_off);\n\n\t/* F-143: Write en_exthash_node descriptor to first 16 bytes of DDR allocation.\n\t * The FE-VM reads this to get hash_bytes_offset, key_size, hash_mask_bits, etc.\n\t */\n\tmemcpy(t->table_base, t->ad, FMAN_EHASH_NODE_SIZE);"
    if "memcpy(t->table_base, t->ad" not in src:
        src = src.replace(encode_call, write_node, 1)
        changes += 1
        print("### F-143: write en_exthash_node to DDR")
    else:
        print("### F-143: en_exthash_node memcpy already present")
else:
    print("### F-143: encode_node call not found")

# ── 4. Offset bucket array pointer by 16 bytes ──
# The bucket array starts after the en_exthash_node.
# Find where bucket_h is computed and adjust.
# The bucket_h computation uses t->table_base directly.
# We need to offset by FMAN_EHASH_NODE_SIZE.
# Find: flow->bucket_h = (u64 *)((u8 *)t->table_base +
old_bucket = "flow->bucket_h = (u64 *)((u8 *)t->table_base +"
if old_bucket in src:
    new_bucket = "flow->bucket_h = (u64 *)((u8 *)t->table_base + FMAN_EHASH_NODE_SIZE +\t/* F-143: skip en_exthash_node */"
    if "FMAN_EHASH_NODE_SIZE" not in src:
        src = src.replace(old_bucket, new_bucket, 1)
        changes += 1
        print("### F-143: offset bucket array by FMAN_EHASH_NODE_SIZE")
    else:
        print("### F-143: bucket offset already present")
else:
    print("### F-143: bucket_h computation not found")

# ── 5. Update fe_ehash show to display the en_exthash_node ──
# The show function prints "node" followed by ad[0..3].
# The node is now at table_base (first 16 bytes), not at a separate location.
# The show already prints t->ad[0..3] which is correct.

if changes:
    with open(pcd_c, "w") as f:
        f.write(src)
    print(f"### F-143: {changes} change(s) applied")
else:
    print("### F-143: no changes — may already be present")
    sys.exit(0)