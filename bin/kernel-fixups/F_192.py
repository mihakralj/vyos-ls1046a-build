"""F-192: bounded, read-only FE workspace diagnostic for the E2 discriminator.

The production flow-learning test now proves both vendor nodes, the 14-byte
flow key, ehash insertion, and hardware-offload ownership.  Frames still
stall, so the remaining discrimination is whether the FE machine allocates a
workspace and reaches the ehash/action phase.  F-192 adds only a debugfs
readout for the already-owned per-port FmPortSetFESupport state:

* params-page +0x54 (management-index MURAM offset) and +0x58 (depletion);
* the bounded 5+tnums-byte management index; and
* the first 32 bytes of its owned workspace pool.

It neither changes a descriptor nor writes MURAM, DDR, KeyGen, or the packet
path.  A before/after low-rate traffic pair reports allocation/depletion state
without relying on the retired unbounded probes.  This is a temporary
DIAGNOSTIC build and requires CONFIG_FMAN_PCD_DEBUG_FS=y.
"""

import sys

changes = 0
path = "drivers/net/ethernet/freescale/fman/fman_pcd.c"
with open(path) as f:
    src = f.read()

marker = "F-192(workspace-snapshot)"
if marker in src:
    print("### F-192: workspace snapshot already applied")
else:
    anchor = "static int fman_pcd_fe_arm_show(struct seq_file *s, void *unused)\n"
    if anchor not in src:
        print("### F-192: FATAL: fe_arm_show anchor not found")
        sys.exit(1)

    block = r'''/*
 * F-192(workspace-snapshot): E2 read-only snapshot of the FE workspace.
 * All addresses come from FmPortSetFESupport-owned params/index allocations;
 * do not use this routine for arbitrary MURAM reads.
 */
static int fman_pcd_fe_workspace_show(struct seq_file *s, void *unused)
{
	struct fman_pcd *pcd = s->private;
	struct muram_info *muram = fman_get_muram(pcd->fman);
	int port_id;

	if (!muram)
		return -ENODEV;

	for (port_id = 0x08; port_id <= 0x11; port_id++) {
		struct fman_port *port = fman_port_lookup_rx(pcd->fman, port_id);
		void __iomem *pp, *idx, *pool;
		u32 pp_off, idx_off, pool_off, depleted;
		u8 tnums, i;

		if (!port)
			continue;
		pp_off = fman_port_get_params_page(port);
		if (IS_ERR_VALUE(pp_off))
			continue;
		pp = fman_muram_offset_to_vbase(muram, pp_off);
		idx_off = ioread32be((u8 __iomem *)pp + 0x54);
		depleted = ioread32be((u8 __iomem *)pp + 0x58);
		tnums = fman_port_get_total_tnums(port);
		seq_printf(s, "port=0x%02x params=0x%05x idx=0x%05x depleted=0x%08x tnums=%u\n",
			   port_id, pp_off, idx_off, depleted, tnums);
		if (!idx_off || idx_off >= 0x60000)
			continue;

		idx = fman_muram_offset_to_vbase(muram, idx_off);
		pool_off = ioread32be(idx) & 0x00ffff00;
		seq_puts(s, "  index:");
		for (i = 0; i < min_t(u8, 5 + tnums, 32); i++)
			seq_printf(s, " %02x", ioread8((u8 __iomem *)idx + i));
		seq_putc(s, '\n');
		if (!pool_off || pool_off >= 0x60000)
			continue;

		pool = fman_muram_offset_to_vbase(muram, pool_off);
		seq_printf(s, "  pool=0x%05x first32:", pool_off);
		for (i = 0; i < 32; i++)
			seq_printf(s, " %02x", ioread8((u8 __iomem *)pool + i));
		seq_putc(s, '\n');
	}
	return 0;
}

static int fman_pcd_fe_workspace_open(struct inode *inode, struct file *file)
{
	return single_open(file, fman_pcd_fe_workspace_show, inode->i_private);
}

static const struct file_operations fman_pcd_fe_workspace_fops = {
	.owner		= THIS_MODULE,
	.open		= fman_pcd_fe_workspace_open,
	.read		= seq_read,
	.llseek		= seq_lseek,
	.release	= single_release,
};

'''
    src = src.replace(anchor, block + anchor, 1)
    changes += 1
    print("### fman_pcd.c: F-192 workspace snapshot handler applied")

    reg_anchor = 'debugfs_create_file("fe_ehash_stats", 0444,'
    pos = src.find(reg_anchor)
    if pos < 0:
        print("### F-192: FATAL: fe_ehash_stats registration anchor not found")
        sys.exit(1)
    end = src.find('\n', src.find(';', pos)) + 1
    if end <= pos:
        print("### F-192: FATAL: fe_ehash_stats registration terminator not found")
        sys.exit(1)
    line_start = src.rfind('\n', 0, pos) + 1
    indent = src[line_start:pos]
    reg = src[pos:end] + (f'{indent}debugfs_create_file("fe_workspace", 0444,\n'
                           f'{indent}\t\t\t    pcd->debugfs_dir, pcd,\n'
                           f'{indent}\t\t\t    &fman_pcd_fe_workspace_fops);\n')
    src = src[:pos] + reg + src[end:]
    changes += 1
    print("### fman_pcd.c: F-192 workspace snapshot registration applied")

if changes:
    with open(path, "w") as f:
        f.write(src)
    print(f"### F-192 complete ({changes} blocks)")
else:
    sys.exit(1)
