"""F-239 (T-M6-8 VLAN-v6 dig): CC-tree comparator input capture (probe2).

WHY A FIXUP, NOT A PATCH
------------------------
Board patch 0193 (same content as this fixup) failed to apply in CI: its
context anchors were taken from ~/kernel-git-cache/linux's CURRENT state,
which already has every kernel-fixups/*.py transform baked in -- including
F-216, which rewrites the exact RXHASH block this capture hooks into
("F-072/F-170 capture removed; RXHASH block normalized"). Board patches in
`series` apply BEFORE the F-*.py fixups run, so at the point 0193 tried to
apply, F-216 had not yet rewritten that block -- git apply --3way partially
matched, left literal conflict markers in dpaa_eth.c, and the subsequent
compile failed on those markers. Exactly the same reason F-236 had to be a
fixup instead of a patch for the F-224 GEC-write block. This must run AFTER
F-216 (anchors on its output); ci-setup-kernel.sh places it at the end of
the fixup sequence, well after F-216's own call.

WHAT IT DOES
------------
The dual-lane CC-tree GEC experiments (0185-0188) all board-tested a clean,
family-independent MISS: KeyGen's GEC registers reprogram correctly
(byte-for-byte identical to the proven ehash dual-lane extraction, F-224)
but a real matching frame never reaches the CC-tree comparator.
fman_pcd_cc.c's own CC_IC_KG_KEY_OFFSET (0x50) documents where the
comparator reads its compare window from -- but this project has never
verified whether GEC's output actually lands there, or anywhere
host-visible at all.

Both existing capture facilities turned out to be dead: ic_probe's
fman_pcd_ic_vaddr is set in the TX cleanup path (dpaa_cleanup_tx_fd), not
RX -- captures the last transmitted frame's data buffer, unrelated to
classification input, dereferenced later (crash-prone, reproduced live
2026-09-03). hash_probe's own capture was removed by F-216 after it
"amplified a zero-address FD into a kernel panic" -- fman_pcd_hash_off/
kg_hash are still declared and displayed but nothing writes them anymore.

This adds a third, deliberately safer capture: synchronous only (no
pointer stashed for later, unlike the removed F-072/F-170 diagnostic),
right inside rx_default_dqrr() immediately after the existing mainline
RX-hash dereference already proved the buffer valid, guarded against a
null vaddr and an underflowing offset, and scoped to eth1 only (the
sacrificial test port, never production eth3/eth4). Captures a 176-byte
window starting at parse-result base (vaddr+hash_offset-0x28, the offset
F-213 already established as safe) into a static buffer, readable via a
new read-only debugfs node (fman_pcd/0/probe2) that hex-dumps it with
offsets relative to parse-result base.

Idempotent via per-section markers, same convention as F-236/F-238.
"""

import os
import sys

fman_c = "drivers/net/ethernet/freescale/fman/fman_pcd.c"
dpaa_c = "drivers/net/ethernet/freescale/dpaa/dpaa_eth.c"

changes = 0


def fatal(msg):
    print(f"### F-239: FATAL: {msg}")
    sys.exit(1)


if not os.path.exists(fman_c) or not os.path.exists(dpaa_c):
    print("### F-239: source files not found, skipping")
    sys.exit(0)

# ─────────────────────────────────────────────────────────────────────────
# 1. fman_pcd.c: globals + show function + debugfs registration.
# ─────────────────────────────────────────────────────────────────────────
with open(fman_c) as f:
    fsrc = f.read()

if "fman_pcd_probe2_buf" in fsrc:
    print("### F-239: fman_pcd.c already has probe2")
