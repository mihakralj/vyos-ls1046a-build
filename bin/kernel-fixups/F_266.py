"""F-266 (control punt, 2026-10-15; TTL added 2026-10-20): soft-parser punt.

Two classes of frame must reach the kernel instead of the FE on an offloaded
port, and the vendor sends both to the host queue from cdx_sp.xml:

* TCP SYN/FIN/RST (tcp.flags & 7). With offload_delay_ms=0 the FE swallows
  the FIN/RST and conntrack keeps the entry ESTABLISHED for ~7300 s
  (plans/ASK2-VENDOR-PARITY.md, "Short TCP flows linger").
* IPv4 TTL <= 1 and IPv6 hop limit <= 1 (A14). UPDATE_TTL drops these with no
  ICMP; UPDATE_HOPLIMIT forwards hlim 1 as 0 and hlim 0 as 255.

This fixup adds:

* fman_port.c/.h: fman_port_sp_attach(), a read-modify-write of one RX port's
  pmda[slot].ssa that sets SW_PRS_EN (0x400) and the halfword label (0x3ff)
  and keeps the other bits (init_hwp()'s short-pad removal 0x80000000).
  Same PCAC stop/readback/start bracket as fman_port_sp_arm(); detach reuses
  fman_port_sp_disarm(), whose log line becomes slot-aware.
* fman_pcd.c: fman_pcd_ctl_punt_set(fm, hw_port_id, mask). The first armed
  port loads the vendor fmc --sp_only -t 0x20 image of
  specs/reference/ask2-sp/ctl_punt.xml (30 words at parser byte 0x40,
  absolute jumps, no loop) under the RM's rule for parser internal memory:
  FMPR_RPIMAC[PEN] cleared, writes only while PSTAT is idle, readback, PEN
  restored. IRQs are off for the few microseconds the parser is stopped.
  The mask then attaches TCP HXS slot 10 (label 0x20) and/or IPv4/IPv6 HXS
  slots 5/6 (labels 0x33/0x47). The last disarm restores the old words.
  A match sets NIA and HPNIA = 0x500002 (BMI enqueue to the default FQ) and
  ends the parse; the parser hands off by NIA, which the first (HPNIA-only)
  version missed. Other frames return to the hard parser unchanged.
* include/linux/fsl/fman_pcd.h: FMAN_PCD_PUNT_* and the export for ask.ko
  (tcp_ctl_punt, ttl_punt).

Proven on silicon 2026-10-20 (DUT2 .156, cold boot, these words loaded
live): FIN/RST 50/50 clean, TTL/hop 1 and 0 get ICMP time exceeded, bulk
traffic stays in the FE. Nothing runs unless ask.ko arms a port. After F-265.
"""

import sys

MARK = "F-266"
PORT_C = "drivers/net/ethernet/freescale/fman/fman_port.c"
PORT_H = "drivers/net/ethernet/freescale/fman/fman_port.h"
PCD_C = "drivers/net/ethernet/freescale/fman/fman_pcd.c"
PCD_H = "include/linux/fsl/fman_pcd.h"

DISARM_LOG_OLD = r"""	dev_info(port->dev, "fman_port: IPv6 HXS soft-sequence disarmed (0x%08x)\n",
		 saved_ssa);
"""
DISARM_LOG_NEW = r"""	dev_info(port->dev, "fman_port: HXS slot %u soft-sequence disarmed (0x%08x)\n",
		 hxs_slot, saved_ssa);	/* F-266 */
"""

