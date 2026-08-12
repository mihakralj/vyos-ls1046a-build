"""F-184: fe_obs arm kernel panic fix — the missing list_del.

CONTEXT (2026-08-12, .185, image 2026.08.12-0223-rolling): the first-ever
live `fe_obs arm` (patch 0169's canary discriminator, until now only
compile-verified) panicked the kernel, reproduced TWICE:

  list_add double add: new=ffff000810cbe9c0, prev=ffff000810cbe9c0, ...
  kernel BUG at lib/list_debug.c:35!
  Call trace:
   __list_add_valid_or_report+0xd4/0xd8
   fman_pcd_fe_obs_enq_one+0xf0/0x168
   fman_pcd_fe_obs_write+0x2e4/0x4b0
  Kernel panic - not syncing: Oops - BUG: Fatal exception
  Rebooting in 60 seconds..

ROOT CAUSE: fman_pcd_fe_obs_enq_one() acquires the canary FE object with
list_first_entry_or_null(&pcd->fe_available, ...) -- which does NOT unlink
the entry -- and then calls list_add_tail(&obj->node, &pcd->fe_singletons)
WITHOUT a list_del. The node is still linked in fe_available, so the add
is a double-add; CONFIG_DEBUG_LIST catches it and BUGs. Every other
fe_available consumer in fman_pcd.c (singletons builder ~L1171, enq
builder ~L1426, hashfe builder ~L2787) does list_del(&obj->node) before
list_add_tail -- fe_obs (written later, patch 0169) missed the pattern.

FIX: insert the same list_del(&obj->node) before the list_add_tail.
(list_move_tail would be equivalent; the del+add pair matches the
established in-file pattern.)

Anchored on the exact derived text (patch 0169 output). Idempotent
(marker "F-184:"). CI-only build.
"""

import sys

path = "drivers/net/ethernet/freescale/fman/fman_pcd.c"
with open(path) as f:
    src = f.read()

if "F-184:" in src:
    print("### F-184 already applied")
    sys.exit(0)

# NOTE: the tail "*out_off = obj->muram_off; list_add_tail(...fe_singletons);
# return 0; }" is NOT unique -- fman_pcd_fe_singleton_one() ends identically
# (and already has its list_del). The anchor MUST start at the
# FMAN_FE_ENQ_CTX_OFF context-build call, which is unique to fe_obs_enq_one.
old = (
    "\t\t\t\t  FMAN_FE_ENQ_CTX_OFF, &c);\n"
    "\t*out_off = obj->muram_off;\n"
    "\tlist_add_tail(&obj->node, &pcd->fe_singletons);\n"
    "\treturn 0;\n"
    "}\n"
)
new = (
    "\t\t\t\t  FMAN_FE_ENQ_CTX_OFF, &c);\n"
    "\t*out_off = obj->muram_off;\n"
    "\t/* F-184: obj came from list_first_entry_or_null(), which does NOT\n"
    "\t * unlink -- list_del is required before list_add_tail, exactly as\n"
    "\t * every other fe_available consumer in this file does (singletons,\n"
    "\t * enq, hashfe builders). Without it the node stays on fe_available\n"
    "\t * and the add is a double-add: CONFIG_DEBUG_LIST BUGs at\n"
    "\t * lib/list_debug.c:35, panicking the kernel on every fe_obs arm.\n"
    "\t * Reproduced twice on .185 (2026-08-12): trace\n"
    "\t * fman_pcd_fe_obs_enq_one -> __list_add_valid_or_report, panic=60\n"
    "\t * reboot both times.\n"
    "\t */\n"
    "\tlist_del(&obj->node);\n"
    "\tlist_add_tail(&obj->node, &pcd->fe_singletons);\n"
    "\treturn 0;\n"
    "}\n"
)

n = src.count(old)
if n != 1:
    print(f"### F-184: FATAL: expected exactly 1 anchor match, found {n} -- "
          "source drifted (patch 0169 changed?) or anchor ambiguous. "
          "Refusing to guess.")
    sys.exit(1)

src = src.replace(old, new, 1)

with open(path, "w") as f:
    f.write(src)

print("### fman_pcd.c: F-184 fe_obs_enq_one list_del fix applied")
