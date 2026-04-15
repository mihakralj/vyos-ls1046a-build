#!/bin/bash
# ask-modules-load.sh — Load ASK out-of-tree kernel modules in correct order
#
# ASK modules (cdx.ko, auto_bridge.ko, fci.ko) are installed to
# /usr/local/lib/ask-modules/ because they are out-of-tree modules
# built against the SDK kernel. Standard modprobe can't find them.
#
# Load order: cdx → auto_bridge → fci (fci depends on cdx symbols)
# In-tree dependencies (nf_conntrack, etc.) are loaded via /etc/modules-load.d/
#
# This runs as a oneshot Before=systemd-modules-load.service WantedBy=sysinit.target

set -e

MODDIR=/usr/local/lib/ask-modules

# Verify we're on LS1046A hardware with DPAA
if [ ! -d /sys/bus/platform/drivers/fsl_dpa ] 2>/dev/null; then
  # DPAA driver not present — SDK kernel not running
  echo "ask-modules-load: fsl_dpa driver not found, skipping ASK modules"
  exit 0
fi

load_mod() {
  local mod="$1"
  local path="$MODDIR/$mod"
  if [ -f "$path" ]; then
    echo "ask-modules-load: loading $mod"
    insmod "$path" || echo "ask-modules-load: WARNING: failed to load $mod (may already be loaded)"
  else
    echo "ask-modules-load: WARNING: $mod not found at $path"
  fi
}

# Load in dependency order
load_mod cdx.ko

# auto_bridge.ko depends on symbols exported by the 'bridge' module
# (br_fdb_register_can_expire_cb, register_brevent_notifier, etc.)
# Load bridge before auto_bridge to satisfy these dependencies
modprobe bridge 2>/dev/null || true

load_mod auto_bridge.ko

# Ensure in-tree conntrack modules are loaded before fci
modprobe nf_conntrack 2>/dev/null || true
modprobe nf_conntrack_netlink 2>/dev/null || true
modprobe xt_conntrack 2>/dev/null || true

load_mod fci.ko

# Verify
if [ -c /dev/cdx_ctrl ]; then
  echo "ask-modules-load: CDX control device present — ASK ready"
else
  echo "ask-modules-load: WARNING: /dev/cdx_ctrl not found after module load"
fi