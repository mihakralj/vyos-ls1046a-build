#!/bin/bash
# run-testpmd.sh — Safe DPDK testpmd launcher for DPAA1 PMD
#
# CRITICAL: Kernel dpaa_eth driver and DPDK DPAA PMD CANNOT coexist.
# DPDK reinitializes QBMan from userspace, corrupting kernel portal state.
# All kernel DPAA interfaces MUST be down before testpmd starts.
# After testpmd exits, interfaces CANNOT be brought back up safely.
# A reboot is MANDATORY after DPDK DPAA PMD use.
#
# Usage: sudo ./run-testpmd.sh [timeout_seconds]
#   Default timeout: 30 seconds

set -e

TIMEOUT="${1:-30}"
TESTPMD="/tmp/dpdk-testpmd-static"
LOGFILE="/tmp/testpmd-output.log"

echo "=== DPAA1 DPDK testpmd launcher ==="
echo "Timeout: ${TIMEOUT}s"
echo "Log: ${LOGFILE}"

# Verify running as root
if [ "$(id -u)" -ne 0 ]; then
    echo "ERROR: Must run as root (sudo)"
    exit 1
fi

# Verify testpmd binary exists
if [ ! -x "$TESTPMD" ]; then
    echo "ERROR: $TESTPMD not found or not executable"
    exit 1
fi

# Ensure /dev symlinks exist (devtmpfs may use hyphens OR underscores)
# DPDK expects /dev/fsl-usdpaa (hyphens)
if [ -c /dev/fsl_usdpaa ] && [ ! -e /dev/fsl-usdpaa ]; then
    ln -sf /dev/fsl_usdpaa /dev/fsl-usdpaa
    echo "Created symlink /dev/fsl-usdpaa -> /dev/fsl_usdpaa"
fi
if [ -c /dev/fsl-usdpaa ] && [ ! -e /dev/fsl_usdpaa ]; then
    ln -sf /dev/fsl-usdpaa /dev/fsl_usdpaa
    echo "Created symlink /dev/fsl_usdpaa -> /dev/fsl-usdpaa"
fi
if [ -c /dev/fsl_usdpaa_irq ] && [ ! -e /dev/fsl-usdpaa-irq ]; then
    ln -sf /dev/fsl_usdpaa_irq /dev/fsl-usdpaa-irq
    echo "Created symlink /dev/fsl-usdpaa-irq -> /dev/fsl_usdpaa_irq"
fi
if [ -c /dev/fsl-usdpaa-irq ] && [ ! -e /dev/fsl_usdpaa_irq ]; then
    ln -sf /dev/fsl-usdpaa-irq /dev/fsl_usdpaa_irq
    echo "Created symlink /dev/fsl_usdpaa_irq -> /dev/fsl-usdpaa-irq"
fi

# Verify USDPAA device exists (check both naming variants)
if [ ! -c /dev/fsl_usdpaa ] && [ ! -c /dev/fsl-usdpaa ]; then
    echo "ERROR: /dev/fsl-usdpaa not found — USDPAA driver not loaded"
    echo "Check: sudo dmesg | grep usdpaa"
    exit 1
fi

echo ""
echo "=== Taking down ALL kernel DPAA interfaces ==="
echo "WARNING: Network connectivity will be PERMANENTLY lost!"
echo "WARNING: A reboot is REQUIRED after testpmd exits!"
echo ""

# Take down all DPAA ethernet interfaces
# Must do this BEFORE DPDK init to prevent kernel qman_enqueue crashes
for iface in eth0 eth1 eth2 eth3 eth4; do
    if ip link show "$iface" &>/dev/null; then
        state=$(cat "/sys/class/net/$iface/operstate" 2>/dev/null || echo "unknown")
        ip link set "$iface" down 2>/dev/null || true
        echo "  $iface: was $state -> DOWN"
    fi
done

# Small delay for kernel driver to quiesce
sleep 1

echo ""
echo "=== Starting testpmd (DPAA PMD) ==="
echo "Cores: 0-1, Memory channels: 1, Log level: 8 (debug)"
echo "Mode: tx-first (send burst then forward), stats every 5s"
echo ""

# Run testpmd with timeout, capture ALL output
# -l 0-1    : use cores 0 and 1
# -n 1      : 1 memory channel
# --log-level=8 : debug logging
# -- --tx-first : send initial burst
# --stats-period=5 : print stats every 5s
timeout "$TIMEOUT" "$TESTPMD" \
    -l 0-1 \
    -n 1 \
    --log-level=8 \
    -- \
    --tx-first \
    --stats-period=5 \
    2>&1 | tee "$LOGFILE"
RETVAL=${PIPESTATUS[0]}

echo ""
echo "=== testpmd exited with code: $RETVAL ==="

if [ $RETVAL -eq 124 ]; then
    echo "(Killed by timeout after ${TIMEOUT}s — this is normal for testing)"
fi

echo ""
echo "================================================================"
echo "DPAA hardware state is CORRUPTED — kernel networking is UNSAFE."
echo "DO NOT bring interfaces back up — this WILL cause a kernel panic."
echo "Rebooting in 5 seconds..."
echo "================================================================"
echo ""

# Also dump output to serial console for capture
if [ -c /dev/ttyS0 ]; then
    echo "=== testpmd log (serial dump) ===" > /dev/ttyS0
    cat "$LOGFILE" > /dev/ttyS0 2>/dev/null || true
    echo "=== end testpmd log ===" > /dev/ttyS0
fi

sleep 5
reboot