else:
    anchor_globals = (
        "u64 fman_pcd_kg_hash;\n"
        "unsigned int fman_pcd_hash_off;\n"
        "void *fman_pcd_ic_vaddr;\n"
    )
    if anchor_globals not in fsrc:
        fatal("fman_pcd_kg_hash/hash_off/ic_vaddr global anchor not found in fman_pcd.c")
    new_globals = (
        anchor_globals +
        "\n"
        "/*\n"
        " * T-M6-8 VLAN-v6 CC-comparator-input dig (F-239). Synchronous, bounded\n"
        " * RX-path capture ONLY -- deliberately NOT a save-a-pointer-for-later\n"
        " * like the F-072/F-170 diagnostic F-216 removed after it panicked on a\n"
        " * zero-address FD (see the F-216 comment in dpaa_eth.c). The capture\n"
        " * site (dpaa_eth.c) memcpy()s a fixed window into this buffer\n"
        " * synchronously, inside the RX callback, while the frame's buffer is\n"
        " * known-valid (the mainline RX-hash read immediately above it already\n"
        " * dereferenced the same vaddr successfully). Scoped to eth1 only\n"
        " * (sacrificial test port, not production eth3/eth4). Window starts at\n"
        " * parse-result base (vaddr+hash_offset-0x28, the offset F-213 already\n"
        " * established as safe) and covers FMAN_PCD_PROBE2_LEN bytes forward --\n"
        " * comfortably inside the 4K RX buffer page. Purpose: does the CC-tree's\n"
        " * KeyGen-extracted GEC composite land anywhere in the host-visible\n"
        " * frame annotation at all?\n"
        " */\n"
        "#define FMAN_PCD_PROBE2_LEN 176\n"
        "u8 fman_pcd_probe2_buf[FMAN_PCD_PROBE2_LEN];\n"
        "bool fman_pcd_probe2_valid;\n"
    )
    fsrc = fsrc.replace(anchor_globals, new_globals, 1)
    changes += 1
    print("### fman_pcd.c: F-239 probe2_buf/valid globals added")

    anchor_show = (
        "static int fman_pcd_hash_probe_show(struct seq_file *m, void *v)\n"
        "{\n"
        "\tif (!fman_pcd_hash_off) {\n"
        "\t\tseq_puts(m, \"idle (no eth4 frame captured)\\n\");\n"
        "\t\treturn 0;\n"
        "\t}\n"
        "\tseq_printf(m, \"hash_off=%u captured=%016llx\\n\",\n"
        "\t\tfman_pcd_hash_off, fman_pcd_kg_hash);\n"
        "\treturn 0;\n"
        "}\n"
        "DEFINE_SHOW_ATTRIBUTE(fman_pcd_hash_probe);\n"
    )
    if anchor_show not in fsrc:
        fatal("fman_pcd_hash_probe_show anchor not found in fman_pcd.c")
    new_show = (
        anchor_show +
        "\n"
        "/* T-M6-8 VLAN-v6 (F-239): dump the raw parse-result-base window\n"
        " * captured on eth1. See the fman_pcd_probe2_buf comment above for the\n"
        " * capture-site safety rationale. Offsets are printed relative to\n"
        " * parse-result base (0), so the hash field (known to live at\n"
        " * parse-result+0x28) and the CC comparator's IC+0x50 key field\n"
        " * (CC_IC_KG_KEY_OFFSET in fman_pcd_cc.c) both land at a fixed,\n"
        " * easy-to-spot offset in this listing if they're present at all in\n"
        " * the host-visible frame annotation.\n"
        " */\n"
        "static int fman_pcd_probe2_show(struct seq_file *s, void *unused)\n"
        "{\n"
        "\tint i;\n"
        "\n"
        "\tif (!fman_pcd_probe2_valid) {\n"
        "\t\tseq_puts(s, \"idle (no eth1 frame captured)\\n\");\n"
        "\t\treturn 0;\n"
        "\t}\n"
        "\tseq_puts(s, \"parse-result-base window, offsets relative to +0:\\n\");\n"
        "\tfor (i = 0; i < FMAN_PCD_PROBE2_LEN; i += 16) {\n"
        "\t\tint j, n = min(16, FMAN_PCD_PROBE2_LEN - i);\n"
        "\n"
        "\t\tseq_printf(s, \"%+04d:\", i);\n"
        "\t\tfor (j = 0; j < n; j++)\n"
        "\t\t\tseq_printf(s, \" %02x\", fman_pcd_probe2_buf[i + j]);\n"
        "\t\tseq_puts(s, \"\\n\");\n"
        "\t}\n"
        "\treturn 0;\n"
        "}\n"
        "\n"
        "static int fman_pcd_probe2_open(struct inode *inode, struct file *file)\n"
        "{\n"
        "\treturn single_open(file, fman_pcd_probe2_show, inode->i_private);\n"
        "}\n"
        "\n"
        "static const struct file_operations fman_pcd_probe2_fops = {\n"
        "\t.owner\t\t= THIS_MODULE,\n"
        "\t.open\t\t= fman_pcd_probe2_open,\n"
        "\t.read\t\t= seq_read,\n"
        "\t.llseek\t\t= seq_lseek,\n"
        "\t.release\t= single_release,\n"
        "};\n"
    )
    fsrc = fsrc.replace(anchor_show, new_show, 1)
    changes += 1
    print("### fman_pcd.c: F-239 probe2_show/fops added")

    anchor_reg = (
        "\t\tif (!debugfs_create_file(\"hash_probe\", 0444, pcd->debugfs_dir, pcd,\n"
        "\t\t\t\t       &fman_pcd_hash_probe_fops))\n"
        "\t\t\tpr_warn(\"%s: error creating hash_probe\\n\", __func__);\n"
    )
    if anchor_reg not in fsrc:
        fatal("hash_probe debugfs_create_file anchor not found in fman_pcd.c")
    new_reg = (
        anchor_reg +
        "\t\t\t\tdebugfs_create_file(\"probe2\", 0444,\n"
        "\t\t\t\t\t\t    pcd->debugfs_dir, pcd,\n"
        "\t\t\t\t\t\t    &fman_pcd_probe2_fops);\n"
    )
    fsrc = fsrc.replace(anchor_reg, new_reg, 1)
    changes += 1
    with open(fman_c, "w") as f:
        f.write(fsrc)
    print("### fman_pcd.c: F-239 probe2 debugfs registration added")

