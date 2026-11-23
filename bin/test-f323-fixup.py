#!/usr/bin/env python3
"""Host verification gate for F-323: G4 multi-table dispatch.

Verifies on a patched kernel tree (or dry-run copy):
  1. F_323.py applies cleanly and is idempotent on re-run.
  2. KeyGen supports custom_gec[8], verified setup readback, all-zero
     keygen_scheme_clear(), and keygen_scheme_read_spc().
  3. fman_pcd_kg allocates secondary schemes top-down (31 down, strictly >
     primary scheme id) and never collides with primary scheme selection.
  4. fman_pcd enforces HXS mutual exclusion against F-266 punt slots, strict
     per-port class isolation, and fail-closed SYNC-fenced attach/detach.
  5. Compiled ASan/UBSan behavioral model (tests/dut/fman_g4_dispatch.c)
     exercises the extracted F-323 code directly.
"""
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parent.parent
F323 = ROOT / "bin" / "kernel-fixups" / "F_323.py"
HARNESS = ROOT / "tests" / "dut" / "fman_g4_dispatch.c"

KG_INT_H = Path("drivers/net/ethernet/freescale/fman/fman_keygen_internal.h")
KG_C = Path("drivers/net/ethernet/freescale/fman/fman_keygen.c")
PCD_KG_C = Path("drivers/net/ethernet/freescale/fman/fman_pcd_kg.c")
PCD_INT_H = Path("drivers/net/ethernet/freescale/fman/fman_pcd_internal.h")
PCD_C = Path("drivers/net/ethernet/freescale/fman/fman_pcd.c")
PCD_H = Path("include/linux/fsl/fman_pcd.h")
FILES = (KG_INT_H, KG_C, PCD_KG_C, PCD_INT_H, PCD_C, PCD_H)


def extract_between(src: str, start: str, end: str) -> str:
    i = src.index(start)
    j = src.index(end, i)
    return src[i:j]


def check_tree(ktree: Path) -> None:
    kg_int = (ktree / KG_INT_H).read_text()
    kg_c = (ktree / KG_C).read_text()
    pcd_kg = (ktree / PCD_KG_C).read_text()
    pcd_int = (ktree / PCD_INT_H).read_text()
    pcd_c = (ktree / PCD_C).read_text()
    pcd_h = (ktree / PCD_H).read_text()

    assert "bool custom_gec;" in kg_int, "struct keygen_scheme missing custom_gec"
    assert "keygen_scheme_clear(" in kg_int, "missing keygen_scheme_clear decl"
    assert "keygen_scheme_setup_verified(" in kg_int, "missing verified setup decl"
    assert "keygen_scheme_read_spc(" in kg_int, "missing read_spc decl"
    assert "scheme->custom_gec" in kg_c, "keygen_scheme_setup missing custom_gec branch"
    assert "fman_pcd_kg_port_add_class_scheme(" in pcd_kg, "missing add_class_scheme"
    assert "fman_pcd_kg_port_del_class_scheme(" in pcd_kg, "missing del_class_scheme"
    assert "fman_pcd_kg_port_add_class_scheme(" in pcd_int, "missing internal decl"
    assert "FMAN_PCD_FE_MAX_CLASSES" in pcd_h, "missing FMAN_PCD_FE_MAX_CLASSES"
    assert "fman_pcd_fe_class_attach(" in pcd_h, "missing fman_pcd_fe_class_attach"
    assert "fman_pcd_fe_class_detach(" in pcd_h, "missing fman_pcd_fe_class_detach"
    assert "fman_pcd_fe_class_stats(" in pcd_h, "missing fman_pcd_fe_class_stats"
    assert "fman_pcd_fe_class_flow_del_identified(" in pcd_h, "missing class flow del"
    assert "fman_pcd_fe_class_flow_get_stats_identified(" in pcd_h, "missing class flow stats"
    assert "fman_pcd_ehash_table_by_index" not in extract_between(
        pcd_c, "int fman_pcd_fe_flow_add(", "EXPORT_SYMBOL_GPL(fman_pcd_fe_flow_add);"
    ), "fman_pcd_fe_flow_add must not fall back to global table_by_index"

    # Extract and compile the real F-323 KG + PCD dispatch bodies under ASan/UBSan
    kg_body = extract_between(
        pcd_kg, "/* F-323-KG-BEGIN */", "/* F-323-KG-END */"
    )
    pcd_body = extract_between(
        pcd_c, "/* F-323-PCD-BEGIN */", "/* F-323-PCD-END */"
    )
    with tempfile.TemporaryDirectory(prefix="fman-g4-") as td:
        td_p = Path(td)
        (td_p / "g4_kg_body.h").write_text(kg_body)
        (td_p / "g4_pcd_body.h").write_text(pcd_body)
        binary = td_p / "fman_g4_dispatch"
        subprocess.run(
            [
                "cc", "-std=gnu11", "-Wall", "-Wextra", "-Werror",
                "-Wno-unused-parameter", "-Wno-unused-function",
                "-fsanitize=address,undefined", "-fno-sanitize-recover=all",
                "-I", str(td_p), str(HARNESS), "-o", str(binary),
            ],
            check=True,
        )
        subprocess.run([str(binary)], check=True)


def main() -> int:
    if len(sys.argv) not in (2, 3):
        print(f"usage: {sys.argv[0]} <kernel-tree> [--check-only]", file=sys.stderr)
        return 2
    ktree = Path(sys.argv[1])
    for rel in FILES:
        if not (ktree / rel).is_file():
            print(f"FATAL: {ktree / rel} missing", file=sys.stderr)
            return 2

    if "--check-only" in sys.argv:
        check_tree(ktree)
        print("PASS test-f323-fixup (--check-only)")
        return 0

    if "F-323" in (ktree / PCD_C).read_text():
        check_tree(ktree)
        with tempfile.TemporaryDirectory(prefix="f323-idem-") as td:
            td_p = Path(td)
            for rel in FILES:
                dst = td_p / rel
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(ktree / rel, dst)
            r = subprocess.run([sys.executable, str(F323)], cwd=td_p,
                               capture_output=True, text=True)
            assert r.returncode == 0, f"F_323 idempotency failed:\n{r.stdout}\n{r.stderr}"
            check_tree(td_p)
        print("PASS test-f323-fixup (already applied + idempotent + ASan/UBSan)")
        return 0

    with tempfile.TemporaryDirectory(prefix="f323-apply-") as td:
        td_p = Path(td)
        for rel in FILES:
            dst = td_p / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(ktree / rel, dst)
        r = subprocess.run([sys.executable, str(F323)], cwd=td_p,
                           capture_output=True, text=True)
        assert r.returncode == 0, f"F_323 apply failed:\n{r.stdout}\n{r.stderr}"
        r2 = subprocess.run([sys.executable, str(F323)], cwd=td_p,
                            capture_output=True, text=True)
        assert r2.returncode == 0, f"F_323 idempotency failed:\n{r2.stdout}\n{r2.stderr}"
        check_tree(td_p)
    print("PASS test-f323-fixup (apply + idempotent + ASan/UBSan)")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (AssertionError, ValueError, subprocess.CalledProcessError) as exc:
        print(f"FAIL test-f323-fixup: {exc}", file=sys.stderr)
        sys.exit(1)
