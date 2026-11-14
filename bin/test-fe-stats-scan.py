#!/usr/bin/env python3
"""test-fe-stats-scan.py <kernel-source-dir>: F-315 host check.

Applies bin/kernel-fixups/F_315.py to a copy of fman_pcd.c and fman_pcd.h from the given (F-312/F-314-applied)
tree, checks it is idempotent, then compiles the generated fman_pcd_fe_stats_scan() into tests/dut/fman_fe_stats_scan.c
under ASan/UBSan: owner filter, big-endian counters, record budget and bucket cap per lock hold, resume across calls,
a cursor past a smaller table's last bucket and a table freed between calls (no out-of-bounds bidx read).
The generated function is also put through bin/test-fe-stats-lock.py's rule: nothing sleeps or frees under the lock.
"""
import os
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PCD = "drivers/net/ethernet/freescale/fman/fman_pcd.c"
HDR = "include/linux/fsl/fman_pcd.h"


def main():
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    with tempfile.TemporaryDirectory() as d:
        for rel in (PCD, HDR):
            os.makedirs(os.path.join(d, os.path.dirname(rel)), exist_ok=True)
            shutil.copy(os.path.join(sys.argv[1], rel), os.path.join(d, rel))
        fix = os.path.join(ROOT, "bin/kernel-fixups/F_315.py")
        first = subprocess.run([sys.executable, fix], cwd=d, capture_output=True, text=True)
        if first.returncode != 0 or "F-315: batched" not in first.stdout:
            sys.exit(f"F-315 did not apply: {first.stdout}{first.stderr}")
        again = subprocess.run([sys.executable, fix], cwd=d, capture_output=True, text=True)
        if again.returncode != 0 or "already applied" not in again.stdout:
            sys.exit(f"F-315 not idempotent: {again.stdout}{again.stderr}")
        src = open(os.path.join(d, PCD)).read()
        if "int fman_pcd_fe_stats_scan(" not in open(os.path.join(d, HDR)).read():
            sys.exit("header declaration missing")
        start = src.index("/* F-315: batched counter read")
        end = src.index("EXPORT_SYMBOL_GPL(fman_pcd_fe_stats_scan);")
        body = src[start:end]
        for bad in ("mutex_lock", "kfree", "dma_pool_free", "fman_pcd_ehash_sync", "msleep", "writel", "iowrite"):
            if bad + "(" in body:
                sys.exit(f"generated scan calls {bad}()")
        open(os.path.join(d, "fe_stats_scan_body.h"), "w").write(body)
        exe = os.path.join(d, "scan")
        cc = os.environ.get("HOSTCC", "cc").split()
        subprocess.run([*cc, "-std=gnu11", "-Wall", "-Wextra", "-Werror", "-Wno-unused-parameter", "-g",
                        "-fsanitize=address,undefined", "-fno-sanitize-recover=all", "-I", d,
                        os.path.join(ROOT, "tests/dut/fman_fe_stats_scan.c"), "-o", exe], check=True)
        out = subprocess.run([exe], capture_output=True, text=True,
                             env={**os.environ, "ASAN_OPTIONS": "detect_leaks=0"})  # mock tables are never freed
        sys.stdout.write(out.stdout + out.stderr)
        if out.returncode != 0 or "fman_fe_stats_scan: OK" not in out.stdout:
            sys.exit("test-fe-stats-scan: FAIL")
    print("test-fe-stats-scan: OK")


if __name__ == "__main__":
    main()
