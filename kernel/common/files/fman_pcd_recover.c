// SPDX-License-Identifier: GPL-2.0-only
/*
 * fman_pcd_port_recover — rebuild FE buffer free-list after BMI stall
 *
 * Port-scoped software de-wedge per specs/dpaa1-afxdp-modernization-spec.md §5.9.
 * Recovers a stalled/deaf RX port from FE-VM workspace corruption without
 * requiring a cold boot.
 *
 * Copyright (C) 2026 VyOS LS1046A
 */

#include "fman_pcd.h"
#include "fman.h"
#include <linux/io.h>

/**
 * fman_pcd_port_recover - rebuild FE buffer free-list after BMI stall
 * @pcd: FMan PCD instance
 * @port_id: hardware RX port number (e.g. 0x11 for eth4)
 *
 * Rebuilds the per-port internal FE buffer free-list by zeroing the
 * MURAM pool memory and re-initialising the management index, then
 * zeroing the depletion counter at params page +0x58.
 *
 * The FE buffer pool (allocated by FmPortSetFESupport) consists of:
 *   pool:  tnums * 512 bytes (256-aligned in MURAM)
 *   index: 5 + tnums bytes (4-aligned in MURAM), pointed to by +0x54
 *
 * Recovery sequence:
 *   1. Look up the RX port and read params page +0x54/+0x58
 *   2. If +0x54 is zero, the pool was never armed — nothing to rebuild
 *   3. Compute pool offset (idx_off - tnums * 512, 256-aligned)
 *   4. Zero the entire pool memory
 *   5. Zero the management index (marks all slots as free)
 *   6. Write +0x58 = 0 (zero depletion counter)
 *   7. Read-back verify
 *
 * Return: 0 on success, -EAGAIN if readback fails (cold boot may be needed),
 *         -ENODEV if port not found, -EINVAL if pcd is NULL, -EIO if MURAM
 *         access fails.
 */
int fman_pcd_port_recover(struct fman_pcd *pcd, u8 port_id)
{
	struct fman_port *port;
	void __iomem *pp, *pool, *idx;
	u32 pp_off, idx_off, pool_off, v54, v58;
	u8 tnums;
	unsigned long pool_sz, idx_sz;
	struct muram_info *muram;
	int ret = 0;

	if (!pcd)
		return -EINVAL;

	port = fman_port_lookup_rx(pcd->fman, port_id);
	if (!port)
		return -ENODEV;

	pp_off = fman_port_get_params_page(port);
	if (IS_ERR_VALUE(pp_off))
		return (int)pp_off;

	muram = fman_get_muram(pcd->fman);
	if (!muram)
		return -EIO;

	pp = fman_muram_offset_to_vbase(muram, pp_off);
	if (!pp)
		return -EIO;

	/* Read current state */
	v54 = ioread32be((void __iomem *)((u8 __iomem *)pp + 0x54));
	v58 = ioread32be((void __iomem *)((u8 __iomem *)pp + 0x58));

	tnums = fman_port_get_total_tnums(port);
	if (!v54 || !tnums) {
		/* Pool was never armed — nothing to rebuild. */
		return 0;
	}

	idx_off = v54;
	pool_sz = (unsigned long)tnums * 0x100 * 2; /* tnums * BMI_FIFO_UNITS * 2 */
	idx_sz = 5UL + tnums;

	/* Pool is typically allocated immediately before the index.
	 * Compute pool_off = idx_off - pool_sz, 256-aligned.
	 */
	pool_off = (idx_off - pool_sz) & ~0xFFUL;

	/* Zero the pool memory (clear all workspace slots) */
	pool = fman_muram_offset_to_vbase(muram, pool_off);
	if (!pool)
		return -EIO;
	memset_io(pool, 0, pool_sz);

	/* Zero the management index (mark all slots free) */
	idx = fman_muram_offset_to_vbase(muram, idx_off);
	if (!idx)
		return -EIO;
	memset_io(idx, 0, idx_sz);

	/* Zero the depletion counter */
	iowrite32be(0, (void __iomem *)((u8 __iomem *)pp + 0x58));

	/* Readback verify */
	v58 = ioread32be((void __iomem *)((u8 __iomem *)pp + 0x58));
	if (v58 != 0)
		ret = -EAGAIN;

	return ret;
}
EXPORT_SYMBOL_GPL(fman_pcd_port_recover);
