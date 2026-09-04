"""F-243 (T-M6-8 VLAN-v6 dig, 2026-09-04): soft-parser LCV-injection PoC --
`sp_load`/`sp_arm`/`sp_disarm` verbs on cc_test.

FIRST VALIDATION STEP ONLY (specs/ask2-soft-parser-lcv-scheme-select.md
Sec6): loads a minimal, ground-truth-verified soft-parser bytecode
sequence (OR_IV_LCV 0x80000000; JMP HXS RETURN_HXS) and wires it to fire
on IPv6 frames (pmda[6].ssa) on a sacrificial port, so probe2/probe3 can
confirm the injected LCV bit is actually visible before ANY scheme-
selection or CC-tree changes are attempted. Bytecode encoding verified
directly against the real FMC Soft Parser Assembler source
(/tmp/kilo/fmc/source/spa/fm_sp_private.h, fm_sp_assembler.tab.c --
_FMSP_INSTR_CODE_OR_IV_LCV=0x0003, _FMSP_RETURN_HXS=0x3fe,
_FMSP_INSTR_MOD_JMP_HXS=0x0400, big-endian word serialization), not
reconstructed from names alone.

Bytecode (8 bytes, at instruction index 0x020 / byte offset 0x040, the
start of the user soft-parser code region per Sec6a/6b):

  00 03            OR_IV_LCV
  00 00            imm low16  (of 0x80000000)
  80 00            imm high16
  1F FE            JMP HXS RETURN_HXS (0x1800|0x0400|0x3fe)

`sp_load`: ioremap()s the parser's soft-parser code RAM
(0x01ac7000, FM_MM_PRS=0xc7000 within the FMan block -- Sec6a, confirmed
live via read-only /dev/mem check that this region is empty on mainline),
writes the 8 bytes as big-endian 16-bit words at offset 0x040,
readback-verifies, iounmaps. Global (not per-port) -- the code sits inert
in shared RAM until a port's pmda[].ssa points at it.

`sp_arm <port_hex>`: sets pmda[6].ssa (HXS slot 6 = IPv6, F-205's already-
established GetPrsHdrNum mapping) = PRS_HDR_SW_PRS_EN(0x400) | 0x020 =
0x00000420 (Sec6b's own worked example) on the given port, via the same
stop_port_hwp/write/readback/start_port_hwp bracket F-205 already uses
for pmda[].lcv (SSA is the same PMDA shadow-RAM class per RM
Sec5.9.3/Table 5-324).

`sp_disarm <port_hex>`: restores pmda[6].ssa = 0 (mainline default, no
soft sequence attached) on the given port.

SAFETY: the trigger (pmda[6].ssa) is per-port -- only an armed port's
IPv6-HXS traffic ever branches into the loaded code; other ports are
unaffected regardless of what sits in the shared code RAM. The bytecode
itself only ORs one bit into LCV and returns to normal hard-parse flow
(RETURN_HXS, not END_PARSE) -- no frame content is touched. Sacrificial
test port only (0x0d/eth1 throughout this investigation).

Must run after F-205 (pmda[]/stop_port_hwp/start_port_hwp,
FMAN_HWP_HXS_IPV6). Idempotent.
"""

import os
import sys

port_c = "drivers/net/ethernet/freescale/fman/fman_port.c"
port_h = "drivers/net/ethernet/freescale/fman/fman_port.h"
cc_test_c = "drivers/net/ethernet/freescale/fman/fman_pcd_cc_test.c"

for p in (port_c, port_h, cc_test_c):
    if not os.path.exists(p):
        print(f"### F-243: {p} not found")
        sys.exit(0)

marker = "F-243(sp-lcv-poc)"

# ---------------------------------------------------------------------
# 1. fman_port.h: declarations
# ---------------------------------------------------------------------
with open(port_h) as f:
    h_src = f.read()

if marker in h_src:
    print("### F-243: already applied (fman_port.h)")
