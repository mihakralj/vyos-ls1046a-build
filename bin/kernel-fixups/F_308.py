"""F-308 (2026-11-08): runtime RX external buffer start margin for ASK2 adaptive alignment.

FMan falls behind -- mEMAC RX FIFO overflow (rdrp) on every port at once -- when
offloaded frames leave at a start address that is not 8-byte aligned. A +-4 B
VLAN POP/PUSH on data placed at 256 does exactly that: vlan-port bidir ran
6.20 + 6.89 Gbit/s with ~355k rdrp per port while plain and vlan-vlan bidir
(length-neutral) ran ~17 Gbit/s with <1k. Board A/B on .185, image
2026.11.07-2039: with FMBM_REBM start margin 260 on both 10G ports, POP lands
at 264 and PUSH at 256 and vlan-port rdrp went to 0/0, while plain and
vlan-vlan (now at 260) collapsed instead. PPPoE (+-8 B) runs at full rate at
256, so 8-byte alignment is the requirement and two margins (256, 260) cover
every edit class. ask.ko picks one per RX port from its offloaded byte mix.

fman_port_set_rx_start_margin() validates against live registers (port->cfg
is freed after init): margin multiple of 4, at least the internal-context
end (FMBM_RICP ICEOF + ICSZ, 256 on this board), and FMan's own init rule
start + 64 + end margin <= the smallest valid pool buffer (FMBM_EBMPI). It
quiesces the BMI around the write; on a drain timeout (-EBUSY, F-274) the
old margin stays and the port is re-enabled. A disabled port (interface
down) is written and left disabled. The FD carries each frame's offset, so
frames already queued with the old margin stay valid. Caller holds RTNL,
which serializes with dpaa open/stop and the AF_XDP bpool reprogram.

Needs F-274 (fman_port_disable() returns -EBUSY). Idempotent; exits non-zero
unless each anchor matches exactly once.
"""

import sys

MARK = "F-308"
C_PATH = "drivers/net/ethernet/freescale/fman/fman_port.c"
H_PATH = "drivers/net/ethernet/freescale/fman/fman_port.h"

C_ANCHOR = "EXPORT_SYMBOL(fman_port_enable);\n"
C_ADD = r'''
/*
 * F-308: change the RX external buffer start margin (FMBM_REBM[BSM]) at
 * runtime. FMan falls behind when offloaded frames leave at a start that is
 * not 8-byte aligned, so ask.ko moves a port whose offloaded bytes are mostly
 * +-4 B VLAN edits from 256 to 260 and back. The margin must be a multiple of
 * 4, keep the internal context (FMBM_RICP) clear and pass FMan's init rule
 * start + 64 + end <= smallest pool buffer. The BMI is quiesced around the
 * write; on a drain timeout the old margin stays. A disabled port is left
 * disabled. Caller holds RTNL.
 *
 * Return: 0, -EINVAL (bad port/margin), -ERANGE (margin does not fit),
 * -EBUSY (drain timeout, old margin kept) or -EIO (readback mismatch).
 */
int fman_port_set_rx_start_margin(struct fman_port *port, u16 margin)
{
	u32 ricp, rebm, want, got, min_buf = U32_MAX;
	unsigned int ic_end, i;
	bool was_enabled;
	int err;

	if (!port || port->port_type != FMAN_PORT_TYPE_RX ||
	    !is_init_done(port->cfg) || (margin & 3) ||
	    margin > BMI_EXT_BUF_MARG_START_MASK)
		return -EINVAL;

	ricp = ioread32be(&port->bmi_regs->rx.fmbm_ricp);
	ic_end = (((ricp >> BMI_IC_TO_EXT_SHIFT) & BMI_IC_TO_EXT_MASK) +
		  (ricp & BMI_IC_SIZE_MASK)) * 16;
	for (i = 0; i < port->ext_pools_num && i < FMAN_PORT_MAX_EXT_POOLS_NUM; i++) {
		u32 bp = ioread32be(&port->bmi_regs->rx.fmbm_ebmpi[i]);

		if (bp & BMI_EXT_BUF_POOL_VALID)
			min_buf = min_t(u32, min_buf, bp & 0xffff);
	}
	rebm = ioread32be(&port->bmi_regs->rx.fmbm_rebm);
	if (margin < ic_end || min_buf == U32_MAX ||
	    margin + MIN_EXT_BUF_SIZE + (rebm & BMI_EXT_BUF_MARG_END_MASK) > min_buf)
		return -ERANGE;

	want = rebm & ~(BMI_EXT_BUF_MARG_START_MASK << BMI_EXT_BUF_MARG_START_SHIFT);
	want |= (u32)margin << BMI_EXT_BUF_MARG_START_SHIFT;
	if (want == rebm)
		return 0;

	was_enabled = ioread32be(&port->bmi_regs->rx.fmbm_rcfg) & BMI_PORT_CFG_EN;
	if (was_enabled) {
		err = fman_port_disable(port);
		if (err) {
			fman_port_enable(port);
			return err;
		}
	}
	iowrite32be(want, &port->bmi_regs->rx.fmbm_rebm);
	got = ioread32be(&port->bmi_regs->rx.fmbm_rebm);
	if (was_enabled)
		fman_port_enable(port);
	return got == want ? 0 : -EIO;
}
EXPORT_SYMBOL_GPL(fman_port_set_rx_start_margin);
'''

H_ANCHOR = "int fman_port_enable(struct fman_port *port);\n"
H_ADD = ("\n/* F-308: runtime RX start margin (ASK2 adaptive alignment); caller holds RTNL. */\n"
         "int fman_port_set_rx_start_margin(struct fman_port *port, u16 margin);\n")


def patch(path, anchor, add):
    try:
        with open(path) as f:
            s = f.read()
    except FileNotFoundError:
        print(f"### F-308: FATAL: {path} not found")
        sys.exit(1)
    if MARK in s:
        return False
    n = s.count(anchor)
    if n != 1:
        print(f"### F-308: FATAL: anchor in {path} found {n} times (expected 1)")
        sys.exit(1)
    with open(path, "w") as f:
        f.write(s.replace(anchor, anchor + add))
    return True


def main():
    with open(C_PATH) as f:
        c = f.read()
    if MARK not in c and "F-274: report the drain timeout" not in c:
        print("### F-308: FATAL: F-274 (fman_port_disable -EBUSY) must be applied first")
        sys.exit(1)
    for need in ("#define MIN_EXT_BUF_SIZE", "static bool is_init_done("):
        if need not in c:
            print(f"### F-308: FATAL: {need!r} missing from {C_PATH}")
            sys.exit(1)
    changed = patch(C_PATH, C_ANCHOR, C_ADD)
    changed |= patch(H_PATH, H_ANCHOR, H_ADD)
    print("### F-308: fman_port_set_rx_start_margin() added" if changed else "### F-308: already applied")


if __name__ == "__main__":
    main()
