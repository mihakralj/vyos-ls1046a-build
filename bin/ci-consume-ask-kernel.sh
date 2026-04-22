#!/bin/bash
# ci-consume-ask-kernel.sh — download prebuilt ASK kernel + userspace .debs
# from mihakralj/lts_6.6_ls1046a and stage them for live-build.
#
# Replaces the from-scratch kernel compile previously driven by
# ci-setup-kernel.sh + ci-build-packages.sh (linux-kernel target).
#
# Reads the pinned tag from data/ask-kernel.pin. Override with env
# ASK_KERNEL_TAG or workflow_dispatch input.
#
# Called by: .github/workflows/auto-build.yml "Consume prebuilt ASK kernel" step
# Expects: GITHUB_WORKSPACE set, curl, jq. (gh CLI not required — uses REST API
# directly so this works inside minimal builder containers.)
set -euo pipefail
cd "${GITHUB_WORKSPACE:-.}"

PIN_FILE=data/ask-kernel.pin
TAG="${ASK_KERNEL_TAG:-}"
if [ -z "$TAG" ] && [ -f "$PIN_FILE" ]; then
    TAG=$(tr -d '[:space:]' < "$PIN_FILE")
fi
[ -n "$TAG" ] || { echo "ERROR: no ASK kernel tag (set ASK_KERNEL_TAG or populate $PIN_FILE)"; exit 1; }

REPO="mihakralj/lts_6.6_ls1046a"
STAGE=work/ask-kernel
mkdir -p "$STAGE" packages

# curl auth header if a token is available (works with public repos without it
# too, but authenticated requests get higher rate limits).
AUTH_ARGS=()
if [ -n "${GH_TOKEN:-}" ]; then
    AUTH_ARGS=(-H "Authorization: Bearer $GH_TOKEN")
elif [ -n "${GITHUB_TOKEN:-}" ]; then
    AUTH_ARGS=(-H "Authorization: Bearer $GITHUB_TOKEN")
fi

echo "### Fetching ASK kernel release $TAG from $REPO"
API="https://api.github.com/repos/$REPO/releases/tags/$TAG"
ASSETS_JSON=$(curl -fsSL "${AUTH_ARGS[@]}" \
    -H "Accept: application/vnd.github+json" \
    -H "X-GitHub-Api-Version: 2022-11-28" \
    "$API")

# Patterns we want to pull down. BRE globs expanded via `case` below.
want() {
    case "$1" in
        linux-*.deb)                return 0 ;;
        iptables_*_arm64.deb)       return 0 ;;
        libxt*_arm64.deb)           return 0 ;;
        libip4tc*_arm64.deb)        return 0 ;;
        libip6tc*_arm64.deb)        return 0 ;;
        libxtables*_arm64.deb)      return 0 ;;
        libiptc*_arm64.deb)         return 0 ;;
        ppp_*_arm64.deb)            return 0 ;;
        ppp-dev_*_all.deb)          return 0 ;;
        pppoe_*_arm64.deb)          return 0 ;;
        SHA256SUMS|manifest.json)   return 0 ;;
    esac
    return 1
}

# Asset download URLs use /repos/:owner/:repo/releases/assets/:id with
# Accept: application/octet-stream so they work inside the API auth flow.
mapfile -t ASSETS < <(jq -r '.assets[] | "\(.id) \(.name)"' <<<"$ASSETS_JSON")
[ "${#ASSETS[@]}" -gt 0 ] || { echo "ERROR: no assets in release $TAG"; exit 1; }

for entry in "${ASSETS[@]}"; do
    id="${entry%% *}"
    name="${entry#* }"
    if want "$name"; then
        echo "  fetching $name"
        curl -fsSL "${AUTH_ARGS[@]}" \
            -H "Accept: application/octet-stream" \
            -o "$STAGE/$name" \
            "https://api.github.com/repos/$REPO/releases/assets/$id"
    fi
done

echo "### Verifying SHA256SUMS (subset we actually downloaded)"
# The release's SHA256SUMS lists every asset including *.buildinfo / *.changes
# that we intentionally skip. Narrow it to what we pulled, then verify strictly.
(
    cd "$STAGE"
    mv SHA256SUMS SHA256SUMS.full
    while read -r sum name; do
        [ -f "$name" ] && printf '%s  %s\n' "$sum" "$name"
    done < SHA256SUMS.full > SHA256SUMS
    sha256sum -c --strict SHA256SUMS
)

# Ship everything except the dbg kernel (+80 MB, not wanted in the ISO).
# Skip -dev packages that conflict with Debian base unless actually needed.
echo "### Staging .deb files to packages/"
find "$STAGE" -maxdepth 1 -name '*.deb' \
    ! -name '*-dbg_*' \
    ! -name 'libip4tc-dev*' \
    ! -name 'libip6tc-dev*' \
    ! -name 'libiptc-dev*' \
    ! -name 'libxtables-dev*' \
    -exec cp -v {} packages/ \;

# Expose version for downstream steps (ISO manifest, DTB fallback logic, logs).
KVER=$(jq -r .kernel_version "$STAGE/manifest.json")
REF_SHA=$(jq -r '.reference_sha[0:12]' "$STAGE/manifest.json")
if [ -n "${GITHUB_ENV:-}" ]; then
    echo "KERNEL_VER=$KVER"         >> "$GITHUB_ENV"
    echo "ASK_KERNEL_TAG=$TAG"      >> "$GITHUB_ENV"
    echo "ASK_REF_SHA=$REF_SHA"     >> "$GITHUB_ENV"
fi

