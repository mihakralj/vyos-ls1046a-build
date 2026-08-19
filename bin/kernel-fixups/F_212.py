"""F-212 (T-M6-1 IPv6 productization, step 4): call the parser LCV split at
engage and restore it at disengage. GATED on fsl_dpaa_fman.v6_enable (F-210); no-op
when OFF, so v4 byte-identical.

WHAT / WHY
----------
The FMan scheme walk selects "first enabled scheme where SI=1 AND
(QLCV & kgse_mv)==kgse_mv". F-211 armed the v4 scheme with match_vector=V4BIT
and the v6 scheme with match_vector=V6BIT. For that discrimination to fire, the
per-frame QLCV must carry V4BIT for IPv4 frames and V6BIT for IPv6 frames. The
per-frame LCV is the OR of every parsed HXS slot's pmda[slot].lcv; mainline sets
every pmda[i].lcv=0xffffffff so QLCV is all-ones and BOTH mv tests always pass
(the walk would just take the first scheme). F-205 added the dormant
fman_port_set_lcv_split(port, v4_bit, v6_bit) primitive that zeroes all pmda[].lcv
then sets slot5(IPv4)=v4_bit, slot6(IPv6)=v6_bit; this fixup finally CALLS it.

  * engage (arm_fe): after F-211 binds the v6 scheme, call
    fman_port_set_lcv_split(rxport, V4BIT, V6BIT). Now IPv4 frames raise only
    V4BIT -> match the v4 scheme; IPv6 frames raise only V6BIT -> match the v6
    scheme (CCOBASE=1 -> table1 node). Proven on eth1 2026-08-19 (exp-apply):
    distinct kgse_spc on scheme2/scheme5, clean table1 HIT.
  * disengage (disarm_fe): fman_port_clear_lcv_split(rxport) restores every
    pmda[].lcv=0xffffffff (the reversibility anchor — mainline default), so a
    later VPP/RSS mode sees a register-identical parser. Ordered before the
    existing set_cc_base(0)/detach teardown does the rest.

SAFETY / S0
-----------
V4BIT/V6BIT are single-sourced with F-211 and bin/kg-lcv-probe.py
(0x40000000 / 0x80000000). Default OFF: fman_pcd_v6_enabled()==false -> neither
call runs -> parser LCV stays mainline 0xffffffff, v4 dispatch untouched. The
LCV split ONLY narrows which HXS bits contribute; it does not change v4 frame
parsing. Readback is inside F-205's primitives (S6 R10.2). Qdrant gate satisfied
(LCV mechanism + pmda offsets cross-checked; passing silicon proof 2026-08-19).

Must run AFTER F-211 (anchors on F-211's bind line in arm_fe) and AFTER F-178
(anchors on F-178's disarm_fe teardown block) and AFTER F-205 (the primitives).
Placed after F-211 in ci-setup-kernel.sh. Idempotent via the F-212 markers.

2026-08-19 board test (image 1730, .185) fixes folded into the disarm block:
  * D1 — the disarm teardown was gated on fman_pcd_v6_enabled(); flipping the
    gate off before disengage stranded the v6 scheme/LCV/mv state. Now
    SELF-DETECTING (scan the port's slots), not gated on the live param.
  * D2 — the disabled v6 slot retained match_vector, showing up as kgse_mv/vsp
    drift in pcd-snapshot. Now fully zero mv/ccobase/ekfc/next_engine/port
    before the disable write. NOTE: the separate per-port fmbm_rccb swap seen
    across a disengage/re-engage is PRE-EXISTING and v6-INDEPENDENT (reproduced
    with the gate OFF on a plain v4 re-engage) — it is gro MURAM allocation-order
    nondeterminism, out of scope for F-212, not introduced by the v6 path.
Note: the H1 root cause (v4 NO_SCHEME under the LCV split on the FE-engaged 10G
datapath) is a SILICON-RESEARCH item, NOT fixed here — v6 stays default-OFF.
"""

import os
import sys

path = "drivers/net/ethernet/freescale/fman/fman_pcd_kg.c"

# Single-sourced with F-211 / kg-lcv-probe.py
V4BIT = "0x40000000U"
V6BIT = "0x80000000U"

