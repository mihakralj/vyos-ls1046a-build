#!/bin/bash
# boot-complete-notify.sh — Whistle fans on boot complete to alert admin
# EMC2305 fan controller on Mono Gateway DK
# Runs once at end of boot, then restarts fancontrol for thermal management

systemctl stop fancontrol.service 2>/dev/null || true

FAN_HWMON=$(grep -l "emc2305" /sys/class/hwmon/hwmon*/name 2>/dev/null | head -1 | xargs dirname 2>/dev/null)
if [ -z "$FAN_HWMON" ]; then
    systemctl start fancontrol.service 2>/dev/null || true
    exit 1
fi

echo 1 > "$FAN_HWMON/pwm1_enable"
for i in {1..6}; do
    echo 130 > "$FAN_HWMON/pwm1"
    sleep 0.3
    echo 255 > "$FAN_HWMON/pwm1"
    sleep 0.3
done

systemctl start fancontrol.service 2>/dev/null || true