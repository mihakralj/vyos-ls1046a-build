"""F-107: gen_pool double-free prevention — per-port engagement guard.

Root cause (F-093-R1, 2026-07-19): Calling fman_pcd_fe_engage() twice on
the same port without an intervening successful disengage overwrites the
active KeyGen scheme MURAM pointer.  A subsequent fe_disengage_full()
causes a gen_pool_free_owner kernel panic (lib/genalloc.c:508) because
the first arm's MURAM allocations are lost and then double-freed.

Fix:
  1. Replace u8 fe_armed_port with DECLARE_BITMAP(fe_port_armed, 32)
  2. Guard fman_pcd_fe_engage() with test_bit → -EBUSY
  3. set_bit on successful engage, clear_bit on disengage
  4. Update fe_arm_show to iterate the bitmap

Disposition: fold-into 0153 + 0157
Upstream-Status: Inappropriate [LS1046A Mono Gateway DK FE-VM]
Risk-Tier: A (engagement guard only, no hot-path changes)
"""

import sys, os

kroot = "drivers/net/ethernet/freescale/fman"
pcd_c = os.path.join(kroot, "fman_pcd.c")

if not os.path.exists(pcd_c):
    print("### F-107: fman_pcd.c not found")
    sys.exit(0)

with open(pcd_c) as f:
    src = f.read()

changes = 0

# ── 1. Replace u8 fe_armed_port with DECLARE_BITMAP in struct fman_pcd ──

old_field = "\tu8 fe_armed_port;\t\t/* F-079-R4: last engaged port */"
new_field = "\tDECLARE_BITMAP(fe_port_armed, 32);\t/* F-107: per-port engagement guard (gen_pool double-free prevention) */"

if old_field in src:
    src = src.replace(old_field, new_field, 1)
    changes += 1
    print("### F-107: replaced fe_armed_port with DECLARE_BITMAP(fe_port_armed, 32)")
else:
    print("### F-107: WARNING — fe_armed_port field not found in struct fman_pcd")

# ── 2. Add -EBUSY guard at top of fman_pcd_fe_engage() ──

# The function starts with validation, then gets pcd.  Insert guard after
# the pcd NULL check but before rxport lookup.
guard_anchor = "\trxport = fman_port_lookup_rx(fm, hw_port_id);\n\tif (!rxport)\n\t\treturn -ENODEV;"

guard_block = """\trxport = fman_port_lookup_rx(fm, hw_port_id);
\tif (!rxport)
\t\treturn -ENODEV;

\t/* F-107: refuse double-engage — prevents gen_pool double-free on disengage. */
\tif (test_bit(hw_port_id, pcd->fe_port_armed)) {
\t\tpr_warn("fman_pcd: FE engage port 0x%02x already armed\\n", hw_port_id);
\t\treturn -EBUSY;
\t}
"""

if guard_anchor in src:
    src = src.replace(guard_anchor, guard_block, 1)
    changes += 1
    print("### F-107: added -EBUSY guard in fman_pcd_fe_engage()")
else:
    print("### F-107: WARNING — guard anchor not found in fman_pcd_fe_engage()")

# ── 3. Replace pcd->fe_armed_port = (u8)port_id with set_bit ──

# This is in __fman_pcd_fe_arm_engage(), after successful KG arm.
old_set = "\tpcd->fe_armed_port = (u8)port_id;"
new_set = "\tset_bit(port_id, pcd->fe_port_armed);\t/* F-107: per-port engagement guard */"

if old_set in src:
    src = src.replace(old_set, new_set, 1)
    changes += 1
    print("### F-107: replaced fe_armed_port assignment with set_bit")
else:
    print("### F-107: WARNING — fe_armed_port assignment not found")

# ── 4. Add clear_bit in __fman_pcd_fe_arm_disengage() ──

# Insert clear_bit right after the port_id range check, before fe_port_del.
disengage_anchor = """\tif (port_id < 0x08 || port_id >= 0x28)
\t\treturn -EINVAL;

\tfman_pcd_fe_port_del(pcd, (u8)port_id);"""

disengage_block = """\tif (port_id < 0x08 || port_id >= 0x28)
\t\treturn -EINVAL;

\t/* F-107: clear engagement guard BEFORE teardown (idempotent). */
\tclear_bit(port_id, pcd->fe_port_armed);

\tfman_pcd_fe_port_del(pcd, (u8)port_id);"""

if disengage_anchor in src:
    src = src.replace(disengage_anchor, disengage_block, 1)
    changes += 1
    print("### F-107: added clear_bit in __fman_pcd_fe_arm_disengage()")
else:
    print("### F-107: WARNING — disengage anchor not found")

# ── 5. Update fe_arm_show to iterate bitmap ──

# Replace the single-port display with a bitmap iteration.
old_show = """\tseq_printf(s, "MISS FQID: 0x%x (port 0x%02x)\\n", pcd->fe_miss_fqid, pcd->fe_armed_port);"""

new_show = """\tseq_printf(s, "MISS FQID: 0x%x\\n", pcd->fe_miss_fqid);
\tseq_puts(s, "Armed ports:");
\t{
\t\tint _i;
\t\tbool _any = false;
\t\tfor (_i = 0; _i < 32; _i++) {
\t\t\tif (test_bit(_i, pcd->fe_port_armed)) {
\t\t\t\tseq_printf(s, " 0x%02x", _i);
\t\t\t\t_any = true;
\t\t\t}
\t\t}
\t\tif (!_any)
\t\t\tseq_puts(s, " (none)");
\t}
\tseq_putc(s, '\\n');"""

if old_show in src:
    src = src.replace(old_show, new_show, 1)
    changes += 1
    print("### F-107: updated fe_arm_show to iterate bitmap")
else:
    print("### F-107: WARNING — fe_arm_show line not found")

# ── 6. Add #include <linux/bitmap.h> if not already present ──

if '#include <linux/bitmap.h>' not in src:
    # Insert after the last #include line near the top
    include_anchor = '#include <linux/slab.h>'
    if include_anchor in src:
        src = src.replace(include_anchor,
                          include_anchor + '\n#include <linux/bitmap.h>\t/* F-107: DECLARE_BITMAP for fe_port_armed */',
                          1)
        changes += 1
        print("### F-107: added #include <linux/bitmap.h>")
    else:
        print("### F-107: WARNING — slab.h include not found for bitmap.h insertion")

if changes:
    with open(pcd_c, "w") as f:
        f.write(src)
    print(f"### F-107: {changes} change(s) applied")
else:
    print("### F-107: no changes applied")