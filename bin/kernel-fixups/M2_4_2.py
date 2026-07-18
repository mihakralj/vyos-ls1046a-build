import sys

path = "drivers/net/ethernet/freescale/fman/fman_pcd.c"
with open(path) as f:
    src = f.read()

changes = 0

# ---- 1. Add fe_pool_off to struct ----
if "unsigned long fe_pool_off;" in src:
    print("### fman_pcd.c: F-061 fe_pool_off field already present")
else:
    struct_anchor = "int fe_refcount;"
    if struct_anchor in src:
        src = src.replace(struct_anchor,
            struct_anchor + "\n\tunsigned long fe_pool_off;\t/* F-061 v6: mid-pool FE slot MURAM offset for fe_probe */", 1)
        changes += 1
        print("### fman_pcd.c: F-061 fe_pool_off field added")
    else:
        print("### fman_pcd.c: F-061 WARNING: struct anchor not found")

# ---- 2. Save fe_pool_off at slot 5 using a UNIQUE anchor ----
# v5 BUG: alloc_anchor matched on the WRONG list_add_tail call (there are
# multiple list_add_tail calls in fman_pcd.c). The replace(..., 1) replaced
# the first match, which was NOT in fe_pool_alloc, corrupting the pool.
# v6 FIX: use a compound anchor that includes context unique to the pool
# allocation loop: gen_pool_alloc + list_add_tail.
unique_anchor = "gen_pool_alloc(pcd->fe_gen_pool"
if "if (i == 5)" in src and "pcd->fe_pool_off = off" in src:
    print("### fman_pcd.c: F-061 fe_pool_off v4 save already present")
else:
    # Remove any broken v5 save that might be left
    for broken in ["\t\tif (i == 5)\t/* F-061 v4: slot 5 past 3 singletons */\n\t\t\tpcd->fe_pool_off = off;;",
                   "\t\tpcd->fe_pool_off = off;\t/* F-061 v2: save last pool slot for fe_probe (overwritten each iter) */",
                   "\t\tif (!pcd->fe_pool_off)\t/* F-061: save first slot for fe_probe */\n\t\t\tpcd->fe_pool_off = off;"]:
        if broken in src:
            src = src.replace(broken, "")
            changes += 1
            print(f"### fman_pcd.c: F-061 removed broken save: {broken[:40]}...")

    # Find the actual pool alloc loop body and add the save AFTER list_add_tail
    # Use a unique enough anchor: the i * FMAN_PCD_FE_MAX_SIZE pattern
    alloc_pattern = "\t\tpcd->fe_obj[i].muram_off = off;"
    if alloc_pattern in src:
        # Find the list_add_tail that immediately follows this line
        tail_anchor = alloc_pattern + "\n\t\tlist_add_tail(&pcd->fe_obj[i].node, &pcd->fe_available);"
        if tail_anchor in src:
            new_block = tail_anchor + "\n\t\tif (i == 5)\t/* F-061 v6: slot 5 past 3 singletons */\n\t\t\tpcd->fe_pool_off = off;"
            src = src.replace(tail_anchor, new_block, 1)
            changes += 1
            print("### fman_pcd.c: F-061 v6: fe_pool_off save at slot 5 using unique anchor")
        else:
            # Fallback: insert after the gen_pool_alloc+list_add_tail pair
            # just after the for loop opening
            loop_body = "\tfor (i = 0; i < ARRAY_SIZE(pcd->fe_obj); i++) {"
            if loop_body in src:
                save_line = "\t\tif (i == 5)\t/* F-061 v6: slot 5 past 3 singletons */\n\t\t\tpcd->fe_pool_off = off;\n"
                # Insert inside the loop body after the first statement
                first_stmt = "\n\t\tpcd->fe_obj[i].muram_off = off;"
                if first_stmt in src:
                    src = src.replace(first_stmt, first_stmt + "\n" + save_line, 1)
                    changes += 1
                    print("### fman_pcd.c: F-061 v6: save inserted after fe_obj[i] init")
            else:
                print("### fman_pcd.c: F-061 WARNING: could not find loop anchor")
    else:
        print("### fman_pcd.c: F-061 WARNING: could not find fe_obj init anchor")

# ---- 3. Add fe_probe_show function (v5: 64-word scan, non-zero filter) ----
old_probe_anchor = "static int fman_pcd_fe_port_show(struct seq_file *s, void *unused)"
new_probe_code = (
    "/* F-061 v6: fe_probe debugfs - scan FE workspace for KG key\n"
    " * Reads 64 u32 words (256B), past the 246B ALLOCATE workspace.\n"
    " * Shows only non-zero words to find the key wherever it lands.\n"
    " */\n"
    "static int fman_pcd_fe_probe_show(struct seq_file *s, void *unused)\n"
    "{\n"
    "\tstruct fman_pcd *pcd = s->private;\n"
    "\tstruct muram_info *muram = fman_get_muram(pcd->fman);\n"
    "\tvoid __iomem *ws_base;\n"
    "\tunsigned int i;\n"
    "\tu32 v;\n"
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
    "\tfor (i = 0; i < 64; i++) {\n"
    "\t\tv = ioread32be((u32 __iomem *)ws_base + i);\n"
    "\t\tif (v)\n"
    "\t\t\tseq_printf(s, \" [%02d]=%08x\", i, v);\n"
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

if "fman_pcd_fe_probe_show" not in src:
    if old_probe_anchor in src:
        src = src.replace(old_probe_anchor, new_probe_code + old_probe_anchor)
        changes += 1
        print("### fman_pcd.c: F-061 fe_probe_show v6 inserted (64 words, non-zero filter)")
    else:
        print("### fman_pcd.c: F-061 WARNING: probe anchor not found")
else:
    # Already present - upgrade to v6: expand loop + non-zero filter
    old_loop = "for (i = 0; i < 8; i++) {"
    new_loop = "for (i = 0; i < 64; i++) {"
    if old_loop in src:
        src = src.replace(old_loop, new_loop)
        changes += 1
        print("### fman_pcd.c: F-061 loop expanded 8->64 words")
    # Add non-zero filter
    old_print = 'seq_printf(s, " [%02d]=%08x", i, v);'
    new_print = 'if (v)\n\t\t\tseq_printf(s, " [%02d]=%08x", i, v);'
    if old_print in src and "if (v)" not in src:
        src = src.replace(old_print, new_print)
        changes += 1
        print("### fman_pcd.c: F-061 non-zero filter added")

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
    print("### fman_pcd.c: F-061 v6: no changes (all already applied)")
else:
    with open(path, "w") as f:
        f.write(src)
    print(f"### fman_pcd.c: F-061 v6: {changes} change(s) applied")
