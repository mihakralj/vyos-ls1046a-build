#!/usr/bin/env python3
"""Exercise the generated KeyGen indirect-window code with host fault injection."""

import os
from pathlib import Path
import re
import shlex
import subprocess
import sys
import tempfile


def section(source, start, end):
    if source.count(start) != 1:
        raise ValueError(f"expected one {start!r}")
    first = source.index(start)
    last = source.index(end, first)
    return source[first:last]


def function(source, name):
    match = re.search(
        rf"(?m)^(?:static )?(?:int|void|u32) {re.escape(name)}\([^;]*?\n\{{\n",
        source,
    )
    if not match:
        raise ValueError(f"function {name} not found")
    start = match.start()
    end = source.index("\n}\n", match.end()) + 3
    return source[start:end]


def main():
    if len(sys.argv) != 2:
        sys.exit("usage: test-keygen-window.py <kernel-source-dir>")
    root = Path(sys.argv[1])
    directory = root / "drivers/net/ethernet/freescale/fman"
    source = (directory / "fman_keygen.c").read_text()
    internal = (directory / "fman_keygen_internal.h").read_text()
    definitions = section(
        source, "struct fman_kg_scheme_regs {", "\n\n/* keygen_write_ar_wait"
    )
    definitions += section(
        internal, "struct keygen_scheme {", "\n/* Demoted from file-static"
    )
    names = (
        "keygen_indirect_ready",
        "keygen_write_ar_wait",
        "build_ar_scheme",
        "build_ar_bind_scheme",
        "keygen_write_sp",
        "build_ar_bind_cls_plan",
        "keygen_write_cpp",
        "keygen_write_scheme",
    )
    body = "\n".join(function(source, name) for name in names)
    body += section(source, "static struct keygen_scheme *get_scheme(",
                    "\n/* keygen_bind_port_to_schemes")
    body += "\n".join(
        function(source, name)
        for name in ("keygen_bind_port_to_schemes", "keygen_cls_plan0_passall")
    )
    body += section(source, "struct fman_keygen *keygen_init(",
                    "\nEXPORT_SYMBOL(keygen_init);")

    harness = Path(__file__).resolve().parents[1] / "tests/dut/fman_keygen_window.c"
    compiler = shlex.split(os.environ.get("HOSTCC", "cc"))
    with tempfile.TemporaryDirectory(prefix="fman-keygen-window-") as tmp:
        tmp = Path(tmp)
        (tmp / "keygen_types.h").write_text(definitions)
        (tmp / "keygen_body.h").write_text(body)
        binary = tmp / "keygen-window"
        subprocess.run(
            [*compiler, "-std=gnu11", "-O2", "-Wall", "-Wextra", "-Werror",
             "-pthread", "-I", str(tmp), str(harness), "-o", str(binary)],
            check=True,
        )
        subprocess.run([str(binary)], check=True, timeout=30)


if __name__ == "__main__":
    main()
