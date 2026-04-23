#!/bin/bash
# ci-compile-mono-dtb.sh — Compile data/dtb/mono-gateway-dk.dts → data/dtb/mono-gw.dtb
#
# Called by: .github/workflows/auto-build.yml "Compile Mono DTB" step
# Runs AFTER: ci-consume-ask-kernel.sh (so we know which kernel version to match)
# Runs BEFORE: ci-setup-vyos-build.sh (which copies data/dtb/mono-gw.dtb into the ISO)
#
# Why this exists:
#   data/dtb/mono-gw.dtb is a binary artefact. When only data/dtb/mono-gateway-dk.dts
#   changes on main, the committed DTB can drift — and bin/ci-setup-vyos-build.sh
#   copies the committed binary verbatim into the ISO, so DTS fixes never reach
#   the device. This script closes that drift window by always recompiling the
#   DTB from the current DTS on every ISO build.
#
# Strategy:
#   - Sparse-clone linux-stable at the tag matching the consumed ASK kernel
#     (only arch/arm64/boot/dts/freescale + include/dt-bindings + scripts/dtc).
#   - Drop data/dtb/mono-gateway-dk.dts into arch/arm64/boot/dts/freescale/.
#   - Preprocess with aarch64-linux-gnu-cpp, compile with dtc.
#   - Overwrite data/dtb/mono-gw.dtb with the fresh binary.
#
# Expects: GITHUB_WORKSPACE (optional), curl, git, dtc, aarch64-linux-gnu-cpp.
# Installs dtc + gcc-aarch64-linux-gnu if missing (Debian/Ubuntu runners).

set -euo pipefail
cd "${GITHUB_WORKSPACE:-.}"

DTS_SRC="data/dtb/mono-gateway-dk.dts"
DTB_OUT="data/dtb/mono-gw.dtb"
WORK="work/dtb-build"
LINUX_SRC="$WORK/linux-src"

[ -f "$DTS_SRC" ] || { echo "ERROR: $DTS_SRC not found"; exit 1; }

### 1. Determine kernel version tag.
# Prefer version from ASK consumed manifest, fall back to data/ask-kernel.pin,
# fall back to data/kernel-version (simple "6.6.135" text file), fall back to
# whatever is hard-coded below.
KVER=""
if [ -f work/ask-kernel/manifest.json ]; then
    KVER=$(jq -r '.linux_version // empty' work/ask-kernel/manifest.json 2>/dev/null || true)
fi
if [ -z "$KVER" ] && [ -f data/ask-kernel.pin ]; then
    # Tag like "kernel-6.6.135-ask8" -> extract "6.6.135"
    PIN=$(tr -d '[:space:]' < data/ask-kernel.pin)
    KVER=$(echo "$PIN" | sed -n 's/^kernel-\([0-9][0-9.]*\)-ask.*/\1/p')
fi
if [ -z "$KVER" ] && [ -f data/kernel-version ]; then
    KVER=$(tr -d '[:space:]' < data/kernel-version)
fi
KVER="${KVER:-6.6.135}"
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
for f in fsl-ls1046a.dtsi fsl-ls1046-post.dtsi; do
    [ -f "$LINUX_SRC/arch/arm64/boot/dts/freescale/$f" ] || {
        echo "ERROR: $f not found in kernel source at $TAG"; exit 1;
    }
done

### 4. Stage the Mono DTS into the kernel source tree.
DTS_DIR="$LINUX_SRC/arch/arm64/boot/dts/freescale"
cp "$DTS_SRC" "$DTS_DIR/mono-gateway-dk.dts"

### 5. Preprocess + compile.
PP="$WORK/mono-gateway-dk.preprocessed.dts"
aarch64-linux-gnu-cpp \
    -nostdinc \
    -I "$DTS_DIR" \
    -I "$LINUX_SRC/include" \
    -undef -D__DTS__ \
    -x assembler-with-cpp \
    "$DTS_DIR/mono-gateway-dk.dts" \
    -o "$PP"

dtc -q -I dts -O dtb \
    -o "$DTB_OUT" \
    "$PP"

### 6. Verify the overlay made it in.
BP=$(dtc -I dtb -O dts "$DTB_OUT" 2>/dev/null | grep -c 'cell-index')
BPID=$(dtc -I dtb -O dts "$DTB_OUT" 2>/dev/null | grep -c 'bpid-range')
if [ "$BP" -lt 10 ] || [ "$BPID" -lt 1 ]; then
    echo "ERROR: compiled DTB is missing expected BMan overlay"
    echo "       cell-index occurrences: $BP (want >= 10)"
    echo "       bpid-range occurrences: $BPID (want >= 1)"
    exit 1
fi

echo "### Mono DTB compiled from DTS:"
ls -l "$DTB_OUT"
echo "### cell-index count: $BP   bpid-range count: $BPID"