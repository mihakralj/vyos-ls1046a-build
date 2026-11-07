#!/usr/bin/env python3
"""Exercise generated FE workspace publication before hardware dispatch."""

import os
import re
import shlex
import subprocess
import sys
import tempfile
from pathlib import Path


def function(source, name):
    match = re.search(
        rf"(?m)^(?:static )?(?:int|void) {re.escape(name)}\([^;]*?\n\{{\n",
        source,
    )
    if not match:
        raise ValueError(f"function {name} not found")
    return source[match.start():source.index("\n}\n", match.end()) + 3]


def main():
    if len(sys.argv) != 2:
        sys.exit("usage: test-fman-arm-workspace.py <kernel-source-dir>")
    source = (
        Path(sys.argv[1]) / "drivers/net/ethernet/freescale/fman/fman_pcd.c"
    ).read_text()
    names = (
        "fman_pcd_fe_port_set",
        "fman_pcd_fe_fault_arm",
        "__fman_pcd_fe_arm_engage",
        "fman_pcd_fe_retained_check",
        "__fman_pcd_fe_engage_profile_locked",
    )
    root = Path(__file__).resolve().parents[1]
    with tempfile.TemporaryDirectory(prefix="fman-arm-workspace-") as directory:
        tmp = Path(directory)
        (tmp / "arm_workspace_body.h").write_text(
            "\n".join(function(source, name) for name in names)
        )
        binary = tmp / "arm_workspace"
        subprocess.run(
            [
                *shlex.split(os.environ.get("HOSTCC", "cc")),
                "-std=gnu11", "-O2", "-Wall", "-Wextra", "-Werror",
                "-Wno-unused-function", "-Wno-unused-parameter",
                "-I", str(tmp),
                str(root / "tests/dut/fman_arm_workspace.c"), "-o", str(binary),
            ],
            check=True,
        )
        subprocess.run([str(binary)], check=True, timeout=30)


if __name__ == "__main__":
    main()
