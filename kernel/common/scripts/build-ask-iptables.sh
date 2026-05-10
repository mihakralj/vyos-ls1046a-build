#!/usr/bin/env bash
# build-ask-iptables.sh — natively build a patched Debian iptables source package
# on arm64 with the NXP ASK QOSMARK/QOSCONNMARK extensions added.
#
# This single script covers both Phase 3 (xtables libxt_QOS*.so extensions)
# and Phase 4 (patched iptables binary). Upstream ASK does not ship a
# pre-baked patch for iptables; instead it provides the four new extension
# source files and four new netfilter UAPI headers under
# `iptables-extensions/` in the upstream tree. This script copies them into
# the unpacked Debian iptables source as new files and lets the iptables
# build's autodiscovery (`extensions/GNUmakefile.in` globs `libxt_*.c`)
# pick them up. The headers are installed into the iptables source's
# `include/linux/netfilter/` so the extensions' `#include
# <linux/netfilter/xt_QOSMARK.h>` resolves at build time.
#
# Files copied from upstream `iptables-extensions/`:
#   libxt_qosmark.c      libxt_QOSMARK.c
#   libxt_qosconnmark.c  libxt_QOSCONNMARK.c
#   include/linux/netfilter/xt_qosmark.h     xt_QOSMARK.h
#   include/linux/netfilter/xt_qosconnmark.h xt_QOSCONNMARK.h
#
# Prerequisites:
#   - release/oot-modules/iptables-extensions/ present in this repo
#     (in-tree post-redistribution; previously pulled at build time from
#     in-tree under release/oot-modules/iptables-extensions/).
#   - Host is arm64 with the Debian build toolchain:
#       build-essential dpkg-dev debhelper devscripts dh-autoreconf quilt
#       libmnl-dev libnftnl-dev libnetfilter-conntrack-dev libnfnetlink-dev
#     The CI workflow installs these in its "Install toolchain" step.
#
# Pipeline position: after build-ask-modules.sh (independent; xtables
# rebuild has no FMan SDK dependency).
#
# Usage:
#   ./scripts/build-ask-iptables.sh
#   ./scripts/build-ask-iptables.sh --dist bookworm   # target distro (default: bookworm)
#   ./scripts/build-ask-iptables.sh --arch arm64      # default; target arch
#
# Outputs (typical Debian iptables binary set, +ask suffix):
#   work/build/iptables_<ver>+ask1_arm64.deb
#   work/build/libxtables12_<ver>+ask1_arm64.deb
#   work/build/libip4tc2_<ver>+ask1_arm64.deb
#   work/build/libip6tc2_<ver>+ask1_arm64.deb
#   work/build/iptables-dev_<ver>+ask1_arm64.deb
#   work/build/iptables_<ver>+ask1_arm64.{changes,buildinfo}
#   work/ask-iptables/build.log
#
# Exit codes:
#   0  .debs built and placed in work/build/
#   1  missing prerequisites, apt-get source failure, or build failure
#   2  source files missing in upstream mirror

set -euo pipefail
source "$(dirname "$0")/common.sh"

# ── Config ──────────────────────────────────────────────────────────────
DIST="${DIST:-bookworm}"
TARGET_ARCH="${TARGET_ARCH:-arm64}"
REVISION_SUFFIX="${REVISION_SUFFIX:-+ask1}"
SRC_PKG="iptables"

# Files to copy from upstream → Debian source tree
# Format: "<upstream-path>:<dest-relative-to-iptables-source>"
ASK_FILES=(
    "iptables-extensions/libxt_qosmark.c:extensions/libxt_qosmark.c"
    "iptables-extensions/libxt_QOSMARK.c:extensions/libxt_QOSMARK.c"
    "iptables-extensions/libxt_qosconnmark.c:extensions/libxt_qosconnmark.c"
    "iptables-extensions/libxt_QOSCONNMARK.c:extensions/libxt_QOSCONNMARK.c"
    "iptables-extensions/include/linux/netfilter/xt_qosmark.h:include/linux/netfilter/xt_qosmark.h"
    "iptables-extensions/include/linux/netfilter/xt_QOSMARK.h:include/linux/netfilter/xt_QOSMARK.h"
    "iptables-extensions/include/linux/netfilter/xt_qosconnmark.h:include/linux/netfilter/xt_qosconnmark.h"
    "iptables-extensions/include/linux/netfilter/xt_QOSCONNMARK.h:include/linux/netfilter/xt_QOSCONNMARK.h"
)

