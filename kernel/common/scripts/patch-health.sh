#!/usr/bin/env bash
# patch-health.sh — FLAVOR-aware dry-run probe.
#
# Layered on the producer's patch-health.sh (./scripts/patch-health.sh in
# kernel-ls1046a-build) but adapted to the consumer's per-flavor layout
# (per plans/INTEGRATION-PLAN.md §2 + §4).
#
# Buckets, applied in this order:
#   kernel/common/patches/vyos/*.patch           (always)
#   kernel/common/patches/board/*.patch          (always)
#   kernel/common/patches/fixes/*.patch          (always)
#   kernel/flavors/${FLAVOR}/patches/ask/*.patch (only if dir exists)
#   kernel/flavors/${FLAVOR}/patches/fixes/*.patch (only if dir exists)
#   kernel/flavors/${FLAVOR}/patches/*.patch     (flavor root, optional)
#
# SDK source drop (ASK-only): kernel/flavors/ask/sdk-sources/* is staged
# into the kernel tree before dry-run so patches that target SDK files can
# validate, mirroring scripts/apply-to-tree.sh semantics.
#
# Usage:
#   ./kernel/common/scripts/patch-health.sh                           # FLAVOR=ask, work/.kernel-version
#   ./kernel/common/scripts/patch-health.sh --flavor default          # FLAVOR=default
#   ./kernel/common/scripts/patch-health.sh --flavor ask 6.6.137      # explicit kernel
#   FLAVOR=vpp ./kernel/common/scripts/patch-health.sh                # via env
#
# Exit codes:
#   0  all patches apply cleanly
#   1  at least one patch rejects, or invariant assertion failed

set -euo pipefail

# ── Derive paths (consumer layout, distinct from producer's $REPO_ROOT/scripts/common.sh) ──
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# kernel/common/scripts/ → repo root is three levels up
REPO_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"
export REPO_ROOT
export SCRIPTS_DIR="$SCRIPT_DIR"
export WORK_DIR="${REPO_ROOT}/work"
mkdir -p "$WORK_DIR"

# Source the helpers that DO travel verbatim (logging, need, fetch_state_*, etc).
# common.sh itself uses BASH_SOURCE-relative path resolution, so our local
# copy under kernel/common/scripts/common.sh is fine.
# shellcheck source=common.sh
source "$SCRIPT_DIR/common.sh"

# common.sh unconditionally re-exports SCRIPTS_DIR=$REPO_ROOT/scripts (producer
# layout). Restore consumer-layout values that we actually want to use.
export SCRIPTS_DIR="$SCRIPT_DIR"
export WORK_DIR="$REPO_ROOT/work"
mkdir -p "$WORK_DIR"

need git find jq

# ── Argument parsing ──────────────────────────────────────────────────
FLAVOR="${FLAVOR:-ask}"
VERSION_ARG=""
while (( $# )); do
    case "$1" in
        --flavor)  FLAVOR="$2"; shift 2 ;;
        --flavor=*) FLAVOR="${1#--flavor=}"; shift ;;
        --source) shift 2 ;;  # accepted-and-ignored for back-compat with producer
        -h|--help) sed -n '1,40p' "$0"; exit 0 ;;
        *) VERSION_ARG="$1"; shift ;;
    esac
done

case "$FLAVOR" in
    default|ask|vpp) : ;;
    *) err "FLAVOR must be one of: default | ask | vpp (got '$FLAVOR')" ;;
esac

# ── Layout invariants ──────────────────────────────────────────────────
COMMON_DIR="$REPO_ROOT/kernel/common"
FLAVOR_DIR="$REPO_ROOT/kernel/flavors/$FLAVOR"
SDK_DIR="$REPO_ROOT/kernel/flavors/ask/sdk-sources"  # ASK-only, present iff $FLAVOR == ask

[[ -d "$COMMON_DIR/patches" ]] \
    || err "kernel/common/patches/ not found at $COMMON_DIR/patches (consumer layout)"
[[ -d "$FLAVOR_DIR" ]] \
    || err "kernel/flavors/$FLAVOR/ not found (unknown flavor)"

# ── Ensure kernel source is present ────────────────────────────────────
if [[ -n "$VERSION_ARG" || ! -f "$WORK_DIR/.kernel-version" ]]; then
    "$SCRIPTS_DIR/fetch-kernel.sh" $VERSION_ARG
fi
KVER=$(cat "$WORK_DIR/.kernel-version")
KDIR="$WORK_DIR/linux-$KVER"
[[ -d "$KDIR" ]] || err "kernel source missing: $KDIR"

# ── Discover patches in apply order ────────────────────────────────────
PATCHES=()

# 1. Common: vyos → board → fixes
for sub in vyos board fixes; do
    d="$COMMON_DIR/patches/$sub"
    [[ -d "$d" ]] || continue
    while IFS= read -r p; do PATCHES+=("$p"); done \
        < <(find "$d" -maxdepth 1 -type f -name '*.patch' | sort)
