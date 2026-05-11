#!/bin/bash
# ci-verify-vpp-iso.sh — fail fast if a FLAVOR=vpp ISO is missing VPP essentials.
set -euo pipefail

REPO_ROOT=${GITHUB_WORKSPACE:-$(pwd)}
cd "$REPO_ROOT"

# shellcheck disable=SC1091
BC_QUIET=1 source "$REPO_ROOT/bin/common.sh"

if [[ "$FLAVOR" != "vpp" ]]; then
    echo "### FLAVOR=$FLAVOR — skipping VPP ISO verification"
    exit 0
fi

CHROOT="$REPO_ROOT/vyos-build/data/live-build-config/includes.chroot"
PKG_MANIFEST="$REPO_ROOT/vyos-build/build/chroot.packages.install"
CONFIG_DEFAULT="$CHROOT/opt/vyatta/etc/config.boot.default"

fail() {
    echo "FATAL: $*" >&2
    exit 1
}

[[ -s "$PKG_MANIFEST" ]] || fail "missing package manifest: $PKG_MANIFEST"
[[ -s "$CONFIG_DEFAULT" ]] || fail "missing staged default config: $CONFIG_DEFAULT"

required_packages=(
    libvppinfra
    python3-vpp-api
    vpp
    vpp-crypto-engines
    vpp-plugin-core
    vpp-plugin-dpdk
)

for pkg in "${required_packages[@]}"; do
    if ! awk '{print $1}' "$PKG_MANIFEST" | grep -Fxq "$pkg"; then
        fail "VPP package '$pkg' is absent from chroot.packages.install"
    fi
done

for iface in eth1 eth2 eth3 eth4; do
    grep -Eq "^[[:space:]]*interface ${iface}[[:space:]]*\{" "$CONFIG_DEFAULT" \
        || fail "VPP default config does not enable $iface"
done

if grep -Eq '^[[:space:]]*interface eth0[[:space:]]*\{' "$CONFIG_DEFAULT"; then
    fail "VPP default config must leave eth0 out of vpp settings"
fi

grep -q 'allow-unsupported-nics' "$CONFIG_DEFAULT" || fail "missing VPP allow-unsupported-nics setting"
grep -q 'poll-sleep-usec 100' "$CONFIG_DEFAULT" || fail "missing thermal-safe VPP poll-sleep-usec 100"
grep -q 'cpu-cores 1' "$CONFIG_DEFAULT" || fail "missing VPP cpu-cores 1"
grep -q 'main-heap-size 256M' "$CONFIG_DEFAULT" || fail "missing VPP main-heap-size 256M"
grep -q 'hugepage-size 2M' "$CONFIG_DEFAULT" || fail "missing hugepage-size 2M"
grep -q 'hugepage-count 512' "$CONFIG_DEFAULT" || fail "missing hugepage-count 512"
grep -q 'version-vpp\.json' "$CONFIG_DEFAULT" || fail "update-check feed is not version-vpp.json"

[[ -x "$CHROOT/usr/local/bin/vpp-post-start.sh" ]] || fail "missing staged vpp-post-start helper"
[[ -f "$CHROOT/etc/systemd/system/vpp.service.d/post-start.conf" ]] || fail "missing vpp post-start systemd drop-in"
if [[ -f "$CHROOT/etc/systemd/system/vpp.service.d/dpaa-rebind.conf" ]]; then
    fail "legacy DPAA rebind drop-in must not be staged for AF_XDP VPP"
fi

echo "### VPP ISO verification OK: packages, config, hugepages, and AF_XDP helpers present"
