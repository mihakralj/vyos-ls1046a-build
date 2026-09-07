"""F-240 (T-M6-8 VLAN-v6 CC-comparator capture): widen/restore a sacrificial
RX port's BMI Internal-Context copy window (FMBM_RICP) so probe2/F-239's
existing 176-byte capture also reaches CC_IC_KG_KEY_OFFSET (0x50) -- the IC
offset the CC-tree comparator's GEC-extracted composite key lives at.

WHY THIS IS THE RIGHT FIX (specs/ask2-ipv6-dual-lane-key-design.md Sec9.1,
2026-09-03): probe2 cannot see the GEC/CC-tree extraction result because
mainline's pass_prs_result/pass_hash_result/pass_time_stamp triplet copies
exactly 48 IC bytes (offsets [32,80) in "internal context source" space --
fman_sp_build_buffer_struct()), and 80 == 0x50 == CC_IC_KG_KEY_OFFSET: the
copy stops exactly where the GEC key starts. hash_result is not a usable
side channel either -- kgse_hc is force-zeroed for next_engine 2/3 (F-201),
so CC-tree schemes never populate it. And the FE-VM's documented HM opcode
set (arch/fman-microcode-210-programming-reference.md Sec8.1) has no
opcode that copies internal-context/workspace bytes into frame data --
every opcode operates on literal header fields or MURAM-constant data.

FMBM_RICP is a plain per-port BMI register (IC_TO_EXT | IC_FROM_INT |
IC_SIZE, all in 16-byte units) -- not parser shadow RAM like pmda[].lcv, no
PCAC stop/start bracket exists around it in the mainline init path either,
so this is a direct S6 R10.2 readback-verified read-modify-write. Widening
IC_SIZE from 48 (3 units) to 96 (6 units), IC_FROM_INT held at 32 (2 units,
unchanged), extends the copy to cover source [32,128) -- comfortably
spanning [0x50, 0x50+47) for the widest (dual_lane_pid, 47 B) key. IC_TO_EXT
(ext_buf_offset) is untouched, so probe2/F-239 needs no changes at all: the
newly-widened bytes land at window offset [48,96) (where "real frame data"
used to start), and real frame data now starts 48 bytes later, at window
offset +96 instead of +48.

SAFETY: narrow exposure window only. Any real (non-test) frame received on
the widened port between ricp_widen and ricp_restore gets an skb built with
dpaa_eth.c's compile-time DPAA_HWA_SIZE=48 headroom assumption against the
NEW 96-byte copy layout -- a real corruption risk for concurrent traffic on
that port, not just a wasted read. Restore immediately after the capture.
Sacrificial test port only (0x0d/eth1 throughout this investigation), never
eth0/management, never eth3/eth4 production.

CONFIRMED, not just theoretical (2026-09-03, same-day follow-up): a
multi-command interactive session over the slow serial-console relay left
the window open long enough (~a minute, several sequential debugfs
round-trips) to measurably corrupt real eth1 background traffic -- ~1500
RX errors accumulated (ip -s link show eth1), climbing, with dmesg
flooding net_ratelimit warnings; stopped instantly and cleanly on
ricp_restore. Fully reversible, no lasting damage, no reboot needed -- but
treat every widen/restore pair as one atomic, fast operation (ideally a
single round-trip), not a sequence of separate interactive commands, on
any port carrying real traffic. Full incident: decomp/fe-action-interpreter.md
"2026-09-03 (same day, second follow-up)".

Sections:
  1. fman_port.h: declare fman_port_widen_ricp()/fman_port_restore_ricp().
  2. fman_port.c: implement them, anchored after fman_port_clear_lcv_split()
     (F-205), same file/area, same readback-verify discipline.
  3. fman_pcd_cc_test.c: new debugfs verbs `ricp_widen <port_hex>` /
     `ricp_restore <port_hex>` on the existing cc_test node, saved-value
     array indexed by port_id (same pattern as cc_test_vlan_hm[32]).

Must run after the 0188 cc_test hybrid-EKFC+GEC patch (fman_pcd_cc_test.c
must already exist) and after F-205 (fman_port_clear_lcv_split anchor).
Idempotent.
"""

import os
import sys

port_c = "drivers/net/ethernet/freescale/fman/fman_port.c"
port_h = "drivers/net/ethernet/freescale/fman/fman_port.h"
cc_test_c = "drivers/net/ethernet/freescale/fman/fman_pcd_cc_test.c"

