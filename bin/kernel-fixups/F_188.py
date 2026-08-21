"""F-188: align the production genl/flowtable path with the E25/E26-verified
14-byte ehash mechanism.

CONTEXT (2026-08-12, P0-2 production-path audit; decomp/experiments.md E28):

The M3 gate (E25/E26) verified the working mechanism end-to-end on .185:
EKFC 0x801C0006 (PORT_ID|SIP|DIP|PROTO|SPORT|DPORT = 14 bytes), PORT_ID =
0x00 (the dv0/dv1 zeroed default), record keys byte-0 = 0x00, and the
record's target FQID = the frame's OWN-port RX FQID (0x300 for eth4;
cross-port enqueues drop in the dpaa driver).

The production path (ask.ko genl engage + nft flowtable -> FLOW_CLS_REPLACE
-> fman_pcd_fe_flow_add) still carried three stale bits that guarantee NO
production HIT:

  1. __fman_pcd_fe_build_vm_chain() created the ehash table with
     ehash_key_sz = 13, and fman_pcd_fe_engage() armed the scheme with
     EKFC 0x001C0006 (13-byte, no PORT_ID) -- internally consistent with
     each other, but the flow records are 14-byte (ASK_FE_KEY_SIZE = 14,
     F-163): comparator byte-count and bucket index can never agree.
     Fix: ehash_key_sz = 14 + EKFC 0x801C0006 (the E25/E26-verified form).

  2. ask_fe_build_key() (ask_flow_offload.c, OOT source -- fixed by a
     direct edit, not this fixup) wrote k[0] = key->port_id = the FMan hw
     port id (0x11 for eth4). The silicon's PORT_ID extraction reads the
     zeroed dv default = 0x00 (E25/E26 brute-force confirmed). Fix: k[0]
     = 0x00.

  3. fman_pcd_fe_flow_add() wrote param.fqid = action->enq_off, and the
     ask path passed ask_hw_get_enq_fe_off() = the ENQ FE's MURAM offset
     (fman_pcd_fe_enq_get_offset returns obj->muram_off) -- an invalid
     FQID. Fix: use the port's own RX FQID via the same
     fman_pcd_resolve_miss_fqid() the miss path uses (params-page default,
     0x300 for eth4).

The genl engage itself (ask_hw -> fman_pcd_fe_engage) already reaches the
F-185/F-186 arm and produced the correct ENQUE node with own-port miss fqid
(verified live: RCCB 8d400000 fa180000 04c7080f 00000300, dv0/dv1=0) -- only
the three above were stale. The nft flowtable offload was additionally found
blocked on this board by passive conntrack (nf_conntrack_count stays 0), so
production-path HIT validation must drive ask_fe_flow_insert via a working
flowtable (conntrack-active) or a REPLACE-unit test -- separate item.

F-188 (4 blocks, fman_pcd.c). Anchored on the exact post-F-187 derived
state. Idempotent ("F-188:" markers). CI-only build.
"""

import sys

changes = 0


def edit(path, blocks):
    """blocks: list of (name, marker, old, new) or
    (name, marker, old, new, optional). The marker string MUST appear in new
    -- it is the per-block idempotency token. optional=True blocks skip (not
    FATAL) when their anchor is absent."""
    global changes
    with open(path) as f:
        src = f.read()
    file_changes = 0
    for blk in blocks:
        name, marker, old, new = blk[0], blk[1], blk[2], blk[3]
        optional = len(blk) > 4 and blk[4]
        if marker not in new:
            print(f"### F-188: FATAL: block '{name}' marker {marker} not "
                  "embedded in its replacement text -- fixup bug.")
            sys.exit(1)
        if marker in src:
            print(f"### F-188: {name} already applied")
            continue
        if old not in src:
            if optional:
                print(f"### F-188: {name} anchor absent -- SKIP (optional; "
                      "F-140 now ships the corrected v6 comment directly).")
                continue
            print(f"### F-188: FATAL: '{name}' text not found verbatim in "
                  f"{path} -- source drifted. Refusing to guess.")
            sys.exit(1)
        src = src.replace(old, new, 1)
        file_changes += 1
        changes += 1
        print(f"### {path}: F-188 {name} applied")
    if file_changes:
        with open(path, "w") as f:
            f.write(src)


