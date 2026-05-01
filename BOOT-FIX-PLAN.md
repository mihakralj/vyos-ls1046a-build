# Boot Fix Plan — clean TFTP-live, USB-live, eMMC-installed boot

Three independent boot paths, one shared symptom set on `2026.05.01-1913-rolling`
(consumer commit `1876cff1`, producer `kernel-6.6.135-ask51`).

After today's `console {}`-removal commit, boot reaches login + sshd is up + DHCP
applies on all 5 interfaces. **Three real defects remain**, none in the path we
just fixed. They affect all three boot media identically because the squashfs is
the same.

```mermaid
flowchart LR
    A[ISO build] --> B[USB live]
    A --> C[TFTP live]
    A --> D[install image → eMMC]
    B & C & D --> E[squashfs (identical bytes)]
    E --> F[Defect set]
    F -. all media .-> G((1 RTC/PAM clash))
    F -. all media .-> H((2 missing /dev/watchdog0))
    F -. all media .-> I((3 cosmetic/migration noise))
```

---

## Defect 1 — Future-dated /etc/shadow vs. RTC at boot   ★ blocks first commit

### Symptom

```
May 01 20:36:18 vyos vyos-configd[474]: PermissionError: [Errno 1] failed to run command:
  None tuned-adm profile network-latency
  returned: Operation timed out after waiting 600 seconds(s) ...
May 01 20:36:18 vyos vyos-configd[474]: Sending reply: ERROR_COMMIT_APPLY with output
May 01 20:36:21 vyos vyos-config[1321]: Configuration error
```

Earlier in journal:

```
Jun 26 14:59:44 vyos sudo[3443]: pam_unix(sudo:account): account root has password changed in future
Jun 26 14:59:46 vyos sudo[3568]: pam_unix(sudo:account): account root has password changed in future
... (10 sudo calls, all rejected)
```

### Root cause

The Mono Gateway's RTC reads **`Thu 2025-06-26 14:59:03 UTC`** at boot (no
battery-backed RTC sync; the value was set during an earlier OpenWrt session and
free-runs from that point). `/etc/shadow` for `root` has `lastchg = 20574` =
**`2026-05-01`** — the date the squashfs was built. PAM's `account` module sees
`lastchg > today` ⇒ rejects every `sudo`. Every Python helper in
`vyos-configd` that shells out via `sudo cmd(...)` therefore fails with
`PermissionError [Errno 1]`.

`tuned-adm` is the *first* sudo'd call after `system_watchdog`, so it's the one
that explodes. `tuned.service` itself is healthy — confirmed post-NTP-sync:

```
Active: active (running) since ... 14:59:51 UTC
$ tuned-adm active
Current active profile: network-latency
```

This applies identically to:
- **TFTP live** — RTC unmodified, runs straight through with stale clock.
- **USB live** — same.
- **eMMC installed** — same on every cold boot until NTP catches up.

### Fix

