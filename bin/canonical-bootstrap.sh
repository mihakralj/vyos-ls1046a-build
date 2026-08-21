#!/bin/bash
# canonical-bootstrap.sh — Bootstrap the canonical kernel branch for R2.
#
# Uses the existing expanded kernel source tree (from a CI build) and
# the CI's own apply loop logic to construct a git branch with one commit
# per patch.  Avoids git quiltimport (which requires format-patch style)
# and avoids re-downloading the kernel.
#
# Usage:
#   bin/canonical-bootstrap.sh /path/to/linux-6.18.38
#
# Output:
#   A git branch 'vyos-6.18.38-dpaa1' in the kernel source tree with
#   one commit per applying patch from kernel/common/patches/board/series.
#   Skipped patches (BOARD_STAGE_SKIP) are excluded.
#
# After this runs, 'kernel-roundtrip.sh verify' can validate round-trip
# identity, and 'kernel-roundtrip.sh export' can regenerate patches.

set -e
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
PATCH_DIR="$REPO_ROOT/kernel/common/patches/board"
SERIES="$PATCH_DIR/series"
# Resolve the kernel version from the project's single source of truth rather
# than hardcoding it (the pin moved 6.18.38 → 6.18.44; a hardcoded branch name
# silently bootstrapped/verified the wrong version — 2026-08-18).
if [ -z "${KERNEL_VERSION:-}" ]; then
    _kv="$(bash "$REPO_ROOT/kernel/common/scripts/sync-kernel-version.sh" 2>/dev/null \
            | sed -n 's/^KERNEL_VERSION=//p' | head -1)"
    KERNEL_VERSION="${_kv:-6.18.44}"
fi
KERNEL_BRANCH="vyos-${KERNEL_VERSION}-dpaa1"
KERNEL_TAG="v${KERNEL_VERSION}"

die() { echo "ERROR: $*" >&2; exit 1; }

KSRC="${1:-}"
[ -z "$KSRC" ] && die "Usage: $0 <path-to-expanded-kernel-source>"
[ -d "$KSRC" ] || die "Kernel source not found at $KSRC"
[ -f "$KSRC/Makefile" ] || die "$KSRC doesn't look like a kernel source tree"

cd "$KSRC"

# Check if we already have the branch
if git rev-parse --verify "$KERNEL_BRANCH" >/dev/null 2>&1; then
    echo "### Branch $KERNEL_BRANCH already exists — skipping bootstrap"
    git checkout "$KERNEL_BRANCH"
    exit 0
fi

echo "### Bootstrapping canonical branch: $KERNEL_BRANCH (base $KERNEL_TAG)"

# Initialize git if not already a repo
if [ ! -d .git ]; then
    git init -q
    git config user.email "canonical@ls1046a-build"
    git config user.name "LS1046A Canonical"
    git add -A
    git commit -q -m "kernel pristine ($KERNEL_TAG base)" --allow-empty
    git tag -f "$KERNEL_TAG" HEAD
    git branch -f "$KERNEL_BRANCH" HEAD
    git checkout -q "$KERNEL_BRANCH"
else
    # Existing repo (e.g. persistent clone at the pristine tag): base the
    # canonical branch on the pristine tag so patches apply onto clean source.
    # WITHOUT this explicit checkout -b the loop would commit onto whatever
    # ref happens to be current (bug: silently built no branch on a fresh
    # git repo — 2026-08-18).
    if git rev-parse --verify "$KERNEL_TAG" >/dev/null 2>&1; then
        git checkout -q -b "$KERNEL_BRANCH" "$KERNEL_TAG"
    else
        # No tag available — commit whatever pristine state is present and
        # branch from it (dev-build expanded tree path).
        git add -A
        git commit -q -m "kernel pristine ($KERNEL_TAG base)" --allow-empty
        git checkout -q -b "$KERNEL_BRANCH" HEAD
    fi
fi

# Apply each patch from the series. Skip list mirrors the series' own
# '# SKIP <name>' ledger; 0150 is the permanent placeholder skip.
SKIP_LIST="0150-fman-pcd-fe-engage-api"
PATCH_COUNT=0; FAIL_COUNT=0; SKIP_COUNT=0

# Carry the series' per-patch metadata comment (the line immediately above a
# patch filename: '# Upstream-Status: … | Risk-Tier: …') into commit trailers.
# kernel-roundtrip.sh's exporter reads Patch-Name / Upstream-Status / Risk-Tier
# trailers to reproduce the exact working filenames + series metadata, so the
# round-trip identity gate can only go green when these trailers are present.
pending_status=""; pending_tier=""

while IFS= read -r line; do
    # Capture a metadata comment for the NEXT patch line, then continue.
    if [[ "$line" =~ ^# ]]; then
        if [[ "$line" =~ Upstream-Status:[[:space:]]*([^|]+) ]]; then
            pending_status="$(echo "${BASH_REMATCH[1]}" | sed 's/[[:space:]]*$//')"
        fi
        if [[ "$line" =~ Risk-Tier:[[:space:]]*([A-Za-z0-9]+) ]]; then
            pending_tier="${BASH_REMATCH[1]}"
        fi
        continue
    fi
    [ -z "$line" ] && { pending_status=""; pending_tier=""; continue; }

    pname="$line"
    ppath="$PATCH_DIR/$pname"

    # Skip if in skip list
    skip=0
    for s in $SKIP_LIST; do
        [[ "$pname" == "${s}"* ]] && skip=1 && break
    done
    if [ $skip -eq 1 ]; then
        echo "SKIP: $pname"
        SKIP_COUNT=$((SKIP_COUNT + 1))
        pending_status=""; pending_tier=""
        continue
    fi

    if [ ! -f "$ppath" ]; then
        echo "MISSING: $pname"; FAIL_COUNT=$((FAIL_COUNT + 1))
        pending_status=""; pending_tier=""
        continue
    fi

    # Build commit message with identity + metadata trailers.
    commit_msg="applied: $pname"$'\n\n'"Patch-Name: $pname"
    [ -n "$pending_status" ] && commit_msg="$commit_msg"$'\n'"Upstream-Status: $pending_status"
    [ -n "$pending_tier" ]   && commit_msg="$commit_msg"$'\n'"Risk-Tier: $pending_tier"
    pending_status=""; pending_tier=""

    echo "APPLY: $pname"
    if git apply --3way --whitespace=nowarn "$ppath" 2>/dev/null; then
        git add -A
        git commit -q -m "$commit_msg"
        PATCH_COUNT=$((PATCH_COUNT + 1))
    else
        echo "  FAILED: $pname (context drift)"
        FAIL_COUNT=$((FAIL_COUNT + 1))
    fi
done < "$SERIES"

echo ""
echo "### Canonical branch bootstrapped:"
echo "###   Applied: $PATCH_COUNT  Failed: $FAIL_COUNT  Skipped: $SKIP_COUNT"
echo "###   Branch: $KERNEL_BRANCH"

if [ $FAIL_COUNT -gt 0 ]; then
    die "$FAIL_COUNT patches failed to apply — see output above"
fi
