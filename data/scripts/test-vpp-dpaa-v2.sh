#!/bin/bash
# =============================================================================
# VPP DPAA PMD Test v2 — Unbind kernel drivers first
# =============================================================================
# Root cause #19: Kernel FMan driver initializes FQIDs on all MACs.
# DPDK DPAA PMD can't re-init FQs that aren't in OOS state.
# Fix: Unbind kernel dpaa_eth from target SFP+ interfaces BEFORE VPP starts,
# which runs qman_retire_fq → qman_oos_fq → qman_destroy_fq on those FQIDs.
#
# Run on Mono Gateway via serial console (NOT via SSH — interfaces will drop!)
# Usage: sudo bash /tmp/test-vpp-dpaa-v2.sh
# =============================================================================
set -euo pipefail

echo "========================================="
echo "VPP DPAA PMD Test v2 ($(date))"
echo "========================================="

# --- Step 0: Pre-flight checks ---
echo ""
echo "=== Step 0: Pre-flight ==="

if [ ! -c /dev/fsl-usdpaa ]; then
    echo "FATAL: /dev/fsl-usdpaa not found — USDPAA driver not loaded"
    exit 1
fi
echo "[OK] /dev/fsl-usdpaa exists"

if [ ! -c /dev/fsl-usdpaa-irq ]; then
    echo "FATAL: /dev/fsl-usdpaa-irq not found"
    exit 1
fi
echo "[OK] /dev/fsl-usdpaa-irq exists"

# Check hugepages
HUGE_FREE=$(grep HugePages_Free /proc/meminfo | awk '{print $2}')
if [ "${HUGE_FREE}" -lt 100 ]; then
    echo "FATAL: Only ${HUGE_FREE} free hugepages (need 256+)"
    exit 1
fi
echo "[OK] ${HUGE_FREE} free hugepages"

# Check reserved-memory
if [ -d /proc/device-tree/reserved-memory/usdpaa-mem@c0000000 ]; then
    echo "[OK] DT reserved-memory node present"
else
    echo "WARN: No usdpaa-mem reserved-memory in DTB (DMA_MAP may fail)"
fi

# Check VPP binary
if [ ! -x /usr/bin/vpp ]; then
    echo "FATAL: /usr/bin/vpp not found"
    exit 1
fi
echo "[OK] VPP binary exists"

# Check DPAA symbols in dpdk_plugin.so
DPAA_SYMS=$(nm -D /usr/lib/aarch64-linux-gnu/vpp_plugins/dpdk_plugin.so 2>/dev/null | grep -c dpaa || echo 0)
echo "[OK] dpdk_plugin.so has ${DPAA_SYMS} DPAA symbols"

# --- Step 1: Kill existing VPP ---
echo ""
echo "=== Step 1: Kill existing VPP ==="
if pgrep -x vpp >/dev/null 2>&1; then
    echo "Stopping existing VPP..."
    pkill -TERM vpp 2>/dev/null || true
    sleep 2
    pkill -9 vpp 2>/dev/null || true
    echo "[OK] VPP killed"
else
    echo "[OK] No VPP running"
fi

# --- Step 2: Check USDPAA driver init ---
echo ""
echo "=== Step 2: USDPAA driver status ==="
dmesg | grep -i 'fsl-usdpaa' || echo "No USDPAA messages in dmesg"
echo ""

# --- Step 3: Identify DPAA ethernet devices ---
echo ""
echo "=== Step 3: DPAA ethernet devices ==="

# List all DPAA ethernet platform devices
DPAA_ETH_DRIVER="/sys/bus/platform/drivers/fsl_dpaa_eth"
if [ -d "$DPAA_ETH_DRIVER" ]; then
    echo "DPAA eth driver sysfs: $DPAA_ETH_DRIVER"
    ls -la "$DPAA_ETH_DRIVER/" 2>/dev/null | grep -E 'dpaa|ethernet' || echo "(no bound devices listed)"
else
    echo "WARN: $DPAA_ETH_DRIVER not found"
    # Try alternative name
    DPAA_ETH_DRIVER="/sys/bus/platform/drivers/dpaa_eth"
    if [ -d "$DPAA_ETH_DRIVER" ]; then
        echo "Found at alternative path: $DPAA_ETH_DRIVER"
    else
        echo "Trying to find dpaa driver..."
        find /sys/bus/platform/drivers -name '*dpaa*' -type d 2>/dev/null
    fi
fi

# Show current interface → MAC mapping
echo ""
echo "Interface mapping:"
for eth in eth0 eth1 eth2 eth3 eth4; do
    if [ -d "/sys/class/net/$eth" ]; then
        DRIVER=$(readlink -f "/sys/class/net/$eth/device/driver" 2>/dev/null | xargs basename 2>/dev/null || echo "unknown")
        DEVPATH=$(readlink -f "/sys/class/net/$eth/device" 2>/dev/null | xargs basename 2>/dev/null || echo "unknown")
        echo "  $eth  driver=$DRIVER  device=$DEVPATH"
    else
        echo "  $eth  NOT PRESENT"
    fi
