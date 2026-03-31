#!/bin/bash
# test-vpp-dpaa-v7.sh — Safe DPAA PMD test with ALL MACs unbound
# 
# MUST run via serial console OR as a detached nohup:
#   nohup sudo bash /tmp/test-vpp-dpaa-v7.sh > /var/log/vpp/v7-full.log 2>&1 &
#
# WHY: This script unbinds ALL FMan MACs (including RJ45 carrying SSH).
#       SSH WILL DIE. Output must be local-only.
#
# KEY CHANGES from v6:
#   - Unbinds ALL 5 FMan MACs (not just SFP+) — prevents QMan FQ conflicts
#   - Checks running DT for U-Boot FMan fixup (expected: 5 dpa-ethernet-init)
#   - Kills VPP at 5s (fast) to capture output
#   - All output saved to /var/log/vpp/ (persistent storage, survives reboot)
#   - Also writes to serial console if running interactively
#
# AFTER: REBOOT REQUIRED. Retrieve results:
#   cat /var/log/vpp/v7-result.txt
#   cat /var/log/vpp/v7-stdout.log

set -uo pipefail

TIMEOUT=8
KILL_AT=5
VPP_BIN="/usr/bin/vpp"
VPP_CONF="/tmp/vpp-v7.conf"
VPP_LOG="/var/log/vpp/vpp.log"
VPP_OUT="/var/log/vpp/v7-stdout.log"
RESULT="/var/log/vpp/v7-result.txt"

# Ensure log dir exists
mkdir -p /var/log/vpp

# Save all output to persistent storage
exec > >(tee "$RESULT") 2>&1

echo "================================================================"
echo "  VPP DPAA PMD Test v7 — $(date -u '+%Y-%m-%d %H:%M:%S UTC')"
echo "================================================================"

# ── 1. Running Device Tree Analysis ──
echo ""
echo "--- 1. Running Device Tree (post U-Boot fixup) ---"

DT_DPAA="/proc/device-tree/soc/fsl,dpaa"
if [ ! -d "$DT_DPAA" ]; then
    echo "❌ FATAL: $DT_DPAA not found — no DPAA in DT"
    exit 1
fi

INIT_COUNT=0
DPA_ETH_COUNT=0
echo "fsl,dpaa children:"
for child in "$DT_DPAA"/ethernet@*; do
    [ -d "$child" ] || continue
    compat=$(cat "$child/compatible" 2>/dev/null | tr '\0' ' ')
    status=$(cat "$child/status" 2>/dev/null || echo "okay")
    name=$(basename "$child")
    
    if echo "$compat" | grep -q "dpa-ethernet-init"; then
        echo "  $name: dpa-ethernet-init [status=$status] ← DPDK PROBES"
        INIT_COUNT=$((INIT_COUNT + 1))
    elif echo "$compat" | grep -q "dpa-ethernet"; then
        echo "  $name: dpa-ethernet [status=$status] ← DPDK SKIPS"
        DPA_ETH_COUNT=$((DPA_ETH_COUNT + 1))
    fi
done

echo ""
echo "dpa-ethernet-init (DPDK probed): $INIT_COUNT"
echo "dpa-ethernet (DPDK skipped): $DPA_ETH_COUNT"

if [ "$INIT_COUNT" -gt 2 ]; then
    echo "⚠️  U-Boot FMan fixup CONFIRMED: $INIT_COUNT dpa-ethernet-init nodes"
fi

# ── 2. Pre-flight ──
echo ""
echo "--- 2. Pre-flight ---"
echo "Kernel: $(uname -r)"
PLUGIN="/usr/lib/aarch64-linux-gnu/vpp_plugins/dpdk_plugin.so"
[ -f "$PLUGIN" ] && echo "Plugin size: $(stat -c%s "$PLUGIN") bytes" || echo "❌ Plugin MISSING"
echo "/dev/mem: $(ls -la /dev/mem 2>&1)"
echo "/dev/fsl-usdpaa: $(ls -la /dev/fsl-usdpaa 2>&1)"
echo "Hugepages: $(grep HugePages_Free /proc/meminfo)"

echo ""
echo "Interfaces before unbind:"
ip -br link show

# ── 3. Unbind ALL FMan MACs ──
echo ""
echo "--- 3. Unbind ALL FMan MACs ---"
echo "⚠️  SSH WILL DIE AFTER THIS STEP"

ALL_MACS="1ae2000.ethernet 1ae8000.ethernet 1aea000.ethernet 1af0000.ethernet 1af2000.ethernet"

