import sys

path = "drivers/net/ethernet/freescale/fman/fman_pcd.c"
try:
    with open(path) as f: src = f.read()
except FileNotFoundError:
    print("### F-072 v3 file not found")
    sys.exit(0)

changes = 0

anchor = "static int fman_pcd_fe_arm_engage(struct fman_pcd *pcd, const char *args)"
if anchor not in src:
    print("### F-072 v3 fe_arm_engage anchor not found")
    sys.exit(0)

setup_func = (
    "/* F-072 v3: Port FmPortSetFESupport. Internal FE buffer pool + mgmt index in MURAM.\n"
    " * SDK 999-patch ~L14545. tnums*512B pool (256-align auto via gen_pool granule),\n"
    " * (5+tnums) byte index. Params page +0x54=index MURAM off, +0x58=0.\n"
    " * port_id is u8 hw port number (not port->port_id — struct fman_port is opaque).\n"
    " */\n"
    "static int fman_pcd_fe_buffer_setup(struct fman_pcd *pcd,\n"
    "                                    struct fman_port *port, u8 port_id)\n"
    "{\n"
    "    struct muram_info *muram = fman_get_muram(pcd->fman);\n"
    "    u8 tnums;\n"
    "    unsigned long pool_off, idx_off;\n"
    "    u32 pp_off;\n"
    "    void __iomem *pp, *idx;\n"
    "    int i;\n"
    "\n"
    "    static const unsigned int BMI_FIFO_UNITS = 0x100;\n"
    "\n"
    "    if (!muram || !port)\n"
    "        return -EINVAL;\n"
    "\n"
    "    tnums = fman_port_get_total_tnums(port);\n"
    "    if (!tnums)\n"
    "        return -EINVAL;\n"
    "\n"
    "    pp_off = fman_port_get_params_page(port);\n"
    "    if (IS_ERR_VALUE(pp_off))\n"
    "        return (int)pp_off;\n"
    "\n"
    "    /* gen_pool MURAM granule is 256 (MURAM_ORDER=8), so alloc is 256-aligned */\n"
    "    pool_off = fman_pcd_muram_alloc(pcd, tnums * BMI_FIFO_UNITS * 2);\n"
    "    if (IS_ERR_VALUE(pool_off))\n"
    "        return (int)pool_off;\n"
    "\n"
    "    memset_io(fman_muram_offset_to_vbase(muram, pool_off), 0,\n"
    "              tnums * BMI_FIFO_UNITS * 2);\n"
    "\n"
    "    idx_off = fman_pcd_muram_alloc(pcd, 5 + tnums);\n"
    "    if (IS_ERR_VALUE(idx_off)) {\n"
    "        fman_pcd_muram_free(pcd, pool_off, tnums * BMI_FIFO_UNITS * 2);\n"
    "        return (int)idx_off;\n"
    "    }\n"
    "\n"
    "    idx = fman_muram_offset_to_vbase(muram, idx_off);\n"
    "    iowrite32be(pool_off, idx);\n"
    "    iowrite8(4, idx);\n"
    "    for (i = 0; i < tnums; i++)\n"
    "        iowrite8(i, (void __iomem *)((u8 __iomem *)idx + 4 + i));\n"
    "    iowrite8(0xFF, (void __iomem *)((u8 __iomem *)idx + 4 + tnums));\n"
    "\n"
    "    pp = fman_muram_offset_to_vbase(muram, pp_off);\n"
    "    iowrite32be((u32)idx_off, (void __iomem *)((u8 __iomem *)pp + 0x54));\n"
    "    iowrite32be(0,          (void __iomem *)((u8 __iomem *)pp + 0x58));\n"
    "\n"
    "    pr_info(\"fman_pcd: F-072 port 0x%02x FE buffer pool=%#lx idx=%#lx tnums=%u\\n\",\n"
    "            port_id, pool_off, idx_off, tnums);\n"
    "    return 0;\n"
    "}\n"
    "\n"
    "static void fman_pcd_fe_buffer_teardown(struct fman_pcd *pcd,\n"
    "                                       struct fman_port *port)\n"
    "{\n"
    "    struct muram_info *muram = fman_get_muram(pcd->fman);\n"
    "    u32 pp_off;\n"
    "    void __iomem *pp;\n"
    "\n"
    "    if (!muram || !port)\n"
    "        return;\n"
    "    pp_off = fman_port_get_params_page(port);\n"
    "    if (IS_ERR_VALUE(pp_off))\n"
    "        return;\n"
    "    pp = fman_muram_offset_to_vbase(muram, pp_off);\n"
    "    iowrite32be(0, (void __iomem *)((u8 __iomem *)pp + 0x54));\n"
    "}\n"
    "\n"
)

src = src.replace(anchor, setup_func + anchor, 1)
changes += 1
print("### fman_pcd.c: F-072 v3 buffer setup/teardown functions added")

