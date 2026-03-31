#!/bin/bash
# recover-and-prep-v7.sh — Run on gateway after serial console reboot
# Steps:
#   1. Recover v6 test logs
#   2. Verify network is back
#   3. Show v6 results
#
# Usage (from serial console):
#   cat /tmp/vpp-v6-stdout.log
#   cat /tmp/vpp-v7-result.txt    (after v7 runs)

echo "=== VPP Test Recovery ==="
echo "Date: $(date -u '+%Y-%m-%d %H:%M:%S UTC')"
echo ""

echo "--- Check network ---"
ip -br link show
echo ""

echo "--- v6 stdout log ---"
if [ -f /tmp/vpp-v6-stdout.log ]; then
    echo "Size: $(wc -c < /tmp/vpp-v6-stdout.log) bytes, $(wc -l < /tmp/vpp-v6-stdout.log) lines"
    echo "=== BEGIN v6 stdout ==="
    cat /tmp/vpp-v6-stdout.log
    echo "=== END v6 stdout ==="
else
    echo "❌ /tmp/vpp-v6-stdout.log NOT FOUND (lost on reboot — /tmp is tmpfs)"
fi

echo ""
echo "--- v6 full log ---"
if [ -f /tmp/v6-full.log ]; then
    echo "Size: $(wc -c < /tmp/v6-full.log) bytes, $(wc -l < /tmp/v6-full.log) lines"
    echo "=== BEGIN v6 full ==="
    cat /tmp/v6-full.log
    echo "=== END v6 full ==="
else
    echo "❌ /tmp/v6-full.log NOT FOUND (lost on reboot — /tmp is tmpfs)"
fi

echo ""
echo "--- VPP log from last session ---"
if [ -f /var/log/vpp/vpp.log ]; then
    echo "Size: $(wc -c < /var/log/vpp/vpp.log) bytes"
    head -50 /var/log/vpp/vpp.log
else
    echo "(no log)"
fi

echo ""
echo "--- Running DT analysis ---"
DT_DPAA="/proc/device-tree/soc/fsl,dpaa"
if [ -d "$DT_DPAA" ]; then
    echo "fsl,dpaa node: EXISTS"
    INIT=0
    for child in "$DT_DPAA"/ethernet@*; do
        [ -d "$child" ] || continue
        compat=$(cat "$child/compatible" 2>/dev/null | tr '\0' ' ')
        name=$(basename "$child")
        if echo "$compat" | grep -q "dpa-ethernet-init"; then
            echo "  $name: dpa-ethernet-init (DPDK probes)"
            INIT=$((INIT + 1))
        else
            echo "  $name: dpa-ethernet (DPDK skips)"
        fi
    done
    echo "Total dpa-ethernet-init: $INIT"
else
    echo "❌ $DT_DPAA MISSING"
fi

echo ""
echo "--- Kernel FMan interfaces ---"
ls /sys/bus/platform/drivers/fsl_dpaa_mac/ 2>/dev/null | grep ethernet || echo "(none)"

echo ""
echo "Recovery complete. Ready for v7 test."
echo "To run v7: sudo bash /tmp/test-vpp-dpaa-v7.sh 2>&1 | tee /tmp/v7-full.log"