else:
    h_old = "void fman_port_clear_lcv_split(struct fman_port *port);\n"
    if h_old not in h_src:
        print("### F-243: FATAL: fman_port.h clear_lcv_split anchor not found")
        sys.exit(1)
    if h_src.count(h_old) != 1:
        print(f"### F-243: FATAL: fman_port.h anchor not unique ({h_src.count(h_old)})")
        sys.exit(1)
    h_new = (
        h_old
        + "\n"
        + f"/* {marker}: arm/disarm a sacrificial RX port's IPv6 HXS soft-\n"
        + " * sequence attachment (pmda[6].ssa), for the soft-parser LCV-\n"
        + " * injection PoC. */\n"
        + "int fman_port_sp_arm(struct fman_port *port, u32 ssa_val, u32 *saved_ssa);\n"
        + "int fman_port_sp_disarm(struct fman_port *port, u32 saved_ssa);\n"
    )
    h_src = h_src.replace(h_old, h_new, 1)
    with open(port_h, "w") as f:
        f.write(h_src)
    print("### fman_port.h: F-243 sp_arm/sp_disarm declarations added")

# ---------------------------------------------------------------------
# 2. fman_port.c: implementation
# ---------------------------------------------------------------------
with open(port_c) as f:
    c_src = f.read()

if marker in c_src:
    print("### F-243: already applied (fman_port.c)")
else:
    c_old = "EXPORT_SYMBOL_GPL(fman_port_restore_ricp);\n"
    if c_old not in c_src:
        print("### F-243: FATAL: fman_port.c restore_ricp export anchor not found")
        sys.exit(1)
    if c_src.count(c_old) != 1:
        print(f"### F-243: FATAL: fman_port.c anchor not unique ({c_src.count(c_old)})")
        sys.exit(1)
    c_new = c_old + (
        "\n"
        f"/**\n"
        f" * fman_port_sp_arm() - {marker}: attach a soft-parser sequence to\n"
        " * this RX port's IPv6 HXS stage (pmda[6].ssa, RM Sec5.9.3/Table 5-324:\n"
        " * bit21=PRS_HDR_SW_PRS_EN, bits[22:31]=2-byte-resolution instruction\n"
        " * index). Same PMDA shadow-RAM class as F-205's pmda[].lcv -- same\n"
        " * stop/write/readback/start bracket.\n"
        " * @port: the FMan RX port (sacrificial test port only)\n"
        " * @ssa_val: the full pmda[6].ssa value to write (caller computes,\n"
        " *   e.g. 0x00000420 for PRS_HDR_SW_PRS_EN | instruction index 0x020)\n"
        " * @saved_ssa: out-param, the pre-arm value (pass to\n"
        " *   fman_port_sp_disarm() to undo)\n"
        " *\n"
        " * Returns 0 on success, -EINVAL on a NULL/non-RX port, -EIO on\n"
        " * readback mismatch (auto-restores the saved value on failure).\n"
        " */\n"
        "int fman_port_sp_arm(struct fman_port *port, u32 ssa_val, u32 *saved_ssa)\n"
        "{\n"
        "\tstruct fman_port_hwp_regs __iomem *regs;\n"
        "\n"
        "\tif (!port || port->port_type != FMAN_PORT_TYPE_RX || !port->hwp_regs ||\n"
        "\t    !saved_ssa)\n"
        "\t\treturn -EINVAL;\n"
        "\tregs = port->hwp_regs;\n"
        "\n"
        "\tstop_port_hwp(port);\n"
        "\t*saved_ssa = ioread32be(&regs->pmda[FMAN_HWP_HXS_IPV6].ssa);\n"
        "\tiowrite32be(ssa_val, &regs->pmda[FMAN_HWP_HXS_IPV6].ssa);\n"
        "\n"
        "\tif (ioread32be(&regs->pmda[FMAN_HWP_HXS_IPV6].ssa) != ssa_val) {\n"
        "\t\tiowrite32be(*saved_ssa, &regs->pmda[FMAN_HWP_HXS_IPV6].ssa);\n"
        "\t\tstart_port_hwp(port);\n"
        "\t\tdev_err(port->dev,\n"
        "\t\t\t\"fman_port: SSA arm readback mismatch\\n\");\n"
        "\t\treturn -EIO;\n"
        "\t}\n"
        "\tstart_port_hwp(port);\n"
        "\tdev_info(port->dev,\n"
        "\t\t \"fman_port: IPv6 HXS soft-sequence armed (0x%08x -> 0x%08x)\\n\",\n"
        "\t\t *saved_ssa, ssa_val);\n"
        "\treturn 0;\n"
        "}\n"
        "EXPORT_SYMBOL_GPL(fman_port_sp_arm);\n"
        "\n"
        "/**\n"
        f" * fman_port_sp_disarm() - undo fman_port_sp_arm().\n"
        " * @port: the FMan RX port\n"
        " * @saved_ssa: the value fman_port_sp_arm() saved\n"
        " *\n"
        " * Returns 0 on success, -EINVAL on a NULL/non-RX port, -EIO on\n"
        " * readback mismatch.\n"
        " */\n"
        "int fman_port_sp_disarm(struct fman_port *port, u32 saved_ssa)\n"
        "{\n"
        "\tstruct fman_port_hwp_regs __iomem *regs;\n"
        "\n"
        "\tif (!port || port->port_type != FMAN_PORT_TYPE_RX || !port->hwp_regs)\n"
        "\t\treturn -EINVAL;\n"
        "\tregs = port->hwp_regs;\n"
        "\n"
        "\tstop_port_hwp(port);\n"
        "\tiowrite32be(saved_ssa, &regs->pmda[FMAN_HWP_HXS_IPV6].ssa);\n"
        "\tif (ioread32be(&regs->pmda[FMAN_HWP_HXS_IPV6].ssa) != saved_ssa) {\n"
        "\t\tstart_port_hwp(port);\n"
        "\t\tdev_err(port->dev,\n"
        "\t\t\t\"fman_port: SSA disarm readback mismatch\\n\");\n"
        "\t\treturn -EIO;\n"
        "\t}\n"
        "\tstart_port_hwp(port);\n"
        "\tdev_info(port->dev, \"fman_port: IPv6 HXS soft-sequence disarmed (0x%08x)\\n\",\n"
        "\t\t saved_ssa);\n"
        "\treturn 0;\n"
        "}\n"
        "EXPORT_SYMBOL_GPL(fman_port_sp_disarm);\n"
    )
    c_src = c_src.replace(c_old, c_new, 1)
    with open(port_c, "w") as f:
        f.write(c_src)
    print("### fman_port.c: F-243 sp_arm/sp_disarm implementation added")

