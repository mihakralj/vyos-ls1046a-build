#!/bin/bash
# test-vpp-dpaa-v6.sh — THE definitive DPAA PMD test
# Run on gateway: sudo bash /tmp/test-vpp-dpaa-v6.sh 2>&1 | tee /tmp/v6-full.log
#
# This script:
#   1. Verifies dpdk_plugin.so has DPAA symbols (636+ symbols = build-dpdk-static)
#   2. Verifies /dev/mem, /dev/fsl-usdpaa access
#   3. Kills any VPP, unbinds SFP+ MACs
#   4. Runs VPP in foreground with timeout (captures ALL stdout)
#   5. Analyzes output for DPAA bus init messages
#
# IMPORTANT: After this test, REBOOT to restore eth3/eth4

set -uo pipefail

TIMEOUT=20
VPP_BIN="/usr/bin/vpp"
VPP_CONF="/tmp/vpp-v6.conf"
VPP_LOG="/var/log/vpp/vpp.log"
VPP_OUT="/tmp/vpp-v6-stdout.log"
PLUGIN="/usr/lib/aarch64-linux-gnu/vpp_plugins/dpdk_plugin.so"

echo "================================================================"
echo "  VPP DPAA PMD Test v6 — $(date -u '+%Y-%m-%d %H:%M:%S UTC')"
echo "================================================================"

# ── 1. Verify Plugin ──
echo ""
echo "--- 1. Verify dpdk_plugin.so ---"
if [ ! -f "$PLUGIN" ]; then
    echo "FATAL: $PLUGIN not found"
    exit 1
fi

PLUGIN_SIZE=$(stat -c%s "$PLUGIN" 2>/dev/null || echo 0)
echo "Plugin size: $PLUGIN_SIZE bytes ($(( PLUGIN_SIZE / 1024 / 1024 )) MB)"

if [ "$PLUGIN_SIZE" -lt 10000000 ]; then
    echo "⚠️  WARNING: Plugin < 10MB — likely NOT build-dpdk-static (needs 15.9MB)"
    echo "  build-dpdk-static = 15.9MB (637 DPAA symbols) ✓"
    echo "  build-dpdk-plugin = 4.1MB  (0 DPAA symbols)   ✗"
    echo "  build-no-dpaa     = 12.2MB (0 DPAA symbols)   ✗"
fi

# Check for DPAA bus symbol
DPAA_SYMS=$(nm -D "$PLUGIN" 2>/dev/null | grep -c dpaa || echo 0)
echo "DPAA dynamic symbols: $DPAA_SYMS"

if [ "$DPAA_SYMS" -lt 10 ]; then
    echo ""
    echo "❌ FATAL: dpdk_plugin.so has ZERO/few DPAA symbols!"
    echo "   This is the WRONG plugin. Deploy build-dpdk-static."
    echo ""
    echo "   Fix: From LXC 200, run:"
    echo "   scp /opt/vyos-dev/vpp/build-dpdk-static/lib/aarch64-linux-gnu/vpp_plugins/dpdk_plugin.so vyos@<gateway>:/tmp/"
    echo "   Then on gateway: sudo cp /tmp/dpdk_plugin.so $PLUGIN"
    echo ""
    echo "   Checking nm for any dpaa reference (static symbols)..."
    nm "$PLUGIN" 2>/dev/null | grep -ci dpaa || echo "  also 0 static symbols"
    # Don't exit — still try the test, maybe symbols are there but nm -D doesn't show
fi

# ── 2. Pre-flight ──
echo ""
echo "--- 2. Pre-flight ---"
echo "Kernel: $(uname -r)"
echo "Architecture: $(uname -m)"
echo ""

# Critical: /dev/mem access (fman_init opens this)
echo "=== /dev/mem ==="
ls -la /dev/mem 2>&1
if [ -r /dev/mem ] && [ -w /dev/mem ]; then
    echo "/dev/mem: read+write OK ✓"
else
    echo "⚠️  /dev/mem: limited access (may block fman_init)"
fi

echo ""
echo "=== /dev/fsl-usdpaa ==="
ls -la /dev/fsl-usdpaa /dev/fsl-usdpaa-irq 2>&1

echo ""
echo "=== /sys/devices/platform/soc/soc:fsl,dpaa ==="
if [ -d /sys/devices/platform/soc/soc:fsl,dpaa ]; then
    echo "DPAA sysfs: EXISTS ✓ (DPAA_DEV_PATH1)"
    ls /sys/devices/platform/soc/soc:fsl,dpaa/ | head -10
else
    echo "DPAA sysfs PATH1: MISSING"
fi
if [ -d /sys/devices/platform/fsl,dpaa ]; then
    echo "DPAA sysfs PATH2: EXISTS ✓"
else
    echo "DPAA sysfs PATH2: MISSING"
