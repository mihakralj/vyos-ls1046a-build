#!/bin/bash
# ci-pick-packages.sh — Filter built debs and copy to packages/
# Called by: .github/workflows/auto-build.yml "Pick Packages" step
# Expects: GITHUB_WORKSPACE set
set -ex
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
  strongswan-nm
  strongswan-pki
  tkmib
  waagent
  wide-dhcpv6-relay
  wide-dhcpv6-server
  vpp-plugin-devtools
  accel-ppp
)

for deb_file in $deb_files; do
  if [[ " ${ignore_packages[@]} " =~ " $(basename "$deb_file" | cut -d_ -f1) " ]]; then
    echo "ignore $deb_file"
    continue
  fi
  cp "$deb_file" packages
done

ls -alh packages

### Validate critical packages are present — no silent fallback to upstream
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
