#!/bin/bash
set -euo pipefail

# Fix: Remove -mcpu from global CMAKE_C_FLAGS because VPP adds per-variant -march/-mcpu
# which conflicts. VPP cortexa72 variant already adds -mtune=cortex-a72.

echo "v25.10.0-48~vyos" > /opt/vyos-dev/vpp/src/scripts/.version

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
    "-DCMAKE_C_FLAGS=-O2" \
    -DVPP_USE_SYSTEM_DPDK=ON \
    -DCMAKE_BUILD_TYPE=Release \
    -DCMAKE_FIND_ROOT_PATH=/usr/aarch64-linux-gnu \
    -DCMAKE_FIND_ROOT_PATH_MODE_LIBRARY=BOTH \
    -DCMAKE_FIND_ROOT_PATH_MODE_INCLUDE=BOTH \
    2>&1

echo "=== Configure done, now building ==="
ninja -j12 dpdk_plugin 2>&1

echo "=== Build done ==="
find . -name "dpdk_plugin.so" -type f
