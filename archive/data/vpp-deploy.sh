#!/bin/bash
set -euo pipefail

# SSH options to bypass host key verification (LXC 200 → gateway)
SSH_OPTS="-o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o LogLevel=ERROR"
GATEWAY="vyos@192.168.1.175"
PLUGIN_SRC="/opt/vyos-dev/vpp/build-dpdk-plugin/lib/aarch64-linux-gnu/vpp_plugins/dpdk_plugin.so"
DPDK_LIB="/opt/vyos-dev/dpaa-pmd/output/dpdk/lib"
DPDK_PMD="${DPDK_LIB}/dpdk/pmds-25.0"

echo "=== Phase 1: Backup original plugin on gateway ==="
ssh $SSH_OPTS $GATEWAY "sudo cp /usr/lib/aarch64-linux-gnu/vpp_plugins/dpdk_plugin.so /usr/lib/aarch64-linux-gnu/vpp_plugins/dpdk_plugin.so.orig.static 2>/dev/null || true"
echo "Backup done"

echo "=== Phase 2: Stop VPP ==="
ssh $SSH_OPTS $GATEWAY "sudo systemctl stop vpp 2>/dev/null || true"

echo "=== Phase 3: Deploy DPDK shared libraries ==="
# Create staging dir and clean old deploy
ssh $SSH_OPTS $GATEWAY "rm -rf /tmp/dpdk-deploy && mkdir -p /tmp/dpdk-deploy"

# Copy core DPDK libs (NEEDED by dpdk_plugin.so)
echo "Copying core DPDK libs..."
for lib in librte_eal librte_ethdev librte_mbuf librte_mempool librte_cryptodev librte_log librte_kvargs librte_argparse librte_telemetry librte_ring librte_rcu librte_net librte_meter librte_pci librte_hash librte_timer librte_cmdline librte_bus_pci librte_bus_vdev librte_bus_dpaa librte_acl librte_security librte_cfgfile librte_compressdev librte_dmadev librte_eventdev librte_bbdev librte_rawdev librte_power librte_reorder librte_sched librte_stack librte_vhost librte_ip_frag librte_gso librte_gro librte_bpf librte_distributor librte_efd librte_fib librte_rib librte_lpm librte_member librte_net_dpaa librte_mempool_dpaa librte_common_dpaax librte_event_dpaa librte_dma_dpaa librte_crypto_dpaa_sec; do
    f="${DPDK_LIB}/${lib}.so.25.0"
    if [ -f "$f" ]; then
        scp $SSH_OPTS -q "$f" $GATEWAY:/tmp/dpdk-deploy/
    fi
done

# Copy DPAA PMD .so files from pmds directory (additional PMDs)
echo "Copying DPAA PMD libs from pmds dir..."
if [ -d "${DPDK_PMD}" ]; then
    for f in ${DPDK_PMD}/*.so.25.0; do
        [ -f "$f" ] && scp $SSH_OPTS -q "$f" $GATEWAY:/tmp/dpdk-deploy/
    done
fi

# Deploy all libs to /usr/lib/aarch64-linux-gnu/
echo "Installing DPDK libs..."
ssh $SSH_OPTS $GATEWAY "sudo cp /tmp/dpdk-deploy/*.so.25.0 /usr/lib/aarch64-linux-gnu/"

# Create symlinks (.so.25 and .so)
ssh $SSH_OPTS $GATEWAY 'cd /usr/lib/aarch64-linux-gnu/ && for f in librte_*.so.25.0; do
    base="${f%.so.25.0}"
    sudo ln -sf "$f" "${base}.so.25"
    sudo ln -sf "$f" "${base}.so"
done'

echo "=== Phase 4: Deploy dpdk_plugin.so ==="
scp $SSH_OPTS "$PLUGIN_SRC" $GATEWAY:/tmp/dpdk_plugin.so
ssh $SSH_OPTS $GATEWAY "sudo cp /tmp/dpdk_plugin.so /usr/lib/aarch64-linux-gnu/vpp_plugins/dpdk_plugin.so && sudo chmod 644 /usr/lib/aarch64-linux-gnu/vpp_plugins/dpdk_plugin.so"

echo "=== Phase 5: Update library cache ==="
ssh $SSH_OPTS $GATEWAY "sudo ldconfig"

echo "=== Phase 6: Verify deployment ==="
ssh $SSH_OPTS $GATEWAY "ls -lh /usr/lib/aarch64-linux-gnu/vpp_plugins/dpdk_plugin.so"
ssh $SSH_OPTS $GATEWAY "ls /usr/lib/aarch64-linux-gnu/librte_net_dpaa.so* 2>/dev/null && echo 'DPAA1 PMD: PRESENT' || echo 'DPAA1 PMD: MISSING'"
ssh $SSH_OPTS $GATEWAY "ls /usr/lib/aarch64-linux-gnu/librte_bus_dpaa.so* 2>/dev/null && echo 'DPAA1 bus: PRESENT' || echo 'DPAA1 bus: MISSING'"
ssh $SSH_OPTS $GATEWAY "ls /usr/lib/aarch64-linux-gnu/librte_mempool_dpaa.so* 2>/dev/null && echo 'DPAA1 mempool: PRESENT' || echo 'DPAA1 mempool: MISSING'"

echo ""
echo "=== Phase 7: Quick VPP load test ==="
echo "Testing if VPP can load the new dpdk_plugin..."
ssh $SSH_OPTS $GATEWAY "sudo vpp -c /dev/null unix { cli-listen /dev/null } 2>&1 | head -5 || true"
sleep 2
ssh $SSH_OPTS $GATEWAY "sudo pkill -9 vpp 2>/dev/null || true"
echo ""
echo "=== Deployment complete! ==="
echo "Next steps: Create USDPAA DTS and startup.conf.dpaa"
