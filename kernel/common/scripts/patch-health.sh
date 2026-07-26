#!/usr/bin/env bash
# patch-health.sh — dry-run patch-rot probe.
#
# Layered on the the archived kernel-build repo's patch-health.sh (./scripts/patch-health.sh in
# kernel-ls1046a-build) but adapted to this repo's layout
# (per plans/archive/INTEGRATION-PLAN.md §2 + §4 — historical merge plan).
#
# Buckets, applied in this order:
#   kernel/common/patches/vyos/*.patch           (always)
#   kernel/common/patches/board/*.patch          (always)
#   kernel/common/patches/fixes/*.patch          (always)
#
# SDK source drop (ASK-only): kernel/ask/sdk-sources/* is staged
# into the kernel tree before dry-run so patches that target SDK files can
# validate, mirroring scripts/apply-to-tree.sh semantics.
#
# Usage:
#   ./kernel/common/scripts/patch-health.sh            # work/.kernel-version
#   ./kernel/common/scripts/patch-health.sh 6.18.38    # explicit kernel
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
VERSION_ARG=""
while (( $# )); do
    case "$1" in
        # --flavor accepted and ignored; the flavor split was retired.
        --flavor)  warn "--flavor is obsolete and ignored"; shift 2 ;;
        --flavor=*) warn "--flavor is obsolete and ignored"; shift ;;
        --source) shift 2 ;;  # accepted-and-ignored for back-compat with the archived kernel-build repo
        -h|--help) sed -n '1,40p' "$0"; exit 0 ;;
        *) VERSION_ARG="$1"; shift ;;
    esac
done

# ── Layout invariants ──────────────────────────────────────────────────
COMMON_DIR="$REPO_ROOT/kernel/common"

[[ -d "$COMMON_DIR/patches" ]] \
    || err "kernel/common/patches/ not found at $COMMON_DIR/patches (in-tree layout)"

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

(( ${#PATCHES[@]} )) || err "no patches found under $COMMON_DIR/patches/"
dim "discovered ${#PATCHES[@]} patches"

# ── Header ─────────────────────────────────────────────────────────────
SUMMARY="$WORK_DIR/patch-health.txt"
{
    echo "=== Patch health probe ==="
    echo "Kernel:      linux-$KVER ($KDIR)"
    echo "Common dir:  $COMMON_DIR"
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
#       tree.  ASK2's kernel/ask/patches/{0001..NNNN}-*.patch
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
# Capture the pristine baseline commit SHA BEFORE any stack-apply commits
# pile up on top of it. Required for the STACK_DIRTY cleanup at end-of-loop
# AND for mid-loop failure recovery — both must reset to *this* SHA, not
# to HEAD (which by then is the last stack-applied commit, not the
# baseline). Without this capture, subsequent patch-health runs start
# from a polluted HEAD with prior stack commits still present, and every
# patch in the series cascade-fails as 'repository lacks the necessary
# blob to perform 3-way merge' — a false-positive rot signal.
# See Qdrant memo 2026-05-23.
BASELINE_REF=$(git -C "$KDIR" rev-parse HEAD)
for p in "${PATCHES[@]}"; do
    parent="$(basename "$(dirname "$p")")"
    name="$parent/$(basename "$p")"

    if [[ "$parent" == "patches" || "$parent" == "board" ]]; then
        # Cumulative apply mode: actually apply + commit so the next
        # patch in the series can rely on the prior one's blobs.
        # board/ is a stacked AF_XDP development series (0068..0088 etc.)
        # where each patch builds on its predecessor — independent
        # dry-run against pristine would always fail for everything
        # after the first.
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
            # Reset the tree to the LAST SUCCESSFUL commit (HEAD), not
            # to BASELINE_REF. Resetting all the way to baseline would
            # throw away every prior successful stack apply (e.g. patch
            # 0004 that creates include/linux/fsl/fman_pcd.h) and cause
            # every subsequent patch that touches those files to falsely
            # fail with "does not exist in index". `git apply --3way`
            # may have written conflict markers into files when it
            # decided it could not merge cleanly; `reset --hard HEAD`
            # discards those markers and leaves the tree at the last
            # known-good cumulative state so the NEXT patch in the
            # series gets a fair shot.
            git -C "$KDIR" reset --hard HEAD -q >/dev/null 2>&1 || true
            git -C "$KDIR" clean -fdq           >/dev/null 2>&1 || true
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
# cumulative stack patch this run.  MUST target $BASELINE_REF — see
# the comment at the BASELINE_REF capture site above.
if (( STACK_DIRTY )); then
    git -C "$KDIR" reset --hard "$BASELINE_REF" -q >/dev/null 2>&1 || true
    git -C "$KDIR" clean -fdq                       >/dev/null 2>&1 || true
fi

# ── Verdict ────────────────────────────────────────────────────────────
echo | tee -a "$SUMMARY"
echo "=== Verdict ===" | tee -a "$SUMMARY"
printf 'Pass: %d   Fail: %d\n' "$PASS" "$FAIL" | tee -a "$SUMMARY"

if (( FAIL > 0 )); then
    echo "Failed patches:" | tee -a "$SUMMARY"
    printf '  %s\n' "${FAILED[@]}" | tee -a "$SUMMARY"
    err "patch rot detected against linux-$KVER"
fi
ok "all patches apply cleanly against linux-$KVER"