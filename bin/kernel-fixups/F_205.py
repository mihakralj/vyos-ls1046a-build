"""F-205 (T-M6-1 Phase 3, S1): parser LCV-split port primitive (DORMANT).

Adds fman_port_set_lcv_split() / fman_port_clear_lcv_split() to fman_port.c and
declares them in fman_port.h. NO caller yet — build-only, zero runtime effect.

Phase 3 proved on silicon (2026-08-19) that the parser-LCV -> kgse_mv SI-walk
discriminates IPv4 vs IPv6 into distinct KeyGen schemes. The per-frame LCV is
the OR of every parsed header slot's pmda[slot].lcv mask; mainline sets every
pmda[i].lcv = 0xffffffff (init_hwp), so nothing discriminates. To route IPv6 to
its own scheme, a port's parser LCV must be split so ONLY the IPv4 HXS slot (5)
and the IPv6 HXS slot (6) contribute distinct single bits; all other slots must
be zeroed (else they OR every bit into the LCV and defeat the match).

  set_lcv_split(port, v4_bit, v6_bit):
    zero all 16 pmda[].lcv, then slot5=v4_bit, slot6=v6_bit; readback-verify.
  clear_lcv_split(port):
    restore all 16 pmda[].lcv = 0xffffffff (mainline default) — the reversibility
    anchor for disengage; readback-verify.

Register facts (verified live 2026-08-19 via /dev/mem on eth2 HWP 0x1a89800):
struct fman_port_hwp_regs at base_addr + HWP_PORT_REGS_OFFSET(0x800); pmda[i] =
{ssa@+0, lcv@+4}, 8-byte stride; IPv4 = pmda[5], IPv6 = pmda[6]
(vendor GetPrsHdrNum: IPv4->5, IPv6->6). HWP_HXS_COUNT = 16.

2026-08-19 vendor-source correction: the pmda[].lcv words are hard-parser
shadow RAM that only commit while the parser is stopped. Vendor SetPcd()
(fm_port.c:1438/1627) and mainline init_hwp() both bracket every PMDA write with
PCAC PSTOP -> wait !PSTAT -> write -> PSTART. This primitive now does the same
via stop_port_hwp()/start_port_hwp() (static helpers in the same TU). Writing
live (the original F-205) let the register readback pass on the shadow while the
live parse array kept 0xffffffff — the leading root cause of the board result
where the split "succeeded" yet slots 5/6 never affected selection. NOTE: the
parser LCV alone is not sufficient — QLCV = CP_entry_mask & LCV, and the KeyGen
classification-plan entry 0 must be 0xffffffff (see the companion CP-entry-mask
fixup) or QLCV is zeroed regardless of the LCV split.

S0 QDRANT GATE satisfied: LCV mechanism + pmda offsets cross-checked against
arch/fman-microcode-210-programming-reference.md and vendor NCSW fm_port.c; the
parser stop/start requirement is vendor- and mainline-confirmed. Readback per
S6 R10.2.

Must run AFTER F-162 (shares the fman_port_set_cc_base tail anchor and the
fman_port.h declaration block). Idempotent via the F-205 markers.
"""

import os
import sys

kroot = "drivers/net/ethernet/freescale/fman"
port_c = os.path.join(kroot, "fman_port.c")
port_h = os.path.join(kroot, "fman_port.h")

changes = 0

# ── 1. fman_port.c: append the two functions after F-162's clearer ──
with open(port_c) as f:
    src = f.read()

# Anchor on F-162's exported clearer (guaranteed present: F-162 runs first).
anchor = (
    "\trfpne &= ~(NIA_KG_DIRECT | 0x1Fu);\n"
    "\tiowrite32be(rfpne, &port->bmi_regs->rx.fmbm_rfpne);\n"
    "}\n"
    "EXPORT_SYMBOL_GPL(fman_port_clear_kg_direct_scheme);\n"
)

