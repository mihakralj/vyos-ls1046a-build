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
set -ex -o pipefail

# Prevent debconf from blocking on interactive prompts in CI.
# The pbuilder package in particular has an interactive mirror-selection
# prompt in pbuilder.config that spins forever without this.
export DEBIAN_FRONTEND=noninteractive

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
  `# Toolchains used by sub-package builds (accel-ppp; ASK2 components` \
  `# will declare their own additional deps here once they land per` \
  `# specs/ask2-rewrite-spec.md).` \
  bubblewrap clang llvm cmake byacc flex \
  `# Protobuf path (vyconf .proto compilation)` \
  `# python3-cracklib: https://github.com/vyos/vyos-build/commit/e846e68f9f6457865f3e3af92adfe42933555c59` \
  `# protobuf-compiler: https://github.com/vyos/vyos-build/commit/0a6c197226400c4bbe210b435baaa716d4fb8377` \
  `# python3-protobuf:  https://github.com/vyos/vyos-build/commit/dd2c245be73c1e83b6ca392924aa549f77c5586e` \
  protobuf-compiler python3-cracklib python3-protobuf \
  `# Live-build / ISO / U-Boot artifact tooling` \
  live-build dosfstools mtools zstd u-boot-tools xorriso kpartx \
  `# build-vyos-image live-build chroot assembler (squashfs union) — run` \
  `# 31762806825 failed at ISO stage with 'Missing packages: squashfs-tools'` \
  squashfs-tools \
  `# git LFS for board/dtb/ blobs and oversize prebuilt assets` \
  git-lfs \
  `# vyos-build Python helpers (TOML / YAML / Jinja2 / pystache template` \
  `# rendering for live-build configs and the kernel build.py)` \
  python3-tomli python3-jinja2 python3-yaml python3-toml python3-git \
  python3-pystache \
  `# Debian packaging stack used by every sub-package's debian/rules` \
  pbuilder python3-setuptools python3-pip python3-build python3-wheel \
  python3-stdeb dh-python debhelper devscripts equivs quilt \
  fakeroot rsync curl ca-certificates ccache sccache \
  `# vyos-http-api-tools rebuild (dh-virtualenv venv + mcp SDK): see` \
  `# bin/ci-build-http-api-tools.sh. dh-virtualenv drives the venv .deb;` \
  `# python3-venv provides the builtin venv module it wraps.` \
  dh-virtualenv python3-venv

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

# Wire the git merge driver unconditionally. The v0.17 CLI only accepts
# `merge --git <BASE> <LEFT> <RIGHT>` plus -l; a stale driver definition
# (e.g. `-s %S -x %X -y %Y -p %P -o %A` from an older mergiraf) makes
# every git apply --3way conflict invocation die with
# "the argument '--git' cannot be used with '--output'" and turns a
# clean 3-way merge into a patch failure (ARM64-runner2 2026-08-14).
# git apply --3way consults .gitattributes (merge=mergiraf) in the target
# tree and invokes this driver on every conflict.
git config --global merge.mergiraf.name "Mergiraf conflict resolver"
git config --global merge.mergiraf.driver "mergiraf merge --git %O %A %B -l %L"
git config --global merge.mergiraf.recursive binary

# ---------------------------------------------------------------------------
# syft — Anchore SBOM generator. NEW hard dependency: upstream vyos-build's
# build-vyos-image now shells out to `syft chroot ...` after the live-build
# binary stage to emit CycloneDX + SPDX SBOMs alongside the ISO (see
# build-vyos-image build() → "Now create SBOM"). It is NOT packaged in
# Debian and is absent from the bare self-hosted runner, so without it the
# ISO build fails with `FileNotFoundError: 'syft'` AFTER the ISO has already
# been assembled (build #28996067767, 2026-07-09). Install the pinned
# prebuilt release tarball, same idempotent pattern as Mergiraf above.
# ---------------------------------------------------------------------------
SYFT_VERSION="v1.46.0"
SYFT_BIN=/usr/local/bin/syft
if "$SYFT_BIN" version 2>/dev/null | grep -q "${SYFT_VERSION#v}"; then
  echo "syft ${SYFT_VERSION} already installed"
else
  arch=$(uname -m)
  case "$arch" in
    aarch64) syft_arch="arm64" ;;
    x86_64)  syft_arch="amd64" ;;
    *) echo "WARN: no syft prebuilt for $arch — ISO SBOM generation will fail"; syft_arch="" ;;
  esac
  if [ -n "$syft_arch" ]; then
    tarball="syft_${SYFT_VERSION#v}_linux_${syft_arch}.tar.gz"
    url="https://github.com/anchore/syft/releases/download/${SYFT_VERSION}/${tarball}"
    tmp=$(mktemp -d)
    curl -fsSL "$url" -o "$tmp/syft.tar.gz"
    tar -xzf "$tmp/syft.tar.gz" -C "$tmp"
    install -m 0755 "$tmp/syft" "$SYFT_BIN"
    rm -rf "$tmp"
  fi
fi
"$SYFT_BIN" version || echo "WARN: syft install failed — ISO SBOM generation will fail"

# ---------------------------------------------------------------------------
# j2lint — Jinja2 linter, NOT packaged in Debian.
# vyos-1x's debian/rules invokes `make j2lint` which runs the `j2lint`
# binary on data/templates/. The Makefile errors out
#   "j2lint binary not found, consider installing:
#    pip install git+https://github.com/aristanetworks/j2lint.git@341b5d5db86"
# if the binary is not on PATH. The upstream vyos-builder Docker image
# preinstalls j2lint via that exact pip URL — replicate on the bare runner.
#
# Debian bookworm marks system Python externally-managed (PEP 668);
# --break-system-packages is acceptable here because we are in a CI runner,
# not a user system. Self-hosted runner caches the install across builds, so
# the `command -v` guard makes this a fast no-op on warm caches.
# ---------------------------------------------------------------------------
J2LINT_PIN='git+https://github.com/aristanetworks/j2lint.git@341b5d5db86'
if ! command -v j2lint >/dev/null 2>&1; then
  pip install --break-system-packages "$J2LINT_PIN"
fi
j2lint --version
