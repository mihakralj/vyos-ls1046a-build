# Hardware Control & Diagnostics in VyOS for Mono Gateway Development Kit

Additional included helper scripts provide diagnostic status checks, and enable the direct control and customisation of the HW (LED, CPU-Fan). This document provides both an overview of these scripts, and examples of more advanced usage.

## Quick reference:

```bash
# LED: status / set colour #hex / RGBW / decimal RGB / decimal RGBW / set off
led
sudo led '#336699'
sudo led '#33669900'
sudo led 51 102 153
sudo led 51 102 153 0
sudo led off

# Fan-check: Status, temps, set-points, PWM control, PWM duty, Fan RPM
fan-check

# Fan full
H=$(dirname "$(grep -l '^emc2305$' /sys/class/hwmon/*/name)")
sudo systemctl stop fancontrol
echo 255 | sudo tee $H/pwm1

# Fan back to automatic
sudo systemctl start fancontrol
```

---

# 1. Built-in `*-check` diagnostic scripts

Every ISO ships eight self-contained diagnostic reporters plus a `support-bundle` aggregator in `/usr/local/bin/` (sources live in [board/scripts/](board/scripts/)). They share one convention: human-readable sectioned output with `[OK]`/`[WARN]`/`[FAIL]`/`[SKIP]` tags, colour when stdout is a tty, **exit 0 = healthy / 1 = fault / 2 = wrong board or feature absent** — so each doubles as a agios/monit/cron probe. All run as the `vyos` user; a few sections inside them use `sudo` internally where root is needed (e.g. EEPROM and RNG reads).

