#!/bin/bash
# ci-build-ask-userspace.sh — Cross-compile ASK userspace binaries from source
#
# Called by: ci-build-packages.sh (after kernel build, while $KSRC exists)
# Expects:  $1 = kernel source dir (for FMD headers)
#           $2 = output dir (includes.chroot prefix)
#           GITHUB_WORKSPACE or repo root auto-detected
#
# Build order (dependency chain):
#   1. libcli       (no deps)
#   2. libfci       (no deps)
#   3. dpa_app      (libcli + pre-built libfmc/libfm)
#   4. cmm          (libfci + libcli + pre-built libnfnetlink/libnetfilter-conntrack + libpcap)
#
# Pre-built dependencies NOT rebuilt here (kept from data/ask-userspace/):
#   - libnfnetlink, libnetfilter-conntrack (NXP-patched, require upstream download + patch)
#   - fmlib, fmc (C++ project, complex build — future phase)

set -e

KSRC="${1:?Usage: ci-build-ask-userspace.sh <kernel-src-dir> <output-chroot-dir>}"
CHROOT="${2:?Usage: ci-build-ask-userspace.sh <kernel-src-dir> <output-chroot-dir>}"

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(dirname "$SCRIPT_DIR")"
ASK_SRC="$REPO_ROOT/ask-ls1046a-6.6"
PREBUILT="$REPO_ROOT/data/ask-userspace"

# Build staging area — holds compiled libs/headers for inter-component linking
STAGING="$REPO_ROOT/build-ask-staging"
rm -rf "$STAGING"
mkdir -p "$STAGING"/{lib,include,share/pkgconfig}

# Detect native vs cross compilation
ARCH_NATIVE=$(uname -m)
if [ "$ARCH_NATIVE" = "aarch64" ]; then
  CC="${CC:-gcc}"
  CXX="${CXX:-g++}"
  AR="${AR:-ar}"
  RANLIB="${RANLIB:-ranlib}"
  STRIP="${STRIP:-strip}"
  HOST_TRIPLET=""
else
  CROSS="${CROSS_COMPILE:-aarch64-linux-gnu-}"
  CC="${CC:-${CROSS}gcc}"
  CXX="${CXX:-${CROSS}g++}"
  AR="${AR:-${CROSS}ar}"
  RANLIB="${RANLIB:-${CROSS}ranlib}"
  STRIP="${STRIP:-${CROSS}strip}"
  HOST_TRIPLET="--host=aarch64-linux-gnu --build=$(uname -m)-linux-gnu"
fi

COMMON_CFLAGS="-O2 -D_FORTIFY_SOURCE=2 -fstack-protector-strong -DLS1043"
NPROC=$(nproc 2>/dev/null || echo 2)

echo "=== ASK Userspace Build ==="
echo "    CC=$CC  ARCH=$ARCH_NATIVE  JOBS=$NPROC"
echo "    KSRC=$KSRC"
echo "    STAGING=$STAGING"
echo "    CHROOT=$CHROOT"

### ====================================================================
### Stage 0: Populate staging sysroot with pre-built dependency headers/libs
### ====================================================================
echo ""
echo "### Stage 0: Populating staging sysroot from pre-built dependencies"

# libnfnetlink (NXP-patched) — headers + .so for cmm link
if [ -d "$PREBUILT/libnfnetlink" ]; then
  cp -a "$PREBUILT/libnfnetlink/include/"* "$STAGING/include/"
  cp -a "$PREBUILT/libnfnetlink/libnfnetlink.so"* "$STAGING/lib/" 2>/dev/null || true
  cp -a "$PREBUILT/libnfnetlink/pkgconfig/"* "$STAGING/share/pkgconfig/" 2>/dev/null || true
  # Fix pkgconfig prefix to point to staging
  for pc in "$STAGING/share/pkgconfig/"*nfnetlink*.pc; do
    [ -f "$pc" ] && sed -i "s|^prefix=.*|prefix=$STAGING|; s|^libdir=.*|libdir=$STAGING/lib|; s|^includedir=.*|includedir=$STAGING/include|" "$pc"
  done
fi

# libnetfilter_conntrack (NXP-patched) — headers + .so for cmm link
if [ -d "$PREBUILT/libnetfilter-conntrack" ]; then
  cp -a "$PREBUILT/libnetfilter-conntrack/include/"* "$STAGING/include/"
  cp -a "$PREBUILT/libnetfilter-conntrack/libnetfilter_conntrack.so"* "$STAGING/lib/" 2>/dev/null || true
  cp -a "$PREBUILT/libnetfilter-conntrack/pkgconfig/"* "$STAGING/share/pkgconfig/" 2>/dev/null || true
  for pc in "$STAGING/share/pkgconfig/"*conntrack*.pc; do
    [ -f "$pc" ] && sed -i "s|^prefix=.*|prefix=$STAGING|; s|^libdir=.*|libdir=$STAGING/lib|; s|^includedir=.*|includedir=$STAGING/include|" "$pc"
  done
fi

