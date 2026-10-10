#!/usr/bin/env python3
"""Build the DUT receive probe against the just-built kernel and moal headers."""
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys

tests = Path(__file__).resolve().parent
repo = tests.parents[2]
work = repo / "meta-ask/build/tmp"
driver = work / "work/ask_ls1046a-oe-linux/nxp-mwifiex/git"
source = driver / "git"
dest = Path(sys.argv[1]).resolve()
dest.mkdir(parents=True, exist_ok=True)
shutil.copyfile(tests / "test.c", dest / "ask_mwifiex_rx_test.c")
# Match the loaded driver's structure layout, including every feature flag.
command = (source / "mlinux/.moal_shim.o.cmd").read_text().splitlines()[0].split(":=", 1)[1]
defines = [arg for arg in shlex.split(command) if arg.startswith("-D")
           and not any(word in arg for word in ("KBUILD", "MODULE", "__KERNEL__"))]
flags = defines + ["-I" + str(source), "-I" + str(source / "mlan"),
                   "-I" + str(source / "mlinux"), "-include", "linux/limits.h"]
(dest / "Makefile").write_text("obj-m := ask_mwifiex_rx_test.o\nccflags-y := " + shlex.join(flags) + "\n")
native = driver / "recipe-sysroot-native/usr/bin"
env = {**os.environ, "PATH": str(native) + ":" + os.environ["PATH"]}
subprocess.run(["make", "-C", str(work / "work-shared/ask-ls1046a/kernel-build-artifacts"),
                "M=" + str(dest), "ARCH=arm64",
                "CROSS_COMPILE=" + str(native / "aarch64-oe-linux/aarch64-oe-linux-"),
                "modules"], env=env, check=True)
