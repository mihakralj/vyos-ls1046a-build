# GPIO Power-Off for Mono Gateway DK (LS1046A)
**Version 1.1.0** · Status: Superseded (goal shipped) · 2026-05-16 (impl note 2026-06-09) · HADS 1.0.0

---

## AI READING INSTRUCTION

Read `[SPEC]` and `[BUG]` blocks for authoritative facts.
Read `[NOTE]` only if additional context is needed.
`[?]` blocks are unverified — treat with lower confidence.
**Read §0 FIRST** — the proposed DTS-driver solution below (§5/§6.1/§6.2) was NOT
shipped; the goal was achieved via the §9 *Rejected Alternative #1* (userspace hook).

---

## 0. IMPLEMENTATION STATUS

**[SPEC] The power-off GOAL is implemented and shipping — but NOT via this spec's proposed mechanism.**
- The problem (§2) is solved: `poweroff` / `shutdown -h now` physically cuts SoC power on the
  Mono Gateway DK, and `reboot` still works via PSCI `SYSTEM_RESET`.
- The shipped solution is the **userspace `systemd-shutdown(8)` hook** — i.e. this spec's own
  §9 *Rejected Alternative #1*, chosen anyway. The proposed kernel DTS `gpio-poweroff` driver
  (§5, §6.1, §6.2) was deliberately **not** applied.
- Implementation: `board/scripts/ls1046a-poweroff` (50-line POSIX `sh`). It acts only on
  `ACTION=poweroff` (exits 0 on `reboot`/`kexec`/`halt`), is board-gated on
  `grep fsl,ls1046a /proc/device-tree/compatible`, then asserts power-off via the **legacy
  sysfs GPIO interface** (`echo 597 > /sys/class/gpio/export` → `direction=out` → `value=1` →
  `sleep 1`). It uses sysfs, **not** `gpioset`/`libgpiod`, so the "needs libgpiod" objection in
  §9 does not apply to what actually shipped.
- Wiring: installed into the ISO by `bin/ci-setup-vyos-build.sh` (copies it to
  `/lib/systemd/system-shutdown/ls1046a-poweroff`); also referenced by
  `data/hooks/96-enable-services.chroot`. Landed in commit `board: add ls1046a-poweroff
  systemd-shutdown hook for Mono Gateway DK` (e691bbd).
- Rationale for the divergence: the sysfs-from-shutdown-hook path avoids **both** a kernel-config
  change **and** a DTS rebuild while achieving the identical physical power-cut. The ISO ships no
  `libgpiod`, but the hook sidesteps that by writing sysfs directly.

**[SPEC] What was NOT done (verification, 2026-06-09):**
- No `/gpio-poweroff` DTS node: `grep gpio-poweroff board/dtb/*.dts` → 0 hits.
- No `CONFIG_POWER_RESET*` fragment: `grep POWER_RESET kernel/common/kernel-config/` → 0 hits.
  The base `kernel/common/vyos-base/arm64/vyos_defconfig` even carries
  `# CONFIG_POWER_RESET_GPIO is not set`.
- Therefore §6.1 and §6.2 below are **historical proposal only** — do not treat them as the
  current build. §6.3 (AGENTS.md) and §6.4 (INSTALL.md) ARE present, but rewritten to describe
  the userspace hook, and `HWCTL.md` documents the operator-facing `poweroff` command.

**[NOTE]** This spec (and its non-HADS twin `specs/gpio-poweroff-spec.md`) is retained as the
design record. The remaining sections describe the *originally proposed* DTS approach; read them
with §0 in mind.

---

## 1. METADATA

**[SPEC]**
- Status: Superseded — the power-off goal shipped via the §9 userspace-hook alternative
  (`board/scripts/ls1046a-poweroff`), not the DTS driver proposed in §5/§6.
- Date: 2026-05-16 (proposal); 2026-06-09 (implementation reconciliation).
- Scope: kernel config + DTS + documentation (as proposed); shipped scope was a userspace
  `systemd-shutdown` hook + documentation only.

---

## 2. PROBLEM

**[BUG] `systemctl poweroff` does not physically power the board down**
- Symptom: the kernel prints `reboot: Power down` and halts, but the power rails stay live and the SoC keeps drawing current. Because `fan-pid` correctly drives `PWM=MAX` on its SIGTERM (a thermal failsafe documented in `AGENTS.md`), the operator is left with a stuck system, a screaming fan, and must pull the DC barrel jack to recover.
- Cause: LS1046A's TF-A implements PSCI `SYSTEM_RESET` (so `reboot` works) but NOT PSCI `SYSTEM_OFF`. The kernel has no registered `pm_power_off` handler, so `machine_power_off()` degenerates into `cpu_halt()` with IRQs off — instructions stop executing, power stays on.
- Fix: bind the mainline `gpio-poweroff` driver via DTS to the board's power-cut GPIO (gpio2 line 21) so `pm_power_off` is registered and asserted at the final shutdown step (see §6).

**[NOTE]**
The umount / loopback warnings that appear immediately before `reboot: Power down` (live medium is busy, `/dev/loop0` cannot be detached, etc.) are normal live-system shutdown noise and unrelated to this problem.

---

## 3. HARDWARE FACT

**[SPEC]**
- The Mono Gateway DK has an undocumented power-cut signal wired to gpio2 line 21, active-high. Driving the line high physically removes SoC power. There is no soft power-on signal — restoring power requires unplugging and re-plugging the DC barrel jack.

**[SPEC]**
GPIO ownership of gpio2 in the current DTS (`board/dtb/mono-gateway-dk.dts`):

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

- Line 21 is currently unclaimed → safe to take.

---

## 4. MANUAL VERIFICATION (NO REBUILD NEEDED)

