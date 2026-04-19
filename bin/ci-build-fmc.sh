#!/bin/bash
# ci-build-fmc.sh — Cross-compile fmc from source with mono ASK extensions
#
# Called by: ci-build-ask-userspace.sh (after fmlib build, before dpa_app build)
# Usage:     ci-build-fmc.sh <staging-dir>
#
# Produces:
#   $STAGING/lib/libfmc.a          — aarch64 static library (used by dpa_app)
#   $STAGING/include/fmc.h         — public API header
#   $STAGING/bin/fmc               — optional standalone binary (installed to chroot)
#
# Requires (in $STAGING):
#   lib/libfm.a                    — built by ci-build-fmlib.sh
#   include/fmd/**                 — patched fmlib headers (with 'bool shared' etc.)

set -e

STAGING="${1:?Usage: ci-build-fmc.sh <staging-dir>}"

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(dirname "$SCRIPT_DIR")"

FMC_REPO="https://github.com/nxp-qoriq/fmc.git"
FMC_REF="lf-6.18.2-1.0.0"
FMC_COMMIT="5b9f4b1"

PATCH="$REPO_ROOT/ask-ls1046a-6.6/patches/fmc/01-mono-ask-extensions.patch"
[ -f "$PATCH" ] || { echo "ERROR: fmc patch not found: $PATCH" >&2; exit 1; }
[ -f "$STAGING/lib/libfm.a" ] || { echo "ERROR: libfm.a missing from staging; run ci-build-fmlib.sh first" >&2; exit 1; }
[ -d "$STAGING/include/fmd" ] || { echo "ERROR: fmd headers missing from staging" >&2; exit 1; }

# Cross-compile detection
if [ "$(uname -m)" = "aarch64" ]; then
  CC="${CC:-gcc}"
  CXX="${CXX:-g++}"
  AR="${AR:-ar}"
else
  CROSS="${CROSS_COMPILE:-aarch64-linux-gnu-}"
  CC="${CC:-${CROSS}gcc}"
  CXX="${CXX:-${CROSS}g++}"
  AR="${AR:-${CROSS}ar}"
fi

WORK="${FMC_WORK_DIR:-/tmp/fmc-build}"
rm -rf "$WORK"
mkdir -p "$WORK"

echo "=== Building fmc from source ==="
echo "    repo=$FMC_REPO ref=$FMC_REF"
echo "    STAGING=$STAGING"
echo "    CC=$CC  CXX=$CXX"

echo "--- cloning fmc ---"
if ! git clone --depth 1 --branch "$FMC_REF" "$FMC_REPO" "$WORK/fmc" 2>&1 | tail -5; then
  echo "    shallow clone by tag failed, retrying full clone + checkout..."
  git clone "$FMC_REPO" "$WORK/fmc" 2>&1 | tail -3
  git -C "$WORK/fmc" checkout "$FMC_REF" 2>&1 | tail -3
fi
HEAD_SHA=$(git -C "$WORK/fmc" rev-parse --short HEAD)
echo "    cloned head=$HEAD_SHA (expected starts with $FMC_COMMIT)"

# Normalise line endings (per data/ask-userspace/fmc/README.md recipe)
find "$WORK/fmc" \( -name "*.cpp" -o -name "*.h" -o -name "*.c" \) -exec sed -i 's/\r$//' {} +

echo "--- applying $PATCH ---"
(cd "$WORK/fmc" && patch --no-backup-if-mismatch -p1 < "$PATCH")
echo "    patch applied OK"

# Ensure libxml2 + tclap headers are available on the builder
LIBXML2_INC="${LIBXML2_HEADER_PATH:-/usr/include/libxml2}"
TCLAP_INC="${TCLAP_HEADER_PATH:-/usr/include}"
if [ ! -f "$LIBXML2_INC/libxml/parser.h" ]; then
  # Try dpkg-architecture-specific location (Debian multiarch)
  for d in /usr/include/aarch64-linux-gnu/libxml2 /usr/include/x86_64-linux-gnu/libxml2 ; do
    if [ -f "$d/libxml/parser.h" ]; then LIBXML2_INC="$d"; break; fi
  done
fi
if [ ! -f "$LIBXML2_INC/libxml/parser.h" ]; then
  echo "ERROR: libxml2 headers not found (tried $LIBXML2_INC)" >&2
  exit 1
fi
if [ ! -f "$TCLAP_INC/tclap/CmdLine.h" ]; then
  echo "ERROR: tclap headers not found at $TCLAP_INC/tclap/ (install libtclap-dev)" >&2
  exit 1
fi
echo "    libxml2 inc: $LIBXML2_INC"
echo "    tclap inc:   $TCLAP_INC"

echo "--- building libfmc.a + fmc (aarch64) ---"
cd "$WORK/fmc/source"

# The fmc Makefile accepts these overrides (see data/ask-userspace/fmc/README.md)
# NOTE: passing CFLAGS/CXXFLAGS on the command line would fully REPLACE the
# Makefile's `+= -I$(FMD_USPACE_HEADER_PATH)` additions (make treats cmdline
# vars as immutable). CPPFLAGS is NOT referenced by this Makefile's CFLAGS
# block so it is appended by the implicit compile rule only — use it to add
# -fpermissive (libxml2 API signature changed; fmc targets older API).
make libfmc.a fmc \
  CC="$CC" CXX="$CXX" AR="$AR" \
  MACHINE=ls1046 \
  FMD_USPACE_HEADER_PATH="$STAGING/include/fmd" \
  FMD_USPACE_LIB_PATH="$STAGING/lib" \
  LIBXML2_HEADER_PATH="$LIBXML2_INC" \
  TCLAP_HEADER_PATH="$TCLAP_INC" \
  CPPFLAGS="-fpermissive" 2>&1 | tail -40

[ -f libfmc.a ] || { echo "ERROR: libfmc.a not produced" >&2; exit 1; }
[ -f fmc ]    || { echo "ERROR: fmc binary not produced" >&2; exit 1; }
echo "    built libfmc.a: $(stat -c '%s bytes' libfmc.a)"
echo "    built fmc:      $(stat -c '%s bytes' fmc)"

# Install into staging
install -D -m 0644 libfmc.a "$STAGING/lib/libfmc.a"
# fmc.h lives in source tree at source/fmc.h
if [ -f fmc.h ]; then
  install -D -m 0644 fmc.h "$STAGING/include/fmc.h"
elif [ -f ../source/fmc.h ]; then
  install -D -m 0644 ../source/fmc.h "$STAGING/include/fmc.h"
fi
mkdir -p "$STAGING/bin"
install -m 0755 fmc "$STAGING/bin/fmc"
echo "    installed to $STAGING/lib/libfmc.a, $STAGING/bin/fmc"

# Cleanup
if [ "${FMC_KEEP:-0}" != "1" ]; then
  rm -rf "$WORK"
fi

echo "=== fmc build complete ==="