# libfmc + libfm (pre-built static libs + headers for dpa_app)
if [ -d "$PREBUILT/fmc" ]; then
  cp "$PREBUILT/fmc/libfmc.a" "$STAGING/lib/"
  cp "$PREBUILT/fmc/fmc.h" "$STAGING/include/"
fi
if [ -d "$PREBUILT/fmlib" ]; then
  cp "$PREBUILT/fmlib/libfm.a" "$STAGING/lib/"
  cp -a "$PREBUILT/fmlib/include/"* "$STAGING/include/" 2>/dev/null || true
fi

echo "    Staging populated: $(ls "$STAGING/lib/" | wc -l) libs, $(ls "$STAGING/include/" | wc -l) headers"

### ====================================================================
### Stage 1: Build libcli
### ====================================================================
echo ""
echo "### Stage 1: Building libcli"
LIBCLI_SRC="$REPO_ROOT/libcli"
if [ -d "$LIBCLI_SRC" ] && [ -f "$LIBCLI_SRC/Makefile" ]; then
  make -C "$LIBCLI_SRC" clean 2>/dev/null || true
  make -C "$LIBCLI_SRC" -j"$NPROC" \
    CC="$CC" AR="$AR" \
    CFLAGS="$COMMON_CFLAGS" \
    TESTS=0
  # Install to staging
  cp "$LIBCLI_SRC/libcli.so"* "$STAGING/lib/" 2>/dev/null || true
  cp "$LIBCLI_SRC/libcli.a" "$STAGING/lib/" 2>/dev/null || true
  cp "$LIBCLI_SRC/libcli.h" "$STAGING/include/"
  # Install to chroot
  cp "$LIBCLI_SRC/libcli.so.1.10.8" "$CHROOT/usr/local/lib/" 2>/dev/null || \
    cp "$LIBCLI_SRC/libcli.so"* "$CHROOT/usr/local/lib/" 2>/dev/null || true
  echo "    libcli built: $(ls -la "$LIBCLI_SRC/libcli.so"* 2>/dev/null | head -1)"
else
  echo "    WARNING: libcli source not found — using pre-built"
  cp "$PREBUILT/libcli/libcli.a" "$STAGING/lib/" 2>/dev/null || true
  cp "$PREBUILT/libcli/libcli.so"* "$STAGING/lib/" 2>/dev/null || true
  cp "$PREBUILT/libcli/libcli.h" "$STAGING/include/" 2>/dev/null || true
fi

### ====================================================================
### Stage 2: Build libfci
### ====================================================================
echo ""
echo "### Stage 2: Building libfci"
LIBFCI_SRC="$ASK_SRC/fci/lib"
if [ -d "$LIBFCI_SRC" ] && [ -f "$LIBFCI_SRC/configure.in" ]; then
  cd "$LIBFCI_SRC"
  # Regenerate autotools if src/Makefile.in is missing
  if [ ! -f "src/Makefile.in" ]; then
    echo "    Running autoreconf..."
    autoreconf -fi 2>&1 | tail -5 || true
  fi
  if [ -x "./configure" ]; then
    ./configure $HOST_TRIPLET CC="$CC" \
      CFLAGS="$COMMON_CFLAGS -fPIC -Wall" 2>&1 | tail -5
    make clean 2>/dev/null || true
    make -j"$NPROC" 2>&1 | tail -10
    # Install to staging
    cp src/.libs/libfci.so* "$STAGING/lib/" 2>/dev/null || \
      cp src/libfci.so* "$STAGING/lib/" 2>/dev/null || true
    cp src/.libs/libfci.a "$STAGING/lib/" 2>/dev/null || true
    cp include/libfci.h "$STAGING/include/"
    # Install to chroot
    if [ -f "src/.libs/libfci.so.0.1" ]; then
      cp "src/.libs/libfci.so.0.1" "$CHROOT/usr/local/lib/"
      echo "    libfci built: $(stat -c '%s bytes' src/.libs/libfci.so.0.1)"
    fi
  else
    echo "    WARNING: configure failed — using pre-built libfci"
    cp "$PREBUILT/fci/libfci.so"* "$STAGING/lib/" 2>/dev/null || true
    cp "$PREBUILT/fci/libfci.h" "$STAGING/include/" 2>/dev/null || true
  fi
  cd "$REPO_ROOT"
else
  echo "    WARNING: libfci source not found — using pre-built"
  cp "$PREBUILT/fci/libfci.so"* "$STAGING/lib/" 2>/dev/null || true
  cp "$PREBUILT/fci/libfci.h" "$STAGING/include/" 2>/dev/null || true
fi

