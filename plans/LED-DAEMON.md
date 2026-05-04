# Mono Gateway DK — Status LED Daemon (`monoledd`)

**Status:** spec / plan — not yet implemented.

A small userspace daemon that drives the front-panel RGBW status LED to
communicate **router load** at a glance, using a black-body /
iron-spectrum colour ramp. Brightness and hue both rise with load, so a
peripheral glance is enough to read "the box is working hard."

This document is the spec and integration plan; once implementation
lands, the user-facing reference will live in [HWCTL.md](../HWCTL.md).

---

## 1. Hardware target

The status indicator is one logical RGBW LED exposed by the LP5812 at
i²c bus `15-006c`, surfaced by the leds class as four sysfs nodes:

| sysfs path                                  | channel |
|---------------------------------------------|---------|
| `/sys/class/leds/status:red/brightness`     | R, 0-255 |
| `/sys/class/leds/status:green/brightness`   | G, 0-255 |
| `/sys/class/leds/status:blue/brightness`    | B, 0-255 |
| `/sys/class/leds/status:white/brightness`   | W, 0-255 |

Each channel's `trigger` must be `none` for direct brightness writes to
stick. The daemon claims all four; any LED trigger / `mono-leds.service`
recipe that mirrors `eth0` link state must move to a different LED
(e.g. `mmc0::` or one of the SFP cage LEDs).

The LP5812 supports **8-bit PWM per channel** plus an autonomous
exponential dimming engine (Weber-Fechner / log-scale brightness). The
mainline `leds-lp5812` driver in this build does **not** expose the
log-scale or autonomous-engine knobs through sysfs — only raw PWM. The
daemon therefore applies a software gamma/log correction and software
interpolation. (Driver-level support for `LOG_SCALE_EN` and the
autonomous engine is a future enhancement, not a v1 dependency.)

---

## 2. Signal philosophy

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
between states is done by the daemon (see §5).

The other thing to log-map is the **input**: network traffic spans
orders of magnitude (1 Mbps idle to 1 Gbps saturated = 1000×). Linear
mapping wastes 90 % of the scale on the top decade. The daemon uses
`log10(bps)` clipped to a log curve, normalised against link capacity.

---

## 3. RGBW state table (16 steps, black-body + cool-idle)

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

Per-channel current trim is applied as a constant scaling factor in the
config (default `1.0`). If a particular LED package has an over-bright
red die, bias R lower (e.g. `red_scale=0.85`).

The values above assume the LP5812's W channel is the cool-white die;
if it is warm-white (~3000 K), the saturation states will look more
orange-white than blue-white, which actually works in our favour for
this scale.

---

## 4. Input source & load metric

The "load" scalar fed into the table is **derived purely from network
throughput** — no CPU, no conntrack, no other signals. The status LED
is a packet-flow indicator. CPU-busy / commit-in-progress / table-walk
states are intentionally invisible to the LED; they belong in
journald, not in a peripheral-vision channel.

The board has five physical FMan ports — three 1G RJ45 (`eth0`, `eth1`,
`eth2`) and two 10G SFP+ (`eth3`, `eth4`). We treat the LED as a
**chassis-wide** indicator: the busiest single port wins, not the sum.
A saturated 10G WAN should glow red even if everything else is idle;
averaging would smear that signal away.

**Capacity is hard-coded at 10 Gbps**, not derived from
`/sys/class/net/<ifc>/speed`. Reasons:

- The 1G ports have a real capacity of 1 Gbps but they share the same
  visual scale: 800 Mbps on a 1G port is "very busy" in absolute terms
  but still 12× slower than a saturated 10G port and should not paint
  the LED red. A per-port capacity normalisation would over-state the
  1G ports.
- `/sys/class/net/<ifc>/speed` is unreliable on this platform — SDK
  `fsl_dpa` interfaces in fixed-link 10G mode return `10000`, but
  swphy / phylink edge cases occasionally read `-1` or `0`, which
  would either crash a divisor or pin the LED at saturation.
- One fixed denominator keeps the daemon stateless about port type.

**Counter source**: read once per tick from
`/sys/class/net/<ifc>/statistics/rx_bytes` and `tx_bytes` (one open()
+ read() per file, ~10 syscalls per tick total — cheaper and less
parsing-fragile than `/proc/net/dev`). The first tick seeds the
baseline and emits step 0; subsequent ticks compute
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

