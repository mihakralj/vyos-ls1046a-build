#!/bin/bash
# test-vpp-dpaa-v5.sh — Definitive VPP DPAA PMD probe test
# Fixes ALL issues from v3 (DT path, no unbind) and v4 (stale process, thermal)
#
# Run: sudo bash /tmp/test-vpp-dpaa-v5.sh
#
# What this tests:
#   1. Clean kill of any existing VPP
#   2. Unbind eth3/eth4 (SFP+ 10G) from kernel fsl_dpaa_mac driver
#   3. Start VPP with DPAA PMD allowlist (fm1-mac9, fm1-mac10 only)
#   4. Capture ALL diagnostic output
#
# Expected outcome: DPAA PMD successfully probes fm1-mac9/mac10 after FQs
# are released by kernel driver unbind.

set -euo pipefail

LOG="/tmp/vpp-dpaa-v5.log"
VPP_CONF="/tmp/vpp-dpaa-v5.conf"
VPP_LOG="/var/log/vpp/vpp.log"
VPP_BIN="/usr/bin/vpp"
TIMEOUT_SEC=15

# Platform devices for SFP+ ports (10G)
UNBIND_DEVS="1af0000.ethernet 1af2000.ethernet"
DRIVER_PATH="/sys/bus/platform/drivers/fsl_dpaa_mac"

exec > >(tee "$LOG") 2>&1

echo "================================================================"
echo "  VPP DPAA PMD Test v5 — $(date -u '+%Y-%m-%d %H:%M:%S UTC')"
echo "================================================================"
echo ""

# ── Phase 1: Pre-flight checks ──────────────────────────────────────

echo "=== Phase 1: Pre-flight ==="

echo "[1.1] Kernel version:"
uname -r

echo ""
echo "[1.2] USDPAA chardevs:"
ls -la /dev/fsl-usdpaa /dev/fsl-usdpaa-irq 2>&1 || echo "MISSING — FATAL"

echo ""
echo "[1.3] BMan/QMan portals:"
ls /sys/bus/platform/drivers/bman_portal/ 2>/dev/null | head -5 || echo "no bman portals"
ls /sys/bus/platform/drivers/qman_portal/ 2>/dev/null | head -5 || echo "no qman portals"

echo ""
echo "[1.4] DPAA DT node (correct path: /proc/device-tree/soc/fsl,dpaa):"
if [ -d /proc/device-tree/soc/fsl,dpaa ]; then
    echo "  EXISTS — listing ethernet children:"
    for eth in /proc/device-tree/soc/fsl,dpaa/ethernet@*; do
        name=$(basename "$eth")
        cell_idx=$(hexdump -e '"%d"' "$eth/cell-index" 2>/dev/null || echo "?")
        echo "    $name  cell-index=$cell_idx"
    done
else
    echo "  NOT FOUND — checking alternatives:"
    find /proc/device-tree -maxdepth 3 -name "fsl,dpaa" -type d 2>/dev/null || echo "  no fsl,dpaa anywhere"
fi

echo ""
echo "[1.5] Current network interfaces:"
ip -br link show 2>/dev/null || ip link show

echo ""
echo "[1.6] Hugepages:"
grep HugePages /proc/meminfo

echo ""
echo "[1.7] Temperature:"
for tz in /sys/class/thermal/thermal_zone*/temp; do
    zone=$(basename "$(dirname "$tz")")
    type=$(cat "$(dirname "$tz")/type" 2>/dev/null || echo "?")
    temp=$(cat "$tz" 2>/dev/null || echo "?")
    echo "  $zone ($type): ${temp}m°C"
done

# ── Phase 2: Kill stale VPP processes ────────────────────────────────

echo ""
echo "=== Phase 2: Clean VPP state ==="

echo "[2.1] Stop VPP systemd service (if running):"
systemctl stop vpp.service 2>/dev/null && echo "  stopped" || echo "  not running"

echo "[2.2] Kill ALL VPP processes:"
for i in 1 2 3; do
    pkill -9 -f '/usr/bin/vpp' 2>/dev/null || true
    pkill -9 vpp 2>/dev/null || true
    sleep 1
done

