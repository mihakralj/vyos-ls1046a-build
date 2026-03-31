#!/bin/bash
set -euo pipefail

# Fix VPP version - the version script looks for .version in its own directory
echo "v25.10.0-48~vyos" > /opt/vyos-dev/vpp/src/scripts/.version

# Clean and retry cmake
rm -rf /opt/vyos-dev/vpp/build-dpdk-plugin
mkdir -p /opt/vyos-dev/vpp/build-dpdk-plugin
cd /opt/vyos-dev/vpp/build-dpdk-plugin

PKG_CONFIG_PATH=/opt/vyos-dev/dpaa-pmd/output/dpdk/lib/pkgconfig \
PKG_CONFIG_LIBDIR=/opt/vyos-dev/dpaa-pmd/output/dpdk/lib/pkgconfig:/usr/lib/aarch64-linux-gnu/pkgconfig \
cmake ../src \
    -G Ninja \
    -DCMAKE_SYSTEM_NAME=Linux \
    -DCMAKE_SYSTEM_PROCESSOR=aarch64 \
    -DCMAKE_C_COMPILER=aarch64-linux-gnu-gcc \
    "-DCMAKE_C_FLAGS=-mcpu=cortex-a72+crc+crypto -O2" \
    -DVPP_USE_SYSTEM_DPDK=ON \
    -DCMAKE_BUILD_TYPE=Release \
    -DCMAKE_FIND_ROOT_PATH=/usr/aarch64-linux-gnu \
    -DCMAKE_FIND_ROOT_PATH_MODE_LIBRARY=BOTH \
    -DCMAKE_FIND_ROOT_PATH_MODE_INCLUDE=BOTH \
    2>&1

echo "=== Configure done ==="
