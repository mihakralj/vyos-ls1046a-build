#!/bin/bash
# =============================================================================
# VPP DPAA PMD Test v3 — Fixed config, handles missing eth3/eth4
# =============================================================================
# Root cause #19: Kernel FMan driver initializes FQIDs on all MACs.
# DPDK DPAA PMD can't re-init FQs that aren't in OOS state.
# Fix: Unbind kernel fsl_dpaa_mac from target SFP+ devices BEFORE VPP starts.
#
# IMPORTANT: If eth3/eth4 don't appear in 'show interfaces', the kernel driver
# may not have bound to them (SFP modules not inserted, or already unbound).
# DPDK discovers MACs via DT fsl,dpaa node, NOT via Linux netdevs.
# So missing netdevs actually means NO FQ conflict — DPDK can claim them clean.
#
# Run on Mono Gateway via serial console:
#   sudo bash /tmp/test-vpp-dpaa-v3.sh
#
# THERMAL WARNING: VPP poll-mode without workers. Monitor temperature.
#   Stop immediately if temp > 80°C: sudo kill $(pgrep vpp)
# =============================================================================
set -euo pipefail

echo "========================================="
echo "VPP DPAA PMD Test v3 ($(date))"
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
    echo "FATAL: Only ${HUGE_FREE} free hugepages (need 100+)"
    exit 1
fi
echo "[OK] ${HUGE_FREE} free hugepages"

# Check reserved-memory
if [ -d /proc/device-tree/reserved-memory/usdpaa-mem@c0000000 ]; then
    echo "[OK] DT reserved-memory node present (256MB CMA @ 0xc0000000)"
else
    echo "WARN: No usdpaa-mem reserved-memory in DTB (DMA_MAP may fail)"
fi

# Check DT fsl,dpaa node (this is what DPDK scans)
# Node is under /soc/ in the DT hierarchy
DPAA_NODE="/proc/device-tree/soc/fsl,dpaa"
if [ -d "$DPAA_NODE" ]; then
    echo "[OK] DT fsl,dpaa node exists"
    DPA_CHILDREN=$(find "$DPAA_NODE" -maxdepth 1 -name 'ethernet@*' -type d 2>/dev/null | wc -l)
    echo "     $DPA_CHILDREN DPA ethernet children found"
    for child in "$DPAA_NODE"/ethernet@*; do
        if [ -d "$child" ]; then
            name=$(basename "$child")
            fman_mac=$(cat "$child/fsl,fman-mac" 2>/dev/null | tr '\0' ' ' | strings 2>/dev/null | head -1 || echo "?")
            echo "     - $name -> fman-mac: $fman_mac"
        fi
    done
else
    echo "FATAL: No fsl,dpaa node in DT — DPDK cannot discover MACs"
    exit 1
fi

# Check VPP binary
if [ ! -x /usr/bin/vpp ]; then
    echo "FATAL: /usr/bin/vpp not found"
    exit 1
fi
echo "[OK] VPP binary exists"

# Check dpdk_plugin.so for DPAA symbols
PLUGIN="/usr/lib/aarch64-linux-gnu/vpp_plugins/dpdk_plugin.so"
if [ -f "$PLUGIN" ]; then
    DPAA_SYMS=$(nm -D "$PLUGIN" 2>/dev/null | grep -c dpaa || echo 0)
    echo "[OK] dpdk_plugin.so: ${DPAA_SYMS} DPAA symbols"
    if [ "$DPAA_SYMS" -lt 10 ]; then
        echo "WARN: Very few DPAA symbols — PMD may not be linked in!"
    fi
else
    echo "FATAL: $PLUGIN not found"
    exit 1
fi

# Temperature check
TEMP=$(cat /sys/class/thermal/thermal_zone3/temp 2>/dev/null || echo 0)
TEMP_C=$((TEMP / 1000))
echo "[OK] Core temperature: ${TEMP_C}°C"
if [ "$TEMP_C" -gt 75 ]; then
    echo "FATAL: Temperature too high (${TEMP_C}°C > 75°C). Cool down first."
    exit 1
fi

