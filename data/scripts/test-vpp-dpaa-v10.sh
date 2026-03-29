#!/bin/bash
# test-vpp-dpaa-v10.sh — VPP DPAA PMD Test v10 (no unbind, USDPAA only)
#
# Root cause #30: v9 unbinds ALL FMan MACs including management port (eth0),
# killing SSH instantly. DPAA PMD should work via /dev/fsl-usdpaa without
# kernel unbind — DPDK dpaa_bus discovers devices through USDPAA chardev.
#
# Changes from v9:
# - NO fsl_dpaa_mac unbind (keeps eth0-eth2 alive for SSH)
# - Only bring eth3/eth4 DOWN (not unbind) so DPDK can claim them
# - Added DPAA-specific EAL args and dpaa bus config
# - Script does NOT reboot — safer for iterative debugging
set -euo pipefail

LOG="/tmp/v10-output.log"
VPP_BIN="/usr/bin/vpp"
VPP_CONF="/tmp/vpp-dpaa-v10.conf"
VPP_LOG="/var/log/vpp/v10-vpp-$(date +%Y%m%d-%H%M%S).log"
FULL_LOG="/var/log/vpp/v10-full-$(date +%Y%m%d-%H%M%S).log"
TIMEOUT=20

exec > >(tee -a "$FULL_LOG") 2>&1

echo "=== VPP DPAA PMD Test v10 (no unbind, USDPAA only) ==="
echo "Date: $(date -u +%Y-%m-%dT%H:%M:%S) UTC"
echo "Kernel: $(uname -r)"
echo ""

# --- 1. Prerequisites ---
echo "--- 1. Prerequisites ---"
echo "USDPAA devices:"
ls -la /dev/fsl-usdpaa* 2>/dev/null || echo "  CRITICAL: No USDPAA devices — kernel not patched"
echo "Plugin size: $(du -h /usr/lib/aarch64-linux-gnu/vpp_plugins/dpdk_plugin.so | cut -f1)"
echo "Plugin MD5: $(md5sum /usr/lib/aarch64-linux-gnu/vpp_plugins/dpdk_plugin.so | cut -d' ' -f1)"
echo "DPAA string count: $(grep -c 'dpaa' /usr/lib/aarch64-linux-gnu/vpp_plugins/dpdk_plugin.so 2>/dev/null || echo 0)"
if grep -qa 'dpaa_vpp_pool' /usr/lib/aarch64-linux-gnu/vpp_plugins/dpdk_plugin.so 2>/dev/null; then
    echo "✓ dpaa_vpp_pool found — BMan mempool patch applied"
else
    echo "✗ dpaa_vpp_pool NOT found — wrong plugin!"
fi
echo "Hugepages: $(cat /sys/kernel/mm/hugepages/hugepages-2048kB/nr_hugepages) total, $(cat /sys/kernel/mm/hugepages/hugepages-2048kB/free_hugepages) free (2MB)"
echo "CPU temperature: $(cat /sys/class/thermal/thermal_zone3/temp 2>/dev/null | awk '{printf "%.0f°C\n", $1/1000}' || echo 'unknown')"
echo ""

# --- 2. Interfaces before ---
echo "--- 2. Network interfaces (before) ---"
ip -br link
echo ""

# --- 3. Stop existing VPP ---
echo "--- 3. Stop existing VPP ---"
systemctl stop vpp 2>/dev/null || true
killall -9 vpp 2>/dev/null || true
sleep 1
echo "VPP stopped."

# --- 4. Bring SFP+ interfaces DOWN (do NOT unbind) ---
echo "--- 4. Bring eth3/eth4 DOWN (keep kernel driver, do NOT unbind) ---"
ip link set eth3 down 2>/dev/null && echo "  eth3 DOWN" || echo "  eth3: already down or missing"
ip link set eth4 down 2>/dev/null && echo "  eth4 DOWN" || echo "  eth4: already down or missing"
echo ""

# --- 5. Write VPP config (DPAA PMD via USDPAA) ---
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
  uio-driver auto
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
echo "Starting VPP at $(date -u +%H:%M:%S)..."
"${VPP_BIN}" -c "${VPP_CONF}" > "${VPP_LOG}" 2>&1 &
VPP_PID=$!
echo "VPP PID: $VPP_PID"

# Wait for VPP to either crash or start
echo "Waiting 5s for VPP init..."
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
        EXIT_CODE=$?
        echo "✗ VPP died between 5-10s (exit: $EXIT_CODE)"
    fi
else
    wait "$VPP_PID" 2>/dev/null
    EXIT_CODE=$?
    echo "✗ VPP CRASHED within 5s (exit: $EXIT_CODE)"
fi
echo ""

# --- 7. Collect VPP output ---
echo "--- 7. VPP log (last 80 lines) ---"
tail -80 "${VPP_LOG}" 2>/dev/null || echo "(no VPP log)"
echo ""

echo "--- 7a. dmesg DPAA/USDPAA entries ---"
dmesg | grep -iE 'dpaa|usdpaa|fsl_usdpaa|fman|bman|qman' | tail -20 || true
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
echo "--- 8c. vppctl show dpdk physmem ---"
vppctl show dpdk physmem 2>/dev/null || echo "(vppctl failed)"
echo ""
echo "--- 8d. vppctl show log ---"
vppctl show log 2>/dev/null | tail -40 || echo "(vppctl failed)"
echo ""

# --- 9. Cleanup ---
echo "--- 9. Cleanup ---"
kill "$VPP_PID" 2>/dev/null || true
sleep 2
kill -9 "$VPP_PID" 2>/dev/null || true
echo "VPP stopped."

# Bring SFP+ back up for kernel
ip link set eth3 up 2>/dev/null || true
ip link set eth4 up 2>/dev/null || true
echo "eth3/eth4 brought back UP."

# Copy important logs to /tmp for retrieval
cp "${VPP_LOG}" /tmp/v10-vpp.log 2>/dev/null || true
echo ""
echo "Logs saved to: $FULL_LOG, $VPP_LOG, /tmp/v10-vpp.log"
echo ""
echo "=== v10 test complete (NO REBOOT) ==="
echo "Retrieve logs with: ssh vyos 'cat /tmp/v10-vpp.log'"