for p in (port_c, port_h, cc_test_c):
    if not os.path.exists(p):
        print(f"### F-240: {p} not found")
        sys.exit(0)

marker = "F-240(ricp-widen)"

# ---------------------------------------------------------------------
# 1. fman_port.h: declarations
# ---------------------------------------------------------------------
with open(port_h) as f:
    h_src = f.read()

if marker in h_src:
    print("### F-240: already applied (fman_port.h)")
else:
    h_old = "void fman_port_clear_lcv_split(struct fman_port *port);\n"
    if h_old not in h_src:
        print("### F-240: FATAL: fman_port.h clear_lcv_split anchor not found")
        sys.exit(1)
    if h_src.count(h_old) != 1:
        print(f"### F-240: FATAL: fman_port.h anchor not unique ({h_src.count(h_old)})")
        sys.exit(1)
    h_new = (
        h_old
        + "\n"
        + f"/* {marker}: widen/restore a sacrificial RX port's BMI\n"
        + " * Internal-Context copy window (FMBM_RICP) to reach\n"
        + " * CC_IC_KG_KEY_OFFSET for CC-comparator capture (probe2/F-239). */\n"
        + "int fman_port_widen_ricp(struct fman_port *port, u32 *saved_ricp);\n"
        + "int fman_port_restore_ricp(struct fman_port *port, u32 saved_ricp);\n"
    )
    h_src = h_src.replace(h_old, h_new, 1)
    with open(port_h, "w") as f:
        f.write(h_src)
    print("### fman_port.h: F-240 widen/restore RICP declarations added")

# ---------------------------------------------------------------------
# 2. fman_port.c: implementation
# ---------------------------------------------------------------------
with open(port_c) as f:
    c_src = f.read()

if marker in c_src:
    print("### F-240: already applied (fman_port.c)")