changes = 0


def fatal(msg):
    print(f"### F-212: FATAL: {msg}")
    sys.exit(1)


with open(path) as f:
    src = f.read()


def apply_block(name, marker, old, new):
    global src, changes
    if marker not in new:
        fatal(f"marker {marker} not embedded in replacement for '{name}'")
    if marker in src:
        print(f"### F-212: {name} already applied")
        return
    if old not in src:
        fatal(f"'{name}' anchor not found verbatim in {path} — source drifted "
              "(F-211/F-178 must run first).")
    src = src.replace(old, new, 1)
    changes += 1
    print(f"### {path}: F-212 {name} applied")


# ── 1. arm_fe: split the parser LCV after the v6 scheme is bound. ──
# Anchor on F-211's bind + unlock pair (unique to the v6-scheme-arm block).
apply_block(
    "arm_fe LCV split",
    "F-212(arm-lcv-split)",
    "\t\t(void)keygen_bind_port_to_schemes(keygen, v6id, true);\n"
    "\t\tmutex_unlock(lock);\n",
    "\t\t(void)keygen_bind_port_to_schemes(keygen, v6id, true);\n"
    "\t\tmutex_unlock(lock);\n"
    "\n"
    "\t\t/* F-212(arm-lcv-split): make QLCV carry V4BIT for IPv4 frames and\n"
    "\t\t * V6BIT for IPv6 frames so the (QLCV & kgse_mv)==kgse_mv walk\n"
    "\t\t * routes each family to its own scheme (F-205 primitive; proven\n"
    "\t\t * 2026-08-19). Non-fatal on error — the v6 scheme is armed but\n"
    "\t\t * would simply not be selected until the split lands.\n"
    "\t\t */\n"
    "\t\t{\n"
    "\t\t\tint lerr = fman_port_set_lcv_split(rxport, " + V4BIT +
    ", " + V6BIT + ");\n"
    "\n"
    "\t\t\tif (lerr)\n"
    "\t\t\t\tpr_warn(\"fman_pcd fe_arm: F-212 LCV split failed (%d) on port 0x%02x\\n\",\n"
    "\t\t\t\t\tlerr, hw_port_id);\n"
    "\t\t}\n",
)

