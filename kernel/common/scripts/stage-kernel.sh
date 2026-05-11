#!/usr/bin/env bash
# stage-kernel.sh — produce a fully patched + configured kernel tree for one flavor.
#
# Per plans/archive/INTEGRATION-PLAN.md §3.4 + §3.5 (historical merge plan).
# of the this repo's bin/ci-setup-kernel.sh. It does NOT touch the this repo's
# legacy ci-setup-kernel.sh — the legacy script is what the existing CI uses
# today; this script is the new path for FLAVOR builds (PR 3+).
#
# Steps:
#   1. Ensure kernel source extracted at $WORK_DIR/linux-$KVER (uses fetch-kernel.sh)
#   2. Apply patches in plan §3.4 order:
#        common/vyos → common/board → common/fixes
#        flavors/$FLAVOR/patches/{ask,fixes,}
#   3. Stage SDK source overlay (FLAVOR=ask only): kernel/flavors/ask/sdk-sources/
#   4. Stage kernel/common/files/ artefacts (lp5812 driver source, fsl_fmd_shim.c)
#      under their target paths in the kernel tree (matches legacy ci-setup-kernel.sh
#      injection block, but done here at staging time — not at build time).
#   5. Run inline Python patchers from kernel/common/files/inject/ against
#      whatever live source files they target (phylink, dpaa xdp queue index,
#      xhci ls1046a quirks). Best-effort: print warning on failure but do not
#      abort — these are runtime-conditional fixups inherited from the legacy
#      injection block in bin/ci-setup-kernel.sh.
#   6. Concatenate defconfig fragments per plan §3.5 into $KSRC/.config and
#      run `make ARCH=arm64 CROSS_COMPILE=aarch64-linux-gnu- olddefconfig`.
#
# Usage:
#   bash kernel/common/scripts/stage-kernel.sh --flavor default
#   bash kernel/common/scripts/stage-kernel.sh --flavor ask
#   FLAVOR=vpp bash kernel/common/scripts/stage-kernel.sh
#
# Optional env:
#   ARCH=arm64                              (default)
#   CROSS_COMPILE=aarch64-linux-gnu-        (default; auto-empty if uname -m == aarch64)
#   STAGE_ONLY=1                            skip make olddefconfig (just produce .config)
#
# Exit codes:
#   0  staging succeeded; tree at $WORK_DIR/linux-$KVER ready for `make`
#   1  any step failed

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"
export REPO_ROOT
export SCRIPTS_DIR="$SCRIPT_DIR"
export WORK_DIR="${REPO_ROOT}/work"
mkdir -p "$WORK_DIR"

# shellcheck source=common.sh
source "$SCRIPT_DIR/common.sh"
# common.sh re-exports SCRIPTS_DIR/WORK_DIR; restore in-tree-layout values.
export SCRIPTS_DIR="$SCRIPT_DIR"
export WORK_DIR="$REPO_ROOT/work"

# ── Pin resolution ────────────────────────────────────────────────────
# 1. Load fallback defaults from versions.lock
[[ -f "$REPO_ROOT/versions.lock" ]] && . "$REPO_ROOT/versions.lock"
# 2. Auto-track upstream vyos-1x: read kernel_version from
#    vyos-build/data/defaults.toml when that checkout is present.
#    sync-kernel-version.sh respects an existing KERNEL_VERSION env var.
# shellcheck source=sync-kernel-version.sh
. "$SCRIPT_DIR/sync-kernel-version.sh"
echo "  kernel pin: $KERNEL_VERSION (series $KERNEL_SERIES, source: $_SOURCE)"

need git find python3 make

# ── Argument parsing ──────────────────────────────────────────────────
FLAVOR="${FLAVOR:-default}"
VERSION_ARG=""
STAGE_ONLY="${STAGE_ONLY:-0}"
while (( $# )); do
    case "$1" in
        --flavor)   FLAVOR="$2"; shift 2 ;;
        --flavor=*) FLAVOR="${1#--flavor=}"; shift ;;
        --stage-only) STAGE_ONLY=1; shift ;;
        -h|--help)  sed -n '1,40p' "$0"; exit 0 ;;
        *) VERSION_ARG="$1"; shift ;;
    esac
done

case "$FLAVOR" in
    default|ask|vpp) : ;;
    *) err "FLAVOR must be one of: default | ask | vpp (got '$FLAVOR')" ;;
esac

# Default cross-compile (skipped when running natively on arm64)
ARCH="${ARCH:-arm64}"
if [[ "$(uname -m)" == "aarch64" ]]; then
    CROSS_COMPILE="${CROSS_COMPILE:-}"
else
    CROSS_COMPILE="${CROSS_COMPILE:-aarch64-linux-gnu-}"
fi
export ARCH CROSS_COMPILE

# ── Layout invariants ──────────────────────────────────────────────────
COMMON_DIR="$REPO_ROOT/kernel/common"
FLAVOR_DIR="$REPO_ROOT/kernel/flavors/$FLAVOR"
SDK_DIR="$REPO_ROOT/kernel/flavors/ask/sdk-sources"
FILES_DIR="$COMMON_DIR/files"
INJECT_DIR="$FILES_DIR/inject"

