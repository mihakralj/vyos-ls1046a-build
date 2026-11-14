"""F-313 (2026-11-13): fman_pcd_fe_ehash_keysize() under fe_stats_lock, not fe_lock.

Stage S4 of plans/ASK2-FLOW-SCALING-PLAN.md. The keysize accessor resolves a
port's ehash table and returns its key size; WGFP calls it on every flow add,
delete and stats poll (wgfp_fm_port, dpaa_wgfp.c), dpaa_eth.c and the ntuple
bridge on their own paths. It took pcd->fe_lock, so each call could sleep
behind inserts and deletes that hold fe_lock across FMFP_EXTC SYNC polls: the
same convoy F-312 removed from the stats read.

What it reads is exactly what F-312 put under the innermost spinlock
pcd->fe_stats_lock: the fe_ports list and fp->table_v4 (fman_pcd_fe_port_find),
the fe_ehash_tables list (fman_pcd_ehash_table_by_index fallback), and
t->key_size, which is set before the table is published and never changes.
Nothing under the lock sleeps. Independent review 2026-11-13 (qdrant) listed
this move as the optional eighth F-312 edit.

Needs F-312. Idempotent (marker); the anchor must match exactly once.
Host check: bin/test-fe-stats-lock.py (runs after this fixup).
"""

import sys

PCD = "drivers/net/ethernet/freescale/fman/fman_pcd.c"
MARK = "F-313"
OLD = """\
\tmutex_lock(&pcd->fe_lock);
\tt = fman_pcd_ehash_table_for_port(pcd, hw_port_id);
\tks = t ? t->key_size : -ENODEV;
\tmutex_unlock(&pcd->fe_lock);
"""
NEW = """\
\tspin_lock(&pcd->fe_stats_lock);\t/* F-313 */
\tt = fman_pcd_ehash_table_for_port(pcd, hw_port_id);
\tks = t ? t->key_size : -ENODEV;
\tspin_unlock(&pcd->fe_stats_lock);
"""


def main():
    try:
        src = open(PCD).read()
    except FileNotFoundError as exc:
        print(f"### F-313: FATAL: {exc}")
        return 1
    if MARK in src:
        print("### F-313: already applied")
        return 0
    if "F-312" not in src:
        print("### F-313: FATAL: needs F-312 (fe_stats_lock)")
        return 1
    if src.count(OLD) != 1:
        print(f"### F-313: FATAL: keysize anchor found {src.count(OLD)} times (expected 1)")
        return 1
    with open(PCD, "w") as stream:
        stream.write(src.replace(OLD, NEW))
    print("### F-313: fman_pcd_fe_ehash_keysize() reads under fe_stats_lock")
    return 0


if __name__ == "__main__":
    sys.exit(main())