done

# 2. Flavor-specific: ask/ → fixes/ → flavor-root
for sub in ask fixes ""; do
    d="$FLAVOR_DIR/patches${sub:+/$sub}"
    [[ -d "$d" ]] || continue
    while IFS= read -r p; do PATCHES+=("$p"); done \
        < <(find "$d" -maxdepth 1 -type f -name '*.patch' | sort)
done

(( ${#PATCHES[@]} )) || err "no patches found for flavor=$FLAVOR under $COMMON_DIR/patches/ and $FLAVOR_DIR/patches/"
dim "discovered ${#PATCHES[@]} patches for flavor=$FLAVOR"

# ── Stage SDK sources into kernel tree (ASK only) ─────────────────────
SDK_TOTAL_PRE=0; SDK_CONFLICTS_PRE=0; SDK_STAGED=0
if [[ "$FLAVOR" == "ask" && -d "$SDK_DIR" ]]; then
    while IFS= read -r f; do
        SDK_TOTAL_PRE=$((SDK_TOTAL_PRE+1))
        [[ -e "$KDIR/$f" ]] && SDK_CONFLICTS_PRE=$((SDK_CONFLICTS_PRE+1))
    done < <(cd "$SDK_DIR" && find . -type f | sed 's|^\./||')

    info "staging SDK sources (266 files) into kernel tree for ASK validation"
    while IFS= read -r f; do
        dst="$KDIR/$f"
        if [[ ! -e "$dst" ]]; then
            mkdir -p "$(dirname "$dst")"
            cp "$SDK_DIR/$f" "$dst"
            SDK_STAGED=$((SDK_STAGED+1))
        fi
    done < <(cd "$SDK_DIR" && find . -type f | sed 's|^\./||')
    dim "   staged $SDK_STAGED SDK file(s) for validation"
fi

# ── Header ─────────────────────────────────────────────────────────────
SUMMARY="$WORK_DIR/patch-health-${FLAVOR}.txt"
{
    echo "=== Patch health probe ==="
    echo "Flavor:      $FLAVOR"
    echo "Kernel:      linux-$KVER ($KDIR)"
    echo "Common dir:  $COMMON_DIR"
    echo "Flavor dir:  $FLAVOR_DIR"
    echo "Patches:     ${#PATCHES[@]}"
    echo "Run at:      $(date -u +%Y-%m-%dT%H:%M:%SZ)"
    echo
} | tee "$SUMMARY"

# ── Dry-run each patch ─────────────────────────────────────────────────
PASS=0; FAIL=0
FAILED=()
for p in "${PATCHES[@]}"; do
    parent="$(basename "$(dirname "$p")")"
    name="$parent/$(basename "$p")"
    if out=$(git apply --check -p1 --unsafe-paths --directory="$KDIR" "$p" 2>&1); then
        printf '  %s ✓%s %s\n' "$_C_GRN" "$_C_RST" "$name" | tee -a "$SUMMARY"
        PASS=$((PASS+1))
    else
        printf '  %s ✗%s %s\n' "$_C_RED" "$_C_RST" "$name" | tee -a "$SUMMARY"
        printf '%s\n' "$out" | sed 's/^/      /' | tee -a "$SUMMARY"
        FAIL=$((FAIL+1))
        FAILED+=("$name")
    fi
done

# ── SDK conflict report (ASK only) ─────────────────────────────────────
if [[ "$FLAVOR" == "ask" && -d "$SDK_DIR" ]]; then
    echo | tee -a "$SUMMARY"
    info "checking SDK source path conflicts…"
    if (( SDK_CONFLICTS_PRE > 0 )); then
        warn "$SDK_CONFLICTS_PRE of $SDK_TOTAL_PRE SDK file(s) already exist (ASK will overwrite)"
    else
        ok "no SDK file conflicts ($SDK_TOTAL_PRE files to install)"
    fi
    echo "SDK files: $SDK_TOTAL_PRE, conflicts: $SDK_CONFLICTS_PRE" >> "$SUMMARY"
fi

# ── Verdict ────────────────────────────────────────────────────────────
echo | tee -a "$SUMMARY"
echo "=== Verdict ===" | tee -a "$SUMMARY"
printf 'Pass: %d   Fail: %d\n' "$PASS" "$FAIL" | tee -a "$SUMMARY"

if (( FAIL > 0 )); then
    echo "Failed patches:" | tee -a "$SUMMARY"
    printf '  %s\n' "${FAILED[@]}" | tee -a "$SUMMARY"
    err "patch rot detected against linux-$KVER (flavor=$FLAVOR)"
fi
ok "all patches apply cleanly against linux-$KVER (flavor=$FLAVOR)"