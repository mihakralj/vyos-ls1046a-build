#!/bin/bash
# ask-modules-load.sh — Load ASK out-of-tree kernel modules in correct order.
#
# Background:
#   The producer release (lts_6.6_ls1046a, kernel-6.6.135-askN) ships an
#   `ask-modules-<KVER>-vyos_*_arm64.deb` whose contents install to the
#   canonical kernel-modules location:
#       /lib/modules/<KVER>/extra/ask/cdx.ko
#       /lib/modules/<KVER>/extra/ask/fci.ko
#       /lib/modules/<KVER>/extra/ask/auto_bridge.ko   (when the producer
#                                                       starts shipping it)
#
#   The deb's postinst runs depmod, so in-tree dependencies are resolvable
#   via modprobe. We still insmod from the explicit path because the OOT
#   modules sit under .../extra/ask/ (out of modprobe's default path
#   priority) and load order matters: cdx → auto_bridge → fci.
#
# Behaviour:
#   * Hard-fails (exit non-zero) if cdx.ko or fci.ko is missing/insmod errors,
#     so systemd marks the unit failed and `ask-check` reports the right thing.
#   * Tolerates a missing auto_bridge.ko (warning only) until the producer
#     starts shipping it.
#   * Skips quietly (exit 0) when DPAA isn't present (non-LS1046A or generic
#     kernel) — there's nothing to do.

set -e

KVER=$(uname -r)
MODDIR=/lib/modules/${KVER}/extra/ask

# Skip on non-DPAA hardware.
if [ ! -d /sys/bus/platform/drivers/fsl_dpa ]; then
    echo "ask-modules-load: fsl_dpa driver not present, skipping ASK modules"
    exit 0
fi

if [ ! -d "$MODDIR" ]; then
    echo "ask-modules-load: $MODDIR missing — ask-modules deb not installed"
    exit 1
fi

# In-tree dependencies first. depmod ran during package install, so modprobe
# resolves these without explicit paths.
modprobe bridge               2>/dev/null || true
modprobe nf_conntrack         2>/dev/null || true
modprobe nf_conntrack_netlink 2>/dev/null || true
modprobe xt_conntrack         2>/dev/null || true

load_required() {
    local mod="$1"
    local path="$MODDIR/${mod}.ko"
    if [ ! -f "$path" ]; then
        echo "ask-modules-load: REQUIRED module $mod.ko missing at $path"
        exit 1
    fi
    echo "ask-modules-load: insmod $mod"
    if ! insmod "$path"; then
        # Already loaded is fine; anything else is a real failure.
        if grep -q "^$mod " /proc/modules 2>/dev/null; then
            echo "ask-modules-load: $mod already loaded"
        else
            echo "ask-modules-load: insmod $mod FAILED"
            exit 1
        fi
    fi
}

load_optional() {
    local mod="$1"
    local path="$MODDIR/${mod}.ko"
    if [ ! -f "$path" ]; then
        echo "ask-modules-load: optional module $mod.ko absent — producer deb does not ship it yet"
        return 0
    fi
    echo "ask-modules-load: insmod $mod"
    insmod "$path" || {
        if grep -q "^$mod " /proc/modules 2>/dev/null; then
            echo "ask-modules-load: $mod already loaded"
        else
            echo "ask-modules-load: insmod $mod FAILED (optional, continuing)"
        fi
    }
}

# Order matters: cdx exports symbols consumed by auto_bridge and fci.
load_required cdx
load_optional auto_bridge
load_required fci

# Sanity: cdx creates the control char device when it loads.
if [ -c /dev/cdx_ctrl ]; then
    echo "ask-modules-load: ASK ready — /dev/cdx_ctrl present"
else
    echo "ask-modules-load: /dev/cdx_ctrl missing after module load"
    exit 1
fi