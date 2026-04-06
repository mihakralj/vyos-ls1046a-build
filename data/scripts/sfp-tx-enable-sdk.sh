#!/bin/bash
# sfp-tx-enable-sdk.sh — Deassert TX_DISABLE on SFP+ cages for SDK kernel
#
# The SDK fsl_mac driver has no phylink/SFP awareness. The kernel SFP driver
# (sfp.c) probes and binds to sfp-xfi0/sfp-xfi1 platform devices, but its
# state machine never starts because no MAC calls sfp_bus_add_upstream().
# Result: TX_DISABLE stays asserted → SFP module TX disabled → no copper link.
#
# Fix: Unbind the SFP driver (releasing the GPIO), then manually deassert
# TX_DISABLE via sysfs GPIO. The SFP-10G-T rollball PHY self-initializes
# after TX_DISABLE deassert — no explicit I2C init needed.
#
# GPIO mapping (Mono Gateway DK with hardware inverter):
#   Physical HIGH → inverter → SFP TX_DISABLE LOW → TX ENABLED
#   Physical LOW  → inverter → SFP TX_DISABLE HIGH → TX DISABLED
#
# eth3 (sfp-xfi0): GPIO2 pin 14 = Linux GPIO 590 (gpiochip576 base + 14)
# eth4 (sfp-xfi1): GPIO2 pin 15 = Linux GPIO 591 (gpiochip576 base + 15)
#
# Usage: Called by systemd service after network interfaces are up.
#        Only needed on SDK kernel (mainline uses phylink → SFP state machine).

set -e

GPIOCHIP2_BASE=576
SFP_CAGES=(
    "sfp-xfi0:$((GPIOCHIP2_BASE + 14)):eth3"
    "sfp-xfi1:$((GPIOCHIP2_BASE + 15)):eth4"
)

log() { echo "sfp-tx-enable: $*"; logger -t sfp-tx-enable "$*"; }

# Check if running on LS1046A with SDK kernel
if ! grep -q "fsl,ls1046a" /proc/device-tree/compatible 2>/dev/null; then
    log "Not LS1046A — skipping"
    exit 0
fi

# Check if SDK DPAA driver is active (not mainline)
if [ ! -d /sys/bus/platform/drivers/fsl_dpa ]; then
    log "SDK fsl_dpa driver not present — mainline kernel, skipping"
    exit 0
fi

for cage_info in "${SFP_CAGES[@]}"; do
    IFS=: read -r sfp_dev gpio_num iface <<< "$cage_info"

    # Check if SFP platform device exists
    if [ ! -d "/sys/bus/platform/devices/$sfp_dev" ]; then
        log "$sfp_dev: platform device not found, skipping"
        continue
    fi

    # Check if SFP module is present (mod-def0 GPIO or I2C detection)
    if [ ! -d "/sys/class/net/$iface" ]; then
        log "$sfp_dev ($iface): interface not found, skipping"
        continue
    fi

    # Unbind SFP driver if currently bound
    if [ -L "/sys/bus/platform/devices/$sfp_dev/driver" ]; then
        log "$sfp_dev: unbinding SFP driver"
        echo "$sfp_dev" > /sys/bus/platform/drivers/sfp/unbind 2>/dev/null || true
        sleep 0.1
    fi

    # Export GPIO if not already exported
    if [ ! -d "/sys/class/gpio/gpio${gpio_num}" ]; then
        echo "$gpio_num" > /sys/class/gpio/export 2>/dev/null || {
            log "$sfp_dev: GPIO $gpio_num export failed (may be claimed by another driver)"
            continue
        }
    fi

    # Set direction to output
    echo out > "/sys/class/gpio/gpio${gpio_num}/direction" 2>/dev/null || {
        log "$sfp_dev: GPIO $gpio_num direction set failed"
        continue
    }

    # Set HIGH = physical HIGH → inverter → SFP TX_DISABLE LOW → TX ENABLED
    echo 1 > "/sys/class/gpio/gpio${gpio_num}/value" 2>/dev/null || {
        log "$sfp_dev: GPIO $gpio_num value set failed"
        continue
    }

    log "$sfp_dev ($iface): TX_DISABLE deasserted (GPIO $gpio_num = HIGH)"
done

log "done"