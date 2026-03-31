#!/bin/bash
# test-vpp-dpaa.sh — VPP DPAA PMD test on Mono Gateway (kernel #26)
#
# This script:
# 1. Verifies kernel #26 (no-memunmap fix)
# 2. Stops VPP if running
# 3. Takes ALL kernel DPAA interfaces DOWN
# 4. Unbinds DPAA ETH interfaces from kernel driver
# 5. Starts VPP with DPAA PMD config
# 6. Checks VPP status and logs
#
# MUST be run as root via: sudo bash /tmp/test-vpp-dpaa.sh
# SSH will DROP when eth0 goes down — run via setsid/nohup
#
# After VPP exits, a REBOOT is MANDATORY.

set -euo pipefail
exec > >(tee /tmp/vpp-dpaa-test.log) 2>&1

echo "=== VPP DPAA PMD Test — $(date -u) ==="

# Step 0: Verify kernel version
KVER=$(uname -r)
KBUILD=$(uname -v | grep -oP '#\d+')
echo "Kernel: $KVER $KBUILD"
if [[ "$KBUILD" != "#26" ]]; then
    echo "WARNING: Expected kernel #26, got $KBUILD"
fi

# Step 1: Verify USDPAA devices exist
echo ""
echo "=== Step 1: Verify USDPAA devices ==="
for dev in /dev/fsl-usdpaa /dev/fsl_usdpaa; do
    if [ -c "$dev" ]; then
        echo "  Found: $dev"
        USDPAA_DEV="$dev"
    fi
done
if [ -z "${USDPAA_DEV:-}" ]; then
    echo "ERROR: No /dev/fsl-usdpaa or /dev/fsl_usdpaa found!"
    dmesg | grep -i usdpaa | tail -5
    exit 1
fi

# Create symlinks for both naming conventions
if [ -c /dev/fsl_usdpaa ] && [ ! -e /dev/fsl-usdpaa ]; then
    ln -sf /dev/fsl_usdpaa /dev/fsl-usdpaa
fi
if [ -c /dev/fsl-usdpaa ] && [ ! -e /dev/fsl_usdpaa ]; then
    ln -sf /dev/fsl-usdpaa /dev/fsl_usdpaa
fi
if [ -c /dev/fsl_usdpaa_irq ] && [ ! -e /dev/fsl-usdpaa-irq ]; then
    ln -sf /dev/fsl_usdpaa_irq /dev/fsl-usdpaa-irq
fi
if [ -c /dev/fsl-usdpaa-irq ] && [ ! -e /dev/fsl_usdpaa_irq ]; then
    ln -sf /dev/fsl-usdpaa-irq /dev/fsl_usdpaa_irq
fi

# Step 2: Verify hugepages
echo ""
echo "=== Step 2: Verify hugepages ==="
HP_TOTAL=$(cat /sys/kernel/mm/hugepages/hugepages-2048kB/nr_hugepages 2>/dev/null || echo 0)
HP_FREE=$(cat /sys/kernel/mm/hugepages/hugepages-2048kB/free_hugepages 2>/dev/null || echo 0)
echo "  Hugepages: ${HP_FREE}/${HP_TOTAL} free (2MB each)"
if [ "$HP_FREE" -lt 200 ]; then
    echo "ERROR: Need at least 200 free hugepages (have $HP_FREE)"
    exit 1
fi

# Step 3: Stop VPP if running
echo ""
echo "=== Step 3: Stop VPP ==="
systemctl stop vpp 2>/dev/null || true
sleep 1
if pgrep -x vpp_main >/dev/null 2>&1; then
    echo "  Killing leftover vpp_main..."
    killall -9 vpp_main 2>/dev/null || true
    sleep 1
fi
echo "  VPP stopped"

# Step 4: Take down ALL interfaces
echo ""
echo "=== Step 4: Taking down ALL kernel DPAA interfaces ==="
echo "WARNING: SSH will drop NOW"
for iface in eth0 eth1 eth2 eth3 eth4; do
    if ip link show "$iface" &>/dev/null; then
        state=$(cat "/sys/class/net/$iface/operstate" 2>/dev/null || echo "unknown")
        ip link set "$iface" down 2>/dev/null || true
        echo "  $iface: was $state -> DOWN"
    fi
done

# Step 5: Small delay for kernel driver quiesce
sleep 2

