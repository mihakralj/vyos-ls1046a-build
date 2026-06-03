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

# The shipped vyos-dev.key is signed with a SHA1 self-signature which
# newer apt/sqv policies reject. The key is fine, the *signature* on it
# is just old. We trust it (the upstream vyos-builder Docker container
# trusts it too), so mark the apt source as `trusted=yes` to bypass the
# signature check.
install -m 0644 vyos-build/docker/vyos-dev.key \
  /usr/share/keyrings/vyos-dev-archive-keyring.asc

# Override the shipped vyos-dev.list to add `trusted=yes`.
echo 'deb [trusted=yes signed-by=/usr/share/keyrings/vyos-dev-archive-keyring.asc] https://packages.vyos.net/repositories/rolling rolling main' \
  > /etc/apt/sources.list.d/vyos-dev.list
cat /etc/apt/sources.list.d/vyos-dev.list

apt-get update -qq
apt-get install -y --no-install-recommends \
  libzmq3-dev pylint whois \
  python3-pyudev python3-systemd python3-pam python3-pyroute2 \
  python3-voluptuous python3-lxml python3-xmltodict python3-coverage \
  python3-netaddr python3-netifaces python3-paramiko python3-passlib \
  python3-psutil python3-tabulate python3-zmq python3-fastapi \
  python3-vici python3-certbot-nginx python3-hurry.filesize \
  python3-nose2 \
  python3-jmespath python3-pyhumps