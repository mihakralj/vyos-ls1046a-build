#!/usr/bin/env python3
"""Exercise the generated RX CC-base SYNC gate on the host."""

import os
from pathlib import Path
import re
import shlex
import subprocess
import sys
import tempfile


def main():
    if len(sys.argv) != 2:
        sys.exit("usage: test-fman-cc-sync-gate.py <kernel-source-dir>")
    source = (Path(sys.argv[1]) /
              "drivers/net/ethernet/freescale/fman/fman_port.c").read_text()
    bodies = []
    for name in ("fman_port_set_cc_base", "fman_port_set_oh_cc_base"):
        match = re.search(rf"(?m)^int {name}\([^;]*?\n\{{\n", source)
        if not match:
            sys.exit(f"FAIL: {name} not found")
        bodies.append(source[match.start():source.index("\n}\n", match.end()) + 3])
    harness = (Path(__file__).resolve().parents[1] /
               "tests/dut/fman_cc_sync_gate.c")
    compiler = shlex.split(os.environ.get("HOSTCC", "cc"))
    # T5: after F-293 the gate uses fman_fpm_extc_sync(); the harness then
    # also checks timeout accounting and concurrent arm/disarm churn.
    flags = ["-DFMAN_F293=1"] if all("fman_fpm_extc_sync(" in b for b in bodies) else []
    with tempfile.TemporaryDirectory(prefix="fman-cc-sync-gate-") as directory:
        path = Path(directory)
        (path / "fman_cc_sync_body.h").write_text("\n".join(bodies))
        binary = path / "fman-cc-sync-gate"
        subprocess.run(
            [*compiler, *flags, "-std=gnu11", "-O2", "-Wall", "-Wextra", "-Werror",
             "-Wno-unused-parameter", "-pthread", "-I", str(path), str(harness),
             "-o", str(binary)],
            check=True,
        )
        subprocess.run([str(binary)], check=True, timeout=30)


if __name__ == "__main__":
    main()
