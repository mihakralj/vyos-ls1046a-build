#!/bin/bash
# ask-conntrack-fix.sh — Remove VyOS default notrack rules for ASK fast-path
#
# VyOS's vyos_conntrack table adds "notrack" as the last rule in PREROUTING
# and OUTPUT chains as a performance optimization when no firewall/NAT rules
# need conntrack. On TFTP dev boot, firewall modules fail to load, leaving
# FW_CONNTRACK and NAT_CONNTRACK chains empty — all traffic gets notracked.
#
# ASK fast-path needs conntrack active on all flows to populate fp_info in
# conntrack entries (used for FMan PCD hardware classifier offload).
#
# This script removes the notrack rules from both ip and ip6 vyos_conntrack
# tables, allowing ASK's nf_ct_netns_get() force-enable to actually track
# packets.
#
# Installed by: 98-fancontrol.chroot or equivalent live-build hook
# Triggered by: vyos-router.service completion (After=vyos-router.service)

set -e

# Only run on LS1046A boards with ASK kernel
if ! grep -q 'fsl,ls1046a' /proc/device-tree/compatible 2>/dev/null; then
    exit 0
fi

# Check if ASK fp_netfilter is active (conntrack force-enabled)
if ! dmesg | grep -q 'ASK fp_netfilter.*conntrack force-enabled' 2>/dev/null; then
    exit 0
fi

# Remove notrack from ip vyos_conntrack
for chain in PREROUTING OUTPUT; do
    handle=$(nft -a list chain ip vyos_conntrack "$chain" 2>/dev/null | grep 'notrack' | grep -o 'handle [0-9]*' | awk '{print $2}')
    if [ -n "$handle" ]; then
        nft delete rule ip vyos_conntrack "$chain" handle "$handle" 2>/dev/null || true
        echo "ask-conntrack-fix: removed notrack from ip vyos_conntrack $chain (handle $handle)"
    fi
done

# Remove notrack from ip6 vyos_conntrack
for chain in PREROUTING OUTPUT; do
    handle=$(nft -a list chain ip6 vyos_conntrack "$chain" 2>/dev/null | grep 'notrack' | grep -o 'handle [0-9]*' | awk '{print $2}')
    if [ -n "$handle" ]; then
        nft delete rule ip6 vyos_conntrack "$chain" handle "$handle" 2>/dev/null || true
        echo "ask-conntrack-fix: removed notrack from ip6 vyos_conntrack $chain (handle $handle)"
    fi
done

remaining=$(nft list ruleset 2>/dev/null | grep -c notrack || true)
echo "ask-conntrack-fix: done (remaining notrack rules: $remaining)"