# ── 2. disarm_fe: tear down the v6 scheme + restore the parser LCV
#    (reversibility anchors — pcd-snapshot must return to baseline). ──
apply_block(
    "disarm_fe v6 teardown + LCV restore",
    "F-212(disarm-v6-teardown)",
    "\trxport = fman_port_lookup_rx(fman, hw_port_id);\n"
    "\tif (rxport) {\n"
    "\t\t(void)fman_port_set_cc_base(rxport, 0);\n"
    "\t\tfman_port_clear_kg_direct_scheme(rxport);\t/* F-178: disarm_fe direct-scheme teardown */\n"
    "\t}\n",
    "\t/* F-212(disarm-v6-teardown): reverse F-211's v6 scheme arm so the\n"
    "\t * FMan returns to the exact pre-engage register state (the S1->S0\n"
    "\t * reversibility contract; pcd-snapshot diff must be clean).\n"
    "\t *\n"
    "\t * D1 FIX (2026-08-19 board test): this is SELF-DETECTING and NOT gated\n"
    "\t * on the current fman_pcd_v6_enabled() value. The gate can be flipped\n"
    "\t * off by an operator BETWEEN engage and disengage; gating the teardown\n"
    "\t * on it stranded the v6 scheme/LCV/mv state (the scheme stayed bound\n"
    "\t * with mv=V6BIT, the v4 scheme stayed narrowed to V4BIT). Instead we\n"
    "\t * detect the armed v6 state from the slots themselves: any scheme on\n"
    "\t * THIS port with cc_base_offset==1 && next_engine==3 is a v6 slot to\n"
    "\t * tear down; any scheme on this port with a non-zero match_vector is a\n"
    "\t * v4 slot to widen back to match-all. If neither exists (v6 was never\n"
    "\t * armed on this port) the scan is a harmless no-op.\n"
    "\t *\n"
    "\t * D2 FIX: fully zero the v6 slot's mv/ccobase/ekfc/port before the\n"
    "\t * disable write so its scheme RAM (kgse_mv etc.) returns to the\n"
    "\t * all-zero baseline pcd-snapshot expects -- leaving match_vector set\n"
    "\t * on a disabled slot showed up as kgse_mv/vsp drift.\n"
    "\t */\n"
    "\tif (fman->keygen) {\n"
    "\t\tstruct fman_keygen *keygen = fman->keygen;\n"
    "\t\tstruct mutex *lock = fman_pcd_get_lock(pcd);\n"
    "\t\tbool v6_torn_down = false;\n"
    "\t\tint i;\n"
    "\n"
    "\t\tmutex_lock(lock);\n"
    "\t\tfor (i = 0; i < FM_KG_MAX_NUM_OF_SCHEMES; i++) {\n"
    "\t\t\tstruct keygen_scheme *s = &keygen->schemes[i];\n"
    "\n"
    "\t\t\tif (!s->used || s->hw_port_id != hw_port_id)\n"
    "\t\t\t\tcontinue;\n"
    "\t\t\tif (s->cc_base_offset == 1 && s->next_engine == 3) {\n"
    "\t\t\t\t/* the v6 scheme: unbind, wipe to baseline, disable,\n"
    "\t\t\t\t * free the slot. Zero the discriminating fields BEFORE\n"
    "\t\t\t\t * the disable write so the scheme-RAM readback is clean.\n"
    "\t\t\t\t */\n"
    "\t\t\t\t(void)keygen_bind_port_to_schemes(keygen, (u8)i, false);\n"
    "\t\t\t\ts->match_vector   = 0;\n"
    "\t\t\t\ts->cc_base_offset = 0;\n"
    "\t\t\t\ts->cc_bits_sel    = 0;\n"
    "\t\t\t\ts->ekfc           = 0;\n"
    "\t\t\t\ts->next_engine    = 0;\n"
    "\t\t\t\ts->used           = true;\t/* setup writes this slot */\n"
    "\t\t\t\t(void)keygen_scheme_setup(keygen, (u8)i, false);\n"
    "\t\t\t\ts->hw_port_id     = 0;\n"
    "\t\t\t\ts->used           = false;\n"
    "\t\t\t\tv6_torn_down = true;\n"
    "\t\t\t} else if (s->match_vector) {\n"
    "\t\t\t\t/* the v4 scheme: restore match-all (mv=0) */\n"
    "\t\t\t\ts->match_vector = 0;\n"
    "\t\t\t\ts->used = false;\n"
    "\t\t\t\t(void)keygen_scheme_setup(keygen, (u8)i, true);\n"
    "\t\t\t\tv6_torn_down = true;\n"
    "\t\t\t}\n"
    "\t\t}\n"
    "\t\tmutex_unlock(lock);\n"
    "\n"
    "\t\t/* restore the parser LCV only if we actually undid a v6 arm on\n"
    "\t\t * this port (self-detected above) -- clearing to the mainline\n"
    "\t\t * 0xffffffff default is harmless either way, but skip the write\n"
    "\t\t * on ports that never had v6 armed.\n"
    "\t\t */\n"
    "\t\tif (v6_torn_down) {\n"
    "\t\t\trxport = fman_port_lookup_rx(fman, hw_port_id);\n"
    "\t\t\tif (rxport)\n"
    "\t\t\t\tfman_port_clear_lcv_split(rxport);\n"
    "\t\t}\n"
    "\t}\n"
    "\n"
    "\trxport = fman_port_lookup_rx(fman, hw_port_id);\n"
    "\tif (rxport) {\n"
    "\t\t(void)fman_port_set_cc_base(rxport, 0);\n"
    "\t\tfman_port_clear_kg_direct_scheme(rxport);\t/* F-178: disarm_fe direct-scheme teardown */\n"
    "\t}\n",
)

if changes:
    with open(path, "w") as f:
        f.write(src)
    print(f"### F-212 complete ({changes} change(s))")
else:
    print("### F-212 no changes applied (already present)")
    sys.exit(0)