# ─────────────────────────────────────────────────────────────────────────
# 2. dpaa_eth.c: extern decls + synchronous capture site.
# ─────────────────────────────────────────────────────────────────────────
with open(dpaa_c) as f:
    dsrc = f.read()

if "fman_pcd_probe2_buf" in dsrc:
    print("### F-239: dpaa_eth.c already has probe2")
else:
    anchor_extern = (
        "extern u64 fman_pcd_kg_hash;\n"
        "extern unsigned int fman_pcd_hash_off;\n"
    )
    if anchor_extern not in dsrc:
        fatal("fman_pcd_kg_hash/hash_off extern anchor not found in dpaa_eth.c")
    new_extern = (
        anchor_extern +
        "/* T-M6-8 VLAN-v6 CC-comparator-input dig (F-239); see the buffer's own\n"
        " * comment in fman_pcd.c for the full rationale and the\n"
        " * synchronous-capture safety argument (this is deliberately NOT a\n"
        " * repeat of the F-072/F-170 pattern F-216 removed after a panic -- no\n"
        " * pointer is stashed here). */\n"
        "#define FMAN_PCD_PROBE2_LEN 176\n"
        "extern u8 fman_pcd_probe2_buf[FMAN_PCD_PROBE2_LEN];\n"
        "extern bool fman_pcd_probe2_valid;\n"
    )
    dsrc = dsrc.replace(anchor_extern, new_extern, 1)
    changes += 1
    print("### dpaa_eth.c: F-239 probe2 extern decls added")

    anchor_capture = (
        "\t\thash = be32_to_cpu(*(__be32 *)(vaddr + hash_offset));\n"
        "\t\thash_valid = true;\n"
        "\t}\n"
    )
    if anchor_capture not in dsrc:
        fatal("F-216 normalized RXHASH block anchor not found in dpaa_eth.c "
              "-- F-239 must run after F-216")
    new_capture = (
        "\t\thash = be32_to_cpu(*(__be32 *)(vaddr + hash_offset));\n"
        "\t\thash_valid = true;\n"
        "\n"
        "\t\t/* T-M6-8 VLAN-v6 CC-comparator-input dig (F-239): synchronous,\n"
        "\t\t * bounded capture only, right here in the RX callback while\n"
        "\t\t * vaddr is known-valid (just dereferenced successfully above)\n"
        "\t\t * -- NOT a save-a-pointer-for-later like the diagnostic F-216\n"
        "\t\t * removed after it panicked on a zero-address FD. Scoped to\n"
        "\t\t * eth1 only (sacrificial test port). hash_offset >= 0x28\n"
        "\t\t * guards the window start against underflow (0x28 is the\n"
        "\t\t * established parse-result-base back-offset, see\n"
        "\t\t * fman_pcd_cc.c CC_IC_KG_KEY_OFFSET's own comment for the\n"
        "\t\t * provenance). */\n"
        "\t\tif (vaddr && !strcmp(net_dev->name, \"eth1\") &&\n"
        "\t\t    hash_offset >= 0x28) {\n"
        "\t\t\tmemcpy(fman_pcd_probe2_buf,\n"
        "\t\t\t       vaddr + hash_offset - 0x28,\n"
        "\t\t\t       FMAN_PCD_PROBE2_LEN);\n"
        "\t\t\tfman_pcd_probe2_valid = true;\n"
        "\t\t}\n"
        "\t}\n"
    )
    dsrc = dsrc.replace(anchor_capture, new_capture, 1)
    changes += 1
    with open(dpaa_c, "w") as f:
        f.write(dsrc)
    print("### dpaa_eth.c: F-239 probe2 synchronous capture added")

if changes:
    print(f"### F-239 complete ({changes} change(s))")
else:
    print("### F-239 no changes (already present)")
    sys.exit(0)
