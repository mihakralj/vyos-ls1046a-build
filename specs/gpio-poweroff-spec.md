# GPIO Power-Off for Mono Gateway DK (LS1046A)

**Status:** Proposed
**Date:** 2026-05-16
**Scope:** kernel config + DTS + documentation

## Problem

`systemctl poweroff` on the Mono Gateway DK does not physically power the
board down. The kernel prints `reboot: Power down` and halts, but the
power rails stay live and the SoC keeps drawing current. Because
`fan-pid` correctly drives `PWM=MAX` on its SIGTERM (a thermal failsafe
documented in `AGENTS.md`), the operator is left looking at a stuck system
with a screaming fan and has to pull the DC barrel jack to recover.

Root cause: LS1046A's TF-A implements PSCI `SYSTEM_RESET` (so `reboot`
works) but **not** PSCI `SYSTEM_OFF`. The kernel has no registered
`pm_power_off` handler, so `machine_power_off()` degenerates into
`cpu_halt()` with IRQs off — instructions stop executing, power stays
on.

The umount / loopback warnings that appear immediately before `reboot:
Power down` (live medium is busy, `/dev/loop0` cannot be detached, etc.)
are normal live-system shutdown noise and are unrelated to this problem.

## Hardware Fact

The Mono Gateway DK has an **undocumented power-cut signal wired to
gpio2 line 21, active-high**. Driving the line high physically removes
SoC power. There is no soft power-on signal — restoring power requires
unplugging and re-plugging the DC barrel jack.

GPIO ownership of gpio2 in the current DTS
(`board/dtb/mono-gateway-dk.dts`):

| Line | Consumer |
|------|----------|
| 2 | `gpio-hog` 3R-enable |
| 6 | `gpio-hog` 2R-enable |
| 9 | sfp-xfi1 LOS |
| 10 | sfp-xfi1 mod-def0 |
| 11 | sfp-xfi0 LOS |
| 12 | sfp-xfi0 mod-def0 |
| 13 | sfp-xfi1 TX_DISABLE |
| 14 | sfp-xfi0 TX_DISABLE |
| 15 | LED sfp1:link |
| 16 | LED sfp1:activity |
| 17 | LED sfp0:link |
| 18 | LED sfp0:activity |
| **21** | **power-off (new)** |
| 26 | `gpio-hog` uart-mux |

Line 21 is currently unclaimed → safe to take.

## Manual Verification (no rebuild needed)

Before committing the kernel/DTS change, the hardware behavior can be
confirmed from any running VyOS image on the board. The current VyOS
ISO does **not** ship `libgpiod` / `gpioset`, so the legacy sysfs
interface is the only available path:

```bash
# As root on the target (this will INSTANTLY cut power — board goes dark)
sudo sh -c 'echo 597 > /sys/class/gpio/export && \
            echo out > /sys/class/gpio/gpio597/direction && \
            echo 1   > /sys/class/gpio/gpio597/value'
```

Pin number derivation: `gpiochip2` base on LS1046A is `576` (verified
in `AGENTS.md` — sfp-xfi0 TX_DISABLE is gpio2 line 14 = global 590 →
base 590 − 14 = 576), so gpio2 line 21 = global GPIO `597`.

**The command does not return** because the SoC loses power before the
final `write()` syscall can complete. To recover: unplug the DC barrel
jack, wait ~3 s, plug it back in. The board will cold-boot through
U-Boot → kernel as normal.

If a future build adds `libgpiod` (`apt install gpiod`), the equivalent
one-liner using the modern interface is:

```bash
sudo gpioset gpiochip2 21=1
```

This avoids hard-coding the global base offset, but `gpiod` is not
currently in the ISO and is not required for the production fix —
the kernel-side `gpio-poweroff` driver below replaces all need for
userspace GPIO tooling.

## Solution

Use the mainline `gpio-poweroff` driver
(`drivers/power/reset/gpio-poweroff.c`) bound through DTS. The driver
registers `pm_power_off` at probe time; the GPIO is **not** asserted at
probe — it is asserted only when `machine_power_off()` is finally
invoked, after all filesystems are unmounted and after the
`reboot: Power down` kernel log line.

This is strictly better than a userspace `systemd-shutdown` hook
because:

- It fires at the very last moment of shutdown, in kernel context,
  with all userspace already torn down.
- It has no userspace dependency (no libgpiod in the shutdown chroot,
  no script ordering concerns).
- It is unaffected by `fan-pid`'s SIGTERM PWM=MAX — that has already
  fired by the time we cut power, but power dies microseconds later
  so the elevated fan speed is irrelevant.
- `reboot` is on a different code path (PSCI `SYSTEM_RESET` via
  `psci_sys_reset`) and is **not affected**.
