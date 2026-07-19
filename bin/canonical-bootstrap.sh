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
KERNEL_BRANCH="vyos-6.18.38-dpaa1"

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

echo "### Bootstrapping canonical branch: $KERNEL_BRANCH"

# Initialize git if not already a repo
if [ ! -d .git ]; then
    git init -q
    git config user.email "canonical@ls1046a-build"
    git config user.name "LS1046A Canonical"
fi

# Commit pristine state as base
echo "### Committing pristine kernel source..."
git add -A
git commit -q -m "kernel pristine (v6.18.38 base)" --allow-empty || true

# Apply each patch from the series
SKIP_LIST="0150-fman-pcd-fe-engage-api 0158-fman-pcd-fqid-resolution-compose 0159-fman-pcd-e2-hash-probe 0160-fman-pcd-ekfc-programming 0161-fman-pcd-rccb-feenter-direct 0162-fman-pcd-port-arm-fe-ekfc-fix"
PATCH_COUNT=0; FAIL_COUNT=0; SKIP_COUNT=0

while IFS= read -r line; do
    # Skip comments and blanks
    [[ "$line" =~ ^# ]] && continue
    [ -z "$line" ] && continue

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
        continue
    fi

    [ ! -f "$ppath" ] && { echo "MISSING: $pname"; FAIL_COUNT=$((FAIL_COUNT + 1)); continue; }

    echo "APPLY: $pname"
    if git apply --3way --whitespace=nowarn "$ppath" 2>/dev/null; then
        git add -A
        git commit -q -m "applied: $pname"
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