# --- Step 1: Kill existing VPP ---
echo ""
echo "=== Step 1: Kill existing VPP ==="
if pgrep -x vpp >/dev/null 2>&1; then
    echo "Stopping existing VPP..."
    pkill -TERM vpp 2>/dev/null || true
    sleep 2
    pkill -9 vpp 2>/dev/null || true
    sleep 1
    echo "[OK] VPP killed"
else
    echo "[OK] No VPP running"
fi

# --- Step 2: USDPAA driver status ---
echo ""
echo "=== Step 2: USDPAA driver status ==="
dmesg | grep -iE 'fsl-usdpaa|usdpaa|DMA pool' | tail -10 || echo "No USDPAA messages"

# --- Step 3: Check/unbind kernel driver from SFP+ MACs ---
echo ""
echo "=== Step 3: Unbind kernel driver from SFP+ ports ==="

# The SFP+ MACs are:
#   1af0000.ethernet = fm1-mac9  (eth3 when bound)
#   1af2000.ethernet = fm1-mac10 (eth4 when bound)
SFP_DEVS="1af0000.ethernet 1af2000.ethernet"

for dev in $SFP_DEVS; do
    DEV_SYSFS="/sys/bus/platform/devices/$dev"
    if [ ! -d "$DEV_SYSFS" ]; then
        echo "  $dev: platform device not found in sysfs"
        continue
    fi

    DRIVER_LINK=$(readlink "$DEV_SYSFS/driver" 2>/dev/null || echo "")
    if [ -z "$DRIVER_LINK" ]; then
        echo "  [OK] $dev: no driver bound (clean for DPDK)"
    else
        DRIVER_NAME=$(basename "$DRIVER_LINK")
        echo "  $dev: bound to '$DRIVER_NAME' — unbinding..."

        # Find the network interface this device creates
        for netdir in /sys/class/net/*/device; do
            if [ "$(readlink -f "$netdir" 2>/dev/null)" = "$(readlink -f "$DEV_SYSFS" 2>/dev/null)" ]; then
                IFACE=$(basename "$(dirname "$netdir")")
                echo "    Bringing $IFACE down first..."
                ip link set "$IFACE" down 2>/dev/null || true
            fi
        done

        # Unbind
        UNBIND_PATH="/sys/bus/platform/drivers/$DRIVER_NAME/unbind"
        if [ -f "$UNBIND_PATH" ]; then
            echo "$dev" > "$UNBIND_PATH" 2>/dev/null && \
                echo "  [OK] $dev unbound from $DRIVER_NAME" || \
                echo "  [FAIL] unbind $dev failed"
        else
            echo "  [WARN] No unbind path: $UNBIND_PATH"
        fi
    fi
done

sleep 2

# Also check the dpaa_eth driver (parent of the MAC netdev)
for iface in eth3 eth4; do
    if [ -d "/sys/class/net/$iface" ]; then
        echo "  WARNING: $iface still exists — attempting dpaa_eth unbind..."
        DEV_PATH=$(readlink -f "/sys/class/net/$iface/device" 2>/dev/null || echo "")
        if [ -n "$DEV_PATH" ]; then
            DEV_NAME=$(basename "$DEV_PATH")
            DRIVER_LINK=$(readlink "$DEV_PATH/driver" 2>/dev/null || echo "")
            if [ -n "$DRIVER_LINK" ]; then
                DRIVER_NAME=$(basename "$DRIVER_LINK")
                echo "$DEV_NAME" > "/sys/bus/platform/drivers/$DRIVER_NAME/unbind" 2>/dev/null || true
                echo "  Attempted unbind $iface ($DEV_NAME) from $DRIVER_NAME"
            fi
        fi
    else
        echo "  [OK] $iface not present (clean for DPDK)"
    fi
done

# Show post-unbind dmesg
echo ""
echo "--- Post-unbind dmesg (last 15 lines) ---"
dmesg | tail -15

# --- Step 4: Write VPP startup config ---
echo ""
echo "=== Step 4: Create VPP startup config ==="

mkdir -p /var/log/vpp /run/vpp

# NOTE: poll-sleep-usec is NOT a valid VPP startup.conf directive.
# It's a VyOS CLI concept. For thermal safety with DPAA PMD:
# - Use cpu { } with no workers (main thread only)
# - Monitor temperature externally
# NOTE: no-pci prevents PCI bus scan (no PCI on LS1046A)
# DPDK DPAA PMD uses dpaa_bus, not PCI — discovered via DT

cat > /tmp/vpp-dpaa-v3.conf << 'VPPEOF'
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

echo "[OK] VPP config written to /tmp/vpp-dpaa-v3.conf"

# --- Step 5: Start VPP ---
echo ""
echo "=== Step 5: Start VPP ==="
echo "THERMAL WARNING: Monitor temperature. Kill VPP if > 80°C"
echo ""

# Clear VPP log
truncate -s 0 /var/log/vpp/vpp.log 2>/dev/null || true

echo "Starting VPP with DPAA PMD..."
/usr/bin/vpp -c /tmp/vpp-dpaa-v3.conf &
VPP_PID=$!
echo "VPP started with PID $VPP_PID"

# Wait for VPP to initialize
echo "Waiting 15s for VPP init..."
for i in $(seq 1 15); do
    if ! kill -0 $VPP_PID 2>/dev/null; then
        echo "VPP DIED at second $i!"
        break
    fi
    # Print temperature every 5 seconds
    if [ $((i % 5)) -eq 0 ]; then
        T=$(cat /sys/class/thermal/thermal_zone3/temp 2>/dev/null || echo 0)
        echo "  [${i}s] Temp: $((T/1000))°C  VPP PID: $VPP_PID"
    fi
    sleep 1
done

# --- Step 6: Collect results ---
echo ""
echo "========================================="
echo "=== Step 6: Results ==="
echo "========================================="

# Check if VPP is still running
if kill -0 $VPP_PID 2>/dev/null; then
    echo "[OK] VPP is RUNNING (PID $VPP_PID)"
else
    echo "[FAIL] VPP CRASHED or exited!"
    wait $VPP_PID 2>/dev/null || true
    echo "Exit code: $?"
fi

# VPP log — full content (critical for diagnosis)
echo ""
echo "--- FULL VPP LOG ---"
cat /var/log/vpp/vpp.log 2>/dev/null || echo "(empty)"
echo "--- END VPP LOG ---"

# Check for DPAA-specific messages
echo ""
echo "--- DPAA-specific log lines ---"
grep -iE 'dpaa|fman|portal|fqid|EAL|dpdk|PMD|bus|probe|init' /var/log/vpp/vpp.log 2>/dev/null || echo "(none)"

# dmesg (kernel side)
echo ""
echo "--- dmesg (last 30 lines) ---"
dmesg | tail -30

# Try vppctl if VPP is running
if kill -0 $VPP_PID 2>/dev/null; then
    echo ""
    echo "--- vppctl show version ---"
    /usr/bin/vppctl show version 2>/dev/null || echo "(failed)"

    echo ""
    echo "--- vppctl show interface ---"
    /usr/bin/vppctl show interface 2>/dev/null || echo "(failed)"

    echo ""
    echo "--- vppctl show dpdk version ---"
    /usr/bin/vppctl show dpdk version 2>/dev/null || echo "(failed)"

    echo ""
    echo "--- vppctl show dpdk physmem ---"
    /usr/bin/vppctl show dpdk physmem 2>/dev/null || echo "(failed)"

    echo ""
    echo "--- vppctl show plugins ---"
    /usr/bin/vppctl show plugins 2>/dev/null | head -30 || echo "(failed)"

    echo ""
    echo "--- vppctl show log ---"
    /usr/bin/vppctl show log 2>/dev/null | tail -30 || echo "(failed)"
fi

# Final temperature
TEMP=$(cat /sys/class/thermal/thermal_zone3/temp 2>/dev/null || echo 0)
echo ""
echo "Final temperature: $((TEMP/1000))°C"

echo ""
echo "========================================="
echo "Test complete."
echo "VPP PID: $VPP_PID (if running)"
echo "To stop: sudo kill $VPP_PID"
echo "Full log: cat /var/log/vpp/vpp.log"
echo "========================================="
