"""F-245 (T-M6-8 VLAN-v6 dig, 2026-09-04): generalize F-243/F-244's
sp_arm/sp_disarm from a hardcoded IPv6 HXS slot (6) to an arbitrary HXS
slot, so the F-244 magic-byte hook can be armed on slot 0 (ETH catch-all)
as a decisive discriminator test.

WHY: F-244's magic-byte hook, armed on slot 6 (IPv6 HXS), was silent on a
real transit IPv6 frame (specs/ask2-soft-parser-lcv-scheme-select.md
Sec6j) -- the second independent negative result on slot 6 specifically
(the first being the 2026-08-19 per-slot LCV sweep, which found only slot
0 ever activates on real transit traffic). Byte-for-byte comparison
against the real vendor SDK source (fm_port.c/fm_port.h,
GetPrsHdrNum()/PRS_HDR_SW_PRS_EN/hdrs[].softSeqAttach) confirms the
mechanism, bit layout, and slot-6-for-IPv6 constant are all correct --
so the open question is whether slot 6 is ever the LIVE hard-parser state
for real frames on this driver/silicon, not whether the write is right.
Arming the identical hook on slot 0 (ETH catch-all, known from the
2026-08-19 sweep to activate on every frame) is the cheap, decisive test:
if it fires there, the soft-parser trigger mechanism is proven fully
working and the defect is narrowed to slot 6 specifically; if it stays
silent there too, the trigger path itself is broken regardless of slot.

Adds a `hxs_slot` parameter to fman_port_sp_arm()/fman_port_sp_disarm()
(replacing the hardcoded FMAN_HWP_HXS_IPV6), and a new
cc_test_saved_slot[] array so sp_disarm doesn't need the slot repeated.
cc_test's `sp_arm <port_hex> [slot_hex]` verb defaults to slot 6 (IPv6)
when the slot argument is omitted, for backward compatibility with the
existing F-244 test procedure.

Must run after F-243 (fman_port_sp_arm/disarm, cc_test_saved_ssa/
cc_test_sp_armed anchors). Idempotent.
"""

import os
import sys

port_c = "drivers/net/ethernet/freescale/fman/fman_port.c"
port_h = "drivers/net/ethernet/freescale/fman/fman_port.h"
cc_test_c = "drivers/net/ethernet/freescale/fman/fman_pcd_cc_test.c"

for p in (port_c, port_h, cc_test_c):
    if not os.path.exists(p):
        print(f"### F-245: {p} not found")
        sys.exit(0)

marker = "F-245(sp-arm-any-slot)"

# ---------------------------------------------------------------------
# 1. fman_port.h: update declarations
# ---------------------------------------------------------------------
with open(port_h) as f:
    h_src = f.read()

if marker in h_src:
    print("### F-245: already applied (fman_port.h)")
else:
    h_old = (
        "int fman_port_sp_arm(struct fman_port *port, u32 ssa_val, u32 *saved_ssa);\n"
        "int fman_port_sp_disarm(struct fman_port *port, u32 saved_ssa);\n"
    )
    if h_old not in h_src:
        print("### F-245: FATAL: fman_port.h sp_arm/sp_disarm decl anchor not found -- F-243 must run first")
        sys.exit(1)
    if h_src.count(h_old) != 1:
        print(f"### F-245: FATAL: fman_port.h anchor not unique ({h_src.count(h_old)})")
        sys.exit(1)
    h_new = (
        f"/* {marker}: hxs_slot generalizes these from the hardcoded IPv6\n"
        " * slot (6) to any HXS slot (e.g. FMAN_HWP_HXS_ETH=0), for testing\n"
        " * the soft-parser trigger on slots other than IPv6. */\n"
        "int fman_port_sp_arm(struct fman_port *port, u32 hxs_slot, u32 ssa_val, u32 *saved_ssa);\n"
        "int fman_port_sp_disarm(struct fman_port *port, u32 hxs_slot, u32 saved_ssa);\n"
    )
    h_src = h_src.replace(h_old, h_new, 1)
    with open(port_h, "w") as f:
        f.write(h_src)
    print("### fman_port.h: F-245 hxs_slot parameter added")

