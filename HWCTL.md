# Mono Gateway DK — Hardware Control & Diagnostics Cheatsheet

Hands-on shell recipes for the front-panel LP5812 status LEDs and the
EMC2305 chassis fan on the LS1046A Mono Gateway running VyOS, plus a
reference for the built-in `*-check` diagnostic scripts (section 3).

Both devices are exposed through standard Linux sysfs interfaces:

| Device   | Subsystem | Path                              |
|----------|-----------|-----------------------------------|
| LP5812   | leds      | `/sys/class/leds/<label>/`        |
| EMC2305  | hwmon     | `/sys/class/hwmon/hwmonN/`        |
| TMU temp | thermal   | `/sys/class/thermal/thermal_zone3/` |

Reads from sysfs work as the `vyos` user. Writes need root: every
`echo … > /sys/…` below is shown as `echo … | sudo tee /sys/…` so the
redirection happens in a privileged process. (Plain `sudo echo … > …`
does **not** work — the redirection is performed by your unprivileged
shell before `sudo` runs.) If you prefer, drop into a root shell once
with `sudo -i` and use the bare `echo … > …` form.

`tee` echoes the written value to stdout; append `> /dev/null` to silence
it when scripting.

---

## 1. Front-panel LEDs (TI LP5812)

The LP5812 is a 12-channel I²C LED controller at `0x6c`, reached through
the SoC i2c2 controller (`21a0000.i2c`) and the on-board i2c mux (it
shows up as bus `15-006c` once the mux is enumerated). The DTS wires up
four of its channels as a single RGBW status indicator. In addition to
those, the kernel exposes `mmc0::` and the SFP cage LEDs
(`sfp{0,1}:link`, `sfp{0,1}:activity`).

The LED triggers `heartbeat`, `timer`, `oneshot`, `netdev`, etc. are
built as **loadable modules** in this kernel — only `none`,
`disk-activity`, `disk-{read,write}`, `cpu`, `cpu0..3`, `panic`, and
`mmc0` are visible by default. Load the rest on demand with
`sudo modprobe ledtrig-<name>` before using sections 1.3 / 1.4.

> **Day-to-day use:** prefer the shipped [`led`](board/scripts/led.py)
> helper (section 1.6) — one command, fades, palette, no sysfs paths to
> remember. The raw sysfs recipes in 1.1–1.5 are the underlying
> mechanism and the fallback when you need triggers or per-channel
> control the helper doesn't expose.

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
echo 255 | sudo tee /sys/class/leds/status:green/brightness

# Dim red (~25%)
echo 64  | sudo tee /sys/class/leds/status:red/brightness

# All off
for c in white blue green red; do
    echo 0 | sudo tee /sys/class/leds/status:$c/brightness
done
```

> A trigger must be `none` for manual writes to stick. If a trigger is
> active, set it back to `none` first:
> `echo none | sudo tee /sys/class/leds/status:green/trigger`.

### 1.3 Heartbeat / timer / oneshot triggers

Load the trigger modules first (they are not auto-loaded):

```bash
sudo modprobe ledtrig-heartbeat
sudo modprobe ledtrig-timer
sudo modprobe ledtrig-oneshot
```

```bash
LED=/sys/class/leds/status:blue

# CPU heartbeat (double-blink, rate = load average)
echo heartbeat | sudo tee $LED/trigger

# Custom timer blink: 200 ms on / 800 ms off
echo timer | sudo tee $LED/trigger
echo 200   | sudo tee $LED/delay_on
echo 800   | sudo tee $LED/delay_off

# Oneshot pulse (manual “invert” fires a single blink)
echo oneshot | sudo tee $LED/trigger
echo 100     | sudo tee $LED/delay_on
echo 100     | sudo tee $LED/delay_off
echo 1       | sudo tee $LED/invert     # arm
echo 1       | sudo tee $LED/shot       # fire

# Disarm / return to manual control
echo none | sudo tee $LED/trigger
echo 0    | sudo tee $LED/brightness
```

### 1.4 Network activity (LEDS_TRIGGER_NETDEV)

`CONFIG_LEDS_TRIGGER_NETDEV=m` is enabled, so any LED can mirror an
interface's link / TX / RX state once the module is loaded:

```bash
sudo modprobe ledtrig-netdev
```

```bash
LED=/sys/class/leds/status:blue

