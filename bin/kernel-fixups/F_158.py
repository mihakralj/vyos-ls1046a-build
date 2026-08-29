"""F-158: debugfs dump node (fe_scaffold) — ground truth on CC match-table layout.

STRATEGIC PURPOSE (option B, 2026-08-01): every prior "HIT still fails"
conclusion was blind because HIT and MISS converged on kernel FQ 0x200.
F-157 fixed that (ENQ -> dedicated TX FQ 0x2b9), giving a real discriminator:
a matching RST STILL reaches eth3 kernel tcpdump -> the CC engine is NOT
dispatching matching frames to the FE-VM.  Remaining question is the CC
match-table LAYOUT: does what F-148 wrote (13-byte EKFC key + 0xff mask in a
32B-per-row table) match what the CC comparator reads against the KG output?
STRICT_DEVMEM blocks /dev/mem reads of MURAM (all zeros), so only a
kernel-side node can dump the actual scaffold MURAM.

This node dumps, for every armed port:
  - group table (gro): CONT_LOOKUP w0..w3 (numKeys, matchTableAddr, keySize)
  - match table (mto): 64 B = 2 rows x (16B key + 16B mask), printed as hex +
    the first 16 bytes interpreted as a byte-ordered flow key
  - AD table (ato): 32 B = 2 x 16B AD entries (HIT-AD + miss-AD), raw hex
  - the port's fe_root_ad_off / ehash keysize via pcd for cross-reference

The hex dump lets us SEE whether the mask is 0xff where expected, whether the
key bytes are where cc_pack_key (patch 0098) would put them, and whether the
HIT-AD is the real FE_ENTER copy.  This is the diagnostic oracle for the
match-layout hypothesis before writing another F-148 variant.
"""

import sys, os

kroot = "drivers/net/ethernet/freescale/fman"
pcd_c = os.path.join(kroot, "fman_pcd.c")

if not os.path.exists(pcd_c):
    print("### F-158: fman_pcd.c not found")
    sys.exit(0)

with open(pcd_c) as f:
    src = f.read()

changes = 0

# ── 1. show function + helpers, inserted before fman_pcd_fe_arm_show ──
show_anchor = "static int fman_pcd_fe_arm_show"
if "fman_pcd_fe_scaffold_show" in src:
    print("### F-158: fe_scaffold dump already present")