PORT_C_ANCHOR = "EXPORT_SYMBOL_GPL(fman_port_sp_disarm);\n"
PORT_C_ADD = r"""
/*
 * F-266 (control punt): attach a resident soft-parser routine to one RX
 * port's HXS stage. Unlike fman_port_sp_arm(), which writes an absolute
 * value, this changes only SW_PRS_EN and the halfword label and keeps the
 * slot's other bits (init_hwp() sets TCP/UDP short-pad removal). Undo with
 * fman_port_sp_disarm(port, hxs_slot, *saved_ssa).
 *
 * Returns 0, -EINVAL on a bad port/slot/label, or -EIO on readback mismatch
 * (the previous value is restored).
 */
#define FMAN_PORT_SSA_SW_PRS_EN	0x00000400
#define FMAN_PORT_SSA_SW_LABEL	0x000003ff

int fman_port_sp_attach(struct fman_port *port, u32 hxs_slot, u32 label,
			u32 *saved_ssa)
{
	struct fman_port_hwp_regs __iomem *regs;
	u32 old, val;

	if (!port || port->port_type != FMAN_PORT_TYPE_RX || !port->hwp_regs ||
	    !saved_ssa || hxs_slot >= HWP_HXS_COUNT ||
	    (label & ~FMAN_PORT_SSA_SW_LABEL))
		return -EINVAL;
	regs = port->hwp_regs;

	stop_port_hwp(port);
	old = ioread32be(&regs->pmda[hxs_slot].ssa);
	val = (old & ~(FMAN_PORT_SSA_SW_PRS_EN | FMAN_PORT_SSA_SW_LABEL)) |
	      FMAN_PORT_SSA_SW_PRS_EN | label;
	iowrite32be(val, &regs->pmda[hxs_slot].ssa);
	if (ioread32be(&regs->pmda[hxs_slot].ssa) != val) {
		iowrite32be(old, &regs->pmda[hxs_slot].ssa);
		start_port_hwp(port);
		dev_err(port->dev,
			"fman_port: F-266 HXS slot %u attach readback mismatch\n",
			hxs_slot);
		return -EIO;
	}
	start_port_hwp(port);
	*saved_ssa = old;
	dev_info(port->dev,
		 "fman_port: F-266 HXS slot %u soft parser attached at label 0x%03x (ssa 0x%08x -> 0x%08x)\n",
		 hxs_slot, label, old, val);
	return 0;
}
EXPORT_SYMBOL_GPL(fman_port_sp_attach);
"""

PORT_H_ANCHOR = "int fman_port_sp_disarm(struct fman_port *port, u32 hxs_slot, u32 saved_ssa);\n"
PORT_H_ADD = r"""/*
 * F-266: read-modify-write attach of a resident soft-parser routine at
 * @label (halfwords) to @hxs_slot; detach with fman_port_sp_disarm().
 */
int fman_port_sp_attach(struct fman_port *port, u32 hxs_slot, u32 label,
			u32 *saved_ssa);
"""

