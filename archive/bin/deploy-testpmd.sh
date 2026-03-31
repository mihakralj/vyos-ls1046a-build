#!/bin/bash
# Create DPDK testpmd deployment tarball for Mono Gateway
# Run on LXC 200 after successful DPDK build
set -euo pipefail

WORKDIR="/opt/vyos-dev/dpaa-pmd"
DPDK_OUTPUT="${WORKDIR}/output/dpdk"
DEPLOY="${WORKDIR}/deploy"
TARBALL="/srv/tftp/dpdk-testpmd.tar.gz"

echo "=== Creating deployment package ==="

# Clean and create deploy directory
rm -rf "${DEPLOY}"
mkdir -p "${DEPLOY}/lib"

# Copy testpmd binary
cp "${DPDK_OUTPUT}/bin/dpdk-testpmd" "${DEPLOY}/"
echo "Copied testpmd: $(du -h ${DEPLOY}/dpdk-testpmd | cut -f1)"

# Copy ONLY dpaa-related PMD libs (not all 15MB of irrelevant drivers)
for lib in bus_dpaa net_dpaa mempool_dpaa common_dpaax event_dpaa dma_dpaa crypto_dpaa_sec; do
    src="${DPDK_OUTPUT}/lib/dpdk/pmds-25.0/librte_${lib}.so.25.0"
    if [ -f "${src}" ]; then
        cp "${src}" "${DEPLOY}/lib/"
        cd "${DEPLOY}/lib"
        ln -sf "librte_${lib}.so.25.0" "librte_${lib}.so.25"
        ln -sf "librte_${lib}.so.25" "librte_${lib}.so"
        echo "  Added: librte_${lib}.so.25.0 ($(du -h librte_${lib}.so.25.0 | cut -f1))"
        cd "${WORKDIR}"
    else
        echo "  MISSING: ${src}"
    fi
done

# Copy libatomic (not present on VyOS minimal install)
LIBATOMIC="/usr/aarch64-linux-gnu/lib/libatomic.so.1.2.0"
if [ -f "${LIBATOMIC}" ]; then
    cp "${LIBATOMIC}" "${DEPLOY}/lib/libatomic.so.1"
    echo "  Added: libatomic.so.1 ($(du -h ${DEPLOY}/lib/libatomic.so.1 | cut -f1))"
else
    echo "  WARNING: libatomic not found at ${LIBATOMIC}"
fi

# Create the run script
cat > "${DEPLOY}/run-testpmd.sh" << 'RUNEOF'
#!/bin/bash
# DPDK testpmd smoke test for Mono Gateway DPAA1 PMD
# Usage: ./run-testpmd.sh
# Run as root on the gateway after TFTP boot with USDPAA kernel
set -e
DIR=$(dirname $(readlink -f $0))

echo "=== DPDK testpmd DPAA1 PMD Smoke Test ==="
echo "Date: $(date)"
echo "Kernel: $(uname -r)"

# Check USDPAA device
if [ ! -c /dev/fsl-usdpaa ]; then
    echo "ERROR: /dev/fsl-usdpaa not found — USDPAA kernel module not loaded"
    exit 1
fi
echo "USDPAA: $(ls -la /dev/fsl-usdpaa)"

# Setup hugepages if not already done
HP_CUR=$(cat /sys/kernel/mm/hugepages/hugepages-2048kB/nr_hugepages 2>/dev/null || echo 0)
if [ "${HP_CUR}" -eq 0 ]; then
    echo "Setting up 256 x 2MB hugepages..."
    echo 256 > /sys/kernel/mm/hugepages/hugepages-2048kB/nr_hugepages
    HP_CUR=$(cat /sys/kernel/mm/hugepages/hugepages-2048kB/nr_hugepages)
fi
echo "Hugepages: ${HP_CUR} x 2MB"

# Mount hugetlbfs if needed
if ! mount | grep -q hugetlbfs; then
    mkdir -p /dev/hugepages
    mount -t hugetlbfs nodev /dev/hugepages
    echo "Mounted hugetlbfs at /dev/hugepages"
fi

# Show network interfaces
echo ""
echo "=== Network Interfaces ==="
ip -br link show 2>/dev/null || true

echo ""
echo "=== Starting testpmd ==="
echo "Loading DPAA PMDs: bus_dpaa, mempool_dpaa, net_dpaa"
echo "Using cores 0-1, no PCI scan"
echo ""

# Run testpmd with DPAA bus
# -d: explicitly load DPAA PMD shared libraries
# --no-pci: skip PCI scan (no PCIe devices on this board)
# -l 0-1: use CPU cores 0 and 1
# -n 1: 1 memory channel
# --log-level=8: debug logging for first run
# --port-topology=chained: chain ports for forwarding
# -i: interactive mode
export LD_LIBRARY_PATH="${DIR}/lib:${LD_LIBRARY_PATH:-}"
exec ${DIR}/dpdk-testpmd \
    -d ${DIR}/lib/librte_common_dpaax.so \
    -d ${DIR}/lib/librte_bus_dpaa.so \
    -d ${DIR}/lib/librte_mempool_dpaa.so \
    -d ${DIR}/lib/librte_net_dpaa.so \
    --no-pci \
    -l 0-1 \
    -n 1 \
    --log-level=8 \
    -- \
    --port-topology=chained \
    -i
RUNEOF
chmod +x "${DEPLOY}/run-testpmd.sh"

# Create tarball
cd "${WORKDIR}"
tar czf "${TARBALL}" -C deploy .

echo ""
echo "=== Deployment Package ==="
echo "Contents:"
find "${DEPLOY}" -type f -exec du -h {} \;
echo ""
echo "Tarball: ${TARBALL} ($(du -h ${TARBALL} | cut -f1))"
echo ""
echo "=== Deploy to gateway ==="
echo "1. On gateway serial: ip addr add 192.168.1.200/24 dev eth0"
echo "2. From LXC 200:      scp ${TARBALL} vyos@192.168.1.200:/tmp/"
echo "3. On gateway serial: cd /tmp && tar xzf dpdk-testpmd.tar.gz && ./run-testpmd.sh"
