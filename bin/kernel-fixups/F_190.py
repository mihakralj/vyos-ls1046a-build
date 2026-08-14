"""F-190: write en_exthash_node vendor node to the root AD in fe_enter_build.

The fe_arm engage function writes the vendor node to the group offset (gro),
but the CC dispatch reads the root AD (off). The fe_enter_build function
writes a CONT_LOOKUP group to the root AD, which the CC dispatch interprets
as garbled parameters (numKeys=142). The fix: overwrite the root AD with
the en_exthash_node when an ehash table exists. Requires a forward
declaration because struct fman_pcd_ehash_table is defined 160 lines later.

F-190 (2 blocks, fman_pcd.c). Idempotent ("F-190:" markers). CI-only.
"""

import sys
changes = 0

def edit(path, blocks):
    global changes
    with open(path) as f:
        src = f.read()
    for name, marker, old, new in blocks:
        if marker not in new:
            print(f"### F-190: FATAL: block '{name}' marker not in replacement")
            sys.exit(1)
        if marker in src:
            print(f"### F-190: {name} already applied")
            continue
        if old not in src:
            print(f"### F-190: FATAL: '{name}' text not found verbatim")
            sys.exit(1)
        src = src.replace(old, new, 1)
        changes += 1
        print(f"### {path}: F-190 {name} applied")
    if changes:
        with open(path, "w") as f:
            f.write(src)

pcd_blocks = [
    ('forward declaration',
     'F-190(fwd-decl)',
     "static int fman_pcd_fe_enter_build(struct fman_pcd *pcd, unsigned long fe_off)\n",
     "struct fman_pcd_ehash_table;\t/* F-190(fwd-decl) */\n"
     "static int fman_pcd_fe_enter_build(struct fman_pcd *pcd, unsigned long fe_off)\n"),
    ('vendor node at root AD',
     'F-190(fe-enter-vendor-node)',
     "\tpcd->fe_root_ad_off = off;\n",
     "\t/* F-190(fe-enter-vendor-node): write en_exthash_node to root AD */\n"
     "\t{\n"
     "\t\tstruct fman_pcd_ehash_table *__et =\n"
     "\t\t\tlist_first_entry_or_null(&pcd->fe_ehash_tables,\n"
     "\t\t\t\tstruct fman_pcd_ehash_table, node);\n"
     "\t\tif (__et && pcd->fe_int_buf_off) {\n"
     "\t\t\tu64 __tb = (u64)__et->table_dma;\n"
     "\t\t\tiowrite32be((2U << 30) |\n"
     "\t\t\t\t    ((u32)(__et->key_size & 0x3f) << 24) |\n"
     "\t\t\t\t    (4U << 20) |\n"
     "\t\t\t\t    ((u32)(__et->hash_shift & 0x7) << 16) |\n"
     "\t\t\t\t    ((u32)(__tb >> 32) & 0xffU), ad + 0);\n"
     "\t\t\tiowrite32be((u32)(__tb & 0xffffffffU), ad + 1);\n"
     "\t\t\tiowrite32be(\n"
     "\t\t\t    ((u32)((pcd->fe_int_buf_off >> 8) & 0xffffU) << 16) |\n"
     "\t\t\t    (0x80U << 4) |\n"
     "\t\t\t    (u32)(__et->hash_mask_bits & 0xfU), ad + 2);\n"
     "\t\t\tiowrite32be(0, ad + 3);\n"
     "\t\t}\n"
     "\t}\n"
     "\n"
     "\tpcd->fe_root_ad_off = off;\n"),
]
edit("drivers/net/ethernet/freescale/fman/fman_pcd.c", pcd_blocks)
if changes:
    print(f"### F-190 complete ({changes} blocks)")
else:
    print("### F-190 no changes applied")
    sys.exit(1)