while (( $# )); do
    case "$1" in
        --dist)    DIST="${2:?--dist needs arg}";          shift 2 ;;
        --arch)    TARGET_ARCH="${2:?--arch needs arg}";   shift 2 ;;
        -h|--help) sed -n '1,50p' "$0"; exit 0 ;;
        *)         err "unknown arg: $1" ;;
    esac
done

need apt-get dpkg-source dpkg-buildpackage git gcc

[[ -f "$REPO_ROOT/versions.lock" ]] || err "versions.lock not found"
# shellcheck disable=SC1091
source "$REPO_ROOT/versions.lock"

host_arch=$(dpkg --print-architecture)
[[ "$host_arch" == "$TARGET_ARCH" ]] \
    || err "this script is arm64-native; host=$host_arch, target=$TARGET_ARCH"

setup_ccache
[[ "${CCACHE_ENABLED:-0}" == "1" ]] && \
    dim "ccache: $(ccache --version | head -1) (PATH-shimmed for dpkg-buildpackage)"

# ── Resolve in-tree iptables-extensions source ────────────────────────
#
# Post-redistribution (2026-05-05): the iptables-extensions/ tree lives
# in-tree under release/oot-modules/iptables-extensions/, imported from
# the now-archived mihakralj/ask-ls1046a-6.6 @ 97d950e.
EXT_SRC="$REPO_ROOT/release/oot-modules/iptables-extensions"
[[ -d "$EXT_SRC" ]] \
    || err "release/oot-modules/iptables-extensions missing — repo corrupt?"

missing=()
for entry in "${ASK_FILES[@]}"; do
    # The ASK_FILES array still uses "iptables-extensions/<path>" prefixes
    # for src; strip that prefix and look up under $EXT_SRC.
    src="${entry%%:*}"
    rel="${src#iptables-extensions/}"
    [[ -f "$EXT_SRC/$rel" ]] || missing+=("$src")
