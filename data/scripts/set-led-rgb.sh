#!/bin/bash
# set-led-rgb.sh — set RGB LED to (x, y, z) values via sysfs
# Usage:
#   set-led-rgb.sh <x> <y> <z> [led-name]
# Examples:
#   set-led-rgb.sh 255 0 0
#   set-led-rgb.sh 32 64 128 lp5812:channel0

set -euo pipefail

usage() {
    echo "Usage: $0 <x> <y> <z> [led-name]" >&2
    echo "  x, y, z must be integers in range 0..255" >&2
}

is_byte() {
    local v="$1"
    [[ "$v" =~ ^[0-9]+$ ]] && [ "$v" -ge 0 ] && [ "$v" -le 255 ]
}

if [ "$#" -lt 3 ] || [ "$#" -gt 4 ]; then
    usage
    exit 2
fi

x="$1"
y="$2"
z="$3"
led_name="${4:-}"

if ! is_byte "$x" || ! is_byte "$y" || ! is_byte "$z"; then
    echo "ERROR: x, y, z must be integers in range 0..255" >&2
    exit 2
fi

if [ -n "$led_name" ]; then
    led_dir="/sys/class/leds/$led_name"
    if [ ! -d "$led_dir" ]; then
        echo "ERROR: LED '$led_name' not found in /sys/class/leds" >&2
        exit 1
    fi
else
    led_dir=""
    for d in /sys/class/leds/*; do
        [ -d "$d" ] || continue
        [ -f "$d/multi_intensity" ] || continue

        channels="$(awk '{ print NF }' "$d/multi_intensity" 2>/dev/null || echo 0)"
        if [ "$channels" -eq 3 ]; then
            led_dir="$d"
            break
        fi
    done

    if [ -z "$led_dir" ]; then
        echo "ERROR: no 3-channel multicolor LED found in /sys/class/leds" >&2
        exit 1
    fi
fi

if [ ! -f "$led_dir/multi_intensity" ]; then
    echo "ERROR: '$led_dir' is not a multicolor LED (missing multi_intensity)" >&2
    exit 1
fi

channels="$(awk '{ print NF }' "$led_dir/multi_intensity" 2>/dev/null || echo 0)"
if [ "$channels" -ne 3 ]; then
    echo "ERROR: '$led_dir' has $channels channels; expected 3 for RGB" >&2
    exit 1
fi

echo "$x $y $z" > "$led_dir/multi_intensity"

if [ -f "$led_dir/max_brightness" ] && [ -f "$led_dir/brightness" ]; then
    max_brightness="$(cat "$led_dir/max_brightness")"
    echo "$max_brightness" > "$led_dir/brightness"
fi

echo "Set $(basename "$led_dir") RGB to ($x,$y,$z)"
