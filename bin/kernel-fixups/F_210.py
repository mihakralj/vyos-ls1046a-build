"""F-210 (T-M6-1 IPv6 productization, step 2): dual-node engage writer + the
default-OFF `fman_pcd.v6_enable` module-param gate that all v6 productization
code (F-210/F-211/F-212) keys off.

WHAT THIS DOES
--------------
The FMan CC dispatch reads the en_exthash_node at `FMBM_RCCB + CCOBASE*16`
(RCCB = gro; F-185 writes table0's node at gro+0, CCOBASE 0). F-209 lets a
scheme carry CCOBASE=1; the silicon proof (2026-08-19, eth1 sandbox, exp-ccobase
in bin/kg-lcv-probe.py) showed that writing table1's en_exthash_node at gro+16
and pointing an IPv6 scheme (CCOBASE=1) at it delivered a clean IPv6 HIT into
table1 (pkt_count 0->3, pkt_bytes 282) while table0 (v4, CCOBASE 0) was
untouched. This fixup reproduces the exp-ccobase node write inside the
production engage scaffold: when v6 is enabled AND a second (38-byte, table
index 1) ehash table exists (F-140 allocates it), write its VARIANT-B node at
`c + 16` (gro+16) with the SAME encoding F-185 uses for table0, so the v6
scheme's AC_CC dispatch reaches table1's node. word3 (miss NIA) is left 0 here
and patched by F-211's v6 arm (own-port miss FQID), mirroring how F-186 patches
table0's word3 for v4.

THE GATE
--------
`static bool fman_pcd_v6_enable;` + `module_param`, default false, plus an
exported accessor `fman_pcd_v6_enabled()`. EVERYTHING v6 (this fixup's second
node, F-211's v6 scheme arm + KG-direct clear, F-212's LCV split) is guarded by
it. With the default (false):
  * F-210 skips the gro+16 write -> single table0 node, exactly as today.
  * F-211 never arms scheme #2, leaves match_vector=0, keeps F-178 KG-direct.
  * F-212 never calls set_lcv_split.
=> the v4 datapath is byte-identical and the whole v6 mechanism is dormant until
an operator sets `fman_pcd.v6_enable=1` on a validated board (M6 board gate).
The OOT ask.ko v6 preflight still returns -EOPNOTSUPP (SW fallback) independent
of this flag until its gate is separately flipped, so a stray module param alone
cannot publish a v6 flow.

SAFETY / S0
-----------
Node encoding is byte-identical to F-185's proven table0 node (same VARIANT-B
word0/word1/word2 layout, key_size/hash_shift/hash_mask_bits from table1). The
only new silicon write is 16 bytes at gro+16, inside the 256-byte `gro`
allocation (2 nodes = 32 B << 256). No new MURAM alloc. Qdrant gate satisfied:
CCOBASE*16 node addressing + VARIANT-B layout cross-checked against
arch/fman-microcode-210-programming-reference.md and the passing 2026-08-19
exp-ccobase silicon proof.

Must run AFTER F-185 (extends its node-write block) and AFTER F-140 (needs the
table1 allocation). Placed after F-186 in ci-setup-kernel.sh. Idempotent via the
F-210 markers.
"""

import os
import sys

kroot = "drivers/net/ethernet/freescale/fman"
pcd_c = os.path.join(kroot, "fman_pcd.c")
ih = os.path.join(kroot, "fman_pcd_internal.h")

changes = 0


def fatal(msg):
    print(f"### F-210: FATAL: {msg}")
    sys.exit(1)


# ─────────────────────────────────────────────────────────────────────────
# 1. fman_pcd.c: the v6_enable module param + accessor (the gate)
# ─────────────────────────────────────────────────────────────────────────
if not os.path.exists(pcd_c):
    fatal(f"{pcd_c} not found")

with open(pcd_c) as f:
    src = f.read()

# Anchor the param block on the MODULE include region tail (stable across the
# whole patch stack). Insert right after the last local #include line group.
gate_marker = "F-210(v6-enable-gate)"
if gate_marker in src:
    print("### F-210: v6_enable gate already present in fman_pcd.c")
