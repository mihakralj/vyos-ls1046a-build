#!/bin/bash
# ci-install-deps.sh — Install build dependencies for VyOS LS1046A ISO
# Called by: .github/workflows/auto-build.yml "Install Dependencies" step
set -ex

echo "HOME: $HOME"
echo "PATH: $PATH"
command -v go
command -v opam
lscpu
free -m

# Install missing packages
apt-get update -qq
apt-get install -y \
  libsystemd-dev libglib2.0-dev libip4tc-dev libipset-dev libnfnetlink-dev \
  libnftnl-dev libnl-nf-3-dev libpopt-dev libpcap-dev libbpf-dev \
  bubblewrap git-lfs kpartx clang llvm cmake \
  protobuf-compiler python3-cracklib python3-protobuf \
  libreadline-dev liblua5.3-dev byacc flex \
  dosfstools mtools zstd u-boot-tools \
  libpcre2-dev xorriso \
  libxml2-dev libtclap-dev
# libxml2-dev + libtclap-dev: required to build fmc (NXP FMan Config tool) from
# source. Rebuilding fmc is needed so dpa_app links against a libfmc.a whose
# t_FmPcdKgSchemeParams / t_FmPcdHashTableParams struct layouts match the
# patched fmlib headers. See bin/ci-build-fmc.sh and bin/ci-build-fmlib.sh.
# python3-cracklib: https://github.com/vyos/vyos-build/commit/e846e68f9f6457865f3e3af92adfe42933555c59
# protobuf-compiler: https://github.com/vyos/vyos-build/commit/0a6c197226400c4bbe210b435baaa716d4fb8377
# python3-protobuf: https://github.com/vyos/vyos-build/commit/dd2c245be73c1e83b6ca392924aa549f77c5586e
apt-get upgrade -y