# Verify no VPP running
if pgrep -a vpp 2>/dev/null; then
    echo "  FATAL: VPP still running after 3 kill attempts!"
    pgrep -a vpp
    exit 1
fi
echo "  All VPP processes killed ✓"

echo "[2.3] Clean stale resources:"
rm -f /run/vpp/cli.sock /dev/shm/vpp-* /dev/shm/db_vpp_* 2>/dev/null || true
echo "  Cleaned sockets and shared memory"

echo "[2.4] Hugepages after cleanup:"
grep HugePages /proc/meminfo

echo "[2.5] Clear VPP log:"
mkdir -p /var/log/vpp
truncate -s 0 "$VPP_LOG" 2>/dev/null || true

# ── Phase 3: Unbind SFP+ ports from kernel ──────────────────────────

echo ""
echo "=== Phase 3: Unbind SFP+ MACs from kernel driver ==="

echo "[3.1] Current driver bindings:"
for dev in $UNBIND_DEVS; do
    driver_link="/sys/bus/platform/devices/$dev/driver"
    if [ -L "$driver_link" ]; then
        drv=$(readlink "$driver_link" | xargs basename)
        echo "  $dev → driver: $drv"
    else
        echo "  $dev → NOT BOUND (already unbound?)"
    fi
done

echo ""
echo "[3.2] Unbinding devices:"
for dev in $UNBIND_DEVS; do
    driver_link="/sys/bus/platform/devices/$dev/driver"
    if [ -L "$driver_link" ]; then
        echo "  Unbinding $dev..."
        # Bring interface down first (reduces FQ teardown errors)
        # Find the net interface name for this device
        net_dir="/sys/bus/platform/devices/$dev/net"
        if [ -d "$net_dir" ]; then
            iface=$(ls "$net_dir" 2>/dev/null | head -1)
            if [ -n "$iface" ]; then
                echo "    ip link set $iface down"
                ip link set "$iface" down 2>/dev/null || true
                sleep 0.5
            fi
        fi
        echo "$dev" > "$DRIVER_PATH/unbind" 2>/dev/null && echo "    ✓ unbound" || echo "    ✗ FAILED"
    else
        echo "  $dev already unbound ✓"
    fi
done

# Wait for kernel to finish FQ teardown
echo ""
echo "[3.3] Waiting 2s for FQ teardown..."
sleep 2

echo "[3.4] Post-unbind driver state:"
for dev in $UNBIND_DEVS; do
    driver_link="/sys/bus/platform/devices/$dev/driver"
    if [ -L "$driver_link" ]; then
        drv=$(readlink "$driver_link" | xargs basename)
        echo "  $dev → STILL BOUND to $drv — UNEXPECTED"
    else
        echo "  $dev → unbound ✓"
    fi
done

echo ""
echo "[3.5] Post-unbind network interfaces:"
ip -br link show 2>/dev/null || ip link show

echo ""
echo "[3.6] dmesg tail (last 20 lines — look for FQ/qman messages):"
dmesg | tail -20

# ── Phase 4: Start VPP with DPAA PMD ────────────────────────────────

echo ""
echo "=== Phase 4: Start VPP with DPAA PMD ==="

echo "[4.1] Writing VPP config:"
cat > "$VPP_CONF" << 'VPPEOF'
unix {
  nodaemon
  log /var/log/vpp/vpp.log
  full-coredump
  cli-listen /run/vpp/cli.sock
  gid vpp
  startup-config /dev/null
}

api-trace { on }
api-segment { gid vpp }
socksvr { default }

cpu {
  main-core 0
}

dpdk {
  no-pci
  dev dpaa_bus:fm1-mac9
  dev dpaa_bus:fm1-mac10
}

buffers {
  buffers-per-numa 16384
  page-size default-hugepage
  default data-size 2048
}

statseg {
  size 32M
}
VPPEOF
cat "$VPP_CONF"

echo ""
echo "[4.2] Mark dmesg position:"
DMESG_BEFORE=$(dmesg | wc -l)

echo ""
echo "[4.3] Starting VPP (timeout ${TIMEOUT_SEC}s, nodaemon)..."
echo "────────────────── VPP stdout/stderr ──────────────────"

