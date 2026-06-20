#!/bin/bash
# bin/common.sh — shared environment for the single-image CI scripts.
#
# Source this from any bin/ci-*.sh that needs the resolved kernel version.
# The multi-flavor (default | ask | vpp) build split was retired: there is
# ONE collapsed image, so this script no longer resolves or exports a FLAVOR.
#
# Resolves KERNEL_VERSION/KERNEL_SERIES per the override chain in
# kernel/common/scripts/sync-kernel-version.sh.
#
# Sets and exports:
#   KERNEL_VERSION, KERNEL_SERIES, REPO_ROOT, KERNEL_SCRIPTS_DIR
#
# Safe to source repeatedly.

# ── Resolve repo root ──────────────────────────────────────────────────
# Use BASH_SOURCE so this works from any CWD.
_BC_SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="${REPO_ROOT:-$(cd "$_BC_SCRIPT_DIR/.." && pwd)}"
export REPO_ROOT

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
    echo "## bin/common.sh: KERNEL_VERSION=$KERNEL_VERSION"
fi