import sys

path = "drivers/net/ethernet/freescale/fman/fman_pcd.c"
with open(path) as f:
    src = f.read()

changes = 0

# ---- 1. Add fe_pool_off to struct fman_pcd ----
if "unsigned long fe_pool_off;" in src:
    print("### fman_pcd.c: F-061 fe_pool_off field already present")
else:
    struct_anchor = "int fe_refcount;"
    if struct_anchor in src:
        src = src.replace(struct_anchor,
            struct_anchor + "\n\tunsigned long fe_pool_off;\t/* F-061: first FE pool slot MURAM offset for fe_probe */",
            1)
        changes += 1
        print("### fman_pcd.c: F-061 fe_pool_off field added")
    else:
        print("### fman_pcd.c: F-061 WARNING: struct anchor 'int fe_refcount;' not found")

# ---- 2. Save fe_pool_off during fe_pool_alloc ----
if "if (!pcd->fe_pool_off)" in src:
    print("### fman_pcd.c: F-061 fe_pool_off save already present")
else:
    alloc_anchor = "\t\tlist_add_tail(&obj->node, &pcd->fe_available);"
    if alloc_anchor in src:
        save_block = alloc_anchor + "\n\t\tif (!pcd->fe_pool_off)\t/* F-061: save first slot for fe_probe */\n\t\t\tpcd->fe_pool_off = off;"
        src = src.replace(alloc_anchor, save_block, 1)
        changes += 1
        print("### fman_pcd.c: F-061 fe_pool_off save inserted")
    else:
        print("### fman_pcd.c: F-061 WARNING: alloc anchor not found")

# ---- 3. Add fe_probe_show function before fe_port_show ----
if "fman_pcd_fe_probe_show" in src:
    print("### fman_pcd.c: F-061 fe_probe_show already present")
else:
    probe_anchor = "static int fman_pcd_fe_port_show(struct seq_file *s, void *unused)"
    if probe_anchor in src:
        probe_code = (
            "/* F-061: fe_probe debugfs - dump first FE pool slot to read\n"
            " * the KG-extracted key bytes from the FE_ENTER workspace.\n"
            " * The 246-byte workspace is alloc'd per-frame and not zeroed\n"
            " * on free, so after a frame passes through, the KG hash and\n"
            " * extracted key bytes are still readable in MURAM.\n"
            " */\n"
            "static int fman_pcd_fe_probe_show(struct seq_file *s, void *unused)\n"
            "{\n"
            "\tstruct fman_pcd *pcd = s->private;\n"
            "\tstruct muram_info *muram = fman_get_muram(pcd->fman);\n"
            "\tvoid __iomem *ws_base;\n"
            "\tunsigned int i;\n"
            "\n"
            "\tmutex_lock(&pcd->fe_lock);\n"
            "\tif (!muram || pcd->fe_refcount == 0) {\n"
            "\t\tseq_puts(s, \"fe pool not engaged\\n\");\n"
            "\t\tmutex_unlock(&pcd->fe_lock);\n"
            "\t\treturn 0;\n"
            "\t}\n"
            "\tif (!pcd->fe_pool_off) {\n"
            "\t\tseq_puts(s, \"fe pool not allocated\\n\");\n"
            "\t\tmutex_unlock(&pcd->fe_lock);\n"
            "\t\treturn 0;\n"
            "\t}\n"
            "\tws_base = fman_muram_offset_to_vbase(muram, pcd->fe_pool_off);\n"
            "\tseq_printf(s, \"pool=0x%05lx\\n\", pcd->fe_pool_off);\n"
            "\tfor (i = 0; i < 8; i++) {\n"
            "\t\tu32 v = ioread32be((u32 __iomem *)ws_base + i);\n"
            "\t\tseq_printf(s, \" [%02d]=%08x\", i, v);\n"
            "\t}\n"
            "\tseq_puts(s, \"\\n\");\n"
            "\tmutex_unlock(&pcd->fe_lock);\n"
            "\treturn 0;\n"
            "}\n"
            "\n"
            "static int fman_pcd_fe_probe_open(struct inode *inode, struct file *file)\n"
            "{\n"
            "\treturn single_open(file, fman_pcd_fe_probe_show, inode->i_private);\n"
            "}\n"
            "\n"
            "static const struct file_operations fman_pcd_fe_probe_fops = {\n"
            "\t.owner\t\t= THIS_MODULE,\n"
            "\t.open\t\t= fman_pcd_fe_probe_open,\n"
            "\t.read\t\t= seq_read,\n"
            "\t.llseek\t\t= seq_lseek,\n"
            "\t.release\t= single_release,\n"
            "};\n\n"
        )
        src = src.replace(probe_anchor, probe_code + probe_anchor)
        changes += 1
        print("### fman_pcd.c: F-061 fe_probe_show function inserted")
    else:
        print("### fman_pcd.c: F-061 WARNING: probe anchor not found")

# ---- 4. Register debugfs_create_file("fe_probe" ...) ----
if 'debugfs_create_file("fe_probe"' in src:
    print("### fman_pcd.c: F-061 fe_probe debugfs already registered")
else:
    dbg_anchor = 'debugfs_create_file("fe_hashfe"'
    if dbg_anchor in src:
        probe_dbg = (
            '\t\t\tdebugfs_create_file("fe_probe", 0444,\n'
            '\t\t\t\t\t    pcd->debugfs_dir, pcd,\n'
            '\t\t\t\t\t    &fman_pcd_fe_probe_fops);\n'
            '\t\t\t' + dbg_anchor
        )
        src = src.replace(dbg_anchor, probe_dbg)
        changes += 1
        print("### fman_pcd.c: F-061 fe_probe debugfs registered")
    else:
        print("### fman_pcd.c: F-061 WARNING: debugfs anchor not found")

if changes == 0:
    print("### fman_pcd.c: F-061 no changes (all already applied)")
else:
    with open(path, "w") as f:
        f.write(src)
    print(f"### fman_pcd.c: F-061 fe_probe: {changes} change(s) applied")
