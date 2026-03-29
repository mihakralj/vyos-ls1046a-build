#!/bin/bash
# =============================================================================
# VPP DPAA PMD Test v4 — Allowlist + Unbind
# =============================================================================
# Fixes from v3:
#   - DT path: /proc/device-tree/soc/fsl,dpaa (not root)
#   - DPDK allowlist: dev dpaa_bus:fm1-mac9/mac10 (skip RJ45 kernel MACs)
#   - Unbind fsl_dpaa_mac from SFP+ devices (release FQs)
#   - Daemon mode (not nodaemon) — doesn't block SSH
#
# Run on Mono Gateway: sudo bash /tmp/test-vpp-dpaa-v4.sh
# =============================================================================
set -euo pipefail

echo "========================================="
echo "VPP DPAA PMD Test v4 ($(date))"
echo "========================================="

# --- Step 0: Pre-flight ---
echo ""
echo "=== Step 0: Pre-flight ==="

# USDPAA chardevs
for dev in /dev/fsl-usdpaa /dev/fsl-usdpaa-irq; do
    [ -c "$dev" ] && echo "[OK] $dev" || { echo "FATAL: $dev missing"; exit 1; }
done

# Hugepages
HUGE_FREE=$(grep HugePages_Free /proc/meminfo | awk '{print $2}')
[ "$HUGE_FREE" -ge 100 ] && echo "[OK] $HUGE_FREE free hugepages" || { echo "FATAL: $HUGE_FREE hugepages (need 100+)"; exit 1; }

# DT fsl,dpaa under /soc/
DPAA_NODE="/proc/device-tree/soc/fsl,dpaa"
if [ -d "$DPAA_NODE" ]; then
    DPA_COUNT=$(find "$DPAA_NODE" -maxdepth 1 -name 'ethernet@*' -type d 2>/dev/null | wc -l)
    echo "[OK] DT fsl,dpaa: $DPA_COUNT ethernet children"
else
    echo "FATAL: No fsl,dpaa node at $DPAA_NODE"
    exit 1
fi

# Reserved memory
[ -d /proc/device-tree/reserved-memory/usdpaa-mem@c0000000 ] && echo "[OK] usdpaa-mem reserved (256MB CMA)" || echo "WARN: no usdpaa-mem"

# VPP binary
[ -x /usr/bin/vpp ] && echo "[OK] /usr/bin/vpp" || { echo "FATAL: no vpp"; exit 1; }

# DPAA symbols in plugin
PLUGIN="/usr/lib/aarch64-linux-gnu/vpp_plugins/dpdk_plugin.so"
DPAA_SYMS=$(nm -D "$PLUGIN" 2>/dev/null | grep -c dpaa || echo 0)
echo "[OK] dpdk_plugin.so: $DPAA_SYMS DPAA symbols"

# Temperature
TEMP=$(cat /sys/class/thermal/thermal_zone3/temp 2>/dev/null || echo 0)
TEMP_C=$((TEMP / 1000))
echo "[OK] Temperature: ${TEMP_C}°C"
[ "$TEMP_C" -gt 75 ] && { echo "FATAL: Too hot (${TEMP_C}°C)"; exit 1; }

# --- Step 1: Kill existing VPP ---
echo ""
echo "=== Step 1: Kill existing VPP ==="
if pgrep -x vpp >/dev/null 2>&1; then
    pkill -TERM vpp 2>/dev/null || true; sleep 2
    pkill -9 vpp 2>/dev/null || true; sleep 1
    echo "[OK] Killed existing VPP"
else
    echo "[OK] No VPP running"
fi

# --- Step 2: Unbind SFP+ MACs from kernel ---
echo ""
echo "=== Step 2: Unbind SFP+ ports from kernel ==="

# SFP+ platform devices (mac9=eth3, mac10=eth4)
SFP_DEVICES="1af0000.ethernet 1af2000.ethernet"

