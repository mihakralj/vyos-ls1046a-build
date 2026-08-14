"""F-191: gate the fman_pcd debugfs surface behind CONFIG_FMAN_PCD_DEBUG_FS.

Phase 1 of plans/ASK2-PRODUCTION-ARCHITECTURE.md: production images must
expose no /sys/kernel/debug/fman_pcd/<id>/ nodes.  The production control
surface is the generic-netlink "ask" family plus the exported fman_pcd_*
kernel API.

Two blocks, fman_pcd.c:
  1. Wrap the whole debugfs registration block (root_get + per-node
     debugfs_create_file calls) in
     `if (IS_ENABLED(CONFIG_FMAN_PCD_DEBUG_FS))`.  IS_ENABLED keeps every
     fops/handler symbol *referenced in source* (no -Wunused warnings)
     while dead-code elimination removes them at -O2 when the option is n.
  2. Wrap the matching `fman_pcd_debugfs_root_put()` call in
     fman_pcd_release() with the same guard — without it the release path
     WARNs on refcount 0 / goes negative when root_get never ran.

Idempotent via per-block markers ("F-191(reg-block)" / "F-191(rel-put)").
CI-only (REPLACEMENT block of ci-setup-kernel.sh).  The Kconfig symbol
itself is board patch 0170-fman-pcd-debugfs-kconfig.patch.
"""
import sys

changes = 0

def edit(path, blocks):
    global changes
    with open(path) as f:
        src = f.read()
    for name, marker, old, new in blocks:
        if marker not in new:
            print(f"### F-191: FATAL: block '{name}' marker not in replacement")
            sys.exit(1)
        if marker in src:
            print(f"### F-191: {name} already applied")
            continue
        if old not in src:
            print(f"### F-191: FATAL: '{name}' text not found verbatim")
            sys.exit(1)
        src = src.replace(old, new, 1)
        changes += 1
        print(f"### {path}: F-191 {name} applied")
    if changes:
        with open(path, "w") as f:
            f.write(src)

def wrap_region(src, name, marker, start_anchor, end_anchor, guard):
    """Wrap src[start:end] in `if (IS_ENABLED(<guard>)) { ... }`, re-indenting
    every non-blank line of the region by one tab.  start_anchor is the FIRST
    line of the region (inclusive).  Returns new src; raises on missing
    anchors."""
    si = src.index(start_anchor)
    ei = src.index(end_anchor, si + len(start_anchor))
    if ei < si:
        raise ValueError(f"{name}: end anchor before start")
    region = src[si:ei]
    indented = "\n".join(("\t" + l) if l.strip() else l
                         for l in region.split("\n"))
    new = (f"\tif (IS_ENABLED({guard})) {{\n"
           f"\t\t/* {marker}: debugfs surface gated behind {guard} */\n" +
           indented +
           "\t}\n")
    return src[:si] + new + src[ei:]

pcd = "drivers/net/ethernet/freescale/fman/fman_pcd.c"
with open(pcd) as f:
    src = f.read()

# ── Block 1: registration block in fman_pcd_init ─────────────────────
m1 = "F-191(reg-block)"
if m1 in src:
    print("### F-191: reg-block already applied")
elif "\terr = fman_pcd_debugfs_root_get();\n" not in src:
    print("### F-191: FATAL: registration block start anchor not found")
    sys.exit(1)
elif '\tdev_info(fman_get_dev(fman),\n\t\t "fman_pcd: ready' not in src:
    print("### F-191: FATAL: registration block end anchor not found")
    sys.exit(1)
else:
    src = wrap_region(src, "reg-block", m1,
                      "\terr = fman_pcd_debugfs_root_get();\n",
                      '\tdev_info(fman_get_dev(fman),\n\t\t "fman_pcd: ready',
                      "CONFIG_FMAN_PCD_DEBUG_FS")
    changes += 1
    print(f"### {pcd}: F-191 reg-block applied")

# ── Block 2: root_put in fman_pcd_release ─────────────────────────────
m2 = "F-191(rel-put)"
if m2 in src:
    print("### F-191: rel-put already applied")
elif ("\tif (!IS_ERR_OR_NULL(pcd->debugfs_dir))\n"
      "\t\tdebugfs_remove_recursive(pcd->debugfs_dir);\n"
      "\tfman_pcd_debugfs_root_put();\n") not in src:
    print("### F-191: FATAL: release-path root_put text not found verbatim")
    sys.exit(1)
else:
    old = ("\tif (!IS_ERR_OR_NULL(pcd->debugfs_dir))\n"
           "\t\tdebugfs_remove_recursive(pcd->debugfs_dir);\n"
           "\tfman_pcd_debugfs_root_put();\n")
    new = ("\tif (IS_ENABLED(CONFIG_FMAN_PCD_DEBUG_FS)) {\n"
           "\t\t/* F-191(rel-put): root_put only when root_get ran */\n"
           "\t\tif (!IS_ERR_OR_NULL(pcd->debugfs_dir))\n"
           "\t\t\tdebugfs_remove_recursive(pcd->debugfs_dir);\n"
           "\t\tfman_pcd_debugfs_root_put();\n"
           "\t}\n")
    src = src.replace(old, new, 1)
    changes += 1
    print(f"### {pcd}: F-191 rel-put applied")

if changes:
    with open(pcd, "w") as f:
        f.write(src)
    print(f"### F-191 complete ({changes} blocks)")
else:
    print("### F-191 no changes applied")
    sys.exit(1)
