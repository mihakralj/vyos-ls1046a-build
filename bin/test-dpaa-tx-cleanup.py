#!/usr/bin/env python3
"""Exercise the prepared kernel's DPAA TX cleanup against a host DMA model."""

import argparse
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[1]
HARNESS = ROOT / "tests/dut/dpaa_tx_cleanup.c"


def cleanup_function(source):
    match = re.search(
        r"(?m)^(?:static )?struct sk_buff \*dpaa_cleanup_tx_fd\([^;]*?\n\{\n",
        source,
    )
    if not match:
        raise ValueError("dpaa_cleanup_tx_fd: function not found")
    return source[match.start():source.index("\n}\n", match.end()) + 3]


def run(kernel):
    path = kernel / "drivers/net/ethernet/freescale/dpaa/dpaa_eth.c"
    body = cleanup_function(path.read_text())
    with tempfile.TemporaryDirectory(prefix="dpaa-tx-cleanup-") as directory:
        tmp = Path(directory)
        (tmp / "dpaa_tx_cleanup_body.h").write_text(body)
        binary = tmp / "dpaa_tx_cleanup"
        subprocess.run(
            shlex.split(os.environ.get("HOSTCC", "cc")) + [
                "-std=gnu11", "-Wall", "-Wextra", "-Werror",
                "-Wno-unused-parameter", "-Wno-unused-variable",
                "-I", str(tmp), str(HARNESS), "-o", str(binary),
            ],
            check=True,
        )
        subprocess.run([str(binary)], check=True, timeout=30)
    print("Host DMA model only: no hardware routing or buffer ownership proved.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("kernel_source", type=Path)
    args = parser.parse_args()
    try:
        run(args.kernel_source)
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        print(f"DPAA TX cleanup test failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
