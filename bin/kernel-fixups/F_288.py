"""F-288: count ehash insert SYNC timeouts and poisoned-table clears.

Telemetry only: no FMan register access, ordering or ownership changes.
ehash_insert_sync_timeouts counts F-177 post-publish INV0 SYNCs that
did not complete; that path only logged before, so a real insert-side
timeout was invisible to ask-check. ehash_poison_clears counts F-269
poisoned tables released by a later completed SYNC (recover or drain).
Both are read-only module parameters of fsl_dpaa_fman, updated under
fe_lock like the F-271 counters. After F-287.
"""

import sys

PCD = "drivers/net/ethernet/freescale/fman/fman_pcd.c"

F177_OLD = (
    "\t\t\tif (__f177_extc & __f177_inv0)\n"
    "\t\t\t\tdev_warn(fman_get_dev(pcd->fman),\n"
    "\t\t\t\t\t \"fe_flow: F-177 FMFP_EXTC SYNC timed out after %u polls (fmfp_extc=0x%08x)\\n\",\n"
)
F177_NEW = (
    "\t\t\tif (__f177_extc & __f177_inv0)\n"
    "\t\t\t\tehash_insert_sync_timeouts++;\t/* F-288 */\n" + F177_OLD
)

# (old, new, expected occurrences of old)
EDITS = [
    ("module_param_named(ehash_sync_wedged, ehash_sync_wedged, uint, 0444);\n",
     "module_param_named(ehash_sync_wedged, ehash_sync_wedged, uint, 0444);\n"
     "/* F-288: F-177 post-insert SYNC timeouts and F-269 poison clears. */\n"
     "static unsigned int ehash_insert_sync_timeouts;\n"
     "static unsigned int ehash_poison_clears;\n"
     "module_param_named(ehash_insert_sync_timeouts, ehash_insert_sync_timeouts, uint, 0444);\n"
     "module_param_named(ehash_poison_clears, ehash_poison_clears, uint, 0444);\n",
     1),
    ("\tt->poisoned = false;\n"
     "\treturn 0;\n"
     "}\n\n"
     "static int fman_pcd_ehash_add_key(",
     "\tt->poisoned = false;\n"
     "\tehash_poison_clears++;\t/* F-288 */\n"
     "\treturn 0;\n"
     "}\n\n"
     "static int fman_pcd_ehash_add_key(",
     1),
    ("\tt->poisoned = false;\n"
     "\tt->draining = false;\n",
     "\tif (t->poisoned)\n"
     "\t\tehash_poison_clears++;\t/* F-288 */\n"
     "\tt->poisoned = false;\n"
     "\tt->draining = false;\n",
     1),
    (F177_OLD, F177_NEW, 2),
]


def main():
    try:
        with open(PCD) as stream:
            src = stream.read()
    except FileNotFoundError:
        print(f"### F-288: FATAL: {PCD} not found")
        return 1
    if "F-288" in src:
        if src.count("/* F-288 */") == 4 and "ehash_poison_clears, uint" in src:
            print("### F-288: already applied")
            return 0
        print("### F-288: FATAL: partially applied tree")
        return 1
    for i, (old, new, expected) in enumerate(EDITS):
        count = src.count(old)
        if count != expected:
            print(f"### F-288: FATAL: anchor {i} found {count} times (expected {expected})")
            return 1
        src = src.replace(old, new)
    with open(PCD, "w") as stream:
        stream.write(src)
    print("### F-288: applied")
    return 0


if __name__ == "__main__":
    sys.exit(main())