elif show_anchor in src:
    dump_func = (
        "/* F-158: ground-truth dump of the CC CONT_LOOKUP scaffold MURAM.\n"
        " * STRICT_DEVMEM blocks /dev/mem, so this kernel node is the only way\n"
        " * to see what F-148 actually wrote vs what the CC comparator reads.\n"
        " */\n"
        "static void fman_pcd_fe_dump_bytes(struct seq_file *s, void __iomem *base,\n"
        "\t\t\t\t  int len)\n"
        "{\n"
        "\tint i;\n"
        "\n"
        "\tfor (i = 0; i < len; i++) {\n"
        "\t\tif (i % 16 == 0)\n"
        "\t\t\tseq_printf(s, \"\\n  %04x:\", i);\n"
        "\t\tseq_printf(s, \" %02x\", ioread8((u8 __iomem *)base + i));\n"
        "\t}\n"
        "\tseq_puts(s, \"\\n\");\n"
        "}\n"
        "\n"
        "static int fman_pcd_fe_scaffold_show(struct seq_file *s, void *unused)\n"
        "{\n"
        "\tstruct fman_pcd *pcd = s->private;\n"
        "\tstruct fman_pcd_fe_port *fp;\n"
        "\tstruct muram_info *muram = fman_get_muram(pcd->fman);\n"
        "\n"
        "\tif (!muram)\n"
        "\t\treturn 0;\n"
        "\n"
        "\tseq_puts(s, \"fe_scaffold dump (per armed port)\\n\");\n"
        "\tlist_for_each_entry(fp, &pcd->fe_ports, node) {\n"
        "\t\tif (!fp->scaffold_gro && !fp->scaffold_mto && !fp->scaffold_ato)\n"
        "\t\t\tcontinue;\n"
        "\n"
        "\t\tseq_printf(s, \"\\nport 0x%02x\\n\", fp->port_id);\n"
        "\t\tseq_printf(s, \"  gro=0x%lx mto=0x%lx ato=0x%lx root_ad=0x%lx\\n\",\n"
        "\t\t\t   fp->scaffold_gro, fp->scaffold_mto, fp->scaffold_ato,\n"
        "\t\t\t   pcd->fe_root_ad_off);\n"
        "\n"
        "\t\t/* group table: 4 words */\n"
        "\t\tif (fp->scaffold_gro) {\n"
        "\t\t\tvoid __iomem *gt = (void __iomem *)\n"
        "\t\t\t\tfman_muram_offset_to_vbase(muram, fp->scaffold_gro);\n"
        "\t\t\tu32 w0 = ioread32be(gt + 0);\n"
        "\t\t\tu32 w1 = ioread32be(gt + 4);\n"
        "\t\t\tu32 w2 = ioread32be(gt + 8);\n"
        "\t\t\tu32 w3 = ioread32be(gt + 12);\n"
        "\n"
        "\t\t\tseq_printf(s, \"  group: w0=%08x w1=%08x w2=%08x w3=%08x\\n\",\n"
        "\t\t\t\t   w0, w1, w2, w3);\n"
        "\t\t\tseq_printf(s, \"    numKeys=%u matchTableAddr=0x%06x \"\n"
        "\t\t\t\t   \"adTableAddr=0x%06x keySize8=%u\\n\",\n"
        "\t\t\t\t   (w0 >> 24) & 0xff, w0 & 0xffffff, w1 & 0xffffff,\n"
        "\t\t\t\t   ((w2 >> 24) & 0xff) + 1);\n"
        "\t\t}\n"
        "\n"
        "\t\t/* match table: 2 rows x 32B (key16+mask16), 64 B */\n"
        "\t\tif (fp->scaffold_mto) {\n"
        "\t\t\tvoid __iomem *mt = (void __iomem *)\n"
        "\t\t\t\tfman_muram_offset_to_vbase(muram, fp->scaffold_mto);\n"
        "\t\t\tseq_puts(s, \"  match table (64 B = row0 key+mask, row1 key+mask):\");\n"
        "\t\t\tfman_pcd_fe_dump_bytes(s, mt, 64);\n"
        "\t\t\t/* decode row0 key bytes 0..12 as the likely EKFC 5-tuple */\n"
        "\t\t\t{\n"
        "\t\t\t\tu8 k[16];\n"
        "\t\t\t\tint j;\n"
        "\t\t\t\tfor (j = 0; j < 16; j++)\n"
        "\t\t\t\t\tk[j] = ioread8((u8 __iomem *)mt + j);\n"
        "\t\t\t\tseq_printf(s, \"    row0.key: %02x%02x%02x%02x %02x%02x%02x%02x \"\n"
        "\t\t\t\t\t   \"%02x %02x%02x %02x%02x [%02x%02x%02x]\\n\",\n"
        "\t\t\t\t\t   k[0],k[1],k[2],k[3], k[4],k[5],k[6],k[7], k[8],\n"
        "\t\t\t\t\t   k[9],k[10], k[11],k[12], k[13],k[14],k[15]);\n"
        "\t\t\t\tseq_printf(s, \"    row0.mask: %02x%02x%02x%02x %02x%02x%02x%02x \"\n"
        "\t\t\t\t\t   \"%02x %02x%02x %02x%02x [%02x%02x%02x]\\n\",\n"
        "\t\t\t\t\t   ioread8((u8 __iomem *)mt+16), ioread8((u8 __iomem *)mt+17),\n"
        "\t\t\t\t\t   ioread8((u8 __iomem *)mt+18), ioread8((u8 __iomem *)mt+19),\n"
        "\t\t\t\t\t   ioread8((u8 __iomem *)mt+20), ioread8((u8 __iomem *)mt+21),\n"
        "\t\t\t\t\t   ioread8((u8 __iomem *)mt+22), ioread8((u8 __iomem *)mt+23),\n"
        "\t\t\t\t\t   ioread8((u8 __iomem *)mt+24), ioread8((u8 __iomem *)mt+25),\n"
        "\t\t\t\t\t   ioread8((u8 __iomem *)mt+26), ioread8((u8 __iomem *)mt+27),\n"
        "\t\t\t\t\t   ioread8((u8 __iomem *)mt+28), ioread8((u8 __iomem *)mt+29),\n"
        "\t\t\t\t\t   ioread8((u8 __iomem *)mt+30), ioread8((u8 __iomem *)mt+31));\n"
        "\t\t\t}\n"
        "\t\t}\n"
        "\n"
        "\t\t/* AD table: 2 x 16B AD entries */\n"
        "\t\tif (fp->scaffold_ato) {\n"
        "\t\t\tvoid __iomem *at = (void __iomem *)\n"
        "\t\t\t\tfman_muram_offset_to_vbase(muram, fp->scaffold_ato);\n"
        "\t\t\tseq_puts(s, \"  AD table (32 B):\");\n"
        "\t\t\tfman_pcd_fe_dump_bytes(s, at, 32);\n"
        "\t\t}\n"
        "\t}\n"
        "\treturn 0;\n"
        "}\n"
        "\n"
        "static int fman_pcd_fe_scaffold_open(struct inode *inode, struct file *file)\n"
        "{\n"
        "\treturn single_open(file, fman_pcd_fe_scaffold_show, inode->i_private);\n"
        "}\n"
        "\n"
        "static const struct file_operations fman_pcd_fe_scaffold_fops = {\n"
        "\t.owner\t\t= THIS_MODULE,\n"
        "\t.open\t\t= fman_pcd_fe_scaffold_open,\n"
        "\t.read\t\t= seq_read,\n"
        "\t.llseek\t\t= seq_lseek,\n"
        "\t.release\t= single_release,\n"
        "};\n"
        "\n"
    )
    src = src.replace(show_anchor, dump_func + show_anchor, 1)
    changes += 1
    print("### F-158: fe_scaffold show function inserted before fe_arm_show")
else:
    print("### F-158: FATAL: fe_arm_show anchor not found")
    sys.exit(1)

# ── 2. register fe_scaffold debugfs node ──
reg_anchor = 'debugfs_create_file("fe_arm", 0600,'
reg_line = ('\t\tdebugfs_create_file("fe_scaffold", 0444, pcd->debugfs_dir, pcd, '
            '&fman_pcd_fe_scaffold_fops);\n')
if "debugfs_create_file(\"fe_scaffold\", 0444" in src:
    print("### F-158: fe_scaffold node already registered")
elif reg_anchor in src:
    src = src.replace(reg_anchor, reg_line + "\t" + reg_anchor, 1)
    changes += 1
    print("### F-158: fe_scaffold debugfs node registered")
else:
    print("### F-158: FATAL: fe_arm registration anchor not found")
    sys.exit(1)

if changes:
    with open(pcd_c, "w") as f:
        f.write(src)
    print(f"### F-158: {changes} change(s) applied")
else:
    print("### F-158: no changes applied")
    sys.exit(1)
