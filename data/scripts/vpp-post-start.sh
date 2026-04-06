#!/bin/bash
# vpp-post-start.sh — Fix defunct interface MTU + VPP internal MTU after VPP/LCP renames
#
# When VPP starts with LCP, it renames hardware netdevs (eth3 → defunct_eth3)
# and creates TAP interfaces with the original names.
#
# MTU strategy for DPAA1 AF_XDP:
#   TAP interfaces (eth3/eth4):      1500 (standard network MTU, set by VyOS config)
#   Defunct hardware (defunct_eth3):  3290 (DPAA1 AF_XDP maximum - required for XSK)
#   VPP internal (af_xdp-eth3):      1500 (match network, not hardware limit)
#
# The defunct hardware interfaces MUST have MTU ≤ 3290 for AF_XDP socket creation.
# Default 1500 works but 3290 gives AF_XDP maximum headroom.
# VPP internal MTU must match the network path (1500) so TCP MSS is correct.
#
# Installed as ExecStartPost in vpp.service via systemd drop-in.

set -e

DPAA_HW_MTU=3290
NETWORK_MTU=1500
WAIT_SECS=3

# Wait for LCP to complete interface rename
sleep "$WAIT_SECS"

# Set defunct hardware interfaces to DPAA1 AF_XDP maximum (3290)
for iface in /sys/class/net/defunct_*; do
    [ -d "$iface" ] || continue
    name=$(basename "$iface")
    current_mtu=$(cat "$iface/mtu" 2>/dev/null || echo 0)
    if [ "$current_mtu" -ne "$DPAA_HW_MTU" ]; then
        ip link set "$name" mtu "$DPAA_HW_MTU" 2>/dev/null && \
            echo "vpp-post-start: $name MTU $current_mtu → $DPAA_HW_MTU" || \
            echo "vpp-post-start: WARNING: failed to set MTU on $name"
    fi
done

# Set VPP internal MTU to match network (1500) — fixes VPP showing 9000
for iface in /sys/class/net/defunct_*; do
    [ -d "$iface" ] || continue
    name=$(basename "$iface")
    vpp_name="${name#defunct_}"
    vppctl set interface mtu "$NETWORK_MTU" "$vpp_name" 2>/dev/null && \
        echo "vpp-post-start: VPP $vpp_name MTU → $NETWORK_MTU" || true
done