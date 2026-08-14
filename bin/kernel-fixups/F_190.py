"""F-190: write en_exthash_node vendor node to the root AD in fe_enter_build.

CONTEXT (2026-08-14, Phase 3 board session finding):

The fe_arm engage function writes the vendor en_exthash_node to the group
offset (gro), but the CC dispatch reads the root AD (off) — the engage
function's node is never reached. The fe_enter_build function writes a
CONT_LOOKUP group to the root AD, which the CC dispatch interprets as a
group AD with garbled parameters (numKeys=142, matchTableAddr=0x400000,
keySize8=5) instead of the en_exthash_node with the correct ehash table
reference. The ehash table's node descriptor is correct at the DDR address,
but the CC dispatch never reaches it because the root AD is the wrong node
type. Result: the ehash comparator never matches, and the F-189 stats
counters stay at 0 through 2 Gbps of traffic.

The fix: in fman_pcd_fe_enter_build, after writing the CONT_LOOKUP group,
overwrite the root AD with the en_exthash_node when an ehash table exists.
The CC dispatch then reads the correct node type and routes to the ehash
table. The engage function's vendor node write to gro is redundant (harmless).

F-190 (1 block, fman_pcd.c). Anchored on the exact post-F-189 derived
state. Idempotent ("F-190:" markers). CI-only build.
"""

import sys

changes = 0


def edit(path, blocks):
    global changes
    with open(path) as f:
        src = f.read()
    file_changes = 0
    for name, marker, old, new in blocks:
        if marker not in new:
            print(f"### F-190: FATAL: block '{name}' marker {marker} not "
                  "embedded in its replacement text -- fixup bug.")
            sys.exit(1)
        if marker in src:
            print(f"### F-190: {name} already applied")
            continue
        if old not in src:
            print(f"### F-190: FATAL: '{name}' text not found verbatim in "
                  f"{path} -- source drifted. Refusing to guess.")
            sys.exit(1)
        src = src.replace(old, new, 1)
        file_changes += 1
        changes += 1
        print(f"### {path}: F-190 {name} applied")
    if file_changes:
        with open(path, "w") as f:
            f.write(src)


pcd_blocks = [
    ('fe_enter_build vendor node at root AD',
     'F-190(fe-enter-vendor-node)',
     "\tiowrite32be(FMAN_AD_CONT_LOOKUP_TYPE | FMAN_AD_FE_ENTER_ALLOCATE,\n"
     "\t\t    ad + 0);\t\t\t\t/* ccAdBase          */\n"
     "\tiowrite32be(0, ad + 1);\t\t\t\t/* matchTblPtr       */\n"
     "\tiowrite32be(FMAN_AD_FE_ENTER_OPCODE, ad + 2);\t/* pcAndOffsets      */\n"
     "\tiowrite32be((u32)fe_off, ad + 3);\t\t/* gmask = FE offset */\n"
     "\n"
     "\tpcd->fe_root_ad_off = off;\n",
     "\t/* F-190(fe-enter-vendor-node): write the en_exthash_node vendor node\n"
     "\t * to the root AD when an ehash table exists. The fe_arm engage\n"
     "\t * function writes the vendor node to the group allocation (gro), but\n"
     "\t * the CC dispatch reads the root AD (off) -- the engage function's\n"
     "\t * node is never reached (Phase 3 finding, 2026-08-14). The CC\n"
     "\t * dispatch then sees a CONT_LOOKUP group AD with garbled parameters\n"
     "\t * (numKeys=142, matchTableAddr=0x400000) instead of the correct\n"
     "\t * en_exthash_node, the ehash comparator never matches, and the F-189\n"
     "\t * stats counters stay at 0 through 2 Gbps of traffic.\n"
     "\t * By writing the correct node HERE, the CC dispatch reads the\n"
     "\t * en_exthash_node regardless of the engage function's placement.\n"
     "\t * The engage function's gro write is then redundant (harmless).\n"
     "\t */\n"
     "\tiowrite32be(FMAN_AD_CONT_LOOKUP_TYPE | FMAN_AD_FE_ENTER_ALLOCATE,\n"
     "\t\t    ad + 0);\t\t\t\t/* ccAdBase          */\n"
     "\tiowrite32be(0, ad + 1);\t\t\t\t/* matchTblPtr       */\n"
     "\tiowrite32be(FMAN_AD_FE_ENTER_OPCODE, ad + 2);\t/* pcAndOffsets      */\n"
     "\tiowrite32be((u32)fe_off, ad + 3);\t\t/* gmask = FE offset */\n"
     "\t{\n"
     "\t\tstruct fman_pcd_ehash_table *__et =\n"
     "\t\t\tlist_first_entry_or_null(&pcd->fe_ehash_tables,\n"
     "\t\t\t\tstruct fman_pcd_ehash_table, node);\n"
     "\t\tif (__et && pcd->fe_int_buf_off) {\n"
     "\t\t\tu64 __tb = (u64)__et->table_dma;\n"
     "\t\t\t/* en_exthash_node VARIANT B: miss_action=ENQUE, key_size,\n"
     "\t\t\t * table_type=4, hash_shift, table_base_hi, table_base_lo,\n"
     "\t\t\t * int_buf pool, hash_mask_bits. word3 = 0 (patched by\n"
     "\t\t\t * fman_pcd_fe_node_set_miss_nia after scheme-id resolution).\n"
     "\t\t\t */\n"
     "\t\t\tiowrite32be((2U << 30) |\n"
     "\t\t\t\t    ((u32)(__et->key_size & 0x3f) << 24) |\n"
     "\t\t\t\t    (4U << 20) |\n"
     "\t\t\t\t    ((u32)(__et->hash_shift & 0x7) << 16) |\n"
     "\t\t\t\t    ((u32)(__tb >> 32) & 0xffU), ad + 0);\n"
     "\t\t\tiowrite32be((u32)(__tb & 0xffffffffU), ad + 1);\n"
     "\t\t\tiowrite32be(\n"
     "\t\t\t    ((u32)((pcd->fe_int_buf_off >> 8) & 0xffffU) << 16) |\n"
     "\t\t\t    (0x80U << 4) |\n"
     "\t\t\t    (u32)(__et->hash_mask_bits & 0xfU), ad + 2);\n"
     "\t\t\tiowrite32be(0, ad + 3);\n"
     "\t\t}\n"
     "\t}\n"
     "\n"
     "\tpcd->fe_root_ad_off = off;\n"),
]
edit("drivers/net/ethernet/freescale/fman/fman_pcd.c", pcd_blocks)

if changes:
    print(f"### F-190 complete ({changes} blocks)")
else:
    print("### F-190 no changes applied")
    sys.exit(1)
