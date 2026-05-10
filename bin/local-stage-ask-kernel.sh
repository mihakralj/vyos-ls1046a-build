#!/bin/bash
# local-stage-ask-kernel.sh — stage prebuilt ASK kernel .debs from a local
# build directory (e.g. a sibling clone where you ran kernel/common/scripts/
# build-kernel.sh) directly into vyos-build/data/live-build-config/
# packages.chroot/ and packages/, mimicking what ci-consume-ask-kernel.sh
# does after a `gh release download`.
#
# Used for local end-to-end iteration without pushing a release tag.
#
# Usage (from /workspace inside the build container):
#   LOCAL_KERNEL_DEB_DIR=/path/to/work/build KVER=6.18.28 bin/local-stage-ask-kernel.sh
set -euo pipefail
cd "${GITHUB_WORKSPACE:-/workspace}"

LOCAL_KERNEL_DEB_DIR="${LOCAL_KERNEL_DEB_DIR:-/work/build}"
KVER="${KVER:?set KVER (e.g. 6.18.28)}"

[ -d "$LOCAL_KERNEL_DEB_DIR" ] || { echo "ERROR: $LOCAL_KERNEL_DEB_DIR not found"; exit 1; }
mkdir -p packages

VB_PKG_CHROOT="vyos-build/data/live-build-config/packages.chroot"
mkdir -p "$VB_PKG_CHROOT"

echo "### Staging .debs for KVER=$KVER from $LOCAL_KERNEL_DEB_DIR"

# Filter to *this* kernel version only (local build dir may contain
# multiple kvers from incremental builds).
shopt -s nullglob
KERNEL_DEBS=(
    "$LOCAL_KERNEL_DEB_DIR"/linux-image-${KVER}-vyos_*_arm64.deb
    "$LOCAL_KERNEL_DEB_DIR"/linux-headers-${KVER}-vyos_*_arm64.deb
    "$LOCAL_KERNEL_DEB_DIR"/linux-libc-dev_${KVER}-*_arm64.deb
    "$LOCAL_KERNEL_DEB_DIR"/linux-perf-${KVER}-vyos_*_arm64.deb
    "$LOCAL_KERNEL_DEB_DIR"/ask-modules-${KVER}-vyos_*_arm64.deb
)
USERSPACE_DEBS=(
    "$LOCAL_KERNEL_DEB_DIR"/iptables_*+ask*_arm64.deb
    "$LOCAL_KERNEL_DEB_DIR"/libxtables*_*+ask*_arm64.deb
    "$LOCAL_KERNEL_DEB_DIR"/libxt*_*+ask*_arm64.deb
    "$LOCAL_KERNEL_DEB_DIR"/libip4tc*_*+ask*_arm64.deb
    "$LOCAL_KERNEL_DEB_DIR"/libip6tc*_*+ask*_arm64.deb
    "$LOCAL_KERNEL_DEB_DIR"/libiptc*_*+ask*_arm64.deb
    "$LOCAL_KERNEL_DEB_DIR"/ppp_*+ask*_arm64.deb
    "$LOCAL_KERNEL_DEB_DIR"/ppp-dev_*+ask*_all.deb
    "$LOCAL_KERNEL_DEB_DIR"/pppoe_*+ask*_arm64.deb
    "$LOCAL_KERNEL_DEB_DIR"/rp-pppoe_*+ask*_arm64.deb
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