else:
    c_old = (
        "\tstart_port_hwp(port);\n"
        "}\n"
        "EXPORT_SYMBOL_GPL(fman_port_clear_lcv_split);\n"
    )
    if c_old not in c_src:
        print("### F-240: FATAL: fman_port.c clear_lcv_split end anchor not found")
        sys.exit(1)
    if c_src.count(c_old) != 1:
        print(f"### F-240: FATAL: fman_port.c anchor not unique ({c_src.count(c_old)})")
        sys.exit(1)
    c_new = c_old + (
        "\n"
        f"/**\n"
        f" * fman_port_widen_ricp() - {marker}: widen this RX port's BMI\n"
        " * Internal-Context copy window so the host-visible IC copy also\n"
        " * reaches CC_IC_KG_KEY_OFFSET (0x50), which the mainline\n"
        " * pass_prs_result/pass_hash_result/pass_time_stamp triplet (48 B,\n"
        " * ending exactly at offset 0x50) never covers. IC_FROM_INT stays 32\n"
        " * (2 units); IC_SIZE grows from 48 to 96 (6 units) so the copy spans\n"
        " * source [32,128) -- covers the full 46/47-byte dual-lane key.\n"
        " * IC_TO_EXT (ext_buf_offset) is untouched, so probe2/F-239's\n"
        " * existing capture window needs no change.\n"
        " * @port: the FMan RX port (sacrificial test port only)\n"
        " * @saved_ricp: out-param, the pre-widen FMBM_RICP value (pass to\n"
        " *   fman_port_restore_ricp() to undo)\n"
        " *\n"
        " * fmbm_ricp is a plain BMI register, not parser shadow RAM (no\n"
        " * PCAC stop/start bracket exists around it in the mainline init\n"
        " * path) -- direct read-modify-write, S6 R10.2 readback-verified.\n"
        " * NARROW EXPOSURE WINDOW: any real frame received on this port\n"
        " * while widened gets an skb built against dpaa_eth.c's compile-time\n"
        " * DPAA_HWA_SIZE=48 headroom assumption, now wrong by 48 bytes --\n"
        " * restore immediately after the capture.\n"
        " *\n"
        " * Returns 0 on success, -EINVAL on a NULL/non-RX port, -EIO on\n"
        " * readback mismatch (auto-restores the saved value on failure).\n"
        " */\n"
        "int fman_port_widen_ricp(struct fman_port *port, u32 *saved_ricp)\n"
        "{\n"
        "\tstruct fman_port_rx_bmi_regs __iomem *regs;\n"
        "\tu32 tmp;\n"
        "\n"
        "\tif (!port || port->port_type != FMAN_PORT_TYPE_RX ||\n"
        "\t    !port->bmi_regs || !saved_ricp)\n"
        "\t\treturn -EINVAL;\n"
        "\tregs = &port->bmi_regs->rx;\n"
        "\n"
        "\t*saved_ricp = ioread32be(&regs->fmbm_ricp);\n"
        "\n"
        "\ttmp = *saved_ricp & (BMI_IC_TO_EXT_MASK << BMI_IC_TO_EXT_SHIFT);\n"
        "\ttmp |= (2u & BMI_IC_FROM_INT_MASK) << BMI_IC_FROM_INT_SHIFT;\n"
        "\ttmp |= 6u & BMI_IC_SIZE_MASK;\n"
        "\tiowrite32be(tmp, &regs->fmbm_ricp);\n"
        "\n"
        "\tif (ioread32be(&regs->fmbm_ricp) != tmp) {\n"
        "\t\tiowrite32be(*saved_ricp, &regs->fmbm_ricp);\n"
        "\t\tdev_err(port->dev,\n"
        "\t\t\t\"fman_port: RICP widen readback mismatch\\n\");\n"
        "\t\treturn -EIO;\n"
        "\t}\n"
        "\tdev_info(port->dev,\n"
        "\t\t \"fman_port: RICP widened for CC-comparator capture (0x%08x -> 0x%08x)\\n\",\n"
        "\t\t *saved_ricp, tmp);\n"
        "\treturn 0;\n"
        "}\n"
        "EXPORT_SYMBOL_GPL(fman_port_widen_ricp);\n"
        "\n"
        "/**\n"
        " * fman_port_restore_ricp() - undo fman_port_widen_ricp().\n"
        " * @port: the FMan RX port\n"
        " * @saved_ricp: the value fman_port_widen_ricp() saved\n"
        " *\n"
        " * Returns 0 on success, -EINVAL on a NULL/non-RX port, -EIO on\n"
        " * readback mismatch.\n"
        " */\n"
        "int fman_port_restore_ricp(struct fman_port *port, u32 saved_ricp)\n"
        "{\n"
        "\tstruct fman_port_rx_bmi_regs __iomem *regs;\n"
        "\n"
        "\tif (!port || port->port_type != FMAN_PORT_TYPE_RX || !port->bmi_regs)\n"
        "\t\treturn -EINVAL;\n"
        "\tregs = &port->bmi_regs->rx;\n"
        "\n"
        "\tiowrite32be(saved_ricp, &regs->fmbm_ricp);\n"
        "\tif (ioread32be(&regs->fmbm_ricp) != saved_ricp) {\n"
        "\t\tdev_err(port->dev,\n"
        "\t\t\t\"fman_port: RICP restore readback mismatch\\n\");\n"
        "\t\treturn -EIO;\n"
        "\t}\n"
        "\tdev_info(port->dev, \"fman_port: RICP restored to 0x%08x\\n\", saved_ricp);\n"
        "\treturn 0;\n"
        "}\n"
        "EXPORT_SYMBOL_GPL(fman_port_restore_ricp);\n"
    )
    c_src = c_src.replace(c_old, c_new, 1)
    with open(port_c, "w") as f:
        f.write(c_src)
    print("### fman_port.c: F-240 widen/restore RICP implementation added")

# ---------------------------------------------------------------------
# 3. fman_pcd_cc_test.c: debugfs verbs
# ---------------------------------------------------------------------
with open(cc_test_c) as f:
    t_src = f.read()

if marker in t_src:
    print("### F-240: already applied (fman_pcd_cc_test.c)")
    sys.exit(0)

t_arr_old = "static u32 cc_test_vlan_hm[32];\n"
if t_arr_old not in t_src:
    print("### F-240: FATAL: fman_pcd_cc_test.c cc_test_vlan_hm anchor not found")
    sys.exit(1)
if t_src.count(t_arr_old) != 1:
    print(f"### F-240: FATAL: cc_test_vlan_hm anchor not unique ({t_src.count(t_arr_old)})")
    sys.exit(1)
t_arr_new = t_arr_old + (
    f"/* {marker}: saved FMBM_RICP value across ricp_widen/ricp_restore,\n"
    " * indexed by port_id, same pattern as cc_test_vlan_hm[] above. */\n"
    "static u32 cc_test_saved_ricp[32];\n"
    "static bool cc_test_ricp_widened[32];\n"
)
t_src = t_src.replace(t_arr_old, t_arr_new, 1)

