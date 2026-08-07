"""F-178: wire F-162's NIA_KG_DIRECT helper into the ACTUAL arm path
(fman_pcd_kg_port_arm_fe()/_disarm_fe()), not just the abandoned
attach_cc()/detach_cc() CC-graft mechanism.

CONTEXT (2026-08-07, in response to direct user challenge: "vendor's real
ASK code works on this exact board/microcode -- what are we doing
differently?"). Full read of vendor's real FM_PORT_SetPCD()/SetPcd()
(999-layerscape-ask patch, fm_port.c) documents, for the exact
single-bound-scheme-per-port case this project's FE-VM model matches:

    case (e_FM_PORT_PCD_SUPPORT_PRS_AND_KG_AND_CC):
        tmpReg = NIA_KG_CC_EN;
        fallthrough;
    case (e_FM_PORT_PCD_SUPPORT_PRS_AND_KG):
        if (p_PcdParams->p_KgParams->directScheme)
            tmpReg |= (NIA_KG_DIRECT | physicalSchemeId);
        WRITE_UINT32(*p_BmiPrsNia, NIA_ENG_KG | tmpReg);

Vendor ALWAYS ORs NIA_KG_DIRECT | physicalSchemeId into fmbm_rfpne for a
directScheme port. F-162 (2026-08-05) already found and fixed exactly
this gap once -- but wired it ONLY into fman_pcd_kg_port_attach_cc()/
detach_cc(), the CONT_LOOKUP/group-AD "CC-graft" mechanism that this
project's own history has since abandoned (group-AD topology confirmed
dead 3 ways; direct RCCB->FE_ENTER, established later via F-147/F-148, is
the mechanism every T-M3-R test this project has ever run actually uses).

The live arm path is fman_pcd_kg_port_arm_fe()/_disarm_fe() (patch 0132),
a SEPARATE function pair from attach_cc()/detach_cc() -- confirmed by
direct read, it reprograms the scheme's next_engine=CC and calls
fman_port_set_cc_base() (fmbm_rccb + fmbm_rfpne's AC_CC-enable bit), but
NEVER calls fman_port_set_kg_direct_scheme() (F-162's own helper, which
already exists and already works -- it was just never called from here).
This is directly confirmed by dmesg on every single T-M3-R test this
project has ever run via fe_arm: "rfpne 0x00480200" -- NIA_ENG_HWK |
AC_CC, generic SI/match-vector scheme selection -- never
"0x00480200 | NIA_KG_DIRECT | scheme_id" (e.g. 0x00480304 for scheme 4),
the vendor-required, single-bound-scheme-port encoding documented in
arch/fman-microcode-210-programming-reference.md sec 5.1.

Without NIA_KG_DIRECT, KeyGen falls back to the generic SI/match-vector
walk (RM sec 4.4: first scheme where SI=1 AND (QLCV & kgse_mv)==kgse_mv
wins) instead of being told deterministically which scheme governs this
port's dispatch. Every T-M3-R test this session configured EKFC/key
format/hash_bytes_offset/PORT_ID on "scheme 4" specifically -- but if the
generic walk selects a DIFFERENT scheme (or scheme 4 was never meant to
be reached via generic matching in the first place, being a mainline
"direct"-style RSS scheme with mv=0 -- confirmed via this session's own
dmesg: "mv=0x00000000" on every arm), none of that careful per-scheme
configuration would ever be consulted for live traffic. This would
mechanistically explain the persistent zero-HIT symptom independent of
every other construction-level, sync-related, and config-value hypothesis
already tested and found negative today (F-053, the full PORT_ID/14-byte
range, F-176, F-177) -- all of which correctly configured scheme 4, but
none of which could matter if traffic never actually dispatches THROUGH
scheme 4 to begin with.

Fix: call fman_port_set_kg_direct_scheme(rxport, id) at the end of
fman_pcd_kg_port_arm_fe()'s success path (id is already in scope --
discovered via kg_find_port_scheme() at function entry), symmetric
fman_port_clear_kg_direct_scheme(rxport) added to
fman_pcd_kg_port_disarm_fe(). Reuses F-162's existing, already-CI-wired
helper functions verbatim -- no new register-level code, only two new
call sites.

Depends on F-162 (adds fman_port_set_kg_direct_scheme()/
fman_port_clear_kg_direct_scheme() to fman_port.c/.h) already being
applied -- F-162 runs earlier in ci-setup-kernel.sh's fixup sequence.
"""