[[ -d "$COMMON_DIR/patches" ]] || err "missing $COMMON_DIR/patches"
# kernel/flavors/$FLAVOR/ is optional: default and vpp share the upstream-tracked
# kernel from kernel/common/ alone (their per-flavor dirs were dead-code
# placeholders, removed 2026-05-11). Only ask is mandatory.
if [[ ! -d "$FLAVOR_DIR" && "$FLAVOR" == "ask" ]]; then
    err "missing $FLAVOR_DIR (required for FLAVOR=ask)"
fi

# ── Kernel source ──────────────────────────────────────────────────────
if [[ -n "$VERSION_ARG" || ! -f "$WORK_DIR/.kernel-version" ]]; then
    "$SCRIPTS_DIR/fetch-kernel.sh" $VERSION_ARG
fi
KVER=$(cat "$WORK_DIR/.kernel-version")
KSRC="$WORK_DIR/linux-$KVER"
TARBALL="$WORK_DIR/linux-$KVER.tar.xz"

# Always start from a fresh extraction so re-runs are deterministic.
if [[ -f "$TARBALL" ]]; then
    info "extracting fresh kernel tree (linux-$KVER)…"
    rm -rf "$KSRC"
    tar -xf "$TARBALL" -C "$WORK_DIR/"
fi
[[ -d "$KSRC" ]] || err "kernel source missing: $KSRC"

# ── Patch application (plan §3.4) ─────────────────────────────────────
PATCHES=()
for sub in vyos board fixes; do
    d="$COMMON_DIR/patches/$sub"
    [[ -d "$d" ]] || continue
    while IFS= read -r p; do PATCHES+=("$p"); done \
        < <(find "$d" -maxdepth 1 -type f -name '*.patch' | sort)
done
if [[ -d "$FLAVOR_DIR" ]]; then
    for sub in ask fixes ""; do
        d="$FLAVOR_DIR/patches${sub:+/$sub}"
        [[ -d "$d" ]] || continue
        while IFS= read -r p; do PATCHES+=("$p"); done \
            < <(find "$d" -maxdepth 1 -type f -name '*.patch' | sort)
    done