call_anchor = "/* F-079: CONT_LOOKUP pass-through scaffold (RM 8.7.4.1)."
if call_anchor in src:
    setup_call = (
        "\t/* F-072 v3: arm FE buffer pool BEFORE scaffold */\n"
        "\t{\n"
        "\t\tstruct fman_port *rxport = fman_port_lookup_rx(pcd->fman, (u8)port_id);\n"
        "\t\tint _bfr;\n"
        "\t\tif (!rxport)\n"
        "\t\t\treturn -ENODEV;\n"
        "\t\t_bfr = fman_pcd_fe_buffer_setup(pcd, rxport, (u8)port_id);\n"
        "\t\tif (_bfr)\n"
        "\t\t\treturn _bfr;\n"
        "\t}\n"
        "\n"
        "\t" + call_anchor
    )
    src = src.replace(call_anchor, setup_call, 1)
    changes += 1
    print("### fman_pcd.c: F-072 v3 buffer setup call inserted in engage")

disanchor = "fman_pcd_kg_port_disarm_fe(pcd, (u8)port_id, 0);"
if disanchor in src:
    teardown_call = (
        "\t/* F-072 v3: tear down FE buffer BEFORE PCD disarm */\n"
        "\t{\n"
        "\t\tstruct fman_port *rxport = fman_port_lookup_rx(pcd->fman, (u8)port_id);\n"
        "\t\tif (rxport)\n"
        "\t\t\tfman_pcd_fe_buffer_teardown(pcd, rxport);\n"
        "\t}\n"
        "\n"
        "\t" + disanchor
    )
    src = src.replace(disanchor, teardown_call, 1)
    changes += 1
    print("### fman_pcd.c: F-072 v3 buffer teardown call inserted in disengage")

dbg_anchor = 'debugfs_create_file("fe_arm", 0600,'
if dbg_anchor in src:
    dbg_node = 'debugfs_create_file("fe_buffer", 0444, pcd->debugfs_dir, pcd, &fman_pcd_fe_buffer_fops);\n\t\t\t'
    src = src.replace(dbg_anchor, dbg_node + dbg_anchor, 1)
    changes += 1
    print("### fman_pcd.c: F-072 v3 fe_buffer debugfs registered")

buf_show_anchor = "static int fman_pcd_fe_arm_show"
if buf_show_anchor in src:
    buffer_show = (
        "static int fman_pcd_fe_buffer_show(struct seq_file *s, void *unused)\n"
        "{\n"
        "\tstruct fman_pcd *pcd = s->private;\n"
        "\tstruct muram_info *muram = fman_get_muram(pcd->fman);\n"
        "\tint port_id;\n"
        "\tvoid __iomem *pp;\n"
        "\tu32 v54, v58;\n"
        "\n"
        "\tfor (port_id = 0x08; port_id <= 0x11; port_id++) {\n"
        "\t\tstruct fman_port *port = fman_port_lookup_rx(pcd->fman, port_id);\n"
        "\t\tu32 pp_off;\n"
        "\t\tif (!port)\n"
        "\t\t\tcontinue;\n"
        "\t\tpp_off = fman_port_get_params_page(port);\n"
        "\t\tif (IS_ERR_VALUE(pp_off))\n"
        "\t\t\tcontinue;\n"
        "\t\tpp = fman_muram_offset_to_vbase(muram, pp_off);\n"
        "\t\tv54 = ioread32be((void __iomem *)((u8 __iomem *)pp + 0x54));\n"
        "\t\tv58 = ioread32be((void __iomem *)((u8 __iomem *)pp + 0x58));\n"
        "\t\tseq_printf(s, \"port 0x%02x: +0x54=%#010x +0x58=%#010x tnums=%u\\n\",\n"
        "\t\t\t   port_id, v54, v58, fman_port_get_total_tnums(port));\n"
        "\t}\n"
        "\treturn 0;\n"
        "}\n"
        "\n"
        "static int fman_pcd_fe_buffer_open(struct inode *inode, struct file *file)\n"
        "{\n"
        "\treturn single_open(file, fman_pcd_fe_buffer_show, inode->i_private);\n"
        "}\n"
        "\n"
        "static const struct file_operations fman_pcd_fe_buffer_fops = {\n"
        "\t.owner\t\t= THIS_MODULE,\n"
        "\t.open\t\t= fman_pcd_fe_buffer_open,\n"
        "\t.read\t\t= seq_read,\n"
        "\t.llseek\t\t= seq_lseek,\n"
        "\t.release\t= single_release,\n"
        "};\n"
        "\n"
    )
    src = src.replace(buf_show_anchor, buffer_show + buf_show_anchor, 1)
    changes += 1
    print("### fman_pcd.c: F-072 v3 fe_buffer show function added")

if changes:
    with open(path, "w") as f: f.write(src)
    print(f"### fman_pcd.c: F-072 v3 {changes} change(s) applied")
else:
    print("### fman_pcd.c: F-072 v3 no changes applied")
