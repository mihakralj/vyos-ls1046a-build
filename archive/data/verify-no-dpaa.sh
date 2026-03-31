#!/bin/bash
PLUGIN=/opt/vyos-dev/vpp/build-no-dpaa/lib/aarch64-linux-gnu/vpp_plugins/dpdk_plugin.so
echo "=== Size ==="
ls -la "$PLUGIN"
echo ""
echo "=== DPAA symbol count ==="
aarch64-linux-gnu-nm "$PLUGIN" 2>/dev/null | grep -ciE 'dpaa|fslmc' || echo "0"
echo ""
echo "=== NEEDED ==="
aarch64-linux-gnu-readelf -d "$PLUGIN" | grep NEEDED || echo "(none)"
echo ""
echo "=== Stage for deploy ==="
cp "$PLUGIN" /tmp/dpdk_plugin.so.no-dpaa
ls -la /tmp/dpdk_plugin.so.no-dpaa
echo ""
echo "=== Size comparison ==="
echo "No-DPAA plugin: $(stat -c%s /tmp/dpdk_plugin.so.no-dpaa) bytes"
echo "With-DPAA plugin: $(stat -c%s /opt/vyos-dev/vpp/build-dpdk-static/lib/aarch64-linux-gnu/vpp_plugins/dpdk_plugin.so 2>/dev/null || echo N/A) bytes"
