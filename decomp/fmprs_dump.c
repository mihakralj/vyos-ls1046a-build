/* fmprs_dump.c -- read-only FMan soft-parser register/state dump, out-of-tree.
 *
 * Built for a live register-state capture on .116 (real NXP OpenWrt/Mono
 * gateway-dk board running the vendor FMan soft-parser in production),
 * while its soft-parser is actively executing traffic -- comparative input
 * for this project's own (.185) soft-parser scheme-selection investigation.
 * ioremap + readl only, never writes anything.
 *
 * On insmod, dumps to dmesg; also exposes /proc/fmprs_dump for repeat
 * sampling while traffic is live, without reloading the module.
 *
 * Register table confirmed 2026-09-04 against vendor SDK source
 * (fm_prs.c:112, fm_prs.h:308 PRS_REGS_OFFSET=0x840,
 * fsl_fman_prs.h:50-96 struct fman_prs_regs):
 *
 *   FMAN_BASE       = 0x01a00000
 *   FM_MM_PRS       = 0x0c7000   (soft-parser instruction RAM base)
 *   PRS_REGS_OFFSET = 0x000840   (struct fman_prs_regs lives HERE)
 *   => struct fman_prs_regs base = 0x01ac7840
 *        fmpr_rpclim @ +0x00 (0x01ac7840), fmpr_rpimac @ +0x04 (0x01ac7844,
 *        bit0 = SW_PRS_EN / execution-unit enable), pmeec @ +0x08
 *        (0x01ac7848), fmpr_pevr @ +0x20 (0x01ac7860), fmpr_pever @ +0x24
 *        (0x01ac7864), fmpr_perr @ +0x2c (0x01ac786c), fmpr_perer @ +0x30
 *        (0x01ac7870), fmpr_ppsc @ +0x60 (0x01ac78a0)
 *   soft-parser code RAM: 0x01ac7000-0x01ac77ff (dump first 128 bytes)
 *
 * Per-port PMDA (HXS slot LCV/SSA) addressing confirmed against this
 * project's own driver on the same SoC family (LS1046A/DPAA1, so identical
 * CCSR addressing applies to .116):
 *   - HWP_PORT_REGS_OFFSET = 0x800 within a port's BMI/QMI/HWP register
 *     block (work/linux-6.18.44/drivers/net/ethernet/freescale/fman/
 *     fman_port.c:40, :1462-1464 -- port->hwp_regs = base_addr + 0x800)
 *   - port->hwp_regs is struct fman_port_hwp_regs { pmda[16] = {ssa@+0,
 *     lcv@+4}, 8-byte stride } (fman_port.c:306, F-205/F-245 kernel-fixups)
 *   - a port's register block base (base_addr) = FMAN_BASE + 0x80000 +
 *     hw_port_id * 0x1000, confirmed against the qoriq-fman3-0-*.dtsi RX
 *     port "reg" properties (e.g. port@88000 for hw_port_id 0x08,
 *     port@90000 for 0x10 -- work/dtb-build/linux-src/.../
 *     qoriq-fman3-0-1g-0.dtsi, qoriq-fman3-0-10g-0.dtsi)
 *   => port_hwp_base(id) = FMAN_BASE + 0x80000 + id*0x1000 + 0x800
 *   Slot 0 = ETH catch-all, slot 6 = IPv6 (vendor GetPrsHdrNum, F-205).
 *
 * .116's active RX hw_port_ids, resolved 2026-09-04 via read-only SSH
 * (dmesg fsl_mac probe order + /sys/class/net/ethN/device -> fsl,dpaa
 * ethernet@N cell-index, cross-referenced against the same *.dtsi memac
 * cell-index values): eth0=0x09, eth1=0x0c (UP, carrying live traffic),
 * eth2=0x0d, eth3=0x10, eth4=0x11. All five are dumped; eth1/0x0c is the
 * one with traffic actually flowing at capture time.
 *
 * ADDED same day, second pass: fmbm_rfne ("Rx Frame Next Engine") and
 * fmbm_rpso ("Rx Parse Start Offset"), from struct fman_port_rx_bmi_regs
 * (fsl_fman_port.h:151-166 in the vendor tree). This is a THIRD,
 * previously-unexamined soft-parser-adjacent mechanism, distinct from both
 * FMPR_RPIMAC (global execution-unit enable) and pmda[].ssa (per-HXS-slot
 * trigger this project has tested exhaustively): fm_port.c's SetPcd() path
 * (lines ~1336-1399, ~1549-1559) shows fmbm_rfne's low byte
 * (BMI_RFNE_HXS_MASK = 0x000000FF) normally holds the initial hard-parser
 * header-type ("HXS") to start from, via `savedBmiNia |= NIA_ENG_PRS |
 * hdrNum` -- but in a specific configuration (no per-header
 * additionalParams -- i.e. HEADER_TYPE_NONE path) that field is instead
 * overwritten with `initialSwPrs`, a direct soft-parser code offset,
 * bypassing the hard parser's normal per-protocol dispatch entirely.
 * `.116`'s ports DO have per-header pmda[].ssa configured (multiple
 * non-zero HXS slots captured below), which per fm_port.c's branching
 * looks like the *other* path (additionalParams, not HEADER_TYPE_NONE) --
 * so this is expected to read a plain header-type constant, not a
 * soft-parser offset, but capturing it directly is cheaper than guessing.
 *   bmi_regs base = port_hwp_base(id) - PORT_REGS_OFFSET (fmbm_rfne is in
 *   the BMI block, not the HWP/PMDA block -- fman_port.c:1462-1464:
 *   bmi_regs = base_addr + 0, hwp_regs = base_addr + 0x800)
 *   fmbm_rfne @ bmi_regs+0x20, fmbm_rfpne @ bmi_regs+0x28 (unrelated --
 *   "next engine AFTER parser finishes", e.g. KeyGen vs direct enqueue;
 *   dumped anyway since it's adjacent and free), fmbm_rpso @ bmi_regs+0x2c
 */

