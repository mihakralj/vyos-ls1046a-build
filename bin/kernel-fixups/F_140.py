"""F-140: Add IPv6 ehash table (key_size=37) and v6 KG scheme to FE-VM chain build.

M6 Piece 2: The v4 FE-VM chain uses a single ehash table with key_size=13 and
a single KG scheme.  For v6, we need a second ehash table with key_size=37
(16+16+1+2+2 bytes for SIP+DIP+PROTO+SPORT+DPORT) and a second KG scheme.

The EKFC value is identical (0x001C0006) — the silicon determines field size
from the parse result (IPv4=4B addresses, IPv6=16B addresses).  The extraction
order is the same MSB-first: SIP → DIP → PROTO → SPORT → DPORT.

Changes:
1. In __fman_pcd_fe_build_vm_chain(), add a second ehash_table_set() call
   with key_size=37 for v6 (table index 1).
2. Create a second KG scheme (scheme 5) for v6 with EKFC=0x001C0006.
3. Store the v6 scheme handle in pcd for later binding.

Must run AFTER 0158 (which defines __fman_pcd_fe_build_vm_chain).
"""

import sys, os

kroot = "drivers/net/ethernet/freescale/fman"
pcd_c = os.path.join(kroot, "fman_pcd.c")

if not os.path.exists(pcd_c):
    print("### F-140: fman_pcd.c not found — skipping")
    sys.exit(0)

with open(pcd_c) as f:
    src = f.read()

changes = 0

# ── 1. Add v6 ehash table after the v4 one ──
# Find the v4 ehash_table_set call and add a v6 one after it
v4_ehash = "\terr = fman_pcd_ehash_table_set(pcd, ehash_mask,\n\t\t\t\t       ehash_key_sz, ehash_shift);"
if v4_ehash not in src:
    print("### F-140: v4 ehash_table_set not found — skipping")
    sys.exit(0)

v6_ehash_block = """\terr = fman_pcd_ehash_table_set(pcd, ehash_mask,
\t\t\t\t       ehash_key_sz, ehash_shift);
\tif (err)
\t\treturn err;

\t/* F-140: Second ehash table for IPv6 (37-byte key: SIP16+DIP16+PROTO1+SPORT2+DPORT2).
\t * Same EKFC (0x001C0006) — silicon determines field size from parse result.
\t * Table index 1; v4 flows use table 0, v6 flows use table 1.
\t */
\terr = fman_pcd_ehash_table_set(pcd, ehash_mask,
\t\t\t\t       37, ehash_shift);"""

if v6_ehash_block not in src:
    src = src.replace(v4_ehash, v6_ehash_block, 1)
    changes += 1
    print("### F-140: added v6 ehash table (key_size=37) after v4 table")
else:
    print("### F-140: v6 ehash table already present")

# ── 2. Add v6 KG scheme ──
# Find the fe_arm_engage call and add v6 scheme creation before it.
# The v6 scheme uses the same EKFC as v4.
# We need to create a scheme, bind it to the port, and store it.

# Find the fman_pcd_fe_engage function and add v6 scheme creation
engage_fn = "int fman_pcd_fe_engage(struct fman_pcd *pcd, u8 hw_port_id)"
if engage_fn not in src:
    print("### F-140: fman_pcd_fe_engage not found — skipping scheme creation")
else:
    # Find where the v4 scheme is used (the __fman_pcd_fe_arm_engage call)
    arm_call = "__fman_pcd_fe_arm_engage(pcd, hw_port_id, 0, miss_fqid, 0x001C0006);"
    if arm_call in src:
        # Add v6 scheme creation before the arm call
        v6_scheme_block = """\t/* F-140: Create v6 KG scheme (scheme 5) with same EKFC as v4.
\t * Silicon determines field size from parse result (16B for v6 addrs).
\t */
\tif (!pcd->kg_scheme_v6) {
\t\tpcd->kg_scheme_v6 = fman_pcd_kg_scheme_create(pcd,
\t\t\t\tFM_PCD_KG_SCHEME_HASH, 0x001C0006);
\t\tif (IS_ERR(pcd->kg_scheme_v6)) {
\t\t\terr = PTR_ERR(pcd->kg_scheme_v6);
\t\t\tpcd->kg_scheme_v6 = NULL;
\t\t\treturn err;
\t\t}
\t}
\terr = fman_pcd_kg_bind_port(pcd->kg_scheme_v6, hw_port_id);
\tif (err)
\t\treturn err;

\t"""
        if "kg_scheme_v6" not in src:
            src = src.replace(arm_call, v6_scheme_block + arm_call, 1)
            changes += 1
            print("### F-140: added v6 KG scheme creation + port bind before arm call")
        else:
            print("### F-140: v6 KG scheme already present")
    else:
        print("### F-140: arm_engage call not found in fman_pcd_fe_engage")

# ── 3. Add kg_scheme_v6 field to struct fman_pcd ──
# Find the struct fman_pcd definition and add the field
pcd_struct = "struct fman_pcd {"
if pcd_struct in src:
    # Find a good insertion point — after fe_port_armed
    insert_after = "\tDECLARE_BITMAP(fe_port_armed, 32);"
    if insert_after in src:
        new_field = insert_after + "\n\tstruct fman_pcd_kg_scheme *kg_scheme_v6;\t/* F-140: v6 KG scheme */"
        if "kg_scheme_v6" not in src:
            src = src.replace(insert_after, new_field, 1)
            changes += 1
            print("### F-140: added kg_scheme_v6 field to struct fman_pcd")
        else:
            print("### F-140: kg_scheme_v6 field already present")
    else:
        print("### F-140: fe_port_armed bitmap not found in struct fman_pcd")
else:
    print("### F-140: struct fman_pcd not found")

# ── 4. Add v6 scheme teardown in disengage ──
# Find the F-129 teardown block and add v6 scheme unbind
teardown_anchor = 'pr_info("fman_pcd: F-129 last port disengaged'
if teardown_anchor in src:
    # Find the teardown block and add v6 scheme cleanup
    unbind_call = "\t\tfman_pcd_kg_unbind_port(pcd->kg_scheme_v6);"
    if "kg_scheme_v6" in src and unbind_call not in src:
        # Insert before the pr_info
        src = src.replace(teardown_anchor, unbind_call + "\n" + teardown_anchor, 1)
        changes += 1
        print("### F-140: added v6 KG scheme unbind in teardown")
    elif "kg_scheme_v6" not in src:
        print("### F-140: kg_scheme_v6 not in src — skipping teardown")
    else:
        print("### F-140: v6 scheme unbind already present")

if changes:
    with open(pcd_c, "w") as f:
        f.write(src)
    print(f"### F-140: {changes} change(s) applied")
else:
    print("### F-140: no changes applied — may already be present")
    sys.exit(0)