### ====================================================================
### Stage 3: Build dpa_app
### ====================================================================
echo ""
echo "### Stage 3: Building dpa_app"
DPA_SRC="$ASK_SRC/dpa_app"
if [ -d "$DPA_SRC" ] && [ -f "$DPA_SRC/Makefile" ]; then
  # dpa_app needs: fmc.h, cdx_ioctl.h, libcli, libfmc.a, libfm.a, libxml2
  DPA_CFLAGS="$COMMON_CFLAGS -Wall"
  DPA_CFLAGS="$DPA_CFLAGS -I$STAGING/include"
  DPA_CFLAGS="$DPA_CFLAGS -I$ASK_SRC/cdx"
  DPA_CFLAGS="$DPA_CFLAGS $(pkg-config --cflags libxml-2.0 2>/dev/null || echo -I/usr/include/libxml2)"

  DPA_LDFLAGS="-L$STAGING/lib"
  DPA_LDFLAGS="$DPA_LDFLAGS -lpthread -lcli -lfmc -lfm"
  DPA_LDFLAGS="$DPA_LDFLAGS $(pkg-config --libs libxml-2.0 2>/dev/null || echo -lxml2)"
  DPA_LDFLAGS="$DPA_LDFLAGS -lm -lstdc++ -lcrypt"

  make -C "$DPA_SRC" clean 2>/dev/null || true
  make -C "$DPA_SRC" -j"$NPROC" \
    CC="$CC" \
    CFLAGS="$DPA_CFLAGS" \
    LDFLAGS="$DPA_LDFLAGS" 2>&1 | tail -10

  if [ -f "$DPA_SRC/dpa_app" ]; then
    cp "$DPA_SRC/dpa_app" "$CHROOT/usr/bin/dpa_app"
    chmod +x "$CHROOT/usr/bin/dpa_app"
    echo "    dpa_app built: $(stat -c '%s bytes' "$DPA_SRC/dpa_app")"
  else
    echo "    WARNING: dpa_app build failed — keeping pre-built"
  fi
else
  echo "    WARNING: dpa_app source not found"
fi

### ====================================================================
### Stage 4: Build cmm
### ====================================================================
echo ""
echo "### Stage 4: Building cmm"
CMM_SRC="$ASK_SRC/cmm"
if [ -d "$CMM_SRC" ] && [ -f "$CMM_SRC/configure.in" ]; then
  cd "$CMM_SRC"

  # Regenerate autotools if needed
  if [ ! -f "src/Makefile.in" ]; then
    echo "    Running autoreconf..."
    autoreconf -fi 2>&1 | tail -5 || true
  fi

  if [ -x "./configure" ]; then
    # CMM needs: libfci, libcli, libnetfilter_conntrack (NXP-patched), libpcap
    CMM_CFLAGS="$COMMON_CFLAGS -Wall"
    CMM_CFLAGS="$CMM_CFLAGS -Wno-address-of-packed-member -Wno-stringop-truncation"
    CMM_CFLAGS="$CMM_CFLAGS -Wno-use-after-free -Wno-unused-label"
    CMM_CFLAGS="$CMM_CFLAGS -I$STAGING/include"
    CMM_CFLAGS="$CMM_CFLAGS -I$ASK_SRC/fci/lib/include"

    CMM_LDFLAGS="-L$STAGING/lib"
    CMM_PKG="$STAGING/share/pkgconfig"

    ./configure $HOST_TRIPLET \
      CC="$CC" \
      CFLAGS="$CMM_CFLAGS" \
      LDFLAGS="$CMM_LDFLAGS" \
      PKG_CONFIG_PATH="$CMM_PKG" 2>&1 | tail -10

    make clean 2>/dev/null || true
    make -j"$NPROC" 2>&1 | tail -20

    if [ -f "src/cmm" ]; then
      cp "src/cmm" "$CHROOT/usr/bin/cmm"
      chmod +x "$CHROOT/usr/bin/cmm"
      echo "    cmm built: $(stat -c '%s bytes' src/cmm)"
    elif [ -f "src/.libs/cmm" ]; then
      cp "src/.libs/cmm" "$CHROOT/usr/bin/cmm"
      chmod +x "$CHROOT/usr/bin/cmm"
      echo "    cmm built: $(stat -c '%s bytes' src/.libs/cmm)"
    else
      echo "    WARNING: cmm build failed — keeping pre-built"
    fi

    # libcmm shared library
    if [ -f "src/.libs/libcmm.so.0.0.0" ]; then
      cp "src/.libs/libcmm.so.0.0.0" "$CHROOT/usr/local/lib/"
      echo "    libcmm built: $(stat -c '%s bytes' src/.libs/libcmm.so.0.0.0)"
    fi
  else
    echo "    WARNING: configure generation failed — keeping pre-built cmm"
  fi
  cd "$REPO_ROOT"
else
  echo "    WARNING: cmm source not found"
fi

### ====================================================================
### Cleanup
### ====================================================================
echo ""
echo "### ASK userspace build complete"
echo "    Binaries installed to $CHROOT/usr/bin/"
ls -la "$CHROOT/usr/bin/dpa_app" "$CHROOT/usr/bin/cmm" 2>/dev/null || true
echo "    Libraries in $CHROOT/usr/local/lib/"
ls -la "$CHROOT/usr/local/lib/libcli"* "$CHROOT/usr/local/lib/libfci"* "$CHROOT/usr/local/lib/libcmm"* 2>/dev/null || true

# Clean up staging
rm -rf "$STAGING"
