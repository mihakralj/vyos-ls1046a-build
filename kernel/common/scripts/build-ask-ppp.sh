#!/usr/bin/env bash
# build-ask-ppp.sh — natively build patched ppp and rp-pppoe Debian source
# packages on arm64 with the NXP ASK PPPoE fast-path patches applied.
#
# This single script covers Phase 5 (both packages). Each sub-build is
# independent: if ppp fails, rp-pppoe is still attempted, and vice versa.
# The final exit code is 0 if at least one package built and none of the
# attempted packages failed after applying.
#
# The patches are small source-level modifications of existing files:
#   patches/ppp/01-nxp-ask-ifindex.patch       (44 lines, pppd/ipcp.c)
#   patches/rp-pppoe/01-nxp-ask-cmm-relay.patch (307 lines, src/Makefile.in + src/relay.c)
#
# Prerequisites:
#   - release/userspace-patches/{ppp,rp-pppoe}/ present in this repo
#   - Host is arm64 with the Debian build toolchain:
#     dpkg-dev, debhelper, devscripts, quilt, libpcap0.8-dev,
#     libpam0g-dev, libssl-dev, and deb-src enabled.
#
# Pipeline position: after build-ask-iptables.sh (independent; runs as
# step 8b layer 3 under --ask-extras).
#
# Usage:
#   ./scripts/build-ask-ppp.sh
#   ./scripts/build-ask-ppp.sh --arch arm64
#   ./scripts/build-ask-ppp.sh --only ppp         # build only ppp
#   ./scripts/build-ask-ppp.sh --only rp-pppoe    # build only rp-pppoe
#
# Outputs:
#   work/build/ppp_<ver>+ask1_arm64.deb
#   work/build/pppoe_<ver>+ask1_arm64.deb     (from rp-pppoe source)
#   work/build/ppp-dev_<ver>+ask1_all.deb, etc.
#   work/ask-ppp/{ppp,rp-pppoe}/build.log
#
# Exit codes:
#   0  all requested packages built (or at least one built, none failed)
#   1  missing prerequisites, all attempted packages failed
#   2  a patch failed to apply

set -euo pipefail
source "$(dirname "$0")/common.sh"

# ── Config ──────────────────────────────────────────────────────────────
DIST="${DIST:-bookworm}"
TARGET_ARCH="${TARGET_ARCH:-arm64}"
REVISION_SUFFIX="${REVISION_SUFFIX:-+ask1}"
ONLY=""

while (( $# )); do
    case "$1" in
        --dist)    DIST="${2:?--dist needs arg}";          shift 2 ;;
        --arch)    TARGET_ARCH="${2:?--arch needs arg}";   shift 2 ;;
        --only)    ONLY="${2:?--only needs arg}";          shift 2 ;;
        -h|--help) sed -n '1,42p' "$0"; exit 0 ;;
        *)         err "unknown arg: $1" ;;
    esac
done

need apt-get dpkg-source dpkg-buildpackage git patch gcc

[[ -f "$REPO_ROOT/versions.lock" ]] || err "versions.lock not found"
# shellcheck disable=SC1091
source "$REPO_ROOT/versions.lock"

host_arch=$(dpkg --print-architecture)
[[ "$host_arch" == "$TARGET_ARCH" ]] \
    || err "this script is arm64-native; host=$host_arch, target=$TARGET_ARCH"

setup_ccache
[[ "${CCACHE_ENABLED:-0}" == "1" ]] && \
    dim "ccache: $(ccache --version | head -1) (PATH-shimmed for dpkg-buildpackage)"

# ── Resolve in-tree userspace patches ─────────────────────────────────
#
# Post-redistribution (2026-05-05): the ppp / rp-pppoe quilt-style
# patches live in-tree under release/userspace-patches/, imported from
# the now-archived mihakralj/ask-ls1046a-6.6 @ 97d950e.
USP_PATCH_ROOT="$REPO_ROOT/release/userspace-patches"
[[ -d "$USP_PATCH_ROOT/ppp" && -d "$USP_PATCH_ROOT/rp-pppoe" ]] \
    || err "release/userspace-patches/{ppp,rp-pppoe} missing — repo corrupt?"

# ── Shared workspace ───────────────────────────────────────────────────
WS="$WORK_DIR/ask-ppp"
OUT_DIR="$WORK_DIR/build"
rm -rf "$WS"
mkdir -p "$WS" "$OUT_DIR"

