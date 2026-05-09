#!/usr/bin/env bash
# common.sh — shared helpers, sourced by all other scripts
#
# Not executable on its own. Source it like:
#   source "$(dirname "$0")/common.sh"

# Resolve repo root regardless of caller CWD
if [[ -z "${REPO_ROOT:-}" ]]; then
    REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
    export REPO_ROOT
fi

export WORK_DIR="${REPO_ROOT}/work"
export SCRIPTS_DIR="${REPO_ROOT}/scripts"

# Colour-aware logging (no colour when not a TTY)
if [[ -t 1 ]]; then
    _C_BLUE=$'\033[1;34m'; _C_YEL=$'\033[1;33m'; _C_RED=$'\033[1;31m'
    _C_GRN=$'\033[1;32m'; _C_DIM=$'\033[2m';    _C_RST=$'\033[0m'
else
    _C_BLUE=''; _C_YEL=''; _C_RED=''; _C_GRN=''; _C_DIM=''; _C_RST=''
fi

# CI-aware logging. When running under GitHub Actions ($GITHUB_ACTIONS=true)
# or any CI system ($CI=true), prepend a UTC timestamp to every log line so
# long gaps between messages are obvious in the job log. Timestamps can be
# force-enabled with LOG_TIMESTAMPS=1 / force-disabled with LOG_TIMESTAMPS=0.
_ts() {
    if [[ "${LOG_TIMESTAMPS:-auto}" == "0" ]]; then return; fi
    if [[ "${LOG_TIMESTAMPS:-auto}" == "1" \
       || "${GITHUB_ACTIONS:-}" == "true" \
       || "${CI:-}" == "true" ]]; then
        printf '[%s] ' "$(date -u +%H:%M:%S)"
    fi
}

info()  { printf '%s%s==>%s %s\n'      "$(_ts)" "$_C_BLUE" "$_C_RST" "$*"; }
ok()    { printf '%s%s ✓%s %s\n'       "$(_ts)" "$_C_GRN"  "$_C_RST" "$*"; }
warn()  { printf '%s%s⚠ %s%s\n'        "$(_ts)" "$_C_YEL"  "$*"      "$_C_RST"; }
err()   { printf '%s%s✗ %s%s\n'        "$(_ts)" "$_C_RED"  "$*"      "$_C_RST" >&2; exit 1; }
dim()   { printf '%s%s%s%s\n'          "$(_ts)" "$_C_DIM"  "$*"      "$_C_RST"; }

# GitHub Actions log-group markers. Under GHA these collapse a range of lines
# into an expandable block; everywhere else they are silent no-ops. Use:
#   begin_group "label"; ...work... ; end_group
begin_group() {
    [[ "${GITHUB_ACTIONS:-}" == "true" ]] && printf '::group::%s\n' "$*"
    return 0
}
end_group() {
    [[ "${GITHUB_ACTIONS:-}" == "true" ]] && printf '::endgroup::\n'
    return 0
}

