#!/bin/bash
# ci-build-dpdk-plugin.sh — Build DPDK 24.11 with DPAA1 PMD + VPP DPDK plugin
# Called by: .github/workflows/auto-build.yml "Build DPDK + VPP DPAA Plugin" step
# Expects: GITHUB_WORKSPACE set, VYOS_MIRROR in env
set -ex
cd "${GITHUB_WORKSPACE:-.}"

echo "### Step 1/3: Build DPDK 24.11 with DPAA1 PMD (native ARM64, static)"
DPDK_WORK=/tmp/dpdk-build
DPDK_OUTPUT=$DPDK_WORK/output
mkdir -p $DPDK_WORK $DPDK_OUTPUT

# Install DPDK build deps (meson/ninja may already exist in builder image)
# libfdt-dev: required by DPAA bus driver (bus/dpaa depends on libfdt)
apt-get update -qq
apt-get install -y --no-install-recommends \
  meson ninja-build pkg-config python3-pyelftools \
  libelf-dev libnuma-dev libfdt-dev 2>&1 | tail -5

# Clone DPDK v24.11 (shallow)
git clone --depth=1 -b v24.11 https://github.com/DPDK/dpdk.git $DPDK_WORK/dpdk

# Apply portal mmap patch (DPDK userspace must mmap portal windows via usdpaa fd)
cd $DPDK_WORK/dpdk
patch -p1 < "$GITHUB_WORKSPACE/data/dpdk-portal-mmap.patch" || true

# Meson setup — native ARM64, static build, DPAA enabled
export PKG_CONFIG_LIBDIR="/usr/lib/aarch64-linux-gnu/pkgconfig:/usr/share/pkgconfig"
# -Dmachine=generic: runner is Neoverse-N2 but target is Cortex-A72 (LS1046A)
# Without this, meson auto-detects -mcpu=neoverse-n2+sve2 which crashes on A72
meson setup build \
  --prefix="$DPDK_OUTPUT" \
  -Ddefault_library=static \
  -Dmax_lcores=4 \
  -Dmachine=generic \
  -Denable_kmods=false \
  -Denable_driver_sdk=true \
  -Dtests=false \
  -Dexamples= \
  -Ddisable_drivers="net/mlx*,net/bnxt,net/ixg*,net/ice,net/i40e,net/e1000,crypto/qat,net/virtio,net/vmxnet*,net/ena,net/hns*,net/hinic,net/octeontx*,net/cnxk,net/thunderx"
ninja -C build -j$(nproc)
ninja -C build install

# Verify DPAA PMD: check for individual librte_*dpaa*.a files
DPAA_LIBS=$(find "$DPDK_OUTPUT" -name 'librte_*dpaa*.a' -not -name '*dpaa2*' | wc -l)
DPAA2_LIBS=$(find "$DPDK_OUTPUT" -name 'librte_*dpaa2*.a' | wc -l)
echo "### DPDK built: $(du -sh $DPDK_OUTPUT | cut -f1), DPAA1 libs: $DPAA_LIBS, DPAA2 libs: $DPAA2_LIBS"

# Find actual libdpdk.a path (may be lib/ or lib/aarch64-linux-gnu/)
# Meson static builds may not create libdpdk.a — only individual librte_*.a files.
# If missing, create a GROUP linker script referencing all rte_ archives.
DPDK_LIB=$(find "$DPDK_OUTPUT" -name 'libdpdk.a' | head -1)
if [ -z "$DPDK_LIB" ]; then
  DPDK_LIBDIR=$(find "$DPDK_OUTPUT" -name 'librte_eal.a' -printf '%h\n' | head -1)
  if [ -n "$DPDK_LIBDIR" ]; then
    echo "### libdpdk.a not found, creating GROUP linker script in $DPDK_LIBDIR"
    LIBS=$(cd "$DPDK_LIBDIR" && ls librte_*.a | sed 's/^/-l:/' | tr '\n' ' ')
    echo "GROUP ( $LIBS )" > "$DPDK_LIBDIR/libdpdk.a"
    DPDK_LIB="$DPDK_LIBDIR/libdpdk.a"
  fi
