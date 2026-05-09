#!/usr/bin/env bash
# sync-kernel-version.sh — read kernel_version from vyos-build/data/defaults.toml
# and export KERNEL_VERSION / KERNEL_SERIES so the rest of the pipeline tracks
# upstream vyos-1x automatically.
#
# Source this from any pipeline script that needs the current upstream pin.
#   . kernel/common/scripts/sync-kernel-version.sh
#   echo "Building kernel $KERNEL_VERSION"
#
# Or run directly to print the values:
#   bash kernel/common/scripts/sync-kernel-version.sh           # prints
#   bash kernel/common/scripts/sync-kernel-version.sh --check   # exits 1 on mismatch with versions.lock
#   bash kernel/common/scripts/sync-kernel-version.sh --update  # rewrites versions.lock in place
#
# Override hierarchy (highest precedence first):
#   1. KERNEL_VERSION already set in env  → respected, no override
#   2. vyos-build/data/defaults.toml      → authoritative when present
#   3. versions.lock pin                  → fallback when vyos-build/ is missing

set -euo pipefail

_SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
_REPO_ROOT="$(cd "$_SCRIPT_DIR/../../.." && pwd)"
_DEFAULTS_TOML="${VYOS_BUILD_DEFAULTS:-$_REPO_ROOT/vyos-build/data/defaults.toml}"
_VERSIONS_LOCK="$_REPO_ROOT/versions.lock"

# ── Helpers ────────────────────────────────────────────────────────────
_extract_toml_kver() {
    local toml="$1"
    [[ -f "$toml" ]] || return 1
    # match: kernel_version = "6.18.28"   (whitespace-tolerant, quoted value)
    awk -F'"' '/^[[:space:]]*kernel_version[[:space:]]*=/ { print $2; exit }' "$toml"
}

_extract_lock_kver() {
    local lock="$1"
    [[ -f "$lock" ]] || return 1
    # match: : "${KERNEL_VERSION:=6.18.28}"
    awk -F'[:=}]' '/KERNEL_VERSION:=/ { gsub(/"/, "", $4); print $4; exit }' "$lock"
}

# ── Resolve the authoritative value ───────────────────────────────────
_UPSTREAM_KVER=""
if [[ -n "${KERNEL_VERSION:-}" ]]; then
    # env-var wins; just export it and (optionally) report.
    _UPSTREAM_KVER="$KERNEL_VERSION"
    _SOURCE="env"
elif _UPSTREAM_KVER=$(_extract_toml_kver "$_DEFAULTS_TOML" 2>/dev/null) && [[ -n "$_UPSTREAM_KVER" ]]; then
    _SOURCE="vyos-build/data/defaults.toml"
elif _UPSTREAM_KVER=$(_extract_lock_kver "$_VERSIONS_LOCK" 2>/dev/null) && [[ -n "$_UPSTREAM_KVER" ]]; then
    _SOURCE="versions.lock (vyos-build checkout missing)"
else
    echo "sync-kernel-version: ERROR — could not determine KERNEL_VERSION" >&2
    echo "  tried env KERNEL_VERSION, $_DEFAULTS_TOML, $_VERSIONS_LOCK" >&2
    return 1 2>/dev/null || exit 1
fi

# Derive series (X.Y) from full version (X.Y.Z)
_UPSTREAM_SERIES="${_UPSTREAM_KVER%.*}"

# Export for any caller that sourced us
export KERNEL_VERSION="$_UPSTREAM_KVER"
export KERNEL_SERIES="$_UPSTREAM_SERIES"

# ── CLI mode (only when not sourced) ──────────────────────────────────
# (return works in sourced context; exit works when run directly)
if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
    cmd="${1:-print}"
    case "$cmd" in
        print|"")
            printf 'KERNEL_VERSION=%s\nKERNEL_SERIES=%s\nSOURCE=%s\n' \
                "$KERNEL_VERSION" "$KERNEL_SERIES" "$_SOURCE"
            ;;
        --check|check)
            lock_kver=$(_extract_lock_kver "$_VERSIONS_LOCK" 2>/dev/null || echo "")
            if [[ "$lock_kver" == "$KERNEL_VERSION" ]]; then
                echo "OK — versions.lock matches upstream ($KERNEL_VERSION, source: $_SOURCE)"
                exit 0
            else
                echo "MISMATCH — versions.lock=$lock_kver  upstream=$KERNEL_VERSION (source: $_SOURCE)"
                echo "  run: bash kernel/common/scripts/sync-kernel-version.sh --update"
                exit 1
            fi
            ;;
        --update|update)
            lock_kver=$(_extract_lock_kver "$_VERSIONS_LOCK" 2>/dev/null || echo "")
            if [[ "$lock_kver" == "$KERNEL_VERSION" ]]; then
                echo "versions.lock already at $KERNEL_VERSION — no changes"
                exit 0
            fi
            # Rewrite both pinned values in place
            sed -i \
                -e "s|^: \"\${KERNEL_VERSION:=.*}\"|: \"\${KERNEL_VERSION:=$KERNEL_VERSION}\"|" \
                -e "s|^: \"\${KERNEL_SERIES:=.*}\"|: \"\${KERNEL_SERIES:=$KERNEL_SERIES}\"|" \
                "$_VERSIONS_LOCK"
            echo "versions.lock updated: $lock_kver → $KERNEL_VERSION (source: $_SOURCE)"
            ;;
        *)
            echo "usage: $0 [print|--check|--update]" >&2
            exit 2
            ;;
    esac
fi