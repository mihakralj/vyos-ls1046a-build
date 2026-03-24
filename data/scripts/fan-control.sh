#!/bin/bash
# Fan control for Mono Gateway DK (EMC2305 PWM fan controller)
#
# The thermal-cooling framework binding is broken (emc2305 driver registers
# cooling_device but thermal OF doesn't bind it to cooling-maps). This script
# provides manual PWM fan control as a workaround.
#
# Hardware: Microchip EMC2305 on I2C mux channel 3 (PCA9545 @ 0x70)
# Fan 1: 4-pin PWM fan on fan header 1
# EMC2305 quantizes PWM to discrete steps (~51 minimum effective)
#
# PWM-to-RPM mapping (measured):
#   PWM=51  → ~1700 RPM (quiet)
#   PWM=102 → ~8600 RPM (EMC2305 plateau)
#   PWM=255 → ~8700 RPM (full)
#
# Temperature impact (with VPP workers 0 + poll-sleep-usec 100):
#   Fan off:  87°C (thermal shutdown risk)
#   PWM=51:   43°C (comfortable)
#   PWM=255:  43°C (overkill - no additional benefit)

set -euo pipefail

# Find EMC2305 hwmon path (hwmon numbering can change across boots)
find_emc2305() {
    for hwmon in /sys/class/hwmon/hwmon*; do
        if [ -f "$hwmon/name" ] && [ "$(cat "$hwmon/name")" = "emc2305" ]; then
            echo "$hwmon"
            return 0
        fi
    done
    return 1
}

# Read SoC temperature (millidegrees C)
get_temp() {
    cat /sys/class/thermal/thermal_zone3/temp 2>/dev/null || echo "0"
}

HWMON=$(find_emc2305) || { logger -t fan-control "EMC2305 not found"; exit 1; }
logger -t fan-control "EMC2305 found at $HWMON"

# Linear fan control based on core-cluster temperature
# Matches fancontrol config: MINTEMP=40, MAXTEMP=80, MINSTART=51, MINSTOP=34
MINTEMP=40     # Fan off below this (°C)
MAXTEMP=80     # Full speed at this (°C)
MINSTART=51    # Minimum PWM to start spinning
MINSTOP=34     # Minimum PWM to keep spinning (below MINSTART, above 0)
MAXPWM=255     # Full speed PWM

# Track if fan is currently running (for hysteresis)
FAN_ON=0
COUNT=0

while true; do
    TEMP=$(get_temp)
    TEMP_C=$((TEMP / 1000))

    if [ "$TEMP_C" -ge "$MAXTEMP" ]; then
        PWM=$MAXPWM
        FAN_ON=1
    elif [ "$TEMP_C" -le "$MINTEMP" ]; then
        PWM=0
        FAN_ON=0
    else
        # Linear interpolation: MINSTART..MAXPWM over MINTEMP..MAXTEMP
        RANGE_TEMP=$((MAXTEMP - MINTEMP))
        RANGE_PWM=$((MAXPWM - MINSTART))
        PWM=$(( MINSTART + (TEMP_C - MINTEMP) * RANGE_PWM / RANGE_TEMP ))
        # Hysteresis: if fan was off, need MINSTART to spin up
        if [ "$FAN_ON" -eq 0 ] && [ "$PWM" -lt "$MINSTART" ]; then
            PWM=0
        else
            FAN_ON=1
            # Keep above MINSTOP if running
            [ "$PWM" -lt "$MINSTOP" ] && PWM=$MINSTOP
        fi
    fi

    # Set PWM for both fan channels
    echo "$PWM" > "$HWMON/pwm1" 2>/dev/null || true
    echo "$PWM" > "$HWMON/pwm2" 2>/dev/null || true

    # Log every 5 minutes (every 30 iterations at 10s interval)
    if [ "$COUNT" -eq 0 ]; then
        RPM1=$(cat "$HWMON/fan1_input" 2>/dev/null || echo "0")
        logger -t fan-control "temp=${TEMP_C}°C pwm=$PWM fan1=${RPM1}rpm"
    fi
    COUNT=$(( (COUNT + 1) % 30 ))

    sleep 10
done