else:
    gate_anchor = '#include "fman_keygen_internal.h"\n'
    if gate_anchor not in src:
        fatal("keygen_internal.h include anchor not found in fman_pcd.c")
    gate_block = (
        gate_anchor +
        "\n"
        "/* F-210(v6-enable-gate): master default-OFF switch for the IPv6 FE\n"
        " * productization path (dual ehash node at gro+16, the v6 KeyGen scheme\n"
        " * arm with CCOBASE=1/kgse_mv, and the parser LCV split). Everything v6\n"
        " * is gated on this so the v4 datapath stays byte-identical until an\n"
        " * operator opts in on a board-validated image (fman_pcd.v6_enable=1).\n"
        " * The OOT ask.ko v6 preflight is separately gated, so this flag alone\n"
        " * cannot publish a v6 flow.\n"
        " */\n"
        "static bool fman_pcd_v6_enable;\n"
        "module_param_named(v6_enable, fman_pcd_v6_enable, bool, 0444);\n"
        "MODULE_PARM_DESC(v6_enable,\n"
        "\t\t \"Enable the dormant IPv6 FE offload path (default 0; \"\n"
        "\t\t \"board-validated opt-in only)\");\n"
        "\n"
        "bool fman_pcd_v6_enabled(void)\n"
        "{\n"
        "\treturn fman_pcd_v6_enable;\n"
        "}\n"
        "EXPORT_SYMBOL_GPL(fman_pcd_v6_enabled);\n"
    )
    src = src.replace(gate_anchor, gate_block, 1)
    changes += 1
    print("### fman_pcd.c: F-210 v6_enable module-param gate added")

# ─────────────────────────────────────────────────────────────────────────
# 2. fman_pcd.c: extend F-185's node-write block to also write table1's node
#    at gro+16 (CCOBASE=1) when v6 is enabled and table1 exists.
# ─────────────────────────────────────────────────────────────────────────
node_marker = "F-210(v6-node)"
if node_marker in src:
    print("### F-210: v6 second-node write already present in fman_pcd.c")