new_funcs = (
    "\n"
    "/* F-205 (T-M6-1 Phase 3): parser LCV slots for the hard-parse HXS graph.\n"
    " * Ethernet = slot 0 (universal catch-all), IPv4 = slot 5, IPv6 = slot 6\n"
    " * (silicon-fixed; vendor GetPrsHdrNum). */\n"
    "#define FMAN_HWP_HXS_ETH\t0\n"
    "#define FMAN_HWP_HXS_IPV4\t5\n"
    "#define FMAN_HWP_HXS_IPV6\t6\n"
    "\n"
    "/**\n"
    " * fman_port_set_lcv_split() - split this RX port's parser LCV so the ETH\n"
    " * (slot 0, catch-all), IPv4 (slot 5) and IPv6 (slot 6) HXS contribute\n"
    " * distinct bits.\n"
    " * @port: the FMan RX port\n"
    " * @catchall_bit: LCV bit ORed by the ETH HXS (slot 0) — present on EVERY\n"
    " *   frame, so a catch-all scheme (mv=catchall_bit) matches non-IP frames\n"
    " *   (ARP, ND, multicast, L2) that would otherwise NO_SCHEME (0x20000000)\n"
    " * @v4_bit: single LCV bit ORed by the IPv4 HXS (e.g. 0x40000000)\n"
    " * @v6_bit: single LCV bit ORed by the IPv6 HXS (e.g. 0x80000000)\n"
    " *\n"
    " * Zeroes every pmda[].lcv, then sets slot0=catchall, slot5=v4, slot6=v6,\n"
    " * and reads back to verify (S6 R10.2). An IPv4 frame's LCV = v4|catchall,\n"
    " * IPv6 = v6|catchall, non-IP = catchall only. DO NOT zero slot 0 — that was\n"
    " * the 2026-08-19 NO_SCHEME/deaf-port root cause. Enables per-family KeyGen\n"
    " * scheme selection via kgse_mv with a catch-all fallback.\n"
    " *\n"
    " * Returns 0 on success, -EINVAL on a NULL/non-RX port, -EIO on readback\n"
    " * mismatch.\n"
    " */\n"
    "int fman_port_set_lcv_split(struct fman_port *port, u32 catchall_bit,\n"
    "\t\t\t   u32 v4_bit, u32 v6_bit)\n"
    "{\n"
    "\tstruct fman_port_hwp_regs __iomem *regs;\n"
    "\tint i;\n"
    "\n"
    "\tif (!port || port->port_type != FMAN_PORT_TYPE_RX || !port->hwp_regs)\n"
    "\t\treturn -EINVAL;\n"
    "\tregs = port->hwp_regs;\n"
    "\n"
    "\t/* The pmda[].lcv words are hard-parser shadow RAM; they only commit to\n"
    "\t * the live parse array while the parser is stopped (PCAC PSTOP/PSTAT).\n"
    "\t * Mainline init_hwp() and the vendor SetPcd() both bracket every PMDA\n"
    "\t * write this way; writing them live lets the register readback pass\n"
    "\t * while the live parse memory silently keeps the old value (root cause\n"
    "\t * of the 2026-08-19 'split set but slots 5/6 stay dark' board result).\n"
    "\t */\n"
    "\tstop_port_hwp(port);\n"
    "\tfor (i = 0; i < HWP_HXS_COUNT; i++)\n"
    "\t\tiowrite32be(0, &regs->pmda[i].lcv);\n"
    "\tiowrite32be(catchall_bit, &regs->pmda[FMAN_HWP_HXS_ETH].lcv);\n"
    "\tiowrite32be(v4_bit, &regs->pmda[FMAN_HWP_HXS_IPV4].lcv);\n"
    "\tiowrite32be(v6_bit, &regs->pmda[FMAN_HWP_HXS_IPV6].lcv);\n"
    "\n"
    "\tif (ioread32be(&regs->pmda[FMAN_HWP_HXS_ETH].lcv) != catchall_bit ||\n"
    "\t    ioread32be(&regs->pmda[FMAN_HWP_HXS_IPV4].lcv) != v4_bit ||\n"
    "\t    ioread32be(&regs->pmda[FMAN_HWP_HXS_IPV6].lcv) != v6_bit) {\n"
    "\t\tstart_port_hwp(port);\n"
    "\t\tdev_err(port->dev,\n"
    "\t\t\t\"fman_port: LCV-split readback mismatch (ca=0x%08x v4=0x%08x v6=0x%08x)\\n\",\n"
    "\t\t\tcatchall_bit, v4_bit, v6_bit);\n"
    "\t\treturn -EIO;\n"
    "\t}\n"
    "\tstart_port_hwp(port);\n"
    "\tdev_info(port->dev,\n"
    "\t\t \"fman_port: parser LCV split (ETH slot0=0x%08x, IPv4 slot5=0x%08x, IPv6 slot6=0x%08x)\\n\",\n"
    "\t\t catchall_bit, v4_bit, v6_bit);\n"
    "\treturn 0;\n"
    "}\n"
    "EXPORT_SYMBOL_GPL(fman_port_set_lcv_split);\n"
    "\n"
    "/**\n"
    " * fman_port_clear_lcv_split() - restore this RX port's parser LCV to the\n"
    " * mainline default (all HXS lcv = 0xffffffff). Reversibility anchor for\n"
    " * disengage. Readback-verified.\n"
    " * @port: the FMan RX port\n"
    " */\n"
    "void fman_port_clear_lcv_split(struct fman_port *port)\n"
    "{\n"
    "\tstruct fman_port_hwp_regs __iomem *regs;\n"
    "\tint i;\n"
    "\n"
    "\tif (!port || port->port_type != FMAN_PORT_TYPE_RX || !port->hwp_regs)\n"
    "\t\treturn;\n"
    "\tregs = port->hwp_regs;\n"
    "\n"
    "\t/* Same parser-access-control rule as set_lcv_split(): quiesce before\n"
    "\t * restoring the PMDA shadow RAM, then restart after readback. */\n"
    "\tstop_port_hwp(port);\n"
    "\tfor (i = 0; i < HWP_HXS_COUNT; i++)\n"
    "\t\tiowrite32be(0xffffffff, &regs->pmda[i].lcv);\n"
    "\n"
    "\tif (ioread32be(&regs->pmda[FMAN_HWP_HXS_IPV6].lcv) != 0xffffffff)\n"
    "\t\tdev_warn(port->dev,\n"
    "\t\t\t \"fman_port: LCV-split clear readback mismatch\\n\");\n"
    "\tstart_port_hwp(port);\n"
    "}\n"
    "EXPORT_SYMBOL_GPL(fman_port_clear_lcv_split);\n"
)