PCD_C_ANCHOR = "EXPORT_SYMBOL_GPL(fman_pcd_offload_disengage);\n"
PCD_C_ADD = r"""
/*
 * F-266 control punt (ask.ko tcp_ctl_punt / ttl_punt): a soft-parser program
 * that sends selected frames on an armed RX port to the port's default FQ
 * (the kernel) instead of KeyGen and the FE, as the vendor's cdx_sp.xml does:
 *
 *  FMAN_PCD_PUNT_TCP_CTL  TCP with SYN, FIN or RST (tcp.flags & 7), so
 *                         conntrack sees offloaded flows open and close.
 *  FMAN_PCD_PUNT_TTL      IPv4 TTL <= 1 and IPv6 hop limit <= 1 (A14). The
 *                         FE's UPDATE_TTL drops these silently and
 *                         UPDATE_HOPLIMIT wraps 1 -> 0 and 0 -> 255; the
 *                         kernel sends ICMP time exceeded instead.
 *
 * A matching routine writes NIA and HPNIA = 0x500002 (BMI enqueue to the
 * default FQ) and ends the parse. The parser hands the frame on by NIA (RM
 * 5.9.4.4.4, result array bytes 93-95); the 2026-10-15 program wrote only
 * HPNIA and never punted. Other frames return to the hard parser unchanged.
 *
 * The code is the vendor fmc --sp_only output for
 * specs/reference/ask2-sp/ctl_punt.xml with -t 0x20 (labels in halfwords:
 * TCP 0x20, IPv4 0x33, IPv6 0x47). It holds absolute jumps, so it must sit
 * at parser byte 0x40. The first armed port loads it and the last disarm
 * restores the previous words. Proven on silicon 2026-10-20 (.156, cold
 * boot, live load of these words): FIN/RST close offloaded IPv4/IPv6 flows,
 * TTL/hop limit 1 and 0 get ICMP time exceeded, bulk data stays in the FE.
 */
#define FMAN_PCD_PRS_RPIMAC		0x844		/* FMPR_RPIMAC, parser block */
#define FMAN_PCD_PRS_RPIMAC_PEN		0x00000001
#define FMAN_PCD_PRS_RPIMAC_PSTAT	0x00000100
#define FMAN_PCD_PRS_IDLE_POLLS		1000		/* x 1 us */
#define FMAN_PCD_PUNT_BASE_LABEL	0x020
#define FMAN_PCD_PUNT_OFF		(FMAN_PCD_PUNT_BASE_LABEL * 2)
#define FMAN_PCD_PUNT_PORTS		16
#define FMAN_PCD_PUNT_NHXS		3

static const u32 fman_pcd_punt_code[] = {
	0x0000b78a, 0x00da0007, 0x00000000, 0x00004031,
	0x0000095f, 0x00020050, 0x094f0002, 0x00500001,
	0x1bff0001, 0x1bfe0000, 0xa38e403c, 0x00010000,
	0xa38e403c, 0x00001843, 0x095f0002, 0x0050094f,
	0x00020050, 0x1bff0001, 0x1bfe0001, 0x1bfe0000,
	0x9f8e4050, 0x00010000, 0x9f8e4050, 0x00001857,
	0x095f0002, 0x0050094f, 0x00020050, 0x1bff0001,
	0x1bfe0001, 0x1bfe0000,
};

/* HXS attachments; attached bit i of a port = entry i. */
static const struct {
	u32 feature;
	u8 hxs;
	u16 label;
} fman_pcd_punt_hxs[FMAN_PCD_PUNT_NHXS] = {
	{ FMAN_PCD_PUNT_TCP_CTL, 10, 0x020 },	/* TCP HXS, pmda+0x50 */
	{ FMAN_PCD_PUNT_TTL, 5, 0x033 },	/* IPv4 HXS, pmda+0x28 */
	{ FMAN_PCD_PUNT_TTL, 6, 0x047 },	/* IPv6 HXS, pmda+0x30 */
};

struct fman_pcd_punt_port {
	struct fman_port *port;		/* set while any HXS is attached */
	u8 hw_port_id;
	u8 attached;
	u32 saved_ssa[FMAN_PCD_PUNT_NHXS];
};

static DEFINE_MUTEX(fman_pcd_punt_lock);
/* Owner while the code is loaded or its restore is pending. */
static struct fman *fman_pcd_punt_fm;
static unsigned int fman_pcd_punt_users;
/* _prev holds the words the code replaced; still to be put back. */
static bool fman_pcd_punt_loaded;
static u32 fman_pcd_punt_prev[ARRAY_SIZE(fman_pcd_punt_code)];
static struct fman_pcd_punt_port fman_pcd_punt_ports[FMAN_PCD_PUNT_PORTS];

/*
 * Write @n words at parser byte @off. RM (FMPR_RPIMAC): parser internal
 * memory may be updated only with PEN cleared and written only while PSTAT
 * is idle. Clearing PEN pauses parse dispatch on every port, so IRQs stay
 * off and the pause lasts only the few microseconds of the write. PEN is put
 * back to its previous value. On readback mismatch the words saved in @prev
 * (if given) are restored inside the same window.
 */
static int fman_pcd_prs_write(struct fman *fm, unsigned int off,
			      const u32 *code, u32 *prev, unsigned int n)
{
	void __iomem *prs = (void __iomem *)fm->hwp_regs;
	void __iomem *rpimac = prs + FMAN_PCD_PRS_RPIMAC;
	unsigned long flags;
	unsigned int i, polls;
	u32 saved, stopped, now;
	int ret = 0;

	local_irq_save(flags);
	saved = ioread32be(rpimac) & ~FMAN_PCD_PRS_RPIMAC_PSTAT;
	iowrite32be(saved & ~FMAN_PCD_PRS_RPIMAC_PEN, rpimac);
	for (polls = 0; polls < FMAN_PCD_PRS_IDLE_POLLS; polls++) {
		stopped = ioread32be(rpimac);
		if (!(stopped & FMAN_PCD_PRS_RPIMAC_PSTAT))
			break;
		udelay(1);
	}
	if (polls == FMAN_PCD_PRS_IDLE_POLLS) {
		ret = -ETIMEDOUT;
		goto restore;
	}
	if (prev)
		for (i = 0; i < n; i++)
			prev[i] = ioread32be(prs + off + i * 4);
	for (i = 0; i < n; i++)
		iowrite32be(code[i], prs + off + i * 4);
	for (i = 0; i < n; i++)
		if (ioread32be(prs + off + i * 4) != code[i])
			ret = -EIO;
	if (ret && prev)
		for (i = 0; i < n; i++)
			iowrite32be(prev[i], prs + off + i * 4);
restore:
	iowrite32be(saved, rpimac);
	now = ioread32be(rpimac);
	local_irq_restore(flags);

	if (ret == -ETIMEDOUT)
		dev_err(fm->dev, "fman_pcd: F-266 parser did not go idle (FMPR_RPIMAC 0x%08x)\n",
			stopped);
	else if (ret)
		dev_err(fm->dev, "fman_pcd: F-266 parser code readback mismatch at 0x%03x\n",
			off);
	if ((now ^ saved) & FMAN_PCD_PRS_RPIMAC_PEN) {
		dev_err(fm->dev, "fman_pcd: F-266 FMPR_RPIMAC not restored (0x%08x, saved 0x%08x)\n",
			now, saved);
		if (!ret)
			ret = -EIO;
	}
	if (!(saved & FMAN_PCD_PRS_RPIMAC_PEN))
		dev_warn(fm->dev, "fman_pcd: F-266 parser was disabled before the load (FMPR_RPIMAC 0x%08x)\n",
			 saved);
	else if (!ret)
		dev_info(fm->dev, "fman_pcd: F-266 %u parser code word(s) written at 0x%03x (PEN 1->0->1, %u idle poll(s))\n",
			 n, off, polls);
	return ret;
}

static bool fman_pcd_punt_resident(struct fman *fm)
{
	void __iomem *prs = (void __iomem *)fm->hwp_regs;
	unsigned int i;

	for (i = 0; i < ARRAY_SIZE(fman_pcd_punt_code); i++)
		if (ioread32be(prs + FMAN_PCD_PUNT_OFF + i * 4) !=
		    fman_pcd_punt_code[i])
			return false;
	return true;
}

/*
 * Put back the words the first arm replaced; lock held, no port attached.
 * On failure the code stays (inert, nothing jumps to it) together with the
 * saved words, and the next first arm and last disarm reuse them.
 */
static void fman_pcd_punt_unload(struct fman *fm)
{
	if (fman_pcd_prs_write(fm, FMAN_PCD_PUNT_OFF, fman_pcd_punt_prev, NULL,
			       ARRAY_SIZE(fman_pcd_punt_prev))) {
		dev_err(fm->dev, "fman_pcd: F-266 parser words at 0x%03x not restored; unused, retried after the next arm\n",
			FMAN_PCD_PUNT_OFF);
		return;
	}
	fman_pcd_punt_loaded = false;
	fman_pcd_punt_fm = NULL;
}

/**
 * fman_pcd_ctl_punt_set() - choose which frames one RX port punts
 * @fm: FMan instance
 * @hw_port_id: BMI hardware port id of an RX port
 * @mask: FMAN_PCD_PUNT_* bits; 0 disarms the port
 *
 * Attaches and detaches the port's TCP/IPv4/IPv6 HXS so that they match
 * @mask. The first armed port loads the program; the last disarm restores
 * the parser words it replaced. Each HXS is tracked on its own, so after an
 * error the port keeps the HXS that did change and a later call with the
 * same or another mask converges. Disarming an unarmed port is a no-op.
 *
 * Return: 0, -EINVAL, -ENODEV (no such RX port), -EBUSY (loaded on another
 * FMan), -ENOSPC, -ETIMEDOUT (parser never idle), or -EIO (readback
 * mismatch or the resident code was overwritten).
 */
int fman_pcd_ctl_punt_set(struct fman *fm, u8 hw_port_id, u32 mask)
{
	struct fman_pcd_punt_port *slot = NULL, *spare = NULL;
	const unsigned int n = ARRAY_SIZE(fman_pcd_punt_code);
	struct fman_port *port;
	bool add = false;
	unsigned int i;
	u8 was;
	int ret = 0;

	if (!fm || !fm->hwp_regs || (mask & ~FMAN_PCD_PUNT_ALL))
		return -EINVAL;

	mutex_lock(&fman_pcd_punt_lock);
	if (fman_pcd_punt_fm && fman_pcd_punt_fm != fm) {
		ret = mask ? -EBUSY : 0;
		goto out;
	}
	for (i = 0; i < ARRAY_SIZE(fman_pcd_punt_ports); i++) {
		struct fman_pcd_punt_port *pp = &fman_pcd_punt_ports[i];

		if (pp->port && pp->hw_port_id == hw_port_id) {
			slot = pp;
			break;
		}
		if (!spare && !pp->port)
			spare = pp;
	}
	if (slot) {
		port = slot->port;
	} else {
		if (!mask)
			goto out;
		port = fman_port_lookup_rx(fm, hw_port_id);
		if (!port) {
			ret = -ENODEV;
			goto out;
		}
		if (!spare) {
			ret = -ENOSPC;
			goto out;
		}
		slot = spare;
		slot->attached = 0;
	}
	was = slot->attached;
	for (i = 0; i < FMAN_PCD_PUNT_NHXS; i++)
		if ((mask & fman_pcd_punt_hxs[i].feature) && !(was & BIT(i)))
			add = true;

	if (!fman_pcd_punt_users) {
		/* After a failed restore, _prev already has the original words. */
		ret = fman_pcd_prs_write(fm, FMAN_PCD_PUNT_OFF, fman_pcd_punt_code,
					 fman_pcd_punt_loaded ? NULL :
					 fman_pcd_punt_prev, n);
		if (ret)
			goto out;
		fman_pcd_punt_loaded = true;
		fman_pcd_punt_fm = fm;
	} else if (add && !fman_pcd_punt_resident(fm)) {
		dev_err(fm->dev, "fman_pcd: F-266 punt code at parser 0x%03x was overwritten\n",
			FMAN_PCD_PUNT_OFF);
		ret = -EIO;
		goto out;
	}

	for (i = 0; i < FMAN_PCD_PUNT_NHXS; i++) {
		bool want = mask & fman_pcd_punt_hxs[i].feature;
		bool have = slot->attached & BIT(i);
		int rc;

		if (want == have)
			continue;
		if (want)
			rc = fman_port_sp_attach(port, fman_pcd_punt_hxs[i].hxs,
						 fman_pcd_punt_hxs[i].label,
						 &slot->saved_ssa[i]);
		else
			rc = fman_port_sp_disarm(port, fman_pcd_punt_hxs[i].hxs,
						 slot->saved_ssa[i]);
		if (rc) {
			if (!ret)
				ret = rc;
			continue;
		}
		slot->attached ^= BIT(i);
	}

	if (!was && slot->attached) {
		slot->port = port;
		slot->hw_port_id = hw_port_id;
		fman_pcd_punt_users++;
	} else if (was && !slot->attached) {
		slot->port = NULL;
		fman_pcd_punt_users--;
	}
	if (!fman_pcd_punt_users)
		fman_pcd_punt_unload(fm);
	dev_info(fm->dev, "fman_pcd: F-266 control punt on port 0x%02x: HXS mask 0x%x -> 0x%x (%u port(s))\n",
		 hw_port_id, was, slot->attached, fman_pcd_punt_users);
out:
	mutex_unlock(&fman_pcd_punt_lock);
	return ret;
}
EXPORT_SYMBOL_GPL(fman_pcd_ctl_punt_set);
"""

