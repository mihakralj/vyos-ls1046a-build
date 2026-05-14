#!/usr/bin/env bash
# patch-health.sh — FLAVOR-aware dry-run probe.
#
# Layered on the the archived kernel-build repo's patch-health.sh (./scripts/patch-health.sh in
# kernel-ls1046a-build) but adapted to the this repo's per-flavor layout
# (per plans/archive/INTEGRATION-PLAN.md §2 + §4 — historical merge plan).
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

# ── Derive paths (in-tree layout, distinct from the archived kernel-build repo's $REPO_ROOT/scripts/common.sh) ──
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

# common.sh unconditionally re-exports SCRIPTS_DIR=$REPO_ROOT/scripts (orig kernel-build layout
# layout). Restore in-tree-layout values that we actually want to use.
export SCRIPTS_DIR="$SCRIPT_DIR"
export WORK_DIR="$REPO_ROOT/work"
mkdir -p "$WORK_DIR"

# ── Pin resolution (auto-track upstream vyos-1x) ─────────────────────
# 1. versions.lock provides fallback defaults.
# 2. sync-kernel-version.sh overrides from vyos-build/data/defaults.toml when
#    that checkout is present (env KERNEL_VERSION still wins).
[[ -f "$REPO_ROOT/versions.lock" ]] && . "$REPO_ROOT/versions.lock"
# shellcheck source=sync-kernel-version.sh
. "$SCRIPT_DIR/sync-kernel-version.sh"

need git find jq

# ── Argument parsing ──────────────────────────────────────────────────
FLAVOR="${FLAVOR:-ask}"
VERSION_ARG=""
while (( $# )); do
    case "$1" in
        --flavor)  FLAVOR="$2"; shift 2 ;;
        --flavor=*) FLAVOR="${1#--flavor=}"; shift ;;
        --source) shift 2 ;;  # accepted-and-ignored for back-compat with the archived kernel-build repo
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
    || err "kernel/common/patches/ not found at $COMMON_DIR/patches (in-tree layout)"
# kernel/flavors/$FLAVOR/ is optional: default and vpp share the upstream-tracked
# kernel from kernel/common/ alone (their per-flavor kernel-config + patches dirs
# were dead-code placeholders, removed 2026-05-11). Only the ask flavor has an
# in-tree subtree. Missing dir is normal for default/vpp; only ask is mandatory.
if [[ ! -d "$FLAVOR_DIR" && "$FLAVOR" == "ask" ]]; then
    err "kernel/flavors/ask/ not found (required for FLAVOR=ask)"
fi

# ── Ensure kernel source is present ────────────────────────────────────
if [[ -n "$VERSION_ARG" || ! -f "$WORK_DIR/.kernel-version" ]]; then
    "$SCRIPTS_DIR/fetch-kernel.sh" $VERSION_ARG
fi
KVER=$(cat "$WORK_DIR/.kernel-version")
KDIR="$WORK_DIR/linux-$KVER"
[[ -d "$KDIR" ]] || err "kernel source missing: $KDIR"

# ── Ensure kernel tree is a git repo (required for `git apply --3way`) ─
# fetch-kernel.sh extracts a vanilla tarball with no .git directory. Without
# an index, `git apply --check --3way` short-circuits with "does not exist
# in index" before it can do any context matching, making every patch look
# like it failed even when it would apply cleanly. We `git init` the
# extracted tree once and stage everything in a single baseline commit.
# Cost: ~30 s + ~2 GB on first run (cached for subsequent runs); enables the
# real --3way diagnostic so legitimate context drift is reported instead of
# being masked by the missing-index error.
# Health-check: a baseline is "good" iff .git exists AND points at a commit.
# We've seen half-initialised .git/ trees (HEAD + empty branches/, no index
# and no objects) survive interrupted runs and silently make every patch
# look broken. Re-initialise from scratch in that case.
_baseline_ok=0
if [[ -d "$KDIR/.git" ]]; then
    if git -C "$KDIR" rev-parse --verify -q HEAD^{commit} >/dev/null 2>&1; then
        _baseline_ok=1
    else
        warn "stale .git in $KDIR (no commit) — re-initialising"
        rm -rf "$KDIR/.git"
    fi
fi
if (( ! _baseline_ok )); then
    info "initialising git baseline in $KDIR (one-time, enables --3way)"
    (
        cd "$KDIR"
        git init -q -b baseline
        # Pure dry-run probe: skip working-tree filters that would otherwise
        # rewrite line endings or smudge content. Use a synthetic identity so
        # the commit doesn't depend on the operator's git config.
        git -c core.autocrlf=false \
            -c user.email='patch-health@localhost' \
            -c user.name='patch-health' \
            add -A
        git -c user.email='patch-health@localhost' \
            -c user.name='patch-health' \
            commit -q -m "linux-$KVER baseline (patch-health probe)"
    ) || err "failed to initialise git baseline in $KDIR"
    ok "git baseline ready in $KDIR"
