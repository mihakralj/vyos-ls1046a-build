"""F-187: free the fe_hashfe miss-result allocations on hash free.

CONTEXT (2026-08-12, E26 follow-up / decomp/experiments.md):

fman_pcd_fe_hash_build() allocates, per successful build:
  - pcd->miss_ctx      = dma_alloc_coherent(dev, 256, ...)   (256 B DDR)
  - pcd->miss_res_off  = fman_pcd_muram_alloc(pcd, 16)       (16 B MURAM
    t_ExtHashResult)
and writes the missResult pointer into the hash FE AD (w4).

fman_pcd_fe_hash_free() only returned the FE object to the
fe_available pool and cleared fe_hash_off -- it NEVER freed
miss_res_off or miss_ctx. Measured on .185 (6.18.44-vyos,
muram_budget): exactly +16 B MURAM per fe_hashfe build/clear
cycle, linear (1616 -> 1632 -> 1648 -> 1664 across clean cycles;
+80 B total across the E26 session = 16 x 5 hash builds). The
256 B DMA context leaks alongside it (not visible in the MURAM
budget but equally unreleased). The minimal arm (no hashfe build)
leaks 0, isolating the defect to this pair.

The hash FE is part of the legacy MURAM FE chain (the node
dispatch does not use it), so the leak only manifests in the test
harness / any build that arms the hashfe singleton -- but it
violates the "used MUST return to baseline" reversibility
invariant and leaks 256 B DMA per cycle.

FIX (1 block, fman_pcd.c): in fman_pcd_fe_hash_free(), after the
FE object is returned to the pool and before fe_hash_off = 0,
free miss_res_off (fman_pcd_muram_free, 16 B) and miss_ctx
(dma_free_coherent via the last ehash table's dev -- same lookup
the build uses), resetting both. Guarded on non-zero/NULL so a
no-op hash build path cannot double-free.

Anchored on the exact current fe_hash_free tail (post-F-186
derived state). Idempotent ("F-187:" marker). CI-only build.
"""

import sys

changes = 0


def edit(path, blocks):
    """blocks: list of (name, marker, old, new). The marker string MUST
    appear in new -- it is the per-block idempotency token."""
    global changes
    with open(path) as f:
        src = f.read()
    file_changes = 0
    for name, marker, old, new in blocks:
        if marker not in new:
            print(f"### F-187: FATAL: block '{name}' marker {marker} not "
                  "embedded in its replacement text -- fixup bug.")
            sys.exit(1)
        if marker in src:
            print(f"### F-187: {name} already applied")
            continue
        if old not in src:
            print(f"### F-187: FATAL: '{name}' text not found verbatim in "
                  f"{path} -- source drifted. Refusing to guess.")
            sys.exit(1)
        src = src.replace(old, new, 1)
        file_changes += 1
        changes += 1
        print(f"### {path}: F-187 {name} applied")
    if file_changes:
        with open(path, "w") as f:
            f.write(src)


# -- fman_pcd.c -------------------------------------------------------------
pcd_blocks = [
    ('free miss-result allocs in fe_hash_free',
     'F-187(hash-free-miss-res)',
     "\tpcd->fe_hash_off = 0;\n"
     "}\n"
     "\n"
     "static int fman_pcd_fe_hashfe_show(struct seq_file *s, void *unused)\n",
     "\t/* F-187(hash-free-miss-res): release the miss-result allocations\n"
     "\t * made by fe_hash_build -- the 16 B MURAM t_ExtHashResult and the\n"
     "\t * 256 B DMA miss context. fe_hash_free previously returned only\n"
     "\t * the FE object to the pool, leaking exactly 16 B MURAM + 256 B\n"
     "\t * DMA per build/clear cycle (measured on .185, E26). The hash AD\n"
     "\t * was already zeroed above (FMAN_PCD_FE_MAX_SIZE memset), so the\n"
     "\t * missResult pointer reference is gone before we free the target.\n"
     "\t */\n"
     "\tif (pcd->miss_res_off) {\n"
     "\t\tfman_pcd_muram_free(pcd, pcd->miss_res_off, 16);\n"
     "\t\tpcd->miss_res_off = 0;\n"
     "\t}\n"
     "\tif (pcd->miss_ctx) {\n"
     "\t\tstruct fman_pcd_ehash_table *__t =\n"
     "\t\t\tlist_last_entry_or_null(&pcd->fe_ehash_tables,\n"
     "\t\t\t\tstruct fman_pcd_ehash_table, node);\n"
     "\t\tif (__t)\n"
     "\t\t\tdma_free_coherent(__t->dev, 256, pcd->miss_ctx,\n"
     "\t\t\t\t\t  pcd->miss_ctx_phys);\n"
     "\t\tpcd->miss_ctx = NULL;\n"
     "\t}\n"
     "\tpcd->fe_hash_off = 0;\n"
     "}\n"
     "\n"
     "static int fman_pcd_fe_hashfe_show(struct seq_file *s, void *unused)\n"),
]
edit("drivers/net/ethernet/freescale/fman/fman_pcd.c", pcd_blocks)

if changes:
    print(f"### F-187 complete ({changes} blocks)")
else:
    print("### F-187 no changes applied")
    sys.exit(1)