This puts each decade (1 Mbps → 10 Mbps → 100 Mbps → 1 Gbps → 10 Gbps)
on roughly four steps of the 16-step ramp, which matches the
"orders-of-magnitude" intuition the colour scale is designed for.

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

Hysteresis: the daemon will only change `s` by ±1 per tick (see §5), so
no extra hysteresis band is needed.

**Caveats — VPP / AF_XDP ports.** When an interface is handed to VPP
via `set vpp settings interface ethN`, VPP runs AF_XDP on top of the
kernel netdev. Kernel `rx_bytes`/`tx_bytes` counters **still
increment** for AF_XDP traffic (XDP redirects copy through the kernel
RX path before XSK delivery), so the daemon reads honest numbers. If
the platform ever moves to full DPDK PMD ownership (RC#31 currently
blocks this — see [VPP-DPAA-PMD-VS-AFXDP.md](VPP-DPAA-PMD-VS-AFXDP.md)),
the kernel counters will go silent on those ports and the daemon will
need to read the VPP stats segment. Out of scope for v1; flagged here
so the future-proofing isn't a surprise.

**Caveats — ASK fast-path.** Conntrack-offloaded flows that go through
the SDK DPAA fast-path still pass through the netdev TX/RX path on
their way in/out of the box, so the byte counters reflect them. The
fast-path bypasses *protocol stack* CPU work, not the netdev counters.

---

## 5. Smoothing & animation

Hard step transitions look chunky on a 16-step ramp; the daemon
animates between table entries:

- **Tick interval:** 200 ms (5 Hz target). Configurable
  (`tick_ms = 200`).
- **Per-tick step delta clamp:** `±1`. A surge from idle to saturated
  takes 16 ticks (~3.2 s) — feels analog, not jumpy.
- **Inter-table interpolation:** between two adjacent table rows the
  daemon does linear interp on RGBW in **gamma-corrected** space and
  re-encodes back to PWM (`pwm = (linear^(1/2.2)) * 255`). This hides
  the quantisation in the dim end where the eye is most sensitive.
- **Idle heartbeat** (only when `--idle cyan` and `s == 0`): a slow
  ±25 % sine on B/G with 4 s period, so the LED visibly breathes —
  confirms the daemon is alive without annoying the eye.
- **Saturation alarm** (`s >= 14`): superimpose a 1 Hz 10 % brightness
  flicker on W. Reads as urgency without blinking.

If/when the LP5812 driver gains autonomous-engine support, the daemon
should hand the per-tick interpolation off to hardware (program the
ramp as start/end + duration, sleep through the transition). v1 stays
pure userspace.

---

## 6. Daemon shape

Single-file Python 3 (consistent with the other `data/scripts/*` we
ship), 200–300 lines. No external Python deps beyond the stdlib.

```
/usr/local/sbin/monoledd                     # the daemon
/etc/monoledd.conf                           # ini-style config
/etc/systemd/system/monoledd.service         # unit
/usr/local/share/monoledd/states.json        # 16-step table (ships in repo)
```

### 6.1 CLI

```
monoledd [--config /etc/monoledd.conf]
         [--once]              # compute + apply one frame, exit (debug)
         [--dry-run]           # log target RGBW each tick, don't write sysfs
         [--idle off|cyan]     # override config
         [--state N]           # force step N for 5s, then resume (calibration)
         [--list-leds]         # print discovered LP5812 channel paths and exit
```

### 6.2 Config (`/etc/monoledd.conf`)

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

Sysfs paths are config-driven so the same daemon works on a board where
the LP5812 channels are wired differently.

### 6.3 Startup behaviour

1. Parse config, resolve LED paths, fail loudly if any of the four are
   missing (refuse to half-control the indicator).
2. For each LED, write `none` to `trigger` (claim it from any active
   trigger; idempotent).
3. Drop privileges to a dedicated `monoled` system user; sysfs
   `brightness` is mode 0644 root-owned by default, so we add a
   tmpfiles.d snippet to chown the four `brightness` nodes to
   `monoled:monoled` at boot. No CAP_SYS_ADMIN or root needed at
   runtime.
4. Open `/proc/net/dev`, `/proc/stat`, optional conntrack counter once
   and `seek(0)` each tick — avoid the open()/close() churn.
5. Apply step 0 (idle) immediately, then enter the tick loop.

### 6.4 Shutdown behaviour

`systemd ExecStop=` writes 0 to all four channels, then re-points each
LED's trigger to `none` (no-op). Safe to restart at any time.

### 6.5 systemd unit

