#!/bin/bash
# test-vpp-dpaa-v8.sh — Test VPP with DPAA BMan mempool fix
#
# Changes from v7:
# - dpdk_plugin.so now has DPAA mempool support (root cause #27 fix)
# - net_dpaa in driver.c dpdk_drivers[] (root cause #28 fix)
# - DPAA devices get BMan hardware mempool instead of VPP mempool
# - VPP config matches v7 (no dev lines — DPAA auto-discovers from DT)
#
# Run as root on gateway. Captures all output to /var/log/vpp/v8-*.log
# WARNING: Network connectivity PERMANENTLY lost when MACs are unbound!
# Reboot required after test.

set -e

LOG_DIR="/var/log/vpp"
TIMESTAMP=$(date +%Y%m%d-%H%M%S)
FULL_LOG="${LOG_DIR}/v8-full-${TIMESTAMP}.log"
VPP_LOG="${LOG_DIR}/v8-vpp-${TIMESTAMP}.log"
VPP_BIN="/usr/bin/vpp"
VPP_CONF="/tmp/vpp-dpaa-v8.conf"
PLUGIN="/usr/lib/aarch64-linux-gnu/vpp_plugins/dpdk_plugin.so"
KILL_AT=10

mkdir -p "${LOG_DIR}"

exec > >(tee -a "${FULL_LOG}") 2>&1

echo "=== VPP DPAA PMD Test v8 (BMan mempool fix) ==="
echo "Date: $(date -u '+%Y-%m-%dT%H:%M:%S UTC')"
echo "Kernel: $(uname -r)"
echo "Plugin: ${PLUGIN}"
echo ""

# === 1. Verify prerequisites ===
echo "--- 1. Prerequisites ---"

# Check USDPAA devices
echo "USDPAA devices:"
ls -la /dev/fsl-usdpaa* /dev/fsl_usdpaa* 2>/dev/null || echo "  WARN: No USDPAA devices"

# Verify plugin has DPAA support
echo "Plugin size: $(du -h ${PLUGIN} | cut -f1)"
echo "Plugin MD5: $(md5sum ${PLUGIN} | cut -d' ' -f1)"
DPAA_SYMS=$(strings "${PLUGIN}" | grep -ci 'dpaa' || echo 0)
echo "DPAA string count: ${DPAA_SYMS}"

if [ "${DPAA_SYMS}" -lt 10 ]; then
    echo "ERROR: Plugin has too few DPAA strings — not the patched version!"
    exit 1
fi

# Check for v8-specific patches
if strings "${PLUGIN}" | grep -q 'dpaa_vpp_pool'; then
    echo "✓ dpaa_vpp_pool found — BMan mempool patch applied"
else
    echo "⚠ dpaa_vpp_pool NOT found — may be old plugin"
fi

if strings "${PLUGIN}" | grep -q 'NXP DPAA1 FMan Mac'; then
    echo "✓ DPAA1 driver description found — driver.c patch applied"
else
    echo "⚠ NXP DPAA1 driver description NOT found"
fi

# Hugepages
HP_FREE=$(cat /sys/kernel/mm/hugepages/hugepages-2048kB/free_hugepages 2>/dev/null || echo 0)
HP_TOTAL=$(cat /sys/kernel/mm/hugepages/hugepages-2048kB/nr_hugepages 2>/dev/null || echo 0)
echo "Hugepages: ${HP_TOTAL} total, ${HP_FREE} free (2MB)"

# Temperature
TEMP=$(cat /sys/class/thermal/thermal_zone3/temp 2>/dev/null || echo 0)
echo "CPU temperature: $((TEMP/1000))°C"

echo ""

# === 2. FMan DT discovery ===
echo "--- 2. Device Tree FMan MACs ---"
for child in /proc/device-tree/soc/fsl,dpaa/ethernet@*; do
    if [ -d "$child" ]; then
        name=$(basename "$child")
        echo "  ${name}"
    fi
done
echo ""

# === 3. Network state before unbind ===
echo "--- 3. Network interfaces (before unbind) ---"
ip -br link show 2>/dev/null || true
echo ""

# === 4. Stop VPP + unbind MACs ===
echo "--- 4. Stop VPP + unbind all FMan MACs ---"
systemctl stop vpp.service 2>/dev/null || true
pkill -9 vpp 2>/dev/null || true; sleep 1
rm -f /run/vpp/cli.sock /dev/shm/vpp-* /dev/shm/db_vpp_* 2>/dev/null || true