for plat_dev in $SFP_DEVICES; do
    SYSFS="/sys/bus/platform/devices/$plat_dev"
    if [ ! -d "$SYSFS" ]; then
        echo "  $plat_dev: not in sysfs (OK if not in DT)"
        continue
    fi

    DRV_LINK=$(readlink "$SYSFS/driver" 2>/dev/null || echo "")
    if [ -z "$DRV_LINK" ]; then
        echo "  [OK] $plat_dev: no driver (clean for DPDK)"
        continue
    fi

    DRV_NAME=$(basename "$DRV_LINK")
    echo "  $plat_dev: bound to '$DRV_NAME'"

    # Bring down any associated network interface
    for netdir in /sys/class/net/*/device; do
        REAL_DEV=$(readlink -f "$netdir" 2>/dev/null)
        REAL_PLAT=$(readlink -f "$SYSFS" 2>/dev/null)
        if [ "$REAL_DEV" = "$REAL_PLAT" ]; then
            IFACE=$(basename "$(dirname "$netdir")")
            echo "    Bringing $IFACE down..."
            ip link set "$IFACE" down 2>/dev/null || true
        fi
    done

    # Unbind from driver
    UNBIND="/sys/bus/platform/drivers/$DRV_NAME/unbind"
    if echo "$plat_dev" > "$UNBIND" 2>/dev/null; then
        echo "  [OK] Unbound $plat_dev from $DRV_NAME"
    else
        echo "  [FAIL] Could not unbind $plat_dev"
    fi
done

sleep 2

# Verify unbind
echo ""
echo "Post-unbind verification:"
for iface in eth3 eth4; do
    if [ -d "/sys/class/net/$iface" ]; then
        DRV=$(readlink "/sys/class/net/$iface/device/driver" 2>/dev/null | xargs basename 2>/dev/null || echo "?")
        echo "  WARNING: $iface still present (driver=$DRV)"
    else
        echo "  [OK] $iface removed from kernel"
    fi
done

# Show dmesg for unbind/FQ retire messages
echo ""
echo "--- dmesg (unbind-related, last 20 lines) ---"
dmesg | tail -20

# --- Step 3: Write VPP config with DPAA allowlist ---
echo ""
echo "=== Step 3: Write VPP config ==="

mkdir -p /var/log/vpp /run/vpp

# KEY: dev dpaa_bus:fm1-mac9 and fm1-mac10 = DPDK EAL allowlist
# This makes scan_mode=ALLOWLIST, probe_all=false
# Only mac9 and mac10 get probed (RJ45 macs skipped entirely)
cat > /tmp/vpp-dpaa-v4.conf << 'VPPEOF'
unix {
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

echo "[OK] Config written (with dpaa_bus allowlist for mac9/mac10)"
grep 'dev dpaa_bus' /tmp/vpp-dpaa-v4.conf

# --- Step 4: Start VPP (daemon mode) ---
echo ""
echo "=== Step 4: Start VPP ==="

truncate -s 0 /var/log/vpp/vpp.log 2>/dev/null || true

/usr/bin/vpp -c /tmp/vpp-dpaa-v4.conf &
VPP_PID=$!
echo "VPP started with PID $VPP_PID (daemon mode)"

# Wait for init
echo "Waiting 15s for DPAA PMD init..."
for i in $(seq 1 15); do
    if ! kill -0 $VPP_PID 2>/dev/null; then
        echo "VPP DIED at second $i!"
        break
    fi
    if [ $((i % 5)) -eq 0 ]; then
        T=$(cat /sys/class/thermal/thermal_zone3/temp 2>/dev/null || echo 0)
        echo "  [${i}s] VPP alive, temp: $((T/1000))°C"
    fi
    sleep 1
done

# --- Step 5: Results ---
echo ""
echo "========================================="
echo "=== Step 5: RESULTS ==="
echo "========================================="

# VPP status
if kill -0 $VPP_PID 2>/dev/null; then
    echo "[OK] VPP RUNNING (PID $VPP_PID)"
else
    echo "[FAIL] VPP CRASHED"
    wait $VPP_PID 2>/dev/null || true
    echo "Exit code: $?"
fi

# Full VPP log
echo ""
echo "--- FULL VPP LOG ---"
cat /var/log/vpp/vpp.log 2>/dev/null || echo "(empty)"
echo "--- END VPP LOG ---"

# DPAA-specific lines
echo ""
echo "--- DPAA/DPDK lines from log ---"
grep -iE 'dpaa|dpdk|EAL|portal|fqid|PMD|bus|probe|init|error|fail|DMA|allow' /var/log/vpp/vpp.log 2>/dev/null || echo "(none)"

# dmesg
echo ""
echo "--- dmesg (last 20) ---"
dmesg | tail -20

# vppctl (only if VPP running)
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
    echo "--- vppctl show log ---"
    /usr/bin/vppctl show log 2>/dev/null | grep -iE 'dpaa|dpdk|EAL|error|portal|probe|PMD|bus|DMA|mac' | head -30 || echo "(failed)"

    echo ""
    echo "--- vppctl show plugins (dpdk only) ---"
    /usr/bin/vppctl show plugins 2>/dev/null | grep -i dpdk || echo "(failed)"
fi

TEMP=$(cat /sys/class/thermal/thermal_zone3/temp 2>/dev/null || echo 0)
echo ""
echo "========================================="
echo "Final temp: $((TEMP/1000))°C | VPP PID: $VPP_PID"
echo "Stop: sudo kill $VPP_PID"
echo "========================================="
