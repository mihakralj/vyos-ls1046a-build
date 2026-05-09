#!/usr/bin/env bash
# fetch-kernel.sh — download a linux-X.Y.Z mainline/stable tarball and extract it
#
# Usage:
#   ./scripts/fetch-kernel.sh              # auto-picks latest of $KERNEL_SERIES
#                                          # (default: 6.18)
#   ./scripts/fetch-kernel.sh 6.18.26      # pins a specific version
#
# Side effects:
#   work/linux-<VERSION>.tar.xz         (cached; not re-downloaded if present)
#   work/linux-<VERSION>/                (extracted source tree)
#   work/.kernel-version                 (contains "<VERSION>")

set -euo pipefail
source "$(dirname "$0")/common.sh"

need curl tar jq

# Priority order for the target kernel version:
#   1. positional arg ($1)                  — explicit CLI override
#   2. KERNEL_VERSION env / versions.lock    — persistent pin
#   3. kernel.org latest of $KERNEL_SERIES   — floating (default series 6.18)
if [[ -f "$REPO_ROOT/versions.lock" ]]; then
    # shellcheck disable=SC1091
    source "$REPO_ROOT/versions.lock"
fi

KERNEL_SERIES="${KERNEL_SERIES:-6.18}"

VERSION="${1:-${KERNEL_VERSION:-}}"
if [[ -z "$VERSION" ]]; then
    info "Resolving latest linux-${KERNEL_SERIES}.y from kernel.org…"
    VERSION="$(latest_stable_y "$KERNEL_SERIES")"
    [[ -n "$VERSION" ]] || err "Could not resolve latest ${KERNEL_SERIES}.y version"
else
    dim "Using pinned kernel version: $VERSION"
fi

# Validate shape
[[ "$VERSION" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || err "invalid version: '$VERSION' (expected X.Y.Z)"

ok "Target kernel: linux-${VERSION}"

TARBALL="${WORK_DIR}/linux-${VERSION}.tar.xz"
SRCDIR="${WORK_DIR}/linux-${VERSION}"
URL_BASE="https://cdn.kernel.org/pub/linux/kernel/v6.x"

# ── Download ────────────────────────────────────────────────────────────
if [[ -f "$TARBALL" ]]; then
    dim "tarball cached: $TARBALL"
else
    info "Downloading ${URL_BASE}/linux-${VERSION}.tar.xz"
    curl -fL --progress-bar -o "$TARBALL" "${URL_BASE}/linux-${VERSION}.tar.xz" \
        || err "download failed"
    ok "downloaded $(du -h "$TARBALL" | cut -f1)"
fi

# ── Extract ─────────────────────────────────────────────────────────────
if [[ -d "$SRCDIR" && -f "$SRCDIR/Makefile" ]]; then
    dim "source tree present: $SRCDIR"
else
    info "Extracting tarball…"
    rm -rf "$SRCDIR"
    tar -C "$WORK_DIR" -xJf "$TARBALL"
    [[ -f "$SRCDIR/Makefile" ]] || err "extract produced no Makefile at $SRCDIR"
    ok "extracted to $SRCDIR"
fi

# ── Record version (legacy marker, kept for backward compat) ────────────
echo "$VERSION" > "$WORK_DIR/.kernel-version"

# ── Normalised state: report old vs new ─────────────────────────────────
# Identity for the kernel is the version string. fetch_state_write returns
# 0 on unchanged, 10 on new/changed; we propagate that as our own exit code.
set +e
fetch_state_write "kernel" "$VERSION"
STATE_RC=$?
set -e

# ── Summary ─────────────────────────────────────────────────────────────
KVER=$(awk '/^VERSION/{v=$3} /^PATCHLEVEL/{p=$3} /^SUBLEVEL/{s=$3} END{print v"."p"."s}' \
    "$SRCDIR/Makefile")
[[ "$KVER" == "$VERSION" ]] || warn "Makefile reports $KVER, expected $VERSION"

ok "kernel ready: ${SRCDIR} (${KVER})"
exit "$STATE_RC"