if "fman_port_set_lcv_split" in src:
    print("### F-205: fman_port.c LCV-split functions already present")
elif anchor in src:
    src = src.replace(anchor, anchor + new_funcs, 1)
    changes += 1
    with open(port_c, "w") as f:
        f.write(src)
    print("### F-205: fman_port.c LCV-split functions added")
else:
    print("### F-205: FATAL: F-162 clearer tail anchor not found in fman_port.c "
          "(F-162 must run first)")
    sys.exit(1)

# ── 2. fman_port.h: declare the new functions after F-162's decls ──
with open(port_h) as f:
    hsrc = f.read()

old_h = (
    "/* F-162: direct KeyGen scheme addressing in fmbm_rfpne (NIA_KG_DIRECT) */\n"
    "int fman_port_set_kg_direct_scheme(struct fman_port *port, u8 scheme_id);\n"
    "void fman_port_clear_kg_direct_scheme(struct fman_port *port);\n"
)
new_h = (
    old_h +
    "\n"
    "/* F-205: parser LCV split for per-family (IPv4/IPv6) KeyGen scheme select\n"
    " * plus an ETH slot-0 catch-all bit for non-IP frames. */\n"
    "int fman_port_set_lcv_split(struct fman_port *port, u32 catchall_bit,\n"
    "\t\t\t   u32 v4_bit, u32 v6_bit);\n"
    "void fman_port_clear_lcv_split(struct fman_port *port);\n"
)
if "fman_port_set_lcv_split" in hsrc:
    print("### F-205: fman_port.h declarations already present")
elif old_h in hsrc:
    hsrc = hsrc.replace(old_h, new_h, 1)
    changes += 1
    with open(port_h, "w") as f:
        f.write(hsrc)
    print("### F-205: fman_port.h declarations added")
else:
    print("### F-205: FATAL: F-162 declaration anchor not found in fman_port.h")
    sys.exit(1)

if changes:
    print(f"### F-205: {changes} change(s) applied")
else:
    print("### F-205: no changes (already present)")
