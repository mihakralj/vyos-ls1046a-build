"""F-140: Add IPv6 ehash table (key_size=37) and v6 KG scheme arm to FE-VM chain.

M6 Piece 2: The v4 FE-VM chain uses a single ehash table with key_size=13 and
a single KG scheme.  For v6, we need a second ehash table with key_size=37
(16+16+1+2+2 bytes for SIP+DIP+PROTO+SPORT+DPORT) and a second KG scheme.

The EKFC value is identical (0x001C0006) — the silicon determines field size
from the parse result (IPv4=4B addresses, IPv6=16B addresses).  The extraction
order is the same MSB-first: SIP → DIP → PROTO → SPORT → DPORT.

Changes:
1. In __fman_pcd_fe_build_vm_chain() (fman_pcd.c), add a second
   ehash_table_set() call with key_size=37 for v6 (table index 1).
2. In fman_pcd_kg_port_arm_fe() (fman_pcd_kg.c), also find a free scheme
   slot and arm it for v6 with the same EKFC.
3. In fman_pcd_kg_port_disarm_fe(), iterate all schemes and disarm any
   that match the port (catches both v4 and v6 schemes).

Note: fman_pcd_kg.c only has an opaque struct fman_pcd* (forward-declared),
so we cannot access pcd->v6_scheme_id.  Instead, disarm iterates all schemes.

Must run AFTER 0158.
"""

import sys, os

kroot = "drivers/net/ethernet/freescale/fman"
pcd_c = os.path.join(kroot, "fman_pcd.c")
kg_c = os.path.join(kroot, "fman_pcd_kg.c")

total_changes = 0

# ═══════════════════════════════════════════════════════════════════════
# Part A: fman_pcd.c — v6 ehash table
# ═══════════════════════════════════════════════════════════════════════

if os.path.exists(pcd_c):
    with open(pcd_c) as f:
        src = f.read()
    changes = 0

    # A1. Add v6 ehash table after the v4 one in __fman_pcd_fe_build_vm_chain
    v4_ehash = "\terr = fman_pcd_ehash_table_set(pcd, ehash_mask,\n\t\t\t\t       ehash_key_sz, ehash_shift);"
    if v4_ehash in src:
        v6_ehash_block = """\terr = fman_pcd_ehash_table_set(pcd, ehash_mask,
\t\t\t\t       ehash_key_sz, ehash_shift);
\tif (err)
\t\treturn err;

\t/* F-140: Second ehash table for IPv6 (37-byte key).
\t * Same EKFC (0x001C0006) — silicon determines field size from parse result.
\t * Table index 1; v4 flows use table 0, v6 flows use table 1.
\t */
\terr = fman_pcd_ehash_table_set(pcd, ehash_mask,
\t\t\t\t       37, ehash_shift);"""
        if "37, ehash_shift" not in src:
            src = src.replace(v4_ehash, v6_ehash_block, 1)
            changes += 1
            print("### F-140: added v6 ehash table (key_size=37)")
        else:
            print("### F-140: v6 ehash table already present")
    else:
        print("### F-140: v4 ehash_table_set not found in fman_pcd.c")

    if changes:
        with open(pcd_c, "w") as f:
            f.write(src)
        print(f"### F-140: {changes} change(s) to fman_pcd.c")
        total_changes += changes
    else:
        print("### F-140: no changes to fman_pcd.c")
else:
    print("### F-140: fman_pcd.c not found")

# ═══════════════════════════════════════════════════════════════════════
# Part B: fman_pcd_kg.c — v6 scheme arm/disarm
# ═══════════════════════════════════════════════════════════════════════