for dev in $ALL_MACS; do
    if [ -L /sys/bus/platform/devices/$dev/driver ]; then
        net_dir=/sys/bus/platform/devices/$dev/net
        iface=""
        if [ -d "$net_dir" ]; then
            iface=$(ls "$net_dir" 2>/dev/null | head -1)
            [ -n "$iface" ] && ip link set "$iface" down 2>/dev/null || true
        fi
        echo "$dev" > /sys/bus/platform/drivers/fsl_dpaa_mac/unbind 2>/dev/null && \
            echo "  $dev ($iface) → unbound ✓" || echo "  $dev → FAILED"
    else
        echo "  $dev → already unbound ✓"
    fi
done
sleep 2

echo ""
echo "Interfaces after unbind:"
ip -br link show

# ── 4. Clean + Config ──
echo ""
echo "--- 4. Clean VPP ---"
systemctl stop vpp.service 2>/dev/null || true
pkill -9 vpp 2>/dev/null || true; sleep 1
rm -f /run/vpp/cli.sock /dev/shm/vpp-* /dev/shm/db_vpp_* 2>/dev/null || true

cat > "$VPP_CONF" << 'VPPEOF'
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
echo "Config written. All MACs unbound. Starting VPP..."
truncate -s 0 "$VPP_LOG" 2>/dev/null || true

# ── 5. Run VPP ──
echo ""
echo "--- 5. Start VPP ---"
rm -f "$VPP_OUT"
"$VPP_BIN" -c "$VPP_CONF" > "$VPP_OUT" 2>&1 &
VPP_PID=$!
echo "VPP PID: $VPP_PID"

sleep "$KILL_AT"

echo ""
echo "--- 5a. VPP alive check at ${KILL_AT}s ---"
if kill -0 $VPP_PID 2>/dev/null; then
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
    echo "[vppctl show hardware-interfaces]"
    timeout 3 vppctl show hardware-interfaces 2>&1 || echo "(timeout/failed)"
    
    echo ""
    echo "[vppctl show log | tail 40]"
    timeout 3 vppctl show log 2>&1 | tail -40 || echo "(timeout/failed)"
else
    echo "❌ VPP DIED before ${KILL_AT}s"
    wait $VPP_PID 2>/dev/null
    echo "Exit: $?"
fi

# ── 6. Kill VPP ──
echo ""
echo "--- 6. Kill VPP ---"
kill $VPP_PID 2>/dev/null; sleep 1
kill -9 $VPP_PID 2>/dev/null
wait $VPP_PID 2>/dev/null
echo "VPP killed"

# ── 7. Post-mortem ──
echo ""
echo "================================================================"
echo "  POST-MORTEM"
echo "================================================================"

echo ""
echo "--- VPP STDOUT ($(wc -l < "$VPP_OUT" 2>/dev/null || echo 0) lines) ---"
cat "$VPP_OUT" 2>/dev/null || echo "(empty)"

echo ""
echo "--- KEY DPAA MESSAGES ---"
for pat in "DPAA Bus" "FMAN\|fman" "netcfg\|ethport" "fm1-mac" "QMAN\|BMAN\|qman\|bman" \
           "unable to probe\|probe.*fail" "EAL:" "DMA\|dma" "error\|fail\|unable\|WARN"; do
    label=$(echo "$pat" | sed 's/\\|.*//;s/[^a-zA-Z]//g')
    echo "[$label]"
    grep -iE "$pat" "$VPP_OUT" 2>/dev/null | head -15 || echo "  (none)"
done

echo ""
echo "--- VPP LOG (first 80 lines) ---"
head -80 "$VPP_LOG" 2>/dev/null || echo "(empty)"

echo ""
echo "--- dmesg DPAA ---"
dmesg | grep -iE 'dpaa|usdpaa|qman|bman|fman|fsl_usdpaa' | tail -40

echo ""
echo "================================================================"
echo "  SUMMARY"
echo "================================================================"
DPAA_BUS=$(grep -c "DPAA Bus Detected" "$VPP_OUT" 2>/dev/null || echo 0)
FM_FOUND=$(grep -c "fm1-mac" "$VPP_OUT" 2>/dev/null || echo 0)
PROBE_FAIL=$(grep -c "unable to probe" "$VPP_OUT" 2>/dev/null || echo 0)
PROBE_OK=$(grep -c "probed successfully" "$VPP_OUT" 2>/dev/null || echo 0)

echo "DPAA Bus Detected: $( [ "$DPAA_BUS" -gt 0 ] && echo '✅ YES' || echo '❌ NO')"
echo "FMan MACs found: $FM_FOUND"
echo "Probe failures: $PROBE_FAIL"
echo "Probe successes: $PROBE_OK"
echo ""
echo "⚠️  REBOOT REQUIRED. Retrieve logs:"
echo "    cat /var/log/vpp/v7-result.txt"
echo "    cat /var/log/vpp/v7-stdout.log"
echo ""
echo "Done: $(date -u '+%Y-%m-%d %H:%M:%S UTC')"
