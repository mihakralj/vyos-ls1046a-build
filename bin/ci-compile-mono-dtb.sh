#!/bin/bash
# ci-compile-mono-dtb.sh — Compile board/dtb/mono-gateway-dk.dts → board/dtb/mono-gw.dtb
#
# Called by: .github/workflows/auto-build.yml "Compile Mono DTB" step
# Runs AFTER: ci-consume-ask-kernel.sh (so we know which kernel version to match)
# Runs BEFORE: ci-setup-vyos-build.sh (which copies board/dtb/mono-gw.dtb into the ISO)
#
# Why this exists:
#   board/dtb/mono-gw.dtb is a binary artefact. When only board/dtb/mono-gateway-dk.dts
#   changes on main, the committed DTB can drift — and bin/ci-setup-vyos-build.sh
#   copies the committed binary verbatim into the ISO, so DTS fixes never reach
#   the device. This script closes that drift window by always recompiling the
#   DTB from the current DTS on every ISO build.
#
# Strategy:
#   - Sparse-clone linux-stable at the tag matching the consumed ASK kernel
#     (only arch/arm64/boot/dts/freescale + include/dt-bindings + scripts/dtc).
#   - Drop board/dtb/mono-gateway-dk.dts into arch/arm64/boot/dts/freescale/.
#   - Preprocess with aarch64-linux-gnu-cpp, compile with dtc.
#   - Overwrite board/dtb/mono-gw.dtb with the fresh binary.
#
# Expects: GITHUB_WORKSPACE (optional), curl, git, dtc, aarch64-linux-gnu-cpp.
# Installs dtc + gcc-aarch64-linux-gnu if missing (Debian/Ubuntu runners).

set -euo pipefail
# Merge stderr into stdout so CI captures preprocessor / dtc diagnostics in the
# job log (gh action logs tend to drop stderr-only output in some steps).
exec 2>&1
cd "${GITHUB_WORKSPACE:-.}"

# ASK 2.0 (rewrite-in-progress): the SDK DTS overlay
# (mono-gateway-dk-sdk.dts) was deleted on the ask20 branch along with the
# rest of the ASK 1.x SDK stack. All flavors now compile the mainline
# mono-gateway-dk.dts directly. The ASK 2.0 spec keeps the mainline FMan
# driver and re-implements the fast-path in ask.ko / askd, so an SDK DTS
# overlay is no longer needed.
DTS_SRC="board/dtb/mono-gateway-dk.dts"
DTS_BASE="board/dtb/mono-gateway-dk.dts"
DTB_OUT="board/dtb/mono-gw.dtb"
WORK="work/dtb-build"
LINUX_SRC="$WORK/linux-src"

[ -f "$DTS_SRC" ]  || { echo "ERROR: $DTS_SRC not found";  exit 1; }

### 1. Determine kernel version tag.
# Read from vyos-build/data/defaults.toml (kernel_version = "6.18.28") if the
# repo is checked out, fall back to data/kernel-version (simple text file),
# fall back to whatever is hard-coded below.
KVER=""
if [ -z "$KVER" ] && [ -f vyos-build/data/defaults.toml ]; then
    KVER=$(awk -F'"' '/^kernel_version/ {print $2; exit}' vyos-build/data/defaults.toml)
fi
if [ -z "$KVER" ] && [ -f data/kernel-version ]; then
    KVER=$(tr -d '[:space:]' < data/kernel-version)
fi
KVER="${KVER:-6.18.28}"
TAG="v${KVER}"
echo "### Compiling Mono DTB against Linux $TAG"

### 2. Install tools if missing (Debian/Ubuntu runner).
need_pkg=""
command -v dtc                      >/dev/null || need_pkg+=" device-tree-compiler"
command -v aarch64-linux-gnu-cpp    >/dev/null || need_pkg+=" gcc-aarch64-linux-gnu"
command -v git                      >/dev/null || need_pkg+=" git"
if [ -n "$need_pkg" ]; then
    echo "### Installing:$need_pkg"
    apt-get update -qq
    apt-get install -y --no-install-recommends $need_pkg
fi

### 3. Sparse clone of just the DTS includes and dt-bindings at the right tag.
# Using github.com/gregkh/linux (canonical stable mirror). Sparse + blob:none
# keeps this to ~20-30 MB and well under a minute.
mkdir -p "$WORK"
if [ ! -d "$LINUX_SRC/.git" ]; then
    echo "### Sparse-cloning Linux $TAG (blob:none, sparse)"
    git clone --depth 1 --filter=blob:none --sparse \
        --branch "$TAG" \
        https://github.com/gregkh/linux.git "$LINUX_SRC"
    git -C "$LINUX_SRC" sparse-checkout set \
        arch/arm64/boot/dts/freescale \
        include/dt-bindings \
        scripts/dtc
else
    echo "### Reusing existing $LINUX_SRC"
fi

# Sanity: the base DTSIs the Mono DTS includes must be present.
echo "### Checking base DTSIs present in kernel source"
for f in fsl-ls1046a.dtsi fsl-ls1046-post.dtsi; do
    [ -f "$LINUX_SRC/arch/arm64/boot/dts/freescale/$f" ] || {
        echo "ERROR: $f not found in kernel source at $TAG"; exit 1;
    }
done
echo "### Base DTSIs OK"

### 4. Stage the Mono DTS into the kernel source tree.
DTS_DIR="$LINUX_SRC/arch/arm64/boot/dts/freescale"
echo "### Staging $DTS_BASE into $DTS_DIR"
cp "$DTS_BASE" "$DTS_DIR/mono-gateway-dk.dts"
ls -l "$DTS_DIR/mono-gateway-dk.dts"

### 5. Preprocess + compile.
echo "### Preprocessing with aarch64-linux-gnu-cpp"
PP="$WORK/mono-gateway-dk.preprocessed.dts"
aarch64-linux-gnu-cpp \
    -nostdinc \
    -I "$DTS_DIR" \
    -I "$LINUX_SRC/include" \
    -undef -D__DTS__ \
    -x assembler-with-cpp \
    "$DTS_DIR/mono-gateway-dk.dts" \
    -o "$PP"
echo "### Preprocessed DTS: $(wc -l < "$PP") lines"

echo "### Compiling with dtc"
dtc -I dts -O dtb \
    -o "$DTB_OUT" \
    "$PP"
echo "### dtc done: $(stat -c%s "$DTB_OUT") bytes"

### 6. Sanity-check the compiled DTB.
# ASK 2.0 (rewrite-in-progress): the SDK-specific verification (cell-index >= 20,
# fsl,bpool-ethernet-cfg, fsl,dpa-ethernet count, etc.) was removed on the ask20
# branch along with the SDK DTS overlay. We now only sanity-check that the DTB
# is non-trivially populated.
DTB_BYTES=$(stat -c%s "$DTB_OUT")
if [ "$DTB_BYTES" -lt 4096 ]; then
    echo "ERROR: compiled DTB is suspiciously small ($DTB_BYTES bytes < 4096)"
    exit 1
fi

echo "### Mono DTB compiled from DTS:"
ls -l "$DTB_OUT"
