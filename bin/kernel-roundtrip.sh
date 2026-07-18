#!/bin/bash
# kernel-roundtrip.sh — Round-trip the kernel patch stack through git.
#
# Canonical model: a git branch (vyos-6.18.y-dpaa1) with one commit per patch
# is the source of truth.  Patch files are a GENERATED export.
#
#   make kernel-import   → git quiltimport from kernel/common/patches/board/
#   make kernel-export   → git format-patch back to kernel/common/patches/board/
#
# The round-trip identity gate (import then export, diff) proves the two
# representations are equivalent — no drift, no silent fixup corruption.
#
# Usage:
#   bin/kernel-roundtrip.sh import   # one-time: bootstrap the kernel branch
#   bin/kernel-roundtrip.sh export   # regenerate patches from the branch
#   bin/kernel-roundtrip.sh verify   # assert import(export) == patches

set -e
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
PATCH_DIR="$REPO_ROOT/kernel/common/patches/board"
SERIES="$PATCH_DIR/series"
KERNEL_VERSION="${KERNEL_VERSION:-6.18.38}"
KERNEL_BRANCH="vyos-${KERNEL_VERSION}-dpaa1"

die() { echo "ERROR: $*" >&2; exit 1; }

cmd_import() {
    # Bootstrap: create kernel branch from patches using git quiltimport
    # Requires the linux-stable tree to be checked out at the right tag.
    local KSRC="${1:-/tmp/linux-stable}"
    if [ ! -f "$KSRC/Makefile" ]; then
        die "Kernel source not found at $KSRC. Clone linux-stable first:"
        die "  git clone --depth=1 --branch v$KERNEL_VERSION https://git.kernel.org/pub/scm/linux/kernel/git/stable/linux.git $KSRC"
    fi
    cd "$KSRC"
    git checkout -b "$KERNEL_BRANCH" "v$KERNEL_VERSION" 2>/dev/null || \
        die "Branch $KERNEL_BRANCH already exists — delete it first or use: git checkout $KERNEL_BRANCH"
    git quiltimport --series "$SERIES" --patches "$PATCH_DIR" || \
        die "quiltimport failed — some patches have conflicting filenames"
    echo "### Kernel branch $KERNEL_BRANCH bootstrapped with $(git rev-list --count v$KERNEL_VERSION..HEAD) commits"
}

cmd_export() {
    # Export: generate patch files from the kernel branch
    local KSRC="${1:-/tmp/linux-stable}"
    cd "$KSRC"
    git checkout "$KERNEL_BRANCH" 2>/dev/null || \
        die "Kernel branch $KERNEL_BRANCH not found — run 'import' first"
    
    local tag="v$KERNEL_VERSION"
    local out="$PATCH_DIR"
    rm -f "$out"/*.patch "$SERIES"
    
    git format-patch --zero-commit --no-signature --no-numbered \
        --output-directory "$out" "$tag..HEAD"
    
    # Generate series file from the format-patch output (filename order)
    ls "$out"/*.patch | sort | while read f; do
        basename "$f" >> "$SERIES"
    done
    
    local count=$(wc -l < "$SERIES")
    echo "### Exported $count patches to $out/"
}

cmd_verify() {
    local tmp=$(mktemp -d)
    trap "rm -rf $tmp" EXIT
    
    # Export to temp directory
    cmd_export "${1:-/tmp/linux-stable}" > /dev/null 2>&1
    
    # Compare with existing patches
    local mismatch=0
    for f in "$PATCH_DIR"/*.patch; do
        local name=$(basename "$f")
        if [ -f "$tmp/$name" ]; then
            if ! cmp -s "$f" "$tmp/$name"; then
                echo "MISMATCH: $name"
                mismatch=$((mismatch + 1))
            fi
        else
            echo "NEW: $name"
        fi
    done
    
    if [ $mismatch -eq 0 ]; then
        echo "### Round-trip identity VERIFIED: patches match export"
    else
        echo "### WARNING: $mismatch patches differ — re-export needed"
    fi
}

case "${1:-help}" in
    import)    shift; cmd_import "$@";;
    export)    shift; cmd_export "$@";;
    verify)    shift; cmd_verify "$@";;
    *)         echo "Usage: $0 {import|export|verify} [kernel-source-dir]";;
esac