fi
# Headers may be at include/dpdk/ or include/ depending on meson version
DPDK_INC=$(find "$DPDK_OUTPUT" -name 'rte_config.h' -type f -printf '%h\n' | head -1)
echo "DPDK_LIB=$DPDK_LIB"
echo "DPDK_INC=$DPDK_INC"
echo "DPDK_OUTPUT=$DPDK_OUTPUT"
echo "DPDK_LIB=$DPDK_LIB" >> "$GITHUB_ENV"
echo "DPDK_INC=$DPDK_INC" >> "$GITHUB_ENV"
echo "DPDK_OUTPUT=$DPDK_OUTPUT" >> "$GITHUB_ENV"
ls -la "$DPDK_LIB" 2>/dev/null || echo "WARNING: libdpdk.a not found"
head -1 "$DPDK_LIB" 2>/dev/null || true

# Return to workspace before cleanup (rm -rf while cwd is inside target = fatal)
cd "$GITHUB_WORKSPACE"
rm -rf $DPDK_WORK/dpdk

echo "### Step 2/3: Build VPP DPDK plugin with DPAA1 mempool patches"
VPP_WORK=/tmp/vpp-plugin-build
mkdir -p $VPP_WORK

# Clone VPP (shallow) — only need plugins/dpdk, vppinfra/linux, plugins/vxlan
git clone --depth=1 --filter=blob:none --sparse \
  https://github.com/FDIo/vpp.git $VPP_WORK/vpp 2>&1 | tail -5
cd $VPP_WORK/vpp
git sparse-checkout set src/plugins/dpdk src/vppinfra/linux src/plugins/vxlan
VPP_SRC=$VPP_WORK/vpp/src

# Apply DPAA mempool patches to VPP dpdk plugin source
DPDK_PLUGIN=$VPP_SRC/plugins/dpdk
DRIVER="$DPDK_PLUGIN/device/driver.c"
DPDK_H="$DPDK_PLUGIN/device/dpdk.h"
INIT="$DPDK_PLUGIN/device/init.c"
COMMON="$DPDK_PLUGIN/device/common.c"

# Patch 1: Add net_dpaa to driver.c
if ! grep -q '"net_dpaa"' "$DRIVER" 2>/dev/null; then
  awk '
    /net_dpaa2/ { in_dpaa2=1 }
    in_dpaa2 && /^  },/ {
      print
      print "  {"
      print "    .drivers = DPDK_DRIVERS ({ \"net_dpaa\", \"NXP DPAA1 FMan Mac\" }),"
      print "    .interface_name_prefix = \"TenGigabitEthernet\","
      print "  },"
      in_dpaa2=0
      next
    }
    { print }
  ' "$DRIVER" > "$DRIVER.tmp" && mv "$DRIVER.tmp" "$DRIVER"
fi

# Patch 2: Add IS_DPAA flag to dpdk.h
if ! grep -q 'IS_DPAA' "$DPDK_H" 2>/dev/null; then
  sed -i 's/_ (15, TX_PREPARE, "tx-prepare")/_ (15, TX_PREPARE, "tx-prepare")                                              \\/' "$DPDK_H"
  sed -i '/_ (15, TX_PREPARE, "tx-prepare")/a\  _ (2, IS_DPAA, "dpaa-device")' "$DPDK_H"
fi

# Patch 3: Add dpaa_mempool to dpdk_main_t
if ! grep -q 'dpaa_mempool' "$DPDK_H" 2>/dev/null; then
  sed -i '/^} dpdk_main_t;/i\\n  /* DPAA1 BMan hardware mempool for DPAA PMD devices */\n  struct rte_mempool *dpaa_mempool;' "$DPDK_H"
fi

# Patch 4: Add IS_DPAA detection in init.c
if ! grep -q 'IS_DPAA' "$INIT" 2>/dev/null; then
  sed -i "/dpdk_log_warn.*unknown driver.*driver_name/a\\
