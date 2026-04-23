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

### 4. Stage the Mono DTS + NXP SDK portal DTSIs into the kernel source tree.
# The board DTS `#include`s qoriq-bman-portals-sdk.dtsi and
# qoriq-qman-portals-sdk.dtsi (supplying cell-index + allocator-range nodes
# the ASK staging driver requires). Mainline kernel doesn't ship those
# DTSIs — NXP's lf-*.y trees do. We carry bit-exact copies under
# data/dtb/sdk-dtsi/ and drop them next to the board DTS so `dtc` resolves
# the includes via the same `-I $DTS_DIR` search path as the base DTSIs.
DTS_DIR="$LINUX_SRC/arch/arm64/boot/dts/freescale"
cp data/dtb/sdk-dtsi/qoriq-bman-portals-sdk.dtsi "$DTS_DIR/"
cp data/dtb/sdk-dtsi/qoriq-qman-portals-sdk.dtsi "$DTS_DIR/"
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

### 6. Verify the SDK DTSI payload made it in.
# Expected (post-#include of qoriq-{bman,qman}-portals-sdk.dtsi):
#   cell-index         >= 20  (10 BMan portals + 10 QMan portals, both numbered 0..9)
#   bpid-range         >= 1   (bman-bpids@0)
#   fqid-range         >= 2   (qman-fqids@0 and @1)
#   pool-channel-range >= 1   (qman-pools@0)
#   cgrid-range        >= 1   (qman-cgrids@0)
DECOMP=$(dtc -I dtb -O dts "$DTB_OUT" 2>/dev/null)
CELL_IDX=$(echo "$DECOMP"   | grep -c 'cell-index')
BPID=$(echo "$DECOMP"       | grep -c 'bpid-range')
FQID=$(echo "$DECOMP"       | grep -c 'fqid-range')
POOLCH=$(echo "$DECOMP"     | grep -c 'pool-channel-range')
CGRID=$(echo "$DECOMP"      | grep -c 'cgrid-range')
FAIL=0
[ "$CELL_IDX" -lt 20 ] && { echo "ERROR: cell-index count $CELL_IDX < 20";               FAIL=1; }
[ "$BPID"     -lt 1  ] && { echo "ERROR: bpid-range count $BPID < 1";                    FAIL=1; }
[ "$FQID"     -lt 2  ] && { echo "ERROR: fqid-range count $FQID < 2";                    FAIL=1; }
[ "$POOLCH"   -lt 1  ] && { echo "ERROR: pool-channel-range count $POOLCH < 1";          FAIL=1; }
[ "$CGRID"    -lt 1  ] && { echo "ERROR: cgrid-range count $CGRID < 1";                  FAIL=1; }
if [ "$FAIL" -ne 0 ]; then
    echo "ERROR: compiled DTB is missing expected NXP SDK portal DTSI payload"
    echo "       (did bin/ci-compile-mono-dtb.sh copy data/dtb/sdk-dtsi/*.dtsi to $DTS_DIR?"
    echo "        did mono-gateway-dk.dts #include qoriq-{bman,qman}-portals-sdk.dtsi?)"
    exit 1
fi

echo "### Mono DTB compiled from DTS:"
ls -l "$DTB_OUT"
echo "### cell-index=$CELL_IDX  bpid-range=$BPID  fqid-range=$FQID  pool-channel-range=$POOLCH  cgrid-range=$CGRID"