PCD_H_ANCHOR = "unsigned long fman_pcd_fe_enq_get_offset(struct fman *fm);\n"
PCD_H_ADD = r"""
/*
 * F-266: control punt (ask.ko tcp_ctl_punt, ttl_punt). A soft-parser program
 * sends the selected frames of an armed RX port to its default FQ instead of
 * KeyGen/FE. fman_pcd_ctl_punt_set() makes the port match @mask; 0 disarms
 * it, and the last disarm restores the parser code words.
 */
#define FMAN_PCD_PUNT_TCP_CTL	0x1	/* TCP SYN/FIN/RST */
#define FMAN_PCD_PUNT_TTL	0x2	/* IPv4 TTL / IPv6 hop limit <= 1 */
#define FMAN_PCD_PUNT_ALL	(FMAN_PCD_PUNT_TCP_CTL | FMAN_PCD_PUNT_TTL)
int fman_pcd_ctl_punt_set(struct fman *fm, u8 hw_port_id, u32 mask);
"""

# (path, old, new, expected matches). Anchors are left in place and the new
# text is appended after them, so a re-run sees MARK and stops.
EDITS = [
    (PORT_C, DISARM_LOG_OLD, DISARM_LOG_NEW, 1),
    (PORT_C, PORT_C_ANCHOR, PORT_C_ANCHOR + PORT_C_ADD, 1),
    (PORT_H, PORT_H_ANCHOR, PORT_H_ANCHOR + PORT_H_ADD, 1),
    (PCD_C, PCD_C_ANCHOR, PCD_C_ANCHOR + PCD_C_ADD, 1),
    (PCD_H, PCD_H_ANCHOR, PCD_H_ANCHOR + PCD_H_ADD, 1),
]


def main():
    srcs = {}
    for path in dict.fromkeys(p for p, _o, _n, _w in EDITS):
        try:
            with open(path) as f:
                srcs[path] = f.read()
        except FileNotFoundError:
            print(f"### {MARK}: FATAL: {path} not found")
            sys.exit(1)
    marked = [p for p, s in srcs.items() if MARK in s]
    if len(marked) == len(srcs):
        print(f"### {MARK}: already applied")
        return
    if marked:
        print(f"### {MARK}: FATAL: partially applied ({', '.join(marked)})")
        sys.exit(1)
    for i, (path, old, _new, want) in enumerate(EDITS):
        n = srcs[path].count(old)
        if n != want:
            print(f"### {MARK}: FATAL: anchor {i} in {path} found {n} times (expected {want})")
            sys.exit(1)
    for path, old, new, _want in EDITS:
        srcs[path] = srcs[path].replace(old, new, 1)
    for path, src in srcs.items():
        with open(path, "w") as f:
            f.write(src)
    print(f"### {MARK}: control punt (fman_port_sp_attach, fman_pcd_ctl_punt_set) added")


if __name__ == "__main__":
    main()