echo netdev | sudo tee $LED/trigger
echo eth0   | sudo tee $LED/device_name
echo 1      | sudo tee $LED/link          # solid on while link is up
echo 1      | sudo tee $LED/tx            # blink on TX
echo 1      | sudo tee $LED/rx            # blink on RX
echo 50     | sudo tee $LED/interval      # blink interval (ms)
```

Use this to wire WAN-link, VPN-up, or per-port indicators without any
userspace daemon.

### 1.6 The `led` command (shipped helper)

Every ISO installs [board/scripts/led.py](board/scripts/led.py) as
**`/usr/local/bin/led`** — a flag-free, Python-stdlib-only CLI that
treats the four LP5812 channels as one logical RGBW indicator. Every
colour change **fades** from the current state to the target (linear
interpolation in raw 8-bit PWM space, 50 ms total), and the helper
forces `trigger=none` before writing, so it always works regardless of
what trigger was active. Reads work as `vyos`; writes need `sudo`.

```bash
led                      # no args: print current state — "R G B W  #RRGGBBWW"
sudo led off             # fade to all-off

# Palette index (0–31, see below)
sudo led 17

# Explicit channels, decimal 0–255
sudo led 255 0 0 0       # R G B W — pure red
sudo led 0 128 255       # R G B   — W forced to 0

# Hex
sudo led '#33003300'     # RRGGBBWW (W = white channel, NOT alpha)
sudo led '#336699'       # RRGGBB — W forced to 0

# Demo / smoke-test modes (Ctrl-C stops and restores 'off')
sudo led simulate        # jump to a random palette index every 0.1 s
sudo led simulate 0.5    # ... every 0.5 s
sudo led steps           # walk the palette 0→31→0 on a triangle wave
```

**Token disambiguation:** 8 hex digits always parse as `RRGGBBWW`;
6 digits parse as hex only when `#`-prefixed or containing a hex letter
(`a–f`) — a bare decimal token is a palette index. When in doubt,
prefix hex with `#`.

**The palette** is a baked-in 32-entry ramp designed as a
network-load indicator: index 0 = faint grey idle baseline, 1–8 ramp
through red, 9–15 orange/amber, 16–20 yellow→yellow-white, 21–25
cooling whites, 26–31 blue-white "overdrive" up to maximum. Indices are
stable across upgrades — scripts that hard-code an index keep working.

**No runtime knobs by design:** fade time (`FADE_MS = 50`), frame rate,
palette, and demo cadence are constants at the top of the script. There
is no config file and no on-disk state — permanent re-tuning means
editing `board/scripts/led.py` in this repo and rebuilding the ISO.

**Exit codes:** `0` ok · `1` LP5812 driver missing · `2` bad arguments
· `3` sysfs write failed (usually: forgot `sudo`).

### 1.7 Boot-time defaults

To make the LEDs come up in a known state on every boot, drop a oneshot
unit on the running system:

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

The EMC2305 is a 5-channel PWM fan controller at `0x2e`, reached via the
SoC i2c0 controller (`2180000.i2c`) and the on-board mux (visible as
bus `7-002e`). Channel 1 (`pwm1` / `fan1_input`) drives the chassis
fan; channel 2 (`pwm2` / `fan2_input`) is wired but currently unused
(reads `fan2_input = 0`). The other three channels are not exported by
the DTS.

The in-kernel `emc2305` driver in this build only exposes the raw `pwmN`
and `fanN_input` / `fanN_fault` attributes — there is **no
`pwmN_enable`**, **no `fanN_alarm`**, **no `fanN_min`**, and **no
`temp*`** node on this hwmon. "Manual mode" therefore just means
*stopping the userspace daemon and writing pwm1 yourself*.

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

cat $H/fan1_input          # tachometer RPM (channel 1, chassis fan)
cat $H/fan2_input          # channel 2 (unused, reads 0)
cat $H/pwm1                # current duty cycle (0–255)
cat $H/fan1_fault          # 1 = no tach pulses

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

sudo systemctl stop fancontrol

echo 51  | sudo tee $H/pwm1           # spin-up minimum (~1700 RPM, EMC2305 floor)
sleep 3; cat $H/fan1_input

