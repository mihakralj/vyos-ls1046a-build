#!/bin/bash
# ci-build-fmlib.sh — Cross-compile fmlib from source with mono ASK extensions
#
# Called by: ci-build-ask-userspace.sh (before dpa_app/fmc build)
# Usage:     ci-build-fmlib.sh <kernel-src-dir> <staging-dir>
#
# Produces:
#   $STAGING/lib/libfm.a           — aarch64 static library
#   $STAGING/include/fmd/**        — patched public headers (with `bool shared`, etc.)
#
# Rationale:
#   The mono-ask-extensions patch ADDS FIELDS to `t_FmPcdKgSchemeParams` and
#   `t_FmPcdHashTableParams`. Any consumer (dpa_app, libfmc) compiled against the
#   patched headers MUST link against a libfm.a built from the same patched
#   source — otherwise struct offsets diverge → heap corruption → SIGSEGV
#   inside libfmc C++ destructors during XML config processing.
#
#   Pre-built libfm.a in data/ask-userspace/fmlib/ cannot be trusted because
#   there is no way to prove it was built from the current patch. Rebuild it.

set -e

KSRC="${1:?Usage: ci-build-fmlib.sh <kernel-src-dir> <staging-dir>}"
STAGING="${2:?Usage: ci-build-fmlib.sh <kernel-src-dir> <staging-dir>}"

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(dirname "$SCRIPT_DIR")"

# Upstream source pinned to the version we validated.
FMLIB_REPO="https://github.com/nxp-qoriq/fmlib.git"
FMLIB_REF="lf-6.18.2-1.0.0"
FMLIB_COMMIT="7a58eca"   # expected HEAD for lf-6.18.2-1.0.0 (informational)

PATCH="$REPO_ROOT/ask-ls1046a-6.6/patches/fmlib/01-mono-ask-extensions.patch"
[ -f "$PATCH" ] || { echo "ERROR: fmlib patch not found: $PATCH" >&2; exit 1; }

# Cross-compile detection (matches ci-build-ask-userspace.sh)
if [ "$(uname -m)" = "aarch64" ]; then
  CROSS=""
else
  CROSS="${CROSS_COMPILE:-aarch64-linux-gnu-}"
fi

WORK="${FMLIB_WORK_DIR:-/tmp/fmlib-build}"
rm -rf "$WORK"
mkdir -p "$WORK"

echo "=== Building fmlib from source ==="
echo "    repo=$FMLIB_REPO ref=$FMLIB_REF"
echo "    KSRC=$KSRC"
echo "    STAGING=$STAGING"
echo "    WORK=$WORK"

# 1. Clone (shallow, single ref)
echo "--- cloning fmlib ---"
if ! git clone --depth 1 --branch "$FMLIB_REF" "$FMLIB_REPO" "$WORK/fmlib" 2>&1 | tail -5; then
  echo "    shallow clone by tag failed, retrying full clone + checkout..."
  git clone "$FMLIB_REPO" "$WORK/fmlib" 2>&1 | tail -3
  git -C "$WORK/fmlib" checkout "$FMLIB_REF" 2>&1 | tail -3
fi
HEAD_SHA=$(git -C "$WORK/fmlib" rev-parse --short HEAD)
echo "    cloned head=$HEAD_SHA (expected starts with $FMLIB_COMMIT)"

# 2. Normalise line endings then apply ASK patch
find "$WORK/fmlib" \( -name "*.c" -o -name "*.h" \) -exec sed -i 's/\r$//' {} +
echo "--- applying $PATCH ---"
(cd "$WORK/fmlib" && patch --no-backup-if-mismatch -p1 < "$PATCH")
echo "    patch applied OK"

# Sanity: verify the critical struct field is present after patch
if ! grep -q "bool.*shared" "$WORK/fmlib/include/fmd/Peripherals/fm_pcd_ext.h"; then
  echo "ERROR: expected field 'bool shared' missing from fm_pcd_ext.h after patching" >&2
  exit 1
fi
echo "    verified: bool shared present in t_FmPcdKgSchemeParams"

# 3. Provide NXP FMD ioctl headers. fmlib's Makefile expects them under
#    $(KERNEL_SRC)/include/uapi/linux/fmd/ — ci-setup-kernel-ask.sh already
#    extracted them into $KSRC, so pointing KERNEL_SRC at it is sufficient.
if [ ! -d "$KSRC/include/uapi/linux/fmd" ]; then
  echo "ERROR: NXP FMD ioctl headers not found at $KSRC/include/uapi/linux/fmd" >&2
  echo "       ci-setup-kernel-ask.sh must run before this script" >&2
  exit 1
fi

# 4. Build. fmlib ships a single top-level Makefile with targets
#    libfm-<arch>.a (libfm-arm.a for aarch64/LS1043). Invoke from root.
echo "--- building libfm-arm.a (aarch64) ---"
cd "$WORK/fmlib"
set +e
make libfm-arm.a \
  CROSS_COMPILE="$CROSS" \
  KERNEL_SRC="$KSRC" 2>&1 | tee /tmp/fmlib-build.log | tail -40
MAKE_RC=${PIPESTATUS[0]}
set -e
if [ "$MAKE_RC" != "0" ]; then
  echo "ERROR: make libfm-arm.a exited $MAKE_RC" >&2
  echo "--- full log ---" >&2
  tail -80 /tmp/fmlib-build.log >&2 || true
  exit 1
fi

# Locate the produced archive (at repo root per the Makefile's `%.a: %.o` rule)
LIBFM=""
for cand in libfm-arm.a libfm.a src/libfm-arm.a src/libfm.a ; do
  if [ -f "$cand" ]; then LIBFM="$cand"; break; fi
done
if [ -z "$LIBFM" ]; then
  echo "ERROR: libfm archive not produced. Candidates checked:" >&2
  find . -name 'libfm*.a' >&2 || true
  exit 1
fi
echo "    built: $LIBFM ($(stat -c '%s bytes' "$LIBFM"))"

# 5. Install into staging
install -D -m 0644 "$LIBFM" "$STAGING/lib/libfm.a"
mkdir -p "$STAGING/include/fmd"
cp -a include/fmd/. "$STAGING/include/fmd/"
# Some consumers expect certain headers at top-level of include/
for h in std_ext.h error_ext.h part_ext.h ; do
  if [ -f "include/$h" ]; then
    cp "include/$h" "$STAGING/include/"
  fi
done
# Symlink /fmd/fm_ext.h → include/fm_ext.h path (convenience for fmc)
echo "    installed: $STAGING/lib/libfm.a"
echo "    installed headers: $(find "$STAGING/include/fmd" -name '*.h' | wc -l) files"

# 6. Cleanup work dir (keep patch applied copy only for debug if FMLIB_KEEP=1)
if [ "${FMLIB_KEEP:-0}" != "1" ]; then
  rm -rf "$WORK"
fi

echo "=== fmlib build complete ==="