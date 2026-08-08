#!/usr/bin/env bash
# build-fman-sleigh.sh — compile + install the fman-risc SLEIGH module into Ghidra.
# Source of truth is decomp/ghidra/fman-risc/ (repo); this copies it into the
# Ghidra install and compiles the .slaspec with the ARM64 sleigh binary.
set -euo pipefail
GH=/opt/ghidra_11.3.2_PUBLIC
SRC="$(cd "$(dirname "$0")/.." && pwd)/ghidra/fman-risc"
DST="$GH/Ghidra/Processors/fman-risc"
SLEIGH="$GH/Ghidra/Features/Decompiler/os/linux_arm_64/sleigh"

sudo mkdir -p "$DST/data/languages"
sudo cp "$SRC/Module.manifest" "$DST/"
sudo cp "$SRC"/data/languages/fman-risc.{slaspec,pspec,cspec,ldefs} "$DST/data/languages/"
echo "compiling $DST/data/languages/fman-risc.slaspec"
sudo "$SLEIGH" "$DST/data/languages/fman-risc.slaspec" "$DST/data/languages/fman-risc.sla"
echo "installed: $DST"
ls -l "$DST/data/languages/"
