"""F-265 (control-plane scaling, 2026-10-10): stop logging every flow insert.

Each hardware flow insert printed two unthrottled kernel lines from fman_pcd.c:
F-177's "FMFP_EXTC SYNC cleared after N poll(s)" (dev_info, two call sites:
the debugfs fe_flow path and fman_pcd_fe_flow_add()) and F-148's "CC match
table full (1 key max ...)" (pr_warn), which the 2026-10-06 investigation
proved cosmetic (F-183 pins numKeys; the match table is never consulted).
An 8192-flow warm-up wrote ~11,000 of each; 20k connections/s would mean
~40k inserts/s and the logging alone would swamp the box. The success line
becomes dev_dbg (the SYNC timeout stays dev_warn) and the F-148 warning is
printed once. Logging only. After F-264.
"""

import sys

MARK = "F-265"
PCD = "drivers/net/ethernet/freescale/fman/fman_pcd.c"

EDITS = [
    ('\t\t\t\tdev_info(fman_get_dev(pcd->fman),\n'
     '\t\t\t\t\t "fe_flow: F-177 FMFP_EXTC SYNC cleared after %u poll(s)\\n",',
     '\t\t\t\tdev_dbg(fman_get_dev(pcd->fman),\t/* F-265 */\n'
     '\t\t\t\t\t "fe_flow: F-177 FMFP_EXTC SYNC cleared after %u poll(s)\\n",', 2),
    ('pr_warn("fman_pcd: F-148 CC match table full (1 key max -- scaffold not sized for more)\\n");',
     'pr_warn_once("fman_pcd: F-148 CC match table full (1 key max -- scaffold not sized for more)\\n");\t/* F-265 */', 1),
]


def main():
    try:
        with open(PCD) as f:
            src = f.read()
    except FileNotFoundError:
        print(f"### F-265: FATAL: {PCD} not found")
        sys.exit(1)
    if MARK in src:
        print("### F-265: already applied")
        return
    for i, (old, _new, want) in enumerate(EDITS):
        n = src.count(old)
        if n != want:
            print(f"### F-265: FATAL: anchor {i} in {PCD} found {n} times (expected {want})")
            sys.exit(1)
    for old, new, _want in EDITS:
        src = src.replace(old, new)
    with open(PCD, "w") as f:
        f.write(src)
    print("### F-265: per-insert F-177/F-148 log lines demoted")


if __name__ == "__main__":
    main()
