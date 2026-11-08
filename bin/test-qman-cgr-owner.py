#!/usr/bin/env python3
"""Test source-extracted QMan CGR portal ownership on the host."""

import os
from pathlib import Path
import re
import shlex
import subprocess
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[1]
HARNESS = ROOT / "tests/dut/qman_cgr_owner.c"


def function(source, prefix):
    match = re.search(rf"(?m)^{re.escape(prefix)}\([^;]*?\n\{{\n", source)
    if not match:
        raise ValueError(f"{prefix}: function not found")
    return source[match.start():source.index("\n}\n", match.end()) + 3]


def main():
    if len(sys.argv) != 2:
        print(f"Usage: {sys.argv[0]} KERNEL_SOURCE", file=sys.stderr)
        return 2
    try:
        kernel = Path(sys.argv[1])
        qman = (kernel / "drivers/soc/fsl/qbman/qman.c").read_text()
        header = (kernel / "include/soc/fsl/qman.h").read_text()
        if "cgr->portal_cpu" in qman and "int portal_cpu;" not in header:
            raise ValueError("CGR portal CPU field missing from header")
        params = re.search(r"(?ms)^struct update_cgr_params \{\n.*?^\};\n", qman)
        if not params:
            raise ValueError("CGR update parameter structure not found")
        delete_params = re.search(r"(?ms)^struct delete_cgr_params \{\n.*?^\};\n", qman)
        checked_delete = "int qman_delete_cgr_safe(" in qman
        if checked_delete != bool(delete_params):
            raise ValueError("CGR delete parameter structure or signature changed")
        body = "\n".join((
            function(qman, "int qman_create_cgr"),
            function(qman, "static struct qman_portal *qman_cgr_get_affine_portal"),
            function(qman, "int qman_delete_cgr"),
            delete_params.group() if delete_params else "",
            function(qman, "static void qman_delete_cgr_smp_call"),
            function(qman, ("int" if checked_delete else "void") +
                     " qman_delete_cgr_safe"),
            function(qman, "static int qman_update_cgr"),
            params.group(),
            function(qman, "static void qman_update_cgr_smp_call"),
            function(qman, "int qman_update_cgr_safe"),
        ))
        with tempfile.TemporaryDirectory(prefix="qman-cgr-owner-") as directory:
            path = Path(directory)
            (path / "qman_cgr_owner_body.h").write_text(body)
            binary = path / "qman_cgr_owner"
            subprocess.run(
                shlex.split(os.environ.get("HOSTCC", "cc")) + [
                    "-std=gnu11", "-Wall", "-Wextra", "-Werror",
                    "-Wno-unused-parameter", "-I", str(path),
                    str(HARNESS), "-o", str(binary),
                ],
                check=True,
            )
            result = subprocess.run([str(binary)], check=False, timeout=30)
            if result.returncode:
                return result.returncode
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        print(f"QMan CGR ownership test failed: {error}", file=sys.stderr)
        return 2
    print("Host model only: no QMan hardware or CPU hotplug was exercised.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
