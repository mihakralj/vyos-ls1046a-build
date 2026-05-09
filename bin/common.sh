#!/bin/bash
# bin/common.sh — shared environment for FLAVOR-aware CI scripts.
#
# Source this from any bin/ci-*.sh that needs to know which flavor is being
# built. Resolution order for FLAVOR (highest precedence first):
#   1. $FLAVOR env var (set by the workflow `env:` block from `inputs.flavor`)
#   2. data/flavor.pin (file containing one of: default | ask | vpp)
#   3. Hard default: "default"
#
# Also resolves KERNEL_VERSION/KERNEL_SERIES per the override chain in
# kernel/common/scripts/sync-kernel-version.sh.
#
# Sets and exports:
#   FLAVOR, KERNEL_VERSION, KERNEL_SERIES, REPO_ROOT, KERNEL_SCRIPTS_DIR
#
# Safe to source repeatedly. Preserves an already-set FLAVOR.

# ── Resolve repo root ──────────────────────────────────────────────────
# Use BASH_SOURCE so this works from any CWD.
_BC_SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="${REPO_ROOT:-$(cd "$_BC_SCRIPT_DIR/.." && pwd)}"
export REPO_ROOT

# ── FLAVOR resolution ─────────────────────────────────────────────────
if [[ -z "${FLAVOR:-}" ]]; then
    if [[ -f "$REPO_ROOT/data/flavor.pin" ]]; then
        FLAVOR=$(tr -d '[:space:]' < "$REPO_ROOT/data/flavor.pin")
    fi
fi
FLAVOR="${FLAVOR:-default}"

case "$FLAVOR" in
    default|ask|vpp) : ;;
    *)
        echo "common.sh: ERROR — invalid FLAVOR='$FLAVOR' (expected default | ask | vpp)" >&2
        # When sourced, `return` exits this script; we then signal the parent
        # shell to terminate so callers don't proceed with bogus FLAVOR.
        # When executed directly, `exit` handles it.
        if (return 0 2>/dev/null); then
            kill -TERM $$
            return 1
        else
            exit 1
        fi
        ;;
esac
export FLAVOR

# ── Kernel version resolution (auto-track upstream vyos-1x) ───────────
KERNEL_SCRIPTS_DIR="$REPO_ROOT/kernel/common/scripts"
export KERNEL_SCRIPTS_DIR

# Pull fallback defaults from versions.lock first.
[[ -f "$REPO_ROOT/versions.lock" ]] && . "$REPO_ROOT/versions.lock"

# Then let sync-kernel-version.sh override from vyos-build/data/defaults.toml
# when that checkout is present. Respects an already-set KERNEL_VERSION env var.
if [[ -f "$KERNEL_SCRIPTS_DIR/sync-kernel-version.sh" ]]; then
    # shellcheck source=../kernel/common/scripts/sync-kernel-version.sh
    . "$KERNEL_SCRIPTS_DIR/sync-kernel-version.sh"
fi
export KERNEL_VERSION KERNEL_SERIES

# ── Status banner (only when sourced from an interactive script) ──────
if [[ "${BC_QUIET:-0}" != "1" ]]; then
    echo "## bin/common.sh: FLAVOR=$FLAVOR  KERNEL_VERSION=$KERNEL_VERSION"
fi