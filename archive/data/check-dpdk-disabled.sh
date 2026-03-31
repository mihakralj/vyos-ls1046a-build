#!/bin/bash
# Check VPP disabled DPDK drivers and compare static plugins
set -e

echo "=== FULL DISABLED DRIVERS LIST ==="
grep -A 100 "DPDK_DRIVERS_DISABLED" /opt/vyos-dev/vpp/build/external/packages/dpdk.mk | head -80
echo ""

echo "=== STATIC PLUGIN COPIES ==="
ls -la /tmp/dpdk_plugin.so.static /tmp/dpdk_plugin.so.static2 2>/dev/null || echo "Not found"
echo ""

echo "=== DPAA SYMBOLS IN /tmp/dpdk_plugin.so.static ==="
aarch64-linux-gnu-nm /tmp/dpdk_plugin.so.static 2>/dev/null | grep -iE 'dpaa|fslmc|businitfn' | head -20 || echo "No dpaa symbols or file not found"
echo ""

echo "=== DPAA SYMBOLS IN /tmp/dpdk_plugin.so.static2 ==="
aarch64-linux-gnu-nm /tmp/dpdk_plugin.so.static2 2>/dev/null | grep -iE 'dpaa|fslmc|businitfn' | head -20 || echo "No dpaa symbols or file not found"
echo ""

echo "=== ALL DPAA-RELATED DISABLED DRIVERS (from dpdk.mk) ==="
grep -i dpaa /opt/vyos-dev/vpp/build/external/packages/dpdk.mk 2>/dev/null || echo "None found"
echo ""

echo "=== NET DPAA DISABLED? ==="
grep -i 'net/dpaa' /opt/vyos-dev/vpp/build/external/packages/dpdk.mk 2>/dev/null || echo "net/dpaa not in dpdk.mk"
echo ""

echo "=== EVENT DPAA DISABLED? ==="
grep -i 'event/dpaa' /opt/vyos-dev/vpp/build/external/packages/dpdk.mk 2>/dev/null || echo "event/dpaa not in dpdk.mk"
echo ""

echo "=== DMA DPAA DISABLED? ==="
grep -i 'dma/dpaa' /opt/vyos-dev/vpp/build/external/packages/dpdk.mk 2>/dev/null || echo "dma/dpaa not in dpdk.mk"
echo ""

echo "=== COMMON DPAAX DISABLED? ==="
grep -i 'common/dpaax' /opt/vyos-dev/vpp/build/external/packages/dpdk.mk 2>/dev/null || echo "common/dpaax not in dpdk.mk"
echo ""

echo "=== DONE ==="