export DEBEMAIL="${DEBEMAIL:-ci@localhost}"
export DEBFULLNAME="${DEBFULLNAME:-ASK LTS 6.6 Autobuilder}"

info "building patched ppp + rp-pppoe (Debian source rebuilds)"
dim "   patch source:   $USP_PATCH_ROOT (in-tree)"
dim "   target arch:    $TARGET_ARCH"
dim "   compiler:       $(gcc --version 2>/dev/null | head -1)"
dim "   revision tag:   $REVISION_SUFFIX"

# ── Worker: build one source package ────────────────────────────────────
#
# Args:
#   $1  src_pkg       Debian source package name (e.g. "ppp", "rp-pppoe")
#   $2  patch_subpath upstream path (e.g. "patches/ppp/01-nxp-ask-ifindex.patch")
#
# Sets: BUILT_<pkg>=<path-to-first-produced-deb>  (on success)
#       FAIL_REASON_<pkg>=<short reason>          (on failure)
BUILT_SUMMARY=()
FAIL_SUMMARY=()

build_one() {
    local src_pkg="$1"
    local patch_subpath="$2"

    local sub="$WS/$src_pkg"
    local src_root="$sub/src"
    mkdir -p "$src_root"
    local patch_file="$sub/patch.diff"

    begin_group "build $src_pkg (+ASK patch)"

    # 1. Resolve in-tree patch (the patch_subpath strings still use the
    #    historical "patches/<pkg>/<file>" layout from the source repo,
    #    which we map onto release/userspace-patches/<pkg>/<file>).
    local rel="${patch_subpath#patches/}"
    local src_patch="$REPO_ROOT/release/userspace-patches/$rel"
    if [[ ! -f "$src_patch" ]]; then
        warn "  in-tree patch missing: $src_patch"
        FAIL_SUMMARY+=("$src_pkg: patch missing in release/userspace-patches/")
        end_group
        return 1
    fi
    cp "$src_patch" "$patch_file"
    ok "  staged patch ($(wc -l < "$patch_file") lines)"

    # 2. Fetch Debian source
    info "  apt-get source $src_pkg"
    if ! (cd "$src_root" && apt-get source "$src_pkg" > "$sub/apt-source.log" 2>&1); then
        warn "  apt-get source $src_pkg failed"
        tail -20 "$sub/apt-source.log" >&2 || true
        FAIL_SUMMARY+=("$src_pkg: apt-get source failed")
        end_group
        return 1
    fi

    local src_dir
    src_dir=$(find "$src_root" -mindepth 1 -maxdepth 1 -type d -name "${src_pkg}-*" | head -1)
    if [[ -z "$src_dir" || ! -d "$src_dir" ]]; then
        warn "  no ${src_pkg}-* directory after apt-get source"
        FAIL_SUMMARY+=("$src_pkg: source tree not found")
        end_group
        return 1
    fi
    local upstream_ver
    upstream_ver=$(basename "$src_dir" | sed -E "s/^${src_pkg}-//")
    ok "  fetched $src_pkg $upstream_ver"

    # 2b. Precondition check for rp-pppoe: the CMM-relay patch adds
    #     `#include <libcmm.h>` to src/relay.c. That header ships with
    #     the NXP ASK layer-2 userspace (libcmm/fmc/cmm packages), which
    #     in turn require the FMan SDK sources. When layer 2 has not
    #     been built and installed, the patched rp-pppoe cannot compile.
    #     Skip cleanly (not fail) so the pipeline reports the missing
    #     precondition rather than a confusing C compilation error.
    if [[ "$src_pkg" == "rp-pppoe" ]]; then
        if ! echo '#include <libcmm.h>' | gcc -E -x c - >/dev/null 2>&1; then
            warn "  rp-pppoe: libcmm.h not found — layer 2 (libcmm userspace) absent"
            dim "    the CMM-relay patch requires NXP ASK layer-2 headers;"
            dim "    skipping rp-pppoe until layer 2 is shipped."
            BUILT_SUMMARY+=("$src_pkg: SKIPPED (libcmm.h absent — layer 2 not present)")
            end_group
            return 0
        fi
    fi

    # 3. Dry-run the patch for clear diagnostics. We use git apply --check
    # against a throwaway git init in $src_dir so --3way fallback can resolve
    # context drift; mergiraf is wired via a .gitattributes drop.
    if [[ ! -d "$src_dir/.git" ]]; then
        ( cd "$src_dir" && git init -q && git config user.email ci@local && \
            git config user.name ci && git config gc.auto 0 && \
            git add -A && git commit -qm "$src_pkg pristine baseline" )
    fi
    cat > "$src_dir/.gitattributes" <<'GITATTR'
*.c     merge=mergiraf
*.h     merge=mergiraf
*.py    merge=mergiraf
*.json  merge=mergiraf
*.yml   merge=mergiraf
*.yaml  merge=mergiraf
*.toml  merge=mergiraf
*.xml   merge=mergiraf
GITATTR
    if ! (cd "$src_dir" && git apply --3way --check --whitespace=nowarn "$patch_file" 2>&1); then
        warn "  $src_pkg: patch does not apply cleanly (git apply --3way --check)"
        (cd "$src_dir" && git apply --3way --check --whitespace=nowarn "$patch_file") 2>&1 | tail -30 >&2
        FAIL_SUMMARY+=("$src_pkg: patch rejected by --3way dry-run")
        end_group
        return 2
    fi

    # 4. Apply and register
    (cd "$src_dir" && git apply --3way --whitespace=nowarn "$patch_file") \
        || { FAIL_SUMMARY+=("$src_pkg: patch apply failed post-dry-run"); end_group; return 1; }
    if [[ -f "$src_dir/debian/source/format" ]] \
        && grep -q '3.0 (quilt)' "$src_dir/debian/source/format"; then
        mkdir -p "$src_dir/debian/patches"
        cp "$patch_file" "$src_dir/debian/patches/0999-ask.patch"
        touch "$src_dir/debian/patches/series"
        grep -qx '0999-ask.patch' "$src_dir/debian/patches/series" 2>/dev/null \
            || echo '0999-ask.patch' >> "$src_dir/debian/patches/series"
    fi
    ok "  patch applied"

    # 5. Changelog bump
    #
    # Append the ASK suffix to the FULL existing Debian version (which
    # includes the Debian revision), not just the upstream part. Several
    # source packages (ppp in particular) have debian/rules-level guards
    # that assert DEB_VERSION_UPSTREAM matches DEB_VERSION — stripping
    # the existing Debian revision by using only upstream_ver+suffix
    # trips that guard. Keeping the revision ("2.4.9-1+1ubuntu3+ask1")
    # keeps DEB_VERSION_UPSTREAM == "2.4.9" stable.
    local current_deb_ver
    current_deb_ver=$(cd "$src_dir" && dpkg-parsechangelog -S Version 2>/dev/null)
    [[ -n "$current_deb_ver" ]] || current_deb_ver="$upstream_ver"
    local new_ver="${current_deb_ver}${REVISION_SUFFIX}"
    (
        cd "$src_dir"
        if command -v dch >/dev/null 2>&1; then
            dch --distribution "$DIST" --newversion "$new_ver" \
                "Apply NXP ASK patch ($(basename "$patch_subpath"))."
        else
            {
                printf '%s (%s) %s; urgency=medium\n\n' "$src_pkg" "$new_ver" "$DIST"
                printf '  * Apply NXP ASK patch (%s).\n\n' \
                    "$(basename "$patch_subpath")"
                printf ' -- %s <%s>  %s\n\n' \
                    "$DEBFULLNAME" "$DEBEMAIL" "$(date -R)"
                cat debian/changelog
            } > debian/changelog.new
            mv debian/changelog.new debian/changelog
        fi
    )
    ok "  version → $new_ver"

    # `dch --newversion` may or may not rename the working directory.
    # When the new version's upstream part matches the current directory
    # name (because we appended to the full Debian version, not the
    # upstream one), the dir stays put. Either way, re-resolve.
    local new_src_dir
    new_src_dir=$(find "$src_root" -mindepth 1 -maxdepth 1 -type d \
        -name "${src_pkg}-${new_ver}" | head -1)
    if [[ -n "$new_src_dir" && -d "$new_src_dir" ]]; then
        src_dir="$new_src_dir"
    elif [[ ! -d "$src_dir" ]]; then
        # Fallback: pick the only remaining ${src_pkg}-* dir
        src_dir=$(find "$src_root" -mindepth 1 -maxdepth 1 -type d \
            -name "${src_pkg}-*" | head -1)
    fi
    if [[ -z "$src_dir" || ! -d "$src_dir" ]]; then
        warn "  $src_pkg: source dir vanished after changelog bump"
        FAIL_SUMMARY+=("$src_pkg: src dir lost post-dch")
        end_group
        return 1
    fi

    # 6. Native build
    local build_log="$sub/build.log"
    info "  building natively for $TARGET_ARCH (log: $build_log)"
    set +e
    (
        cd "$src_dir"
        export DEB_BUILD_OPTIONS="nocheck parallel=$(nproc_any)"
        dpkg-buildpackage \
            --build=binary \
            -uc -us 2>&1
    ) > "$build_log"
    local rc=$?
    set -e

    if (( rc != 0 )); then
        warn "  $src_pkg: dpkg-buildpackage failed (exit $rc); last 40 lines:"
        tail -40 "$build_log" >&2
        FAIL_SUMMARY+=("$src_pkg: dpkg-buildpackage exit $rc")
        end_group
        return 1
    fi

    # 7. Collect artefacts
    shopt -s nullglob
    local produced=( "$src_root"/*"${REVISION_SUFFIX}"*"_${TARGET_ARCH}.deb"
                     "$src_root"/*"${REVISION_SUFFIX}"*"_all.deb"
                     "$src_root"/*"${REVISION_SUFFIX}"*".changes"
                     "$src_root"/*"${REVISION_SUFFIX}"*".buildinfo" )
    shopt -u nullglob

    if (( ${#produced[@]} == 0 )); then
        warn "  $src_pkg: build completed but no .debs found"
        FAIL_SUMMARY+=("$src_pkg: no .debs produced")
        end_group
        return 1
    fi

    local debs_count=0
    for f in "${produced[@]}"; do
        cp -v "$f" "$OUT_DIR/" | sed 's|^|   |'
        [[ "$f" == *.deb ]] && debs_count=$((debs_count+1))
    done
    ok "  $src_pkg: built $debs_count .deb(s), version $new_ver"
    BUILT_SUMMARY+=("$src_pkg $new_ver ($debs_count .deb)")
    end_group
    return 0
}

# ── Drive both builds ──────────────────────────────────────────────────
PKGS=()
[[ -z "$ONLY" || "$ONLY" == "ppp"      ]] && PKGS+=( "ppp:patches/ppp/01-nxp-ask-ifindex.patch" )
[[ -z "$ONLY" || "$ONLY" == "rp-pppoe" ]] && PKGS+=( "rp-pppoe:patches/rp-pppoe/01-nxp-ask-cmm-relay.patch" )

if (( ${#PKGS[@]} == 0 )); then
    err "nothing to build; --only must be 'ppp' or 'rp-pppoe'"
fi

ANY_OK=0
ANY_FAIL=0
for entry in "${PKGS[@]}"; do
    pkg="${entry%%:*}"
    patch="${entry#*:}"
    if build_one "$pkg" "$patch"; then
        ANY_OK=1
    else
        ANY_FAIL=1
    fi
done

# ── Summary ─────────────────────────────────────────────────────────────
echo
info "── ask-ppp build summary ──"
printf '   patch source:   %s\n' "$USP_PATCH_ROOT"
printf '   target arch:    %s\n' "$TARGET_ARCH"
if (( ${#BUILT_SUMMARY[@]} )); then
    printf '   built:\n'
    for s in "${BUILT_SUMMARY[@]}"; do printf '     ✓ %s\n' "$s"; done
fi
if (( ${#FAIL_SUMMARY[@]} )); then
    printf '   failed:\n'
    for s in "${FAIL_SUMMARY[@]}"; do printf '     ✗ %s\n' "$s"; done
fi

# Exit code policy:
#   All attempted succeeded     → 0
#   Mixed                       → 0 (partial OK; don't block the rest of the pipeline)
#   All failed                  → 1
#   Any patch rejected dry-run  → 2 (with priority over 1 so CI summary is informative)
if (( ANY_OK && ! ANY_FAIL )); then
    exit 0
fi
if (( ANY_OK && ANY_FAIL )); then
    warn "partial success: some packages failed but at least one built"
    exit 0
fi
# all failed → pick specific exit code
if printf '%s\n' "${FAIL_SUMMARY[@]}" | grep -q 'patch rejected'; then
    exit 2
fi
exit 1