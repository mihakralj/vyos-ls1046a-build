"""F-162: address the CC-attached KeyGen scheme directly in FMBM_RFPNE
(NIA_KG_DIRECT | scheme_id), matching the vendor SDK's documented
FM_PORT_SetPCD sequence -- not a workaround, a missing register field.

CONTEXT (2026-08-05, CC-Tree Rebuild Plan): F-159/F-160/F-161 all targeted
the CC-tree's own match table, leaf AD, and KeyGen scheme mode -- all
confirmed correct against vendor source and board dmesg -- yet hwport
0x11 still goes totally RX-silent (both matching and non-matching
traffic) within a handful of frames of any cc_test install, recovering
only on reboot. Every register/AD-level theory checked out; this pointed
at something outside the CC-tree/leaf/scheme entirely.

Reading the vendor SDK's actual FM_PORT_SetPCD()/SetPcd() (Peripherals/
FM/Port/fm_port.c, nxp-sdk branch) for the exact PRS_AND_KG_AND_CC case
this project's CC-graft model matches (one scheme bound per port, no
match-vector selection among several) shows:

    case (e_FM_PORT_PCD_SUPPORT_PRS_AND_KG_AND_CC):
        tmpReg = NIA_KG_CC_EN;
        fallthrough;
    case (e_FM_PORT_PCD_SUPPORT_PRS_AND_KG):
        if (p_PcdParams->p_KgParams->directScheme)
            tmpReg |= (NIA_KG_DIRECT | physicalSchemeId);
        WRITE_UINT32(*p_BmiPrsNia, NIA_ENG_KG | tmpReg);

NIA_ENG_KG (0x00480000) and NIA_KG_CC_EN (0x00000200) are already
correctly written by fman_port_set_cc_base() (0115) -- confirmed via
board dmesg on every single test this session ("rfpne 0x00480200").
NIA_KG_DIRECT (0x00000100) | physicalSchemeId is NOT: this project's
CC-graft (fman_pcd_kg_port_attach_cc(), grafting the port's one existing
scheme) is exactly vendor's "directScheme" case, but no code path has
ever written this. Without it, the KeyGen falls back to the generic
SI/match-vector scheme-selection walk (RM Sec 4.4, "walk schemes SC0-
SC31: first scheme where SI=1 AND (QLCV & kgse_mv)==kgse_mv wins")
instead of being told deterministically which scheme governs this
port's dispatch -- for a port meant to have exactly one CC-attached
scheme, that is a real, missing piece of vendor's documented sequence,
not a hardware limitation of CONT_LOOKUP/AC_CC dispatch itself.

This fixup:
  1. fman_port.c/.h: adds fman_port_set_kg_direct_scheme()/
     fman_port_clear_kg_direct_scheme() -- RMW fmbm_rfpne's low 5 bits
     (scheme id, 0-31) + the NIA_KG_DIRECT bit, mirroring
     fman_port_set_cc_base()'s existing RMW pattern exactly.
  2. fman_pcd_kg.c: fman_pcd_kg_port_attach_cc() calls the setter with
     the scheme id it already discovers via kg_find_port_scheme(), right
     before returning success. fman_pcd_kg_port_detach_cc() calls the
     clear counterpart, symmetric teardown.

This is a hypothesis fix like F-159/160/161 before it, but a more
precise one: every other register this project could cross-check against
vendor source has matched exactly. This is the one vendor writes that
this project never has.
"""

import sys, os

kroot = "drivers/net/ethernet/freescale/fman"
port_c = os.path.join(kroot, "fman_port.c")
port_h = os.path.join(kroot, "fman_port.h")
kg_c = os.path.join(kroot, "fman_pcd_kg.c")

for p in (port_c, port_h, kg_c):
    if not os.path.exists(p):
        print(f"### F-162: {p} not found")
        sys.exit(0)

changes = 0

# ── 1a. fman_port.c: add NIA_KG_DIRECT constant next to NIA_KG_CC_EN ──
with open(port_c) as f:
    src = f.read()

old_const = "#define NIA_KG_CC_EN\t\t\t\t\t0x00000200\n"
new_const = old_const + "#define NIA_KG_DIRECT\t\t\t\t\t0x00000100\t/* F-162 */\n"
if "NIA_KG_DIRECT" in src:
    print("### F-162: NIA_KG_DIRECT constant already present")
elif old_const in src:
    src = src.replace(old_const, new_const, 1)
    changes += 1
    print("### F-162: NIA_KG_DIRECT constant added")
else:
    print("### F-162: FATAL: NIA_KG_CC_EN anchor not found in fman_port.c")
    sys.exit(1)

