"""F-179: zero kgse_dv0/kgse_dv1/kgse_ekdv when this project's own EKFC
override is active, closing the uncontrolled-register confound found while
tracing KG_SCH_KN_PORT_ID's actual extraction source (2026-08-07).

CONTEXT. fm_pcd_ext.h's t_FmPcdExtractEntry has no dedicated union member
for e_FM_PCD_KG_EXTRACT_PORT_PRIVATE_INFO (only extractByHdr/extractNonHdr
exist), and vendor's fm_kg.c BuildSchemeRegs() assigns
`p_SchemeRegs->kgse_dv0 = p_KeyAndHash->privateDflt0` /
`kgse_dv1 = p_KeyAndHash->privateDflt1` -- meaning KG_SCH_KN_PORT_ID (EKFC
bit 31, when set) draws its extracted value from these same two "scheme
default register 0/1" fields. This project's own fman_keygen.c (mainline-
derived, unmodified for this specific logic) populates kgse_dv0/kgse_dv1
with DEFAULT_HASH_KEY_IPv4_ADDR/DEFAULT_HASH_KEY_L4_PORT (0x0A0A0A0A/
0x0B0B0B0B) inside the `if (scheme->use_hashing)` branch -- a completely
unrelated mainline RSS-hashing-fallback mechanism (default key material
used when a frame lacks the IPv4/L4-port header a generic-RSS scheme wants
to hash on). Live-read on .185 scheme 4 confirmed an exact byte-for-byte
match to these constants.

This project's own `//bmr`-equivalent hack (kgse_ekfc |= KG_SCH_KN_PORT_ID,
via fman_pcd_kg_scheme_set_ekfc()) never reprograms kgse_dv0/dv1 -- it
inherits whatever mainline's unrelated RSS logic happened to leave there.
Both existing PORT_ID board measurements (the 2026-07-13 184,320-candidate
brute force, which found extraction 0x00 -- not a byte present in
0x0a0a0a0a/0x0b0b0b0b, so that session's registers held something else
entirely -- and the 2026-08-07 16-candidate 0x00-0x0f sweep) were run
against this uncontrolled register, not a value either test accounted for.
Neither result can be trusted to rule the mechanism out.

Fix: once this project's own scheme->ekfc override is in control (the
`if (scheme->ekfc)` guard in keygen_scheme_setup(), fman_keygen.c, right
after kgse_fqb is set), explicitly zero kgse_dv0/kgse_dv1/kgse_ekdv too.
This makes any PORT_ID extraction read a known, controlled 0x00 --
eliminating the confound outright, regardless of the still-open question of
exactly which byte width/position within dv0/dv1 hardware actually reads.
A portid=0x00 DDR-key retest after this fixup is, for the first time, a
genuinely controlled experiment.

Full narrative: arch/fman-microcode-210-programming-reference.md sec 10.5a;
arch/fman-config-value-ledger.md's KG_SCH_KN_PORT_ID/<combine> rows;
arch/fman-vendor-source-extraction-2026-08-07.md sec 6; qdrant tag
kgse-dv0-dv1-mainline-rss-confound-confirmed.
"""

import sys

path = "drivers/net/ethernet/freescale/fman/fman_keygen.c"
with open(path) as f:
    src = f.read()

changes = 0


def apply_block(name, old, new):
    global src, changes
    marker = f"F-179: {name}"
    if marker in src:
        print(f"### F-179: {name} already applied")
        return
    if old not in src:
        print(
            f"### F-179: FATAL: expected '{name}' text not found verbatim "
            "-- keygen_scheme_setup()'s EKFC-override site has drifted "
            "since this fixup was written. Refusing to guess."
        )
        sys.exit(1)
    src = src.replace(old, new, 1)
    changes += 1
    print(f"### fman_keygen.c: F-179 {name} applied")


old_override = (
    "\tif (scheme->ekfc)\n"
    "\t\tscheme_regs.kgse_ekfc = scheme->ekfc;\n"
    "\n"
    "\tpr_info(\"ASK2-DBG scheme%u EKFC write: ekfc=0x%08x (slot->ekfc=0x%08x)\\n\", scheme_id, scheme_regs.kgse_ekfc, scheme->ekfc);\n"
)
new_override = (
    "\tif (scheme->ekfc) {\n"
    "\t\tscheme_regs.kgse_ekfc = scheme->ekfc;\n"
    "\n"
    "\t\t/* F-179: zero kgse_dv0/dv1/ekdv under EKFC override. These\n"
    "\t\t * are what KG_SCH_KN_PORT_ID (EKFC bit 31) extracts from when\n"
    "\t\t * set -- the use_hashing branch above just populated them\n"
    "\t\t * with mainline's own, unrelated RSS-fallback constants\n"
    "\t\t * (DEFAULT_HASH_KEY_IPv4_ADDR/_L4_PORT). Once this project's\n"
    "\t\t * own EKFC is in control, zero them so any PORT_ID\n"
    "\t\t * extraction reads a known 0x00, not a leftover RSS const.\n"
    "\t\t */\n"
    "\t\tscheme_regs.kgse_dv0 = 0;\n"
    "\t\tscheme_regs.kgse_dv1 = 0;\n"
    "\t\tscheme_regs.kgse_ekdv = 0;\n"
    "\t}\n"
    "\n"
    "\tpr_info(\"ASK2-DBG scheme%u EKFC write: ekfc=0x%08x (slot->ekfc=0x%08x) dv0=0x%08x dv1=0x%08x\\n\",\n"
    "\t\tscheme_id, scheme_regs.kgse_ekfc, scheme->ekfc,\n"
    "\t\tscheme_regs.kgse_dv0, scheme_regs.kgse_dv1);\n"
)
apply_block("zero kgse_dv0/dv1/ekdv under EKFC override", old_override, new_override)

if changes:
    with open(path, "w") as f:
        f.write(src)
    print(f"### fman_keygen.c: F-179 {changes} change(s) applied")
else:
    print("### fman_keygen.c: F-179 no changes applied")
    sys.exit(1)