# -- fman_pcd.c -------------------------------------------------------------
pcd_blocks = [
    ('vm-chain ehash key_size 14',
     'F-188(prod-key-sz)',
     "\tconst u8  ehash_key_sz  = 13;\n"
     "\tconst u8  ehash_shift   = 0;\n",
     "\t/* F-188(prod-key-sz): 14-byte PORT_ID form (E25/E26-verified: EKFC\n"
     "\t * 0x801C0006, PORT_ID=0x00 from the zeroed dv default, records with\n"
     "\t * byte-0 = 0x00). The previous 13-byte key_size matched EKFC\n"
     "\t * 0x001C0006 but not the 14-byte ASK_FE_KEY_SIZE records, so the\n"
     "\t * comparator byte-count and bucket index could never agree.\n"
     "\t */\n"
     "\tconst u8  ehash_key_sz  = 14;\n"
     "\tconst u8  ehash_shift   = 0;\n"),
    # OPTIONAL: F-140 (T-M6-1 Phase 1, 2026-08-19) was rewritten to ship the
    # correct 38-byte / EKFC 0x801C0006 v6 comment directly, so the old
    # "Same EKFC (0x001C0006)" line no longer exists to patch. Skip when absent.
    ('v6 table comment EKFC',
     'F-188(v6-comment)',
     "\t * Same EKFC (0x001C0006) \u2014 silicon determines field size from parse result.\n",
     "\t * F-188(v6-comment): same EKFC (0x801C0006) \u2014 silicon determines field size from parse result.\n",
     True),
    ('fe_engage EKFC 14-byte',
     'F-188(prod-ekfc)',
     "\t\terr = __fman_pcd_fe_arm_engage(pcd, hw_port_id, 0, miss_fqid, 0x001C0006);\n",
     "\t\t/* F-188(prod-ekfc): PORT_ID|5-tuple 14-byte extraction, the\n"
     "\t\t * E25/E26-verified form (PORT_ID = 0x00, zeroed dv default).\n"
     "\t\t */\n"
     "\t\terr = __fman_pcd_fe_arm_engage(pcd, hw_port_id, 0, miss_fqid, 0x801C0006);\n"),
    ('flow-add target own-port FQID',
     'F-188(prod-flow-target)',
     "\t\tint err = fman_pcd_ehash_add_key(t, action->key, action->key_size,\n"
     "\t\t\t\t\t\t (u32)enq_obj->muram_off,\n"
     "\t\t\t\t\t\t (u32)action->enq_off);\n",
     "\t\t/* F-188(prod-flow-target): the record's target FQID must be the\n"
     "\t\t * frame's OWN-port RX FQID (E25: cross-port enqueues drop in the\n"
     "\t\t * dpaa driver; the ask path was passing the ENQ FE's MURAM\n"
     "\t\t * offset via action->enq_off -- an invalid FQID). Resolve it\n"
     "\t\t * from the port, same source the miss path uses.\n"
     "\t\t */\n"
     "\t\tint err = fman_pcd_ehash_add_key(t, action->key, action->key_size,\n"
     "\t\t\t\t\t\t (u32)enq_obj->muram_off,\n"
     "\t\t\t\t\t\t fman_pcd_resolve_miss_fqid(pcd, hw_port_id));\n"),
]
edit("drivers/net/ethernet/freescale/fman/fman_pcd.c", pcd_blocks)

if changes:
    print(f"### F-188 complete ({changes} blocks)")
else:
    print("### F-188 no changes applied")
    sys.exit(1)