import sys

path = "drivers/net/ethernet/freescale/fman/fman_pcd_kg.c"
with open(path) as f:
    src = f.read()

changes = 0


def apply_block(name, old, new):
    global src, changes
    marker = f"F-178: {name}"
    if marker in src:
        print(f"### F-178: {name} already applied")
        return
    if old not in src:
        print(
            f"### F-178: FATAL: expected '{name}' text not found verbatim "
            "-- fman_pcd_kg_port_arm_fe()/_disarm_fe() source has drifted "
            "since this fixup was written, or F-162 has not applied. "
            "Refusing to guess."
        )
        sys.exit(1)
    src = src.replace(old, new, 1)
    changes += 1
    print(f"### fman_pcd_kg.c: F-178 {name} applied")


# --- 1. fman_pcd_kg_port_arm_fe(): direct-scheme addressing on success. ---
old_arm_tail = (
    "\t}\n"
    "\n"
    "\treturn 0;\n"
    "}\n"
    "EXPORT_SYMBOL_GPL(fman_pcd_kg_port_arm_fe);\n"
)
new_arm_tail = (
    "\t}\n"
    "\n"
    "\t/* F-178: arm_fe direct-scheme addressing. Vendor's real SetPcd()\n"
    "\t * ORs NIA_KG_DIRECT | physicalSchemeId into fmbm_rfpne for a\n"
    "\t * single-bound-scheme port (RM sec 4.4's SI/match-vector walk is\n"
    "\t * otherwise used, and this scheme's own mv is 0 -- not meant to\n"
    "\t * be reached that way). F-162 already wrote this helper but only\n"
    "\t * ever called it from the abandoned attach_cc() CC-graft path;\n"
    "\t * this is the FE-VM arm path every T-M3-R test actually uses.\n"
    "\t */\n"
    "\t(void)fman_port_set_kg_direct_scheme(rxport, id);\n"
    "\n"
    "\treturn 0;\n"
    "}\n"
    "EXPORT_SYMBOL_GPL(fman_pcd_kg_port_arm_fe);\n"
)
apply_block("arm_fe direct-scheme addressing", old_arm_tail, new_arm_tail)

# --- 2. fman_pcd_kg_port_disarm_fe(): symmetric teardown. ---
old_disarm = (
    "\trxport = fman_port_lookup_rx(fman, hw_port_id);\n"
    "\tif (rxport)\n"
    "\t\t(void)fman_port_set_cc_base(rxport, 0);\n"
    "\t(void)fman_pcd_kg_port_detach_cc(pcd, hw_port_id);\n"
)
new_disarm = (
    "\trxport = fman_port_lookup_rx(fman, hw_port_id);\n"
    "\tif (rxport) {\n"
    "\t\t(void)fman_port_set_cc_base(rxport, 0);\n"
    "\t\tfman_port_clear_kg_direct_scheme(rxport);\t/* F-178: disarm_fe direct-scheme teardown */\n"
    "\t}\n"
    "\t(void)fman_pcd_kg_port_detach_cc(pcd, hw_port_id);\n"
)
apply_block("disarm_fe direct-scheme teardown", old_disarm, new_disarm)

if changes:
    with open(path, "w") as f:
        f.write(src)
    print(f"### fman_pcd_kg.c: F-178 {changes} change(s) applied")
else:
    print("### fman_pcd_kg.c: F-178 no changes applied")
    sys.exit(1)
