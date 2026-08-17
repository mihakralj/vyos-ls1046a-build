#!/bin/bash
# ci-install-vyos1x-deps.sh — Install vyos-1x build dependencies from VyOS apt repo
#
# Called by: .github/workflows/auto-build.yml "Install vyos-1x build
# dependencies (VyOS apt repo)" step, AFTER the `actions/checkout@v6` of
# `vyos/vyos-build` has populated `vyos-build/docker/vyos-dev.key`.
#
# Ordering invariant: this script CANNOT fold into bin/ci-install-deps.sh
# because that script runs at the very start of the job (before vyos-build
# is cloned), and the VyOS apt key + sources lookup below depends on
# vyos-build/docker/vyos-dev.key already existing on disk. Keep the two
# scripts separate; their roles are documented at the top of each.
#
# mk-build-deps for vyos-1x needs VyOS-specific packages (python3-vici
# >=5.7.2, python3-certbot-nginx, python3-hurry.filesize, python3-nose2,
# ...) that don't exist in Debian bookworm main. The vyos-build repo
# ships its own apt key + sources.list, so we install them straight from
# the freshly-checked-out vyos-build/docker/ tree.
set -ex -o pipefail

# Prevent debconf interactive prompts in CI.
export DEBIAN_FRONTEND=noninteractive

# The VyOS dev apt repo is added ONLY when vyos-build still ships its key.
#
# Upstream vyos/vyos-build removed docker/vyos-dev.key and the whole VyOS
# binary-repo dependency in rolling commit 35091fcd / PR #1270 (T9216,
# 2026-08-17): "pylint can be instructed to ignore the vici import ... removing
# a circular dependency on the repository." So on current rolling the key is
# gone and the repo is no longer required for the vyos-1x build deps.
#
# Handle both worlds: if the key is present (older vyos-build), wire the repo
# exactly as before; if it is absent, skip it and rely on Debian bookworm +
# the vyos-builder base image for the packages below.
VYOS_DEV_KEY="vyos-build/docker/vyos-dev.key"
if [ -f "$VYOS_DEV_KEY" ]; then
  # The shipped key carries an old SHA1 self-signature that newer apt/sqv
  # policies reject; the key is fine, so trust the source explicitly.
  install -m 0644 "$VYOS_DEV_KEY" \
    /usr/share/keyrings/vyos-dev-archive-keyring.asc
  echo 'deb [trusted=yes signed-by=/usr/share/keyrings/vyos-dev-archive-keyring.asc] https://packages.vyos.net/repositories/rolling rolling main' \
    > /etc/apt/sources.list.d/vyos-dev.list
  cat /etc/apt/sources.list.d/vyos-dev.list
else
  echo "### vyos-dev.key absent (upstream T9216/PR#1270) — skipping VyOS dev apt repo"
  rm -f /etc/apt/sources.list.d/vyos-dev.list
fi

apt-get update -qq

# Debian-provided build deps (always available in bookworm main).
apt-get install -y --no-install-recommends \
  libzmq3-dev pylint whois \
  python3-pyudev python3-systemd python3-pam python3-pyroute2 \
  python3-voluptuous python3-lxml python3-xmltodict python3-coverage \
  python3-netaddr python3-netifaces python3-paramiko python3-passlib \
  python3-psutil python3-tabulate python3-zmq python3-fastapi \
  python3-jmespath python3-pyhumps

# VyOS-repo-only packages. These live in the VyOS dev repo (when present) and
# are NOT in Debian bookworm main. Do not fail the build if they are
# unavailable: upstream T9216 made the vici import non-fatal for the vyos-1x
# build (pylint is told to ignore it), and the ISO's runtime versions come
# from the vyos-1x package build / live-build chroot, not this host step.
apt-get install -y --no-install-recommends \
  python3-vici python3-certbot-nginx python3-hurry.filesize python3-nose2 \
  || echo "### VyOS-repo-only deps unavailable (expected without vyos-dev repo) — continuing"