# ---------------------------------------------------------------------
# 2. fman_port.c: implementation
# ---------------------------------------------------------------------
with open(port_c) as f:
    c_src = f.read()

if marker in c_src:
    print("### F-245: already applied (fman_port.c)")
else:
    c_old = (
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
    )
    if c_old not in c_src:
        print("### F-245: FATAL: fman_port.c fman_port_sp_arm body anchor not found -- F-243 must run first")
        sys.exit(1)
    if c_src.count(c_old) != 1:
        print(f"### F-245: FATAL: fman_port.c sp_arm anchor not unique ({c_src.count(c_old)})")
        sys.exit(1)
    c_new = (
        f"/* {marker}: hxs_slot replaces the hardcoded FMAN_HWP_HXS_IPV6. */\n"
        "int fman_port_sp_arm(struct fman_port *port, u32 hxs_slot, u32 ssa_val, u32 *saved_ssa)\n"
        "{\n"
        "\tstruct fman_port_hwp_regs __iomem *regs;\n"
        "\n"
        "\tif (!port || port->port_type != FMAN_PORT_TYPE_RX || !port->hwp_regs ||\n"
        "\t    !saved_ssa || hxs_slot >= HWP_HXS_COUNT)\n"
        "\t\treturn -EINVAL;\n"
        "\tregs = port->hwp_regs;\n"
        "\n"
        "\tstop_port_hwp(port);\n"
        "\t*saved_ssa = ioread32be(&regs->pmda[hxs_slot].ssa);\n"
        "\tiowrite32be(ssa_val, &regs->pmda[hxs_slot].ssa);\n"
        "\n"
        "\tif (ioread32be(&regs->pmda[hxs_slot].ssa) != ssa_val) {\n"
        "\t\tiowrite32be(*saved_ssa, &regs->pmda[hxs_slot].ssa);\n"
        "\t\tstart_port_hwp(port);\n"
        "\t\tdev_err(port->dev,\n"
        "\t\t\t\"fman_port: SSA arm readback mismatch (slot %u)\\n\", hxs_slot);\n"
        "\t\treturn -EIO;\n"
        "\t}\n"
        "\tstart_port_hwp(port);\n"
        "\tdev_info(port->dev,\n"
        "\t\t \"fman_port: HXS slot %u soft-sequence armed (0x%08x -> 0x%08x)\\n\",\n"
        "\t\t hxs_slot, *saved_ssa, ssa_val);\n"
        "\treturn 0;\n"
        "}\n"
        "EXPORT_SYMBOL_GPL(fman_port_sp_arm);\n"
    )
    c_src = c_src.replace(c_old, c_new, 1)

    c_old2 = (
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
    )
    if c_old2 not in c_src:
        print("### F-245: FATAL: fman_port.c fman_port_sp_disarm body anchor not found")
        sys.exit(1)
    if c_src.count(c_old2) != 1:
        print(f"### F-245: FATAL: fman_port.c sp_disarm anchor not unique ({c_src.count(c_old2)})")
        sys.exit(1)
    c_new2 = (
        "int fman_port_sp_disarm(struct fman_port *port, u32 hxs_slot, u32 saved_ssa)\n"
        "{\n"
        "\tstruct fman_port_hwp_regs __iomem *regs;\n"
        "\n"
        "\tif (!port || port->port_type != FMAN_PORT_TYPE_RX || !port->hwp_regs ||\n"
        "\t    hxs_slot >= HWP_HXS_COUNT)\n"
        "\t\treturn -EINVAL;\n"
        "\tregs = port->hwp_regs;\n"
        "\n"
        "\tstop_port_hwp(port);\n"
        "\tiowrite32be(saved_ssa, &regs->pmda[hxs_slot].ssa);\n"
        "\tif (ioread32be(&regs->pmda[hxs_slot].ssa) != saved_ssa) {\n"
    )
    c_src = c_src.replace(c_old2, c_new2, 1)

    with open(port_c, "w") as f:
        f.write(c_src)
    print("### fman_port.c: F-245 hxs_slot parameter added to sp_arm/sp_disarm")

