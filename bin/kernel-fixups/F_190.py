"""F-190: add forward declaration for struct fman_pcd_ehash_table.

The fe_enter_build function uses struct fman_pcd_ehash_table (via
list_first_entry_or_null) but the struct is defined 160 lines later in
the file. The forward declaration fixes the compile error.

F-190 (1 block, fman_pcd.c). Idempotent ("F-190:" markers). CI-only.
"""

import sys
changes = 0

def edit(path, blocks):
    global changes
    with open(path) as f:
        src = f.read()
    for name, marker, old, new in blocks:
        if marker not in new:
            print(f"### F-190: FATAL: block '{name}' marker not in replacement")
            sys.exit(1)
        if marker in src:
            print(f"### F-190: {name} already applied")
            continue
        if old not in src:
            print(f"### F-190: FATAL: '{name}' text not found verbatim")
            sys.exit(1)
        src = src.replace(old, new, 1)
        changes += 1
        print(f"### {path}: F-190 {name} applied")
    if changes:
        with open(path, "w") as f:
            f.write(src)

pcd_blocks = [
    ('forward declaration',
     'F-190(fwd-decl)',
     "static int fman_pcd_fe_enter_build(struct fman_pcd *pcd, unsigned long fe_off)\n",
     "struct fman_pcd_ehash_table;\t/* F-190(fwd-decl) */\n"
     "static int fman_pcd_fe_enter_build(struct fman_pcd *pcd, unsigned long fe_off)\n"),
]
edit("drivers/net/ethernet/freescale/fman/fman_pcd.c", pcd_blocks)
if changes:
    print(f"### F-190 complete ({changes} blocks)")
else:
    print("### F-190 no changes applied")
    sys.exit(1)
