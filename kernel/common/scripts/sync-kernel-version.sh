#!/usr/bin/env bash
# sync-kernel-version.sh — resolve KERNEL_VERSION / KERNEL_SERIES / KERNEL_REVISION
# from upstream vyos-build (local checkout or live GitHub rolling) and keep
# versions.lock in sync.
#
# Source this from any pipeline script that needs the current upstream pin:
#   . kernel/common/scripts/sync-kernel-version.sh
#   echo "Building kernel ${KERNEL_VERSION}-${KERNEL_REVISION}-vyos"
#
# Or run directly:
#   bash kernel/common/scripts/sync-kernel-version.sh                    # prints resolved values
#   bash kernel/common/scripts/sync-kernel-version.sh --check            # exits 1 on mismatch with versions.lock
#   bash kernel/common/scripts/sync-kernel-version.sh --update           # rewrites versions.lock in place
#   bash kernel/common/scripts/sync-kernel-version.sh --fetch --update   # fetches live upstream rolling/data/defaults.toml first
#   bash kernel/common/scripts/sync-kernel-version.sh --fetch --check    # compares versions.lock against live upstream
#
# Override hierarchy (highest precedence first):
#   1. KERNEL_VERSION already set in env        → respected, no override
#   2. --fetch / SYNC_FETCH_UPSTREAM=1          → live vyos/vyos-build rolling/data/defaults.toml
#   3. VYOS_BUILD_DEFAULTS / vyos-build/data/defaults.toml → authoritative when present
#   4. versions.lock pin                        → fallback when vyos-build/ is missing

set -euo pipefail

_SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
_REPO_ROOT="$(cd "$_SCRIPT_DIR/../../.." && pwd)"
_DEFAULTS_TOML="${VYOS_BUILD_DEFAULTS:-$_REPO_ROOT/vyos-build/data/defaults.toml}"
_VERSIONS_LOCK="${VERSIONS_LOCK:-$_REPO_ROOT/versions.lock}"
_UPSTREAM_URL="${VYOS_UPSTREAM_DEFAULTS_URL:-https://raw.githubusercontent.com/vyos/vyos-build/rolling/data/defaults.toml}"

# ── Parse CLI args before resolution (only when executed directly) ─────
_IS_CLI=0
_CLI_CMD="print"
_FETCH_UPSTREAM="${SYNC_FETCH_UPSTREAM:-0}"
if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
    _IS_CLI=1
    _CMD_SET=0
    for _arg in "$@"; do
        case "$_arg" in
            --fetch|fetch)
                _FETCH_UPSTREAM=1
                ;;
            print|--check|check|--update|update)
                if [[ "$_CMD_SET" == "1" ]]; then
                    echo "usage: $0 [--fetch] [print|--check|--update]" >&2
                    exit 2
                fi
                _CLI_CMD="$_arg"
                _CMD_SET=1
                ;;
            -h|--help)
                echo "usage: $0 [--fetch] [print|--check|--update]"
                exit 0
                ;;
            *)
                echo "usage: $0 [--fetch] [print|--check|--update]" >&2
                exit 2
                ;;
        esac
    done
fi

# ── Helpers ────────────────────────────────────────────────────────────
_extract_toml_field() {
    local field="$1" toml="$2"
    [[ -f "$toml" ]] || return 1
    awk -F'"' -v f="$field" '$0 ~ "^[[:space:]]*" f "[[:space:]]*=" { print $2; exit }' "$toml"
}

_extract_lock_field() {
    local field="$1" lock="$2"
    [[ -f "$lock" ]] || return 1
    awk -F'[:=}]' -v f="$field" '$0 ~ f ":=" { gsub(/"/, "", $4); print $4; exit }' "$lock"
}

