#!/bin/bash
set -euo pipefail

DPDK_SRC=/opt/vyos-dev/dpaa-pmd/src/dpdk
DPDK_INC=/opt/vyos-dev/dpaa-pmd/output/dpdk/include

echo "=== Copying missing DPDK internal headers for VPP ==="

# Copy EAL internal driver headers
cp -v $DPDK_SRC/lib/eal/include/bus_driver.h $DPDK_INC/
cp -v $DPDK_SRC/lib/eal/include/dev_driver.h $DPDK_INC/

# Copy bus driver headers from drivers/bus/
for dir in $DPDK_SRC/drivers/bus/*/; do
    for h in "$dir"*_driver.h; do
        [ -f "$h" ] && cp -v "$h" $DPDK_INC/
    done
done

# Copy PCI bus private header (VPP needs bus_pci_driver.h)
for h in $DPDK_SRC/drivers/bus/pci/*.h; do
    [ -f "$h" ] && cp -v "$h" $DPDK_INC/
done

echo
echo "=== VPP dpdk.h includes ==="
grep '#include' /opt/vyos-dev/vpp/src/plugins/dpdk/device/dpdk.h | head -30

echo
echo "=== Now retry build ==="
cd /opt/vyos-dev/vpp/build-dpdk-plugin
ninja -j12 dpdk_plugin 2>&1
