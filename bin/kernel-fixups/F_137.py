"""F-137: Allocate per-port FE buffer pools from global FMan MURAM.

The per-port FE buffer pools (~9 KB each) are allocated from the PCD
gen_pool via fman_pcd_muram_alloc().  With the warm-chain strategy
(F-136), the ehash int_buf (33280 B) stays allocated at a fixed offset,
fragmenting the 84 KiB arena.  The second port's pool cannot be placed
in the remaining fragments → -12 ENOMEM.

Fix: allocate per-port pools from the global FMan MURAM
(fman_muram_alloc) instead of the PCD gen_pool.  The global pool has
~299 KiB of space (0x0-0x4ac00) minus FMan firmware usage — ample room
for two ~9 KB pools.  The pools are freed via fman_muram_free_mem().

This keeps the PCD arena dedicated to the FE-VM chain (ehash, pool,
singletons, enq, hash) which fits comfortably in 84 KiB.

Must run AFTER F-136 (warm chain) and AFTER 0123 (fe_port_set).

Disposition: fold into 0123
Upstream-Status: Inappropriate [LS1046A Mono Gateway DK FE-VM]
Risk-Tier: B (changes allocator for per-port pools, needs fman_muram handle)
"""

import sys, os

kroot = "drivers/net/ethernet/freescale/fman"
pcd_c = os.path.join(kroot, "fman_pcd.c")

if not os.path.exists(pcd_c):
    print("### F-137: fman_pcd.c not found — skipping")
    sys.exit(0)

with open(pcd_c) as f:
    src = f.read()

changes = 0

# The per-port pool is allocated in fman_pcd_fe_port_set() via:
#   pool_raw_off = fman_pcd_muram_alloc(pcd, pool_raw_size);
#   mgmt_off = fman_pcd_muram_alloc(pcd, mgmt_size);
# And freed in fman_pcd_fe_port_del() via:
#   fman_pcd_muram_free(pcd, fp->mgmt_off, fp->mgmt_size);
#   fman_pcd_muram_free(pcd, fp->pool_raw_off, fp->pool_raw_size);
#
# We change the alloc to use fman_muram_alloc(muram, size) and the free
# to use fman_muram_free_mem(muram, offset, size).

# Anchor 1: pool allocation
pool_alloc = "\tpool_raw_off = fman_pcd_muram_alloc(pcd, pool_raw_size);"
if pool_alloc not in src:
    print("### F-137: pool allocation not found — skipping")
    sys.exit(0)

# Count must be exactly 1
if src.count(pool_alloc) != 1:
    print(f"### F-137: expected 1 pool alloc, found {src.count(pool_alloc)}")
    sys.exit(1)

new_pool_alloc = "\tpool_raw_off = fman_muram_alloc(muram, pool_raw_size);"
src = src.replace(pool_alloc, new_pool_alloc, 1)
changes += 1
print("### F-137: pool allocation -> global FMan MURAM")

# Anchor 2: mgmt allocation
mgmt_alloc = "\tmgmt_off = fman_pcd_muram_alloc(pcd, mgmt_size);"
if mgmt_alloc not in src:
    print("### F-137: mgmt allocation not found — skipping")
    sys.exit(0)

if src.count(mgmt_alloc) != 1:
    print(f"### F-137: expected 1 mgmt alloc, found {src.count(mgmt_alloc)}")
    sys.exit(1)

new_mgmt_alloc = "\tmgmt_off = fman_muram_alloc(muram, mgmt_size);"
src = src.replace(mgmt_alloc, new_mgmt_alloc, 1)
changes += 1
print("### F-137: mgmt allocation -> global FMan MURAM")

# Anchor 3: pool free in fe_port_del
pool_free = "\tfman_pcd_muram_free(pcd, fp->mgmt_off, fp->mgmt_size);"
if pool_free not in src:
    print("### F-137: mgmt free not found — skipping")
    sys.exit(0)

new_pool_free = "\tfman_muram_free_mem(muram, fp->mgmt_off, fp->mgmt_size);"
# Replace both occurrences (mgmt and pool)
count_before = src.count(pool_free)
src = src.replace(pool_free, new_pool_free)
changes += 1
print(f"### F-137: mgmt free -> global FMan MURAM ({count_before} occurrences)")

# Anchor 4: pool_raw free
pool_raw_free = "\tfman_pcd_muram_free(pcd, fp->pool_raw_off, fp->pool_raw_size);"
if pool_raw_free not in src:
    print("### F-137: pool_raw free not found — skipping")
    sys.exit(0)

new_pool_raw_free = "\tfman_muram_free_mem(muram, fp->pool_raw_off, fp->pool_raw_size);"
count_before = src.count(pool_raw_free)
src = src.replace(pool_raw_free, new_pool_raw_free)
changes += 1
print(f"### F-137: pool_raw free -> global FMan MURAM ({count_before} occurrences)")

# Also fix the drain function which has the same frees
drain_mgmt = "\t\tfman_pcd_muram_free(pcd, fp->mgmt_off, fp->mgmt_size);"
if drain_mgmt in src:
    src = src.replace(drain_mgmt, "\t\tfman_muram_free_mem(muram, fp->mgmt_off, fp->mgmt_size);")
    changes += 1
    print("### F-137: drain mgmt free -> global FMan MURAM")

drain_pool = "\t\tfman_pcd_muram_free(pcd, fp->pool_raw_off, fp->pool_raw_size);"
if drain_pool in src:
    src = src.replace(drain_pool, "\t\tfman_muram_free_mem(muram, fp->pool_raw_off, fp->pool_raw_size);")
    changes += 1
    print("### F-137: drain pool free -> global FMan MURAM")

if changes:
    with open(pcd_c, "w") as f:
        f.write(src)
    print(f"### F-137: {changes} change(s) applied")
else:
    print("### F-137: no changes applied")
    sys.exit(1)