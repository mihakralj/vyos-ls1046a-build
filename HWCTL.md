# Mono Gateway DK — LED & Fan Shell Cheatsheet

Hands-on shell recipes for the front-panel LP5812 status LEDs and the
EMC2305 chassis fan on the LS1046A Mono Gateway running VyOS.

Both devices are exposed through standard Linux sysfs interfaces:

| Device   | Subsystem | Path                              |
|----------|-----------|-----------------------------------|
| LP5812   | leds      | `/sys/class/leds/<label>/`        |
| EMC2305  | hwmon     | `/sys/class/hwmon/hwmonN/`        |
| TMU temp | thermal   | `/sys/class/thermal/thermal_zone3/` |

Run all commands as `root` (`sudo -i` from the `vyos` account).

---

## 1. Front-panel LEDs (TI LP5812)

The LP5812 is a 12-channel I²C LED controller at `i2c3 / 0x6c`. The DTS
currently wires up four of its channels as a single RGBW status indicator.

### 1.1 Discovery

```bash
# List the LEDs the kernel has registered
ls /sys/class/leds/
# expected:
#   status:white  status:blue  status:green  status:red

# Confirm the driver bound
ls -l /sys/bus/i2c/drivers/lp5812/
dmesg | grep -i lp5812

# Inspect one LED
LED=/sys/class/leds/status:green
ls $LED
cat $LED/max_brightness        # usually 255
cat $LED/brightness            # current value
cat $LED/trigger               # available + active trigger (active is in [brackets])
```

### 1.2 Manual on/off / dim

```bash
# Solid green at full brightness
echo 255 > /sys/class/leds/status:green/brightness

# Dim red (~25%)
echo 64  > /sys/class/leds/status:red/brightness

# All off
for c in white blue green red; do
    echo 0 > /sys/class/leds/status:$c/brightness
done
```

> A trigger must be `none` for manual writes to stick. If a trigger is
> active, set it back to `none` first:
> `echo none > /sys/class/leds/status:green/trigger`.

### 1.3 Heartbeat / timer / oneshot triggers

```bash
LED=/sys/class/leds/status:blue

# CPU heartbeat (double-blink, rate = load average)
echo heartbeat > $LED/trigger

# Custom timer blink: 200 ms on / 800 ms off
echo timer    > $LED/trigger
echo 200      > $LED/delay_on
echo 800      > $LED/delay_off

# Oneshot pulse (manual “invert” fires a single blink)
echo oneshot  > $LED/trigger
echo 100      > $LED/delay_on
echo 100      > $LED/delay_off
echo 1        > $LED/invert     # arm
echo 1        > $LED/shot       # fire

# Disarm / return to manual control
echo none     > $LED/trigger
echo 0        > $LED/brightness
```

### 1.4 Network activity (LEDS_TRIGGER_NETDEV)

`CONFIG_LEDS_TRIGGER_NETDEV=y` is enabled, so any LED can mirror an
interface's link / TX / RX state.

```bash
LED=/sys/class/leds/status:blue

echo netdev > $LED/trigger
echo eth0   > $LED/device_name
echo 1      > $LED/link          # solid on while link is up
echo 1      > $LED/tx            # blink on TX
echo 1      > $LED/rx            # blink on RX
echo 50     > $LED/interval      # blink interval (ms)
```

Use this to wire WAN-link, VPN-up, or per-port indicators without any
userspace daemon.

### 1.5 Boot-time defaults

To make the LEDs come up in a known state on every boot, drop a oneshot
unit (do this on the running system, **not** the producer repo):

```bash
cat >/etc/systemd/system/mono-leds.service <<'EOF'
[Unit]
Description=Mono Gateway front-panel LEDs
After=multi-user.target

[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=/bin/sh -c '\
    echo netdev    > /sys/class/leds/status:green/trigger; \
    echo eth0      > /sys/class/leds/status:green/device_name; \
    echo 1         > /sys/class/leds/status:green/link; \
    echo heartbeat > /sys/class/leds/status:blue/trigger'

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable --now mono-leds.service
```

---

## 2. Chassis fan (Microchip EMC2305)

The EMC2305 is a 5-channel PWM fan controller on `i2c-7` at `0x2e`.
Channel 1 (`pwm1` / `fan1`) drives the chassis fan.

In normal operation the **`fancontrol` daemon owns the fan** —
configuration lives in `/etc/fancontrol`, regenerated on each boot by
`fancontrol-setup.sh` to absorb the unstable `hwmonN` numbering.