# ── 1b. fman_port.c: add the setter/clearer functions after fman_port_set_cc_base() ──
old_tail = (
    "\tdev_info(port->dev,\n"
    "\t\t \"fman_port: RX coarse-classification base set to MURAM off 0x%x (rfpne 0x%08x)\\n\",\n"
    "\t\t cc_muram_off, rfpne);\n"
    "\treturn 0;\n"
    "}\n"
    "EXPORT_SYMBOL_GPL(fman_port_set_cc_base);\n"
)
new_functions = (
    "\n"
    "/**\n"
    " * fman_port_set_kg_direct_scheme() - address a single KeyGen scheme\n"
    " * directly in fmbm_rfpne (F-162)\n"
    " * @port: the FMan RX port\n"
    " * @scheme_id: physical scheme id (0-31) to address directly\n"
    " *\n"
    " * SDK FM_PORT_SetPCD's PRS_AND_KG_AND_CC case (fm_port.c SetPcd())\n"
    " * ORs NIA_KG_DIRECT | physicalSchemeId into fmbm_rfpne whenever the\n"
    " * port has exactly one (non-match-vector-selected) scheme bound --\n"
    " * this project's CC-graft model exactly. Without this, the KeyGen\n"
    " * falls back to the generic SI/match-vector scheme-selection walk\n"
    " * instead of deterministically using the CC-attached scheme.\n"
    " *\n"
    " * Returns 0 on success, -EINVAL on a NULL/non-RX port.\n"
    " */\n"
    "int fman_port_set_kg_direct_scheme(struct fman_port *port, u8 scheme_id)\n"
    "{\n"
    "\tu32 rfpne;\n"
    "\n"
    "\tif (!port || port->port_type != FMAN_PORT_TYPE_RX)\n"
    "\t\treturn -EINVAL;\n"
    "\n"
    "\trfpne = ioread32be(&port->bmi_regs->rx.fmbm_rfpne);\n"
    "\trfpne = (rfpne & ~0x1Fu) | NIA_KG_DIRECT | ((u32)scheme_id & 0x1Fu);\n"
    "\tiowrite32be(rfpne, &port->bmi_regs->rx.fmbm_rfpne);\n"
    "\n"
    "\tdev_info(port->dev,\n"
    "\t\t \"fman_port: KG direct-scheme addressing set, scheme %u (rfpne 0x%08x)\\n\",\n"
    "\t\t scheme_id, rfpne);\n"
    "\treturn 0;\n"
    "}\n"
    "EXPORT_SYMBOL_GPL(fman_port_set_kg_direct_scheme);\n"
    "\n"
    "/**\n"
    " * fman_port_clear_kg_direct_scheme() - inverse of\n"
    " * fman_port_set_kg_direct_scheme() (F-162)\n"
    " * @port: the FMan RX port\n"
    " */\n"
    "void fman_port_clear_kg_direct_scheme(struct fman_port *port)\n"
    "{\n"
    "\tu32 rfpne;\n"
    "\n"
    "\tif (!port || port->port_type != FMAN_PORT_TYPE_RX)\n"
    "\t\treturn;\n"
    "\n"
    "\trfpne = ioread32be(&port->bmi_regs->rx.fmbm_rfpne);\n"
    "\trfpne &= ~(NIA_KG_DIRECT | 0x1Fu);\n"
    "\tiowrite32be(rfpne, &port->bmi_regs->rx.fmbm_rfpne);\n"
    "}\n"
    "EXPORT_SYMBOL_GPL(fman_port_clear_kg_direct_scheme);\n"
)
if "fman_port_set_kg_direct_scheme" in src:
    print("### F-162: fman_port.c setter/clearer already present")
elif old_tail in src:
    src = src.replace(old_tail, old_tail + new_functions, 1)
    changes += 1
    print("### F-162: fman_port.c setter/clearer functions added")
else:
    print("### F-162: FATAL: fman_port_set_cc_base() tail anchor not found in fman_port.c")
    sys.exit(1)

if changes:
    with open(port_c, "w") as f:
        f.write(src)

# ── 2. fman_port.h: declare the new functions ──
with open(port_h) as f:
    hsrc = f.read()

