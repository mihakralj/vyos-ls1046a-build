#!/bin/bash
# bin/common.sh — shared environment for the CI scripts.
#
# Resolves KERNEL_VERSION/KERNEL_SERIES per the override chain in
# kernel/common/scripts/sync-kernel-version.sh.
#
# Sets and exports:
#   KERNEL_VERSION, KERNEL_SERIES, REPO_ROOT, KERNEL_SCRIPTS_DIR
#
# Safe to source repeatedly.
#
# The default|ask|vpp FLAVOR variable was removed 2026-07-26. The flavor
# split was retired on 2026-06-14 in favour of a single flavor-neutral
# dual-dataplane image (mainline/RSS at boot, ASK or VPP engaged at runtime
# per plans/DUAL-DATAPLANE.md). FLAVOR had resolved to "default" in every
# build since: no workflow set it and data/flavor.pin never existed, so every
# ask/vpp branch it guarded was unreachable.

# ── Resolve repo root ──────────────────────────────────────────────────
# Use BASH_SOURCE so this works from any CWD.
_BC_SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="${REPO_ROOT:-$(cd "$_BC_SCRIPT_DIR/.." && pwd)}"
export REPO_ROOT

# ── Kernel version resolution (auto-track upstream vyos-1x) ───────────
KERNEL_SCRIPTS_DIR="$REPO_ROOT/kernel/common/scripts"
export KERNEL_SCRIPTS_DIR

# Resolve KERNEL_VERSION/KERNEL_SERIES from vyos-build/data/defaults.toml
# (auto-tracked) FIRST, falling back to versions.lock internally when that
# checkout is missing. Must run BEFORE sourcing versions.lock below: that
# file uses `: "${KERNEL_VERSION:=6.18.44}"` and `export`s it, and
# sync-kernel-version.sh's own env-var precedence tier treats any
# already-exported KERNEL_VERSION as an explicit caller override and
# "respects" it — so sourcing versions.lock first silently poisoned the
# env and made every local dev-loop script stick to its stale fallback
# pin forever, even after defaults.toml moved on (found 2026-10-05:
# defaults.toml pinned 6.18.48, but common.sh kept resolving 6.18.44).
if [[ -f "$KERNEL_SCRIPTS_DIR/sync-kernel-version.sh" ]]; then
    # shellcheck source=../kernel/common/scripts/sync-kernel-version.sh
    . "$KERNEL_SCRIPTS_DIR/sync-kernel-version.sh"
fi
export KERNEL_VERSION KERNEL_SERIES

# Now safe to source versions.lock for its other pins (e.g. ARCH): its
# `: "${KERNEL_VERSION:=...}"` form is a no-op since KERNEL_VERSION is
# already exported above.
[[ -f "$REPO_ROOT/versions.lock" ]] && . "$REPO_ROOT/versions.lock"
export KERNEL_VERSION KERNEL_SERIES

# ── Status banner (only when sourced from an interactive script) ──────
if [[ "${BC_QUIET:-0}" != "1" ]]; then
    echo "## bin/common.sh: KERNEL_VERSION=$KERNEL_VERSION"
fi