done

# --- Step 4: Unbind kernel driver from SFP+ ports (eth3/eth4) ---
echo ""
echo "=== Step 4: Unbind kernel driver from SFP+ ports ==="

# First bring interfaces down
for iface in eth3 eth4; do
    if ip link show "$iface" >/dev/null 2>&1; then
        echo "Bringing $iface down..."
        ip link set "$iface" down 2>/dev/null || true
    fi
done
sleep 1

# Find the sysfs device paths for eth3/eth4
for iface in eth3 eth4; do
    DEV_PATH=$(readlink -f "/sys/class/net/$iface/device" 2>/dev/null || echo "")
    if [ -n "$DEV_PATH" ] && [ -d "$DEV_PATH" ]; then
        DEV_NAME=$(basename "$DEV_PATH")
        DRIVER_PATH=$(readlink -f "$DEV_PATH/driver" 2>/dev/null || echo "")
        if [ -n "$DRIVER_PATH" ]; then
            DRIVER_NAME=$(basename "$DRIVER_PATH")
            UNBIND_PATH="$DRIVER_PATH/unbind"
            echo "Unbinding $iface ($DEV_NAME) from $DRIVER_NAME..."
            echo "$DEV_NAME" > "$UNBIND_PATH" 2>/dev/null && echo "  [OK] Unbound $iface" || echo "  [FAIL] Unbind $iface failed"
        else
            echo "$iface: no driver bound"
        fi
    else
        echo "$iface: device path not found (already unbound?)"
    fi
done

# Verify unbind
echo ""
echo "Post-unbind status:"
for iface in eth3 eth4; do
    if [ -d "/sys/class/net/$iface" ]; then
        echo "  WARNING: $iface still exists as network interface!"
    else
        echo "  [OK] $iface removed from network stack"
    fi
done

# --- Step 5: Check FQ states via dmesg ---
echo ""
echo "=== Step 5: Post-unbind FQ state ==="
dmesg | tail -30 | grep -iE 'dpaa|fman|qman|fq|retire|oos|destroy' || echo "(no relevant dmesg since unbind)"

# --- Step 6: Write VPP startup config ---
echo ""
echo "=== Step 6: Create VPP startup config ==="

mkdir -p /var/log/vpp /run/vpp /tmp

cat > /tmp/vpp-dpaa-v2.conf << 'VPPEOF'
unix {
  nodaemon
  log /var/log/vpp/vpp.log
  full-coredump
  cli-listen /run/vpp/cli.sock
  gid vpp
}

api-trace {
  on
}

api-segment {
  gid vpp
}

socksvr {
  default
}

cpu {
}

dpdk {
  no-pci
  poll-sleep-usec 100
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

echo "[OK] VPP config written to /tmp/vpp-dpaa-v2.conf"
echo "Config contents:"
cat /tmp/vpp-dpaa-v2.conf

# --- Step 7: Start VPP ---
echo ""
echo "=== Step 7: Start VPP ==="
echo "Starting VPP with DPAA PMD..."

# Clear VPP log
> /var/log/vpp/vpp.log 2>/dev/null || true

# Start VPP in background
/usr/bin/vpp -c /tmp/vpp-dpaa-v2.conf &
VPP_PID=$!
echo "VPP started with PID $VPP_PID"

# Wait for VPP to initialize
echo "Waiting 10s for VPP init..."
sleep 10

# --- Step 8: Check results ---
echo ""
echo "=== Step 8: Results ==="

if kill -0 $VPP_PID 2>/dev/null; then
    echo "[OK] VPP is running (PID $VPP_PID)"
else
    echo "[FAIL] VPP crashed or exited!"
    wait $VPP_PID 2>/dev/null
    echo "Exit code: $?"
fi

# Check VPP log for DPAA messages
echo ""
echo "--- VPP log (DPAA-related) ---"
grep -iE 'dpaa|dpdk|error|fail|EAL|portal|fqid|PMD|DMA' /var/log/vpp/vpp.log 2>/dev/null | head -40 || echo "(no log)"

# Check dmesg for kernel messages
echo ""
echo "--- dmesg (last 20 lines) ---"
dmesg | tail -20

# Try vppctl
echo ""
echo "--- VPP interfaces ---"
sleep 2
/usr/bin/vppctl show version 2>/dev/null || echo "(vppctl failed)"
echo ""
/usr/bin/vppctl show interface 2>/dev/null || echo "(vppctl failed)"
echo ""
/usr/bin/vppctl show dpdk version 2>/dev/null || echo "(no dpdk version)"

echo ""
echo "========================================="
echo "Test complete. VPP PID: $VPP_PID"
echo "To stop: sudo kill $VPP_PID"
echo "Full log: /var/log/vpp/vpp.log"
echo "========================================="