else
    dim "git baseline present in $KDIR"
fi

# ── Discover patches in apply order ────────────────────────────────────
PATCHES=()

# 1. Common: vyos → board → fixes
for sub in vyos board fixes; do
    d="$COMMON_DIR/patches/$sub"
    [[ -d "$d" ]] || continue
    while IFS= read -r p; do PATCHES+=("$p"); done \
        < <(find "$d" -maxdepth 1 -type f -name '*.patch' | sort)
done

# 2. Flavor-specific: ask/ → fixes/ → flavor-root (only if flavor dir exists)
if [[ -d "$FLAVOR_DIR" ]]; then
    for sub in ask fixes ""; do
        d="$FLAVOR_DIR/patches${sub:+/$sub}"
        [[ -d "$d" ]] || continue
        while IFS= read -r p; do PATCHES+=("$p"); done \
            < <(find "$d" -maxdepth 1 -type f -name '*.patch' | sort)
    done
fi

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
#
# Two modes per patch, decided by the parent directory:
#
#   parent != "patches"  → independent dry-run (--check) against the
#       pristine baseline.  Used for vyos/board/fixes/ask which are
#       all designed to apply standalone on a clean kernel tree.
#
#   parent == "patches"  → cumulative stack apply against the running
#       tree.  ASK2's kernel/flavors/ask/patches/{0001..NNNN}-*.patch
#       set is a logical patch series (each one expects its
#       predecessors applied, e.g. 0005-fman-pcd-kg-prep modifies
#       include/linux/fsl/fman_pcd.h which is *created* by
#       0004-fman-pcd-subsystem).  Treating them as independent
#       dry-runs produces false-positive rot.  We apply each one,
#       commit the result, and let the next patch see the cumulative
#       tree — exactly mirroring what build-kernel.sh does at build
#       time after the GNU-patch→git-apply loop rewrite.
#
# Patch path must be absolute since `git -C $KDIR` changes the
# git directory.
PASS=0; FAIL=0
FAILED=()
STACK_DIRTY=0
for p in "${PATCHES[@]}"; do
    parent="$(basename "$(dirname "$p")")"
    name="$parent/$(basename "$p")"

    if [[ "$parent" == "patches" ]]; then
        # Cumulative apply mode: actually apply + commit so the next
        # patch in the series can rely on the prior one's blobs.
        if out=$(git -C "$KDIR" apply --3way -p1 "$p" 2>&1); then
            git -C "$KDIR" add -A >/dev/null 2>&1 || true
            git -C "$KDIR" -c user.email=patch-health@local \
                -c user.name=patch-health \
                commit -q -m "patch-health: stack apply $name" \
                --allow-empty >/dev/null 2>&1 || true
            STACK_DIRTY=1
            printf '  %s ✓%s %s\n' "$_C_GRN" "$_C_RST" "$name" \
                | tee -a "$SUMMARY"
            PASS=$((PASS+1))
        else
            printf '  %s ✗%s %s\n' "$_C_RED" "$_C_RST" "$name" \
                | tee -a "$SUMMARY"
            printf '%s\n' "$out" | sed 's/^/      /' | tee -a "$SUMMARY"
            FAIL=$((FAIL+1))
            FAILED+=("$name")
            # Reset the tree so a failing patch in the middle of the
            # stack doesn't poison the rest of the series.
            git -C "$KDIR" reset --hard -q >/dev/null 2>&1 || true
            git -C "$KDIR" clean -fdq      >/dev/null 2>&1 || true
        fi
    else
        # Independent dry-run mode: --check against the current tree.
        if out=$(git -C "$KDIR" apply --check --3way -p1 "$p" 2>&1); then
            printf '  %s ✓%s %s\n' "$_C_GRN" "$_C_RST" "$name" \
                | tee -a "$SUMMARY"
            PASS=$((PASS+1))
        else
            printf '  %s ✗%s %s\n' "$_C_RED" "$_C_RST" "$name" \
                | tee -a "$SUMMARY"
            printf '%s\n' "$out" | sed 's/^/      /' | tee -a "$SUMMARY"
            FAIL=$((FAIL+1))
            FAILED+=("$name")
        fi
    fi
done

# Roll the kernel tree back to the pristine baseline so the next
# patch-health run starts clean.  Only needed if we ever applied a
# cumulative stack patch this run.
if (( STACK_DIRTY )); then
    git -C "$KDIR" reset --hard -q >/dev/null 2>&1 || true
    git -C "$KDIR" clean -fdq      >/dev/null 2>&1 || true
fi

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