\\
          /* Mark DPAA1 devices for BMan mempool routing */\\
          if (di.driver_name \&\& strstr (di.driver_name, \"net_dpaa\") \&\&\\
              !strstr (di.driver_name, \"net_dpaa2\"))\\
            dpdk_device_flag_set (xd, DPDK_DEVICE_FLAG_IS_DPAA, 1);" "$INIT"
fi

# Patch 5: Add dpdk_dpaa_mempool_create() function + call
if ! grep -q 'dpaa_mempool_create' "$INIT" 2>/dev/null; then
  cat > /tmp/dpaa_mempool_func.c << 'CFUNC'

static void
dpdk_dpaa_mempool_create (dpdk_main_t *dm)
{
  dm->dpaa_mempool = rte_pktmbuf_pool_create_by_ops (
    "dpaa_vpp_pool", 4096, 256, 0, RTE_MBUF_DEFAULT_BUF_SIZE, 0, "dpaa");
  if (!dm->dpaa_mempool)
    dpdk_log_notice ("DPAA mempool not created: %s", rte_strerror (rte_errno));
  else
    dpdk_log_notice ("DPAA BMan mempool created: %u buffers", dm->dpaa_mempool->size);
}
CFUNC
  LAST_INCLUDE=$(grep -n '^#include' "$INIT" | tail -1 | cut -d: -f1)
  sed -i "${LAST_INCLUDE}r /tmp/dpaa_mempool_func.c" "$INIT"
  sed -i '/error = dpdk_lib_init (dm);/i\  dpdk_dpaa_mempool_create (dm);' "$INIT"
  rm -f /tmp/dpaa_mempool_func.c
fi

# Patch 6: Route DPAA devices to BMan mempool in common.c
if ! grep -q 'IS_DPAA' "$COMMON" 2>/dev/null; then
  sed -i '/^dpdk_device_setup (dpdk_device_t \* xd)/,/ASSERT/{
    /ASSERT/a\  dpdk_main_t *dm = \&dpdk_main;
  }' "$COMMON"
  sed -i 's|struct rte_mempool \*mp = dpdk_mempool_by_buffer_pool_index\[bpidx\];|/* Route DPAA devices to BMan hardware mempool */\n      struct rte_mempool *mp;\n      if ((xd->flags \& DPDK_DEVICE_FLAG_IS_DPAA) \&\& dm->dpaa_mempool)\n        mp = dm->dpaa_mempool;\n      else\n        mp = dpdk_mempool_by_buffer_pool_index[bpidx];|' "$COMMON"
fi

# Patch 7: Add rte_mbuf.h include
if ! grep -q 'rte_mbuf.h' "$INIT" 2>/dev/null; then
  sed -i '1,/^#include.*dpdk.h/{/^#include.*dpdk.h/a\#include <rte_mbuf.h>
}' "$INIT"
fi

# Remove cryptodev plugin — VPP HEAD uses APIs not in VyOS mirror headers
rm -rf "$DPDK_PLUGIN/cryptodev"
sed -i '/cryptodev\/cryptodev\.h/d' "$DPDK_PLUGIN/device/init.c"
sed -i '/dpdk_cryptodev/d' "$DPDK_PLUGIN/device/init.c"
sed -i '/add_subdirectory.*cryptodev/d' "$DPDK_PLUGIN/CMakeLists.txt" 2>/dev/null || true

echo "### VPP DPAA patches applied"

# Download vpp-dev headers from VyOS mirror
VPP_DEV_DIR=$VPP_WORK/vpp-dev
mkdir -p $VPP_DEV_DIR /tmp/vpp-debs
cd /tmp/vpp-debs

# Add VyOS repo temporarily to download vpp-dev
rm -f /etc/apt/sources.list.d/vyos*.list
echo "deb [trusted=yes] $VYOS_MIRROR current main" > /etc/apt/sources.list.d/vyos-tmp.list
apt-get update -qq 2>&1 | tail -3
apt-get download vpp-dev vpp libvppinfra libvppinfra-dev 2>&1 | tail -5
rm -f /etc/apt/sources.list.d/vyos-tmp.list

# Extract headers
for deb in *.deb; do
  dpkg-deb -x "$deb" "$VPP_DEV_DIR" 2>/dev/null || true
done
rm -f /tmp/vpp-debs/*.deb

# Remove upstream dpdk plugin headers (we use our DPAA-patched copy)
rm -rf "$VPP_DEV_DIR/usr/include/vpp_plugins/dpdk"

# Copy supplementary headers from VPP source
mkdir -p "$VPP_DEV_DIR/usr/include/vppinfra/linux"
cp "$VPP_SRC/vppinfra/linux/sysfs.c" "$VPP_DEV_DIR/usr/include/vppinfra/linux/" 2>/dev/null || true
mkdir -p "$VPP_DEV_DIR/usr/include/vpp_plugins/vxlan"
cp "$VPP_SRC/plugins/vxlan/vxlan.h" "$VPP_DEV_DIR/usr/include/vpp_plugins/vxlan/" 2>/dev/null || true
cp "$VPP_SRC/plugins/vxlan/vxlan_packet.h" "$VPP_DEV_DIR/usr/include/vpp_plugins/vxlan/" 2>/dev/null || true
cp "$VPP_SRC/plugins/vxlan/vxlan_error.def" "$VPP_DEV_DIR/usr/include/vpp_plugins/vxlan/" 2>/dev/null || true

# Set up out-of-tree cmake build using extracted CMakeLists.txt
BUILD_DIR=$VPP_WORK/build-oot
mkdir -p "$BUILD_DIR/plugins"
cp -a "$DPDK_PLUGIN" "$BUILD_DIR/plugins/dpdk"
cp "$GITHUB_WORKSPACE/data/cmake/CMakeLists.txt" "$BUILD_DIR/CMakeLists.txt"

# Find VPP cmake path
VPP_CMAKE=$(find "$VPP_DEV_DIR" -name 'VPPConfig.cmake' -printf '%h\n' | head -1)

mkdir -p "$BUILD_DIR/build" && cd "$BUILD_DIR/build"
# IMPORTANT: do NOT pipe cmake to tail — pipeline swallows non-zero
# exit codes with set -e (only tail's exit status is checked).
cmake .. \
  -G Ninja \
  -DCMAKE_C_FLAGS="-O2 -I$BUILD_DIR/plugins" \
  -DCMAKE_PREFIX_PATH="$VPP_CMAKE" \
  -DCMAKE_FIND_ROOT_PATH="$VPP_DEV_DIR/usr;$DPDK_OUTPUT" \
  -DVPP_INCLUDE_DIR="$VPP_DEV_DIR/usr/include" \
  -DVPP_APIGEN="$VPP_DEV_DIR/usr/bin/vppapigen" \
  -DDPDK_INCLUDE_DIR="$DPDK_INC" \
  -DDPDK_LIB="$DPDK_LIB" \
  -DCMAKE_SHARED_LINKER_FLAGS="-lz -latomic -lfdt -lnuma" \
  2>&1 || { echo "ERROR: cmake configuration failed"; exit 1; }
ninja -j$(nproc) 2>&1 || { echo "ERROR: ninja build failed"; exit 1; }

echo "### Step 3/3: Verify and deploy plugin"
PLUGIN=$(find "$BUILD_DIR" /vpp_plugins -name 'dpdk_plugin.so' 2>/dev/null | head -1)
if [ -z "$PLUGIN" ]; then
  echo "WARNING: dpdk_plugin.so not found — ISO will use upstream plugin"
else
  PLUGIN_SIZE=$(stat -c%s "$PLUGIN")
  DPAA_SYMS=$(nm -D "$PLUGIN" 2>/dev/null | grep -ci dpaa || true)
  PMD_INIT=$(nm "$PLUGIN" 2>/dev/null | grep -c 'dpaainitfn_net_dpaa' || true)
  echo "### Plugin: $(du -h $PLUGIN | cut -f1), DPAA symbols: $DPAA_SYMS, PMD constructor: $PMD_INIT"

  if [ "$PLUGIN_SIZE" -gt 5000000 ] && [ "$PMD_INIT" -ge 1 ]; then
    # Stage plugin inside includes.chroot at a temp path, then use a
    # chroot hook to move it into place AFTER vpp-plugin-dpdk deb installs.
    CHROOT=$GITHUB_WORKSPACE/vyos-build/data/live-build-config/includes.chroot
    HOOKS=$GITHUB_WORKSPACE/vyos-build/data/live-build-config/hooks/live
    mkdir -p "$CHROOT/opt"
    cp "$PLUGIN" "$CHROOT/opt/dpaa-dpdk-plugin.so"
    cp "$GITHUB_WORKSPACE/data/hooks/97-dpaa-dpdk-plugin.chroot" "$HOOKS/97-dpaa-dpdk-plugin.chroot"
    chmod +x "$HOOKS/97-dpaa-dpdk-plugin.chroot"
    echo "### DPAA-enabled dpdk_plugin.so staged to includes.chroot/opt + hook created"
  else
    echo "WARNING: Plugin too small or missing PMD constructor — keeping upstream"
  fi
fi

# Cleanup build artifacts to save disk
cd "$GITHUB_WORKSPACE"
rm -rf $DPDK_WORK $VPP_WORK
df -Th
