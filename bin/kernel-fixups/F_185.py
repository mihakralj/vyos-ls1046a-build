"""F-185: vendor-faithful CC dispatch — AC_CC mode + VARIANT B
en_exthash_node at RCCB (the NXP ASK SDK production path).

CONTEXT (2026-08-12, E23 / decomp/findings.md): Ghidra re-analysis decoded
the 210.10.1 CC dispatch. The single AD-type extraction site (c600001e >>30
@ w1857, br_tbl[0xf000]) routes type-1 (CONT_LOOKUP species) into the
ENHANCED EXTERNAL-HASH MACHINE, which parses the 16B AD at RCCB+CCOBASE*16
as an en_exthash_node VARIANT B:

    word0: table_base_hi:8[7:0] | ipv4_ad_offset:8[15:8] |
           hash_bytes_offset:3[18:16] | rsv:1[19] | table_type:4[23:20] |
           key_size:6[29:24] | miss_action_type:2[31:30]
    word1: table_base_lo (DDR bucket-array bus address)
    word2: hash_mask_bits:4[3:0] | global_mem_offset:12[15:4] |
           int_buf_pool_addr:16[31:16]
    word3: miss NIA

Field widths proven by the microcode's own extraction census (w1711
AND-0x3f, w1610 AND-0xff, w1598 AND-0xf, w1557 >>16; table_base_lo staged
to dmem[0xe000] at w2045/2049; bucket_index w1928 reads the KG hash from
ctx[0xd048]). The .106 production row (tcp4: 4e400008 eb700100 0402080f
00480308) decodes VARIANT B four independent ways, incl. word3 =
NIA_ENG_KG|NIA_KG_CC_EN|NIA_KG_DIRECT|scheme (byte-exact the SDK
fm_ehash.c miss-NIA encoding) and word1 = the probed DDR bucket array.

ROOT CAUSE of the F-183 failure (frames consumed, no canary, no delivery):
F-183's RM-8.7.4.1 group AD (w0=0x00056e00, w1=ato, w2=0x4F000000, w3=0)
parses as a garbage node — miss_action_type=0 (DONE: terminate with no
disposition), key_size=0, table_base=0x56f00 (a MURAM offset misread as a
DDR physical address), mask_bits=0, pool=0x4F0000 (out of range). The
machine terminates every frame with no action. There is no match-table
walker in this blob; only the external-hash machine.

The historical AC_CC stalls (0118 iter-48, Path A 08-10, E20) are
invalid-CONTENT stalls, not invalid-MODE stalls: a bare FE_ENTER at RCCB
parses as a node with table_base=0, pool=0 -> the machine waits forever on
a pool-0 workspace allocation = the silent-WAIT signature. .106 runs AC_CC
(0x8x000006, ccbs=0) on this identical blob in production.

CHANGES (6 blocks, 3 files):
  1. fman_pcd.c engage scaffold: write the VARIANT B node at gro (needs the
     fe_ehash table + fe_int_buf pool; falls back to F-183's numKeys=0
     miss-enq group when either is absent, preserving the production
     pass-through). word3 = 0 placeholder (block 5 patches it).
  2. fman_pcd.c: new fman_pcd_fe_node_set_miss_nia() — writes word3 of the
     node (no-op unless the node form was built).
  3. fman_pcd_internal.h: declare it.
  4. fman_pcd_kg.c arm_fe: next_engine 2 -> 3 (AC_CC, mode 0x80000006,
     CCOBASE=0), cc_bits_sel = 0 (vendor: ccbs=0 in AC_CC mode).
  5. fman_pcd_kg.c arm_fe: commit the miss NIA
     (NIA_ENG_KG|NIA_KG_CC_EN|NIA_KG_DIRECT|scheme_id, byte-exact the
     .106 0x0048030x encoding) before fman_port_set_cc_base() asserts
     EXTC SYNC.
  6. fman_pcd.c: ENGAGED dmesg text.

HIT = the DDR entry's opcode script ENQUEUE_PKT -> param.fqid (F-181/F-182
records, already vendor-faithful). MISS = word3 NIA -> KG-direct re-entry
with CC_EN -> scheme fqb enqueue -> kernel. Teardown unchanged
(detach_cc -> next_engine=0, ccbs word3 cleared via F-051 zeroing;
set_cc_base(0) clears RCCB + rfpne CC_EN; scaffold freed).

Anchored on the exact derived state (F-183 outputs). Idempotent
(per-block "F-185:" markers). CI-only build.
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
            print(f"### F-185: FATAL: block '{name}' marker {marker} not "
                  "embedded in its replacement text -- fixup bug.")
            sys.exit(1)
        if marker in src:
            print(f"### F-185: {name} already applied")
            continue
        if old not in src:
            print(f"### F-185: FATAL: '{name}' text not found verbatim in "
                  f"{path} -- source drifted. Refusing to guess.")
            sys.exit(1)
        src = src.replace(old, new, 1)
        file_changes += 1
        changes += 1
        print(f"### {path}: F-185 {name} applied")
    if file_changes:
        with open(path, "w") as f:
            f.write(src)


# -- fman_pcd.c -------------------------------------------------------------
pcd_blocks = [
    ('engage vendor-node scaffold',
     'F-185(vendor-node)',
     "/* F-183(group-root-miss-slot): Delta-1 dispatch -- the\n\t\t\t\t * group ALWAYS ships numKeys=0. With an explicit\n\t\t\t\t * FE_ENTER target, a verbatim copy of the caller's\n\t\t\t\t * FE_ENTER AD sits in the MISS slot (ato[0]): every\n\t\t\t\t * frame -> FE_ENTER -> the ehash decides HIT/MISS.\n\t\t\t\t * The CC comparator is proven INSENSITIVE to match\n\t\t\t\t * rows (5 negative variants, 2026-08-10), so a\n\t\t\t\t * match-leaf FE_ENTER is unreachable -- the miss\n\t\t\t\t * slot is the only dispatch the walker honors. Bare\n\t\t\t\t * FE_ENTER-at-RCCB (the old off!=0 form) STALLS the\n\t\t\t\t * port on the first dispatched frame (E20).\n\t\t\t\t */\n\t\t\t\tiowrite32be((0U << 24) | (mto & 0xFFFFFF), c + 0);\n\t\t\t\tiowrite32be((ato & 0xFFFFFF), c + 4);\n\t\t\t\tiowrite32be(0x4F000000, c + 8);\n\t\t\t\tiowrite32be(0, c + 12);\n\t\t\t\tc = (void __iomem *)\n\t\t\t\t\t(void *)fman_muram_offset_to_vbase(muram, ato);\n\t\t\t\tif (fe_enter_off) {\n\t\t\t\t\tvoid __iomem *fe = (void __iomem *)\n\t\t\t\t\t\t(void *)fman_muram_offset_to_vbase(muram, fe_enter_off);\n\t\t\t\t\t/* miss slot = verbatim copy of the caller's FE_ENTER AD */\n\t\t\t\t\tiowrite32be(ioread32be(fe + 0), c + 0);\n\t\t\t\t\tiowrite32be(ioread32be(fe + 4), c + 4);\n\t\t\t\t\tiowrite32be(ioread32be(fe + 8), c + 8);\n\t\t\t\t\tiowrite32be(ioread32be(fe + 12), c + 12);\n\t\t\t\t\t/* ato[1] (unused at numKeys=0): park a kernel-delivery enq-AD */\n\t\t\t\t\tiowrite32be((u32)miss_fqid, c + 16);\n\t\t\t\t\tiowrite32be(0, c + 20);\n\t\t\t\t\tiowrite32be(0, c + 24);\n\t\t\t\t\tiowrite32be(0, c + 28);\n\t\t\t\t} else {\n\t\t\t\t\tiowrite32be((u32)miss_fqid, c + 0);\n\t\t\t\t\tiowrite32be(0, c + 4);\n\t\t\t\t\tiowrite32be(0, c + 8);\n\t\t\t\t\tiowrite32be(0, c + 12);\n\t\t\t\t\tiowrite32be((u32)miss_fqid, c + 16);\n\t\t\t\t\tiowrite32be(0, c + 20);\n\t\t\t\t\tiowrite32be(0, c + 24);\n\t\t\t\t\tiowrite32be(0, c + 28);\n\t\t\t\t}\n\t\t\t\t/* F-183: RCCB always points at the group. F-165's\n\t\t\t\t * bare-FE_ENTER-direct form for off!=0 is the\n\t\t\t\t * stalling topology -- the group wrapper supersedes\n\t\t\t\t * it (the scaffold is tracked for cleanup either way).\n\t\t\t\t */\n\t\t\t\tfe_enter_off = gro;\n",
     "\t\t\t\t/* F-185(vendor-node): the 210.10.1 CC engine's CONT_LOOKUP\n\t\t\t\t * path is the enhanced external-hash machine -- it parses\n\t\t\t\t * the 16B AD at RCCB+CCOBASE*16 as an en_exthash_node\n\t\t\t\t * VARIANT B (Ghidra decode E23, 2026-08-12; .106 row\n\t\t\t\t * confirmed 4 ways). F-183's RM-8.7.4.1 group AD parses\n\t\t\t\t * as garbage-as-node (miss_action DONE, keysize 0,\n\t\t\t\t * table_base = MURAM-off-as-DDR, pool out of range) ->\n\t\t\t\t * frames terminated with no disposition (E23 root\n\t\t\t\t * cause). Write the vendor node: HIT = the DDR entry's\n\t\t\t\t * opcode script ENQUEUE_PKT -> param.fqid (F-181/F-182\n\t\t\t\t * records); MISS = word3 NIA, patched by arm_fe\n\t\t\t\t * (KG-direct|CC_EN|scheme, byte-exact the .106\n\t\t\t\t * 0x0048030x encoding). Needs the fe_ehash table +\n\t\t\t\t * fe_int_buf pool; without them fall back to the\n\t\t\t\t * numKeys=0 miss-enq group (production pass-through).\n\t\t\t\t */\n\t\t\t\t{\n\t\t\t\t\tstruct fman_pcd_ehash_table *et =\n\t\t\t\t\t\tlist_first_entry_or_null(&pcd->fe_ehash_tables,\n\t\t\t\t\t\t\tstruct fman_pcd_ehash_table, node);\n\n\t\t\t\t\tif (et && pcd->fe_int_buf_off) {\n\t\t\t\t\t\tu64 tb = (u64)et->table_dma;\n\n\t\t\t\t\t\tiowrite32be((1U << 30) |\n\t\t\t\t\t\t\t    ((u32)(et->key_size & 0x3f) << 24) |\n\t\t\t\t\t\t\t    (4U << 20) |\n\t\t\t\t\t\t\t    ((u32)(et->hash_shift & 0x7) << 16) |\n\t\t\t\t\t\t\t    ((u32)(tb >> 32) & 0xffU), c + 0);\n\t\t\t\t\t\tiowrite32be((u32)(tb & 0xffffffffU), c + 4);\n\t\t\t\t\t\tiowrite32be(((u32)((pcd->fe_int_buf_off >> 8) &\n\t\t\t\t\t\t\t\t   0xffffU) << 16) |\n\t\t\t\t\t\t\t    (0x80U << 4) |\n\t\t\t\t\t\t\t    (u32)(et->hash_mask_bits & 0xfU),\n\t\t\t\t\t\t\t    c + 8);\n\t\t\t\t\t\t/* word3 = miss NIA: patched by arm_fe via\n\t\t\t\t\t\t * fman_pcd_fe_node_set_miss_nia() before the\n\t\t\t\t\t\t * EXTC SYNC in fman_port_set_cc_base().\n\t\t\t\t\t\t */\n\t\t\t\t\t\tiowrite32be(0, c + 12);\n\t\t\t\t\t} else {\n\t\t\t\t\t\tiowrite32be((0U << 24) | (mto & 0xFFFFFF), c + 0);\n\t\t\t\t\t\tiowrite32be((ato & 0xFFFFFF), c + 4);\n\t\t\t\t\t\tiowrite32be(0x4F000000, c + 8);\n\t\t\t\t\t\tiowrite32be(0, c + 12);\n\t\t\t\t\t\tc = (void __iomem *)\n\t\t\t\t\t\t\t(void *)fman_muram_offset_to_vbase(muram, ato);\n\t\t\t\t\t\tiowrite32be((u32)miss_fqid, c + 0);\n\t\t\t\t\t\tiowrite32be(0, c + 4);\n\t\t\t\t\t\tiowrite32be(0, c + 8);\n\t\t\t\t\t\tiowrite32be(0, c + 12);\n\t\t\t\t\t}\n\t\t\t\t}\n\t\t\t\t/* F-185: RCCB always points at gro (node or group). */\n\t\t\t\tfe_enter_off = gro;\n"),
    ("ENGAGED dmesg text",
     "F-185(engaged-dmesg)",
     '\t/* F-183(engaged-dmesg): this is the CCBS-graft model, not AC_CC. */\n\tpr_info("fman_pcd fe_arm: port 0x%02x ENGAGED (CCBS-graft group-root)\\n", port_id);\n',
     '\t/* F-185(engaged-dmesg): vendor AC_CC + VARIANT B node model. */\n\tpr_info("fman_pcd fe_arm: port 0x%02x ENGAGED (AC_CC vendor-node)\\n", port_id);\n'),
    ("miss-nia setter",
     "F-185(miss-nia-setter)",
     '/* Debugfs wrapper — parse string, delegate to __fman_pcd_fe_arm_engage(). */\n',
     '/* F-185(miss-nia-setter): write word3 (miss NIA) of the vendor node\n * at node_off. No-op unless the node form was built (ehash table\n * present) -- the fallback RM group needs word3 = 0. Called from\n * arm_fe after scheme-id resolution, before fman_port_set_cc_base()\n * asserts EXTC SYNC.\n */\nvoid fman_pcd_fe_node_set_miss_nia(struct fman_pcd *pcd, u32 node_off,\n\t\t\t\t   u32 nia)\n{\n\tstruct muram_info *muram = fman_get_muram(pcd->fman);\n\tvoid __iomem *nd;\n\n\tif (!muram || !node_off || list_empty(&pcd->fe_ehash_tables))\n\t\treturn;\n\tnd = (void __iomem *)(void *)\n\t\tfman_muram_offset_to_vbase(muram, node_off);\n\tiowrite32be(nia, nd + 12);\n}\n\n/* Debugfs wrapper — parse string, delegate to __fman_pcd_fe_arm_engage(). */\n'),
]
edit("drivers/net/ethernet/freescale/fman/fman_pcd.c", pcd_blocks)

# -- fman_pcd_internal.h ------------------------------------------------------
ih_blocks = [
    ("internal header declaration",
     "F-185(miss-nia-decl)",
     'struct list_head *fman_pcd_get_kg_list(struct fman_pcd *pcd);\n',
     'struct list_head *fman_pcd_get_kg_list(struct fman_pcd *pcd);\n/* F-185(miss-nia-decl): vendor-node miss-NIA commit (arm_fe path). */\nvoid fman_pcd_fe_node_set_miss_nia(struct fman_pcd *pcd, u32 node_off,\n\t\t\t\t   u32 nia);\n'),
]
edit("drivers/net/ethernet/freescale/fman/fman_pcd_internal.h", ih_blocks)

# -- fman_pcd_kg.c -------------------------------------------------------------
pkg_blocks = [
    ('arm_fe AC_CC dispatch',
     'F-185(arm-fe-accc)',
     '/*\n\t * F-183(arm-fe-ccbs-implicit): the ONLY dispatch form with every\n\t * element individually proven non-stalling on .185 (E20,\n\t * 2026-08-12): KGSE_MODE stays EN|ENQUEUE_KG_DFLT_NIA (0x80500002,\n\t * next_engine=2 branch), KGSE_CCBS carries the group-table MURAM\n\t * offset (fe_enter_off -- repointed at the scaffold group by\n\t * F-183-E), written to scheme window WORD 3 by the F-183\n\t * keygen_scheme_setup fix. AC_CC mode (next_engine=3, 0x80000006)\n\t * STALLS port 0x11 on the first dispatched frame on .185 mainline\n\t * (E20 replaying 0118 iter-48 + Path A 08-10). The CCBS word-3\n\t * graft is the form that fired the walk with 1:1 miss-row delivery\n\t * (F-184 session) and the form that carried 24M+ frames on ask20.\n\t */\n\tslot->next_engine    = 2;\n\tslot->cc_base_offset = 0;\n\tslot->cc_bits_sel    = fe_enter_off & 0x00FFFFFF;\n',
     "\t/*\n\t * F-185(arm-fe-accc): vendor dispatch mode (NXP ASK SDK production\n\t * path, .106: 12 schemes 0x8x000006, ccbs=0, 0% loss under load).\n\t * E23 (2026-08-12) decoded the 210.10.1 CC dispatch: the\n\t * CONT_LOOKUP path is the enhanced external-hash machine parsing\n\t * the RCCB-target AD as an en_exthash_node VARIANT B -- F-185's\n\t * engage scaffold writes it. F-183's CCBS-implicit + RM-group-AD\n\t * form feeds the machine garbage (frames consumed, no\n\t * disposition). The historical AC_CC stalls (0118 iter-48,\n\t * Path A 08-10) were invalid-CONTENT stalls (bare FE_ENTER =\n\t * node with table_base=0, pool=0 -> pool-0 wait), now fixed by\n\t * the node. Vendor: EN|NIA_ENG_FM_CTL|AC_CC, CCOBASE=0, ccbs=0.\n\t */\n\tslot->next_engine    = 3;\n\tslot->cc_base_offset = 0;\n\tslot->cc_bits_sel    = 0;\n"),
    ('miss-nia commit',
     'F-185(miss-nia-commit)',
     '\tmutex_unlock(lock);\n\n\trxport = fman_port_lookup_rx(fman, hw_port_id);\n',
     '\tmutex_unlock(lock);\n\n\t/* F-185(miss-nia-commit): complete the vendor node before it goes\n\t * live -- word3 = NIA_ENG_KG|NIA_KG_CC_EN|NIA_KG_DIRECT|scheme\n\t * (byte-exact the .106 0x0048030x encoding): MISS -> KG-direct\n\t * re-entry with CC_EN (no re-classify loop) -> scheme fqb\n\t * enqueue -> kernel. No-op unless engage built the node form.\n\t * Lands before fman_port_set_cc_base() asserts EXTC SYNC.\n\t */\n\tfman_pcd_fe_node_set_miss_nia(pcd, fe_enter_off,\n\t\t\t\t      0x00480000U | 0x00000200U |\n\t\t\t\t      0x00000100U | (u32)id);\n\n\trxport = fman_port_lookup_rx(fman, hw_port_id);\n'),
]
edit("drivers/net/ethernet/freescale/fman/fman_pcd_kg.c", pkg_blocks)

if changes:
    print(f"### F-185 complete ({changes} blocks)")
else:
    print("### F-185 no changes applied")
    sys.exit(1)
