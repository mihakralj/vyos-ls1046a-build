#!/bin/bash
# test-vpp-dpaa-v9.sh — VPP DPAA PMD Test v9 (mempool ordering fix)
# Root cause #29: dpdk_dpaa_mempool_create() was called AFTER dpdk_lib_init()
# Fix: moved BEFORE dpdk_lib_init() so BMan pool exists during device setup
set -euo pipefail

LOG="/tmp/v9-output.log"
VPP_BIN="/usr/sbin/vpp"
VPP_CONF="/tmp/vpp-dpaa-v9.conf"
VPP_LOG="/var/log/vpp/v9-vpp-$(date +%Y%m%d-%H%M%S).log"
FULL_LOG="/var/log/vpp/v9-full-$(date +%Y%m%d-%H%M%S).log"
TIMEOUT=20

exec > >(tee -a "$FULL_LOG") 2>&1

echo "=== VPP DPAA PMD Test v9 (mempool ordering fix) ==="
echo "Date: $(date -u +%Y-%m-%dT%H:%M:%S) UTC"
echo "Kernel: $(uname -r)"
echo "Plugin: /usr/lib/aarch64-linux-gnu/vpp_plugins/dpdk_plugin.so"
echo ""

# --- 1. Prerequisites ---
echo "--- 1. Prerequisites ---"
echo "USDPAA devices:"
ls -la /dev/fsl-usdpaa* 2>/dev/null || echo "  WARN: No USDPAA devices"
echo "Plugin size: $(du -h /usr/lib/aarch64-linux-gnu/vpp_plugins/dpdk_plugin.so | cut -f1)"
echo "Plugin MD5: $(md5sum /usr/lib/aarch64-linux-gnu/vpp_plugins/dpdk_plugin.so | cut -d' ' -f1)"
echo "DPAA string count: $(grep -c 'dpaa' /usr/lib/aarch64-linux-gnu/vpp_plugins/dpdk_plugin.so 2>/dev/null || echo 0)"
if grep -qa 'dpaa_vpp_pool' /usr/lib/aarch64-linux-gnu/vpp_plugins/dpdk_plugin.so 2>/dev/null; then
    echo "✓ dpaa_vpp_pool found — BMan mempool patch applied"
else
    echo "✗ dpaa_vpp_pool NOT found — wrong plugin!"
fi
if grep -qa 'NXP DPAA1 FMan Mac' /usr/lib/aarch64-linux-gnu/vpp_plugins/dpdk_plugin.so 2>/dev/null; then
    echo "✓ DPAA1 driver description found — driver.c patch applied"
else
    echo "✗ DPAA1 driver description NOT found"
fi
echo "Hugepages: $(grep -c '' /sys/kernel/mm/hugepages/hugepages-2048kB/nr_hugepages 2>/dev/null && cat /sys/kernel/mm/hugepages/hugepages-2048kB/nr_hugepages) total, $(cat /sys/kernel/mm/hugepages/hugepages-2048kB/free_hugepages 2>/dev/null || echo '?') free (2MB)"
echo "CPU temperature: $(cat /sys/class/thermal/thermal_zone3/temp 2>/dev/null | awk '{printf "%.0f°C\n", $1/1000}' || echo 'unknown')"
echo ""

# --- 2. Device Tree FMan MACs ---
echo "--- 2. Device Tree FMan MACs ---"
for mac in /sys/devices/platform/soc/1a00000.fman/1a00000.fman:fman@0/*/; do
    basename "$mac" 2>/dev/null
done
echo ""

# --- 3. Interfaces before ---
echo "--- 3. Network interfaces (before unbind) ---"
ip -br link
echo ""

# --- 4. Stop VPP + unbind FMan MACs ---
echo "--- 4. Stop VPP + unbind all FMan MACs ---"
systemctl stop vpp 2>/dev/null || true
killall -9 vpp 2>/dev/null || true
sleep 1

UNBOUND=0
for dev in /sys/bus/platform/drivers/fsl_dpaa_mac/*/; do
    devname=$(basename "$dev")
    if [[ "$devname" == *.ethernet ]]; then
        echo -n "  Unbinding $devname... "
        echo "$devname" > /sys/bus/platform/drivers/fsl_dpaa_mac/unbind 2>/dev/null && echo "OK" || echo "FAIL"
        UNBOUND=$((UNBOUND + 1))
    fi
done
echo "Unbound $UNBOUND FMan MACs"
echo ""

# --- 4a. Interfaces after unbind ---
echo "--- 4a. Network interfaces (after unbind) ---"
ip -br link
echo ""

# --- 5. Write VPP config ---
echo "--- 5. VPP config ---"
cat > "$VPP_CONF" << 'VPPCONF'
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
VPPCONF
echo "Config written."
cat "$VPP_CONF"
echo ""

# --- 6. Start VPP ---
echo "--- 6. Start VPP ---"
mkdir -p /var/log/vpp /run/vpp
"${VPP_BIN}" -c "${VPP_CONF}" > "${VPP_LOG}" 2>&1 &
VPP_PID=$!
echo "VPP PID: $VPP_PID"

# Wait for VPP to either crash or start
sleep 5
echo ""

# --- 6a. Check if VPP is still alive ---
echo "--- 6a. VPP alive check at 5s ---"
if kill -0 "$VPP_PID" 2>/dev/null; then
    echo "✓ VPP is ALIVE (PID $VPP_PID)"
    
    # Wait a bit more for full init
    sleep 5
    echo ""
    echo "--- 6b. VPP alive check at 10s ---"
    if kill -0 "$VPP_PID" 2>/dev/null; then
        echo "✓ VPP still ALIVE at 10s"
    else
        wait "$VPP_PID" 2>/dev/null
        echo "✗ VPP died between 5-10s (exit: $?)"
    fi
else
    wait "$VPP_PID" 2>/dev/null
    echo "✗ VPP CRASHED within 5s (exit: $?)"
fi
echo ""

# --- 7. Collect VPP output ---
echo "--- 7. VPP log (last 60 lines) ---"
tail -60 "${VPP_LOG}" 2>/dev/null || echo "(no VPP log)"
echo ""

echo "--- 7a. VPP syslog entries ---"
grep -i 'vpp\|dpdk\|dpaa' /var/log/messages 2>/dev/null | tail -30 || true
echo ""

# --- 8. Try vppctl ---
echo "--- 8. vppctl show version ---"
vppctl show version 2>/dev/null || echo "(vppctl failed — VPP may be dead)"
echo ""
echo "--- 8a. vppctl show interface ---"
vppctl show interface 2>/dev/null || echo "(vppctl failed)"
echo ""
echo "--- 8b. vppctl show dpdk version ---"
vppctl show dpdk version 2>/dev/null || echo "(vppctl failed)"
echo ""
echo "--- 8c. vppctl show log ---"
vppctl show log 2>/dev/null | tail -30 || echo "(vppctl failed)"
echo ""

# --- 9. Cleanup ---
echo "--- 9. Cleanup ---"
kill "$VPP_PID" 2>/dev/null || true
sleep 2
kill -9 "$VPP_PID" 2>/dev/null || true
echo "VPP stopped."

# Copy important logs to /tmp for retrieval
cp "${VPP_LOG}" /tmp/v9-vpp.log 2>/dev/null || true
echo "Logs saved to: $FULL_LOG, $VPP_LOG, /tmp/v9-vpp.log"
echo ""

echo "--- 10. Reboot (10s delay) ---"
echo "Rebooting in 10s..."
sleep 10
reboot