fi
(( ${#PATCHES[@]} )) || err "no patches found for flavor=$FLAVOR"

# Turn $KSRC into a throwaway git repo so that `git apply --3way` has access to
# the pristine blobs for fallback 3-way merge. This is the same strategy used
# by bin/ci-setup-vyos1x.sh / bin/ci-setup-vyos-build.sh on their upstream
# clones. We commit per-bucket so a later patch sees previous patches' context.
if [[ ! -d "$KSRC/.git" ]]; then
    info "initializing throwaway git repo in $KSRC for --3way fallback…"
    ( cd "$KSRC" && git init -q && git config user.email ci@local && \
        git config user.name ci && git config gc.auto 0 && \
        git add -A && git commit -qm "linux-$KVER pristine baseline" )
fi

# Drop .gitattributes so Mergiraf is wired as the merge driver for source
# files when --3way needs to fall back to a real 3-way merge.
cat > "$KSRC/.gitattributes" <<'GITATTR'
*.c     merge=mergiraf
*.h     merge=mergiraf
*.py    merge=mergiraf
*.json  merge=mergiraf
*.yml   merge=mergiraf
*.yaml  merge=mergiraf
*.toml  merge=mergiraf
*.xml   merge=mergiraf
GITATTR

info "applying ${#PATCHES[@]} patches to linux-$KVER…"
for p in "${PATCHES[@]}"; do
    name="$(basename "$(dirname "$p")")/$(basename "$p")"
    if git -C "$KSRC" apply --3way --whitespace=nowarn "$p" 2>&1; then
        printf '   ✓ %s\n' "$name"
        ( cd "$KSRC" && git add -A && git commit -qm "$name" --allow-empty )
    else
        # Fall back to --reject to surface failing hunks for the maintainer.
        git -C "$KSRC" apply --reject --whitespace=nowarn "$p" 2>&1 || true
        err "patch failed: $name (see *.rej under $KSRC)"
    fi
done

# ── SDK source overlay (ASK only) ─────────────────────────────────────
if [[ "$FLAVOR" == "ask" && -d "$SDK_DIR" ]]; then
    info "overlaying SDK sources (266 files) onto kernel tree…"
    (cd "$SDK_DIR" && find . -type f -print0) | \
        while IFS= read -r -d '' f; do
            f="${f#./}"
            mkdir -p "$KSRC/$(dirname "$f")"
            cp -f "$SDK_DIR/$f" "$KSRC/$f"
        done
    ok "SDK overlay complete"
fi

# ── Inject driver source files (LP5812, FMD shim) ─────────────────────
if [[ -d "$FILES_DIR/lp5812" ]]; then
    info "injecting LP5812 LED driver source…"
    LP_DST="$KSRC/drivers/leds/lp5812"
    mkdir -p "$LP_DST"
    cp -f "$FILES_DIR/lp5812/leds-lp5812.c" "$LP_DST/"
    cp -f "$FILES_DIR/lp5812/leds-lp5812.h" "$LP_DST/"
    # Minimal Kconfig+Makefile for the new dir (the 095 patch wires the parent
    # Kconfig+Makefile entries; this provides the leaf files).
    cat > "$LP_DST/Kconfig" <<'KEOF'
config LEDS_LP5812
	tristate "LED Support for TI LP5812 I2C LED controller"
	depends on LEDS_CLASS && I2C && LEDS_CLASS_MULTICOLOR
	help
	  TI LP5812 12-channel I2C LED controller with per-LED
	  analog and PWM dimming.
KEOF
    cat > "$LP_DST/Makefile" <<'MEOF'
obj-$(CONFIG_LEDS_LP5812) += leds-lp5812.o
MEOF
fi

if [[ -f "$FILES_DIR/fsl_fmd_shim.c" ]]; then
    info "injecting FMD shim chardev…"
    FMD_DST="$KSRC/drivers/soc/fsl/fmd_shim"
    mkdir -p "$FMD_DST"
    cp -f "$FILES_DIR/fsl_fmd_shim.c" "$FMD_DST/"
    cat > "$FMD_DST/Kconfig" <<'KEOF'
config FSL_FMD_SHIM
	bool "FMD Shim chardev for DPDK fmlib FMan RSS"
	depends on FSL_FMAN
	default y
	help
	  Minimal character device driver that creates /dev/fm0 etc.
KEOF
    cat > "$FMD_DST/Makefile" <<'MEOF'
obj-$(CONFIG_FSL_FMD_SHIM) += fsl_fmd_shim.o
MEOF
    # Hook into parent Kconfig and Makefile if not already present.
    grep -q fmd_shim "$KSRC/drivers/soc/fsl/Kconfig" 2>/dev/null \
        || echo 'source "drivers/soc/fsl/fmd_shim/Kconfig"' >> "$KSRC/drivers/soc/fsl/Kconfig"
    grep -q fmd_shim "$KSRC/drivers/soc/fsl/Makefile" 2>/dev/null \
        || echo 'obj-$(CONFIG_FSL_FMD_SHIM) += fmd_shim/' >> "$KSRC/drivers/soc/fsl/Makefile"
fi

# ── Inline Python patchers — RETIRED ────────────────────────────────────
# Previously this script ran 3 inline Python patchers (patch-phylink.py,
# patch-dpaa-xdp-queue-index.py, patch-xhci-ls1046a-quirks.py) against the
# kernel tree as a "best-effort" fallback. As of the patch-migration-3way
# work, those have been converted to proper unified-diff patches that ship
# under kernel/common/patches/board/ (4005/4006/4007). They are now applied
# by the standard PATCHES loop above, with the same --3way safety as every
# other patch. The patch-dpaa-probe-fix.py SDK patcher likewise became
# kernel/flavors/ask/patches/fixes/4008-sdk-dpaa-probe-fix.patch.

# ── Defconfig assembly (plan §3.5) ────────────────────────────────────
info "assembling .config from defconfig fragments…"
DEFCONFIG="$KSRC/.config"
shopt -s nullglob
{
    [[ -f "$COMMON_DIR/vyos-base/arm64/vyos_defconfig" ]] && cat "$COMMON_DIR/vyos-base/arm64/vyos_defconfig"
    for f in "$COMMON_DIR/vyos-base"/*.config; do cat "$f"; done
    for f in "$COMMON_DIR/kernel-config"/*.config; do cat "$f"; done
    for f in "$FLAVOR_DIR/kernel-config"/*.config; do cat "$f"; done
    # ASK rule: ask.config wins last (per AGENTS.md "SDK DPAA config must apply LAST").
    [[ "$FLAVOR" == "ask" && -f "$FLAVOR_DIR/ask.config" ]] && cat "$FLAVOR_DIR/ask.config"
} > "$DEFCONFIG"
shopt -u nullglob
ok "wrote $DEFCONFIG ($(wc -l < "$DEFCONFIG") lines)"

if [[ "$STAGE_ONLY" == "1" ]]; then
    info "STAGE_ONLY=1 — skipping make olddefconfig"
    ok "stage-kernel done (flavor=$FLAVOR, kernel=linux-$KVER, no resolve)"
    exit 0
fi

info "running make olddefconfig…"
( cd "$KSRC" && make ARCH="$ARCH" ${CROSS_COMPILE:+CROSS_COMPILE="$CROSS_COMPILE"} olddefconfig ) >/dev/null 2>&1 \
    || warn "make olddefconfig exited non-zero (config may have unresolved symbols)"

ok "stage-kernel done (flavor=$FLAVOR, kernel=linux-$KVER)"
echo "Tree ready at: $KSRC"
echo "Next: cd $KSRC && make ARCH=$ARCH ${CROSS_COMPILE:+CROSS_COMPILE=$CROSS_COMPILE} -j\$(nproc) Image modules"