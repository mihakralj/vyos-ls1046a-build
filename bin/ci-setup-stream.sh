#!/bin/bash
# ci-setup-stream.sh — Download, verify, and extract VyOS Stream source tarball
# Called by: .github/workflows/stream-build.yml
# Expects: STREAM_VERSION, GITHUB_WORKSPACE set in env
set -ex
cd "${GITHUB_WORKSPACE:-.}"

STREAM_VERSION="${STREAM_VERSION:?STREAM_VERSION env var required}"
BASE_URL="https://community-downloads.vyos.dev/stream/${STREAM_VERSION}"
TARBALL="circinus-${STREAM_VERSION}.tar.gz"
MINISIG="${TARBALL}.minisig"

# VyOS Stream public minisign key (from https://vyos.net/get/stream/)
VYOS_STREAM_PUBKEY="RWTR1ty93Oyontk6caB9WqmiQC4fgeyd/ejgRxCRGd2MQej7nqebHneP"

### 1. Download tarball and signature
echo "### Downloading VyOS Stream ${STREAM_VERSION} source tarball"
curl -fSL -o "$TARBALL" "${BASE_URL}/${TARBALL}"
curl -fSL -o "$MINISIG" "${BASE_URL}/${MINISIG}"
echo "### Downloaded: $(stat -c '%s bytes' "$TARBALL")"

### 2. Verify signature
echo "### Verifying signature"
if command -v minisign &>/dev/null; then
  minisign -Vm "$TARBALL" -x "$MINISIG" -P "$VYOS_STREAM_PUBKEY"
  echo "### Signature verified OK"
elif [ -x bin/minisign ]; then
  bin/minisign -Vm "$TARBALL" -x "$MINISIG" -P "$VYOS_STREAM_PUBKEY"
  echo "### Signature verified OK (using local minisign)"
else
  echo "WARNING: minisign not found, skipping signature verification"
fi

### 3. Extract tarball
echo "### Extracting tarball"
tar xzf "$TARBALL"
rm -f "$TARBALL" "$MINISIG"

# The tarball extracts to circinus-${STREAM_VERSION}/
EXTRACT_DIR="circinus-${STREAM_VERSION}"
if [ ! -d "$EXTRACT_DIR" ]; then
  # Fallback: try to find the extracted directory
  EXTRACT_DIR=$(find . -maxdepth 1 -type d -name 'circinus-*' | head -1)
  if [ -z "$EXTRACT_DIR" ]; then
    echo "ERROR: Cannot find extracted stream source directory"
    ls -la
    exit 1
  fi
fi
echo "### Extracted to: $EXTRACT_DIR"
ls -la "$EXTRACT_DIR/"

### 4. Move vyos-build into place
if [ -d "$EXTRACT_DIR/vyos-build" ]; then
  mv "$EXTRACT_DIR/vyos-build" vyos-build
  echo "### vyos-build moved to workspace root"
else
  echo "ERROR: vyos-build not found in stream tarball"
  ls -la "$EXTRACT_DIR/"
  exit 1
fi

### 5. Pre-populate vyos-1x for build.py
# build.py runs from scripts/package-build/vyos-1x/ and looks for
# repo_dir = Path("vyos-1x") relative to CWD. If it exists, git clone
# is skipped. We must also make it a valid git repo so git checkout HEAD succeeds.
VYOS1X_BUILD_DIR="vyos-build/scripts/package-build/vyos-1x"
if [ -d "$EXTRACT_DIR/vyos-1x" ]; then
  mkdir -p "$VYOS1X_BUILD_DIR"
  mv "$EXTRACT_DIR/vyos-1x" "$VYOS1X_BUILD_DIR/vyos-1x"

  # Make it a minimal git repo so build.py's `git checkout HEAD` succeeds
  pushd "$VYOS1X_BUILD_DIR/vyos-1x"
  git init
  git config user.email "build@ls1046a"
  git config user.name "LS1046A Stream Build"
  git add -A
  git commit -m "VyOS Stream ${STREAM_VERSION} frozen source"
  popd
  echo "### vyos-1x pre-populated and git-init'd"
else
  echo "WARNING: vyos-1x not found in stream tarball — build.py will attempt git clone"
fi

### 6. Pre-populate other packages that may be in the tarball
# build.py also builds packages listed in linux-kernel/package.toml.
# If those source dirs exist in the tarball, pre-populate them too.
KERNEL_BUILD_DIR="vyos-build/scripts/package-build/linux-kernel"
for pkg_dir in "$EXTRACT_DIR"/*/; do
  pkg_name=$(basename "$pkg_dir")
  case "$pkg_name" in
    vyos-build|vyos-1x) continue ;;  # already handled
  esac
  # Check if this package is referenced in the kernel package.toml
  # or any other package.toml — pre-populate if so
  if [ -d "$pkg_dir" ] && [ "$(ls -A "$pkg_dir")" ]; then
    echo "### Found extra package in tarball: $pkg_name"
    # Don't move — just note it. The package build dirs are per-package.toml
    # and we only build linux-kernel + vyos-1x
  fi
done

### 7. Clean up extracted directory
rm -rf "$EXTRACT_DIR"

echo "### Stream source setup complete (VyOS Stream ${STREAM_VERSION})"
