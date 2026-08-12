"""F-186: correct the F-185 node miss-action to the E25-verified form.

CONTEXT (2026-08-12, E25 / decomp/experiments.md, live /dev/mem validated
on .185, 6.18.44-vyos, CI run 31634513313):

The F-185 node shipped with miss_action_type = NIA (word0 bits[31:30] =
0b01) and word3 = the KG-direct miss NIA
(NIA_ENG_KG|NIA_KG_CC_EN|NIA_KG_DIRECT|scheme). E24/E25 proved that form
is FATAL on the 210.10.1 blob:

  1. MISS on an empty bucket -> word3 NIA -> full KG re-classification
     into the AC_CC scheme -> node -> MISS -> NIA -> INFINITE LOOP
     (~4.5M classifications/sec sustained, no hop limit, no stall).
  2. KG-direct to a FOREIGN scheme instead -> FM_FD_ERR_NO_SCHEME
     (0x00004000) -> port error FQ (refqid 0x291) -> dmesg
     "Err FD status = 0x00004000".

The correct miss form (999 patch ExternalHashTableSet e_FM_PCD_DONE,
EN_EHASH_MISS_ACTION_ENQUE = 2, and E25 live-verified):
  word0 bits[31:30] = 0b10 (ENQUE), word3 = fqid (the nia/fqid union)
  -> DIRECT ENQUEUE, no KG, no re-entry, loop-free. E25: ENQUE + the
  frame's OWN-port fqb (0x300 for eth4) delivers to the kernel (RST,
  curl rc=7, spc stable at 2 for 2 SYNs).

E25 also proved the miss fqid MUST be the frame's own-port fqb: enqueuing
to a CROSS-port fqb (0x200/eth3) delivers to eth3's FQ but the dpaa
driver drops it (rx_dropped++; the FD's buffer belongs to the frame's own
BM pool, so eth3 cannot release it).

F-186 (2 blocks, 2 files):
  1. fman_pcd.c engage scaffold: node word0 bits[31:30] = 0b10 (ENQUE)
     instead of 0b01 (NIA). The F-183 fallback group form is untouched.
  2. fman_pcd_kg.c arm_fe: capture slot->base_fqid under the lock
     (own-port fqb, e.g. 0x300 for eth4) and commit it as word3 via
     fman_pcd_fe_node_set_miss_nia() instead of the KG-direct NIA.

bpid intentionally NOT changed (stays 0 in the record): bpid=0 to the
own-port fqb was never cleanly tested in E25 (the only bpid=1 HIT runs
were live-patched); whether the machine uses the record's param.bpid for
the ENQUEUE_PKT path is an open single-variable test in the E26 matrix.
If that test shows bpid matters, it becomes F-187.

HIT path unchanged (opcode script ENQUEUE_PKT -> param.fqid, F-181/F-182
records, verified delivering in E25). Anchored on the exact F-185
derived output. Idempotent (per-block "F-186:" markers). CI-only build.
"""

import sys

changes = 0


def edit(path, blocks):
    """blocks: list of (name, marker, old, new). The marker string MUST
    appear in new -- it is the per-block idempotency token."""
    global changes
    with open(path) as f:
        src = f.read()
    file_changes = 0
    for name, marker, old, new in blocks:
        if marker not in new:
            print(f"### F-186: FATAL: block '{name}' marker {marker} not "
                  "embedded in its replacement text -- fixup bug.")
            sys.exit(1)
        if marker in src:
            print(f"### F-186: {name} already applied")
            continue
        if old not in src:
            print(f"### F-186: FATAL: '{name}' text not found verbatim in "
                  f"{path} -- source drifted. Refusing to guess.")
            sys.exit(1)
        src = src.replace(old, new, 1)
        file_changes += 1
        changes += 1
        print(f"### {path}: F-186 {name} applied")
    if file_changes:
        with open(path, "w") as f:
            f.write(src)