fi

echo ""
echo "=== /proc/device-tree/soc/fsl,dpaa (DT for fman_init) ==="
if [ -d /proc/device-tree/soc/fsl,dpaa ]; then
    echo "DT fsl,dpaa: EXISTS ✓"
    ls /proc/device-tree/soc/fsl,dpaa/ 2>/dev/null
else
    echo "DT fsl,dpaa: MISSING — fman_init will fail!"
fi

echo ""
echo "=== Hugepages ==="
grep HugePages_Total /proc/meminfo
grep HugePages_Free /proc/meminfo

echo ""
echo "=== CONFIG_STRICT_DEVMEM check ==="
if grep -q "CONFIG_STRICT_DEVMEM=y" /boot/config-$(uname -r) 2>/dev/null; then
    echo "⚠️  CONFIG_STRICT_DEVMEM=y — may block /dev/mem MMIO for FMan CCSR"
elif grep -q "# CONFIG_STRICT_DEVMEM is not set" /boot/config-$(uname -r) 2>/dev/null; then
    echo "CONFIG_STRICT_DEVMEM=n ✓ — /dev/mem unrestricted"
else
    echo "Cannot determine (no /boot/config-*). Proceeding..."
fi

# ── 3. Unbind SFP+ ──
echo ""
echo "--- 3. Unbind SFP+ MACs ---"
for dev in 1af0000.ethernet 1af2000.ethernet; do
    if [ -L /sys/bus/platform/devices/$dev/driver ]; then
        net_dir=/sys/bus/platform/devices/$dev/net
        if [ -d "$net_dir" ]; then
            iface=$(ls "$net_dir" 2>/dev/null | head -1)
            [ -n "$iface" ] && ip link set "$iface" down 2>/dev/null || true
        fi
        echo "$dev" > /sys/bus/platform/drivers/fsl_dpaa_mac/unbind 2>/dev/null && \
            echo "  $dev → unbound ✓" || echo "  $dev → FAILED"
    else
        echo "  $dev → already unbound ✓"
    fi
done
sleep 2

echo ""
echo "Interfaces after unbind:"
ip -br link show

# ── 4. Clean VPP ──
echo ""
echo "--- 4. Clean VPP state ---"
systemctl stop vpp.service 2>/dev/null || true
pkill -9 vpp 2>/dev/null || true; sleep 1
pkill -9 vpp 2>/dev/null || true
rm -f /run/vpp/cli.sock /dev/shm/vpp-* /dev/shm/db_vpp_* 2>/dev/null || true
echo "VPP cleaned"

# ── 5. Write VPP config ──
echo ""
echo "--- 5. VPP config ---"
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
echo "Config: no-pci, no dev lines, only dpdk_plugin enabled"

# ── 6. Start VPP ──
echo ""
echo "--- 6. Start VPP (timeout ${TIMEOUT}s, foreground) ---"
truncate -s 0 "$VPP_LOG" 2>/dev/null || true
rm -f "$VPP_OUT"

echo "┌──────────── VPP STDOUT/STDERR ────────────┐"

# Use a FIFO to capture output while VPP runs
FIFO="/tmp/vpp-v6-fifo"
rm -f "$FIFO"
mkfifo "$FIFO"

# Background: tee from FIFO to both stdout and logfile
tee "$VPP_OUT" < "$FIFO" &
TEE_PID=$!

# Run VPP, output goes to FIFO
timeout "$TIMEOUT" "$VPP_BIN" -c "$VPP_CONF" > "$FIFO" 2>&1 &
VPP_PID=$!
echo "(VPP PID: $VPP_PID)"

# Wait for VPP to init (8 seconds)
sleep 8

# Run vppctl diagnostics while VPP is alive
if kill -0 $VPP_PID 2>/dev/null; then
    echo "" >> "$FIFO"
    echo "--- VPP alive after 8s, running vppctl ---" >> "$FIFO"

    {
        echo ""
        echo "[vppctl] show version:"
        vppctl show version 2>&1 || echo "(failed)"

        echo ""
        echo "[vppctl] show plugins | grep -i dpdk:"
        vppctl show plugins 2>&1 | grep -i dpdk || echo "(no dpdk)"

        echo ""
        echo "[vppctl] show interface:"
        vppctl show interface 2>&1 || echo "(failed)"

        echo ""
        echo "[vppctl] show hardware:"
        vppctl show hardware-interfaces 2>&1 || echo "(failed)"

        echo ""
        echo "[vppctl] show dpdk version:"
        vppctl show dpdk version 2>&1 || echo "(no dpdk version cmd)"

        echo ""
        echo "[vppctl] show log (last 30):"
        vppctl show log 2>&1 | tail -30 || echo "(failed)"
    } >> "$FIFO" 2>&1
fi

