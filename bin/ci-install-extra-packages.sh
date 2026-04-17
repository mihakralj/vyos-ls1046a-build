#!/bin/bash
# ci-install-extra-packages.sh — Download and stage third-party binaries
# not available in default Debian repos into the ISO chroot.
# Called by: .github/workflows/auto-build.yml after "Pick Packages"
# Expects: GITHUB_WORKSPACE set in env
#
# All extra packages are optional best-effort: a failure to fetch/install any
# single package logs a WARNING and the script continues. The script always
# exits 0 so a flaky upstream release mirror cannot break the ISO build.
set -uo pipefail
cd "${GITHUB_WORKSPACE:-.}"

CHROOT=vyos-build/data/live-build-config/includes.chroot
ARCH="aarch64"
mkdir -p "$CHROOT/usr/local/bin"

TMP_DIR=$(mktemp -d)
trap 'rm -rf "$TMP_DIR"' EXIT

# Wrap a single package install block. Any error inside the body is caught and
# logged; the script keeps going.
try_install() {
  local name="$1"
  shift
  echo "### Installing $name"
  if ! ( set -e; "$@" ); then
    echo "WARNING: $name install failed, skipping" >&2
  fi
}

###############################################################################
# Ookla Speedtest CLI
###############################################################################
install_speedtest() {
  local base="https://install.speedtest.net/app/cli"
  local version
  version=$(curl -fsSL --max-time 30 https://packagecloud.io/ookla/speedtest-cli \
    | grep -oP 'speedtest_\K[0-9]+\.[0-9]+\.[0-9]+' \
    | sort -V | tail -1 || true)
  [ -n "$version" ] || { echo "WARNING: Could not determine speedtest version" >&2; return 0; }
  local file="ookla-speedtest-${version}-linux-${ARCH}.tgz"
  echo "Downloading speedtest ${version} from ${base}/${file}"
  curl -fSL --max-time 120 -o "${TMP_DIR}/${file}" "${base}/${file}"
  tar xzf "${TMP_DIR}/${file}" -C "$TMP_DIR"
  install -m 755 "${TMP_DIR}/speedtest" "$CHROOT/usr/local/bin/speedtest"
  echo "### Speedtest ${version} staged to includes.chroot/usr/local/bin/"
}
try_install "Ookla Speedtest CLI" install_speedtest

###############################################################################
# bandwhich — terminal bandwidth utilization tool
###############################################################################
install_bandwhich() {
  local v
  v=$(curl -fsSI --max-time 30 https://github.com/imsnif/bandwhich/releases/latest \
    | grep -i '^location:' | grep -oP 'v\K[0-9]+\.[0-9]+\.[0-9]+' || true)
  [ -n "$v" ] || { echo "WARNING: Could not determine bandwhich version" >&2; return 0; }
  local url="https://github.com/imsnif/bandwhich/releases/download/v${v}/bandwhich-v${v}-aarch64-unknown-linux-musl.tar.gz"
  echo "Downloading bandwhich ${v} from ${url}"
  curl -fSL --max-time 120 -o "${TMP_DIR}/bandwhich.tar.gz" "$url"
  tar xzf "${TMP_DIR}/bandwhich.tar.gz" -C "$TMP_DIR"
  install -m 755 "${TMP_DIR}/bandwhich" "$CHROOT/usr/local/bin/bandwhich"
  echo "### bandwhich ${v} staged to includes.chroot/usr/local/bin/"
}
try_install "bandwhich" install_bandwhich

###############################################################################
# trippy — network diagnostic tool (mtr alternative)
###############################################################################
install_trippy() {
  local v
  v=$(curl -fsSI --max-time 30 https://github.com/fujiapple852/trippy/releases/latest \
    | grep -i '^location:' | grep -oP 'v?\K[0-9]+\.[0-9]+\.[0-9]+' || true)
  [ -n "$v" ] || { echo "WARNING: Could not determine trippy version" >&2; return 0; }
  local url="https://github.com/fujiapple852/trippy/releases/download/${v}/trippy-${v}-aarch64-unknown-linux-musl.tar.gz"
  echo "Downloading trippy ${v} from ${url}"
  curl -fSL --max-time 120 -o "${TMP_DIR}/trippy.tar.gz" "$url"
  tar xzf "${TMP_DIR}/trippy.tar.gz" -C "$TMP_DIR"
  local bin
  bin=$(find "$TMP_DIR" -name 'trip' -type f | head -1)
  [ -n "$bin" ] || { echo "WARNING: trip binary not found in archive" >&2; return 0; }
  install -m 755 "$bin" "$CHROOT/usr/local/bin/trip"
  echo "### trippy ${v} staged to includes.chroot/usr/local/bin/"
}
try_install "trippy" install_trippy

###############################################################################
# gping — ping with a graph
###############################################################################
install_gping() {
  local v
  v=$(curl -fsSI --max-time 30 https://github.com/orf/gping/releases/latest \
    | grep -i '^location:' | grep -oP 'gping-v\K[0-9]+\.[0-9]+\.[0-9]+' || true)
  [ -n "$v" ] || { echo "WARNING: Could not determine gping version" >&2; return 0; }
  local url="https://github.com/orf/gping/releases/download/gping-v${v}/gping-Linux-musl-arm64.tar.gz"
  echo "Downloading gping ${v} from ${url}"
  curl -fSL --max-time 120 -o "${TMP_DIR}/gping.tar.gz" "$url"
  tar xzf "${TMP_DIR}/gping.tar.gz" -C "$TMP_DIR"
  install -m 755 "${TMP_DIR}/gping" "$CHROOT/usr/local/bin/gping"
  echo "### gping ${v} staged to includes.chroot/usr/local/bin/"
}
try_install "gping" install_gping

###############################################################################
# doggo — DNS client (dig alternative)
###############################################################################
install_doggo() {
  local v
  v=$(curl -fsSI --max-time 30 https://github.com/mr-karan/doggo/releases/latest \
    | grep -i '^location:' | grep -oP 'v\K[0-9]+\.[0-9]+\.[0-9]+' || true)
  [ -n "$v" ] || { echo "WARNING: Could not determine doggo version" >&2; return 0; }
  local url="https://github.com/mr-karan/doggo/releases/download/v${v}/doggo_${v}_Linux_arm64.tar.gz"
  echo "Downloading doggo ${v} from ${url}"
  curl -fSL --max-time 120 -o "${TMP_DIR}/doggo.tar.gz" "$url"
  tar xzf "${TMP_DIR}/doggo.tar.gz" -C "$TMP_DIR"
  local bin
  bin=$(find "$TMP_DIR" -name 'doggo' -type f -executable | head -1)
  [ -n "$bin" ] || bin=$(find "$TMP_DIR" -name 'doggo' -type f | head -1)
  [ -n "$bin" ] || { echo "WARNING: doggo binary not found in archive" >&2; return 0; }
  install -m 755 "$bin" "$CHROOT/usr/local/bin/doggo"
  echo "### doggo ${v} staged to includes.chroot/usr/local/bin/"
}
try_install "doggo" install_doggo

###############################################################################
# Add more third-party packages below using the same pattern:
#   1. Download binary/archive to $TMP_DIR
#   2. Extract if needed
#   3. Install to $CHROOT/usr/local/bin/ (or other appropriate path)
###############################################################################
