#!/bin/bash
# ci-install-extra-packages.sh — Download and stage third-party binaries
# not available in default Debian repos into the ISO chroot.
# Called by: .github/workflows/auto-build.yml after "Pick Packages"
# Expects: GITHUB_WORKSPACE set in env
set -euo pipefail
cd "${GITHUB_WORKSPACE:-.}"

CHROOT=vyos-build/data/live-build-config/includes.chroot
ARCH="aarch64"
mkdir -p "$CHROOT/usr/local/bin"

###############################################################################
# Ookla Speedtest CLI
###############################################################################
echo "### Installing Ookla Speedtest CLI"
TMP_DIR=$(mktemp -d)
trap 'rm -rf "$TMP_DIR"' EXIT

BASE_URL="https://install.speedtest.net/app/cli"
VERSION=$(curl -sL https://packagecloud.io/ookla/speedtest-cli \
  | grep -oP 'speedtest_\K[0-9]+\.[0-9]+\.[0-9]+' \
  | sort -V \
  | tail -1)

if [ -z "$VERSION" ]; then
  echo "WARNING: Could not determine speedtest version, skipping" >&2
else
  FILENAME="ookla-speedtest-${VERSION}-linux-${ARCH}.tgz"
  URL="${BASE_URL}/${FILENAME}"
  echo "Downloading speedtest ${VERSION} from ${URL}"
  if curl -fSL -o "${TMP_DIR}/${FILENAME}" "$URL"; then
    tar xzf "${TMP_DIR}/${FILENAME}" -C "$TMP_DIR"
    install -m 755 "${TMP_DIR}/speedtest" "$CHROOT/usr/local/bin/speedtest"
    echo "### Speedtest ${VERSION} staged to includes.chroot/usr/local/bin/"
  else
    echo "WARNING: Failed to download speedtest, skipping" >&2
  fi
fi

###############################################################################
# bandwhich — terminal bandwidth utilization tool
###############################################################################
echo "### Installing bandwhich"
BANDWHICH_VERSION=$(curl -sI https://github.com/imsnif/bandwhich/releases/latest \
  | grep -i '^location:' | grep -oP 'v\K[0-9]+\.[0-9]+\.[0-9]+')

if [ -z "$BANDWHICH_VERSION" ]; then
  echo "WARNING: Could not determine bandwhich version, skipping" >&2
else
  BANDWHICH_URL="https://github.com/imsnif/bandwhich/releases/download/v${BANDWHICH_VERSION}/bandwhich-v${BANDWHICH_VERSION}-aarch64-unknown-linux-musl.tar.gz"
  echo "Downloading bandwhich ${BANDWHICH_VERSION} from ${BANDWHICH_URL}"
  if curl -fSL -o "${TMP_DIR}/bandwhich.tar.gz" "$BANDWHICH_URL"; then
    tar xzf "${TMP_DIR}/bandwhich.tar.gz" -C "$TMP_DIR"
    install -m 755 "${TMP_DIR}/bandwhich" "$CHROOT/usr/local/bin/bandwhich"
    echo "### bandwhich ${BANDWHICH_VERSION} staged to includes.chroot/usr/local/bin/"
  else
    echo "WARNING: Failed to download bandwhich, skipping" >&2
  fi
fi

###############################################################################
# trippy — network diagnostic tool (mtr alternative)
###############################################################################
echo "### Installing trippy"
TRIPPY_VERSION=$(curl -sI https://github.com/fujiapple852/trippy/releases/latest \
  | grep -i '^location:' | grep -oP 'v?\K[0-9]+\.[0-9]+\.[0-9]+')

if [ -z "$TRIPPY_VERSION" ]; then
  echo "WARNING: Could not determine trippy version, skipping" >&2
