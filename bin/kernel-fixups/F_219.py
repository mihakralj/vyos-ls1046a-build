"""F-219: per-port IPv6 arm intent — make ASK2 engage protocol-independent per
port instead of forcing every engaged port to arm v6 via one global flag.

ARCHITECTURE
------------
ASK2 engages independently per port (`set interfaces ethernet ethN offload ask`).
A port that carries IPv6 should arm v4+v6+catch-all; a different engaged port
may carry v4 only. The mechanism was already per-port (arm_fe(hw_port_id),
per-port HWP LCV window, per-port scheme partition), but F-211's decision was
gated only by the module-global fman_pcd_v6_enable — so every subsequently
engaged port armed v6 whenever that one flag was on. This encouraged artificial
simultaneous dual-port v6 re-arm and hid the correct per-port model.

FIX
---
Add pcd->fe_port_v6 bitmap, mirroring fe_port_armed. Export
fman_pcd_fe_set_port_v6(fm, hw_port_id, enable) so ask.ko can set this port's
intent immediately before fman_pcd_fe_engage(). Add internal
fman_pcd_port_wants_v6(pcd, hw_port_id) = global master gate AND bitmap bit.
F-211 uses this predicate: only the named port arms v6/catch-all/LCV. Other
engaged ports retain their existing mv=0 v4 scheme and parser defaults.

The global fsl_dpaa_fman.v6_enable remains a fail-closed master safety switch.
ask.ko additionally has ask.v6_offload; it sets the per-port bit only when that
module gate is on AND the target netdev has a non-link-local, non-tentative IPv6
address. No public ABI signature changes. F-210 may still write a dormant table1
node while the global master is on; without this port's v6 scheme nothing can
select it, so it is harmless and avoids invasive node-builder signature churn.

S0/SAFETY: bitmap-only software state; no direct register write. Existing
per-port F-211/F-212/F-214 hardware code runs unchanged after the predicate.
Idempotent markers. Must run AFTER F-210 (anchors on v6 accessor), and F-211
must be updated to use the helper.
"""

import os
import sys

kroot = "drivers/net/ethernet/freescale/fman"
pcd_c = os.path.join(kroot, "fman_pcd.c")
ih = os.path.join(kroot, "fman_pcd_internal.h")

changes = 0


def fatal(msg):
    print(f"### F-219: FATAL: {msg}")
    sys.exit(1)


with open(pcd_c) as f:
    src = f.read()

# 1. Per-port bitmap next to the existing engagement bitmap.
if "fe_port_v6" not in src:
    anchor = "\tDECLARE_BITMAP(fe_port_armed, 32);\t/* F-107: per-port engagement guard (gen_pool double-free prevention) */\n"
    if anchor not in src:
        fatal("fe_port_armed bitmap anchor not found")
    src = src.replace(
        anchor,
        anchor +
        "\t/* F-219: per-port IPv6 arm intent; set by ask.ko before engage. */\n"
        "\tDECLARE_BITMAP(fe_port_v6, 32);\n",
        1)
    changes += 1
    print("### fman_pcd.c: F-219 fe_port_v6 bitmap added")

# 2. Setter + internal predicate. MUST be placed AFTER the struct fman_pcd
#    definition (it dereferences pcd->fe_port_v6); the F-210 accessor sits
#    ABOVE the struct. F-219 v1 accidentally inserted here (before the struct)
#    and failed the full kernel compile with "invalid use of undefined type
#    struct fman_pcd". The persistent runner may already carry that broken
#    placement; detect+strip it before re-injecting at the correct location.
setter_pos = src.find("void fman_pcd_fe_set_port_v6")
struct_pos = src.find("struct fman_pcd {")
if setter_pos >= 0 and struct_pos >= 0 and setter_pos < struct_pos:
    old_start = src.rfind("\n/* F-219: set/clear one RX port", 0, setter_pos)
    pred_pos = src.find("bool fman_pcd_port_wants_v6", setter_pos)
    old_end = src.find("}\n", pred_pos)
    if old_start < 0 or pred_pos < 0 or old_end < 0:
        fatal("old pre-struct F-219 block found but boundaries are ambiguous")
    old_end += 2
    src = src[:old_start] + src[old_end:]
    changes += 1
    print("### fman_pcd.c: F-219 removed stale pre-struct setter/predicate block")

if "fman_pcd_fe_set_port_v6" not in src:
    anchor = (
        "};\n"
        "\n"
        "/*\n"
        " * Globally-rooted debugfs parent.  Created on the first fman_pcd_init()\n"
    )
    if anchor not in src:
        fatal("struct fman_pcd closing-brace anchor not found (layout drift)")
    funcs = (
        anchor +
        "\n"
        "/* F-219: set/clear one RX port's v6-arm intent. ask.ko calls this\n"
        " * immediately before fman_pcd_fe_engage(); no hardware is touched. */\n"
        "void fman_pcd_fe_set_port_v6(struct fman *fm, u8 hw_port_id, bool enable)\n"
        "{\n"
        "\tstruct fman_pcd *pcd = fm ? fman_get_pcd(fm) : NULL;\n"
        "\n"
        "\tif (!pcd || hw_port_id >= 32)\n"
        "\t\treturn;\n"
        "\tif (enable)\n"
        "\t\tset_bit(hw_port_id, pcd->fe_port_v6);\n"
        "\telse\n"
        "\t\tclear_bit(hw_port_id, pcd->fe_port_v6);\n"
        "\tpr_info(\"fman_pcd: F-219 port 0x%02x v6 intent=%u\\n\",\n"
        "\t\thw_port_id, enable ? 1 : 0);\n"
        "}\n"
        "EXPORT_SYMBOL_GPL(fman_pcd_fe_set_port_v6);\n"
        "\n"
        "bool fman_pcd_port_wants_v6(struct fman_pcd *pcd, u8 hw_port_id)\n"
        "{\n"
        "\treturn pcd && hw_port_id < 32 && fman_pcd_v6_enabled() &&\n"
        "\t       test_bit(hw_port_id, pcd->fe_port_v6);\n"
        "}\n"
    )
    src = src.replace(anchor, funcs, 1)
    changes += 1
    print("### fman_pcd.c: F-219 per-port setter + predicate added")

if changes:
    with open(pcd_c, "w") as f:
        f.write(src)

# 3. Internal declaration for fman_pcd_kg.c (F-211 predicate).
with open(ih) as f:
    h = f.read()

if "fman_pcd_port_wants_v6" not in h:
    anchor = "bool fman_pcd_v6_enabled(void);\n"
    if anchor not in h:
        fatal("F-210 v6-enabled declaration anchor not found")
    h = h.replace(
        anchor,
        anchor +
        "/* F-219: per-port v6 intent (global master AND port bitmap). */\n"
        "bool fman_pcd_port_wants_v6(struct fman_pcd *pcd, u8 hw_port_id);\n",
        1)
    with open(ih, "w") as f:
        f.write(h)
    changes += 1
    print("### fman_pcd_internal.h: F-219 predicate declaration added")

if changes:
    print(f"### F-219 complete ({changes} change(s))")
else:
    print("### F-219 no changes (already present)")
    sys.exit(0)