**[SPEC]**
- Before committing the kernel/DTS change, the hardware behavior can be confirmed from any running VyOS image. The current ISO does NOT ship `libgpiod` / `gpioset`, so the legacy sysfs interface is the only available path:

```bash
# As root on the target (this will INSTANTLY cut power — board goes dark)
sudo sh -c 'echo 597 > /sys/class/gpio/export && \
            echo out > /sys/class/gpio/gpio597/direction && \
            echo 1   > /sys/class/gpio/gpio597/value'
```

- Pin number derivation: `gpiochip2` base on LS1046A is `576` (sfp-xfi0 TX_DISABLE is gpio2 line 14 = global 590 → base 590 − 14 = 576), so gpio2 line 21 = global GPIO `597`.
- The command does not return because the SoC loses power before the final `write()` syscall completes. To recover: unplug the DC barrel jack, wait ~3 s, plug it back in; the board cold-boots through U-Boot → kernel as normal.

**[SPEC]**
- If a future build adds `libgpiod` (`apt install gpiod`), the modern equivalent avoids hard-coding the base offset (but `gpiod` is not in the ISO and is not required for the production fix):
```bash
sudo gpioset gpiochip2 21=1
```

---

## 5. SOLUTION

**[SPEC]**
- Use the mainline `gpio-poweroff` driver (`drivers/power/reset/gpio-poweroff.c`) bound through DTS. The driver registers `pm_power_off` at probe time; the GPIO is NOT asserted at probe — only when `machine_power_off()` is finally invoked, after all filesystems are unmounted and after the `reboot: Power down` log line.

**[SPEC]**
This is strictly better than a userspace `systemd-shutdown` hook because:
- It fires at the very last moment of shutdown, in kernel context, with all userspace already torn down.
- It has no userspace dependency (no libgpiod in the shutdown chroot, no script ordering concerns).
- It is unaffected by `fan-pid`'s SIGTERM PWM=MAX — that has already fired, but power dies microseconds later so the elevated fan speed is irrelevant.
- `reboot` is on a different code path (PSCI `SYSTEM_RESET` via `psci_sys_reset`) and is NOT affected.
- It survives kexec emergency shutdown, `systemctl --force --force poweroff`, and panic-triggered power-off paths.

---

## 6. CHANGES

### 6.1 `board/dtb/mono-gateway-dk.dts`

**[SPEC]**
- Add a top-level node alongside the existing `/ { leds { … }; sfp_xfi0 { … }; sfp_xfi1 { … }; };` block:
```dts
/ {
    gpio-poweroff {
        compatible = "gpio-poweroff";
        gpios = <&gpio2 21 GPIO_ACTIVE_HIGH>;
        /* timeout-ms defaults to 3000; omit unless we measure a need */
    };
};
```
- Polarity `GPIO_ACTIVE_HIGH` matches the confirmed hardware behavior (driving line 21 to logical 1 cuts power). Do NOT add an `input` property — that is for buttons, not for a dedicated power-cut line.

### 6.2 Kernel config fragment

**[SPEC]**
- Append to the relevant fragment under `data/kernel-config/` (likely `ls1046a-board.config` — verify by checking which fragment already carries `CONFIG_GPIO_MPC8XXX=y`, the parent driver for `&gpio2`):
```
CONFIG_POWER_RESET=y
CONFIG_POWER_RESET_GPIO=y
```
- Both must be `=y`, not `=m`: the `gpio-poweroff` probe must run during boot so `pm_power_off` is registered before any shutdown, and modules are not loaded in early-shutdown contexts.

### 6.3 `AGENTS.md`

**[SPEC]**
- Add under *Critical Non-Obvious Rules*:
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

### 6.4 `INSTALL.md`

**[SPEC]**
- Add one operator-facing line under operations / troubleshooting:
```
- `poweroff` / `shutdown -h now` physically cuts SoC power on the Mono
  Gateway DK. To turn the board back on, unplug and re-plug the DC
  barrel jack — there is no soft power-on signal.
```

---

## 7. VERIFICATION AFTER REBUILD

**[SPEC]**
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
4. Operator-driven (cannot be automated in CI):
   - `sudo poweroff` → board goes fully dark within ~1 s. No fan-at-MAX hang.
   - Replug DC jack → cold boot through U-Boot proceeds normally.
   - `sudo reboot` → board reboots normally (regression check — must still work because it uses PSCI `SYSTEM_RESET`, not the new `pm_power_off` path).

---

## 8. RISKS AND ROLLBACK

**[SPEC]**
- Low risk. If line 21 is wired to something other than reported, the `gpio-poweroff` driver never *drives* the line during normal operation — it only registers `pm_power_off` and waits. The worst plausible outcome is "poweroff still doesn't work" (the current state). There is no scenario where the board powers off unexpectedly at boot or runtime from this change.
- Rollback: remove the DTS node and the two `CONFIG_POWER_RESET_*` lines. Both kernel symbols are otherwise unused by this build.

---

## 9. REJECTED ALTERNATIVES

**[SPEC]**
- `/lib/systemd/system-shutdown/` script invoking `gpioset` — works but runs from userspace before the kernel's final reboot syscall, depends on libgpiod being present in the shutdown environment, runs slightly earlier than the kernel `pm_power_off` hook, and offers zero advantage over the DTS path.
- Out-of-tree kernel module — pointless; `gpio-poweroff` has been mainline since 3.13.
- Input-mode `gpio-poweroff` — that is the button-input variant (`input;` property), wrong semantics for a dedicated power-cut signal.
- Wiring through the IMX2 watchdog as a "force-reboot" pseudo-poweroff — does not actually cut power, only reboots; doesn't solve the problem.