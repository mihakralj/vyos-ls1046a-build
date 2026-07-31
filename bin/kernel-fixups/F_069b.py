import sys

path = "drivers/net/ethernet/freescale/fman/fman_pcd.c"
with open(path) as f:
    src = f.read()

changes = 0

ic_probe_body = (
    "/* F-069b v23: page-boundary-aware scan from frame-data vaddr, BOTH directions.\n"
    " * vaddr = phys_to_virt(fd.addr) = frame-data address.\n"
    " * IC is at vaddr - data_offset (data_offset typically 64-256).\n"
    " * Safe backward scan: only up to the page boundary of vaddr.\n"
    " * v23: v22 bounded the backward scan to the page boundary but left the\n"
    " * forward scan fixed at +512 with no check against the page/mapping\n"
    " * extent -- a forward-direction read past the mapped page causes a\n"
    " * level-1 translation fault (board-reproduced 2026-07-31, PC in\n"
    " * fman_pcd_ic_probe_show).  v23 bounds the forward scan symmetrically:\n"
    " * stop 16 bytes (one scan block) before the page end so the last\n"
    " * 4-word read never crosses into the next (possibly unmapped) page.\n"
    " */\n"
    "extern void *fman_pcd_ic_vaddr;\n"
    "extern void *fman_pcd_ic_buf_base;\n"
    "\n"
    "static int fman_pcd_ic_probe_show(struct seq_file *s, void *unused)\n"
    "{\n"
    "\tvoid *vaddr;\n"
    "\tlong page_off;\n"
    "\tint off, max_back, max_fwd;\n"
    "\tunsigned int i, v, nonzero;\n"
    "\n"
    "\tsmp_rmb();\n"
    "\tvaddr = fman_pcd_ic_vaddr;\n"
    "\tif (!vaddr) {\n"
    "\t\tseq_puts(s, \"no frame captured\\n\");\n"
    "\t\treturn 0;\n"
    "\t}\n"
    "\tpage_off = (unsigned long)vaddr & 0xFFF;\n"
    "\tmax_back = page_off < 256 ? page_off : 256;\n"
    "\t/* v23: cap forward scan to stay within vaddr's page, minus one\n"
    "\t * 16-byte block so the last read (off..off+15) never crosses the\n"
    "\t * page boundary into unmapped memory.\n"
    "\t */\n"
    "\tmax_fwd = 4096 - (int)page_off - 16;\n"
    "\tif (max_fwd > 512)\n"
    "\t\tmax_fwd = 512;\n"
    "\tif (max_fwd < 0)\n"
    "\t\tmax_fwd = 0;\n"
    "\tseq_printf(s, \"vaddr=%px page_off=0x%03lx max_back=%d max_fwd=%d\\n\",\n"
    "\t\t   vaddr, page_off, max_back, max_fwd);\n"
    "\t/* Scan -max_back .. +max_fwd around vaddr (covers IC + frame start),\n"
    "\t * bounded on both ends by vaddr's containing page.\n"
    "\t */\n"
    "\tfor (off = -max_back; off < max_fwd; off += 16) {\n"
    "\t\tnonzero = 0;\n"
    "\t\tfor (i = 0; i < 4; i++) {\n"
    "\t\t\tif (((u32 *)vaddr)[off/4 + i]) nonzero++;\n"
    "\t\t}\n"
    "\t\tif (nonzero) {\n"
    "\t\t\tseq_printf(s, \"%+05d:\", off);\n"
    "\t\t\tfor (i = 0; i < 4; i++) {\n"
    "\t\t\t\tv = be32_to_cpu(((u32 *)vaddr)[off/4 + i]);\n"
    "\t\t\t\tseq_printf(s, \" %08x\", v);\n"
    "\t\t\t}\n"
    "\t\t\tseq_puts(s, \"\\n\");\n"
    "\t\t}\n"
    "\t}\n"
    "\treturn 0;\n"
    "}\n"
    "\n"
    "static int fman_pcd_ic_probe_open(struct inode *inode, struct file *file)\n"
    "{\n"
    "\treturn single_open(file, fman_pcd_ic_probe_show, inode->i_private);\n"
    "}\n"
    "\n"
    "static const struct file_operations fman_pcd_ic_probe_fops = {\n"
    "\t.owner\t\t= THIS_MODULE,\n"
    "\t.open\t\t= fman_pcd_ic_probe_open,\n"
    "\t.read\t\t= seq_read,\n"
    "\t.llseek\t\t= seq_lseek,\n"
    "\t.release\t= single_release,\n"
    "};\n\n"
)

if "fman_pcd_ic_probe_show" in src:
    marker = "/* F-069b"
    start = src.find(marker)
    if start > 0:
        end = "static int fman_pcd_fe_probe_show"
        end_pos = src.find(end, start)
        if end_pos > start:
            src = src[:start] + ic_probe_body + src[end_pos:]
            changes += 1
            print("### fman_pcd.c: F-069b v23 ic_probe replaced")
else:
    anchor = "static int fman_pcd_fe_probe_show"
    pos = src.find(anchor)
    if pos > 0:
        src = src[:pos] + ic_probe_body + src[pos:]
        changes += 1
        print("### fman_pcd.c: F-069b v23 ic_probe inserted fresh")

# Inject extern declarations at file scope (needed for fman_pcd_ic_vaddr)
extern_decl = "extern void *fman_pcd_ic_vaddr;\nextern void *fman_pcd_ic_buf_base;\n\n"
if "extern void *fman_pcd_ic_vaddr" not in src:
    # Insert after the SPDX comment block, before first #include
    spdx_end = src.find(" */\n\n")
    if spdx_end > 0:
        insert_at = spdx_end + len(" */\n\n")
        src = src[:insert_at] + extern_decl + src[insert_at:]
        changes += 1
        print("### fman_pcd.c: F-069b v23 extern declarations added")
    else:
        # Fallback: insert before first #include
        first_include = src.find("\n#include")
        if first_include > 0:
            src = src[:first_include+1] + extern_decl + src[first_include+1:]
            changes += 1
            print("### fman_pcd.c: F-069b v23 extern declarations added (fallback)")

fe_dbg = ('\t\t\tdebugfs_create_file("fe_probe", 0444,\n'
          '\t\t\t\t\t    pcd->debugfs_dir, pcd,\n'
          '\t\t\t\t\t    &fman_pcd_fe_probe_fops);')
ic_dbg = ('\t\t\tdebugfs_create_file("fe_probe", 0444,\n'
          '\t\t\t\t\t    pcd->debugfs_dir, pcd,\n'
          '\t\t\t\t\t    &fman_pcd_fe_probe_fops);\n'
          '\t\t\tdebugfs_create_file("ic_probe", 0444,\n'
          '\t\t\t\t\t    pcd->debugfs_dir, pcd,\n'
          '\t\t\t\t\t    &fman_pcd_ic_probe_fops);')
if fe_dbg in src and 'debugfs_create_file("ic_probe"' not in src:
    src = src.replace(fe_dbg, ic_dbg, 1)
    changes += 1

if changes:
    with open(path, "w") as f: f.write(src)
    print(f"### fman_pcd.c: F-069b v23 {changes} change(s) applied")
else:
    print("### fman_pcd.c: F-069b v23 no changes")
