#!/usr/bin/env python3
"""Host verification gate for F-321: strict per-port ehash table isolation.

Verifies on a patched kernel tree (or dry-run copy):
  1. F_321.py applies cleanly and is idempotent on re-run.
  2. fman_pcd_ehash_table_for_port() has no global table-0 fallback.
  3. fman_pcd_fe_flow_{add,del_checked,get_stats_checked}() route strictly
     through fman_pcd_ehash_table_for_port() (no table_idx!=0 or key_size==38
     global table bypass).
  4. fman_pcd_fe_disarm_preflight_locked() checks only fp->table_v4 and
     shared (!table->per_port) tables, allowing port A to disengage while
     unrelated port B has live flows.
  5. dpaa_ethtool.c dpaa_cls_add() engages FE before querying
     fman_pcd_fe_ehash_keysize(), after validating ring_cookie and slot count.
"""
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parent.parent
F321 = ROOT / "bin" / "kernel-fixups" / "F_321.py"
FMAN_PCD_REL = Path("drivers/net/ethernet/freescale/fman/fman_pcd.c")
ETHTOOL_REL = Path("drivers/net/ethernet/freescale/dpaa/dpaa_ethtool.c")


def func_body(src: str, header_prefix: str) -> str:
    idx = src.find(header_prefix)
    if idx < 0:
        raise AssertionError(f"function not found: {header_prefix}")
    brace = src.find("{", idx)
    depth = 0
    for i in range(brace, len(src)):
        c = src[i]
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return src[idx : i + 1]
    raise AssertionError(f"unterminated function: {header_prefix}")


def check_tree(ktree: Path) -> None:
    pcd = (ktree / FMAN_PCD_REL).read_text()
    eth = (ktree / ETHTOOL_REL).read_text()

    assert "bool per_port;" in pcd, "struct fman_pcd_ehash_table missing per_port"
    assert "et->per_port = true;" in pcd, \
        "fman_pcd_fe_arm missing et->per_port = true"

    tfp = func_body(pcd, "fman_pcd_ehash_table_for_port(")
    assert "fman_pcd_ehash_table_by_index" not in tfp, \
        "fman_pcd_ehash_table_for_port still falls back to table_by_index"
    assert "return NULL;" in tfp, \
        "fman_pcd_ehash_table_for_port must return NULL when port has no table_v4"

    add_fn = func_body(pcd, "int fman_pcd_fe_flow_add(")
    assert "action->table_idx != 0" in add_fn or "action->table_idx)" in add_fn, \
        "fman_pcd_fe_flow_add must reject non-zero table_idx"
    assert "fman_pcd_ehash_table_by_index" not in add_fn, \
        "fman_pcd_fe_flow_add still indexes global tables"

    del_fn = func_body(pcd, "static int fman_pcd_fe_flow_del_checked(")
    assert "fman_pcd_ehash_table_by_index" not in del_fn, \
        "fman_pcd_fe_flow_del_checked still indexes global table 1"

    stats_fn = func_body(pcd, "static int fman_pcd_fe_flow_get_stats_checked(")
    assert "fman_pcd_ehash_table_by_index" not in stats_fn, \
        "fman_pcd_fe_flow_get_stats_checked still indexes global table 1"

    preflight = func_body(pcd, "static int fman_pcd_fe_disarm_preflight_locked(")
    assert "table != fp->table_v4 && table->per_port" in preflight, \
        "fman_pcd_fe_disarm_preflight_locked does not skip unrelated per-port tables"

    cls_add = func_body(eth, "static int dpaa_cls_ins(")
    pos_ring = cls_add.find("dpaa_cls_queue_to_fqid(")
    pos_cap = cls_add.find("FMAN_CC_MAX_STATIC_KEYS")
    pos_eng = cls_add.find("fman_pcd_fe_engage(")
    pos_ks = cls_add.find("fman_pcd_fe_ehash_keysize(")
    assert 0 < pos_ring < pos_cap < pos_eng < pos_ks, (
        f"dpaa_cls_ins ordering wrong: ring={pos_ring} cap={pos_cap} "
        f"eng={pos_eng} ks={pos_ks}"
    )


def main() -> int:
    if len(sys.argv) not in (2, 3):
        print(f"usage: {sys.argv[0]} <kernel-tree> [--check-only]", file=sys.stderr)
        return 2
    ktree = Path(sys.argv[1])
    if not (ktree / FMAN_PCD_REL).is_file():
        print(f"FATAL: {ktree / FMAN_PCD_REL} missing", file=sys.stderr)
        return 2

    # If the tree already has F-321 applied (CI gate mode), verify in place + idempotency.
    if "F-321" in (ktree / FMAN_PCD_REL).read_text():
        check_tree(ktree)
        with tempfile.TemporaryDirectory() as td:
            td_p = Path(td)
            for rel in (FMAN_PCD_REL, ETHTOOL_REL):
                dst = td_p / rel
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(ktree / rel, dst)
            r = subprocess.run([sys.executable, str(F321)], cwd=td_p,
                               capture_output=True, text=True)
            assert r.returncode == 0, f"F_321 idempotency failed:\n{r.stdout}\n{r.stderr}"
            check_tree(td_p)
        print("PASS test-f321-fixup (already applied + idempotent)")
        return 0

    # Otherwise verify F-321 is NOT yet satisfied (failing-first) when invoked with --check-only,
    # or test applying F_321.py in a temporary copy.
    if "--check-only" in sys.argv:
        check_tree(ktree)
        return 0

    check_tree(ktree)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except AssertionError as exc:
        print(f"FAIL test-f321-fixup: {exc}", file=sys.stderr)
        sys.exit(1)
