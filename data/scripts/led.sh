#!/bin/bash
# led.sh — Set LP5812 status LED to purple at 20% illumination
#
# The Mono Gateway LP5812 has 4 single-color LEDs:
#   /sys/class/leds/status:white   (reg 0)
#   /sys/class/leds/status:blue    (reg 1)
#   /sys/class/leds/status:green   (reg 2)
#   /sys/class/leds/status:red     (reg 3)
#
# Purple = blue + red mixed.  20% of max 255 ≈ 51.
# All other channels off.

set -euo pipefail

LED_BASE="/sys/class/leds"
BRIGHTNESS=51   # 20% of 255

# Verify LED sysfs nodes exist
for led in status:white status:blue status:green status:red; do
    if [ ! -d "${LED_BASE}/${led}" ]; then
        echo "ERROR: ${LED_BASE}/${led} not found — LP5812 driver not loaded?" >&2
        exit 1
    fi
done

# Turn off white and green
echo 0 > "${LED_BASE}/status:white/brightness"
echo 0 > "${LED_BASE}/status:green/brightness"

# Set blue + red to 20% → purple
echo "${BRIGHTNESS}" > "${LED_BASE}/status:blue/brightness"
echo "${BRIGHTNESS}" > "${LED_BASE}/status:red/brightness"

echo "LED set to purple at 20% (brightness=${BRIGHTNESS}/255)"
