#!/bin/bash
# kernel-roundtrip.sh — Round-trip kernel patches through a git branch.
#
# Canonical model: a git branch (vyos-6.18.y-dpaa1) with one commit per patch
# is the source of truth.  Patch files are a GENERATED export.
#
#   import   → git quiltimport from kernel/common/patches/board/
#   export   → git format-patch back to kernel/common/patches/board/
#   verify   → non-destructive: export to temp, compare against real dir
#
# Patch identity is carried in commit trailers:
#   Patch-Name: 0158-fman-pcd-fqid-resolution-compose.patch
#   Upstream-Status: Inappropriate [LS1046A Mono Gateway DK]
#   Risk-Tier: A
#
# The exporter renames format-patch output using Patch-Name trailers and
# regenerates the series file with metadata comments from commit trailers.
# The downstream 0001-*/0003-* protection namespace is never touched.
#
# Usage:
#   bin/kernel-roundtrip.sh import  [kernel-source-dir]
#   bin/kernel-roundtrip.sh export  [kernel-source-dir] [--force]
#   bin/kernel-roundtrip.sh verify  [kernel-source-dir]

set -e
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
PATCH_DIR="$REPO_ROOT/kernel/common/patches/board"
SERIES="$PATCH_DIR/series"
# Resolve the kernel version from the project's single source of truth
# (sync-kernel-version.sh → defaults.toml/versions.lock) unless overridden.
# Never hardcode a stale version here — the pin moved 6.18.38 → 6.18.44 and
# left this tool pointing at a nonexistent branch (2026-08-18).
if [ -z "${KERNEL_VERSION:-}" ]; then
    _kv="$(bash "$REPO_ROOT/kernel/common/scripts/sync-kernel-version.sh" 2>/dev/null \
            | sed -n 's/^KERNEL_VERSION=//p' | head -1)"
    KERNEL_VERSION="${_kv:-6.18.44}"
fi
KERNEL_BRANCH="vyos-${KERNEL_VERSION}-dpaa1"
PATCH_NS_BLACKLIST="^000[13]-"  # protect vyos-build's own patches

die() { echo "ERROR: $*" >&2; exit 1; }
warn() { echo "WARNING: $*" >&2; }

# ── import: bootstrap the canonical branch from patches ──────────────────