# Step 6: Write VPP startup config for DPAA PMD
echo ""
echo "=== Step 6: Writing VPP DPAA startup.conf ==="
cat > /tmp/vpp-dpaa-startup.conf << 'CONF'
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

logging {
  default-log-level debug
  default-syslog-log-level info
}
CONF
echo "  Config written to /tmp/vpp-dpaa-startup.conf"

# Step 7: Verify dpdk_plugin.so has DPAA symbols
echo ""
echo "=== Step 7: Verify DPAA plugin ==="
PLUGIN="/usr/lib/aarch64-linux-gnu/vpp_plugins/dpdk_plugin.so"
if [ -f "$PLUGIN" ]; then
    DPAA_SYMS=$(nm -D "$PLUGIN" 2>/dev/null | grep -ci dpaa || echo 0)
    echo "  Plugin: $PLUGIN ($(stat -c%s "$PLUGIN") bytes)"
    echo "  DPAA symbols: $DPAA_SYMS"
    if [ "$DPAA_SYMS" -lt 5 ]; then
        echo "WARNING: Few DPAA symbols — plugin may not have DPAA PMD"
    fi
else
    echo "ERROR: $PLUGIN not found!"
    exit 1
fi

# Step 8: Start VPP
echo ""
echo "=== Step 8: Starting VPP with DPAA PMD ==="
mkdir -p /var/log/vpp /run/vpp
echo "  Starting vpp_main..."

# Run VPP in background, capture output
/usr/bin/vpp_main -c /tmp/vpp-dpaa-startup.conf &
VPP_PID=$!
echo "  VPP PID: $VPP_PID"

# Wait for VPP to start (up to 30s)
echo "  Waiting for VPP to initialize..."
for i in $(seq 1 30); do
    if ! kill -0 $VPP_PID 2>/dev/null; then
        echo "  VPP DIED after ${i}s!"
        echo "  === VPP log ==="
        tail -100 /var/log/vpp/vpp.log 2>/dev/null || echo "  (no log)"
        echo "  === dmesg ==="
        dmesg | tail -30
        break
    fi
    if [ -S /run/vpp/cli.sock ]; then
        echo "  VPP ready after ${i}s (CLI socket exists)"
        break
    fi
    sleep 1
done

# Step 9: Query VPP status
echo ""
echo "=== Step 9: VPP status ==="
if [ -S /run/vpp/cli.sock ]; then
    echo "--- show version ---"
    vppctl show version 2>/dev/null || echo "  (failed)"
    echo ""
    echo "--- show interface ---"
    vppctl show interface 2>/dev/null || echo "  (failed)"
    echo ""
    echo "--- show dpdk buffer ---"
    vppctl show dpdk buffer 2>/dev/null || echo "  (not available)"
    echo ""
    echo "--- show dpdk physmem ---"
    vppctl show dpdk physmem 2>/dev/null || echo "  (not available)"
    echo ""
    echo "--- show plugins ---"
    vppctl show plugins 2>/dev/null | grep -i dpdk || echo "  (dpdk not found)"
    echo ""
    echo "--- show log ---"
    vppctl show log 2>/dev/null | grep -iE 'dpaa|dpdk|error|warn' | head -30 || echo "  (no relevant logs)"
else
    echo "  CLI socket not available"
fi

# Step 10: Dump VPP log
echo ""
echo "=== Step 10: VPP log (last 100 lines) ==="
tail -100 /var/log/vpp/vpp.log 2>/dev/null || echo "(no log)"

# Step 11: Check for kernel panics
echo ""
echo "=== Step 11: Kernel health ==="
if dmesg | grep -q "Kernel panic\|Unable to handle\|BUG:"; then
    echo "  !!! KERNEL ISSUE DETECTED !!!"
    dmesg | grep -A5 "Kernel panic\|Unable to handle\|BUG:" | head -20
else
    echo "  Kernel healthy — no panics, no BUGs"
fi

echo ""
echo "=== Test complete — $(date -u) ==="
echo "VPP PID: ${VPP_PID:-dead}"
echo "Log saved to: /tmp/vpp-dpaa-test.log"
echo ""
echo "To stop VPP and reboot: kill $VPP_PID; sleep 2; reboot"

# Keep script running so VPP stays alive
# VPP runs in background — script exits, VPP remains