# ---------------------------------------------------------------------
# 3. fman_pcd_cc_test.c: sp_arm/sp_disarm verbs gain an optional slot arg
# ---------------------------------------------------------------------
with open(cc_test_c) as f:
    t_src = f.read()

if marker in t_src:
    print("### F-245: already applied (fman_pcd_cc_test.c)")
    sys.exit(0)

t_arr_old = (
    "static u32 cc_test_saved_ssa[32];\n"
    "static bool cc_test_sp_armed[32];\n"
)
if t_arr_old not in t_src:
    print("### F-245: FATAL: cc_test_saved_ssa/cc_test_sp_armed array anchor not found")
    sys.exit(1)
if t_src.count(t_arr_old) != 1:
    print(f"### F-245: FATAL: array anchor not unique ({t_src.count(t_arr_old)})")
    sys.exit(1)
t_arr_new = t_arr_old + (
    f"/* {marker}: which HXS slot sp_arm used, so sp_disarm doesn't need it\n"
    " * repeated. */\n"
    "static u32 cc_test_saved_slot[32];\n"
)
t_src = t_src.replace(t_arr_old, t_arr_new, 1)

t_arm_old = (
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
)
if t_arm_old not in t_src:
    print("### F-245: FATAL: cc_test sp_arm/sp_disarm dispatch anchor not found -- F-243 must run first")
    sys.exit(1)
if t_src.count(t_arm_old) != 1:
    print(f"### F-245: FATAL: sp_arm/sp_disarm dispatch anchor not unique ({t_src.count(t_arm_old)})")
    sys.exit(1)
t_arm_new = (
    f"\t}} else if (sscanf(kbuf, \"sp_arm %hhi %hhi\", &port_id, &slot_id) == 2 ||\n"
    "\t\t    (sscanf(kbuf, \"sp_arm %hhi\", &port_id) == 1 &&\n"
    "\t\t     (slot_id = 6, true))) {\n"
    f"\t\t/* {marker}: slot_id defaults to 6 (IPv6 HXS) when omitted, for\n"
    "\t\t * backward compatibility with the F-244 test procedure. */\n"
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
    "\t\t\tint aerr = fman_port_sp_arm(rxport, slot_id, 0x00000420,\n"
    "\t\t\t\t\t\t     &cc_test_saved_ssa[port_id]);\n"
    "\n"
    "\t\t\tif (aerr) {\n"
    "\t\t\t\tret = aerr;\n"
    "\t\t\t} else {\n"
    "\t\t\t\tcc_test_sp_armed[port_id] = true;\n"
    "\t\t\t\tcc_test_saved_slot[port_id] = slot_id;\n"
    "\t\t\t\tpr_info(\"fman_pcd cc_test: port 0x%02x HXS slot %u soft-sequence armed\\n\",\n"
    "\t\t\t\t\tport_id, slot_id);\n"
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
    "\t\t\tint derr = fman_port_sp_disarm(rxport, cc_test_saved_slot[port_id],\n"
    "\t\t\t\t\t\t\tcc_test_saved_ssa[port_id]);\n"
    "\n"
    "\t\t\tcc_test_sp_armed[port_id] = false;\n"
)
t_src = t_src.replace(t_arm_old, t_arm_new, 1)

decl_old = (
    "\tstruct fman_pcd *pcd = m->private;\n"
    "\tchar *kbuf;\n"
    "\tu8 port_id;\n"
    "\tint ret;\n"
)
if decl_old not in t_src:
    print("### F-245: FATAL: cc_test_write() local-var decl anchor not found -- cannot add slot_id")
    sys.exit(1)
if t_src.count(decl_old) != 1:
    print(f"### F-245: FATAL: cc_test_write() decl anchor not unique ({t_src.count(decl_old)})")
    sys.exit(1)
decl_new = (
    "\tstruct fman_pcd *pcd = m->private;\n"
    "\tchar *kbuf;\n"
    "\tu8 port_id;\n"
    f"\tu8 slot_id; /* {marker} */\n"
    "\tint ret;\n"
)
t_src = t_src.replace(decl_old, decl_new, 1)

with open(cc_test_c, "w") as f:
    f.write(t_src)
print("### fman_pcd_cc_test.c: F-245 sp_arm optional slot arg added")