if os.path.exists(kg_c):
    with open(kg_c) as f:
        kg_src = f.read()
    kg_changes = 0

    # B1. In fman_pcd_kg_port_arm_fe(), after setting slot->ekfc, also find
    #     a free scheme slot and arm it for v6 with the same EKFC.
    #     Note: fman_pcd_kg.c only has opaque struct fman_pcd*, so we cannot
    #     access pcd->v6_scheme_id.  We just always try to arm a v6 scheme.
    v4_arm = "\tif (ekfc)\n\t\tslot->ekfc = ekfc;"
    if v4_arm in kg_src:
        v6_arm_block = """\tif (ekfc)
\t\tslot->ekfc = ekfc;

\t/* F-140: Find a free KG scheme slot and arm it for IPv6.
\t * Same EKFC — silicon determines field size from parse result.
\t * Skip slots already used by this port (the v4 slot).
\t */
\tif (ekfc) {
\t\tint vi;
\t\tfor (vi = 0; vi < FM_KG_MAX_NUM_OF_SCHEMES; vi++) {
\t\t\tstruct keygen_scheme *vs = &keygen->schemes[vi];
\t\t\tif (vs->used && vs->hw_port_id == hw_port_id)
\t\t\t\tcontinue;\t/* already ours (v4 slot) */
\t\t\tif (!vs->used || vs->hw_port_id == 0) {
\t\t\t\t/* Free or unbound — take it for v6 */
\t\t\t\tvs->ekfc = ekfc;
\t\t\t\tvs->next_engine = 2;\t/* CC (AC_CC dispatch) */
\t\t\t\tvs->hw_port_id = hw_port_id;
\t\t\t\tvs->used = true;
\t\t\t\tpr_info("fman_pcd: v6 KG scheme %d armed for port 0x%02x (EKFC=0x%08x)\\n",
\t\t\t\t\tvi, hw_port_id, ekfc);
\t\t\t\tbreak;
\t\t\t}
\t\t}
\t}"""
        if "v6 KG scheme" not in kg_src:
            kg_src = kg_src.replace(v4_arm, v6_arm_block, 1)
            kg_changes += 1
            print("### F-140: added v6 KG scheme arm in arm_fe()")
        else:
            print("### F-140: v6 arm already present in fman_pcd_kg.c")
    else:
        print("### F-140: v4 arm block not found in fman_pcd_kg.c")

    # B2. In fman_pcd_kg_port_disarm_fe(), also disarm any v6 scheme bound
    #     to this port.  Iterate all schemes and clear any matching hw_port_id
    #     beyond the first one (the v4 slot is already handled by the existing
    #     disarm code).
    disarm_end = "\t(void)fman_pcd_kg_port_detach_cc(pcd, hw_port_id);"
    if disarm_end in kg_src:
        v6_disarm = """\t(void)fman_pcd_kg_port_detach_cc(pcd, hw_port_id);

\t/* F-140: Disarm any additional schemes bound to this port (v6).
\t * The first match is the v4 slot already disarmed above; clear any others.
\t */
\t{
\t\tint vi;
\t\tint found_v4 = 0;
\t\tfor (vi = 0; vi < FM_KG_MAX_NUM_OF_SCHEMES; vi++) {
\t\t\tstruct keygen_scheme *vs = &keygen->schemes[vi];
\t\t\tif (vs->used && vs->hw_port_id == hw_port_id) {
\t\t\t\tif (!found_v4) {
\t\t\t\t\tfound_v4 = 1;\t/* skip the v4 slot */
\t\t\t\t\tcontinue;
\t\t\t\t}
\t\t\t\tvs->used = false;
\t\t\t\tvs->ekfc = 0;
\t\t\t\tvs->next_engine = 0;
\t\t\t\tvs->hw_port_id = 0;
\t\t\t\tpr_info("fman_pcd: v6 KG scheme %d disarmed\\n", vi);
\t\t\t}
\t\t}
\t}"""
        if "v6 KG scheme" not in kg_src:
            kg_src = kg_src.replace(disarm_end, v6_disarm, 1)
            kg_changes += 1
            print("### F-140: added v6 KG scheme disarm in disarm_fe()")
        else:
            print("### F-140: v6 disarm already present in fman_pcd_kg.c")
    else:
        print("### F-140: disarm end not found in fman_pcd_kg.c")

    if kg_changes:
        with open(kg_c, "w") as f:
            f.write(kg_src)
        print(f"### F-140: {kg_changes} change(s) to fman_pcd_kg.c")
        total_changes += kg_changes
    else:
        print("### F-140: no changes to fman_pcd_kg.c")
else:
    print("### F-140: fman_pcd_kg.c not found")

if total_changes:
    print(f"### F-140: {total_changes} total change(s) applied")
else:
    print("### F-140: no changes — may already be present")
    sys.exit(0)