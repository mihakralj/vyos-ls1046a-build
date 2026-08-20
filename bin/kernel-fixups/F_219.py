"""F-219: per-port IPv6 arm intent — make ASK2 engage protocol-independent per
port instead of forcing every engaged port to arm v6 via one global flag.

ARCHITECTURE
------------
ASK2 engages independently per port (`set interfaces ethernet ethN offload ask`).
A port carrying IPv6 should arm v4+v6+catch-all; another engaged port may carry
v4 only. The mechanism was already per-port (arm_fe(hw_port_id), per-port HWP
LCV window, per-port scheme partition), but F-211's decision was gated only by
the module-global fman_pcd_v6_enable, so every engaged port armed v6 whenever
that one flag was on (forcing artificial simultaneous dual-port re-arm).

FIX
---
Track v6-arm intent per RX port in a file-scope bitmap keyed by hw_port_id
(0x08..0x27; unique within this single-FMan LS1046A board — get-info num-fman=1).
A file-scope bitmap is used deliberately instead of a struct fman_pcd member so
the accessor/predicate can live beside F-210's fman_pcd_v6_enabled() at the top
of the file WITHOUT dereferencing struct fman_pcd (which is defined lower down);
this avoids the incomplete-type / comment-splitting placement hazards that broke
earlier revisions.

  * exported fman_pcd_fe_set_port_v6(fm, hw_port_id, enable): ask.ko sets this
    port's intent immediately before fman_pcd_fe_engage().
  * fman_pcd_port_wants_v6(pcd, hw_port_id) = global master gate
    (fman_pcd_v6_enabled()) AND the per-port bit. F-211 uses this predicate, so
    only the named port arms v6/catch-all/LCV; other engaged ports keep their
    mv=0 v4 scheme + parser defaults.

The global fsl_dpaa_fman.v6_enable stays a fail-closed master. ask.ko also has
ask.v6_offload and only sets the per-port bit when that gate is on AND the target
netdev has a usable global IPv6 address. No public ABI signature change; v4
byte-identical when off. The `pcd` arg to the predicate is unused (kept for a
stable call shape / future multi-FMan keying) — cast to void.

Idempotent. The CI kernel tree is reset pristine each run (git checkout -f
v<KVER>) then all fixups replay, so no cross-run self-repair is needed. Must run
AFTER F-210 (anchors on its accessor export). F-211 uses the predicate.
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

# Setter + predicate + file-scope bitmap, all placed right after F-210's
# accessor export. No struct fman_pcd dereference here, so placement above the
# struct definition is safe.
if "fman_pcd_fe_set_port_v6" in src:
    print("### F-219: setter/predicate already present in fman_pcd.c")
else:
    anchor = "EXPORT_SYMBOL_GPL(fman_pcd_v6_enabled);\n"
    if anchor not in src:
        fatal("F-210 v6 accessor export anchor not found in fman_pcd.c")
    block = (
        anchor +
        "\n"
        "/* F-219: per-port IPv6 arm intent, keyed by hw_port_id (file-scope so\n"
        " * no struct fman_pcd deref is needed at this point in the file). One\n"
        " * FMan on this board; hw_port_id is unique within it. */\n"
        "static DECLARE_BITMAP(fman_pcd_fe_port_v6, 64);\n"
        "\n"
        "/* Set/clear one RX port's v6-arm intent. ask.ko calls this immediately\n"
        " * before fman_pcd_fe_engage(); no hardware is touched. */\n"
        "void fman_pcd_fe_set_port_v6(struct fman *fm, u8 hw_port_id, bool enable)\n"
        "{\n"
        "\t(void)fm;\n"
        "\tif (hw_port_id >= 64)\n"
        "\t\treturn;\n"
        "\tif (enable)\n"
        "\t\tset_bit(hw_port_id, fman_pcd_fe_port_v6);\n"
        "\telse\n"
        "\t\tclear_bit(hw_port_id, fman_pcd_fe_port_v6);\n"
        "\tpr_info(\"fman_pcd: F-219 port 0x%02x v6 intent=%u\\n\",\n"
        "\t\thw_port_id, enable ? 1 : 0);\n"
        "}\n"
        "EXPORT_SYMBOL_GPL(fman_pcd_fe_set_port_v6);\n"
        "\n"
        "/* True when v6 should arm on this port: global master AND per-port bit. */\n"
        "bool fman_pcd_port_wants_v6(struct fman_pcd *pcd, u8 hw_port_id)\n"
        "{\n"
        "\t(void)pcd;\n"
        "\treturn hw_port_id < 64 && fman_pcd_v6_enabled() &&\n"
        "\t       test_bit(hw_port_id, fman_pcd_fe_port_v6);\n"
        "}\n"
    )
    src = src.replace(anchor, block, 1)
    changes += 1
    print("### fman_pcd.c: F-219 file-scope per-port v6 bitmap + setter + predicate added")

if changes:
    with open(pcd_c, "w") as f:
        f.write(src)

# Internal declaration for fman_pcd_kg.c (F-211 predicate).
with open(ih) as f:
    h = f.read()

if "fman_pcd_port_wants_v6" not in h:
    anchor = "bool fman_pcd_v6_enabled(void);\n"
    if anchor not in h:
        fatal("F-210 v6-enabled declaration anchor not found in internal header")
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
