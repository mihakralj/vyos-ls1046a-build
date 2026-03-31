#!/bin/bash
# VPP AF_XDP interface setup for SFP+ ports (eth3, eth4)
# Runs after VPP starts to create AF_XDP interfaces and Linux CP mirrors
# DPAA1 XDP max MTU is 3290 (~3304 byte max frame)
#
# THERMAL NOTE: startup.conf MUST have `workers 0` and `poll-sleep-usec 100`
# to prevent thermal shutdown on passively-cooled LS1046A.
# Worker threads ignore poll-sleep and burn 100% CPU.

LOG_TAG="vpp-setup"

log() { logger -t "$LOG_TAG" "$1"; echo "$1"; }

# Wait for VPP CLI socket
for i in $(seq 1 30); do
    [ -S /run/vpp/cli.sock ] && break
    log "Waiting for VPP CLI socket ($i/30)..."
    sleep 1
done
if [ ! -S /run/vpp/cli.sock ]; then
    log "ERROR: VPP CLI socket not available after 30s"
    exit 1
fi

# Wait for eth3 and eth4 to appear (FMan probe can be slow)
for iface in eth3 eth4; do
    for i in $(seq 1 60); do
        ip link show "$iface" >/dev/null 2>&1 && break
        log "Waiting for $iface ($i/60)..."
        sleep 1
    done
    if ! ip link show "$iface" >/dev/null 2>&1; then
        log "ERROR: $iface not found after 60s"
        exit 1
    fi
    log "$iface is ready"
done

# DPAA1 XDP constraint: max MTU 3290 for XDP path
ip link set eth3 mtu 3290
ip link set eth4 mtu 3290
log "MTU set to 3290 on eth3 eth4"

# Create AF_XDP interfaces on SFP+ ports only
vppctl create interface af_xdp host-if eth3 name vpp-eth3 num-rx-queues 1
vppctl create interface af_xdp host-if eth4 name vpp-eth4 num-rx-queues 1
log "AF_XDP interfaces created"

# Bring interfaces up
vppctl set interface state vpp-eth3 up
vppctl set interface state vpp-eth4 up
log "Interfaces up"

# Create Linux CP mirrors for VyOS visibility
vppctl lcp create vpp-eth3 host-if lcp-eth3
vppctl lcp create vpp-eth4 host-if lcp-eth4
log "LCP mirrors created"

log "VPP AF_XDP setup complete: eth3 eth4 at MTU 3290"
