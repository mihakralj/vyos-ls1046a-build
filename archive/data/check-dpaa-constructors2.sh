#!/bin/bash
set -e
DPDK_SRC="/opt/vyos-dev/dpaa-pmd/src/dpdk"

echo "=== DPAA BUS CONSTRUCTOR (registration) ==="
grep -rn "RTE_REGISTER_BUS\|BUS_REGISTER\|bus_register" "$DPDK_SRC/drivers/bus/dpaa/" 2>/dev/null | head -10
echo ""

echo "=== rte_dpaa_bus struct definition ==="
grep -B2 -A20 "struct rte_bus rte_dpaa_bus" "$DPDK_SRC/drivers/bus/dpaa/dpaa_bus.c" 2>/dev/null
echo ""

echo "=== DPAA bus scan function (first 40 lines) ==="
grep -A 40 "^rte_dpaa_bus_scan\b\|rte_dpaa_bus_scan(" "$DPDK_SRC/drivers/bus/dpaa/dpaa_bus.c" 2>/dev/null | head -50
echo ""

echo "=== rte_bus_register implementation ==="
cat "$DPDK_SRC/lib/eal/common/eal_common_bus.c" 2>/dev/null | head -40
echo ""

echo "=== How was OUR DPDK built? ==="
cat /opt/vyos-dev/dpaa-pmd/build-dpdk.sh 2>/dev/null
echo ""

echo "=== Check DPAA bus scan for hardware access ==="
grep -n "open\|mmap\|fopen\|ccsr\|/dev/\|/sys/" "$DPDK_SRC/drivers/bus/dpaa/dpaa_bus.c" 2>/dev/null | head -20
echo ""

echo "=== FMan init function (hardware mmap) ==="
grep -n "open\|mmap\|/dev/\|/sys/" "$DPDK_SRC/drivers/bus/dpaa/base/fman/fman.c" 2>/dev/null | head -20
echo ""

echo "=== DONE ==="