Two-line patch to `data/hooks/95-vyos-hostname.chroot` — clear `lastchg` on
`root` (and `vyos` while we're there) so PAM's "future" test cannot fire.
`chage -d 0` sets lastchg to "must change at next login" which is a special
sentinel `0`; better: `chage -d 1970-01-01` makes it always-in-the-past.

```diff
@@ -55,9 +55,18 @@ if getent passwd vyos >/dev/null 2>&1; then
   echo "vyos:${VYOS_HASH}" | chpasswd -e
-  chage -M 99999 -m 0 -W 7 -I -1 -E -1 vyos 2>/dev/null || true
+  # Clear ageing AND set lastchg to epoch so PAM 'account' never sees
+  # the squashfs build date as 'in the future' when the RTC is stale
+  # (LS1046A has no battery-backed RTC — boots at last-set time).
+  chage -d 1970-01-01 -M 99999 -m 0 -W 7 -I -1 -E -1 vyos 2>/dev/null || true
   passwd -u vyos 2>/dev/null || true
 else
   useradd -m -s /bin/vbash -G sudo,adm,frrvty,vyattacfg,dip -p "${VYOS_HASH}" vyos
 fi
+
+# Same treatment for root — vyos-configd uses sudo, and PAM's account stack
+# rejects sudo when /etc/shadow's lastchg > today. With a stale RTC this
+# kills the first 'commit' in vyos-router and produces 'Configuration error'.
+chage -d 1970-01-01 root 2>/dev/null || true
+passwd -u root 2>/dev/null || true
```

Belt-and-braces: also add a `vyos-router` drop-in that runs **before** any
commit, to force the system clock forward to at least the squashfs build date:

```ini
# /etc/systemd/system/vyos-router.service.d/clock-forward.conf
[Service]
ExecStartPre=/usr/local/bin/clock-forward
```

```bash
#!/bin/sh
# /usr/local/bin/clock-forward
# Force RTC forward to >= squashfs build date so PAM doesn't see future shadow.
build_epoch=$(stat -c %Y /usr/share/vyos/version.json 2>/dev/null || echo 0)
now_epoch=$(date +%s)
[ "$now_epoch" -lt "$build_epoch" ] && date -s "@$build_epoch" >/dev/null
```

Either approach alone is sufficient. The `chage` fix is preferable because it
also unblocks operator `sudo` sessions before NTP syncs; the `clock-forward`
helper is robust against future passwords appearing for any other reason.

### Touch points

- **Edit:** `/root/vyos-ls1046a-build/data/hooks/95-vyos-hostname.chroot`
  (root chage + chage -d epoch for vyos).
- **Optional add:** `/root/vyos-ls1046a-build/data/scripts/clock-forward` +
  drop-in installed by `bin/ci-setup-vyos-build.sh`.

Producer repo unaffected.

---

## Defect 2 — `system_watchdog` apply error: no `/dev/watchdog0`

### Symptom

```
Jun 26 14:59:49 vyos vyos-configd[474]:
  No watchdog device found at /dev/watchdog0 and no module configured.
  Use 'system watchdog module <name>' to load the required watchdog driver
  for your system.
Sending reply: ERROR_COMMIT with output
```

The script returns ERROR_COMMIT but does not raise → vyos-configd continues
to `system_option`. So this is non-fatal today, but it's a lit warning and the
LS1046A IMX2 watchdog *should* be present.

### Root cause

```
$ zcat /proc/config.gz | grep IMX2_WDT
# CONFIG_IMX2_WDT is not set
```

Producer-side:

```
$ grep -r IMX2_WDT /root/lts_6.6_ls1046a/release/vyos-base /root/lts_6.6_ls1046a/release/ask.config
(no match)
```

Consumer-side `data/kernel-config/ls1046a-watchdog.config:4` has `CONFIG_IMX2_WDT=y`,
but that fragment is no longer in the build path — the consumer now consumes
the **prebuilt producer kernel** (`bin/ci-consume-ask-kernel.sh` against the
`kernel-6.6.135-askN` release tag) and does not apply consumer-side kernel
config fragments. The fragment is dead code.

### Fix

Add to producer `release/ask.config` (or `release/vyos-base/02-watchdog.config`):

```
CONFIG_IMX2_WDT=y
```

LS1046A watchdog DT node already exists in `fsl-ls1046a.dtsi` with
`compatible = "fsl,ls1046a-wdt", "fsl,imx21-wdt"`. With the symbol enabled,
`/dev/watchdog0` will appear and `system_watchdog` will return SUCCESS instead
of ERROR_COMMIT.

This is a **producer-side change** → cut `kernel-6.6.135-ask52`, bump
`/root/vyos-ls1046a-build/data/ask-kernel.pin`. Per
`.clinerules/00-tag-discipline.md`: tag-only push, no branch+tag in same push.

### Touch points

- Producer `/root/lts_6.6_ls1046a/release/ask.config` — add `CONFIG_IMX2_WDT=y`.
- Producer git: `git tag kernel-6.6.135-ask52 && git push origin kernel-6.6.135-ask52`.
- Consumer `/root/vyos-ls1046a-build/data/ask-kernel.pin` — `kernel-6.6.135-ask52`.
- Consumer optional: delete `data/kernel-config/ls1046a-watchdog.config` (dead).

---

## Defect 3 — `host-name` script dispatched 12× per commit (cosmetic)

### Symptom

```
scripts_called: ['system_host-name', 'system_conntrack',
                 'interfaces_loopback_lo', 'interfaces_ethernet_eth1',
                 'interfaces_ethernet_eth4', 'interfaces_ethernet_eth0',
                 'interfaces_ethernet_eth3', 'interfaces_ethernet_eth2',
                 'system_host-name', 'system_host-name', 'system_host-name',
                 'system_host-name', 'system_host-name', 'system_host-name',
                 'system_host-name', 'system_host-name', 'system_host-name',
                 'system_host-name', 'system_login', ...]
```

`system_host-name.py` is dispatched **12 times** per first-boot commit (once
properly, then ten times more, then once again before `system_login`). Adds
~2 s but doesn't break anything.

### Root cause (conjecture, not chased — low priority)

VyOS commit-pipeline orders dependents of `system host-name` by walking the
dependency graph for each interface, and our `interfaces ethernet ethN` →
`system host-name` edge fires once per interface. Five eth interfaces ⇒ 5–10
duplicate dispatches.

### Fix

Don't bother. Cosmetic only.

---

## What is NOT a problem

| Boot log line | Verdict |
|---|---|
| `WARNING failed to get smmu node: FDT_ERR_NOTFOUND` | DTB has no SMMU node — harmless on LS1046A. |
| `Unknown kernel command line parameters "components noeject ..."` | live-boot's own params, passed to userspace per kernel cmdline contract. Harmless. |
| `bridge: filtering via arp/ip/ip6tables is no longer available by default` | `br_netfilter` lazy-load — VyOS pulls it in when bridge filtering is configured. |
| `cdx: loading out-of-tree module taints kernel` | Expected — ASK fast-path modules are signed but out-of-tree. |
| `MAJOR FM-PCD Error ... fm_cc.c:4830 MatchTableSet Memory Allocation Failed` | **Chain-2 PCD/MURAM exhaustion.** Separate ticket, fix is `fmc`/`fmlib` rebuild + `cdx_pcd.xml` key-count trim. NOT in this fix plan. |
| `cdx_module_init::start_dpa_app failed rc 65280 (continuing anyway)` | Same Chain-2 issue. |
| `could not generate DUID ... failed!` | Live boot has no stable machine-id — expected. |
| `WARNING: "system update-check url" has a valid URL but unable to retrieve data: SSLError` | Live boot has stale clock → cert validation fails. After clock-forward fix, this also clears. |
| `pam_unix(sudo:account): account root has password changed in future` | This **is** Defect 1's smoking gun, listed here so it isn't double-counted. |

---

## Per-medium boot status after fixes

| Medium | Result with Defects 1 & 2 fixed |
|---|---|
| **TFTP live** (`run dev_boot_live`) | Clean boot. configd commit succeeds end-to-end. ssh up, all 5 ifaces DHCP, watchdog0 present, fan-control thermal binding works, login banner clean. |
| **USB live** (`run usb_vyos`) | Identical to TFTP — same squashfs. |
| **eMMC installed** (`run vyos`) | First boot: identical to live but persists `/config/config.boot` from `/opt/vyatta/etc/config.boot.default`. Subsequent boots: stale RTC still possible after long power-off — `chage -d 1970-01-01` fix at install time + `clock-forward` drop-in keep working. |

---

## Order of operations

1. **Consumer** — apply Defect 1 chroot hook patch.
2. Commit, push to `origin/main`, dispatch `self-hosted-build.yml`. Pin remains `ask51`.
3. Boot-test the new ISO via TFTP-live. Verify `journalctl -u vyos-configd` shows zero `pam_unix(sudo:account)` lines and no `Configuration error`.
4. **Producer** — add `CONFIG_IMX2_WDT=y` to `release/ask.config`.
5. `git commit -am 'ask: enable CONFIG_IMX2_WDT for LS1046A IMX21 watchdog'` (no branch push).
6. `git tag kernel-6.6.135-ask52 && git push origin kernel-6.6.135-ask52`.
7. Wait ~22 min for producer release.
8. **Consumer** — bump `data/ask-kernel.pin` → `kernel-6.6.135-ask52`. Optionally `git rm data/kernel-config/ls1046a-watchdog.config` (dead).
9. Commit, push, dispatch build. Boot-test: `/dev/watchdog0` should now exist; `system_watchdog` should return SUCCESS.
10. `dd` final ISO to USB → `install image` → reboot from eMMC for the third boot path.

Both producer and consumer changes are minimal (defconfig one-liner + chroot
hook two-liner) and the boot path differences (TFTP/USB/eMMC) collapse to the
same squashfs once the squashfs commits cleanly.