| Script           | What it checks                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                     | Typical use                                                                                                                                                                                                                                                                                                       |
| ---------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `dpaa1-check`    | Full DPAA1 networking posture: FMan/QMan/BMan DT nodes + portals, the five built-in drivers (`fsl-fman`, `fsl-fman-port`, `fsl-fman_xmdio`, `fsl_dpaa_mac`,  fsl_dpa`), microcode/MURAM, BMI ports, MEMACs + MDIO buses, PCD capability bits (KeyGen/CC/HM/Policer), jumbo bootarg, eth0–eth4 enumeration, and the AF_XDP counter block.                                                                                                                                                                                                                                                                                                                           | First stop when any network interface is missing or dead. Exit 1 pinpoints which layer of the DPAA1 stack broke.                                                                                                                                                                                                  |
| `sfp-check`      | Decodes the EEPROM of every inserted SFP/SFP+ module (`ethtool -m`): vendor/PN/rev/serial, connector, transceiver class, bit rate, cable lengths, etc and prints a verdict plus a **paste-ready `SFP_QUIRK_F(...)` line** when it detects a copper 10GBASE-T rollball module masquerading as SR fiber.                                                                                                                                                                                                                                                                                                                                                             | Qualifying a new SFP module. If a module needs a kernel quirk, send the printed block to the maintainer; the patch line drops straight into the `sfp_quirks[]` array (patch `4009`/sibling).                                                                                                                      |
| `fan-check`      | All 5 thermal zones (ddr, serdes, fman, cluster, sec) tagged `[COOL]`/`[WARM]`/`[HOT]`/`[CRIT]` against the `fan-pid` setpoints, EMC2305 PWM duty (raw + %) and tach RPM, `fan-pid` daemon health + last log lines, and detection of a conflicting legacy `fancontrol`.                                                                                                                                                                                                                                                                                                                                                                                            | Thermal sanity check; exit 1 if `fan-pid` is dead or any zone is at/above crit. **Regression flag:** `pwm1=255 (100%)` here while the daemon journal says `pwm=51` means the broken sysfs PWM path is back in use.                                                                                                |
| `caam-check`     | CAAM (SEC 5.4) hardware crypto: DT controller node, driver posture (`caam`, `caam_jr` mandatory; `caamalg`/`caamhash`/`caamrng`/`caampkc` optional), active Job Ring count, dmesg banners, CAAM-backed algorithms in `/proc/crypto`, and the hardware RNG (`rng_current` = `caam-rng`, 16-byte sample read). When the ASK offload is engaged it adds a CDX↔SEC FQ wiring section (self-skips otherwise).                                                                                                                                                                                                                                                           | Verifying hardware crypto offload is alive — e.g. before relying on it for IPsec or `/dev/hwrng` entropy.                                                                                                                                                                                                         |
| `xsk-zc-check`   | The AF_XDP true-zero-copy RX gate counters (`ethtool -S`, default eth3 eth4): `xsk_zc_eligible` / `xsk_zc_rx_armed` / `xsk_fill_guard_block` / `xsk_zc_rx_recovered` plus the wider `xsk_*` block, rendered as the spec §6.1.12 verdict — **dormant** (no ZC bind; the normal shipping state), **ZC-armed** (preconditions met), or **fault** (`xsk_fill_guard_block > 0` / attach-DMA errors — the ZC reprogram WRITE must stay disabled).                                                                                                                                                                                                                        | AF_XDP/VPP datapath debugging on the SFP+ ports. Accepts an interface list: `xsk-zc-check eth3`.                                                                                                                                                                                                                  |
| `ask-check`      | The ASK2 preview datapath, layer by layer in boot order: dataplane posture, FMan PCD subsystem + MURAM budget, reversible mode switch, hardware classification (FE-VM chain), IPv4 forwarding op-set, `ask.ko` genl control plane, per-interface `offload ask` CLI, and dmesg integrity. Capabilities intentionally outside the preview scope — IPv6, NAT/PAT, VLAN rewrite, IPsec/ESP, bridge/L2 switchdev, multicast/non-TCP-UDP — are reported with the `DEFER` tag (software fallback), never as `[FAIL]`.                                                                                                                                                     | Present in every image; the single command to confirm the plain-routed-IPv4 hardware datapath is healthy. Exit 0 = supported IPv4 datapath operational; 1 = a required capability is missing/broken; `DEFER` lines are expected for this preview.                                                                 |
| `vpp-check`      | The VPP AF_XDP Zero-Copy status probe: board & kernel posture (isolcpus=3, 2M hugepages >=512), VPP installation & library linkage (libxdp.so check), BPF object & redirect helper (/usr/share/vpp/xdp_redirect.o, xsks_map), VyOS Python integration (control_vpp.py signature), VPP service status & PID, VPP hardware interfaces & zero-copy flags, kernel DPAA1 ZC driver posture (dmesg ZCBIND logs, bpid, defunct netdev handoff), and summary verdict.                                                                                                                                                                                                      | VPP AF_XDP Zero-Copy verification and health check. Exit 0 when VPP is active and AF_XDP Zero-Copy prerequisites are healthy.                                                                                                                                                                                     |
| `firmware-check` | The complete boot-firmware inventory below the OS: board/SoC identity (DT model, SVR, silicon rev), running U-Boot version vs the copy embedded in QSPI flash, the full `/proc/mtd` partition map with per-partition fingerprints (RCW/PBL preamble, env CRC, FMan-ucode QEF header, recovery-DTB FDT magic, recovery kernel), a deep decode of the running FMan microcode (id, length, SoC code, **proprietary 210.x vs open-source 106.x** classification, md5) cross-checked against the on-flash copy and the kernel's `FMan PCD caps` probe, boot-critical U-Boot env variables + boot targets, and the `/boot/vyos.env` image selector vs the running image. | After any firmware/flash operation, before reporting a bug, or when `add system image` boot selection misbehaves. Run with `sudo` for the full report (flash reads + `fw_printenv`); unprivileged runs skip those sections. A `WARN` on running-vs-flash ucode mismatch means a flash update is pending a reboot. |
| `support-bundle` | Paste-ready ASK2 issue report: board/kernel identity, non-interactive `show version`, active Ethernet/firewall/VPP offload configuration, live nft/FE-arm/MTU/link state, complete `ask-check` and `firmware-check` output with per-tool exit codes, and a focused kernel-log extract. Never opens an interactive vbash/config session.                                                                                                                                                                                                                                                                                                                            | Run `sudo support-bundle > ask2-bundle.txt` and attach the file to every preview bug report. Aggregate exit 0 = both health checks passed; 1 = one reported a real fault; 2 = wrong board.                                                                                                                        |

```bash
# Run everything that applies to this board
for c in dpaa1-check sfp-check fan-check caam-check xsk-zc-check ask-check vpp-check firmware-check; do
    echo "== $c =="; "$c"; echo "rc=$?"
done

# Or, for a bug report, capture one paste-ready file with everything:
sudo support-bundle > ask2-bundle.txt

# Cron/monitoring probe example (alert on any non-zero exit)
fan-check >/dev/null || logger -p user.err "fan-check failed rc=$?"
```

> **NOTE:** Any subsequent diagnostic scripts will also mirror this style convention - IE sectioned reports, colour-tagged results, stdout 0/1/2 exit). Scripts installation is globally defined in `bin/ci-setup-vyos-build.sh`.

---

# 2. The `led` command (helper)

>**NOTE:** By default, the stock LED behaviour of the Mono Gateway Development Kit is **not** modified. The helper enables both static user-set, or programmatic control of the LED in response to user-defined triggers, e.g. indicating traffic throughput.

Every ISO installs [board/scripts/led.py](board/scripts/led.py) as **`/usr/local/bin/led`** a flag-free, Python-stdlib-only CLI that treats the four LP5812 channels as one logical RGBW indicator. Every colour change **fades** from the current state to the target (linear interpolation in raw 8-bit PWM space, 50 ms total), and the helper forces `trigger=none` before writing, so it always works regardless of what trigger was active. Reads work as user `vyos`, but writes require `sudo`.

```bash
led                      # no args: read and print current state — "R G B W  #RRGGBBWW"
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

> **NOTE:** The first invocation of `led` or `sudo led` following a reboot (to print the LED status) will always return `0 0 0 0 #00000000`. This is expected, as the initial state of the LED is set by U-Boot to indicate the result of the [boot self-test](https://docs.mono.si/gateway-development-kit/getting-started#status-led), prior to VyOS booting. Following successful write via `sudo led`, any subsequent invocation will correctly return the current LED state.

## 2.1 Colour definition format

- 8 digit hex is always parsed as `RRGGBBWW` where `WW` is the white sub-pixel channel, **NOT** alpha
- 6 digit hex is parsed as a standard sRGB hex colour *only* when `#`-prefixed, or containing a hex letter (`a–f`)
- A bare decimal token e.g `1` is parsed as a palette index. (see §2.3)
 
>**NOTE:** When in doubt, prefix any hex code with `#`.

## 2.2 RGBW LED Colour representation

The colour volume (representation) of the included RGBW LED deviates significantly from that expected when defined using colours referenced to the sRGB colour space. Like any device, this RGBW LED is imperfect, and excessive use of the white sub-pixel will notably both skew-blue, and eventually wash-out the intended colour. If a particular hue is desired, experimentation to define the necessary RGBW balance is required.

> **NOTE:** This LED is incredibly bright even without the white sub-pixel. Unless attempting to script a 'disco-mode', start by specifying only RRGGBB values, and omit the white pixel entirely, unless explicitly required.

## 2.3 Colour palette index

The default colour palette defined in [board/scripts/led.py](board/scripts/led.py) provides an baked-in 32-entry indexed ramp intended for use as a network-load indicator. This behaviour is not active by default, but the index steps may still be invoked directly via `sudo led`. Indices are stable across VyOS builds, ensuring any user-scripts which rely on a set index step will working as expected. The index steps are graduated by both LED brightness and colour. This provides a relative indication, at a glance, of both traffic load (brightness) and throughput (colour).

|                |           |       |       |       |       |                |                      |
| -------------- | --------- | ----- | ----- | ----- | ----- | -------------- | -------------------- |
| **Index step** | **~Mbps** | **R** | **G** | **B** | **W** | **HEX (RGBW)** | **IRL appearance**   |
| 0              | 312.5     | 0     | 2     | 0     | 0     | \#00020000     | v.v.v. dim green     |
| 1              | 625       | 0     | 8     | 0     | 0     | \#00080000     | v.v. dim light green |
| 2              | 937.5     | 0     | 16    | 0     | 0     | \#00100000     | v. dim light green   |
| 3              | 1250      | 0     | 32    | 0     | 0     | \#00200000     | dim green            |
| 4              | 1562.5    | 8     | 32    | 0     | 0     | \#08200000     | dim green/yellow     |
| 5              | 1875      | 16    | 32    | 0     | 0     | \#10200000     | dim yellow/green     |
| 6              | 2187.5    | 32    | 24    | 0     | 0     | \#20180000     | dim yellow           |
| 7              | 2500      | 48    | 16    | 0     | 0     | \#30100000     | dim yellow/orange    |
| 8              | 2812.5    | 64    | 8     | 0     | 0     | \#40080000     | dim orange           |
| 9              | 3125      | 80    | 0     | 0     | 0     | \#50000000     | dark orange/red      |
| 10             | 3437.5    | 96    | 8     | 0     | 0     | \#60080000     | dark orange/amber    |
| 11             | 3750      | 128   | 16    | 0     | 0     | \#80100000     | dark amber           |
| 12             | 4062.5    | 160   | 8     | 0     | 0     | \#a0080000     | amber                |
| 13             | 4375      | 160   | 16    | 0     | 0     | \#a0100000     | bright amber         |
| 14             | 4687.5    | 160   | 16    | 2     | 0     | \#a0100200     | amber/pink           |
| 15             | 5000      | 160   | 16    | 8     | 0     | \#a0100800     | pink/amber           |
| 16             | 5312.5    | 128   | 16    | 16    | 0     | \#80101000     | bright pink          |
| 17             | 5625      | 96    | 16    | 16    | 0     | \#60101000     | pink/purple          |
| 18             | 5937.5    | 96    | 16    | 24    | 4     | \#60101804     | purple               |
| 19             | 6250      | 64    | 8     | 24    | 8     | \#40081808     | purple/white         |
| 20             | 6562.5    | 32    | 8     | 16    | 16    | \#20081010     | white/purple         |
| 21             | 6875      | 8     | 0     | 8     | 32    | \#08000820     | dim white            |
| 22             | 7187.5    | 0     | 0     | 0     | 64    | \#00000040     | off-white            |
| 23             | 7500      | 0     | 0     | 32    | 64    | \#00002040     | neutral white        |
| 24             | 7812.5    | 0     | 0     | 64    | 64    | \#00004040     | cool white           |
| 25             | 8125      | 0     | 0     | 96    | 32    | \#00006020     | ice white            |
| 26             | 8437.5    | 0     | 0     | 112   | 16    | \#00007010     | ice blue             |
| 27             | 8750      | 0     | 0     | 144   | 8     | \#00009008     | light blue           |
| 28             | 9062.5    | 0     | 0     | 160   | 0     | \#0000a000     | dim dark blue        |
| 29             | 9375      | 0     | 0     | 192   | 0     | \#0000c000     | dark blue            |
| 30             | 9687.5    | 0     | 0     | 224   | 0     | \#0000e000     | bright Blue          |
| 31             | 10000     | 0     | 0     | 255   | 0     | \#0000ff00     | v.bright Blue        |
## 2.4 Sane defaults and exit codes

**No runtime knobs by design:** fade time (`FADE_MS = 50`), frame rate, palette, and demo cadence are constants at the top of the script. There is no config file and no on-disk state. Permanent re-tuning requires editing `board/scripts/led.py` in this repo and rebuilding the ISO.

**Exit codes:** `0` ok · `1` LP5812 driver missing · `2` bad arguments · `3` sysfs write failed (this usually indicates a missed `sudo`).

---

# 3. Advanced configuration

As the overview of these helper scripts hints, they can enable functionality beyond assessing the status of the HW, including:

- Using the LED as a \<trigger\> indicator, e.g.:
	- Using the LED as a network activity indicator
	- Pulsing the LED as a CPU-load driven heartbeat
- Setting boot-time defaults for LED behaviour
- Manual override of CPU-fan (for debug only)
- Controlling the CPU F_1 and F_2 fans via PID loop
 
Fundamental to all advanced configuration is the discovery and enumeration of the hardware devices, communication interface addresses, and the associated sysfs objects with which scripts may usefully interact. Broadly, these are split along three paths:

| Device   | Subsystem | Path                                |
| -------- | --------- | ----------------------------------- |
| LP5812   | leds      | `/sys/class/leds/<label>/`          |
| EMC2305  | hwmon     | `/sys/class/hwmon/hwmonN/`          |
| TMU temp | thermal   | `/sys/class/thermal/thermal_zoneN/` |
>**NOTE:** Following sections frequently modify sysfs objects. Reads from sysfs will work as the `vyos` user, but writes require root. Every `echo … > /sys/…` below is shown as `echo … | sudo tee /sys/…` to ensure the redirection occurs in the privileged process. A plain `sudo echo … > …` will **not** work, as the redirection is performed by the unprivileged shell before `sudo` executes. `tee` echoes the written value to stdout, but appending `> /dev/null` silences this output, which is useful when scripting. 

>**NOTE:** If preferred, invoke a `root` shell directly via `sudo -i`, and then use the bare `echo … > …` form of the commands shown. However, the usual warnings and disclaimers apply here, as misuse of `root` may render your VyOS installation useable.

## 3.1 Front-panel LEDs (TI LP5812)

> **NOTE:** For day-to-day usage, use the [`led`](board/scripts/led.py) helper (see §2.). This provides a singular means to control colour, fades and an indexed palette with no sysfs paths to remember. The raw sysfs recipes discussed here use the same underlying mechanism, and provide a fallback option when you need specific triggers or per-channel control which the helper doesn't expose.

The [LP5812](https://www.ti.com/lit/ds/symlink/lp5812.pdf?ts=1782652167518) is a 12-channel I²C LED controller at `0x6c`, reached through the SoC I²C2 controller (`21a0000.i2c`) and the on-board I²C mux (it shows up as bus `15-006c` once the mux is enumerated). The device tree source (DTS) wires up the four LED channels as a single RGBW status indicator. 

### 3.1.1 Device discovery

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

### 3.1.2 Manual on/off/dim

If you're curious what the `led` helper does under the hood, this is it.

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

>**NOTE:** A trigger must be `none` for manual writes to stick. If a trigger is active, set it back to `none` first: `echo none | sudo tee /sys/class/leds/status:green/trigger`.

## 3.1.3 Triggers

A range of system activity states can be used to trigger LED behaviour. Some are enabled by default, whilst others require specific kernel modules to be loaded on-demand.

By default, `none`, `disk-activity`, `disk-{read,write}`, `cpu`, `cpu0..3`, `panic`, and `mmc0` are usable for defining the LED state. The kernel exposes `mmc0::` and the SFP cage LEDs (`sfp{0,1}:link`, `sfp{0,1}:activity`) as well, which can be directly mirrored.

Via additional **loadable kernel modules** built for this kernel, `heartbeat`, `timer`, `oneshot`, `netdev`, etc, may also be used. To load these on-demand use `sudo modprobe ledtrig-<name>`.

### 3.1.4 Heartbeat / timer / oneshot triggers

Load the trigger modules first (they are not auto-loaded):

```bash
sudo modprobe ledtrig-heartbeat
sudo modprobe ledtrig-timer
sudo modprobe ledtrig-oneshot
```

> **NOTE:** To persistently load a desired module across reboots, place a named `.conf` e.g. "my_modules.conf" in `/etc/modules-load.d/` that contains the module name. If loading multiple modules, declare one per line.

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

### 3.1.5 Network activity (LEDS_TRIGGER_NETDEV)

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
user-space daemon.

### 3.1.6 Boot-time defaults

To make the LEDs come up in a particular defined state on every boot, drop a oneshot
systemd unit which defines the desired behaviour. This will then execute when VyOS boots

e.g: To use the link-state of eth0 as a trigger for a solid active/on green LED channel, and using CPU load to 'heartbeat' pulse on the Blue LED:

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

>**NOTE:** For the above example to actually function as desired, also ensure the dependent kernel modules are loaded at boot, see §3.1.4

---

## 3.2 Chassis fan (Microchip EMC2305)

>**WARNING:** Exercise caution. Unlike modern x86 CPUs which have extensive thermal-throttling and emergency thermal-shutdown protections, there are limited over-temperature controls on embedded device SoCs like the LS1046A. Thermal runaway may occur if thermal load exceeds cooling capacity risking permanent damage to the SoC.

The EMC2305 is a 5-channel PWM fan controller at `0x2e`, reached via the SoC I²C0 controller (`2180000.i2c`) and the on-board mux (visible as bus `7-002e`). Channel 1 (`pwm1` / `fan1_input`) drives the chassis fan; channel 2 (`pwm2` / `fan2_input`) is wired but currently unused (reads `fan2_input = 0`). The other three channels are not exported by the Device Tree Source (DTS).

The in-kernel `emc2305` driver in this build only exposes the raw `pwmN` and `fanN_input` / `fanN_fault` attributes. There is **no `pwmN_enable`**, **no `fanN_alarm`**, **no `fanN_min`**, and **no
`temp*`** node on this hwmon device. 

During normal operations, the CPU fan is controlled by `fan-pid`, a self-contained Python 3 multi-zone [PID controller](https://en.wikipedia.org/wiki/PID_controller) which takes input from all five LS1046A thermal zones (cluster\[SoC\], DDR, SerDes, Fman, CAAM). The hottest thermal zone dictates the PID loop managed fan RPM setpoint, whilst additional policy controls clamp and smooth RPM changes to further minimise noise. A measure of fault-tolerance is achieved using last-known values should a sensor become unreadable or unresponsive. As a self-contained solution, the full operating logic is defined within [board/scripts/fan-pid.py](board/scripts/fan-pid.py).

See [data/hooks/98-fancontrol.chroot](data/hooks/98-fancontrol.chroot) for the rationale behind masking the upstream alternative`fancontrol.service` defensively (as two PWM controllers must **never** run concurrently).

### 3.2.1 Device discovery

Enumeration for emc2305 is slightly complicated by variation in the assigned hwmon device number, but is trivially determined and brought into active control by `fan-pid`

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
# The fan curve response is based on the core-cluster temperature sensor value
```

### 3.2.2 Manually read-only inspection (safe)

>**NOTE:** For day-to-day use run the `fan-check` diagnostic script.

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
systemctl status fan-pid
journalctl -u fan-pid -n 30 --no-pager
```

### 3.2.3 Manual override (test / debug only)

> **NOTE:** The design of 'fan-pid' provides an easy method to test 100% PWM fan duty, as  defensively, this is set as the final act when stopping this daemon.

The `fan-control` script, returns all relevant thermal and fan data along with the output of `sudo systemctl status fan-check.service` with which to validate the responsiveness of the emc2305 controller to `fan-pid` inputs.

For diagnostic purposes, if setting CPU fan RPM to 100% is required, this can be achieved by simply stopping the fan-pid service.

```bash
sudo systemctl stop fan-pid.service   # Defensively sets CPU fan RPM 100%
sudo systemctl start fan-pid.service  # PID loop pulses slows to required RPM
fan-check                             # Print all temps, duty and RPM setpoints
```

>**NOTE:** Ensure you restart the fan-pid service. If the fans sound like a jet-engine, the defensive 100% setting is still active. Confirm status using `fan-check` or `sudo systemctl status fan-pid.service` directly.

### 3.2.4 Controlling the CPU F_1 and F_2 fans via PID loop

The [PID loop](https://en.wikipedia.org/wiki/PID_controller) used by `fan-pid` (See: [fan-pid](/board/scripts/fan-pid)) should greatly reduce the need for (any) manual intervention or tweaking of the fan-curve by through effective adaption to environmental conditions.

However, if required, the setpoints for each thermal zone, along with I- and P- term gains constants can be set within the fan-pid script. Restart the service to see the effect of changes.

>**NOTE:** Local modification is possible, but will not persist across a reboot. Edits in the source repo and rebuilding the ISO are required to affect permanent changes.