echo 128 | sudo tee $H/pwm1           # ~50%
sleep 3; cat $H/fan1_input

echo 255 | sudo tee $H/pwm1           # full speed
sleep 3; cat $H/fan1_input

echo 0   | sudo tee $H/pwm1           # off (fan will coast then stop)
```

> **Note**: PWM values below ~51 are quantized to off by the EMC2305;
> there is no smooth ramp from 0. The minimum *running* duty is 51.

Hand control back to the daemon (it re-asserts `pwm1` on the next tick):

```bash
sudo systemctl start fancontrol
```

### 2.4 Tweaking the curve permanently

The active curve is a linear ramp 40 °C (off) → 80 °C (full). To change
it on a running system edit `/etc/fancontrol`:

```bash
$EDITOR /etc/fancontrol
# Adjust MINTEMP / MAXTEMP / MINSTART / MINSTOP / MAXPWM as needed
systemctl restart fancontrol
```

> **Stale on current builds:** the lm-sensors `fancontrol` stack (and its
> `fancontrol.conf` template) was removed and replaced by the multi-zone PID
> daemon [board/scripts/fan-pid](board/scripts/fan-pid) (`fan-pid.service`).
> Zone setpoints and gains are constants at the top of that script — edit it
> in the source repo and rebuild the ISO for permanent changes.

### 2.5 Power / fault check

```bash
H=$(dirname "$(grep -l '^emc2305$' /sys/class/hwmon/*/name)")

# Fan stalled / unplugged?
cat $H/fan1_fault           # 1 = no tach pulses

# Temperature cooling check (full ramp)
sudo systemctl stop fancontrol
echo 255 | sudo tee $H/pwm1
for i in $(seq 1 12); do
    awk -v p="$(cat $H/pwm1)" -v r="$(cat $H/fan1_input)" \
        -v t="$(cat /sys/class/thermal/thermal_zone3/temp)" \
        'BEGIN{printf "pwm=%s rpm=%s temp=%.1fC\n", p, r, t/1000}'
    sleep 5