UNBIND_COUNT=0
for mac_dir in /sys/bus/platform/drivers/fsl_dpaa_mac/*/; do
    if [ -d "${mac_dir}" ]; then
        mac_name=$(basename "${mac_dir}")
        [[ "${mac_name}" == "module" || "${mac_name}" == "uevent" ]] && continue
        echo -n "  Unbinding ${mac_name}..."
        echo "${mac_name}" > /sys/bus/platform/drivers/fsl_dpaa_mac/unbind 2>/dev/null && {
            echo " OK"
            UNBIND_COUNT=$((UNBIND_COUNT + 1))
        } || echo " SKIP"
    fi
done
echo "Unbound ${UNBIND_COUNT} FMan MACs"
sleep 2

echo ""
echo "--- 4a. Network interfaces (after unbind) ---"
ip -br link show 2>/dev/null || echo "(all gone)"
echo ""

# === 5. Write VPP config ===
echo "--- 5. VPP config ---"

# Match v7's working config — NO dev lines, DPAA auto-discovers from DT
cat > "${VPP_CONF}" << 'VPPEOF'
unix {
  nodaemon
  log /var/log/vpp/vpp.log
  full-coredump
  cli-listen /run/vpp/cli.sock
  gid vpp
}
api-trace { on }
api-segment { gid vpp }
socksvr { default }
cpu { main-core 0 }
dpdk {
  no-pci
  no-multi-seg
}
buffers {
  buffers-per-numa 16384
  page-size default-hugepage
  default data-size 2048
}
statseg { size 32M }
plugins {
  plugin default { disable }
  plugin dpdk_plugin.so { enable }
}
VPPEOF

echo "Config written."
cat "${VPP_CONF}"
echo ""

# === 6. Run VPP ===
echo "--- 6. Start VPP ---"
truncate -s 0 /var/log/vpp/vpp.log 2>/dev/null || true

"${VPP_BIN}" -c "${VPP_CONF}" > "${VPP_LOG}" 2>&1 &
VPP_PID=$!
echo "VPP PID: ${VPP_PID}"

sleep ${KILL_AT}

echo ""
echo "--- 6a. VPP alive check at ${KILL_AT}s ---"
if kill -0 ${VPP_PID} 2>/dev/null; then
    echo "✅ VPP ALIVE at ${KILL_AT}s"

    echo ""
    echo "[vppctl show version]"
    timeout 3 vppctl show version 2>&1 || echo "(timeout/failed)"

    echo ""
    echo "[vppctl show plugins | grep dpdk]"
    timeout 3 vppctl show plugins 2>&1 | grep -i dpdk || echo "(none)"

    echo ""
    echo "[vppctl show interface]"
    timeout 3 vppctl show interface 2>&1 || echo "(timeout/failed)"

    echo ""
    echo "[vppctl show hardware]"
    timeout 3 vppctl show hardware 2>&1 || echo "(timeout/failed)"

    echo ""
    echo "[vppctl show dpdk version]"
    timeout 3 vppctl show dpdk version 2>&1 || echo "(timeout/failed)"

    echo ""
    echo "[vppctl show dpdk buffer]"
    timeout 3 vppctl show dpdk buffer 2>&1 || echo "(timeout/failed)"

    # Kill VPP cleanly
    echo ""
    echo "Stopping VPP..."
    kill ${VPP_PID} 2>/dev/null
    sleep 2
    kill -9 ${VPP_PID} 2>/dev/null || true
    wait ${VPP_PID} 2>/dev/null
    VPP_EXIT=$?
    echo "VPP stopped (exit: ${VPP_EXIT})"
else
    wait ${VPP_PID} 2>/dev/null
    VPP_EXIT=$?
    echo "❌ VPP DIED before ${KILL_AT}s (exit code: ${VPP_EXIT})"
fi

# === 7. Post-mortem ===
echo ""
echo "--- 7. Post-mortem ---"

echo ""
echo "[VPP stdout/stderr log — last 60 lines]"
tail -60 "${VPP_LOG}" 2>/dev/null || echo "(empty)"

echo ""
echo "[VPP runtime log — last 60 lines]"
tail -60 /var/log/vpp/vpp.log 2>/dev/null || echo "(empty)"

echo ""
echo "=== Analysis ==="

# Check for SEGV
if grep -qi 'SIGSEGV\|SIGABRT\|Segmentation\|signal 11' "${VPP_LOG}" /var/log/vpp/vpp.log 2>/dev/null; then
    echo "❌ CRASH DETECTED"
    grep -i 'SIGSEGV\|SIGABRT\|Segmentation\|signal 11' "${VPP_LOG}" /var/log/vpp/vpp.log 2>/dev/null | head -5
else
    echo "✓ No crash signals"
fi

# Check for DPAA mempool creation
if grep -qi 'dpaa.*mempool\|dpaa_vpp_pool\|BMan.*mempool\|BMan pool' "${VPP_LOG}" /var/log/vpp/vpp.log 2>/dev/null; then
    echo "✓ DPAA mempool messages found:"
    grep -i 'dpaa.*mempool\|dpaa_vpp_pool\|BMan.*mempool\|BMan pool' "${VPP_LOG}" /var/log/vpp/vpp.log 2>/dev/null | head -5
else
    echo "⚠ No DPAA mempool messages"
fi

# Check for DPAA device detection
if grep -qi 'net_dpaa\|dpaa_bus\|fm1-mac\|DPAA Bus Detected' "${VPP_LOG}" /var/log/vpp/vpp.log 2>/dev/null; then
    echo "✓ DPAA device messages found:"
    grep -i 'net_dpaa\|dpaa_bus\|fm1-mac\|DPAA Bus Detected' "${VPP_LOG}" /var/log/vpp/vpp.log 2>/dev/null | head -10
else
    echo "⚠ No DPAA device messages"
fi

# Check for unknown driver (should be gone with driver.c patch)
if grep -qi 'unknown driver.*net_dpaa' "${VPP_LOG}" /var/log/vpp/vpp.log 2>/dev/null; then
    echo "⚠ Still seeing 'unknown driver net_dpaa'"
else
    echo "✓ No 'unknown driver' for net_dpaa"
fi

echo ""
echo "=== Test v8 complete ==="
echo "Full log: ${FULL_LOG}"
echo "VPP log:  ${VPP_LOG}"
echo ""
echo "================================================================"
echo "DPAA hardware state is CORRUPTED — kernel networking is UNSAFE."
echo "Rebooting in 10 seconds..."
echo "================================================================"
sleep 10
reboot