#include <linux/module.h>
#include <linux/proc_fs.h>
#include <linux/io.h>
#include <linux/seq_file.h>

#define FMAN_BASE		0x01a00000UL
#define FM_MM_PRS		0x0c7000UL
#define PRS_REGS_OFFSET		0x000840UL
#define PRS_REGS_BASE		(FMAN_BASE + FM_MM_PRS + PRS_REGS_OFFSET)
#define PRS_CODE_BASE		(FMAN_BASE + FM_MM_PRS)
#define PRS_CODE_DUMP_LEN	320U	/* covers real ETH (0x06e) + IPv6 (0x110) entry points */

#define PORT_REGS_OFFSET	0x800UL
#define PMDA_STRIDE		8U
#define HXS_SLOT_ETH		0U
#define HXS_SLOT_IPV6		6U

/* .116's active RX hw_port_ids: eth0, eth1(UP/live), eth2, eth3, eth4 */
static const u32 rx_hw_port_ids[] = { 0x09, 0x0c, 0x0d, 0x10, 0x11 };

static inline unsigned long port_hwp_base(u32 hw_port_id)
{
	return FMAN_BASE + 0x80000UL + (unsigned long)hw_port_id * 0x1000UL +
	       PORT_REGS_OFFSET;
}

static void dump_prs_regs(struct seq_file *m)
{
	void __iomem *base = ioremap(PRS_REGS_BASE, 0x100);
	u32 rpclim, rpimac, pmeec, pevr, pever, perr, perer, ppsc;

	if (!base) {
		seq_printf(m, "prs_regs: ioremap failed\n");
		return;
	}
	rpclim = ioread32be(base + 0x00);
	rpimac = ioread32be(base + 0x04);
	pmeec  = ioread32be(base + 0x08);
	pevr   = ioread32be(base + 0x20);
	pever  = ioread32be(base + 0x24);
	perr   = ioread32be(base + 0x2c);
	perer  = ioread32be(base + 0x30);
	ppsc   = ioread32be(base + 0x60);
	iounmap(base);

	seq_printf(m, "prs_regs @0x%08lx: rpclim=0x%08x rpimac=0x%08x (SW_PRS_EN=%u) "
		      "pmeec=0x%08x pevr=0x%08x pever=0x%08x perr=0x%08x perer=0x%08x ppsc=0x%08x\n",
		   PRS_REGS_BASE, rpclim, rpimac, rpimac & 1, pmeec, pevr, pever,
		   perr, perer, ppsc);
}