# ---------------------------------------------------------------------
# 3. fman_pcd_cc_test.c: sp_load/sp_arm/sp_disarm debugfs verbs
# ---------------------------------------------------------------------
with open(cc_test_c) as f:
    t_src = f.read()

if marker in t_src:
    print("### F-243: already applied (fman_pcd_cc_test.c)")
    sys.exit(0)

for needed in ("cc_test_saved_ricp",):
    if needed not in t_src:
        print(f"### F-243: FATAL: {needed} not found -- F-240 must run first")
        sys.exit(1)

t_arr_old = "static u32 cc_test_saved_ricp[32];\nstatic bool cc_test_ricp_widened[32];\n"
if t_arr_old not in t_src:
    print("### F-243: FATAL: cc_test_saved_ricp/cc_test_ricp_widened block not found")
    sys.exit(1)
if t_src.count(t_arr_old) != 1:
    print(f"### F-243: FATAL: array anchor not unique ({t_src.count(t_arr_old)})")
    sys.exit(1)
t_arr_new = t_arr_old + (
    f"/* {marker}: saved pmda[6].ssa across sp_arm/sp_disarm, indexed by\n"
    " * port_id, same pattern as cc_test_saved_ricp[] above. */\n"
    "static u32 cc_test_saved_ssa[32];\n"
    "static bool cc_test_sp_armed[32];\n"
)
t_src = t_src.replace(t_arr_old, t_arr_new, 1)

# --- sp_load implementation, anchored right before cc_test_write() ---
anchor2 = "static ssize_t cc_test_write(struct file *file, const char __user *buf,\n"
if anchor2 not in t_src:
    print("### F-243: FATAL: cc_test_write anchor not found")
    sys.exit(1)
