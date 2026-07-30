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
2. In fman_pcd_kg_port_arm_fe() (fman_pcd_keygen.c), also find a free scheme
   slot and arm it for v6 with the same EKFC.
3. In fman_pcd_kg_port_disarm_fe(), also disarm the v6 scheme.
4. Add v6_scheme_id field to the private struct fman_pcd in fman_pcd.c,
   initialized to -1 in fman_pcd_init().

Must run AFTER 0158 and AFTER F_090 (which adds fe_vm_chain_built).
"""

import sys, os

kroot = "drivers/net/ethernet/freescale/fman"
pcd_c = os.path.join(kroot, "fman_pcd.c")
keygen_c = os.path.join(kroot, "fman_pcd_kg.c")

total_changes = 0

# ═══════════════════════════════════════════════════════════════════════
# Part A: fman_pcd.c — v6 ehash table + struct field + init
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

    # A2. Add v6_scheme_id field to the private struct fman_pcd in fman_pcd.c.
    #     The struct starts with "struct fman_pcd {" around line 89.
    #     Insert after fe_armed_port (added by 0131) or after fe_exit_off.
    #     Use fe_armed_port as anchor since it's the last field before debugfs_dir.
    anchor = "\tu8 fe_armed_port;\t\t/* F-079-R4: last engaged port */"
    if anchor in src:
        new_field = anchor + "\n\tint v6_scheme_id;\t/* F-140: v6 KG scheme id, -1 = not armed */"
        if "v6_scheme_id" not in src:
            src = src.replace(anchor, new_field, 1)
            changes += 1
            print("### F-140: added v6_scheme_id field to private struct")
        else:
            print("### F-140: v6_scheme_id field already present")
    else:
        # Fallback: try fe_exit_off
        anchor2 = "\tunsigned long fe_exit_off;"
        if anchor2 in src:
            new_field2 = anchor2 + "\n\tint v6_scheme_id;\t/* F-140: v6 KG scheme id, -1 = not armed */"
            if "v6_scheme_id" not in src:
                src = src.replace(anchor2, new_field2, 1)
                changes += 1
                print("### F-140: added v6_scheme_id field (fallback anchor)")
            else:
                print("### F-140: v6_scheme_id already present (fallback)")
        else:
            print("### F-140: neither fe_armed_port nor fe_exit_off found in struct")

    # A3. Initialize v6_scheme_id = -1 in fman_pcd_init().
    #     Use pcd->fman = fman as anchor (first assignment after kzalloc).
    init_anchor = "\tpcd->fman = fman;"
    if init_anchor in src:
        v6_init = "\tpcd->v6_scheme_id = -1;\t/* F-140 */\n" + init_anchor
        if "v6_scheme_id = -1" not in src:
            src = src.replace(init_anchor, v6_init, 1)
            changes += 1
            print("### F-140: initialized v6_scheme_id = -1 in fman_pcd_init")
        else:
            print("### F-140: v6_scheme_id init already present")
    else:
        print("### F-140: pcd->fman = fman init not found in fman_pcd_init")

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
# Part B: fman_pcd_keygen.c — v6 scheme arm/disarm
# ═══════════════════════════════════════════════════════════════════════

if os.path.exists(keygen_c):
    with open(keygen_c) as f:
        kg_src = f.read()
    kg_changes = 0

    # B1. In fman_pcd_kg_port_arm_fe(), after setting slot->ekfc, also find
    #     a free scheme slot and arm it for v6 with the same EKFC.
    v4_arm = "\tif (ekfc)\n\t\tslot->ekfc = ekfc;"
    if v4_arm in kg_src:
        v6_arm_block = """\tif (ekfc)
\t\tslot->ekfc = ekfc;

\t/* F-140: Find a free KG scheme slot and arm it for IPv6.
\t * Same EKFC — silicon determines field size from parse result.
\t * Skip slots already used by this port (the v4 slot).
\t */
\tif (ekfc && pcd->v6_scheme_id < 0) {
\t\tint vi;
\t\tfor (vi = 0; vi < FM_KG_MAX_NUM_OF_SCHEMES; vi++) {
\t\t\tstruct keygen_scheme *vs = &keygen->schemes[vi];
\t\t\tif (vs->used && vs->hw_port_id == hw_port_id)
\t\t\t\tcontinue;\t/* already ours (v4 slot) */
\t\t\tif (!vs->used || vs->hw_port_id == 0) {
\t\t\t\t/* Free or unbound — take it for v6 */
\t\t\t\tvs->ekfc = ekfc;
\t\t\t\tvs->next_engine = 2;\t/* CC (AC_CC dispatch) */
\t\t\t\tvs->mode = 0x80000006;\t/* EN | CC/DONE */
\t\t\t\tvs->ccbs = 0;
\t\t\t\tvs->hw_port_id = hw_port_id;
\t\t\t\tvs->used = true;
\t\t\t\tpcd->v6_scheme_id = vi;
\t\t\t\tpr_info("fman_pcd: v6 KG scheme %d armed for port 0x%02x (EKFC=0x%08x)\\n",
\t\t\t\t\tvi, hw_port_id, ekfc);
\t\t\t\tbreak;
\t\t\t}
\t\t}
\t\tif (pcd->v6_scheme_id < 0)
\t\t\tpr_warn("fman_pcd: no free KG scheme for v6 on port 0x%02x\\n", hw_port_id);
\t}"""
        if "v6_scheme_id" not in kg_src:
            kg_src = kg_src.replace(v4_arm, v6_arm_block, 1)
            kg_changes += 1
            print("### F-140: added v6 KG scheme arm in arm_fe()")
        else:
            print("### F-140: v6 arm already present in keygen.c")
    else:
        print("### F-140: v4 arm block not found in keygen.c")

    # B2. In fman_pcd_kg_port_disarm_fe(), add v6 disarm
    disarm_end = "\t(void)fman_pcd_kg_port_detach_cc(pcd, hw_port_id);"
    if disarm_end in kg_src:
        v6_disarm = """\t(void)fman_pcd_kg_port_detach_cc(pcd, hw_port_id);

\t/* F-140: Disarm v6 KG scheme */
\tif (pcd->v6_scheme_id >= 0) {
\t\tstruct keygen_scheme *v6 = &keygen->schemes[pcd->v6_scheme_id];
\t\tv6->used = false;
\t\tv6->ekfc = 0;
\t\tv6->mode = 0;
\t\tv6->next_engine = 0;
\t\tv6->ccbs = 0;
\t\tv6->hw_port_id = 0;
\t\tpr_info("fman_pcd: v6 KG scheme %d disarmed\\n", pcd->v6_scheme_id);
\t\tpcd->v6_scheme_id = -1;
\t}"""
        if "v6_scheme_id" not in kg_src:
            kg_src = kg_src.replace(disarm_end, v6_disarm, 1)
            kg_changes += 1
            print("### F-140: added v6 KG scheme disarm in disarm_fe()")
        else:
            print("### F-140: v6 disarm already present in keygen.c")
    else:
        print("### F-140: disarm end not found in keygen.c")

    if kg_changes:
        with open(keygen_c, "w") as f:
            f.write(kg_src)
        print(f"### F-140: {kg_changes} change(s) to fman_pcd_keygen.c")
        total_changes += kg_changes
    else:
        print("### F-140: no changes to fman_pcd_keygen.c")
else:
    print("### F-140: fman_pcd_keygen.c not found")

if total_changes:
    print(f"### F-140: {total_changes} total change(s) applied")
else:
    print("### F-140: no changes — may already be present")
    sys.exit(0)