# Wait for VPP to exit (timeout or crash)
wait $VPP_PID 2>/dev/null
VPP_EXIT=$?

# Close FIFO
exec 3>"$FIFO"  # open write end
exec 3>&-       # close it — signals EOF to tee
wait $TEE_PID 2>/dev/null
rm -f "$FIFO"

echo "└──────────── END VPP ──────────────────────┘"
echo ""
echo "VPP exit code: $VPP_EXIT"
[ "$VPP_EXIT" = "124" ] && echo "→ Timeout = VPP survived full ${TIMEOUT}s ✓"
[ "$VPP_EXIT" = "137" ] && echo "→ SIGKILL (likely timeout)"
[ "$VPP_EXIT" = "0" ] && echo "→ Clean exit"

# ── 7. Post-mortem analysis ──
echo ""
echo "================================================================"
echo "  POST-MORTEM ANALYSIS"
echo "================================================================"

echo ""
echo "--- 7a. Key DPAA messages in stdout ---"
if [ -f "$VPP_OUT" ]; then
    echo "Total stdout lines: $(wc -l < "$VPP_OUT")"
    echo ""

    echo "[DPAA Bus Detection]"
    grep -i "DPAA Bus" "$VPP_OUT" 2>/dev/null || echo "  (none)"

    echo "[DPAA Bus Scan]"
    grep -i "dpaa.*scan\|dpaa.*not present\|dpaa.*skip" "$VPP_OUT" 2>/dev/null || echo "  (none)"

    echo "[FMan Init]"
    grep -iE "fman|FMAN" "$VPP_OUT" 2>/dev/null || echo "  (none)"

    echo "[netcfg]"
    grep -i "netcfg\|num_ethports\|USDPAA" "$VPP_OUT" 2>/dev/null || echo "  (none)"

    echo "[/dev/mem]"
    grep -i "/dev/mem\|Unable to open" "$VPP_OUT" 2>/dev/null || echo "  (none)"

    echo "[Device creation]"
    grep -i "fm1-mac\|dpaa_sec\|qdma" "$VPP_OUT" 2>/dev/null || echo "  (none)"

    echo "[QMan/BMan init]"
    grep -iE "qman.*init\|bman.*init\|QMAN\|BMAN" "$VPP_OUT" 2>/dev/null || echo "  (none)"

    echo "[Probe results]"
    grep -iE "unable to probe\|probe.*err\|probed successfully" "$VPP_OUT" 2>/dev/null || echo "  (none)"

    echo "[EAL]"
    grep -i "EAL:" "$VPP_OUT" 2>/dev/null | head -20 || echo "  (none)"

    echo "[Errors]"
    grep -iE "error|fail|unable|cannot" "$VPP_OUT" 2>/dev/null | head -20 || echo "  (none)"
else
    echo "❌ No stdout file!"
fi

echo ""
echo "--- 7b. VPP log file ---"
if [ -f "$VPP_LOG" ]; then
    echo "Log lines: $(wc -l < "$VPP_LOG")"
    head -80 "$VPP_LOG"
else
    echo "(no log file)"
fi

echo ""
echo "--- 7c. dmesg DPAA/USDPAA (last 30) ---"
dmesg | grep -iE 'dpaa|usdpaa|qman_portal|bman_portal|fman' | tail -30

echo ""
echo "--- 7d. Full stdout dump ---"
echo "=== BEGIN ==="
cat "$VPP_OUT" 2>/dev/null
echo "=== END ==="

# ── 8. Result Summary ──
echo ""
echo "================================================================"
echo "  RESULT SUMMARY"
echo "================================================================"

DPAA_BUS=false
DPAA_IFACES=false
DPAA_FAIL=false

if grep -q "DPAA Bus Detected" "$VPP_OUT" 2>/dev/null; then
    DPAA_BUS=true
    echo "✅ DPAA Bus Detected"
else
    echo "❌ DPAA Bus NOT Detected"
fi

if grep -q "fm1-mac" "$VPP_OUT" 2>/dev/null; then
    echo "✅ FMan MACs referenced"
    grep "fm1-mac" "$VPP_OUT" | head -10
fi

if grep -q "unable to probe" "$VPP_OUT" 2>/dev/null; then
    DPAA_FAIL=true
    echo "❌ DPAA PMD probe FAILED"
fi

IFACE_COUNT=$(grep -c "fm1-mac" "$VPP_OUT" 2>/dev/null || echo 0)
echo "DPAA interface references: $IFACE_COUNT"

echo ""
echo "⚠️  DO NOT rebind eth3/eth4 — REBOOT instead"
echo "Full output: /tmp/v6-full.log (if piped through tee)"
echo "VPP stdout: $VPP_OUT"
echo "Test complete: $(date -u '+%Y-%m-%d %H:%M:%S UTC')"
