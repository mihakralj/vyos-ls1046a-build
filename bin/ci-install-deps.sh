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

# Install Mergiraf (AST-aware 3-way merge driver) from upstream Codeberg
# release. Wired as the merge driver for *.c *.h *.py *.json *.yml *.yaml
# *.toml *.xml via the .gitattributes drops in bin/ci-setup-vyos1x.sh and
# bin/ci-setup-vyos-build.sh — git apply --3way invokes it on context drift.
#
# Without mergiraf, --3way silently falls back to producing conflict markers
# in the patched file AND returns exit 0 with a "with conflicts" warning.
# That breaks every patch with even minor blob-SHA drift versus the upstream
# clone (observed 2026-05-11 on vpp-flavor build #25703103908: vyos-1x-013
# applied "with conflicts" instead of cleanly, then later patches refused).
#
# Idempotent: skip if /usr/local/bin/mergiraf is already at the pinned
# version. Self-hosted runner caches the binary across builds.
MERGIRAF_VERSION="v0.17.0"
MERGIRAF_BIN=/usr/local/bin/mergiraf
if ! "$MERGIRAF_BIN" --version 2>/dev/null | grep -q "${MERGIRAF_VERSION#v}"; then
  arch=$(uname -m)
  case "$arch" in
    aarch64) tarball="mergiraf_aarch64-unknown-linux-gnu.tar.gz" ;;
    x86_64)  tarball="mergiraf_x86_64-unknown-linux-gnu.tar.gz" ;;
    *) echo "WARN: no mergiraf prebuilt for $arch — git apply --3way will fall back to conflict markers" ;;
  esac
  if [ -n "${tarball:-}" ]; then
    url="https://codeberg.org/mergiraf/mergiraf/releases/download/${MERGIRAF_VERSION}/${tarball}"
    tmp=$(mktemp -d)
    curl -fsSL "$url" -o "$tmp/mergiraf.tar.gz"
    tar -xzf "$tmp/mergiraf.tar.gz" -C "$tmp"
    install -m 0755 "$tmp"/mergiraf*/mergiraf "$MERGIRAF_BIN" 2>/dev/null \
      || install -m 0755 "$tmp/mergiraf" "$MERGIRAF_BIN"
    rm -rf "$tmp"
  fi
fi
"$MERGIRAF_BIN" --version || echo "WARN: mergiraf install failed — patches with context drift may produce conflict markers"