old_h_tail = (
    "u32 fman_port_get_params_page(struct fman_port *port);\n"
    "int fman_port_set_params_page(struct fman_port *port, u32 muram_off,\n"
    "\t\t\t      void __iomem *page);\n"
    "\n"
    "#endif /* __FMAN_PORT_H */\n"
)
new_h = (
    "u32 fman_port_get_params_page(struct fman_port *port);\n"
    "int fman_port_set_params_page(struct fman_port *port, u32 muram_off,\n"
    "\t\t\t      void __iomem *page);\n"
    "\n"
    "/* F-162: direct KeyGen scheme addressing in fmbm_rfpne (NIA_KG_DIRECT) */\n"
    "int fman_port_set_kg_direct_scheme(struct fman_port *port, u8 scheme_id);\n"
    "void fman_port_clear_kg_direct_scheme(struct fman_port *port);\n"
    "\n"
    "#endif /* __FMAN_PORT_H */\n"
)
if "fman_port_set_kg_direct_scheme" in hsrc:
    print("### F-162: fman_port.h declarations already present")
elif old_h_tail in hsrc:
    hsrc = hsrc.replace(old_h_tail, new_h, 1)
    changes += 1
    with open(port_h, "w") as f:
        f.write(hsrc)
    print("### F-162: fman_port.h declarations added")
else:
    print("### F-162: FATAL: fman_port.h tail anchor not found")
    sys.exit(1)

# ── 3. fman_pcd_kg.c: wire calls into attach_cc()/detach_cc() ──
with open(kg_c) as f:
    ksrc = f.read()

old_attach_tail = (
    "\tmutex_unlock(lock);\n"
    "\treturn 0;\n"
    "}\n"
    "EXPORT_SYMBOL_GPL(fman_pcd_kg_port_attach_cc);\n"
)
new_attach_tail = (
    "\t{\n"
    "\t\tstruct fman_port *rxport = fman_port_lookup_rx(fman, hw_port_id);\n"
    "\n"
    "\t\t/* F-162: address this scheme directly, matching SDK FM_PORT_SetPCD's\n"
    "\t\t * directScheme case for a port with exactly one bound scheme. */\n"
    "\t\tif (rxport)\n"
    "\t\t\t(void)fman_port_set_kg_direct_scheme(rxport, id);\n"
    "\t}\n"
    "\n"
    "\tmutex_unlock(lock);\n"
    "\treturn 0;\n"
    "}\n"
    "EXPORT_SYMBOL_GPL(fman_pcd_kg_port_attach_cc);\n"
)
if "fman_port_set_kg_direct_scheme(rxport" in ksrc:
    print("### F-162: fman_pcd_kg_port_attach_cc() wiring already present")
elif old_attach_tail in ksrc:
    ksrc = ksrc.replace(old_attach_tail, new_attach_tail, 1)
    changes += 1
    print("### F-162: fman_pcd_kg_port_attach_cc() wired to set direct scheme")
else:
    print("### F-162: FATAL: fman_pcd_kg_port_attach_cc() success-path anchor not found")
    sys.exit(1)

old_detach_tail = (
    "\tslot->used = false;\n"
    "\terr = keygen_scheme_setup(keygen, id, true);\n"
    "\tmutex_unlock(lock);\n"
    "\treturn err;\n"
    "}\n"
    "EXPORT_SYMBOL_GPL(fman_pcd_kg_port_detach_cc);\n"
)
new_detach_tail = (
    "\tslot->used = false;\n"
    "\terr = keygen_scheme_setup(keygen, id, true);\n"
    "\n"
    "\t{\n"
    "\t\tstruct fman_port *rxport = fman_port_lookup_rx(fman, hw_port_id);\n"
    "\n"
    "\t\t/* F-162: symmetric teardown of the direct-scheme addressing. */\n"
    "\t\tif (rxport)\n"
    "\t\t\tfman_port_clear_kg_direct_scheme(rxport);\n"
    "\t}\n"
    "\n"
    "\tmutex_unlock(lock);\n"
    "\treturn err;\n"
    "}\n"
    "EXPORT_SYMBOL_GPL(fman_pcd_kg_port_detach_cc);\n"
)
if "fman_port_clear_kg_direct_scheme(rxport" in ksrc:
    print("### F-162: fman_pcd_kg_port_detach_cc() wiring already present")
elif old_detach_tail in ksrc:
    ksrc = ksrc.replace(old_detach_tail, new_detach_tail, 1)
    changes += 1
    print("### F-162: fman_pcd_kg_port_detach_cc() wired to clear direct scheme")
else:
    print("### F-162: FATAL: fman_pcd_kg_port_detach_cc() tail anchor not found")
    sys.exit(1)

if changes:
    with open(kg_c, "w") as f:
        f.write(ksrc)
    print(f"### F-162: {changes} total change(s) applied")
else:
    print("### F-162: no changes applied")
    sys.exit(1)
