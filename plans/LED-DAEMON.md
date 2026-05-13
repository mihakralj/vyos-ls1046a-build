# Mono Gateway DK — Status LED control

The Mono Gateway DK has one logical RGBW status LED driven by an LP5812
on `i²c-15` addr `0x6c`, exposed by the kernel `leds` class as four sysfs
channels:

| sysfs path                                  | channel |
|---------------------------------------------|---------|
| `/sys/class/leds/status:red/brightness`     | R, 0–255 |
| `/sys/class/leds/status:green/brightness`   | G, 0–255 |
| `/sys/class/leds/status:blue/brightness`    | B, 0–255 |
| `/sys/class/leds/status:white/brightness`   | W, 0–255 |

The `trigger` attribute on each channel must be `none` for direct
brightness writes to stick (the in-kernel `netdev` / `mmc0` / `sfp`
triggers will otherwise overwrite anything userspace writes within
milliseconds). Every tool described below sets `trigger=none` before
writing.

This document specs **two complementary tools** that share the same LED
hardware and the same `trigger=none` convention:

1. **`led`** — manual single-shot CLI (ships today, lives in
   `board/scripts/led.py` → `/usr/local/bin/led`). Operator-facing.
   No CLI flags. Six input forms (palette index, 3- or 4-decimal,
   6- or 8-hex, `off`), all animated by a baked-in 200 ms fade.
   Calling it with **no arguments** prints the current LED state.
   Implementation-grade spec in [Part 1](#part-1--led--manual-cli).
2. **`monoledd`** — autonomous load-driven daemon (not implemented
   yet). 16-step black-body / iron colour ramp tied to NIC throughput.
   Plan-grade spec in [Part 2](#part-2--monoledd--autonomous-daemon).

They share **mutual exclusion semantics**: `monoledd` claims the LED
for the lifetime of its service; `led` is for one-shot operator use
when `monoledd.service` is stopped (or for manual override during
debugging). The two are not meant to run concurrently — they will
fight each other and the LED will flicker. See
[§ Coexistence](#coexistence--led-vs-monoledd).

---

# Part 1 — `led` (manual CLI)

**Status:** shipping. Source: `board/scripts/led.py`. Installed path
in the ISO: `/usr/local/bin/led` (no `.py` suffix, matching the
`fan-pid` / `caam-check` / `sfp-check` convention). Staged into the
chroot by `bin/ci-setup-vyos-build.sh` directly after the `caam-check`
block.

The CLI is **flag-free by design.** Fade duration and palette are
**baked in as Python constants** at the top of `led.py` so the script
behaves identically every invocation, with no per-call tuning surface
for the operator to get wrong. To re-tune fade speed or palette,
edit and reinstall the script; there is no runtime knob.

## 1.1 Baked-in constants

Top of `board/scripts/led.py`:

| Constant | Value | Meaning |
|---|---|---|
| `FADE_MS` | `200` | total fade duration in ms, applied to **every** colour change |
| `FADE_FPS` | `50` | frame rate during the fade (→ 20 ms/frame, 10 frames) |
| `MIN_FRAME_MS` | `5` | hard floor on per-frame sleep (200 Hz cap on sysfs writes) |
| `PALETTE` | 32-entry tuple of `"RRGGBBWW"` strings | indices stable across upgrades |

Setting `FADE_MS = 0` (edit and reinstall) disables the fade engine —
all transitions become a single 4-channel write. There is no CLI flag
for this.

## 1.2 Input forms

The CLI dispatches purely on **argument count and shape** of `argv`:

| `argv` | Example | Meaning |
|---|---|---|
| (empty) | `led` | print current LED state as `R G B W  #RRGGBBWW`; exit 0 |
| `off` | `led off` | fade to `0 0 0 0` |
| 1 token, pure decimal | `led 17` | fade to `PALETTE[17]` |
| 1 token, 8 hex digits (optional `#`) | `led 33003300` / `led '#33003300'` | fade to `RR GG BB WW` |
| 1 token, 6 hex digits with `#` *or* containing a non-decimal hex digit | `led '#ff8800'` / `led ab12cd` | fade to `RR GG BB 00` (W zeroed) |
| 3 decimals | `led 51 0 51` | fade to `R G B 0` (W zeroed) |
| 4 decimals | `led 51 0 51 0` | fade to `R G B W` |
| `-h` / `--help` / `help` | `led --help` | print usage; exit 0 |

There are **no other input forms** and **no flags**. Anything that
doesn't match one of the rows above exits with code 2.

### Disambiguation rules

For single-token input the parser tests in this fixed order:

1. literal `off` (case-insensitive) → fade to all zeros.
2. `^[0-9a-fA-F]{8}$` (after stripping a leading `#`) → 8-hex form.
   Consequence: `00000000` is the hex colour, **not** palette index 0
   (palette index 0 is the literal token `0`).
3. `^[0-9a-fA-F]{6}$` (after stripping `#`) AND the token either
   started with `#` *or* is not pure decimal → 6-hex form (W := 0).
   Consequence: a bare 6-decimal-digit token like `123456` is treated
   as a palette index (and will fail the range check, palette is 32
   long). Prefix with `#` to force hex: `led '#123456'`.
4. `^[0-9]+$` → palette index.
5. Anything else → exit 2.

This ordering is deliberate: the user cannot accidentally select an
out-of-range palette index by typing a long-but-numeric hex string,
and a bare 6-digit decimal cannot silently become a hex colour.

## 1.3 Read-current (no-args) form

```
$ led
51 0 51 0  #33003300
```

Output is one line, two views of the same state, separated by **two
spaces**:

- four space-separated decimal channel values in `R G B W` order
- the 8-digit uppercase hex form prefixed with `#`

Machine-readable: `read R G B W _ HEX < <(led)` works in bash. No
trailing newline beyond the single one from `print()`. No extra
columns, no header, no commentary.

## 1.4 Fade animation

**Every transition fades from the current LED state to the target
over `FADE_MS` milliseconds** by linear interpolation in raw 8-bit
PWM space at `FADE_FPS` Hz. This applies to every form that sets a
colour: `off`, `<index>`, 3-decimal, 4-decimal, 6-hex, 8-hex.

### Algorithm

```python
def fade_to(target):
    if FADE_MS <= 0:
        write_rgbw_now(*target)
        return
    r0,g0,b0,w0 = read_brightness_all()           # current sysfs state
    if (r0,g0,b0,w0) == target:                   # nothing to do
        return
    frame_ms = max(MIN_FRAME_MS, int(1000 / FADE_FPS))
    frames   = max(1, FADE_MS // frame_ms)
    for i in range(1, frames + 1):
        t = i / frames                            # t == 1 on final frame
        write_rgbw_now(*linear_interp((r0,g0,b0,w0), target, t))
        if i < frames:
            time.sleep(frame_ms / 1000.0)
```

Linear interpolation in raw PWM space (no gamma correction). At
200 ms total duration the perceptual non-linearity of LED brightness
isn't visible — gamma would help only on multi-second fades. The
final frame always lands on the exact target so rounding never
strands the LED one PWM tick off.

### Cost

A 200 ms / 50 Hz fade is 10 frames × 4 sysfs writes = **40 writes**,
total wall-clock 200 ms, total CPU << 1 ms on the LS1046A A72. Well
under any reasonable cost budget for a one-shot CLI.

### Failure modes during fade

- Sysfs write fails mid-fade → exit code 3 immediately. No attempt
  to "wind back" the partial fade; the LED state is wherever the
  last successful write left it (most likely cause is missing root,
  which the operator needs to fix anyway).
- `read_brightness()` returns 0 on sysfs read failure → fade starts
  from "off", which is the safest default if state cannot be
  recovered.

## 1.5 Palette: baked into `led.py`

The palette is a 32-entry Python tuple at the top of `led.py`.
There is **no `/config/led.json`**, no on-disk state, no auto-create
logic. Indices are stable across upgrades because they're literally
in the source — bumping the palette requires editing `led.py` and
republishing the ISO (or copying the new script into
`/usr/local/bin/led` on a live system).

Indices 0–28 sit at ~40% peak brightness (the LP5812 driving four
white SMDs through the Mono case at 100% is uncomfortably bright in
an office). Indices 29–31 are reserved `alert-*` entries at full
brightness for emergency overrides — `monoledd` (Part 2) treats
these the same way.

A palette index ≥ `len(PALETTE)` (i.e. ≥ 32) exits with code 2.

## 1.6 Exit codes

| Code | Meaning |
|---|---|
| 0 | success |
| 1 | LP5812 driver not loaded (sysfs path absent) |
| 2 | bad argument / parse error / out-of-range index |
| 3 | sysfs write failed (usually need root) |

These match what other operator tools in this repo (`fan-check`,
`caam-check`, `sfp-check`) emit so they can all be wired as
Nagios/monit probes by the same selector. Note: there is no exit
code 4 (palette is in source, cannot be "corrupt" at runtime).

## 1.7 Examples

```bash
led                         # print current state, e.g. "51 0 51 0  #33003300"
led off                     # fade to 0 0 0 0
led 1                       # fade to PALETTE[1]
led 51 0 51 0               # R=51 G=0 B=51 W=0  (decimal 4-tuple)
led 51 0 51                 # same as above; W forced to 0
led 33003300                # 8-hex form, same colour as the decimals above
led '#33003300'             # same, with explicit '#'
led ff8800                  # 6-hex form, fades to (255,136,0,0)
led '#ff8800'               # same, with explicit '#'
```

---

# Part 2 — `monoledd` (autonomous daemon)

**Status:** spec / plan — not yet implemented.

A small userspace daemon that drives the front-panel RGBW status LED
to communicate **router load** at a glance, using a black-body /
iron-spectrum colour ramp. Brightness and hue both rise with load, so
a peripheral glance is enough to read "the box is working hard."

`monoledd` shares the LED hardware, the `trigger=none` convention, and
the alert-colour conventions (palette indices 29–31) with the manual
`led` tool. It is a different process — claimed by a systemd unit,
running as a dedicated `monoled` system user, dropping privileges
after sysfs node ownership is set by `tmpfiles.d`.

## 2.1 Signal philosophy

For "the box is working hard," black-body / iron wins by a mile:
**brightness and colour both increase with load**, which double-encodes
the signal. The user reads warmer + brighter as louder without ever
having to interpret the colour explicitly.

Three separate "resolution" questions hide in one:

| Question | Answer |
|---|---|
| Distinct visual states the eye reliably distinguishes at a glance | ~5–10. Beyond that you are chasing decimals nobody reads. |
| Smooth gradient steps so transitions don't look chunky | ~32–64 with gamma / log correction. Without correction, even 256 raw PWM steps look chunky in the dim end. |
| Underlying PWM resolution | 8-bit (256) per channel from the LP5812. |

**Decision: 16 logical states.** Enough granularity to *feel* the load
climbing, not so much that we tune levels nobody sees. Smoothing
between states is done by the daemon (see §2.5).

The other thing to log-map is the **input**: network traffic spans
orders of magnitude (1 Mbps idle to 1 Gbps saturated = 1000×). Linear
mapping wastes 90% of the scale on the top decade. The daemon uses
`log10(bps)` clipped to a log curve, normalised against link capacity.

## 2.2 RGBW state table (16 steps, black-body + cool-idle)

Idle is faint cool cyan (LED is always alive and informative);
transitions to black-body as load climbs. White only kicks in at
step 12+ as a "saturation alarm."

| Step | Load % | R   | G   | B   | W   | Look                          |
|-----:|-------:|----:|----:|----:|----:|-------------------------------|
|    0 |     0  |   0 |   8 |  16 |   0 | faint cool cyan (heartbeat)   |
|    1 |   1–2  |   0 |  16 |  24 |   0 | dim cyan                      |
|    2 |   3–5  |   0 |  32 |  24 |   0 | teal                          |
|    3 |   6–10 |   0 |  64 |  16 |   0 | green                         |
|    4 |  11–15 |  16 |  96 |   8 |   0 | yellow-green                  |
|    5 |  16–22 |  48 | 128 |   0 |   0 | lime                          |
|    6 |  23–30 |  96 | 144 |   0 |   0 | yellow-lime                   |
|    7 |  31–40 | 160 | 144 |   0 |   0 | yellow                        |
|    8 |  41–50 | 200 | 128 |   0 |   0 | amber                         |
|    9 |  51–60 | 224 |  96 |   0 |   0 | orange                        |
|   10 |  61–70 | 240 |  64 |   0 |   0 | deep orange                   |
|   11 |  71–78 | 255 |  32 |   0 |   0 | red-orange                    |
|   12 |  79–85 | 255 |   0 |   0 |  16 | red                           |
|   13 |  86–92 | 255 |   0 |  32 |  64 | red-white                     |
|   14 |  93–97 | 255 |  96 |  16 | 128 | hot white                     |
|   15 | 98–100 | 255 | 200 |  64 | 255 | white-hot, saturated          |

Notes on the design:

- **W (white) is reserved as the saturation alarm.** A white tint
  specifically means the router is near link cap, visually distinct
  from "busy but fine."
- Total emitted brightness rises monotonically with step. Even a
  peripheral glance reads it as "warmer = louder."
- B at idle keeps the LED visibly on without claiming the "active"
  green/yellow band.

Alternative: **black-body purist** — drop steps 0–2 to all zeros and
start the red rise at step 3. Cleaner aesthetic, less informative when
idle. Selected via `--idle off|cyan` (default `cyan`).

The values above assume the LP5812's W channel is the cool-white die;
if it is warm-white (~3000 K), the saturation states will look more
orange-white than blue-white, which actually works in our favour for
this scale.

## 2.3 Load metric

The "load" scalar fed into the table is **derived purely from network
throughput** — no CPU, no conntrack, no other signals. The status LED
is a packet-flow indicator. CPU-busy / commit-in-progress / table-walk
states are intentionally invisible to the LED; they belong in
journald, not in a peripheral-vision channel.

The board has five physical FMan ports — three 1G RJ45 (`eth0`,
`eth1`, `eth2`) and two 10G SFP+ (`eth3`, `eth4`). We treat the LED
as a **chassis-wide** indicator: the busiest single port wins, not
the sum. A saturated 10G WAN should glow red even if everything else
is idle; averaging would smear that signal away.

**Capacity is hard-coded at 10 Gbps**, not derived from
`/sys/class/net/<ifc>/speed`. Reasons:

- The 1G ports have a real capacity of 1 Gbps but they share the
  same visual scale: 800 Mbps on a 1G port is "very busy" in
  absolute terms but still 12× slower than a saturated 10G port and
  should not paint the LED red. A per-port capacity normalisation
  would over-state the 1G ports.
- `/sys/class/net/<ifc>/speed` is unreliable on this platform — SDK
  `fsl_dpa` interfaces in fixed-link 10G mode return `10000`, but
  swphy / phylink edge cases occasionally read `-1` or `0`, which
  would either crash a divisor or pin the LED at saturation.
- One fixed denominator keeps the daemon stateless about port type.

**Counter source**: read once per tick from
`/sys/class/net/<ifc>/statistics/rx_bytes` and `tx_bytes` (one
`open()` + `read()` per file, ~10 syscalls per tick total — cheaper
and less parsing-fragile than `/proc/net/dev`). The first tick seeds
the baseline and emits step 0; subsequent ticks compute
`bps = (Δrx_bytes + Δtx_bytes) * 8 / Δt`.

**Per-port load**:

```
PORT_CAP_BPS = 10e9                       # 10 Gbps, fixed
LOG_FLOOR    = 1e6                        # 1 Mbps -> step ≈ 1
LOG_CEIL     = PORT_CAP_BPS               # 10 Gbps -> step 15

bps      = max(1.0, port_bps)             # avoid log(0)
port_log = (log10(bps) - log10(LOG_FLOOR)) /
           (log10(LOG_CEIL) - log10(LOG_FLOOR))
port_log = clamp(port_log, 0.0, 1.0)
```

This puts each decade (1 Mbps → 10 Mbps → 100 Mbps → 1 Gbps →
10 Gbps) on roughly four steps of the 16-step ramp, which matches
the "orders-of-magnitude" intuition the colour scale is designed for.

**Chassis aggregate**:

```
load = max(port_log[i] for i in interfaces)
```

`max`, not sum: we want "the busiest pipe," not an average that
under-reports a single saturated port. If a future use case wants
total-throughput visualisation, that is a different LED daemon, not
a config knob here.

Mapping from `load ∈ [0,1]` to step `s ∈ [0,15]`:

```
s = round(load * 15)
```

Hysteresis: the daemon will only change `s` by ±1 per tick (see
§2.5), so no extra hysteresis band is needed.

**Caveats — VPP / AF_XDP ports.** When an interface is handed to VPP
via `set vpp settings interface ethN`, VPP runs AF_XDP on top of the
kernel netdev. Kernel `rx_bytes`/`tx_bytes` counters **still
increment** for AF_XDP traffic (XDP redirects copy through the kernel
RX path before XSK delivery), so the daemon reads honest numbers. If
the platform ever moves to full DPDK PMD ownership (RC#31 currently
blocks this — see [VPP.md](VPP.md)), the kernel counters will go
silent on those ports and the daemon will need to read the VPP stats
segment. Out of scope for v1; flagged here so the future-proofing
isn't a surprise.

**Caveats — ASK fast-path.** Conntrack-offloaded flows that go
through the SDK DPAA fast-path still pass through the netdev TX/RX
path on their way in/out of the box, so the byte counters reflect
them. The fast-path bypasses *protocol stack* CPU work, not the
netdev counters.

## 2.4 Animation / tick loop

The daemon ticks at 5 Hz and animates between table entries the same
way `led` (Part 1) animates between user inputs — but with different
defaults appropriate for a continuous load indicator:

- **Tick interval:** 200 ms (5 Hz target). Configurable
  (`tick_ms = 200`).
- **Per-tick step delta clamp:** `±1`. A surge from idle to saturated
  takes 16 ticks (~3.2 s) — feels analog, not jumpy.
- **Inter-table interpolation:** between two adjacent table rows the
  daemon does linear interp on RGBW in **gamma-corrected** space and
  re-encodes back to PWM (`pwm = (linear^(1/2.2)) * 255`). This hides
  the quantisation in the dim end where the eye is most sensitive.
  (`led` skips gamma because 200 ms total transitions are too short
  for it to matter; `monoledd`'s slow continuous animation needs it.)
- **Idle heartbeat** (only when `--idle cyan` and `s == 0`): a slow
  ±25% sine on B/G with 4 s period, so the LED visibly breathes —
  confirms the daemon is alive without annoying the eye.
- **Saturation alarm** (`s >= 14`): superimpose a 1 Hz 10% brightness
  flicker on W. Reads as urgency without blinking.

If/when the LP5812 driver gains autonomous-engine support, the daemon
should hand the per-tick interpolation off to hardware (program the
ramp as start/end + duration, sleep through the transition). v1 stays
pure userspace.

## 2.5 Shape

Single-file Python 3 (consistent with `led`, `fan-pid`, and the
other `board/scripts/*` we ship), 200–300 lines. No external Python
deps beyond the stdlib.

```
/usr/local/sbin/monoledd                     # the daemon
/etc/monoledd.conf                           # ini-style config
/etc/systemd/system/monoledd.service         # unit
/usr/local/share/monoledd/states.json        # 16-step table (ships in repo)
```

### CLI

```
monoledd [--config /etc/monoledd.conf]
         [--once]              # compute + apply one frame, exit (debug)
         [--dry-run]           # log target RGBW each tick, don't write sysfs
         [--idle off|cyan]     # override config
         [--state N]           # force step N for 5s, then resume (calibration)
         [--list-leds]         # print discovered LP5812 channel paths and exit
```

### Config (`/etc/monoledd.conf`)

```ini
[general]
tick_ms        = 200
idle_mode      = cyan        ; cyan | off
log_level      = info        ; debug | info | warn | error

[load]
interfaces       = eth0,eth1,eth2,eth3,eth4   ; the 5 FMan ports
port_cap_gbps    = 10                         ; chassis-wide normaliser
log_floor_mbps   = 1                          ; bps below this -> step 0

[trim]
red_scale      = 1.0
green_scale    = 1.0
blue_scale     = 1.0
white_scale    = 1.0

[paths]
led_red        = /sys/class/leds/status:red
led_green      = /sys/class/leds/status:green
led_blue       = /sys/class/leds/status:blue
led_white      = /sys/class/leds/status:white
```

Sysfs paths are config-driven so the same daemon works on a board
where the LP5812 channels are wired differently.

### Startup behaviour

1. Parse config, resolve LED paths, fail loudly if any of the four
   are missing (refuse to half-control the indicator).
2. For each LED, write `none` to `trigger` (claim it from any active
   trigger; idempotent).
3. Drop privileges to a dedicated `monoled` system user; sysfs
   `brightness` is mode 0644 root-owned by default, so a
   `tmpfiles.d` snippet chowns the four `brightness` nodes to
   `monoled:monoled` at boot. No `CAP_SYS_ADMIN` or root needed at
   runtime.
4. Open `/sys/class/net/<ifc>/statistics/rx_bytes` and `tx_bytes`
   once per interface and `seek(0)` each tick — avoid the
   `open()`/`close()` churn.
5. Apply step 0 (idle) immediately, then enter the tick loop.

### Shutdown behaviour

`systemd ExecStop=` writes 0 to all four channels, then re-points
each LED's trigger to `none` (no-op). Safe to restart at any time.

### systemd unit

```ini
[Unit]
Description=Mono Gateway status LED daemon
After=systemd-tmpfiles-setup.service network-pre.target
Wants=systemd-tmpfiles-setup.service
ConditionPathExists=/sys/class/leds/status:red

[Service]
Type=simple
ExecStart=/usr/local/sbin/monoledd
Restart=on-failure
RestartSec=2s
User=monoled
Group=monoled
NoNewPrivileges=true
ProtectSystem=strict
ProtectHome=true
PrivateTmp=true
ReadWritePaths=/sys/class/leds/status:red /sys/class/leds/status:green /sys/class/leds/status:blue /sys/class/leds/status:white

[Install]
WantedBy=multi-user.target
```

`monoled` user is created via the same hook pattern that wires
`fancontrol` (`98-fancontrol.chroot` style — a new `97-monoled.chroot`
is the cleanest split).

## 2.6 Failure modes & guarantees

- **LP5812 not bound** → daemon refuses to start (`exit 1`), logs a
  one-line `journalctl` message. Do not silently downgrade to a
  partial indicator; a half-lit RGBW LED is worse than off.
- **An interface in `[load].interfaces` does not exist yet** → skip
  it for that tick, log once at `warn` level. Useful for VPP /
  hot-plug ports that appear after boot.
- **`fancontrol` style hwmon-renumber problem doesn't apply here**
  — the LP5812 sysfs node names are stable across boots (driven by
  DT label, not enumeration order).
- **Trigger reclaim race:** if a sysadmin manually writes
  `/sys/class/leds/status:*/trigger` while the daemon is running,
  the next tick's brightness write will fail silently (kernel
  ignores it while a non-`none` trigger is active). Daemon detects
  this by reading back `brightness` once per second; if it doesn't
  match what was written, it re-writes `none` to `trigger` and logs
  a warning.
- **Software gamma is approximate.** If/when the driver gains
  `LOG_SCALE_EN` support, switch a config flag (`use_hw_log =
  true`) to skip the userspace gamma step.

## 2.7 Build / packaging integration

Files land in the repo as:

| File | Purpose |
|---|---|
| `board/scripts/monoledd` | The daemon (Python 3, executable). |
| `board/scripts/monoledd.conf` | Default `/etc/monoledd.conf`. |
| `board/systemd/monoledd.service` | systemd unit. |
| `board/systemd/monoledd.tmpfiles` | tmpfiles.d entry that chowns the four `brightness` nodes to `monoled:monoled` at boot and creates the `multi-user.target.wants/monoledd.service` symlink. |
| `data/hooks/97-monoled.chroot` | live-build hook: creates `monoled` system user, installs daemon + unit + tmpfiles. |

Wiring in `bin/ci-setup-vyos-build.sh`: copy `data/hooks/97-monoled.chroot`
into `vyos-build/data/live-build-config/hooks/live/` exactly the way
`98-fancontrol.chroot` already is (per the AGENTS.md "chroot hooks do
NOT auto-apply" rule), and copy `board/scripts/monoledd` into
`$CHROOT/usr/local/sbin/`, alongside the existing `led` block.

## 2.8 Out of scope for v1

- Driver-level changes to `leds-lp5812` (autonomous engine,
  `LOG_SCALE_EN`, per-channel current trim via DT). Tracked
  separately if/when v2 needs them.
- Encoding additional signals (memory pressure, temperature, VPN
  state, PPS, etc.) into the same LED. The W channel is reserved
  for link-saturation; everything else fights for the same colour
  space and degrades the "load" reading.
- Per-port directional indication. The other six LEDs (`mmc0::`,
  `sfp{0,1}:link/activity`) are perfect for per-port duties via the
  in-tree `netdev` / `mmc0` / `sfp` triggers, and don't need the
  daemon.

## 2.9 Acceptance checklist

Before declaring v1 done:

- [ ] `monoledd --once --dry-run` prints sane RGBW for representative
      load points (idle, 50%, 100%).
- [ ] Cold-boot to lit LED in <5 s after `multi-user.target`.
- [ ] `iperf3 -c …` against an SFP+ port produces a visible monotonic
      warming of the LED through the colour ramp.
- [ ] Stopping `monoledd` returns the LED to dark (no stuck colour).
- [ ] `journalctl -u monoledd` is silent in steady state (no per-tick
      log spam).
- [ ] Resource cost: <1% CPU on a single A72 at 200 ms tick, RSS
      < 20 MiB.
- [ ] Surviving an unbind/rebind of the LP5812 driver: daemon detects
      the sysfs nodes disappearing, exits cleanly, systemd respawns
      when they come back (rely on `Restart=on-failure` +
      `ConditionPathExists`).

---

# Coexistence — `led` vs `monoledd`

Both tools claim the same four LP5812 sysfs channels. They cannot run
concurrently without fighting each other.

| Scenario | What to do |
|---|---|
| Just want to set a colour by hand once | Use `led`. Do not stop `monoledd` — the daemon will overwrite within one tick (200 ms). For a brief override you can `led 29` (alert-red) and the daemon will fade back to the load colour within ~3 s. |
| Want manual control for an extended period (debugging, scripted UI) | `systemctl stop monoledd.service` first, then use `led`. Re-`systemctl start monoledd.service` when done. |
| Want manual control permanently (operator preference) | `systemctl disable --now monoledd.service`. `led` then has exclusive ownership; the baked-in `PALETTE` is the only colour source. |
| Want to script colour changes from a custom daemon | Either disable `monoledd` or fork it. Two userspace processes writing the same sysfs nodes is unsupported. |

The shared conventions guarantee that switching between them is safe:

- Both tools write `trigger=none` before any brightness write
- Both tools use the same RGBW byte order (`status:red`, `:green`,
  `:blue`, `:white`)
- Both tools recognise palette indices 29–31 as alert overrides

---

# References

- Hardware spec & driver background: [HWCTL.md](../HWCTL.md) §1
- LP5812 driver source in this tree: `kernel/common/files/lp5812/`
- Manual tool source: `board/scripts/led.py`
- Manual tool staging block: `bin/ci-setup-vyos-build.sh` (just
  after the `caam-check` block, before the FLAVOR=ask gate)
- Palette: baked into `board/scripts/led.py` as the `PALETTE`
  constant (32 entries). No on-disk state.
- Future daemon source location: `board/scripts/monoledd` (to be
  added)
