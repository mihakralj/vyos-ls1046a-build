"""F-266 (TCP control punt, 2026-10-15): send TCP SYN/FIN/RST to the kernel.

With offload_delay_ms=0 every TCP flow is offloaded from its first packet and
the FE swallows the FIN/RST, so conntrack keeps the entry ESTABLISHED for
~7300 s (plans/ASK2-VENDOR-PARITY.md, "Short TCP flows linger"). The vendor
avoids this with a TCP soft-parser routine (cdx_sp.xml) that sends
tcp.flags & 7 to the host queue. This fixup adds the same punt, default off:

* fman_port.c/.h: fman_port_sp_attach(), a read-modify-write of one RX port's
  pmda[slot].ssa that sets SW_PRS_EN (0x400) and the halfword label (0x3ff)
  and keeps the other bits (init_hwp()'s TCP short-pad removal 0x80000000).
  Same PCAC stop/readback/start bracket as fman_port_sp_arm(); detach reuses
  fman_port_sp_disarm(), whose log line becomes slot-aware.
* fman_pcd.c: fman_pcd_tcp_ctl_punt_arm()/_disarm(). The first armed port
  loads the vendor fmc --sp_only image of specs/reference/ask2-sp/
  tcp_ctl_punt.xml (8 words, label 0x20 = parser byte 0x40, absolute jump,
  no loop) with the RM's rule for parser internal memory: FMPR_RPIMAC[PEN]
  cleared, writes only while PSTAT is idle, readback, then PEN restored
  (mainline hwp_init() sets PEN=1 at boot). IRQs are off for the few
  microseconds the parser is stopped. Each armed port then attaches TCP HXS
  slot 10 to label 0x20. The last disarm restores the previous code words.
  The routine sets HPNIA=0x500002 (BMI enqueue to the port's default FQ) and
  ends the parse for SYN/FIN/RST; other TCP frames return to the hard parser.
* include/linux/fsl/fman_pcd.h: the two exports for ask.ko (tcp_ctl_punt=1).

Nothing runs unless ask.ko arms a port. After F-265.
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
 * F-266 (TCP control punt): attach a resident soft-parser routine to one RX
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
 * F-266 (TCP control punt, default off): TCP SYN/FIN/RST on an armed port go
 * to the kernel instead of the FE, so conntrack sees offloaded flows open and
 * close (the vendor's cdx_sp.xml does the same). A soft-parser routine on the
 * TCP HXS tests tcp.flags & 7; when set it writes HPNIA = 0x500002 (BMI
 * enqueue to the port's default FQ) and ends the parse, skipping KeyGen and
 * the FE. Other TCP frames return to the hard parser unchanged.
 *
 * The code is the vendor fmc --sp_only output for
 * specs/reference/ask2-sp/tcp_ctl_punt.xml at label 0x20 (halfwords). It
 * holds an absolute jump, so it must sit at parser byte 0x40. The first
 * armed port loads it, and the last disarm restores the previous words.
 */
#define FMAN_PCD_PRS_RPIMAC		0x844		/* FMPR_RPIMAC, parser block */
#define FMAN_PCD_PRS_RPIMAC_PEN		0x00000001
#define FMAN_PCD_PRS_RPIMAC_PSTAT	0x00000100
#define FMAN_PCD_PRS_IDLE_POLLS		1000		/* x 1 us */
#define FMAN_PCD_TCP_PUNT_LABEL		0x020
#define FMAN_PCD_TCP_PUNT_OFF		(FMAN_PCD_TCP_PUNT_LABEL * 2)
#define FMAN_PCD_TCP_PUNT_HXS		10		/* TCP HXS, pmda+0x50 */
#define FMAN_PCD_TCP_PUNT_PORTS		16

static const u32 fman_pcd_tcp_punt_code[] = {
	0x0000b78a, 0x00da0007, 0x00000000, 0x0000402e,
	0x0000094f, 0x00020050, 0x00011bff, 0x00011bfe,
};

struct fman_pcd_tcp_punt_port {
	struct fman_port *port;
	u8 hw_port_id;
	u32 saved_ssa;
};

static DEFINE_MUTEX(fman_pcd_tcp_punt_lock);
/* Owner while the code is loaded or its restore is pending. */
static struct fman *fman_pcd_tcp_punt_fm;
static unsigned int fman_pcd_tcp_punt_users;
/* _prev holds the words the code replaced; still to be put back. */
static bool fman_pcd_tcp_punt_loaded;
static u32 fman_pcd_tcp_punt_prev[ARRAY_SIZE(fman_pcd_tcp_punt_code)];
static struct fman_pcd_tcp_punt_port
	fman_pcd_tcp_punt_ports[FMAN_PCD_TCP_PUNT_PORTS];

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

static bool fman_pcd_tcp_punt_resident(struct fman *fm)
{
	void __iomem *prs = (void __iomem *)fm->hwp_regs;
	unsigned int i;

	for (i = 0; i < ARRAY_SIZE(fman_pcd_tcp_punt_code); i++)
		if (ioread32be(prs + FMAN_PCD_TCP_PUNT_OFF + i * 4) !=
		    fman_pcd_tcp_punt_code[i])
			return false;
	return true;
}

/*
 * Put back the words the first arm replaced; lock held, no port attached.
 * On failure the code stays (inert, nothing jumps to it) together with the
 * saved words, and the next first arm and last disarm reuse them.
 */
static void fman_pcd_tcp_punt_unload(struct fman *fm)
{
	if (fman_pcd_prs_write(fm, FMAN_PCD_TCP_PUNT_OFF,
			       fman_pcd_tcp_punt_prev, NULL,
			       ARRAY_SIZE(fman_pcd_tcp_punt_prev))) {
		dev_err(fm->dev, "fman_pcd: F-266 parser words at 0x%03x not restored; unused, retried after the next arm\n",
			FMAN_PCD_TCP_PUNT_OFF);
		return;
	}
	fman_pcd_tcp_punt_loaded = false;
	fman_pcd_tcp_punt_fm = NULL;
}

/**
 * fman_pcd_tcp_ctl_punt_arm() - punt TCP SYN/FIN/RST on one RX port
 * @fm: FMan instance
 * @hw_port_id: BMI hardware port id of an RX port
 *
 * Loads the punt routine on the first armed port and attaches the port's TCP
 * HXS to it. Arming an armed port is a no-op.
 *
 * Return: 0, -EINVAL, -ENODEV (no such RX port), -EBUSY (loaded on another
 * FMan), -ENOSPC, -ETIMEDOUT (parser never idle), or -EIO (readback
 * mismatch or the resident code was overwritten).
 */
int fman_pcd_tcp_ctl_punt_arm(struct fman *fm, u8 hw_port_id)
{
	struct fman_pcd_tcp_punt_port *slot = NULL;
	const unsigned int n = ARRAY_SIZE(fman_pcd_tcp_punt_code);
	struct fman_port *port;
	unsigned int i;
	int ret;

	if (!fm || !fm->hwp_regs)
		return -EINVAL;
	port = fman_port_lookup_rx(fm, hw_port_id);
	if (!port)
		return -ENODEV;

	mutex_lock(&fman_pcd_tcp_punt_lock);
	if (fman_pcd_tcp_punt_fm && fman_pcd_tcp_punt_fm != fm) {
		ret = -EBUSY;
		goto out;
	}
	for (i = 0; i < ARRAY_SIZE(fman_pcd_tcp_punt_ports); i++) {
		if (fman_pcd_tcp_punt_ports[i].port == port) {
			ret = 0;
			goto out;
		}
		if (!slot && !fman_pcd_tcp_punt_ports[i].port)
			slot = &fman_pcd_tcp_punt_ports[i];
	}
	if (!slot) {
		ret = -ENOSPC;
		goto out;
	}

	if (!fman_pcd_tcp_punt_users) {
		/* After a failed restore, _prev already has the original words. */
		ret = fman_pcd_prs_write(fm, FMAN_PCD_TCP_PUNT_OFF,
					 fman_pcd_tcp_punt_code,
					 fman_pcd_tcp_punt_loaded ? NULL :
					 fman_pcd_tcp_punt_prev, n);
		if (ret)
			goto out;
		fman_pcd_tcp_punt_loaded = true;
		fman_pcd_tcp_punt_fm = fm;
	} else if (!fman_pcd_tcp_punt_resident(fm)) {
		dev_err(fm->dev, "fman_pcd: F-266 TCP punt code at parser 0x%03x was overwritten\n",
			FMAN_PCD_TCP_PUNT_OFF);
		ret = -EIO;
		goto out;
	}

	ret = fman_port_sp_attach(port, FMAN_PCD_TCP_PUNT_HXS,
				  FMAN_PCD_TCP_PUNT_LABEL, &slot->saved_ssa);
	if (ret) {
		if (!fman_pcd_tcp_punt_users)
			fman_pcd_tcp_punt_unload(fm);
		goto out;
	}
	slot->port = port;
	slot->hw_port_id = hw_port_id;
	fman_pcd_tcp_punt_users++;
	dev_info(fm->dev, "fman_pcd: F-266 TCP control punt armed on port 0x%02x (%u port(s))\n",
		 hw_port_id, fman_pcd_tcp_punt_users);
out:
	mutex_unlock(&fman_pcd_tcp_punt_lock);
	return ret;
}
EXPORT_SYMBOL_GPL(fman_pcd_tcp_ctl_punt_arm);

/**
 * fman_pcd_tcp_ctl_punt_disarm() - undo fman_pcd_tcp_ctl_punt_arm()
 * @fm: FMan instance
 * @hw_port_id: BMI hardware port id
 *
 * Restores the port's TCP HXS attachment; the last port also restores the
 * parser code words the first arm replaced. If the attachment cannot be
 * restored the port stays armed and the code stays loaded. Once the port is
 * detached it counts as disarmed even if the code words cannot be put back.
 *
 * Return: 0, -EINVAL, -ENOENT (port not armed), or the error from restoring
 * the attachment.
 */
int fman_pcd_tcp_ctl_punt_disarm(struct fman *fm, u8 hw_port_id)
{
	struct fman_pcd_tcp_punt_port *slot = NULL;
	unsigned int i;
	int ret;

	if (!fm)
		return -EINVAL;

	mutex_lock(&fman_pcd_tcp_punt_lock);
	if (fman_pcd_tcp_punt_users && fman_pcd_tcp_punt_fm == fm) {
		for (i = 0; i < ARRAY_SIZE(fman_pcd_tcp_punt_ports); i++) {
			if (fman_pcd_tcp_punt_ports[i].port &&
			    fman_pcd_tcp_punt_ports[i].hw_port_id == hw_port_id) {
				slot = &fman_pcd_tcp_punt_ports[i];
				break;
			}
		}
	}
	if (!slot) {
		ret = -ENOENT;
		goto out;
	}

	ret = fman_port_sp_disarm(slot->port, FMAN_PCD_TCP_PUNT_HXS,
				  slot->saved_ssa);
	if (ret)
		goto out;
	slot->port = NULL;
	if (!--fman_pcd_tcp_punt_users)
		fman_pcd_tcp_punt_unload(fm);
	dev_info(fm->dev, "fman_pcd: F-266 TCP control punt disarmed on port 0x%02x (%u port(s) left)\n",
		 hw_port_id, fman_pcd_tcp_punt_users);
out:
	mutex_unlock(&fman_pcd_tcp_punt_lock);
	return ret;
}
EXPORT_SYMBOL_GPL(fman_pcd_tcp_ctl_punt_disarm);
"""

PCD_H_ANCHOR = "unsigned long fman_pcd_fe_enq_get_offset(struct fman *fm);\n"
PCD_H_ADD = r"""
/*
 * F-266: TCP control punt (default off; ask.ko tcp_ctl_punt=1). Arm attaches
 * the RX port's TCP HXS to a soft-parser routine (loaded on first use) that
 * sends SYN/FIN/RST to the port's default FQ instead of KeyGen/FE. Disarm
 * restores the attachment and, on the last port, the parser code words.
 */
int fman_pcd_tcp_ctl_punt_arm(struct fman *fm, u8 hw_port_id);
int fman_pcd_tcp_ctl_punt_disarm(struct fman *fm, u8 hw_port_id);
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
    print(f"### {MARK}: TCP control punt (fman_port_sp_attach, fman_pcd_tcp_ctl_punt_arm/_disarm) added")


if __name__ == "__main__":
    main()
