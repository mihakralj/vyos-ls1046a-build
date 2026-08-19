"""F-212 (T-M6-1 IPv6 productization, step 4): call the parser LCV split at
engage and restore it at disengage. GATED on fman_pcd.v6_enable (F-210); no-op
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
    "\t * reversibility contract; pcd-snapshot diff must be clean). Unbind\n"
    "\t * and disable the port's v6 scheme (used && this port && CCOBASE==1),\n"
    "\t * and restore the v4 scheme's match_vector to 0 (match-all) so a\n"
    "\t * subsequent v4-only re-arm is byte-identical. No-op when v6 is off.\n"
    "\t */\n"
    "\tif (fman_pcd_v6_enabled() && fman->keygen) {\n"
    "\t\tstruct fman_keygen *keygen = fman->keygen;\n"
    "\t\tstruct mutex *lock = fman_pcd_get_lock(pcd);\n"
    "\t\tint i;\n"
    "\n"
    "\t\tmutex_lock(lock);\n"
    "\t\tfor (i = 0; i < FM_KG_MAX_NUM_OF_SCHEMES; i++) {\n"
    "\t\t\tstruct keygen_scheme *s = &keygen->schemes[i];\n"
    "\n"
    "\t\t\tif (!s->used || s->hw_port_id != hw_port_id)\n"
    "\t\t\t\tcontinue;\n"
    "\t\t\tif (s->cc_base_offset == 1 && s->next_engine == 3) {\n"
    "\t\t\t\t/* the v6 scheme: unbind, disable, free the slot */\n"
    "\t\t\t\t(void)keygen_bind_port_to_schemes(keygen, (u8)i, false);\n"
    "\t\t\t\ts->used = false;\n"
    "\t\t\t\t(void)keygen_scheme_setup(keygen, (u8)i, false);\n"
    "\t\t\t\ts->used = false;\n"
    "\t\t\t} else if (s->match_vector) {\n"
    "\t\t\t\t/* the v4 scheme: restore match-all (mv=0) */\n"
    "\t\t\t\ts->match_vector = 0;\n"
    "\t\t\t\ts->used = false;\n"
    "\t\t\t\t(void)keygen_scheme_setup(keygen, (u8)i, true);\n"
    "\t\t\t}\n"
    "\t\t}\n"
    "\t\tmutex_unlock(lock);\n"
    "\t}\n"
    "\n"
    "\trxport = fman_port_lookup_rx(fman, hw_port_id);\n"
    "\tif (rxport) {\n"
    "\t\t(void)fman_port_set_cc_base(rxport, 0);\n"
    "\t\tfman_port_clear_kg_direct_scheme(rxport);\t/* F-178: disarm_fe direct-scheme teardown */\n"
    "\t\t/* F-212(disarm-lcv-restore): restore mainline parser LCV\n"
    "\t\t * (all pmda[].lcv=0xffffffff) so a later RSS/VPP mode sees a\n"
    "\t\t * register-identical parser. No-op when v6 was never enabled.\n"
    "\t\t */\n"
    "\t\tif (fman_pcd_v6_enabled())\n"
    "\t\t\tfman_port_clear_lcv_split(rxport);\n"
    "\t}\n",
)

if changes:
    with open(path, "w") as f:
        f.write(src)
    print(f"### F-212 complete ({changes} change(s))")
else:
    print("### F-212 no changes applied (already present)")
    sys.exit(0)