else
  TRIPPY_URL="https://github.com/fujiapple852/trippy/releases/download/${TRIPPY_VERSION}/trippy-${TRIPPY_VERSION}-aarch64-unknown-linux-musl.tar.gz"
  echo "Downloading trippy ${TRIPPY_VERSION} from ${TRIPPY_URL}"
  if curl -fSL -o "${TMP_DIR}/trippy.tar.gz" "$TRIPPY_URL"; then
    tar xzf "${TMP_DIR}/trippy.tar.gz" -C "$TMP_DIR"
    TRIPPY_BIN=$(find "$TMP_DIR" -name 'trip' -type f | head -1)
    if [ -n "$TRIPPY_BIN" ]; then
      install -m 755 "$TRIPPY_BIN" "$CHROOT/usr/local/bin/trip"
      echo "### trippy ${TRIPPY_VERSION} staged to includes.chroot/usr/local/bin/"
    else
      echo "WARNING: trip binary not found in archive" >&2
    fi
  else
    echo "WARNING: Failed to download trippy, skipping" >&2
  fi
fi

###############################################################################
# gping — ping with a graph
###############################################################################
echo "### Installing gping"
GPING_VERSION=$(curl -sI https://github.com/orf/gping/releases/latest \
  | grep -i '^location:' | grep -oP 'gping-v\K[0-9]+\.[0-9]+\.[0-9]+')

if [ -z "$GPING_VERSION" ]; then
  echo "WARNING: Could not determine gping version, skipping" >&2
else
  GPING_URL="https://github.com/orf/gping/releases/download/gping-v${GPING_VERSION}/gping-Linux-musl-arm64.tar.gz"
  echo "Downloading gping ${GPING_VERSION} from ${GPING_URL}"
  if curl -fSL -o "${TMP_DIR}/gping.tar.gz" "$GPING_URL"; then
    tar xzf "${TMP_DIR}/gping.tar.gz" -C "$TMP_DIR"
    install -m 755 "${TMP_DIR}/gping" "$CHROOT/usr/local/bin/gping"
    echo "### gping ${GPING_VERSION} staged to includes.chroot/usr/local/bin/"
  else
    echo "WARNING: Failed to download gping, skipping" >&2
  fi
fi

###############################################################################
# doggo — DNS client (dig alternative)
###############################################################################
echo "### Installing doggo"
DOGGO_VERSION=$(curl -sI https://github.com/mr-karan/doggo/releases/latest \
  | grep -i '^location:' | grep -oP 'v\K[0-9]+\.[0-9]+\.[0-9]+')

if [ -z "$DOGGO_VERSION" ]; then
  echo "WARNING: Could not determine doggo version, skipping" >&2
else
  DOGGO_URL="https://github.com/mr-karan/doggo/releases/download/v${DOGGO_VERSION}/doggo_${DOGGO_VERSION}_Linux_arm64.tar.gz"
  echo "Downloading doggo ${DOGGO_VERSION} from ${DOGGO_URL}"
  if curl -fSL -o "${TMP_DIR}/doggo.tar.gz" "$DOGGO_URL"; then
    tar xzf "${TMP_DIR}/doggo.tar.gz" -C "$TMP_DIR"
    DOGGO_BIN=$(find "$TMP_DIR" -name 'doggo' -type f -executable | head -1)
    if [ -z "$DOGGO_BIN" ]; then
      DOGGO_BIN=$(find "$TMP_DIR" -name 'doggo' -type f | head -1)
    fi
    if [ -n "$DOGGO_BIN" ]; then
      install -m 755 "$DOGGO_BIN" "$CHROOT/usr/local/bin/doggo"
      echo "### doggo ${DOGGO_VERSION} staged to includes.chroot/usr/local/bin/"
    else
      echo "WARNING: doggo binary not found in archive" >&2
    fi
  else
    echo "WARNING: Failed to download doggo, skipping" >&2
  fi
fi

###############################################################################
# Add more third-party packages below using the same pattern:
#   1. Download binary/archive to $TMP_DIR
#   2. Extract if needed
#   3. Install to $CHROOT/usr/local/bin/ (or other appropriate path)
###############################################################################