```ini
[Unit]
Description=Mono Gateway status LED daemon
After=systemd-tmpfiles-setup.service network-pre.target
Wants=systemd-tmpfiles-setup.service

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

`monoled` user is created via the same hook that wires `fancontrol`
(`98-fancontrol.chroot` style — a new `97-monoled.chroot` is the
cleanest split).

---

## 7. Failure modes & guarantees

- **LP5812 not bound** → daemon refuses to start (`exit 1`), logs a
  one-line `journalctl` message. Do not silently downgrade to a partial
  indicator; a half-lit RGBW LED is worse than off.
- **An interface in `[load].interfaces` does not exist yet** → skip
  it for that tick, log once at `warn` level. Useful for VPP /
  hot-plug ports that appear after boot.
- **`fancontrol` style hwmon-renumber problem doesn't apply here** —
  the LP5812 sysfs node names are stable across boots (driven by DT
  label, not enumeration order).
- **Trigger reclaim race:** if a sysadmin manually writes
  `/sys/class/leds/status:*/trigger` while the daemon is running, the
  next tick's brightness write will fail silently (kernel ignores it
  while a non-`none` trigger is active). Daemon detects this by
  reading back `brightness` once per second; if it doesn't match what
  was written, it re-writes `none` to `trigger` and logs a warning.
- **Software gamma is approximate.** If/when the driver gains
  `LOG_SCALE_EN` support, switch a config flag (`use_hw_log = true`)
  to skip the userspace gamma step.

---

## 8. Build / packaging integration

This is a producer-side artifact (it ships in the ISO). Files land in
the repo as:

| File | Purpose |
|---|---|
| `data/scripts/monoledd` | The daemon (Python 3, executable). |
| `data/scripts/monoledd.conf` | Default `/etc/monoledd.conf`. |
| `data/systemd/monoledd.service` | systemd unit. |
| `data/systemd/monoledd.tmpfiles` | tmpfiles.d entry that chowns the four `brightness` nodes to `monoled:monoled` at boot. |
| `data/hooks/97-monoled.chroot` | live-build hook: creates `monoled` system user, installs daemon + unit + tmpfiles, enables `monoledd.service`. |

Wiring in `bin/ci-setup-vyos-build.sh`: copy `data/hooks/97-monoled.chroot`
into `vyos-build/data/live-build-config/hooks/live/` exactly the way
`98-fancontrol.chroot` already is (per the AGENTS.md "chroot hooks do
NOT auto-apply" rule).

The daemon claiming all four `status:*` LEDs supersedes the boot-time
example in [HWCTL.md §1.5](../HWCTL.md). Update HWCTL once `monoledd`
ships:

- Move the netdev-trigger example from `status:green` / `status:blue`
  to one of the SFP cage or `mmc0::` LEDs.
- Add a §1.6 documenting how to disable `monoledd` for users who want
  to drive the status LED manually (`systemctl disable --now
  monoledd.service`, then standard sysfs writes work again).

---

## 9. Out of scope for v1

- Driver-level changes to `leds-lp5812` (autonomous engine,
  `LOG_SCALE_EN`, per-channel current trim via DT). Tracked separately
  if/when v2 needs them.
- Encoding additional signals (memory pressure, temperature, VPN
  state, PPS, etc.) into the same LED. The W channel is reserved for
  link-saturation; everything else fights for the same colour space
  and degrades the "load" reading.
- Per-port directional indication. The other six LEDs (`mmc0::`,
  `sfp{0,1}:link/activity`) are perfect for per-port duties via the
  in-tree `netdev` / `mmc0` / `sfp` triggers, and don't need the
  daemon.

---

## 10. Acceptance checklist

Before declaring v1 done:

- [ ] `monoledd --once --dry-run` prints sane RGBW for representative
  load points (idle, 50 %, 100 %).
- [ ] Cold-boot to lit LED in <5 s after `multi-user.target`.
- [ ] `iperf3 -c …` against an SFP+ port produces a visible monotonic
  warming of the LED through the colour ramp.
- [ ] Stopping `monoledd` returns the LED to dark (no stuck colour).
- [ ] `journalctl -u monoledd` is silent in steady state (no per-tick
  log spam).
- [ ] Resource cost: <1 % CPU on a single A72 at 200 ms tick, RSS
  < 20 MiB.
- [ ] Surviving an unbind/rebind of the LP5812 driver: daemon detects
  the sysfs nodes disappearing, exits cleanly, systemd respawns when
  they come back (rely on `Restart=on-failure` + `ConditionPathExists`).