# Run VPP in foreground with timeout — captures ALL output
set +e
timeout "$TIMEOUT_SEC" "$VPP_BIN" -c "$VPP_CONF" 2>&1
VPP_EXIT=$?
set -e

echo "────────────────── end VPP output ─────────────────────"
echo ""
echo "[4.4] VPP exit code: $VPP_EXIT"
case $VPP_EXIT in
    0)   echo "  → Normal exit" ;;
    124) echo "  → Timeout (expected — VPP was still running after ${TIMEOUT_SEC}s) ← SUCCESS" ;;
    137) echo "  → SIGKILL (killed)" ;;
    139) echo "  → SEGFAULT" ;;
    *)   echo "  → Unexpected exit code" ;;
esac

# ── Phase 5: Post-mortem diagnostics ────────────────────────────────

echo ""
echo "=== Phase 5: Post-mortem ==="

echo "[5.1] VPP log file:"
echo "─────────────────────────────────────────────────────"
cat "$VPP_LOG" 2>/dev/null || echo "(empty or missing)"
echo "─────────────────────────────────────────────────────"

echo ""
echo "[5.2] New dmesg lines (USDPAA/DPAA activity):"
DMESG_AFTER=$(dmesg | wc -l)
DMESG_NEW=$((DMESG_AFTER - DMESG_BEFORE))
if [ "$DMESG_NEW" -gt 0 ]; then
    dmesg | tail -"$DMESG_NEW"
else
    echo "  (no new dmesg lines)"
fi

echo ""
echo "[5.3] Temperature after test:"
for tz in /sys/class/thermal/thermal_zone*/temp; do
    zone=$(basename "$(dirname "$tz")")
    type=$(cat "$(dirname "$tz")/type" 2>/dev/null || echo "?")
    temp=$(cat "$tz" 2>/dev/null || echo "?")
    echo "  $zone ($type): ${temp}m°C"
done

echo ""
echo "[5.4] Hugepages after test:"
grep HugePages /proc/meminfo

echo ""
echo "[5.5] Network interfaces after test:"
ip -br link show 2>/dev/null || ip link show

# ── Phase 6: Result summary ─────────────────────────────────────────

echo ""
echo "================================================================"
echo "  RESULT SUMMARY"
echo "================================================================"

# Check key indicators
DPAA_DETECTED=false
PROBE_SUCCESS=false
PROBE_FAIL=false
DMA_ERROR=false

if grep -q "DPAA Bus Detected" "$VPP_LOG" 2>/dev/null; then
    DPAA_DETECTED=true
    echo "  ✅ DPAA Bus Detected"
else
    echo "  ❌ DPAA Bus NOT Detected"
fi

if grep -q "dpaa_rx_queue_init.*failed" "$VPP_LOG" 2>/dev/null; then
    PROBE_FAIL=true
    echo "  ❌ DPAA PMD probe FAILED (FQ init error)"
    grep "dpaa_rx_queue_init.*failed" "$VPP_LOG" 2>/dev/null | head -5
fi

if grep -q "fm1-mac9" "$VPP_LOG" 2>/dev/null || grep -q "fm1-mac10" "$VPP_LOG" 2>/dev/null; then
    echo "  ℹ️  Target MACs referenced in log"
fi

if grep -q "Couldn't map new region for DMA" "$VPP_LOG" 2>/dev/null; then
    DMA_ERROR=true
    echo "  ⚠️  DMA mapping error"
fi

if grep -q "dpdk_plugin.so.*loaded" "$VPP_LOG" 2>/dev/null || grep -q "dpdk_plugin" "$VPP_LOG" 2>/dev/null; then
    echo "  ✅ DPDK plugin loaded"
fi

if [ "$VPP_EXIT" = "124" ]; then
    echo "  ✅ VPP survived full timeout (stayed alive)"
fi

if grep -qiE "unable to probe|probe failed|unable to init" "$VPP_LOG" 2>/dev/null; then
    echo "  ❌ Device probe errors in log"
    grep -iE "unable to probe|probe failed|unable to init" "$VPP_LOG" 2>/dev/null | head -5
fi

echo ""
echo "Full log saved to: $LOG"
echo "VPP log at: $VPP_LOG"
echo "Test completed at: $(date -u '+%Y-%m-%d %H:%M:%S UTC')"