# Fetch latest stable version from kernel.org releases.json for a given X.Y series.
# Tries the "stable" moniker first (current mainline), falls back to "longterm".
# Prints e.g. "6.18.26" to stdout.
latest_stable_y() {
    local series="${1:-6.18}"
    local json
    json=$(curl -fsSL https://www.kernel.org/releases.json) || return 1
    # Try stable moniker first, then longterm.
    local v
    v=$(printf '%s' "$json" | jq -r --arg s "${series}." \
        '.releases[] | select(.moniker=="stable") | select(.version|startswith($s)) | .version' \
        | head -1)
    if [[ -z "$v" ]]; then
        v=$(printf '%s' "$json" | jq -r --arg s "${series}." \
            '.releases[] | select(.moniker=="longterm") | select(.version|startswith($s)) | .version' \
            | head -1)
    fi
    [[ -n "$v" ]] || return 1
    printf '%s\n' "$v"
}

# Backward-compat alias for legacy callers (build-kernel.sh, etc.).
latest_6_6_y() { latest_stable_y 6.6; }

# Cross-platform nproc
nproc_any() { nproc 2>/dev/null || sysctl -n hw.ncpu 2>/dev/null || echo 4; }

# ── ccache integration ─────────────────────────────────────────────────
#
# Wrap gcc/HOSTCC with ccache when available. Kernel + OOT module builds
# use plenty of headers and rebuild the same TUs across iterations
# (ask-N → ask-N+1 typically touches <5 files), so ccache hit rates of
# 90%+ are normal once the cache is warm.
#
# Sets two globals when ccache is on PATH:
#   CCACHE_MAKE_ARGS  — array; pass to `make` to wrap CC + HOSTCC
#   CCACHE_ENABLED    — "1" if active, unset otherwise
#
# Exports CCACHE_DIR / CCACHE_MAXSIZE / CCACHE_SLOPPINESS with sensible
# defaults if the user has not already set them. The sloppiness list is
# the ccache-recommended set for kernel builds (kbuild stamps every TU
# with build time + path).
#
# Caller may opt out by setting CCACHE_DISABLE=1 in the environment.
setup_ccache() {
    CCACHE_MAKE_ARGS=()
    [[ "${CCACHE_DISABLE:-0}" == "1" ]] && return 0
    command -v ccache >/dev/null 2>&1 || return 0

    export CCACHE_DIR="${CCACHE_DIR:-$HOME/.ccache}"
    export CCACHE_MAXSIZE="${CCACHE_MAXSIZE:-20G}"
    export CCACHE_SLOPPINESS="${CCACHE_SLOPPINESS:-time_macros,file_macro,include_file_mtime,include_file_ctime,pch_defines}"
    export CCACHE_COMPRESS="${CCACHE_COMPRESS:-1}"

    # Prepend Debian's /usr/lib/ccache shim dir to PATH so dpkg-buildpackage
    # / autotools / make-without-explicit-CC flows (build-ask-iptables.sh,
    # build-ask-ppp.sh) transparently route gcc/g++/cc through ccache. The
    # kernel build's own `make CC="ccache gcc"` (CCACHE_MAKE_ARGS below)
    # still wins for kbuild because it sets CC explicitly.
    if [[ -d /usr/lib/ccache && ":$PATH:" != *":/usr/lib/ccache:"* ]]; then
        export PATH="/usr/lib/ccache:$PATH"
    fi

    CCACHE_MAKE_ARGS=(
        "CC=ccache ${CROSS_COMPILE:-}gcc"
        "HOSTCC=ccache gcc"
    )
    CCACHE_ENABLED=1
    return 0
}

# Print a one-line ccache status (call after setup_ccache + a build run).
ccache_status_line() {
    [[ "${CCACHE_ENABLED:-0}" == "1" ]] || return 0
    command -v ccache >/dev/null 2>&1 || return 0
    local hits misses
    hits=$(ccache -s 2>/dev/null | awk -F: '/[Cc]acheable calls.*hit/ {gsub(/[^0-9]/,"",$2); print $2; exit} /^cache hit/ {gsub(/[^0-9]/,"",$2); print $2; exit}')
    misses=$(ccache -s 2>/dev/null | awk -F: '/[Cc]acheable calls.*miss/ {gsub(/[^0-9]/,"",$2); print $2; exit} /^cache miss/ {gsub(/[^0-9]/,"",$2); print $2; exit}')
    dim "   ccache:        dir=$CCACHE_DIR hits=${hits:-?} misses=${misses:-?} max=$CCACHE_MAXSIZE"
}

# Require command(s) on PATH; exit cleanly if missing
need() {
    local missing=()
    for c in "$@"; do command -v "$c" >/dev/null 2>&1 || missing+=("$c"); done
    if (( ${#missing[@]} )); then
        err "missing required command(s): ${missing[*]}"
    fi
}

# ── Shared classification: ASK upstream path → tier ────────────────────
# Single source of truth for "what counts as a kernel patch (T2)" so that
# any future consumer needs.
#
#   T1  direct-apply  — userspace / OOT modules / lib patches
#   T2  port required — touches patches/kernel/* (needs re-derivation onto 6.6)
#   T3  meta          — README / Makefile / build scripts / anything else
#
# classify_path <path>           → echoes T1|T2|T3
classify_path() {
    case "$1" in
        patches/kernel/*)                                     echo "T2" ;;
        cdx/*|fci/*|auto_bridge/*)                            echo "T1" ;;
        cmm/*|dpa_app/*)                                      echo "T1" ;;
        patches/fmc/*|patches/fmlib/*|patches/lib*|\
        patches/iptables*|patches/ppp/*|patches/rp-pppoe/*)   echo "T1" ;;
        *)                                                    echo "T3" ;;
    esac
}

# classify_commit <git-dir> <sha> → echoes the dominant tier (T2 > T1 > T3)
# Reads files-changed list from the given bare/normal git dir.
classify_commit() {
    local gitdir="$1" sha="$2"
    local files has_t1=0 has_t2=0 has_t3=0 t
    files=$(git --git-dir="$gitdir" show --name-only --format= "$sha" | grep -v '^$' || true)
    while IFS= read -r f; do
        [[ -z "$f" ]] && continue
        t=$(classify_path "$f")
        case "$t" in
            T2) has_t2=1 ;;
            T1) has_t1=1 ;;
            T3) has_t3=1 ;;
        esac
    done <<< "$files"
    if   (( has_t2 )); then echo "T2"
    elif (( has_t1 )); then echo "T1"
    else                    echo "T3"
    fi
}

# ── Shared patch-text helpers ──────────────────────────────────────────

# sanitise_path <path> → safe filename token (matches the awk gsub used in
# split_patch_per_file so that shell-side and awk-side names agree).
# IMPORTANT: uses printf (no trailing newline) — `echo | tr` would translate
# the appended newline into '_' and produce mismatched names.
sanitise_path() { printf '%s' "$1" | tr -c 'A-Za-z0-9._-' '_'; }

# split_patch_per_file <input.patch> <outdir>
#   Walks a unified-diff patch and writes one chunk file per source file
#   touched, named "<sanitised-path>.chunk", plus a manifest "_files".
#   Strips `index aaa..bbb` blob-SHA lines (these change every kernel bump
#   even when hunks are identical, and would produce false-positive drift).
#
# Implementation: lsdiff + filterdiff from patchutils. This replaces a
# hand-rolled awk parser that did not correctly handle binary diffs, mode
# changes ("new file mode 100755"), rename-detected blocks ("rename from/
# rename to"), or "GIT binary patch" sections. patchutils is the reference
# C implementation Debian/Fedora maintainers have used for 20+ years.
#
# Requires: lsdiff, filterdiff (package: patchutils).
split_patch_per_file() {
    local infile="$1" outdir="$2"
    mkdir -p "$outdir"
    : > "$outdir/_files"
    # lsdiff --strip=1 lists the b-side path of each diff-git block once.
    lsdiff --strip=1 "$infile" | while IFS= read -r path; do
        [[ -z "$path" ]] && continue
        local safe
        safe=$(sanitise_path "$path")
        # filterdiff matches -i globs against the RAW patch headers, i.e. the
        # full "a/<path>" / "b/<path>" form. Both sides are supplied so that
        # renames and add/delete blocks (where one side is /dev/null) still
        # match. Using "*/<path>" would over-match subpaths (e.g. "Makefile"
        # would match every "*/Makefile"). Strip `index aaa..bbb` blob-SHA
        # lines to avoid false-positive drift when the upstream tree re-hashes.
        filterdiff -i "a/$path" -i "b/$path" "$infile" \
            | grep -v '^index [0-9a-f]\+\.\.[0-9a-f]\+' \
            > "$outdir/$safe.chunk"
        echo "$path" >> "$outdir/_files"
    done
}

# ── Normalised "old vs new" state tracking for fetchers ────────────────
#
# All three fetchers (kernel / reference / upstream) write a state file with
# a single content-identity string (kernel version, or commit SHA). The
# helper below is the single source of truth for:
#   - reading the previous identity
#   - writing the new one
#   - logging the transition (new / unchanged / changed A → B)
#   - preserving the previous value to .prev so callers can diff later
#
# State file layout:  work/.<name>.state
#     ID=<identity>
#     TIMESTAMP=<ISO-8601 UTC>
#
# Conventional exit codes for fetchers using this helper:
#   0   unchanged (cache hit; identity identical to previous run)
#   10  changed  (first fetch, or identity differs from previous run)
#   >0 non-10    error (via err())

# fetch_state_read <name>      → echoes previous ID (empty if none)
fetch_state_read() {
    local name="$1" state="$WORK_DIR/.${name}.state"
    [[ -f "$state" ]] || { echo ""; return 0; }
    # shellcheck disable=SC2002
    cat "$state" | awk -F= '$1=="ID"{print $2; exit}'
}

# fetch_state_write <name> <id>
#   Preserves previous state to .prev, writes new state, and returns:
#     - echoes one of: "new" | "unchanged" | "changed"
#     - exports FETCH_PREV_ID / FETCH_NEW_ID for the caller
#     - returns 0 on unchanged, 10 on new/changed (fetcher should propagate)
fetch_state_write() {
    local name="$1" new_id="$2"
    local state="$WORK_DIR/.${name}.state"
    local prev_state="${state}.prev"
    local prev_id=""
    [[ -f "$state" ]] && prev_id=$(fetch_state_read "$name")

    # Preserve previous for caller diffing
    [[ -f "$state" ]] && cp "$state" "$prev_state"

    {
        echo "ID=$new_id"
        echo "TIMESTAMP=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    } > "$state"

    export FETCH_PREV_ID="$prev_id"
    export FETCH_NEW_ID="$new_id"

    if [[ -z "$prev_id" ]]; then
        ok "[$name] new: ${new_id:0:40}"
        return 10
    elif [[ "$prev_id" == "$new_id" ]]; then
        dim "[$name] unchanged: ${new_id:0:40}"
        return 0
    else
        ok "[$name] changed: ${prev_id:0:12} → ${new_id:0:12}"
        return 10
    fi
}

mkdir -p "$WORK_DIR"
