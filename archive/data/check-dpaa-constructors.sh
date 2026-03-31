#!/bin/bash
# Deep-dive into DPAA bus constructor behavior
set -e

DPDK_SRC="/opt/vyos-dev/dpaa-pmd/output/dpdk-24.11"

echo "=== DPAA BUS CONSTRUCTOR (RTE_REGISTER_BUS macro) ==="
grep -n "RTE_REGISTER_BUS\|RTE_BUS_REGISTER\|DRIVER_REGISTER_BUS" "$DPDK_SRC/drivers/bus/dpaa/dpaa_bus.c" 2>/dev/null | head -10
echo ""

echo "=== WHAT DPAA BUS INIT DOES ==="
grep -A 20 "static struct rte_dpaa_bus rte_dpaa_bus" "$DPDK_SRC/drivers/bus/dpaa/dpaa_bus.c" 2>/dev/null
echo ""

echo "=== DPAA BUS SCAN FUNCTION ==="
grep -B 2 -A 30 "rte_dpaa_bus_scan" "$DPDK_SRC/drivers/bus/dpaa/dpaa_bus.c" 2>/dev/null | head -50
echo ""

echo "=== FSLMC BUS CONSTRUCTOR ==="
grep -n "RTE_REGISTER_BUS\|RTE_BUS_REGISTER\|DRIVER_REGISTER_BUS" "$DPDK_SRC/drivers/bus/fslmc/fslmc_bus.c" 2>/dev/null | head -10
echo ""

echo "=== HOW OUR DPDK WAS BUILT (meson config) ==="
cat "$DPDK_SRC/build/meson-info/intro-buildoptions.json" 2>/dev/null | python3 -c "import json,sys; opts=json.load(sys.stdin); [print(f'{o[\"name\"]}={o[\"value\"]}') for o in opts if 'disable' in o['name'] or 'enable' in o['name'] or 'driver' in o['name']]" 2>/dev/null || echo "No meson-info"
echo ""

echo "=== DPDK BUILD meson.log disabled drivers check ==="
grep -i "disable_drivers\|enable_drivers" "$DPDK_SRC/build/meson-logs/meson-log.txt" 2>/dev/null | head -10 || echo "No meson-log"
echo ""

echo "=== DPDK build dir exists? ==="
ls -d "$DPDK_SRC/build" 2>/dev/null || echo "No build dir"
echo ""

echo "=== Check reconfigure.sh or meson_options ==="
cat /opt/vyos-dev/dpaa-pmd/output/dpdk-24.11/build/build.ninja 2>/dev/null | head -5 || echo "No build.ninja"
echo ""

echo "=== How we originally built DPDK ==="
cat /opt/vyos-dev/dpaa-pmd/build-dpdk.sh 2>/dev/null | head -40 || echo "No build script"
echo ""

echo "=== DPAA bus scan - does it access hardware? ==="
# Check if scan reads /sys or mmaps /dev/mem
grep -n "open\|mmap\|fopen\|sysfs\|ccsr\|/dev/" "$DPDK_SRC/drivers/bus/dpaa/dpaa_bus.c" 2>/dev/null | head -20
echo ""

echo "=== FMAN init - hardware access ==="
grep -n "open\|mmap\|fopen\|/dev/" "$DPDK_SRC/drivers/bus/dpaa/base/fman/fman.c" 2>/dev/null | head -20
echo ""

echo "=== rte_bus_register source ==="
grep -B 2 -A 15 "rte_bus_register" "$DPDK_SRC/lib/eal/common/eal_common_bus.c" 2>/dev/null | head -25
echo ""

echo "=== DONE ==="