t_dispatch_old = (
    "\t} else if (strncmp(kbuf, \"install \", 8) == 0) {\n"
    "\t\tret = cc_test_install(pcd, kbuf);\n"
    "\t\tif (ret == 0)\n"
    "\t\t\tret = count;\n"
    "\t} else {\n"
    "\t\tret = -EINVAL;\n"
    "\t}\n"
)
if t_dispatch_old not in t_src:
    print("### F-240: FATAL: fman_pcd_cc_test.c dispatch-tail anchor not found")
    sys.exit(1)
if t_src.count(t_dispatch_old) != 1:
    print(f"### F-240: FATAL: dispatch-tail anchor not unique ({t_src.count(t_dispatch_old)})")
    sys.exit(1)
t_dispatch_new = (
    "\t} else if (strncmp(kbuf, \"install \", 8) == 0) {\n"
    "\t\tret = cc_test_install(pcd, kbuf);\n"
    "\t\tif (ret == 0)\n"
    "\t\t\tret = count;\n"
    f"\t}} else if (sscanf(kbuf, \"ricp_widen %hhi\", &port_id) == 1) {{\n"
    f"\t\t/* {marker}: widen the port's BMI IC copy window so probe2\n"
    "\t\t * (F-239) can reach CC_IC_KG_KEY_OFFSET. Narrow exposure window\n"
    "\t\t * -- restore with ricp_restore as soon as the capture is read. */\n"
    "\t\tstruct fman_port *rxport =\n"
    "\t\t\tfman_port_lookup_rx(fman_pcd_get_fman(pcd), port_id);\n"
    "\n"
    "\t\tif (!rxport || port_id >= ARRAY_SIZE(cc_test_saved_ricp)) {\n"
    "\t\t\tret = -EINVAL;\n"
    "\t\t} else if (cc_test_ricp_widened[port_id]) {\n"
    "\t\t\tpr_warn(\"fman_pcd cc_test: port 0x%02x RICP already widened\\n\",\n"
    "\t\t\t\tport_id);\n"
    "\t\t\tret = -EBUSY;\n"
    "\t\t} else {\n"
    "\t\t\tint werr = fman_port_widen_ricp(rxport,\n"
    "\t\t\t\t\t\t\t &cc_test_saved_ricp[port_id]);\n"
    "\n"
    "\t\t\tif (werr) {\n"
    "\t\t\t\tret = werr;\n"
    "\t\t\t} else {\n"
    "\t\t\t\tcc_test_ricp_widened[port_id] = true;\n"
    "\t\t\t\tpr_info(\"fman_pcd cc_test: port 0x%02x RICP widened for CC-comparator capture\\n\",\n"
    "\t\t\t\t\tport_id);\n"
    "\t\t\t\tret = count;\n"
    "\t\t\t}\n"
    "\t\t}\n"
    f"\t}} else if (sscanf(kbuf, \"ricp_restore %hhi\", &port_id) == 1) {{\n"
    "\t\tstruct fman_port *rxport =\n"
    "\t\t\tfman_port_lookup_rx(fman_pcd_get_fman(pcd), port_id);\n"
    "\n"
    "\t\tif (!rxport || port_id >= ARRAY_SIZE(cc_test_saved_ricp)) {\n"
    "\t\t\tret = -EINVAL;\n"
    "\t\t} else if (!cc_test_ricp_widened[port_id]) {\n"
    "\t\t\tpr_warn(\"fman_pcd cc_test: port 0x%02x RICP not widened\\n\",\n"
    "\t\t\t\tport_id);\n"
    "\t\t\tret = -EINVAL;\n"
    "\t\t} else {\n"
    "\t\t\tint rerr = fman_port_restore_ricp(rxport,\n"
    "\t\t\t\t\t\t\t   cc_test_saved_ricp[port_id]);\n"
    "\n"
    "\t\t\tcc_test_ricp_widened[port_id] = false;\n"
    "\t\t\tif (rerr) {\n"
    "\t\t\t\tret = rerr;\n"
    "\t\t\t} else {\n"
    "\t\t\t\tpr_info(\"fman_pcd cc_test: port 0x%02x RICP restored\\n\",\n"
    "\t\t\t\t\tport_id);\n"
    "\t\t\t\tret = count;\n"
    "\t\t\t}\n"
    "\t\t}\n"
    "\t} else {\n"
    "\t\tret = -EINVAL;\n"
    "\t}\n"
)
t_src = t_src.replace(t_dispatch_old, t_dispatch_new, 1)

with open(cc_test_c, "w") as f:
    f.write(t_src)
print("### fman_pcd_cc_test.c: F-240 ricp_widen/ricp_restore verbs added")
