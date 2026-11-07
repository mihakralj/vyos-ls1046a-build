#!/usr/bin/env python3
"""Fault-test the source-extracted QMan CGR update wrapper on the host."""

import argparse
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[1]
HARNESS = ROOT / "tests/dut/qman_cgr_update.c"


def function(source, prefix):
    match = re.search(rf"(?m)^{re.escape(prefix)}\([^;]*?\n\{{\n", source)
    if not match:
        raise ValueError(f"{prefix}: function not found")
    return source[match.start():source.index("\n}\n", match.end()) + 3]


def run(kernel):
    qman = (kernel / "drivers/soc/fsl/qbman/qman.c").read_text()
    dpaa = (kernel / "drivers/net/ethernet/freescale/dpaa/dpaa_eth.c").read_text()
    smp = (kernel / "kernel/smp.c").read_text()
    caller = function(dpaa, "static void dpaa_eth_cgr_set_speed")
    if ("qman_update_cgr_safe(" not in caller or
            'netdev_err(net_dev, "could not update speed: %d\\n", err);' not in caller):
        raise ValueError("DPAA CGR update error handling changed")
    if "!cpu_online(cpu)" not in smp or "return -ENXIO;" not in smp:
        raise ValueError("SMP offline-CPU dispatch contract changed")
    functions = (
        "static struct qman_portal *qman_cgr_get_affine_portal",
        "static int qman_update_cgr",
        "static void qman_update_cgr_smp_call",
        "int qman_update_cgr_safe",
    )
    params = re.search(r"(?ms)^struct update_cgr_params \{\n.*?^\};\n", qman)
    if not params:
        raise ValueError("CGR update parameter structure not found")
    body = "\n".join((
        function(qman, functions[0]),
        function(qman, functions[1]),
        params.group(),
        function(qman, functions[2]),
        function(qman, functions[3]),
    ))
    with tempfile.TemporaryDirectory(prefix="qman-cgr-update-") as directory:
        path = Path(directory)
        (path / "qman_cgr_update_body.h").write_text(body)
        binary = path / "qman_cgr_update"
        subprocess.run(
            shlex.split(os.environ.get("HOSTCC", "cc")) + [
                "-std=gnu11", "-Wall", "-Wextra", "-Werror",
                "-Wno-unused-parameter", "-I", str(path),
                str(HARNESS), "-o", str(binary),
            ],
            check=True,
        )
        subprocess.run([str(binary)], check=True, timeout=30)
    print("Host model only: no CPU hotplug or QMan hardware was exercised.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("kernel_source", type=Path)
    args = parser.parse_args()
    try:
        run(args.kernel_source)
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        print(f"QMan CGR update test failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
