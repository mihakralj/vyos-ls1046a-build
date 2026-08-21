"""F-140: Add the IPv6 ehash table (dormant, T-M6-1 Phase 1) to the FE-VM chain.

M6 Piece 2 / T-M6-1 Phase 1 (2026-08-19 correction). The v4 FE-VM chain uses a
single ehash table (14-byte key after F-188) and one KG scheme. For v6 we need
a SECOND ehash table sized for the 38-byte v6 key.

CORRECTED CONTRACT (was 37-byte / EKFC 0x001C0006 — both pre-PORT_ID and wrong):
  * v6 key is 38 bytes: PORT_ID(1) + SIP(16) + DIP(16) + PROTO(1) + SPORT(2) +
    DPORT(2), matching ask_fe_build_key_v6() (ASK_FE_KEY_SIZE_V6=38). A 37-byte
    table can NEVER byte-match the 38-byte record the OOT builder emits — the
    exact zero-HIT class F-188 fixed for v4.
  * EKFC is 0x801C0006 (PORT_ID bit 31 set), identical to v4; silicon sizes
    IPSRC1/IPDST1 from the parse result (v4=4B, v6=16B). Extraction order is the
    same MSB-first: PORT_ID -> SIP -> DIP -> PROTO -> SPORT -> DPORT.

SCOPE — DORMANT PLUMBING ONLY. This fixup allocates the second (table index 1)
ehash table so v6 flow records have somewhere to live and the data structures
line up. It deliberately does NOT arm a second KG scheme: dual-scheme per-port
selection (kgse_mv against the parser v4/v6 LCV, with NIA_KG_DIRECT disabled) is
an unresolved silicon question (see T-M6-1 Phase 3 in ASK2-MASTER-PLAN and the
S0 qdrant gate). Arming a second, unselectable scheme with mv=0 while the live
port runs KG-direct would be dead state at best and could perturb the proven v4
dispatch at worst. Until the LCV/kgse_mv experiment lands, v6 flows fail to
software (ask_hw_flow_preflight returns -EOPNOTSUPP for v6 when the v6 path is
not enabled), so this table stays allocated-but-unreferenced and cannot affect
the v4 bytes or the v4 scheme programming.

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

\t/* F-140 (T-M6-1 Phase 1): dormant second ehash table for IPv6.
\t * 38-byte key = PORT_ID|SIP16|DIP16|PROTO|SPORT|DPORT, matching
\t * ask_fe_build_key_v6(); EKFC will be 0x801C0006 when the separately
\t * gated v6 KG scheme is implemented. Table index 1; v4 stays table 0.
\t * The table is intentionally unreferenced until the LCV/kgse_mv
\t * dual-scheme selection experiment passes on silicon.
\t */
\terr = fman_pcd_ehash_table_set(pcd, ehash_mask,
\t\t\t\t       38, ehash_shift);"""
        if "38, ehash_shift" not in src:
            src = src.replace(v4_ehash, v6_ehash_block, 1)
            changes += 1
            print("### F-140: added dormant v6 ehash table (key_size=38)")
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
# Part B: fman_pcd_kg.c — v6 scheme arm/disarm  (DEFERRED to T-M6-1 Phase 3)
# ═══════════════════════════════════════════════════════════════════════
#
# The original F-140 armed a SECOND KG scheme here by grabbing "any free/unbound
# slot" and setting next_engine=CC with mv=0. That is intentionally NOT applied:
#
#   * The live v4 port runs with NIA_KG_DIRECT | scheme_id (F-178), which
#     bypasses the SI/match-vector walk. A second scheme with mv=0 can never be
#     selected under KG-direct, so it is dead state — and worse, an extra armed
#     scheme on the same port risks perturbing the only proven-working v4
#     dispatch.
#   * Correct dual-scheme selection needs the parser IPv4/IPv6 LCV bit driven
#     into each scheme's kgse_mv with KG-direct turned OFF. The exact LCV/mv
#     values for the 210.10.1 microcode are unknown and require a cold-boot
#     silicon experiment (T-M6-1 Phase 3; S0 qdrant gate applies before any
#     kgse_mv / scheme change).
#
# Until that experiment resolves the selection mechanism, the v6 scheme arm is
# left unimplemented and v6 flows fail to software. Only the dormant v6 ehash
# table (Part A) is created. Do NOT re-add a free-slot scheme arm here without
# the LCV/kgse_mv design decision.
print("### F-140: v6 KG scheme arm DEFERRED to T-M6-1 Phase 3 (dispatch unresolved)")

if total_changes:
    print(f"### F-140: {total_changes} total change(s) applied")
else:
    print("### F-140: no changes — may already be present")
    sys.exit(0)