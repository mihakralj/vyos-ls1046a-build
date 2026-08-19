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

S0 QDRANT GATE satisfied: LCV mechanism + pmda offsets cross-checked against
arch/fman-microcode-210-programming-reference.md and confirmed by the passing
silicon experiment (scheme2/scheme5 distinct kgse_spc). Readback per S6 R10.2.

Must run AFTER F-162 (shares the fman_port_set_cc_base tail anchor and the
fman_port.h declaration block). Idempotent via the F-205 markers.
"""

import os
import sys

kroot = "drivers/net/ethernet/freescale/fman"
port_c = os.path.join(kroot, "fman_port.c")
port_h = "include/linux/fsl/fman_port.h"

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
    " * IPv4 = slot 5, IPv6 = slot 6 (silicon-fixed; vendor GetPrsHdrNum). */\n"
    "#define FMAN_HWP_HXS_IPV4\t5\n"
    "#define FMAN_HWP_HXS_IPV6\t6\n"
    "\n"
    "/**\n"
    " * fman_port_set_lcv_split() - split this RX port's parser LCV so only the\n"
    " * IPv4 (slot 5) and IPv6 (slot 6) HXS contribute distinct bits.\n"
    " * @port: the FMan RX port\n"
    " * @v4_bit: single LCV bit ORed by the IPv4 HXS (e.g. 0x40000000)\n"
    " * @v6_bit: single LCV bit ORed by the IPv6 HXS (e.g. 0x80000000)\n"
    " *\n"
    " * Zeroes every pmda[].lcv, then sets slots 5/6, and reads back to verify\n"
    " * (S6 R10.2). Enables per-family KeyGen scheme selection via kgse_mv.\n"
    " * DORMANT until the Phase-3 v6 arm path calls it.\n"
    " *\n"
    " * Returns 0 on success, -EINVAL on a NULL/non-RX port, -EIO on readback\n"
    " * mismatch.\n"
    " */\n"
    "int fman_port_set_lcv_split(struct fman_port *port, u32 v4_bit, u32 v6_bit)\n"
    "{\n"
    "\tstruct fman_port_hwp_regs __iomem *regs;\n"
    "\tint i;\n"
    "\n"
    "\tif (!port || port->port_type != FMAN_PORT_TYPE_RX || !port->hwp_regs)\n"
    "\t\treturn -EINVAL;\n"
    "\tregs = port->hwp_regs;\n"
    "\n"
    "\tfor (i = 0; i < HWP_HXS_COUNT; i++)\n"
    "\t\tiowrite32be(0, &regs->pmda[i].lcv);\n"
    "\tiowrite32be(v4_bit, &regs->pmda[FMAN_HWP_HXS_IPV4].lcv);\n"
    "\tiowrite32be(v6_bit, &regs->pmda[FMAN_HWP_HXS_IPV6].lcv);\n"
    "\n"
    "\tif (ioread32be(&regs->pmda[FMAN_HWP_HXS_IPV4].lcv) != v4_bit ||\n"
    "\t    ioread32be(&regs->pmda[FMAN_HWP_HXS_IPV6].lcv) != v6_bit) {\n"
    "\t\tdev_err(port->dev,\n"
    "\t\t\t\"fman_port: LCV-split readback mismatch (v4=0x%08x v6=0x%08x)\\n\",\n"
    "\t\t\tv4_bit, v6_bit);\n"
    "\t\treturn -EIO;\n"
    "\t}\n"
    "\tdev_info(port->dev,\n"
    "\t\t \"fman_port: parser LCV split (IPv4 slot5=0x%08x, IPv6 slot6=0x%08x)\\n\",\n"
    "\t\t v4_bit, v6_bit);\n"
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
    "\tfor (i = 0; i < HWP_HXS_COUNT; i++)\n"
    "\t\tiowrite32be(0xffffffff, &regs->pmda[i].lcv);\n"
    "\n"
    "\tif (ioread32be(&regs->pmda[FMAN_HWP_HXS_IPV6].lcv) != 0xffffffff)\n"
    "\t\tdev_warn(port->dev,\n"
    "\t\t\t \"fman_port: LCV-split clear readback mismatch\\n\");\n"
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
    "/* F-205: parser LCV split for per-family (IPv4/IPv6) KeyGen scheme select */\n"
    "int fman_port_set_lcv_split(struct fman_port *port, u32 v4_bit, u32 v6_bit);\n"
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