if t_src.count(anchor2) != 1:
    print(f"### F-243: FATAL: anchor2 not unique ({t_src.count(anchor2)})")
    sys.exit(1)

sp_load_fn = (
    f"/* {marker}: parser soft-parser code RAM. FM_MM_PRS=0xc7000 within the\n"
    " * FMan block (vendor fm_common.h, cross-checked live against this\n"
    " * project's own already-validated FM_MM_KG offset). User code region\n"
    " * starts at instruction index 0x020 / byte offset 0x040 (vendor\n"
    " * SP_OFFSET=0x20 convention). */\n"
    "#define SP_CODE_PHYS_BASE 0x01ac7000UL\n"
    "#define SP_CODE_REGION_SIZE 0x1000UL\n"
    "#define SP_USER_CODE_BYTE_OFF 0x040U\n"
    "\n"
    "/* Minimal LCV-injection PoC, ground-truth-verified against the real FMC\n"
    " * Soft Parser Assembler source (see file header comment): OR_IV_LCV\n"
    " * 0x80000000 (opcode 0x0003, imm low16, imm high16), then JMP HXS\n"
    " * RETURN_HXS (0x1800 | 0x0400 | 0x3fe = 0x1FFE) to resume normal\n"
    " * hard-parse flow. Big-endian 16-bit words. */\n"
    "static const u16 cc_test_sp_poc_code[4] = {\n"
    "\t0x0003, 0x0000, 0x8000, 0x1FFE,\n"
    "};\n"
    "\n"
    f"/* {marker}: load the PoC bytecode into the parser's soft-parser code\n"
    " * RAM. Global (not per-port) -- the code sits inert until some port's\n"
    " * pmda[].ssa (see fman_port_sp_arm()) points at it. */\n"
    "static int cc_test_sp_load(void)\n"
    "{\n"
    "\tvoid __iomem *base;\n"
    "\tint i;\n"
    "\n"
    "\tbase = ioremap(SP_CODE_PHYS_BASE, SP_CODE_REGION_SIZE);\n"
    "\tif (!base)\n"
    "\t\treturn -ENOMEM;\n"
    "\n"
    "\tfor (i = 0; i < ARRAY_SIZE(cc_test_sp_poc_code); i++)\n"
    "\t\tiowrite16be(cc_test_sp_poc_code[i],\n"
    "\t\t\t    base + SP_USER_CODE_BYTE_OFF + i * 2);\n"
    "\n"
    "\tfor (i = 0; i < ARRAY_SIZE(cc_test_sp_poc_code); i++) {\n"
    "\t\tu16 rb = ioread16be(base + SP_USER_CODE_BYTE_OFF + i * 2);\n"
    "\n"
    "\t\tif (rb != cc_test_sp_poc_code[i]) {\n"
    "\t\t\tpr_err(\"fman_pcd cc_test: sp_load readback mismatch at word %d (wrote 0x%04x, read 0x%04x)\\n\",\n"
    "\t\t\t       i, cc_test_sp_poc_code[i], rb);\n"
    "\t\t\tiounmap(base);\n"
    "\t\t\treturn -EIO;\n"
    "\t\t}\n"
    "\t}\n"
    "\tiounmap(base);\n"
    "\tpr_info(\"fman_pcd cc_test: sp_load OK, %zu-byte LCV-injection PoC at code offset 0x%03x\\n\",\n"
    "\t\tsizeof(cc_test_sp_poc_code), SP_USER_CODE_BYTE_OFF);\n"
    "\treturn 0;\n"
    "}\n"
    "\n"
)
t_src = t_src.replace(anchor2, sp_load_fn + anchor2, 1)

# --- dispatch: sp_load / sp_arm / sp_disarm verbs ---
anchor3 = (
    "\t} else {\n"
    "\t\tret = -EINVAL;\n"
    "\t}\n"
    "\n"
    "\tkfree(kbuf);\n"
    "\treturn ret;\n"
    "}\n"
)
if anchor3 not in t_src:
    print("### F-243: FATAL: cc_test_write final-else anchor not found")
    sys.exit(1)
