"""F-247 (T-M6-2 B2, 2026-09-15): extend probe3 (F-241) with mode 2 --
atomic RICP-widen capture for the bridge FDB L2 composite
(PORT_ID|DA|SA|ETYPE), reusing cc_test_install_l2() (patch 0206/0207)
the same way probe3's existing modes 0/1 reuse cc_test_install_v6()/
cc_test_install_v6pid(). Answers plan §8.1's read-only comparator-window
question for the L2 case: does the CC comparator actually see the bytes
cc_pack_key_l2() packs, for a real live frame, before any CC tree is
trusted to match traffic for real.

New probe3 mode:
  probe3 2 <port_hex> <dst_mac> <fqid-hex>
    mode 2 -> cc_test_install_l2, reprefixed exactly like modes 0/1
    reprefix into cc_test_install_l2's own arg shape.

Same atomic safety rationale as F-241's own file-header comment (a
multi-command interactive sequence over a slow console measurably
corrupted real eth1 traffic on 2026-09-03) -- this stays ONE kernel-side
debugfs write bounded by one msleep(), not a human-driven multi-step
round-trip, for the L2 case exactly as already proven for the V6 case.

Must run after F-241 (cc_test_probe3/cc_test_probe3_buf/mode dispatch
must already exist) and after 0206/0207 (cc_test_install_l2 must
already exist -- it lives in the plain-tracked fman_pcd_cc_test.c, not
a prior fixup). Idempotent.
"""

import os
import sys

cc_test_c = "drivers/net/ethernet/freescale/fman/fman_pcd_cc_test.c"

if not os.path.exists(cc_test_c):
    print(f"### F-247: {cc_test_c} not found")
    sys.exit(0)

marker = "F-247(probe3-l2)"

with open(cc_test_c) as f:
    src = f.read()

if marker in src:
    print("### F-247: already applied")
    sys.exit(0)

for needed in ("cc_test_probe3", "cc_test_install_l2", "cc_test_saved_ricp"):
    if needed not in src:
        print(f"### F-247: FATAL: {needed} not found -- F-241 and 0206/0207 must run first")
        sys.exit(1)

# ---------------------------------------------------------------------
# 1. Widen the mode check and add the mode-2 reprefix branch.
# ---------------------------------------------------------------------
anchor1 = (
    '\tn = sscanf(kbuf, "probe3 %d %159[^\\n]", &mode, tail);\n'
    "\tif (n != 2 || (mode != 0 && mode != 1))\n"
    "\t\treturn -EINVAL;\n"
)
if anchor1 not in src:
    print("### F-247: FATAL: probe3 mode-check anchor not found")
    sys.exit(1)
if src.count(anchor1) != 1:
    print(f"### F-247: FATAL: anchor1 not unique ({src.count(anchor1)})")
    sys.exit(1)
new1 = (
    '\tn = sscanf(kbuf, "probe3 %d %159[^\\n]", &mode, tail);\n'
    f"\t/* {marker}: mode 2 added alongside the existing 0/1. */\n"
    "\tif (n != 2 || (mode != 0 && mode != 1 && mode != 2))\n"
    "\t\treturn -EINVAL;\n"
)
src = src.replace(anchor1, new1, 1)

anchor2 = (
    "\tif (mode == 1)\n"
    '\t\tn = snprintf(reprefixed, sizeof(reprefixed), "install_v6pid %s", tail);\n'
    "\telse\n"
    '\t\tn = snprintf(reprefixed, sizeof(reprefixed), "install_v6 %s", tail);\n'
)
if anchor2 not in src:
    print("### F-247: FATAL: probe3 reprefix anchor not found")
    sys.exit(1)
if src.count(anchor2) != 1:
    print(f"### F-247: FATAL: anchor2 not unique ({src.count(anchor2)})")
    sys.exit(1)
new2 = (
    f"\t/* {marker}: mode 2 = bridge FDB L2 composite. */\n"
    "\tif (mode == 2)\n"
    '\t\tn = snprintf(reprefixed, sizeof(reprefixed), "install_l2 %s", tail);\n'
    "\telse if (mode == 1)\n"
    '\t\tn = snprintf(reprefixed, sizeof(reprefixed), "install_v6pid %s", tail);\n'
    "\telse\n"
    '\t\tn = snprintf(reprefixed, sizeof(reprefixed), "install_v6 %s", tail);\n'
)
src = src.replace(anchor2, new2, 1)

# ---------------------------------------------------------------------
# 2. Dispatch to cc_test_install_l2() for mode 2.
# ---------------------------------------------------------------------
anchor3 = (
    "\terr = (mode == 1) ? cc_test_install_v6pid(pcd, reprefixed)\n"
    "\t\t\t   : cc_test_install_v6(pcd, reprefixed);\n"
)
if anchor3 not in src:
    print("### F-247: FATAL: probe3 install dispatch anchor not found")
    sys.exit(1)
if src.count(anchor3) != 1:
    print(f"### F-247: FATAL: anchor3 not unique ({src.count(anchor3)})")
    sys.exit(1)
new3 = (
    f"\t/* {marker}: mode 2 dispatch. */\n"
    "\tif (mode == 2)\n"
    "\t\terr = cc_test_install_l2(pcd, reprefixed);\n"
    "\telse\n"
    "\t\terr = (mode == 1) ? cc_test_install_v6pid(pcd, reprefixed)\n"
    "\t\t\t\t   : cc_test_install_v6(pcd, reprefixed);\n"
)
src = src.replace(anchor3, new3, 1)

with open(cc_test_c, "w") as f:
    f.write(src)
print("### fman_pcd_cc_test.c: F-247 probe3 mode 2 (bridge L2) added")
