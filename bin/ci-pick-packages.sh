#!/bin/bash
# ci-pick-packages.sh — Filter built debs and copy to packages/
# Called by: .github/workflows/auto-build.yml "Pick Packages" step
# Expects: GITHUB_WORKSPACE set
set -ex -o pipefail
cd "${GITHUB_WORKSPACE:-.}/vyos-build"

deb_files=$(find scripts/package-build -name "*.deb" -type f | \
  grep -v -- -dbg | \
  grep -v -- -dev | \
  grep -v -- -doc
)

ignore_packages=(
  charon-cmd
  dropbear-initramfs
  dropbear-run
  eapoltest
  frr-test-tools
  isc-dhcp-client-ddns
  isc-dhcp-common
  isc-dhcp-keama
  isc-dhcp-server
  isc-dhcp-server-ldap
  libnetsnmptrapd40
  libsnmp-perl
  libtac2-bin
  libyang-modules
  libyang-tools
  # python3-nftables
  rtr-tools
  sflowovsd.deb
  snmptrapd
  sstp-client
  strongswan-nm
  strongswan-pki
  tkmib
  waagent
  wide-dhcpv6-relay
  wide-dhcpv6-server
  vpp-plugin-devtools
  accel-ppp
)

mkdir -p packages

for deb_file in $deb_files; do
  if [[ " ${ignore_packages[@]} " =~ " $(basename "$deb_file" | cut -d_ -f1) " ]]; then
    echo "ignore $deb_file"
    continue
  fi
  cp "$deb_file" packages/
done

ls -alh packages

### Validate critical packages are present — no silent fallback to upstream
#
# ASK2 (rewrite-in-progress): the legacy ASK-consume branch
# (ASK_KERNEL_TAG → packages.chroot/) was removed on the ask20 branch.
# All flavors now build the kernel locally and stage it under packages/.
KERNEL_PKGS=$(find packages -name 'linux-image-*.deb' ! -name '*-dbg*' | wc -l)
if [ "$KERNEL_PKGS" -eq 0 ]; then
  echo ""
  echo "###############################################################"
  echo "### FATAL: No linux-image .deb found in packages/           ###"
  echo "### The ISO would silently use the upstream VyOS kernel.    ###"
  echo "###############################################################"
  echo ""
  exit 1
fi
echo "### Package validation OK: $KERNEL_PKGS kernel image package(s) in packages/"

### FLAVOR=ask: validate the ASK2 OOT module .deb is present
#
# The default `find scripts/package-build -name '*.deb'` glob above
# already sweeps in our ask-modules-${KVER}_*_arm64.deb (produced by
# kernel/flavors/ask/oot-modules/ask/ci-build.sh under
# scripts/package-build/linux-kernel/) — no name-based filtering excludes
# it. This block is a fail-fast guard: if the OOT module didn't make it
# in, the ISO would silently boot without ask.ko and the operator would
# only discover the omission after USB-booting the device.
if [ "${FLAVOR:-default}" = "ask" ]; then
    ASK_MOD_PKGS=$(find packages -name 'ask-modules-*.deb' | wc -l)
    if [ "$ASK_MOD_PKGS" -eq 0 ]; then
        echo ""
        echo "###############################################################"
        echo "### FATAL: FLAVOR=ask but no ask-modules-*.deb in packages/ ###"
        echo "### ASK2 OOT kernel module would be MISSING from the ISO.###"
        echo "###############################################################"
        echo ""
        exit 1
    fi
    echo "### FLAVOR=ask validation OK: $ASK_MOD_PKGS ASK OOT module .deb(s) in packages/"
    find packages -name 'ask-modules-*.deb' -exec ls -lh {} \;
fi

### HOTFIX: Debian bookworm-backports is currently missing libhtp2 arm64 binary
### but suricata from bookworm-backports depends on it. We fetch it from snapshot.
echo "### Fetching missing libhtp2 from snapshot.debian.org..."
curl -sSfL "https://snapshot.debian.org/archive/debian/20260529T053103Z/pool/main/libh/libhtp/libhtp2_0.5.53-1~bpo12%2B1_arm64.deb" -o packages/libhtp2_0.5.53-1~bpo12+1_arm64.deb
echo "### Downloaded libhtp2:"
ls -l packages/libhtp2_0.5.53-1~bpo12+1_arm64.deb

