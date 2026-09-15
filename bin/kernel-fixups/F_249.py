"""F-249 (T-M6-2 B2, 2026-09-15): make cc_test_install_l2() (F-248) point its
CC-miss row at the live FE_ENTER root instead of always leaving it 0.

F-248 deliberately shipped with miss_fe_off left 0 ("simplest possible arm")
because the read-only comparator-window question (plan §8.1) had to be
answered first -- it now has (probe3 mode 2 / F-247, patches 0208).  The
remaining unresolved silicon question is §8.2: does a DA-keyed CC leaf
coexist with ASK ehash on the same port via CC-miss->FE_ENTER, exactly as
already proven for VLAN CC keys (R4c, plans/ASK2-VLAN-REARCH.md §7c).

fman_pcd_fe_root_get_offset(fm) (fman_pcd.c, exported) returns the MURAM
offset of the one shared FE_ENTER root AD if ANY port currently has ASK
ehash engaged (pcd->fe_refcount > 0), else 0. Wiring it in here costs one
line and is byte-identical to today's behaviour when nothing has ehash
engaged yet (miss_fe_off stays 0 -> falls through to RSS, same as F-248
shipped): it only changes behaviour once a real ehash flow is live
somewhere, which is the exact precondition the coexistence experiment
needs. Same idea as cc_test_install_vlan()'s existing fe_miss_off arg,
just auto-sourced instead of operator-supplied, matching what patch
0206's own design note originally described for this command.

Must run after F-248 (cc_test_install_l2 must already exist). Idempotent.
"""

import os
import sys

cc_test_c = "drivers/net/ethernet/freescale/fman/fman_pcd_cc_test.c"

if not os.path.exists(cc_test_c):
    print(f"### F-249: {cc_test_c} not found")
    sys.exit(0)

marker = "F-249(install-l2-fe-coexist)"

with open(cc_test_c) as f:
    src = f.read()

if marker in src:
    print("### F-249: already applied")
    sys.exit(0)

if "cc_test_install_l2" not in src:
    print("### F-249: FATAL: cc_test_install_l2 not found -- F-248 must run first")
    sys.exit(1)

anchor = (
    "\tspec->miss_fqid = 0;\t/* miss stays on the existing RSS/SW path */\n"
    "\tspec->keys[0].present = FMAN_PCD_CC_HW_F_MAC_DST;\n"
)
n = src.count(anchor)
if n != 1:
    print(f"### F-249: FATAL: expected 1 miss_fqid/keys[0] anchor in cc_test_install_l2, found {n}")
    sys.exit(1)

new = (
    "\tspec->miss_fqid = 0;\t/* miss stays on the existing RSS/SW path */\n"
    f"\t/* {marker}: plan §8.2 coexistence -- if ASK ehash is already\n"
    "\t * engaged anywhere (shared FE_ENTER root), CC miss continues into\n"
    "\t * it instead of falling through to plain RSS. 0 (no-op, same as\n"
    "\t * before) if nothing has ehash engaged yet.\n"
    "\t */\n"
    "\tspec->miss_fe_off = fman_pcd_fe_root_get_offset(fm);\n"
    "\tspec->keys[0].present = FMAN_PCD_CC_HW_F_MAC_DST;\n"
)
src = src.replace(anchor, new, 1)

with open(cc_test_c, "w") as f:
    f.write(src)
print("### fman_pcd_cc_test.c: F-249 cc_test_install_l2 miss->FE_ENTER coexistence wired")