done
sudo systemctl start fancontrol
```

---

## 3. Built-in diagnostics — the `*-check` scripts

Every ISO ships seven self-contained diagnostic reporters in
`/usr/local/bin/` (sources live in
[board/scripts/](board/scripts/)). They share one convention:
human-readable sectioned output with `[OK]`/`[WARN]`/`[FAIL]`/`[SKIP]`
tags, colour when stdout is a tty, **exit 0 = healthy / 1 = fault /
2 = wrong board or feature absent** — so each doubles as a
Nagios/monit/cron probe. All run as the `vyos` user; a few sections
inside them use `sudo` internally where root is needed (e.g. EEPROM and
RNG reads).

| Script | What it checks | Typical use |
|--------|----------------|-------------|
| `dpaa1-check` | Full DPAA1 networking posture: FMan/QMan/BMan DT nodes + portals, the five built-in drivers (`fsl-fman`, `fsl-fman-port`, `fsl-fman_xmdio`, `fsl_dpaa_mac`, `fsl_dpa`), microcode/MURAM, BMI ports, MEMACs + MDIO buses, PCD capability bits (KeyGen/CC/HM/Policer), jumbo bootarg, eth0–eth4 enumeration, and the AF_XDP counter block. | First stop when any network interface is missing or dead. Exit 1 pinpoints which layer of the DPAA1 stack broke. |
| `sfp-check` | Decodes the EEPROM of every inserted SFP/SFP+ module (`ethtool -m`): vendor/PN/rev/serial, connector, transceiver class, bit rate, cable lengths — and prints a verdict plus a **paste-ready `SFP_QUIRK_F(...)` line** when it detects a copper 10GBASE-T rollball module masquerading as SR fiber. | Qualifying a new SFP module. If a module needs a kernel quirk, send the printed block to the maintainer; the patch line drops straight into the `sfp_quirks[]` array (patch `4009`/sibling). |
| `fan-check` | All 5 thermal zones (ddr, serdes, fman, cluster, sec) tagged `[COOL]`/`[WARM]`/`[HOT]`/`[CRIT]` against the `fan-pid` setpoints, EMC2305 PWM duty (raw + %) and tach RPM, `fan-pid` daemon health + last log lines, and detection of a conflicting legacy `fancontrol`. | Thermal sanity check; exit 1 if `fan-pid` is dead or any zone is at/above crit. **Regression flag:** `pwm1=255 (100%)` here while the daemon journal says `pwm=51` means the broken sysfs PWM path is back in use. |
| `caam-check` | CAAM (SEC 5.4) hardware crypto: DT controller node, driver posture (`caam`, `caam_jr` mandatory; `caamalg`/`caamhash`/`caamrng`/`caampkc` optional), active Job Ring count, dmesg banners, CAAM-backed algorithms in `/proc/crypto`, and the hardware RNG (`rng_current` = `caam-rng`, 16-byte sample read). When the ASK offload is engaged it adds a CDX↔SEC FQ wiring section (self-skips otherwise). | Verifying hardware crypto offload is alive — e.g. before relying on it for IPsec or `/dev/hwrng` entropy. |
| `xsk-zc-check` | The AF_XDP true-zero-copy RX gate counters (`ethtool -S`, default eth3 eth4): `xsk_zc_eligible` / `xsk_zc_rx_armed` / `xsk_fill_guard_block` / `xsk_zc_rx_recovered` plus the wider `xsk_*` block, rendered as the spec §6.1.12 verdict — **dormant** (no ZC bind; the normal shipping state), **ZC-armed** (preconditions met), or **fault** (`xsk_fill_guard_block > 0` / attach-DMA errors — the ZC reprogram WRITE must stay disabled). | AF_XDP/VPP datapath debugging on the SFP+ ports. Accepts an interface list: `xsk-zc-check eth3`. |
| `ask-check` | The full ASK2 fast-path chain, layer by layer in boot order: kernel posture, the four in-tree patches, `ask.ko`, `ask_bridge.ko`, FMan PCD subsystem, CAAM QI sharing, dpaa-eth flow_block, xfrm offload, `askd`, `ask-cli`, an nft `flags offload` round trip, and dmesg integrity. Uses an extra `[TODO]` tag for milestones not yet landed. | Present in every image; meaningful once the ASK offload is engaged (self-skips otherwise). Tracks ASK2 bring-up progress — `[TODO]` failures map to specific PR rows in `plans/ASK2-IMPLEMENTATION.md`. |
| `firmware-check` | The complete boot-firmware inventory below the OS: board/SoC identity (DT model, SVR, silicon rev), running U-Boot version vs the copy embedded in QSPI flash, the full `/proc/mtd` partition map with per-partition fingerprints (RCW/PBL preamble, env CRC, FMan-ucode QEF header, recovery-DTB FDT magic, recovery kernel), a deep decode of the running FMan microcode (id, length, SoC code, **proprietary 210.x vs open-source 106.x** classification, md5) cross-checked against the on-flash copy and the kernel's `FMan PCD caps` probe, boot-critical U-Boot env variables + boot targets, and the `/boot/vyos.env` image selector vs the running image. | After any firmware/flash operation, before reporting a bug, or when `add system image` boot selection misbehaves. Run with `sudo` for the full report (flash reads + `fw_printenv`); unprivileged runs skip those sections. A `WARN` on running-vs-flash ucode mismatch means a flash update is pending a reboot. |

```bash
# Run everything that applies to this board/flavor
for c in dpaa1-check sfp-check fan-check caam-check xsk-zc-check ask-check firmware-check; do
    echo "== $c =="; "$c"; echo "rc=$?"
done

# Cron/monitoring probe example (alert on any non-zero exit)
fan-check >/dev/null || logger -p user.err "fan-check failed rc=$?"
```

> When adding a new diagnostic, mirror this style (sectioned report,
> tag set, 0/1/2 exit convention) and wire the install in
> `bin/ci-setup-vyos-build.sh` alongside the existing seven.

---

## Quick reference

```bash
# LED: current state / colour / off (shipped helper, see 1.6)
led
sudo led '#336699'
sudo led off

# LED raw sysfs on/off
echo 255 | sudo tee /sys/class/leds/status:red/brightness
echo 0   | sudo tee /sys/class/leds/status:red/brightness

# Fan full
H=$(dirname "$(grep -l '^emc2305$' /sys/class/hwmon/*/name)")
sudo systemctl stop fancontrol
echo 255 | sudo tee $H/pwm1

# Fan back to automatic
sudo systemctl start fancontrol
```
