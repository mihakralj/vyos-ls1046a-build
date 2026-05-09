#!/bin/bash
# local-stage-ask-kernel.sh — stage producer .debs from a local kernel-ls1046a-build
# work/build/ directory directly into vyos-build/data/live-build-config/packages.chroot/
# and packages/, mimicking what ci-consume-ask-kernel.sh does after a `gh release
# download`.
#
# Used for local end-to-end iteration without pushing a producer tag.
#
# Usage (from /workspace inside the build container):
#   PRODUCER_BUILD_DIR=/producer/work/build KVER=6.18.28 bin/local-stage-ask-kernel.sh
set -euo pipefail
cd "${GITHUB_WORKSPACE:-/workspace}"

PRODUCER_BUILD_DIR="${PRODUCER_BUILD_DIR:-/producer/work/build}"
KVER="${KVER:?set KVER (e.g. 6.18.28)}"

[ -d "$PRODUCER_BUILD_DIR" ] || { echo "ERROR: $PRODUCER_BUILD_DIR not found"; exit 1; }
mkdir -p packages

VB_PKG_CHROOT="vyos-build/data/live-build-config/packages.chroot"
mkdir -p "$VB_PKG_CHROOT"

echo "### Staging .debs for KVER=$KVER from $PRODUCER_BUILD_DIR"

# Filter to *this* kernel version only (producer build dir may contain
# multiple kvers from incremental builds).
shopt -s nullglob
KERNEL_DEBS=(
    "$PRODUCER_BUILD_DIR"/linux-image-${KVER}-vyos_*_arm64.deb
    "$PRODUCER_BUILD_DIR"/linux-headers-${KVER}-vyos_*_arm64.deb
    "$PRODUCER_BUILD_DIR"/linux-libc-dev_${KVER}-*_arm64.deb
    "$PRODUCER_BUILD_DIR"/linux-perf-${KVER}-vyos_*_arm64.deb
    "$PRODUCER_BUILD_DIR"/ask-modules-${KVER}-vyos_*_arm64.deb
)
USERSPACE_DEBS=(
    "$PRODUCER_BUILD_DIR"/iptables_*+ask*_arm64.deb
    "$PRODUCER_BUILD_DIR"/libxtables*_*+ask*_arm64.deb
    "$PRODUCER_BUILD_DIR"/libxt*_*+ask*_arm64.deb
    "$PRODUCER_BUILD_DIR"/libip4tc*_*+ask*_arm64.deb
    "$PRODUCER_BUILD_DIR"/libip6tc*_*+ask*_arm64.deb
    "$PRODUCER_BUILD_DIR"/libiptc*_*+ask*_arm64.deb
    "$PRODUCER_BUILD_DIR"/ppp_*+ask*_arm64.deb
    "$PRODUCER_BUILD_DIR"/ppp-dev_*+ask*_all.deb
    "$PRODUCER_BUILD_DIR"/pppoe_*+ask*_arm64.deb
    "$PRODUCER_BUILD_DIR"/rp-pppoe_*+ask*_arm64.deb
)
shopt -u nullglob

for f in "${KERNEL_DEBS[@]}" "${USERSPACE_DEBS[@]}"; do
    case "$f" in *-dbg_*|*-dbgsym_*|*-dev*) continue ;; esac
    cp -v "$f" packages/
    cp -v "$f" "$VB_PKG_CHROOT/"
done

# Synthesize the bits ci-consume-ask-kernel.sh emits for downstream steps.
cat > packages/.ask-kernel-manifest.json <<EOF
{
  "kernel_version": "$KVER",
  "ask_iteration": "local",
  "reference_sha": "local-build-no-tag",
  "published_at": "$(date -u +%Y-%m-%dT%H:%M:%SZ)",
  "source": "local-stage-ask-kernel.sh"
}
EOF

# Expected-package list for ci-verify-ask-iso.sh
VERIFY_LIST=packages/.ask-expected-packages.txt
: > "$VERIFY_LIST"
echo "linux-image-${KVER}-vyos"   >> "$VERIFY_LIST"
echo "linux-headers-${KVER}-vyos" >> "$VERIFY_LIST"
shopt -s nullglob
for f in packages/iptables_*+ask*_arm64.deb \
         packages/libxtables*_*+ask*_arm64.deb \
         packages/libip4tc*_*+ask*_arm64.deb \
         packages/libip6tc*_*+ask*_arm64.deb \
         packages/ppp_*+ask*_arm64.deb \
         packages/pppoe_*+ask*_arm64.deb \
         packages/rp-pppoe_*+ask*_arm64.deb \
         packages/ask-modules-*_arm64.deb; do
    [ -f "$f" ] && dpkg-deb -f "$f" Package >> "$VERIFY_LIST"
done
shopt -u nullglob
awk 'NF && !seen[$0]++' "$VERIFY_LIST" > "$VERIFY_LIST.tmp" && mv "$VERIFY_LIST.tmp" "$VERIFY_LIST"

# Export env for downstream steps
if [ -n "${GITHUB_ENV:-}" ]; then
    {
        echo "KERNEL_VER=$KVER"
        echo "ASK_KERNEL_TAG=local-${KVER}-ask1"
        echo "ASK_REF_SHA=local-build"
    } >> "$GITHUB_ENV"
fi

echo "### Staged ASK kernel + userspace (local, KVER=$KVER)"
ls "$VB_PKG_CHROOT/" | grep -E '\.deb$'
