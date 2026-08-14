"""F-190: write en_exthash_node vendor node to the root AD in fe_enter_build.

The CC dispatch reads the root AD; fe_enter_build writes a CONT_LOOKUP group
there, which the CC dispatch interprets as garbled parameters.  The fix
overwrites the root AD with the vendor en_exthash_node when an ehash table
exists.

struct fman_pcd_ehash_table is defined AFTER fe_enter_build, so the member
accesses (table_dma/key_size/hash_shift/hash_mask_bits) cannot live inline
in fe_enter_build (incomplete type).  The write is therefore a small helper
defined right after the struct definition, forward-declared before
fe_enter_build.

F-190 (3 blocks, fman_pcd.c). Idempotent ("F-190:" markers). CI-only.
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
     "static void fman_pcd_fe_enter_write_vendor_node(struct fman_pcd *pcd,\n"
     "\t\t\t\t\t\tu32 __iomem *ad,\n"
     "\t\t\t\t\t\tunsigned long off);\n"
     "static int fman_pcd_fe_enter_build(struct fman_pcd *pcd, unsigned long fe_off)\n"),
    ('vendor node call at root AD',
     'F-190(fe-enter-vendor-node)',
     "\tpcd->fe_root_ad_off = off;\n",
     "\t/* F-190(fe-enter-vendor-node): write en_exthash_node to root AD */\n"
     "\tfman_pcd_fe_enter_write_vendor_node(pcd, ad, off);\n"
     "\n"
     "\tpcd->fe_root_ad_off = off;\n"),
    ('vendor node helper (after struct definition)',
     'F-190(fe-enter-vendor-node-helper)',
     "/* Table teardown cascades to its inserted flow records (defined below). */\n"
     "static void fman_pcd_ehash_flow_drain(struct fman_pcd_ehash_table *t);\n",
     "/* F-190(fe-enter-vendor-node-helper): overwrite the root AD with the\n"
     " * vendor en_exthash_node when an ehash table exists.  Defined here (not\n"
     " * inline in fe_enter_build) because struct fman_pcd_ehash_table is\n"
     " * complete only after this point.\n"
     " */\n"
     "static void fman_pcd_fe_enter_write_vendor_node(struct fman_pcd *pcd,\n"
     "\t\t\t\t\t\tu32 __iomem *ad,\n"
     "\t\t\t\t\t\tunsigned long off)\n"
     "{\n"
     "\tstruct fman_pcd_ehash_table *__et =\n"
     "\t\tlist_first_entry_or_null(&pcd->fe_ehash_tables,\n"
     "\t\t\t\t\t struct fman_pcd_ehash_table, node);\n"
     "\n"
     "\tpr_info(\"F-190: et=%p int_buf_off=0x%lx\\n\",\n"
     "\t\t(void *)__et, (unsigned long)pcd->fe_int_buf_off);\n"
     "\tif (__et && pcd->fe_int_buf_off) {\n"
     "\t\tu64 __tb = (u64)__et->table_dma;\n"
     "\t\tpr_info(\"F-190: WRITE root AD 0x%lx tb=0x%llx\\n\",\n"
     "\t\t\t(unsigned long)off, __tb);\n"
     "\t\tiowrite32be((2U << 30) |\n"
     "\t\t\t    ((u32)(__et->key_size & 0x3f) << 24) |\n"
     "\t\t\t    (4U << 20) |\n"
     "\t\t\t    ((u32)(__et->hash_shift & 0x7) << 16) |\n"
     "\t\t\t    ((u32)(__tb >> 32) & 0xffU), ad + 0);\n"
     "\t\tiowrite32be((u32)(__tb & 0xffffffffU), ad + 1);\n"
     "\t\tiowrite32be(\n"
     "\t\t    ((u32)((pcd->fe_int_buf_off >> 8) & 0xffffU) << 16) |\n"
     "\t\t    (0x80U << 4) |\n"
     "\t\t    (u32)(__et->hash_mask_bits & 0xfU), ad + 2);\n"
     "\t\tiowrite32be(0, ad + 3);\n"
     "\t} else {\n"
     "\t\tpr_info(\"F-190: SKIP __et=%p int_buf_off=0x%lx\\n\",\n"
     "\t\t\t(void *)__et, (unsigned long)pcd->fe_int_buf_off);\n"
     "\t}\n"
     "}\n"
     "\n"
     "/* Table teardown cascades to its inserted flow records (defined below). */\n"
     "static void fman_pcd_ehash_flow_drain(struct fman_pcd_ehash_table *t);\n"),
]
edit("drivers/net/ethernet/freescale/fman/fman_pcd.c", pcd_blocks)
if changes:
    print(f"### F-190 complete ({changes} blocks)")
else:
    print("### F-190 no changes applied")
    sys.exit(1)
