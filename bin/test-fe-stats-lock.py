#!/usr/bin/env python3
"""test-fe-stats-lock.py <kernel-source-dir>: F-312 structural check on the generated fman_pcd.c.

The ehash stats reader (fman_pcd_fe_flow_get_stats_checked) must take only pcd->fe_stats_lock, never the
fe_lock mutex that inserts/deletes hold across FMFP_EXTC SYNC polls; and every change of what that reader
walks (t->bidx links, the fe_ehash_tables and fe_ports lists, fp->table_v4) must happen inside a
fe_stats_lock section, with table_free unpublishing the table before freeing it. Fails on a tree without
F-312 (the 2026-11-13 flow-ladder convoy: ~1000 D-state kworkers at fman_pcd_fe_flow_get_stats_checked+0x6c).
"""
import re
import sys

LOCK, UNLOCK = re.compile(r"\bspin_lock\(&[\w>.-]*fe_stats_lock\)"), re.compile(r"\bspin_unlock\(&[\w>.-]*fe_stats_lock\)")
MUTATIONS = re.compile(r"hlist_add_head\(&\w+->bnode|hlist_del\(&\w+->bnode|list_add_tail\(&fp->node, &pcd->fe_ports\)"
                       r"|list_del\(&fp->node\)|list_add_tail\(&t->node, &pcd->fe_ehash_tables\)|list_del\(&t->node\)"
                       r"|\bfp->table_v4 = ")


def function(src, head):
    """The definition of head (skips forward declarations: a ';' before the body's '{')."""
    i = src.index(head)
    while src.index(";", i) < src.index("{", i):
        i = src.index(head, i + 1)
    return src[i:src.index("\n}\n", i) + 3]


def check(src):
    errors = []
    get = function(src, "static int fman_pcd_fe_flow_get_stats_checked(")
    if "fe_lock)" in get.replace("fe_stats_lock)", ""):
        errors.append("get_stats_checked still takes fe_lock")
    if len(LOCK.findall(get)) != 1 or len(UNLOCK.findall(get)) != 3:
        errors.append("get_stats_checked: expected 1 spin_lock / 3 spin_unlock on fe_stats_lock")
    # F-313: WGFP calls the keysize accessor on every add, delete and stats poll
    ks = function(src, "int fman_pcd_fe_ehash_keysize(")
    if "fe_lock)" in ks.replace("fe_stats_lock)", "") or len(LOCK.findall(ks)) != 1 or len(UNLOCK.findall(ks)) != 1:
        errors.append("fman_pcd_fe_ehash_keysize: expected fe_stats_lock only (F-313)")
    held = False
    for n, line in enumerate(src.splitlines(), 1):
        if LOCK.search(line):
            held = True
        elif UNLOCK.search(line):
            held = False
        elif MUTATIONS.search(line) and not held:
            errors.append(f"line {n}: reader-visible mutation outside fe_stats_lock: {line.strip()}")
        elif held and re.search(r"\b(mutex_lock|kfree|kvfree|dma_pool_free|dma_pool_destroy|"
                                r"dma_free_coherent|fman_pcd_ehash_sync|fman_fpm_extc_sync|msleep|usleep_range)\(", line):
            errors.append(f"line {n}: sleeping/free call under fe_stats_lock: {line.strip()}")
    free = function(src, "static int fman_pcd_ehash_table_free(")
    if not UNLOCK.search(free) or UNLOCK.search(free).start() > free.index("dma_pool_destroy("):
        errors.append("table_free: table not unpublished under fe_stats_lock before dma_pool_destroy")
    return errors


def main():
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    src = open(f"{sys.argv[1]}/drivers/net/ethernet/freescale/fman/fman_pcd.c").read()
    errors = check(src)
    for e in errors:
        print(f"### test-fe-stats-lock: FAIL: {e}")
    if errors:
        return 1
    print("### test-fe-stats-lock: OK (stats reader off fe_lock; all reader-visible mutations under fe_stats_lock)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