cmd_import() {
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

# ── export: regenerate patches from the canonical branch ─────────────────

cmd_export() {
    local KSRC="${1:-/tmp/linux-stable}"
    local FORCE=0
    [ "$2" = "--force" ] && FORCE=1

    # Safety gate: refuse to overwrite a dirty working patch dir
    if [ $FORCE -eq 0 ] && ! git -C "$REPO_ROOT" diff --quiet -- "$PATCH_DIR"; then
        die "Patch directory $PATCH_DIR has uncommitted changes. Use --force to overwrite."
    fi

    cd "$KSRC"
    git checkout "$KERNEL_BRANCH" 2>/dev/null || \
        die "Kernel branch $KERNEL_BRANCH not found — run 'import' first"

    local tag="v$KERNEL_VERSION"
    local tmp=$(mktemp -d)
    trap "rm -rf $tmp" EXIT

    # Export to temp directory first
    git format-patch --zero-commit --no-signature --no-numbered \
        --output-directory "$tmp" "$tag..HEAD"

    # Rename using Patch-Name trailers and collect metadata
    local new_series="$tmp/series"
    local count=0
    for f in $(ls "$tmp"/*.patch | sort); do
        local patch_name
        # --zero-commit deliberately writes an all-zero From SHA, so looking
        # trailers up via `git log <From-SHA>` can NEVER work.  Parse the
        # trailers directly from the format-patch commit-message body instead.
        # This latent bug made every export fall back to numbered Subject-based
        # filenames despite correct Patch-Name trailers (2026-08-18).
        patch_name=$(sed -n 's/^Patch-Name:[[:space:]]*//p' "$f" | head -1)
        if [ -z "$patch_name" ]; then
            # Fallback: derive from Subject or use existing filename
            patch_name="$(basename "$f")"
        fi

        # Guard against blacklisted namespace
        if echo "$patch_name" | grep -qE "$PATCH_NS_BLACKLIST"; then
            die "Exported patch name '$patch_name' collides with protected namespace ($PATCH_NS_BLACKLIST). Add a Patch-Name trailer to this commit."
        fi

        # Collect metadata from commit-message trailers for series file.
        local upstream_status risk_tier
        upstream_status=$(sed -n 's/^Upstream-Status:[[:space:]]*//p' "$f" | head -1)
        risk_tier=$(sed -n 's/^Risk-Tier:[[:space:]]*//p' "$f" | head -1)

        # Write metadata comment before the patch filename
        if [ -n "$upstream_status" ] || [ -n "$risk_tier" ]; then
            echo "# Upstream-Status: ${upstream_status:-Inappropriate} | Risk-Tier: ${risk_tier:-A}" >> "$new_series"
        fi
        echo "$patch_name" >> "$new_series"

        mv "$f" "$tmp/$patch_name"
        count=$((count + 1))
    done

    # Copy to real patch directory
    rm -f "$PATCH_DIR"/*.patch
    cp "$tmp"/*.patch "$PATCH_DIR/"
    cp "$new_series" "$SERIES"
    rm -rf "$tmp"

    echo "### Exported $count patches to $PATCH_DIR/"
}

# ── verify: non-destructive round-trip identity check ────────────────────

cmd_verify() {
    local KSRC="${1:-/tmp/linux-stable}"
    local tmp=$(mktemp -d)
    trap "rm -rf $tmp" EXIT

    # Export to temp directory (NEVER to real PATCH_DIR)
    cd "$KSRC"
    git checkout "$KERNEL_BRANCH" 2>/dev/null || \
        die "Kernel branch $KERNEL_BRANCH not found — run 'import' first"

    local tag="v$KERNEL_VERSION"
    git format-patch --zero-commit --no-signature --no-numbered \
        --output-directory "$tmp" "$tag..HEAD"

    # Rename using Patch-Name trailers (matching export logic for comparison)
    for f in $(ls "$tmp"/*.patch | sort); do
        local patch_name
        # See cmd_export: --zero-commit zeros the From SHA.  Read Patch-Name
        # from the commit-message trailer embedded in the patch body.
        patch_name=$(sed -n 's/^Patch-Name:[[:space:]]*//p' "$f" | head -1)
        [ -n "$patch_name" ] && mv "$f" "$tmp/$patch_name" || true
    done

    # Compare against real patch directory — byte-for-byte
    local ok=0 mismatch=0 new_patches=0 missing=0
    for f in "$PATCH_DIR"/*.patch; do
        local name=$(basename "$f")
        if [ -f "$tmp/$name" ]; then
            if cmp -s "$f" "$tmp/$name"; then
                ok=$((ok + 1))
            else
                echo "MISMATCH: $name"
                mismatch=$((mismatch + 1))
            fi
        else
            # This patch exists in the working dir but not in the export — might be a fixup-modified patch
            echo "MISSING-FROM-EXPORT: $name (present in working tree, absent from canonical branch export)"
            missing=$((missing + 1))
        fi
    done
    for f in "$tmp"/*.patch; do
        local name=$(basename "$f")
        if [ ! -f "$PATCH_DIR/$name" ]; then
            echo "NEW-IN-EXPORT: $name (in canonical branch but not in working tree)"
            new_patches=$((new_patches + 1))
        fi
    done

    echo ""
    echo "### Round-trip verify: OK=$ok  MISMATCH=$mismatch  MISSING=$missing  NEW=$new_patches"

    if [ $mismatch -eq 0 ] && [ $new_patches -eq 0 ]; then
        echo "### Round-trip identity VERIFIED (${ok} patches byte-identical)"
        return 0
    else
        warn "$mismatch patch(es) differ — export needed"
        [ $mismatch -gt 0 ] && return 1
        return 0
    fi
}

case "${1:-help}" in
    import)  shift; cmd_import "$@";;
    export)  shift; cmd_export "$@";;
    verify)  shift; cmd_verify "$@";;
    *)       cat <<'USAGE'
Usage: kernel-roundtrip.sh {import|export|verify} [kernel-source-dir]

  import         Bootstrap the canonical git branch from patches
                 (one-time: v6.18.38 + git quiltimport)

  export         Regenerate patches from the canonical branch
                 Renames output using Patch-Name commit trailers.
                 Refuses to overwrite dirty patch dir without --force.

  verify         Non-destructive round-trip identity check.
                 Exports to a temp directory, compares byte-for-byte
                 against the working patch directory. Exits 1 on mismatch.
                 NEVER touches the real patch directory.
USAGE
        ;;
esac
