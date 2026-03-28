#!/bin/bash
# Compare DPAA constructors between our custom and VyOS stock dpdk_plugin.so
set -e

OUR_PLUGIN="/opt/vyos-dev/vpp/build-dpdk-static/lib/aarch64-linux-gnu/vpp_plugins/dpdk_plugin.so"

echo "=== OUR CUSTOM PLUGIN ==="
ls -la "$OUR_PLUGIN" 2>/dev/null || echo "NOT FOUND"
echo ""

echo "=== DPAA/FSLMC SYMBOLS IN OUR PLUGIN ==="
aarch64-linux-gnu-nm "$OUR_PLUGIN" 2>/dev/null | grep -iE 'dpaa|fslmc' | grep -i 'T \|D \|B \|R ' | head -60
echo ""

echo "=== RTE_INIT / CONSTRUCTOR REGISTRATIONS ==="
# DPDK uses RTE_INIT macro which creates __attribute__((constructor)) functions
# These show up as entries that call rte_*_register
aarch64-linux-gnu-nm "$OUR_PLUGIN" 2>/dev/null | grep -iE 'rte_.*_register|bus_register|driver_register' | head -30
echo ""

echo "=== DPAA BUS INIT FUNCTIONS ==="
aarch64-linux-gnu-nm "$OUR_PLUGIN" 2>/dev/null | grep -iE 'rte_dpaa_bus|dpaa_bus_init|dpaa_scan|rte_fslmc|fslmc_bus' | head -30
echo ""

echo "=== INIT_ARRAY SECTION ==="
aarch64-linux-gnu-readelf -S "$OUR_PLUGIN" 2>/dev/null | grep -i init_array
echo ""

echo "=== .init_array CONTENTS (constructor function pointers) ==="
# Dump init_array section offset and size
INIT_INFO=$(aarch64-linux-gnu-readelf -S "$OUR_PLUGIN" 2>/dev/null | grep '\.init_array' | head -1)
echo "Section info: $INIT_INFO"
# Count entries (each is 8 bytes on aarch64)
INIT_SIZE=$(echo "$INIT_INFO" | awk '{print $7}')
if [ -n "$INIT_SIZE" ]; then
    DECIMAL_SIZE=$((16#$INIT_SIZE))
    NUM_CTORS=$((DECIMAL_SIZE / 8))
    echo "Number of constructor entries: $NUM_CTORS"
fi
echo ""

echo "=== ALL rte_pci/rte_dpaa/rte_vdev BUS SYMBOLS ==="
aarch64-linux-gnu-nm "$OUR_PLUGIN" 2>/dev/null | grep -E ' [TtDdBb] .*rte_(pci|dpaa|fslmc|vdev|bus)' | head -40
echo ""

echo "=== DPAA PLATFORM DEVICE SCANNING SYMBOLS ==="
aarch64-linux-gnu-nm "$OUR_PLUGIN" 2>/dev/null | grep -iE 'rte_of_|fman_|fmc_|dpaa_create_|dpaa_clean|dpaa_portal|dpaa_eal' | head -40
echo ""

echo "=== KEY DPAA PMD INIT FUNCTIONS ==="  
aarch64-linux-gnu-nm "$OUR_PLUGIN" 2>/dev/null | grep -iE 'dpaa_dev_init|dpaa_dev_configure|dpaa_eth_dev|net_dpaa' | head -30
echo ""

echo "=== DONE ==="