static void dump_prs_code(struct seq_file *m)
{
	void __iomem *base = ioremap(PRS_CODE_BASE, PRS_CODE_DUMP_LEN);
	unsigned int i;

	if (!base) {
		seq_printf(m, "prs_code: ioremap failed\n");
		return;
	}
	seq_printf(m, "prs_code @0x%08lx (first %u bytes of instruction RAM):\n",
		   PRS_CODE_BASE, PRS_CODE_DUMP_LEN);
	for (i = 0; i < PRS_CODE_DUMP_LEN; i += 4) {
		if (i % 16 == 0)
			seq_printf(m, "  %03x:", i);
		seq_printf(m, " %08x", ioread32be(base + i));
		if (i % 16 == 12)
			seq_printf(m, "\n");
	}
	iounmap(base);
}

static void dump_port_bmi(struct seq_file *m, u32 hw_port_id)
{
	unsigned long bmi_base_phys = port_hwp_base(hw_port_id) - PORT_REGS_OFFSET;
	void __iomem *base = ioremap(bmi_base_phys, 0x30);
	u32 rfne, rfpne, rpso;

	if (!base) {
		seq_printf(m, "port 0x%02x bmi: ioremap failed\n", hw_port_id);
		return;
	}
	rfne  = ioread32be(base + 0x20);
	rfpne = ioread32be(base + 0x28);
	rpso  = ioread32be(base + 0x2c);
	iounmap(base);

	seq_printf(m, "port 0x%02x bmi @0x%08lx: rfne=0x%08x (HXS=0x%02x) rfpne=0x%08x rpso=0x%08x\n",
		   hw_port_id, bmi_base_phys, rfne, rfne & 0xff, rfpne, rpso);
}

static void dump_port_pmda(struct seq_file *m, u32 hw_port_id)
{
	unsigned long base_phys = port_hwp_base(hw_port_id);
	/* Only slots 0 (ETH catch-all) and 6 (IPv6) are read; map just far
	 * enough to cover slot 6. */
	void __iomem *base = ioremap(base_phys, (HXS_SLOT_IPV6 + 1) * PMDA_STRIDE);
	u32 eth_ssa, eth_lcv, v6_ssa, v6_lcv;

	if (!base) {
		seq_printf(m, "port 0x%02x: ioremap failed\n", hw_port_id);
		return;
	}
	eth_ssa = ioread32be(base + HXS_SLOT_ETH * PMDA_STRIDE + 0);
	eth_lcv = ioread32be(base + HXS_SLOT_ETH * PMDA_STRIDE + 4);
	v6_ssa  = ioread32be(base + HXS_SLOT_IPV6 * PMDA_STRIDE + 0);
	v6_lcv  = ioread32be(base + HXS_SLOT_IPV6 * PMDA_STRIDE + 4);
	iounmap(base);

	seq_printf(m, "port 0x%02x pmda @0x%08lx: slot0(ETH) ssa=0x%08x lcv=0x%08x  "
		      "slot6(IPv6) ssa=0x%08x lcv=0x%08x\n",
		   hw_port_id, base_phys, eth_ssa, eth_lcv, v6_ssa, v6_lcv);
}

static int fmprs_dump_show(struct seq_file *m, void *v)
{
	int i;

	dump_prs_regs(m);
	dump_prs_code(m);
	for (i = 0; i < ARRAY_SIZE(rx_hw_port_ids); i++) {
		dump_port_bmi(m, rx_hw_port_ids[i]);
		dump_port_pmda(m, rx_hw_port_ids[i]);
	}
	return 0;
}

static int fmprs_dump_open(struct inode *inode, struct file *file)
{
	return single_open(file, fmprs_dump_show, NULL);
}

static const struct proc_ops fmprs_dump_fops = {
	.proc_open	= fmprs_dump_open,
	.proc_read	= seq_read,
	.proc_lseek	= seq_lseek,
	.proc_release	= single_release,
};

static struct proc_dir_entry *fmprs_dump_entry;

static int __init fmprs_dump_init(void)
{
	fmprs_dump_entry = proc_create("fmprs_dump", 0444, NULL, &fmprs_dump_fops);
	if (!fmprs_dump_entry)
		return -ENOMEM;
	pr_info("fmprs_dump: loaded, cat /proc/fmprs_dump to sample\n");
	return 0;
}

static void __exit fmprs_dump_exit(void)
{
	proc_remove(fmprs_dump_entry);
	pr_info("fmprs_dump: unloaded\n");
}

module_init(fmprs_dump_init);
module_exit(fmprs_dump_exit);
MODULE_LICENSE("GPL");
MODULE_DESCRIPTION("Read-only FMan soft-parser register/state dump");
