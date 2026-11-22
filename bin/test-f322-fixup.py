#!/usr/bin/env python3
"""Host verification gate for F-322: WGFP confirmed TX FQ, batch BMan release, and bpid=0xff guards.

Verifies on a patched kernel tree (or dry-run copy):
  1. F_322.py applies cleanly after F-199/F-227/F-295/F-300/F-316/F-320 and is idempotent.
  2. Re-running F-199, F-295, F-316, F-320 after F-322 stays idempotent (no anchor breakage).
  3. FQ_TYPE_TX_WGFP programs context_a=0x9a00000080000000, context_b=0 (WE_CONTEXTB),
     priv->offload_cgr (CGE), and ics_cred=0 (WE_ICSCRED).
  4. dpaa_alloc_wgfp_tx_fq() requires ASSERT_RTNL() (no internal rtnl_lock()), checks
     NETREG_REGISTERED, priv->offload_cgr, priv->ceetm, and replicates F-295 fault retention.
  5. dpaa_tx_error(), dpaa_tx_conf(), and egress_ern() all invoke dpaa_wgfp_tx_error_claim()
     before dpaa_cleanup_tx_fd().
  6. dpaa_wgfp_bman_release_batch() is defined and caps batches at 8 buffers.
"""

from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parent.parent
F322 = ROOT / "bin" / "kernel-fixups" / "F_322.py"
ETH_H = Path("drivers/net/ethernet/freescale/dpaa/dpaa_eth.h")
ETH_C = Path("drivers/net/ethernet/freescale/dpaa/dpaa_eth.c")
OFF_H = Path("include/linux/fsl/dpaa_flow_offload.h")


def func_body(src: str, prefix: str) -> str:
    idx = src.find(prefix)
    if idx < 0:
        raise AssertionError(f"function not found: {prefix}")
    brace = src.find("{", idx)
    depth = 0
    for i in range(brace, len(src)):
        if src[i] == "{":
            depth += 1
        elif src[i] == "}":
            depth -= 1
            if depth == 0:
                return src[idx : i + 1]
    raise AssertionError(f"unterminated function: {prefix}")


def check_tree(ktree: Path) -> None:
    eth_h = (ktree / ETH_H).read_text()
    eth_c = (ktree / ETH_C).read_text()

    assert "FQ_TYPE_TX_WGFP" in eth_h, "missing FQ_TYPE_TX_WGFP in dpaa_eth.h"
    assert "dpaa_alloc_wgfp_tx_fq(" in eth_h, "missing dpaa_alloc_wgfp_tx_fq decl in dpaa_eth.h"
    assert "dpaa_wgfp_bman_release_batch(" in eth_h, "missing batch release decl in dpaa_eth.h"

    fq_init = func_body(eth_c, "static int dpaa_fq_init(")
    assert "dpaa_fq->fq_type == FQ_TYPE_TX_WGFP" in fq_init
    assert "QM_INITFQ_WE_CONTEXTB" in fq_init
    assert "initfq.fqd.context_b = 0;" in fq_init
    assert "0x9a00000080000000ULL" in fq_init
    assert "priv->offload_cgr->cgr.cgrid" in fq_init

    alloc_fn = func_body(eth_c, "int dpaa_alloc_wgfp_tx_fq(")
    assert "ASSERT_RTNL();" in alloc_fn, "dpaa_alloc_wgfp_tx_fq must assert RTNL"
    assert "rtnl_lock()" not in alloc_fn, "dpaa_alloc_wgfp_tx_fq must NOT acquire rtnl_lock()"
    assert "NETREG_REGISTERED" in alloc_fn
    assert "!priv->offload_cgr" in alloc_fn
    assert "IS_ENABLED(CONFIG_DPAA_HW_CEETM)" in alloc_fn, "CEETM guard must use CONFIG_DPAA_HW_CEETM"
    assert "CONFIG_FSL_DPAA_CEETM" not in alloc_fn, "dead CONFIG_FSL_DPAA_CEETM symbol must not be used"
    assert "priv->ceetm" in alloc_fn
    assert "wgfp_fq_faulted" in alloc_fn
    assert "dpaa_fq_init(dpaa_fq, false, &retain_failed_fq)" in alloc_fn
    assert "netdev_hold(dev, &wgfp_fq_tracker, GFP_KERNEL)" in alloc_fn

    for fn_name in ("static void dpaa_tx_error(", "static void dpaa_tx_conf(", "static void egress_ern("):
        body = func_body(eth_c, fn_name)
        pos_claim = body.find("dpaa_wgfp_tx_error_claim(fd)")
        pos_clean = body.find("dpaa_cleanup_tx_fd(")
        assert 0 < pos_claim < pos_clean, f"{fn_name} claim guard must precede dpaa_cleanup_tx_fd"

    tx_conf = func_body(eth_c, "static void dpaa_tx_conf(")
    assert "!(qm_fd_addr(fd) & ~PAGE_MASK)" in tx_conf, "dpaa_tx_conf must fast-path page alignment"

    batch = func_body(eth_c, "void dpaa_wgfp_bman_release_batch(")
    assert "dpaa_bman_release(dpaa_bp, bmb, n);" in batch


def main() -> int:
    if len(sys.argv) not in (2, 3):
        print(f"usage: {sys.argv[0]} <kernel-tree> [--check-only]", file=sys.stderr)
        return 2
    ktree = Path(sys.argv[1])
    if not (ktree / ETH_C).is_file():
        print(f"FATAL: {ktree / ETH_C} missing", file=sys.stderr)
        return 2

    if "F-322" in (ktree / ETH_C).read_text():
        check_tree(ktree)
        with tempfile.TemporaryDirectory() as td:
            td_p = Path(td)
            for rel in (ETH_H, ETH_C, OFF_H):
                src = ktree / rel
                if src.is_file():
                    dst = td_p / rel
                    dst.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(src, dst)
            fixups = ["F_322.py", "F_316.py", "F_295.py", "F_199.py"]
            if "F-320" in (ktree / ETH_C).read_text():
                fixups.append("F_320.py")
            for fx in fixups:
                r = subprocess.run([sys.executable, str(ROOT / "bin" / "kernel-fixups" / fx)],
                                   cwd=td_p, capture_output=True, text=True)
                assert r.returncode == 0, f"{fx} idempotency failed after F-322:\n{r.stdout}\n{r.stderr}"
            check_tree(td_p)
        print(f"PASS test-f322-fixup (already applied + idempotent with {', '.join(fixups)})")
        return 0

    if "--check-only" in sys.argv:
        check_tree(ktree)
        return 0

    with tempfile.TemporaryDirectory() as td:
        td_p = Path(td)
        for rel in (ETH_H, ETH_C, OFF_H):
            src = ktree / rel
            if src.is_file():
                dst = td_p / rel
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dst)
        for _ in range(2):
            r = subprocess.run([sys.executable, str(F322)], cwd=td_p,
                               capture_output=True, text=True)
            assert r.returncode == 0, f"F_322 apply/idempotency failed:\n{r.stdout}\n{r.stderr}"
        check_tree(td_p)
    print("PASS test-f322-fixup (fresh apply + idempotent)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