# -- fman_pcd.c -------------------------------------------------------------
pcd_blocks = [
    ('engage node ENQUE miss form',
     'F-186(enque-miss)',
     "\t\t\t\t\t\tiowrite32be((1U << 30) |\n"
     "\t\t\t\t\t\t\t    ((u32)(et->key_size & 0x3f) << 24) |\n"
     "\t\t\t\t\t\t\t    (4U << 20) |\n"
     "\t\t\t\t\t\t\t    ((u32)(et->hash_shift & 0x7) << 16) |\n"
     "\t\t\t\t\t\t\t    ((u32)(tb >> 32) & 0xffU), c + 0);",
     "\t\t\t\t\t\t/* F-186(enque-miss): word0 bits[31:30] = 0b10 =\n"
     "\t\t\t\t\t\t * EN_EHASH_MISS_ACTION_ENQUE (E25, 2026-08-12): word3\n"
     "\t\t\t\t\t\t * is then an fqid and the miss is a DIRECT enqueue --\n"
     "\t\t\t\t\t\t * loop-free. F-185's 0b01 (NIA) made word3 a\n"
     "\t\t\t\t\t\t * KG-direct NIA which INFINITELY LOOPS on an empty\n"
     "\t\t\t\t\t\t * bucket on 210.10.1 (~4.5M class/sec, no hop limit,\n"
     "\t\t\t\t\t\t * no stall; KG-direct to a foreign scheme instead =\n"
     "\t\t\t\t\t\t * FM_FD_ERR_NO_SCHEME 0x00004000 -> error FQ). The\n"
     "\t\t\t\t\t\t * fallback RM group form is untouched (not a node).\n"
     "\t\t\t\t\t\t */\n"
     "\t\t\t\t\t\tiowrite32be((2U << 30) |\n"
     "\t\t\t\t\t\t\t    ((u32)(et->key_size & 0x3f) << 24) |\n"
     "\t\t\t\t\t\t\t    (4U << 20) |\n"
     "\t\t\t\t\t\t\t    ((u32)(et->hash_shift & 0x7) << 16) |\n"
     "\t\t\t\t\t\t\t    ((u32)(tb >> 32) & 0xffU), c + 0);"),
]
edit("drivers/net/ethernet/freescale/fman/fman_pcd.c", pcd_blocks)

# -- fman_pcd_kg.c ------------------------------------------------------------
pkg_blocks = [
    ('arm_fe capture own-port fqb',
     'F-186(miss-fqid-capture)',
     "\tslot->next_engine    = 3;\n"
     "\tslot->cc_base_offset = 0;\n"
     "\tslot->cc_bits_sel    = 0;\n"
     "\tslot->used = false;",
     "\tslot->next_engine    = 3;\n"
     "\tslot->cc_base_offset = 0;\n"
     "\tslot->cc_bits_sel    = 0;\n"
     "\t/* F-186(miss-fqid-capture): the node word3 miss action is a\n"
     "\t * direct ENQUE to the scheme's OWN base FQID (E25: cross-port\n"
     "\t * fqb enqueues drop in the dpaa driver -- the FD's buffer\n"
     "\t * belongs to the frame's own BM pool). Capture under the lock;\n"
     "\t * committed after mutex_unlock, before the EXTC SYNC.\n"
     "\t */\n"
     "\tu32 miss_fqid = slot->base_fqid;\n"
     "\tslot->used = false;"),
    ('arm_fe commit miss fqid',
     'F-186(miss-fqid-commit)',
     "\t/* F-185(miss-nia-commit): complete the vendor node before it goes\n"
     "\t * live -- word3 = NIA_ENG_KG|NIA_KG_CC_EN|NIA_KG_DIRECT|scheme\n"
     "\t * (byte-exact the .106 0x0048030x encoding): MISS -> KG-direct\n"
     "\t * re-entry with CC_EN (no re-classify loop) -> scheme fqb\n"
     "\t * enqueue -> kernel. No-op unless engage built the node form.\n"
     "\t * Lands before fman_port_set_cc_base() asserts EXTC SYNC.\n"
     "\t */\n"
     "\tfman_pcd_fe_node_set_miss_nia(pcd, fe_enter_off,\n"
     "\t\t\t\t      0x00480000U | 0x00000200U |\n"
     "\t\t\t\t      0x00000100U | (u32)id);",
     "\t/* F-186(miss-fqid-commit): complete the vendor node before it\n"
     "\t * goes live -- with miss_action_type = ENQUE (F-186(enque-miss)),\n"
     "\t * word3 is the miss FQID: a direct enqueue to the scheme's own\n"
     "\t * base FQID (slot->base_fqid, e.g. 0x300 for eth4). E25 proved\n"
     "\t * the F-185 KG-direct NIA form loops forever on 210.10.1 and\n"
     "\t * cross-port fqb enqueues drop in the dpaa driver. No-op unless\n"
     "\t * engage built the node form. Lands before\n"
     "\t * fman_port_set_cc_base() asserts EXTC SYNC.\n"
     "\t */\n"
     "\tfman_pcd_fe_node_set_miss_nia(pcd, fe_enter_off, miss_fqid);"),
]
edit("drivers/net/ethernet/freescale/fman/fman_pcd_kg.c", pkg_blocks)

if changes:
    print(f"### F-186 complete ({changes} blocks)")
else:
    print("### F-186 no changes applied")
    sys.exit(1)
