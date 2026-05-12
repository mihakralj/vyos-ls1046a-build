#!/bin/bash
# ci-install-deps.sh — Single source of truth for host-side build deps.
#
# Called by: .github/workflows/auto-build.yml "Install Dependencies" step.
# Also safe to run manually on a fresh self-hosted runner / LXC dev VM —
# every action is idempotent.
#
# Reconciled package list = union of (former inline workflow apt-get
# block) ∪ (former bin/ci-install-deps.sh apt-get block) — see commit
# log for the audit. Future additions go HERE, never inline in the
# workflow YAML, so the dev-loop on LXC 200 stays able to reproduce a
# CI build without dispatching CI.
set -ex

echo "HOME: $HOME"
echo "PATH: $PATH"
command -v go || true
command -v opam || true
lscpu
free -m

# Self-hosted runners persist /etc/apt/sources.list.d/ across builds.
# A stale or wrongly-signed VyOS source dropped by a prior failed run
# breaks `apt-get update`. The proper VyOS source (with trusted=yes)
# gets installed later by the "Install vyos-1x build dependencies"
# workflow step, which still lives inline because it depends on
# `vyos-build/docker/vyos-dev.key` only existing AFTER the
# `actions/checkout@v6` of the vyos-build repo.
rm -f /etc/apt/sources.list.d/vyos-dev.list \
      /etc/apt/sources.list.d/vyos.list \
      /etc/apt/sources.list.d/vyos.sources \
      /etc/apt/preferences.d/vyos

apt-get update -qq

# IMPORTANT: keep this list sorted by category to make duplicates
# obvious during code review. Adding a package elsewhere (e.g. inline
# in a workflow step) is a layering bug — fix THIS file instead.
apt-get install -y \
  `# vyos-1x C/C++ link-time deps (libsystemd-dev pulled in by vyconf)` \
  libsystemd-dev libglib2.0-dev libip4tc-dev libipset-dev libnfnetlink-dev \
  libnftnl-dev libnl-nf-3-dev libpopt-dev libpcap-dev libbpf-dev \
  libreadline-dev liblua5.3-dev libpcre2-dev \
  `# Toolchains used by sub-package builds (cdx, fci, dpa_app, accel-ppp)` \
  bubblewrap clang llvm cmake byacc flex \
  `# fmc / fmlib (NXP FMan Config tool) build deps — see ci-build-fmc.sh` \
  `# and ci-build-fmlib.sh for why we MUST rebuild fmlib so that` \
  `# t_FmPcdKgSchemeParams / t_FmPcdHashTableParams struct layouts match` \
  `# the patched fmlib headers used by dpa_app.` \
  libxml2-dev libtclap-dev \
  `# Protobuf path (vyconf .proto compilation)` \
  `# python3-cracklib: https://github.com/vyos/vyos-build/commit/e846e68f9f6457865f3e3af92adfe42933555c59` \
  `# protobuf-compiler: https://github.com/vyos/vyos-build/commit/0a6c197226400c4bbe210b435baaa716d4fb8377` \
  `# python3-protobuf:  https://github.com/vyos/vyos-build/commit/dd2c245be73c1e83b6ca392924aa549f77c5586e` \
  protobuf-compiler python3-cracklib python3-protobuf \
  `# Live-build / ISO / U-Boot artifact tooling` \
  live-build dosfstools mtools zstd u-boot-tools xorriso kpartx \
  `# git LFS for board/dtb/ blobs and oversize prebuilt assets` \
  git-lfs \
  `# vyos-build Python helpers (TOML / YAML / Jinja2 / pystache template` \
  `# rendering for live-build configs and the kernel build.py)` \
  python3-tomli python3-jinja2 python3-yaml python3-toml python3-git \
  python3-pystache \
  `# Debian packaging stack used by every sub-package's debian/rules` \
  pbuilder python3-setuptools python3-pip python3-build python3-wheel \
  python3-stdeb dh-python debhelper devscripts equivs quilt \
  fakeroot rsync curl ca-certificates

# Upgrade after install so that any of the just-installed packages
# get their security patches — matches what the upstream vyos-builder
# Docker image does on every container start.
apt-get upgrade -y

# ---------------------------------------------------------------------------
# Mergiraf — AST-aware 3-way merge driver. Wired as the merge driver for
# *.c *.h *.py *.json *.yml *.yaml *.toml *.xml via the .gitattributes drops
# in bin/ci-setup-vyos1x.sh and bin/ci-setup-vyos-build.sh — `git apply
# --3way` invokes it on context drift.
#
# Without mergiraf, --3way silently falls back to producing conflict markers
# in the patched file AND returns exit 0 with a "with conflicts" warning.
# That broke vpp-flavor build #25703103908 on 2026-05-11 (vyos-1x-013
# applied "with conflicts" instead of cleanly, then later patches refused).
#
# Idempotent: skip if /usr/local/bin/mergiraf is already at the pinned
# version. Self-hosted runner caches the binary across builds.
# ---------------------------------------------------------------------------
MERGIRAF_VERSION="v0.17.0"
MERGIRAF_BIN=/usr/local/bin/mergiraf
if "$MERGIRAF_BIN" --version 2>/dev/null | grep -q "${MERGIRAF_VERSION#v}"; then
  echo "mergiraf ${MERGIRAF_VERSION} already installed"
else
  arch=$(uname -m)
  case "$arch" in
    aarch64) tarball="mergiraf_aarch64-unknown-linux-gnu.tar.gz" ;;
    x86_64)  tarball="mergiraf_x86_64-unknown-linux-gnu.tar.gz" ;;
    *) echo "WARN: no mergiraf prebuilt for $arch — git apply --3way will fall back to conflict markers"; tarball="" ;;
  esac
  if [ -n "$tarball" ]; then
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