# Stash provenance so it lands in build artefacts.
cp "$STAGE/manifest.json" "packages/.ask-kernel-manifest.json"

# ── Stage kernel/headers into live-build's packages.chroot/ ───────────
# The ASK kernel is named linux-image-<KVER>-vyos (LOCALVERSION=-vyos),
# matching the default VyOS kernel_flavor. Dropping the .debs into
# `vyos-build/data/live-build-config/packages.chroot/` makes `dpkg -i`
# run before the apt pass, so:
#   - the kernel + modules are installed from OUR .debs (not re-pulled
#     from packages.vyos.net by name), and
#   - every VyOS out-of-tree kernel-module package
#     (jool, nat-rtsp, openvpn-dco, vyos-ipt-netflow, …) whose control
#     file carries `Depends: linux-image-<KVER>-vyos` resolves cleanly.
# ask-modules-<KVER>-vyos ships the cdx/fci/auto_bridge OOT .ko files
# and must sit next to the kernel in packages.chroot/ (same dpkg pass).
#
# vyos-build is checked out at $GITHUB_WORKSPACE/vyos-build by the
# preceding "Checkout vyos-build repo" step in auto-build.yml.
VB_PKG_CHROOT="vyos-build/data/live-build-config/packages.chroot"
if [ -d "vyos-build/data/live-build-config" ]; then
    mkdir -p "$VB_PKG_CHROOT"
    # Kernel + OOT modules (always staged — the build depends on them by name).
    for f in packages/linux-image-*_arm64.deb \
             packages/linux-headers-*_arm64.deb \
             packages/linux-libc-dev_*_arm64.deb \
             packages/ask-modules-*_arm64.deb; do
        [ -f "$f" ] && cp -v "$f" "$VB_PKG_CHROOT/"
    done
    # ASK userspace overrides: iptables + xtables libs (QOSMARK/QOSCONNMARK),
    # ppp/ppp-dev (NXP PPPoE offload / CMM relay), rp-pppoe when ever the
    # producer ships one. Must be present in packages.chroot/ so live-build's
    # dpkg pass installs them instead of apt pulling stock Debian. Without
    # this, Debian bookworm's iptables_1.8.9-2 and ppp_2.4.9-1+1.1+b1 win
    # over our _1.8.10+ask1 / _+ask1 builds and the ISO ships non-ASK
    # userspace silently (detected as regression in run 24794085304).
    shopt -s nullglob
    _ask_userspace=(
        packages/iptables_*+ask*_arm64.deb
        packages/libxt*_*+ask*_arm64.deb
        packages/libxtables*_*+ask*_arm64.deb
        packages/libip4tc*_*+ask*_arm64.deb
        packages/libip6tc*_*+ask*_arm64.deb
        packages/libiptc*_*+ask*_arm64.deb
        packages/ppp_*+ask*_arm64.deb
        packages/ppp-dev_*+ask*_all.deb
        packages/pppoe_*+ask*_arm64.deb
        packages/rp-pppoe_*+ask*_arm64.deb
    )
    for f in "${_ask_userspace[@]}"; do
        [ -f "$f" ] && cp -v "$f" "$VB_PKG_CHROOT/"
    done
    shopt -u nullglob
    echo "### Staged kernel + ASK userspace .debs into $VB_PKG_CHROOT/"
    ls -la "$VB_PKG_CHROOT/" 2>/dev/null | grep -E '\.deb$' || true
else
    echo "WARN: vyos-build/ not present yet — skipping packages.chroot staging."
    echo "      This should not happen: ci-consume-ask-kernel.sh runs AFTER"
    echo "      'Checkout vyos-build repo' in auto-build.yml."
fi

# ── Emit expected-ASK-packages manifest for post-build verification ───
# ci-verify-ask-iso.sh reads this list and greps live/filesystem.packages
# for each name to confirm the ASK userspace actually landed in the ISO
# (guards against live-build dropping packages.chroot/ entries on apt
# version-compare ties, etc.)
VERIFY_LIST=packages/.ask-expected-packages.txt
: > "$VERIFY_LIST"
echo "linux-image-${KVER}-vyos"   >> "$VERIFY_LIST"
echo "linux-headers-${KVER}-vyos" >> "$VERIFY_LIST"
# Derive ASK userspace expectations from what we actually downloaded,
# so removing or adding a +ask build in the producer repo auto-adjusts
# the assertion without a corresponding consumer-side edit.
shopt -s nullglob
for f in packages/iptables_*+ask*_arm64.deb \
         packages/libxtables*_*+ask*_arm64.deb \
         packages/libip4tc*_*+ask*_arm64.deb \
         packages/libip6tc*_*+ask*_arm64.deb \
         packages/ppp_*+ask*_arm64.deb \
         packages/pppoe_*+ask*_arm64.deb \
         packages/rp-pppoe_*+ask*_arm64.deb \
         packages/ask-modules-*_arm64.deb; do
    if [ -f "$f" ]; then
        dpkg-deb -f "$f" Package >> "$VERIFY_LIST"
    fi
done
shopt -u nullglob
# Deduplicate while preserving order
awk 'NF && !seen[$0]++' "$VERIFY_LIST" > "$VERIFY_LIST.tmp" && mv "$VERIFY_LIST.tmp" "$VERIFY_LIST"
echo "### Expected-in-ISO package names written to $VERIFY_LIST:"
sed 's/^/    /' "$VERIFY_LIST"

echo "### ASK kernel consumed: $TAG (linux $KVER, ref $REF_SHA)"
ls -la packages/ | grep -E '\.(deb|json)$' || true
