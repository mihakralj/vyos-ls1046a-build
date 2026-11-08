"""F-284: wire the opt-in FMan Host-Command SYNC probe (fman_pcd_hc.c).

The source file is staged from kernel/common/files. This fixup only links it
into fsl_dpaa_fman and creates /sys/kernel/debug/fman_pcd/<N>/hc. Nothing is
programmed until a lab operator writes "init" to that file.
"""

import sys

MARK = "fman_pcd_hc"
BASE = "drivers/net/ethernet/freescale/fman/"

EDITS = (
    (
        BASE + "Makefile",
        "fman_pcd_cc_test.o fman_pcd_dcsr.o\n",
        "fman_pcd_cc_test.o fman_pcd_dcsr.o fman_pcd_hc.o\n",
        "Makefile object list",
    ),
    (
        BASE + "fman_pcd_internal.h",
        "void fman_pcd_dcsr_debugfs_init(struct dentry *parent,\n"
        "\t\t\t\tstruct fman_pcd *pcd);\n",
        "void fman_pcd_dcsr_debugfs_init(struct dentry *parent,\n"
        "\t\t\t\tstruct fman_pcd *pcd);\n"
        "\n"
        "/* F-284 opt-in Host-Command SYNC probe (fman_pcd_hc.c). */\n"
        "void fman_pcd_hc_debugfs_init(struct dentry *parent,\n"
        "\t\t\t      struct fman_pcd *pcd);\n",
        "dcsr prototype",
    ),
    (
        BASE + "fman_pcd.c",
        "\t\t\t\tfman_pcd_dcsr_debugfs_init(pcd->debugfs_dir, pcd);\n",
        "\t\t\t\tfman_pcd_dcsr_debugfs_init(pcd->debugfs_dir, pcd);\n"
        "\t\t\t\tfman_pcd_hc_debugfs_init(pcd->debugfs_dir, pcd);\n",
        "dcsr debugfs init call",
    ),
)

try:
    with open(BASE + "fman_pcd_hc.c") as f:
        if "fman_pcd_hc_debugfs_init" not in f.read():
            print("### F-284: FATAL: fman_pcd_hc.c lacks its init function")
            sys.exit(1)
except FileNotFoundError:
    print("### F-284: FATAL: fman_pcd_hc.c was not staged from kernel/common/files")
    sys.exit(1)

sources = {}
for path, old, _new, name in EDITS:
    try:
        with open(path) as f:
            sources[path] = f.read()
    except FileNotFoundError:
        print(f"### F-284: FATAL: {path} not found")
        sys.exit(1)

if all(MARK in sources[path] for path, _old, _new, _name in EDITS):
    print("### F-284: already applied")
    sys.exit(0)

for path, old, _new, name in EDITS:
    count = sources[path].count(old)
    if count != 1 or MARK in sources[path]:
        print(f"### F-284: FATAL: {name} anchor found {count} times (expected 1, unapplied)")
        sys.exit(1)

for path, old, new, _name in EDITS:
    with open(path, "w") as f:
        f.write(sources[path].replace(old, new, 1))
print("### F-284: opt-in HC SYNC probe linked (debugfs fman_pcd/<N>/hc)")