done
if (( ${#missing[@]} )); then
    warn "missing under $EXT_SRC:"
    for m in "${missing[@]}"; do warn "    $m"; done
    err "cannot synthesize ASK iptables overlay; in-tree source incomplete"
fi

# ── Workspace ──────────────────────────────────────────────────────────
WS="$WORK_DIR/ask-iptables"
SRC_ROOT="$WS/src"
OUT_DIR="$WORK_DIR/build"
rm -rf "$WS"
mkdir -p "$SRC_ROOT" "$OUT_DIR"

info "building patched iptables (Debian source rebuild)"
dim "   source pkg:     $SRC_PKG"
dim "   ext sources:    $EXT_SRC (in-tree)"
dim "   target arch:    $TARGET_ARCH"
dim "   compiler:       $(gcc --version 2>/dev/null | head -1)"
dim "   revision tag:   $REVISION_SUFFIX"
dim "   workspace:      $WS"
dim "   strategy:       copy 8 ASK source files into Debian source tree"

# ── Fetch source package ───────────────────────────────────────────────
begin_group "apt-get source $SRC_PKG"
(
    cd "$SRC_ROOT"
    if ! apt-cache showsrc "$SRC_PKG" 2>/dev/null | grep -q '^Package:'; then
        warn "no deb-src for $SRC_PKG; attempting to enable"
        if [[ -w /etc/apt/sources.list ]]; then
            sed -i 's/^# *deb-src /deb-src /' /etc/apt/sources.list || true
            sudo apt-get update -qq || apt-get update -qq || true
        fi
    fi
    apt-get source "$SRC_PKG" 2>&1 | tail -10
) > "$WS/apt-source.log" 2>&1 \
    || { warn "apt-get source failed; see $WS/apt-source.log"; tail -40 "$WS/apt-source.log" >&2; err "cannot download $SRC_PKG source"; }

SRC_DIR=$(find "$SRC_ROOT" -mindepth 1 -maxdepth 1 -type d -name "${SRC_PKG}-*" | head -1)
[[ -d "$SRC_DIR" ]] || err "no ${SRC_PKG}-* directory after apt-get source"
UPSTREAM_VER=$(basename "$SRC_DIR" | sed -E "s/^${SRC_PKG}-//")
ok "fetched $SRC_PKG $UPSTREAM_VER into $(basename "$SRC_DIR")"
end_group

# ── Copy ASK source files into the Debian source tree ──────────────────
begin_group "overlay ASK QOSMARK/QOSCONNMARK source files"
overlay_root="$WS/overlay"
rm -rf "$overlay_root"
mkdir -p "$overlay_root"

# Stage the files first under $overlay_root so we can build a single
# unified diff against the pristine source for debian/patches/series.
for entry in "${ASK_FILES[@]}"; do
    src="${entry%%:*}"
    dst="${entry#*:}"
    rel="${src#iptables-extensions/}"
    mkdir -p "$overlay_root/$(dirname "$dst")"
    cp "$EXT_SRC/$rel" "$overlay_root/$dst"
done
ok "staged $(( ${#ASK_FILES[@]} )) files under $overlay_root"

# Sanity: every destination must NOT already exist in the Debian source.
# If it does, upstream Debian or someone before us already added it and
# our overlay would silently clobber a file we don't want to fight with.
for entry in "${ASK_FILES[@]}"; do
    dst="${entry#*:}"
    if [[ -e "$SRC_DIR/$dst" ]]; then
        warn "  $dst already exists in $SRC_PKG-$UPSTREAM_VER (will overwrite)"
    fi
done

# Copy with directory creation. Preserve permissions defaults.
for entry in "${ASK_FILES[@]}"; do
    dst="${entry#*:}"
    install -D -m 0644 "$overlay_root/$dst" "$SRC_DIR/$dst"
done
ok "installed ASK overlay into $(basename "$SRC_DIR")"

# Generate a clean unified diff that adds these files (against /dev/null),
# so a downstream reader can reproduce exactly what we changed.
PATCH_FILE="$WS/0999-ask-qosmark-extensions.patch"
{
    printf 'Description: Add NXP ASK QOSMARK/QOSCONNMARK xtables extensions\n'
    printf ' Copies four new libxt_*.c extension sources and their UAPI\n'
    printf ' headers from the in-tree release/oot-modules/iptables-extensions/.\n'
    printf ' These compile into libxt_{qos,QOS}{mark,connmark}.so xtables\n'
    printf ' plugins shipped inside the iptables binary package.\n'
    printf 'Origin: imported from mihakralj/ask-ls1046a-6.6 @ 97d950ed\n'
    printf 'Forwarded: not-needed\n'
    printf 'Last-Update: %s\n\n' "$(date +%F)"
    for entry in "${ASK_FILES[@]}"; do
        dst="${entry#*:}"
        printf -- '--- /dev/null\n+++ b/%s\n' "$dst"
        # Generate the +lines block; count lines for hunk header.
        nlines=$(wc -l < "$overlay_root/$dst")
        printf -- '@@ -0,0 +1,%d @@\n' "$nlines"
        sed 's/^/+/' "$overlay_root/$dst"
    done
} > "$PATCH_FILE"
ok "synthesized debian-style patch ($(wc -l < "$PATCH_FILE") lines)"

# Register the patch in debian/patches/series for 3.0 (quilt) format,
# so dpkg-source preserves provenance and downstream rebuilds via
# `dpkg-source -x` regenerate it cleanly.
if [[ -f "$SRC_DIR/debian/source/format" ]] \
    && grep -q '3.0 (quilt)' "$SRC_DIR/debian/source/format"; then
    mkdir -p "$SRC_DIR/debian/patches"
    cp "$PATCH_FILE" "$SRC_DIR/debian/patches/0999-ask-qosmark-extensions.patch"
    touch "$SRC_DIR/debian/patches/series"
    grep -qx '0999-ask-qosmark-extensions.patch' \
        "$SRC_DIR/debian/patches/series" 2>/dev/null \
        || echo '0999-ask-qosmark-extensions.patch' \
            >> "$SRC_DIR/debian/patches/series"
    ok "registered overlay in debian/patches/series"
fi
end_group

# ── Bump version with ASK suffix ───────────────────────────────────────
begin_group "record version + changelog entry"
export DEBEMAIL="${DEBEMAIL:-ci@localhost}"
export DEBFULLNAME="${DEBFULLNAME:-ASK LTS 6.6 Autobuilder}"
NEW_VER="${UPSTREAM_VER}${REVISION_SUFFIX}"
(
    cd "$SRC_DIR"
    if command -v dch >/dev/null 2>&1; then
        dch --distribution "$DIST" --newversion "$NEW_VER" \
            "Apply NXP ASK QOSMARK/QOSCONNMARK extensions (in-tree)."
    else
        {
            printf '%s (%s) %s; urgency=medium\n\n' "$SRC_PKG" "$NEW_VER" "$DIST"
            printf '  * Apply NXP ASK QOSMARK/QOSCONNMARK extensions (in-tree).\n\n'
            printf ' -- %s <%s>  %s\n\n' \
                "$DEBFULLNAME" "$DEBEMAIL" "$(date -R)"
            cat debian/changelog
        } > debian/changelog.new
        mv debian/changelog.new debian/changelog
    fi
)
ok "new version: $NEW_VER"

# `dch --newversion` renames the working directory from
# <pkg>-<ver> to <pkg>-<new_ver>. Re-resolve SRC_DIR.
NEW_SRC_DIR=$(find "$SRC_ROOT" -mindepth 1 -maxdepth 1 -type d \
    -name "${SRC_PKG}-${NEW_VER}" | head -1)
if [[ -n "$NEW_SRC_DIR" && -d "$NEW_SRC_DIR" ]]; then
    SRC_DIR="$NEW_SRC_DIR"
elif [[ ! -d "$SRC_DIR" ]]; then
    SRC_DIR=$(find "$SRC_ROOT" -mindepth 1 -maxdepth 1 -type d \
        -name "${SRC_PKG}-*" | head -1)
fi
[[ -d "$SRC_DIR" ]] || err "source dir vanished after dch (looked for ${SRC_PKG}-${NEW_VER})"
dim "   source dir now: $(basename "$SRC_DIR")"
end_group

# ── Native build ────────────────────────────────────────────────────────
begin_group "dpkg-buildpackage (native $TARGET_ARCH)"
BUILD_LOG="$WS/build.log"
info "  target arch: $TARGET_ARCH"
dim "  log:         $BUILD_LOG"
set +e
(
    cd "$SRC_DIR"
    export DEB_BUILD_OPTIONS="nocheck parallel=$(nproc_any)"
    dpkg-buildpackage \
        --build=binary \
        -uc -us \
        2>&1
) > "$BUILD_LOG"
rc=$?
set -e

if (( rc != 0 )); then
    warn "last 60 lines of $BUILD_LOG:"
    tail -60 "$BUILD_LOG" >&2
    err "dpkg-buildpackage failed (exit $rc)"
fi
end_group

# ── Collect artefacts ───────────────────────────────────────────────────
begin_group "collect produced .debs"
shopt -s nullglob
produced=( "$SRC_ROOT"/*"${REVISION_SUFFIX}"*"_${TARGET_ARCH}.deb"
           "$SRC_ROOT"/*"${REVISION_SUFFIX}"*"_all.deb"
           "$SRC_ROOT"/*"${REVISION_SUFFIX}"*".changes"
           "$SRC_ROOT"/*"${REVISION_SUFFIX}"*".buildinfo" )
shopt -u nullglob

if (( ${#produced[@]} == 0 )); then
    err "build completed but no .deb artefacts found in $SRC_ROOT"
fi

for f in "${produced[@]}"; do
    cp -v "$f" "$OUT_DIR/" | sed 's|^|   |'
done
end_group

# ── Summary ─────────────────────────────────────────────────────────────
echo
info "── ask-iptables build summary ──"
printf '   source:         %s %s\n'    "$SRC_PKG" "$UPSTREAM_VER"
printf '   new version:    %s\n'       "$NEW_VER"
printf '   ext sources:    %s\n'       "$EXT_SRC"
printf '   target arch:    %s\n'       "$TARGET_ARCH"
printf '   ASK files:      %d copied\n' "${#ASK_FILES[@]}"
printf '   produced:\n'
for f in "${produced[@]}"; do
    if [[ "$f" == *.deb ]]; then
        printf '     %s (%s)\n' \
            "$(basename "$f")" "$(du -h "$f" | cut -f1)"
    fi
done

# Explicit success exit: the last executed expression in the summary
# loop must not decide the script's exit code when set -e is active.
exit 0

# ── Note on what's inside ──────────────────────────────────────────────
# The four new extension sources compile into:
#   /usr/lib/<triple>/xtables/libxt_qosmark.so
#   /usr/lib/<triple>/xtables/libxt_QOSMARK.so
#   /usr/lib/<triple>/xtables/libxt_qosconnmark.so
#   /usr/lib/<triple>/xtables/libxt_QOSCONNMARK.so
# delivered in the iptables binary package alongside the patched iptables
# binary. The kernel-side xt_QOSMARK / xt_QOSCONNMARK headers are
# installed by the kernel linux-libc-dev .deb (via 003-ask-kernel-hooks.patch);
# the userspace headers we copied here let the extensions build against
# matching layouts.