else:
    # Anchor: the exact tail of F-185's table0-node 'if' branch — word3 write
    # then the closing brace opening the 'else' fallback. Inserting the v6 node
    # between word3 and '} else {' keeps it inside the (et && fe_int_buf_off)
    # success branch, so it only runs when the FE-VM node form was built.
    anchor = (
        "\t\t\t\t\t\t/* word3 = miss NIA: patched by arm_fe via\n"
        "\t\t\t\t\t\t * fman_pcd_fe_node_set_miss_nia() before the\n"
        "\t\t\t\t\t\t * EXTC SYNC in fman_port_set_cc_base().\n"
        "\t\t\t\t\t\t */\n"
        "\t\t\t\t\t\tiowrite32be(0, c + 12);\n"
        "\t\t\t\t\t} else {\n"
    )
    if anchor not in src:
        fatal("F-185 table0-node 'if' branch tail not found verbatim in "
              "fman_pcd.c (F-185 must run first / source drifted).")
    v6_node = (
        "\t\t\t\t\t\t/* word3 = miss NIA: patched by arm_fe via\n"
        "\t\t\t\t\t\t * fman_pcd_fe_node_set_miss_nia() before the\n"
        "\t\t\t\t\t\t * EXTC SYNC in fman_port_set_cc_base().\n"
        "\t\t\t\t\t\t */\n"
        "\t\t\t\t\t\tiowrite32be(0, c + 12);\n"
        "\t\t\t\t\t\t/* F-210(v6-node): when v6 is enabled, also write\n"
        "\t\t\t\t\t\t * table1's (38-byte, F-140) en_exthash_node at\n"
        "\t\t\t\t\t\t * c+16 = gro+16 (CCOBASE=1). The v6 scheme\n"
        "\t\t\t\t\t\t * (F-211, cc_base_offset=1 -> KGSE_MODE\n"
        "\t\t\t\t\t\t * 0x81000006 via F-209) dispatches to this node.\n"
        "\t\t\t\t\t\t * Same VARIANT-B encoding as table0 above; word3\n"
        "\t\t\t\t\t\t * (v6 miss NIA) patched by F-211's v6 arm. gro is\n"
        "\t\t\t\t\t\t * a 256-byte alloc so gro+16..+28 is in-bounds.\n"
        "\t\t\t\t\t\t * Silicon-proven 2026-08-19 (exp-ccobase).\n"
        "\t\t\t\t\t\t */\n"
        "\t\t\t\t\t\tif (fman_pcd_v6_enabled()) {\n"
        "\t\t\t\t\t\t\tstruct fman_pcd_ehash_table *et6 =\n"
        "\t\t\t\t\t\t\t\tfman_pcd_ehash_table_by_index(pcd, 1);\n"
        "\n"
        "\t\t\t\t\t\t\tif (et6) {\n"
        "\t\t\t\t\t\t\t\tu64 tb6 = (u64)et6->table_dma;\n"
        "\n"
        "\t\t\t\t\t\t\t\tiowrite32be((1U << 30) |\n"
        "\t\t\t\t\t\t\t\t\t    ((u32)(et6->key_size & 0x3f) << 24) |\n"
        "\t\t\t\t\t\t\t\t\t    (4U << 20) |\n"
        "\t\t\t\t\t\t\t\t\t    ((u32)(et6->hash_shift & 0x7) << 16) |\n"
        "\t\t\t\t\t\t\t\t\t    ((u32)(tb6 >> 32) & 0xffU), c + 16);\n"
        "\t\t\t\t\t\t\t\tiowrite32be((u32)(tb6 & 0xffffffffU), c + 20);\n"
        "\t\t\t\t\t\t\t\tiowrite32be(((u32)((pcd->fe_int_buf_off >> 8) &\n"
        "\t\t\t\t\t\t\t\t\t\t   0xffffU) << 16) |\n"
        "\t\t\t\t\t\t\t\t\t    (0x80U << 4) |\n"
        "\t\t\t\t\t\t\t\t\t    (u32)(et6->hash_mask_bits & 0xfU),\n"
        "\t\t\t\t\t\t\t\t\t    c + 24);\n"
        "\t\t\t\t\t\t\t\tiowrite32be(0, c + 28);\n"
        "\t\t\t\t\t\t\t\tpr_info(\"fman_pcd fe_arm: F-210 v6 node written at gro+16 (table1 key_size=%u)\\n\",\n"
        "\t\t\t\t\t\t\t\t\tet6->key_size);\n"
        "\t\t\t\t\t\t\t}\n"
        "\t\t\t\t\t\t}\n"
        "\t\t\t\t\t} else {\n"
    )
    src = src.replace(anchor, v6_node, 1)
    changes += 1
    print("### fman_pcd.c: F-210 v6 second-node write added (gro+16, gated)")

if changes:
    with open(pcd_c, "w") as f:
        f.write(src)

# ─────────────────────────────────────────────────────────────────────────
# 3. fman_pcd_internal.h: declare the gate accessor for the other TUs
# ─────────────────────────────────────────────────────────────────────────
with open(ih) as f:
    hsrc = f.read()

decl_marker = "F-210(v6-enable-decl)"
if decl_marker in hsrc:
    print("### F-210: v6_enabled() declaration already present")
else:
    # F-185 added its miss-nia decl right after fman_pcd_get_kg_list(); anchor
    # on that stable line.
    h_anchor = "struct list_head *fman_pcd_get_kg_list(struct fman_pcd *pcd);\n"
    if h_anchor not in hsrc:
        fatal("fman_pcd_get_kg_list decl anchor not found in fman_pcd_internal.h")
    h_block = (
        h_anchor +
        "/* F-210(v6-enable-decl): master gate for the dormant IPv6 FE path. */\n"
        "bool fman_pcd_v6_enabled(void);\n"
    )
    hsrc = hsrc.replace(h_anchor, h_block, 1)
    with open(ih, "w") as f:
        f.write(hsrc)
    changes += 1
    print("### fman_pcd_internal.h: F-210 v6_enabled() declaration added")

if changes:
    print(f"### F-210 complete ({changes} change(s))")
else:
    print("### F-210 no changes applied (already present)")
    sys.exit(0)