### 2.1 Discovery

```bash
# Find the EMC2305 hwmon device (number varies across boots!)
grep -l emc2305 /sys/class/hwmon/*/name
# e.g. /sys/class/hwmon/hwmon3/name

H=$(dirname "$(grep -l '^emc2305$' /sys/class/hwmon/*/name)")
echo "EMC2305 at $H"
ls $H

# Find the CPU thermal zone fancontrol uses
for z in /sys/class/thermal/thermal_zone*; do
    printf '%s = %s\n' "$z" "$(cat $z/type)"
done
# core-cluster is the one driving the fan curve
```

### 2.2 Read-only inspection (safe, daemon stays running)

```bash
H=$(dirname "$(grep -l '^emc2305$' /sys/class/hwmon/*/name)")

cat $H/fan1_input          # tachometer RPM
cat $H/pwm1                # current duty cycle (0–255)
cat $H/pwm1_enable         # 0=off, 1=manual, 2=automatic

# CPU temp the daemon is reacting to (millidegrees C)
awk '{printf "%.1f °C\n", $1/1000}' \
    /sys/class/thermal/thermal_zone3/temp

# Daemon status & current decisions
systemctl status fancontrol
journalctl -u fancontrol -n 30 --no-pager
```

### 2.3 Manual override (test / debug)

> Stop the daemon first, otherwise it will overwrite your PWM within
> `INTERVAL` seconds (default 10 s).

```bash
H=$(dirname "$(grep -l '^emc2305$' /sys/class/hwmon/*/name)")

systemctl stop fancontrol

echo 1   > $H/pwm1_enable    # switch to manual mode
echo 51  > $H/pwm1           # spin-up minimum (~1700 RPM, EMC2305 floor)
sleep 3; cat $H/fan1_input

echo 128 > $H/pwm1           # ~50%
sleep 3; cat $H/fan1_input

echo 255 > $H/pwm1           # full speed
sleep 3; cat $H/fan1_input

echo 0   > $H/pwm1           # off (fan will coast then stop)
```

> **Note**: PWM values below ~51 are quantized to off by the EMC2305;
> there is no smooth ramp from 0. The minimum *running* duty is 51.

Hand control back to the daemon:

```bash
echo 2 > $H/pwm1_enable
systemctl start fancontrol
```

### 2.4 Tweaking the curve permanently

The active curve is a linear ramp 40 °C (off) → 80 °C (full). To change
it on a running system edit `/etc/fancontrol`:

```bash
$EDITOR /etc/fancontrol
# Adjust MINTEMP / MAXTEMP / MINSTART / MINSTOP / MAXPWM as needed
systemctl restart fancontrol
```

Note that `/etc/fancontrol` is **rewritten on every boot** by
`fancontrol-setup.sh` from the template in
[data/scripts/fancontrol.conf](data/scripts/fancontrol.conf). For
permanent changes, edit that template in the source repo and rebuild
the ISO — otherwise your tweaks survive only until the next reboot.

### 2.5 Power / fault check

```bash
H=$(dirname "$(grep -l '^emc2305$' /sys/class/hwmon/*/name)")

# Fan stalled / unplugged?
cat $H/fan1_alarm           # 1 = alarm asserted
cat $H/fan1_fault           # 1 = no tach pulses

# Temperature cooling check (full ramp)
systemctl stop fancontrol
echo 1   > $H/pwm1_enable
echo 255 > $H/pwm1
for i in $(seq 1 12); do
    awk -v p="$(cat $H/pwm1)" -v r="$(cat $H/fan1_input)" \
        -v t="$(cat /sys/class/thermal/thermal_zone3/temp)" \
        'BEGIN{printf "pwm=%s rpm=%s temp=%.1fC\n", p, r, t/1000}'
    sleep 5
done
echo 2 > $H/pwm1_enable
systemctl start fancontrol
```

---

## Quick reference

```bash
# LED on
echo 255 > /sys/class/leds/status:red/brightness

# LED off
echo 0   > /sys/class/leds/status:red/brightness

# Fan full
H=$(dirname "$(grep -l '^emc2305$' /sys/class/hwmon/*/name)")
systemctl stop fancontrol
echo 1   > $H/pwm1_enable
echo 255 > $H/pwm1

# Fan back to automatic
echo 2   > $H/pwm1_enable
systemctl start fancontrol
```
