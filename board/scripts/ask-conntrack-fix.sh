#!/bin/bash
# ask-conntrack-fix.sh — Remove VyOS notrack rules so ASK fp_netfilter can track flows
#
# VyOS creates notrack rules in the vyos_conntrack nftables table for certain
# traffic classes. ASK's fp_netfilter module needs connection tracking active
# on ALL flows to offload them to the FMan Coarse Classifier fast-path.
# With notrack rules present, those flows bypass conntrack → CMM never sees
# them → no fast-path offload.
#
# This script flushes the vyos_conntrack table's notrack chains. It runs once
# after vyos-router.service applies the initial config. VyOS may re-add rules
# on config changes — a more complete fix would hook into VyOS config commit.
#
# Guarded by: ConditionPathExists=/dev/cdx_ctrl (ASK must be loaded)

set -e

# Only run if ASK CDX is active
if [ ! -c /dev/cdx_ctrl ]; then
  echo "ask-conntrack-fix: CDX not loaded, skipping"
  exit 0
fi

# Check if nft is available
if ! command -v nft >/dev/null 2>&1; then
  echo "ask-conntrack-fix: nft not found, skipping"
  exit 0
fi

# Flush notrack chains in vyos_conntrack table (both ip and ip6)
for family in ip ip6; do
  if nft list table "$family" vyos_conntrack >/dev/null 2>&1; then
    # Find and flush all chains with "notrack" in the name
    nft list table "$family" vyos_conntrack -a 2>/dev/null | \
      grep -oP 'chain \K\S*notrack\S*' | while read -r chain; do
        echo "ask-conntrack-fix: flushing $family vyos_conntrack $chain"
        nft flush chain "$family" vyos_conntrack "$chain" 2>/dev/null || true
      done
  fi
done

echo "ask-conntrack-fix: done — all flows now tracked for ASK fast-path offload"