if t_src.count(anchor3) != 1:
    print(f"### F-243: FATAL: anchor3 not unique ({t_src.count(anchor3)})")
    sys.exit(1)
new3 = (
    f"\t}} else if (strncmp(kbuf, \"sp_load\", 7) == 0) {{\n"
    f"\t\t/* {marker}: load the LCV-injection PoC bytecode (global). */\n"
    "\t\tret = cc_test_sp_load();\n"
    "\t\tif (ret == 0)\n"
    "\t\t\tret = count;\n"
    "\t} else if (sscanf(kbuf, \"sp_arm %hhi\", &port_id) == 1) {\n"
    "\t\tstruct fman_port *rxport =\n"
    "\t\t\tfman_port_lookup_rx(fman_pcd_get_fman(pcd), port_id);\n"
    "\n"
    "\t\tif (!rxport || port_id >= ARRAY_SIZE(cc_test_saved_ssa)) {\n"
    "\t\t\tret = -EINVAL;\n"
    "\t\t} else if (cc_test_sp_armed[port_id]) {\n"
    "\t\t\tpr_warn(\"fman_pcd cc_test: port 0x%02x soft-sequence already armed\\n\",\n"
    "\t\t\t\tport_id);\n"
    "\t\t\tret = -EBUSY;\n"
    "\t\t} else {\n"
    "\t\t\t/* PRS_HDR_SW_PRS_EN(0x400) | instruction index 0x020 */\n"
    "\t\t\tint aerr = fman_port_sp_arm(rxport, 0x00000420,\n"
    "\t\t\t\t\t\t     &cc_test_saved_ssa[port_id]);\n"
    "\n"
    "\t\t\tif (aerr) {\n"
    "\t\t\t\tret = aerr;\n"
    "\t\t\t} else {\n"
    "\t\t\t\tcc_test_sp_armed[port_id] = true;\n"
    "\t\t\t\tpr_info(\"fman_pcd cc_test: port 0x%02x IPv6 soft-sequence armed\\n\",\n"
    "\t\t\t\t\tport_id);\n"
    "\t\t\t\tret = count;\n"
    "\t\t\t}\n"
    "\t\t}\n"
    "\t} else if (sscanf(kbuf, \"sp_disarm %hhi\", &port_id) == 1) {\n"
    "\t\tstruct fman_port *rxport =\n"
    "\t\t\tfman_port_lookup_rx(fman_pcd_get_fman(pcd), port_id);\n"
    "\n"
    "\t\tif (!rxport || port_id >= ARRAY_SIZE(cc_test_saved_ssa)) {\n"
    "\t\t\tret = -EINVAL;\n"
    "\t\t} else if (!cc_test_sp_armed[port_id]) {\n"
    "\t\t\tpr_warn(\"fman_pcd cc_test: port 0x%02x soft-sequence not armed\\n\",\n"
    "\t\t\t\tport_id);\n"
    "\t\t\tret = -EINVAL;\n"
    "\t\t} else {\n"
    "\t\t\tint derr = fman_port_sp_disarm(rxport,\n"
    "\t\t\t\t\t\t\tcc_test_saved_ssa[port_id]);\n"
    "\n"
    "\t\t\tcc_test_sp_armed[port_id] = false;\n"
    "\t\t\tif (derr) {\n"
    "\t\t\t\tret = derr;\n"
    "\t\t\t} else {\n"
    "\t\t\t\tpr_info(\"fman_pcd cc_test: port 0x%02x IPv6 soft-sequence disarmed\\n\",\n"
    "\t\t\t\t\tport_id);\n"
    "\t\t\t\tret = count;\n"
    "\t\t\t}\n"
    "\t\t}\n"
    "\t} else {\n"
    "\t\tret = -EINVAL;\n"
    "\t}\n"
    "\n"
    "\tkfree(kbuf);\n"
    "\treturn ret;\n"
    "}\n"
)
t_src = t_src.replace(anchor3, new3, 1)

with open(cc_test_c, "w") as f:
    f.write(t_src)
print("### fman_pcd_cc_test.c: F-243 sp_load/sp_arm/sp_disarm verbs added")