_valid_kver() {
    [[ "$1" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]
}

_valid_krev() {
    [[ "$1" =~ ^[0-9]+$ ]]
}

# ── Resolve the authoritative value ───────────────────────────────────
_UPSTREAM_KVER=""
_TOML_KREV=""
_ACTIVE_TOML=""
_FETCHED_TMP=""

if [[ -n "${KERNEL_VERSION:-}" ]]; then
    _UPSTREAM_KVER="$KERNEL_VERSION"
    _SOURCE="env"
    if [[ -f "$_DEFAULTS_TOML" ]]; then
        _ACTIVE_TOML="$_DEFAULTS_TOML"
    fi
elif [[ "$_FETCH_UPSTREAM" == "1" ]]; then
    _FETCHED_TMP="$(mktemp)"
    if ! curl -fsSL --max-time 15 "$_UPSTREAM_URL" -o "$_FETCHED_TMP"; then
        rm -f "$_FETCHED_TMP"
        echo "sync-kernel-version: ERROR — failed to fetch $_UPSTREAM_URL" >&2
        return 1 2>/dev/null || exit 1
    fi
    _UPSTREAM_KVER="$(_extract_toml_field kernel_version "$_FETCHED_TMP" 2>/dev/null || true)"
    _TOML_KREV="$(_extract_toml_field kernel_revision "$_FETCHED_TMP" 2>/dev/null || true)"
    rm -f "$_FETCHED_TMP"
    if ! _valid_kver "$_UPSTREAM_KVER"; then
        echo "sync-kernel-version: ERROR — invalid kernel_version '${_UPSTREAM_KVER}' in $_UPSTREAM_URL" >&2
        return 1 2>/dev/null || exit 1
    fi
    if [[ -n "$_TOML_KREV" ]] && ! _valid_krev "$_TOML_KREV"; then
        echo "sync-kernel-version: ERROR — invalid kernel_revision '${_TOML_KREV}' in $_UPSTREAM_URL" >&2
        return 1 2>/dev/null || exit 1
    fi
    _SOURCE="upstream ($_UPSTREAM_URL)"
elif _UPSTREAM_KVER="$(_extract_toml_field kernel_version "$_DEFAULTS_TOML" 2>/dev/null || true)" && [[ -n "$_UPSTREAM_KVER" ]]; then
    _ACTIVE_TOML="$_DEFAULTS_TOML"
    _TOML_KREV="$(_extract_toml_field kernel_revision "$_ACTIVE_TOML" 2>/dev/null || true)"
    _SOURCE="vyos-build/data/defaults.toml"
elif _UPSTREAM_KVER="$(_extract_lock_field KERNEL_VERSION "$_VERSIONS_LOCK" 2>/dev/null || true)" && [[ -n "$_UPSTREAM_KVER" ]]; then
    _SOURCE="versions.lock (vyos-build checkout missing)"
else
    echo "sync-kernel-version: ERROR — could not determine KERNEL_VERSION" >&2
    echo "  tried env KERNEL_VERSION, $_DEFAULTS_TOML, $_VERSIONS_LOCK" >&2
    return 1 2>/dev/null || exit 1
fi

if ! _valid_kver "$_UPSTREAM_KVER"; then
    echo "sync-kernel-version: ERROR — invalid KERNEL_VERSION '${_UPSTREAM_KVER}' (source: $_SOURCE)" >&2
    return 1 2>/dev/null || exit 1
fi

# Derive series (X.Y) from full version (X.Y.Z)
_UPSTREAM_SERIES="${_UPSTREAM_KVER%.*}"

# Export for any caller that sourced us
export KERNEL_VERSION="$_UPSTREAM_KVER"
export KERNEL_SERIES="$_UPSTREAM_SERIES"

# VyOS internal kernel revision (vyos-build T9591): the release string is
# <kernel_version>-<kernel_revision>-vyos, e.g. 6.18.64-1-vyos.
# Same precedence as the version: env, active TOML source, versions.lock, then 1.
if [[ -z "${KERNEL_REVISION:-}" ]]; then
    if [[ -n "$_TOML_KREV" ]]; then
        KERNEL_REVISION="$_TOML_KREV"
    elif [[ -n "$_ACTIVE_TOML" ]]; then
        KERNEL_REVISION="$(_extract_toml_field kernel_revision "$_ACTIVE_TOML" 2>/dev/null || true)"
    fi
    [[ -n "${KERNEL_REVISION:-}" ]] || KERNEL_REVISION="$(_extract_lock_field KERNEL_REVISION "$_VERSIONS_LOCK" 2>/dev/null || true)"
    KERNEL_REVISION="${KERNEL_REVISION:-1}"
fi
if ! _valid_krev "$KERNEL_REVISION"; then
    echo "sync-kernel-version: ERROR — invalid KERNEL_REVISION '${KERNEL_REVISION}'" >&2
    return 1 2>/dev/null || exit 1
fi
export KERNEL_REVISION

# ── CLI mode (only when not sourced) ──────────────────────────────────
if [[ "$_IS_CLI" == "1" ]]; then
    case "$_CLI_CMD" in
        print|"")
            printf 'KERNEL_VERSION=%s\nKERNEL_SERIES=%s\nKERNEL_REVISION=%s\nSOURCE=%s\n' \
                "$KERNEL_VERSION" "$KERNEL_SERIES" "$KERNEL_REVISION" "$_SOURCE"
            ;;
        --check|check)
            lock_kver="$(_extract_lock_field KERNEL_VERSION "$_VERSIONS_LOCK" 2>/dev/null || echo "")"
            lock_kseries="$(_extract_lock_field KERNEL_SERIES "$_VERSIONS_LOCK" 2>/dev/null || echo "")"
            lock_krev="$(_extract_lock_field KERNEL_REVISION "$_VERSIONS_LOCK" 2>/dev/null || echo "")"
            if [[ "$lock_kver" == "$KERNEL_VERSION" && "$lock_kseries" == "$KERNEL_SERIES" && "$lock_krev" == "$KERNEL_REVISION" ]]; then
                echo "OK — versions.lock matches upstream (${KERNEL_VERSION}-${KERNEL_REVISION}, source: $_SOURCE)"
                exit 0
            else
                echo "MISMATCH — versions.lock=${lock_kver}-${lock_krev} (series ${lock_kseries})  upstream=${KERNEL_VERSION}-${KERNEL_REVISION} (series ${KERNEL_SERIES}, source: $_SOURCE)"
                echo "  run: bash kernel/common/scripts/sync-kernel-version.sh --fetch --update"
                exit 1
            fi
            ;;
        --update|update)
            lock_kver="$(_extract_lock_field KERNEL_VERSION "$_VERSIONS_LOCK" 2>/dev/null || echo "")"
            lock_kseries="$(_extract_lock_field KERNEL_SERIES "$_VERSIONS_LOCK" 2>/dev/null || echo "")"
            lock_krev="$(_extract_lock_field KERNEL_REVISION "$_VERSIONS_LOCK" 2>/dev/null || echo "")"
            if [[ "$lock_kver" == "$KERNEL_VERSION" && "$lock_kseries" == "$KERNEL_SERIES" && "$lock_krev" == "$KERNEL_REVISION" ]]; then
                echo "versions.lock already at ${KERNEL_VERSION}-${KERNEL_REVISION} — no changes"
                exit 0
            fi
            # Rewrite pinned version, series, and revision in place
            sed -i \
                -e "s|^: \"\${KERNEL_VERSION:=.*}\"|: \"\${KERNEL_VERSION:=$KERNEL_VERSION}\"|" \
                -e "s|^: \"\${KERNEL_SERIES:=.*}\"|: \"\${KERNEL_SERIES:=$KERNEL_SERIES}\"|" \
                -e "s|^: \"\${KERNEL_REVISION:=.*}\"|: \"\${KERNEL_REVISION:=$KERNEL_REVISION}\"|" \
                "$_VERSIONS_LOCK"
            echo "versions.lock updated: ${lock_kver}-${lock_krev} → ${KERNEL_VERSION}-${KERNEL_REVISION} (source: $_SOURCE)"
            ;;
    esac
fi