- It survives kexec emergency shutdown, `systemctl --force --force
  poweroff`, and panic-triggered power-off paths.

## Changes

### 1. `board/dtb/mono-gateway-dk.dts`

Add a top-level node alongside the existing `/ { leds { … }; sfp_xfi0
{ … }; sfp_xfi1 { … }; };` block:

```dts
/ {
    gpio-poweroff {
        compatible = "gpio-poweroff";
        gpios = <&gpio2 21 GPIO_ACTIVE_HIGH>;
        /* timeout-ms defaults to 3000; omit unless we measure a need */
    };
};
```

Polarity `GPIO_ACTIVE_HIGH` matches the confirmed hardware behavior
(driving line 21 to logical 1 cuts power). Do **not** add an `input`
property — that is for buttons, not for a dedicated power-cut line.

### 2. Kernel config fragment

Append to the relevant fragment under `data/kernel-config/` (likely
`ls1046a-board.config` — verify by checking which fragment already
carries `CONFIG_GPIO_MPC8XXX=y` since that is the parent driver for
`&gpio2`):

```
CONFIG_POWER_RESET=y
CONFIG_POWER_RESET_GPIO=y
```

Both must be `=y`, not `=m`. The `gpio-poweroff` probe must run during
boot so `pm_power_off` is registered before any shutdown can be
triggered, and modules are not loaded in early-shutdown contexts.

### 3. `AGENTS.md`

Add under *Critical Non-Obvious Rules*:

```
- **`poweroff` cuts power via gpio2 line 21:** The Mono Gateway DK has
  no PSCI `SYSTEM_OFF` in TF-A, but it has an undocumented power-cut
  GPIO wired to gpio2 line 21 (active-high). The mainline
  `gpio-poweroff` driver, bound via the `/gpio-poweroff` DTS node,
  registers `pm_power_off` so `systemctl poweroff` physically powers
  the board down. Requires `CONFIG_POWER_RESET=y` +
  `CONFIG_POWER_RESET_GPIO=y`. Powering back on requires unplugging
  and re-plugging the DC barrel jack — there is no soft power-on.
  `reboot` is unaffected (uses PSCI `SYSTEM_RESET`).
```

### 4. `INSTALL.md`

Add one operator-facing line under the operations / troubleshooting
section:

```
- `poweroff` / `shutdown -h now` physically cuts SoC power on the Mono
  Gateway DK. To turn the board back on, unplug and re-plug the DC
  barrel jack — there is no soft power-on signal.
```

## Verification After Rebuild

On the next CI build, on the booted target:

1. Driver bound:
   ```bash
   dmesg | grep -i 'gpio-poweroff'
   # expect a probe-success line; expect NO "pm_power_off already registered"
   ```

2. DTS node present:
   ```bash
   ls /sys/firmware/devicetree/base/gpio-poweroff/
   ```

3. Line reserved:
   ```bash
   gpioinfo gpiochip2 | grep ' 21 '
   # expect: used / consumer="poweroff" / output
   ```

4. **Operator-driven (cannot be automated in CI):**
   - `sudo poweroff` → board goes fully dark within ~1 s. No fan-at-MAX
     hang.
   - Replug DC jack → cold boot through U-Boot proceeds normally.
   - `sudo reboot` → board reboots normally (regression check — must
     still work because it uses PSCI `SYSTEM_RESET`, not the new
     `pm_power_off` path).

## Risks and Rollback

**Low risk.** If line 21 is wired to something other than what the user
reports, the `gpio-poweroff` driver never *drives* the line during
normal operation — it only registers `pm_power_off` and waits. The
worst plausible outcome is "poweroff still doesn't work", which is the
current state. There is no scenario where the board powers off
unexpectedly at boot or runtime from this change.

**Rollback:** remove the DTS node and the two `CONFIG_POWER_RESET_*`
lines. Both kernel symbols are otherwise unused by this build.

## Rejected Alternatives

- **`/lib/systemd/system-shutdown/` script invoking `gpioset`** — works
  but runs from userspace before the kernel's final reboot syscall,
  depends on libgpiod being present and functional in the shutdown
  environment, runs slightly earlier than the kernel `pm_power_off`
  hook, and offers zero advantage over the DTS path.
- **Out-of-tree kernel module** — pointless; `gpio-poweroff` has been
  mainline since 3.13.
- **Input-mode `gpio-poweroff`** — that is the button-input variant
  (`input;` property), wrong semantics for a dedicated power-cut
  signal.
- **Wiring through the IMX2 watchdog as a "force-reboot" pseudo-poweroff**
  — does not actually cut power, only reboots. Doesn't solve the
  problem.
