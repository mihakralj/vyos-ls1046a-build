#!/bin/bash
# sfp-tx-enable-sdk.sh — Deassert TX_DISABLE on SFP+ cages for SDK kernel
#
# The SDK fsl_mac driver has no phylink/SFP awareness. The kernel SFP driver
# (sfp.c) probes and binds to sfp-xfi0/sfp-xfi1 platform devices, but since
# no MAC calls sfp_bus_add_upstream(), the SFP state machine never starts.
# Result: TX_DISABLE stays asserted → SFP module TX disabled → no link.
#
# Fix: Unbind the SFP driver (releasing the GPIOs it claimed), then manually
# deassert TX_DISABLE via the GPIO character device (/dev/gpiochipN ioctl).
# A background process holds the GPIO fd to prevent the line from reverting.
#
# GPIO mapping (Mono Gateway DK with hardware signal inverter):
#   Physical HIGH → inverter → SFP TX_DISABLE LOW → TX ENABLED
#   Physical LOW  → inverter → SFP TX_DISABLE HIGH → TX DISABLED
#
# DTS GPIO assignments (gpio2 = gpiochip2, base 576):
#   sfp-xfi0 (eth3, left SFP+):  tx-disable = gpio2 pin 14 → line 14 on /dev/gpiochip2
#   sfp-xfi1 (eth4, right SFP+): tx-disable = gpio2 pin 13 → line 13 on /dev/gpiochip2
#
# Requires: Python 3 (for GPIO character device ioctl — no libgpiod needed)
# Usage: Called by sfp-tx-enable-sdk.service before vyos-router.service

set -e

log() { echo "sfp-tx-enable: $*"; logger -t sfp-tx-enable "$*" 2>/dev/null || true; }

# Check if running on LS1046A
if ! grep -q "fsl,ls1046a" /proc/device-tree/compatible 2>/dev/null; then
    log "Not LS1046A — skipping"
    exit 0
fi

# Check if SDK DPAA driver is active (not mainline)
if [ ! -d /sys/bus/platform/drivers/fsl_dpa ]; then
    log "SDK fsl_dpa driver not present — mainline kernel, skipping"
    exit 0
fi

# SFP cage definitions: "platform_device:gpiochip_line:interface"
SFP_CAGES=(
    "sfp-xfi0:14:eth3"
    "sfp-xfi1:13:eth4"
)

GPIOCHIP="/dev/gpiochip2"

for cage_info in "${SFP_CAGES[@]}"; do
    IFS=: read -r sfp_dev gpio_line iface <<< "$cage_info"

    # Check if SFP platform device exists
    if [ ! -d "/sys/bus/platform/devices/$sfp_dev" ]; then
        log "$sfp_dev: platform device not found, skipping"
        continue
    fi

    # Check if interface exists
    if [ ! -d "/sys/class/net/$iface" ]; then
        log "$sfp_dev ($iface): interface not found, skipping"
        continue
    fi

    # Unbind SFP driver if currently bound (releases GPIO)
    if [ -L "/sys/bus/platform/devices/$sfp_dev/driver" ]; then
        log "$sfp_dev: unbinding SFP driver to release TX_DISABLE GPIO"
        echo "$sfp_dev" > /sys/bus/platform/drivers/sfp/unbind 2>/dev/null || true
        sleep 0.2
    fi

    # Use Python to set GPIO via character device ioctl and hold it in background
    python3 - "$GPIOCHIP" "$gpio_line" "$sfp_dev" "$iface" << 'PYEOF' &
import struct, fcntl, os, sys, time

gpiochip = sys.argv[1]
line = int(sys.argv[2])
sfp_dev = sys.argv[3]
iface = sys.argv[4]

# GPIO v1 ABI: GPIOHANDLE_REQUEST_IOCTL = _IOWR(0xB4, 0x03, struct gpiohandle_request)
GPIOHANDLE_REQUEST_IOCTL = 0xC16CB403
GPIOHANDLE_REQUEST_OUTPUT = 0x2

fd = os.open(gpiochip, os.O_RDWR)

# struct gpiohandle_request (364 bytes):
#   u32[64] lineoffsets (256 bytes)
#   u32 flags (4 bytes)
#   u8[64] default_values (64 bytes)
#   char[32] consumer_label (32 bytes)
#   u32 lines (4 bytes)
#   s32 fd (4 bytes)
buf = bytearray(364)
struct.pack_into("<I", buf, 0, line)
struct.pack_into("<I", buf, 256, GPIOHANDLE_REQUEST_OUTPUT)
buf[260] = 1  # default_values[0] = 1 (physical HIGH → inverter → TX_DISABLE LOW → TX ENABLED)
label = f"sfp-txen-{iface}".encode()[:31]
buf[324:324+len(label)] = label
struct.pack_into("<I", buf, 356, 1)  # lines = 1

try:
    fcntl.ioctl(fd, GPIOHANDLE_REQUEST_IOCTL, buf)
    handle_fd = struct.unpack_from("<i", buf, 360)[0]
except OSError as e:
    print(f"sfp-tx-enable: {sfp_dev} ({iface}): GPIO line {line} request failed: {e}", file=sys.stderr)
    sys.exit(1)

os.close(fd)

# Daemonize: the GPIO line stays active as long as handle_fd is open
print(f"sfp-tx-enable: {sfp_dev} ({iface}): TX_DISABLE deasserted (gpiochip2 line {line} = HIGH)")

# Stay alive to hold the GPIO handle
try:
    while True:
        time.sleep(86400)
except (KeyboardInterrupt, SystemExit):
    pass
PYEOF
    log "$sfp_dev ($iface): TX enable process started"
done

# Wait briefly for background processes to start
sleep 0.5
log "done"