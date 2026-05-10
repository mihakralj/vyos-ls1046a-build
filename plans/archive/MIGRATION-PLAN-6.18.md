# MIGRATION PLAN — Mainline Linux 6.18 Uplift (Consumer-side companion)

> **Status:** Phase A (hold pattern). Owner: consumer (`vyos-ls1046a-build`).
> **Producer-side master plan:** `kernel-ls1046a-build/plans/MIGRATION-PLAN-6.18.md`.
> **This document covers ONLY consumer-side work.** Read the producer plan
> first for context, strategic decisions, and the kernel-side phasing.
> **Last updated:** 2026-05-07 — full vyos repo review complete.

## Why this migration is happening

VyOS upstream `current` rolling repo bumped `linux-image-*-vyos` from
`6.6.137-vyos` to `6.18.26-vyos` on **2026-05-06 22:29Z**. The newest
versions of these out-of-tree kernel-module packages now hard-depend on
`linux-image-6.18.26-vyos`:

- `jool 4.1.15-1`
- `nat-rtsp 5.3-5-g5aeee02`
- `vyos-ipt-netflow 2.6-67-g6135551`
- `vyos-drivers-realtek-r8152 2.21.4-1`

Our consumer build run **25466790501** failed at `lb chroot_install-packages`
with an "unable to satisfy dependencies" error because our apt pin
(`vyos-build/data/live-build-config/archives/00-pin-ask-kernel.pref.chroot`)
correctly blocks `linux-image-6.18.26-vyos` from `packages.vyos.net`, but
does NOT block the OOT modules — leaving them with no kernel to install
against.

We follow VyOS rolling. The producer therefore moves to mainline 6.18.

## Strategic decisions (consumer-side)

1. **Pin file is the contract.** `data/ask-kernel.pin` will move from
   `kernel-6.6.137-ask42` → `kernel-6.18.26-ask1` in lockstep with the
   producer's first 6.18 release. No multi-pin or per-branch pin scheme.
2. **Apt pin pattern unchanged.** The existing `Pin: origin packages.vyos.net`
   block on `linux-image-*-vyos` matches both 6.6.137-vyos and 6.18.26-vyos,
   so no edit needed there.
3. **`bin/ci-setup-vyos-build.sh` self-pinning logic stays.** It reads the
   ASK kernel deb's filename and rewrites `vyos-build/data/defaults.toml`
   `kernel_version`. The regex
   `linux-image-([0-9]+\.[0-9]+\.[0-9]+)-vyos_.*` matches `6.18.26` just
   as well as `6.6.137`. No edit needed.
4. **DTS, U-Boot env, FMan PCD XML stay 6.18-agnostic.** The 6.18 kernel
   still consumes the same `mono-gw.dtb`, the same `cdx_pcd.xml`, the same
   `vyos.env`. None of these are kernel-version-tied.
5. **VPP / DPDK / AF_XDP integration: re-test, don't re-design.** AF_XDP
   socket API is stable; DPDK 24.x in Trixie supports 6.18; VPP 25.x ships
   with VyOS rolling. Phase B hardware test will surface any regressions.
6. **Userspace ASK components (`cmm`, `dpa_app`, `fmc`, `fmlib`, `libcli`)
   under `ASK/` stay in-tree.** They build against userspace headers, not
   kernel headers; the 6.18 move is invisible to them. `fmlib` is already
   pinned to `lf-6.18.2-1.0.0` tag — forward-compatible.
7. **`bin/ci-setup-kernel-ask.sh` requires targeted updates.** It injects
   a post-defconfig override block into `vyos-build/scripts/…/build-kernel.sh`
   referencing `6.6.137`-era symbol names; some of these need verification
   against the 6.18 Kconfig surface (see Phase A task list below).
8. **`bin/ci-consume-ask-kernel.sh` asset-filter is version-agnostic.** The
   `want()` glob table matches `linux-*.deb`, `ask-modules-*_arm64.deb`,
   `iptables_*_arm64.deb`, `ppp_*_arm64.deb`, `pppoe_*_arm64.deb` — none
   contain a hard-coded kernel version. No edit needed.

## Component-by-component 6.18 impact assessment

### Kernel defconfig injection (`bin/ci-setup-kernel-ask.sh`)

This script injects a `scripts/config --set-val` block after
`make vyos_defconfig` in the VyOS kernel build script. Current symbols
injected (relevant subset) and their 6.18 status:

| Symbol | Current value | 6.18 status | Action |
|---|---|---|---|
| `CONFIG_NR_CPUS` | `4` | No change — LS1046A has 4 cores | **keep** |
| `CONFIG_QORIQ_CPUFREQ` | `y` | Present in 6.18 (`drivers/cpufreq/`) | **keep** |
| `CONFIG_FSL_SDK_FMAN` | `y` | SDK overlay, not mainline | **keep** |
| `CONFIG_FSL_SDK_DPAA_ETH` | `y` | SDK overlay | **keep** |
| `CONFIG_FSL_SDK_DPA` | `y` | SDK overlay (fsl_qbman staging) | **keep** |
| `CONFIG_FSL_XGMAC_MDIO` | `y` | Present (NXP freescale) | **verify** |
| `CONFIG_MAXLINEAR_GPHY` | `y` | Present (GPY115C PHY) | **verify** |
| `CONFIG_IMX2_WDT` | `y` | Present | **keep** |
| `CONFIG_SPI_FSL_QUADSPI` | `y` | Present | **keep** |
| `CONFIG_STRICT_DEVMEM` | `n` | Present | **keep** (required for DPDK fmlib /dev/mem) |
| `CONFIG_IO_STRICT_DEVMEM` | `n` | Present | **keep** |
| `CONFIG_NET_SCH_FQ` | `y` | Present | **keep** |
| `CONFIG_SENSORS_INA2XX` | `y` | May have renamed to `INA2XX_CORE` in 6.18 | **verify** |
| `CONFIG_CPE_FAST_PATH` | `y` | ASK-specific — injected by producer SDK | **keep** |
| `CONFIG_INET_IPSEC_OFFLOAD` | `n` | Per producer D1 decision: stays `n` | **keep** |
| `CONFIG_NET_KEY` | `y` | **Critical** — must be `=y` (not `=m`) for NETLINK_KEY=32 | **keep** |
| `CONFIG_MODULE_SIG_FORCE` | `y` | Present | **keep** |
| `CONFIG_LEDS_LP5812` | `y` | Producer carries `fixes/095-leds-lp5812-register.patch` | **keep** |
| `CONFIG_LEDS_TRIGGER_NETDEV` | `y` | Present | **keep** |
| `CONFIG_KVM` | `y` | Present | **keep** |
| `CONFIG_VFIO` | `y` | Present | **keep** |
| `CONFIG_CMA` / `CONFIG_DMA_CMA` | `y` | Present | **keep** |
| `CONFIG_MODVERSIONS` | `y` | Present | **keep** (required for signed modules) |

**Symbol verification result (verified 2026-05-07 against `linux-6.18.26`):**
- `CONFIG_SENSORS_INA2XX` — ✅ present in `drivers/hwmon/Kconfig` (not renamed).
  Note: this symbol is NOT in the `scripts/config` injection block; it is
  enabled implicitly via `data/kernel-patches/4002-hwmon-ina2xx-add-INA234-support.patch`.
- `CONFIG_FSL_XGMAC_MDIO` — ✅ present in `drivers/net/ethernet/freescale/Kconfig`.
- `CONFIG_MAXLINEAR_GPHY` — ✅ present in `drivers/net/phy/Kconfig`.

All three symbols are stable in 6.18.26. No edits to `ci-setup-kernel-ask.sh`
required for these.

### ASK userspace (`ASK/cmm`, `ASK/dpa_app`, `ASK/fmlib`, `ASK/fmc`, `ASK/libcli`)

These components build against **userspace headers** and **NXP SDK FMan
ioctl headers** (`include/uapi/linux/fmd/`), NOT against kernel `.h` files
from the running kernel. The 6.18 kernel move is transparent to them
**provided the SDK FMan ioctl header set in the producer's kernel stays ABI-
compatible** — which it does because the SDK source tree under
`release/patches/kernel/sdk-sources/` is maintained in-place across the
6.6 → 6.18 transition (same files, same ioctl numbers, same struct layouts).

| Component | Build depends on | 6.18 risk |
|---|---|---|
| `fmlib` | `nxp-qoriq/fmlib @ lf-6.18.2-1.0.0` + ASK patches | **already on lf-6.18.2 tag** — no change |
| `fmc` | `nxp-qoriq/fmc` + `fmlib` headers | fmc uses fmlib userspace ABI only — **no change** |
| `dpa_app` | `fmlib` headers, `/dev/fm0` ioctl | Same ioctl ABI — **no change** |
| `cmm` | `libnetfilter_conntrack`, libc, NETLINK_KEY=32 socket | Kernel side: `CONFIG_NET_KEY=y` carries forward — **no change** |
| `libcli` | libc only | **no change** |

**One watch item:** `fmlib` is already pinned to `lf-6.18.2-1.0.0`. If the
producer's 6.18 SDK overlay diverges from that tag's header expectations,
`fmlib` build may need a patch to `ASK/fmlib/`. This is unlikely (the
ASK fmlib patch only adds `FM_ReadTimeStamp()`, `FM_PCD_Get_Sch_handle()`,
and a `bool shared` field — none touch ioctl numbers).

### OOT kernel modules (`cdx.ko`, `fci.ko`, `auto_bridge.ko`)

These are **producer-built** and shipped in the release tarball consumed by
`bin/ci-consume-ask-kernel.sh` as `ask-modules-6.18.26-vyos_*_arm64.deb`.
The consumer does not build them. No consumer-side action required beyond
verifying the `.deb` is present and installs cleanly.

### FMan PCD configuration (`data/fmc/`, `cdx_pcd.xml`)

The `cdx_pcd.xml` / `cdx_cfg_mono_gw.xml` configuration is consumed by
`dpa_app` at runtime via ioctl against the 6.18 kernel's FMan driver.
The 2026-05-06 fix (cdx_cfg_mono_gw.xml 10G ports + DTS vsp-window
restoration, commit `28a76ef4`) is kernel-version-agnostic.

**Watch item:** if the producer's 6.18 SDK resolves the MURAM exhaustion
(Chain 2 sub-trigger A) via EHASH DDR-offload (enabled at ask25+), the
consumer must ensure `fmc` is rebuilt against the updated `fmlib` that
understands `external="yes" aging="yes"` directives. This is a Phase B
validation item, not a pre-condition.

### DTS (`data/dtb/mono-gateway-dk-sdk.dts`)

The DTS is not kernel-version-tied. The 6.18 arm64 DT binding format
is backward-compatible for all nodes used (FMan, MDIO, SFP, I2C, GPIO,
MTD, eMMC). No change needed.

**Watch item:** verify `chosen { bootargs }` does not need a console
device rename between 6.6 and 6.18 — `ttyS0` is stable on LS1046A.

### vyos-1x patches (`bin/ci-setup-vyos1x.sh`)

The consumer applies a set of patches to `vyos-1x` before the ISO build.
These patches are against VyOS Python/CLI code, not kernel code, and are
kernel-version-agnostic. No change needed.

### U-Boot env / grub / vyos-postinstall

`/boot/vyos.env`, `grub.py` patches, `vyos-postinstall` are all in
Python/shell and operate on file paths, not kernel ABIs. The 6.18 kernel
package name change (`6.6.137-vyos` → `6.18.26-vyos`) is handled entirely
by the `ci-setup-vyos-build.sh` self-pinning logic and `ci-consume-ask-kernel.sh`
asset filter — no other consumer files reference the literal version.

## Phase A — Hold pattern until producer cuts `kernel-6.18.26-ask1`

**Goal:** keep producing buildable images on 6.6.137-ask42 while the
producer runs phases 0..4 of its plan. Default to "no consumer commits
that move kernel-versioned surface area until the producer is ready."

Tasks:

- [ ] Pause auto-build runs that consume the latest VyOS rolling
      kernel-version drift (i.e. anything that reads `defaults.toml`
      `kernel_version` without our self-pinning override).
- [x] **Verify `bin/ci-setup-kernel-ask.sh` symbol set against 6.18 Kconfig**
      (verified 2026-05-07 against extracted `work/linux-6.18.26` tree):
      `SENSORS_INA2XX`, `FSL_XGMAC_MDIO`, `MAXLINEAR_GPHY` all present
      unchanged. No script edits required for these three symbols.
- [ ] **Confirm `fmlib @ lf-6.18.2-1.0.0` ABI compatibility** with the
      producer's 6.18 SDK overlay. Run `bin/ci-build-fmlib.sh` dry-run
      in the build container and confirm it compiles without errors against
      the producer's `include/uapi/linux/fmd/` headers from the release
      tarball.
- [ ] Document the producer's reconnaissance findings here once they
      land in `kernel-ls1046a-build/plans/PHASE-0-FINDINGS.md`.
      Current snapshot (from producer Phase 0, 2026-05-07):
      - VyOS rolling carries only 2 in-tree patches (refreshed for 6.18.x).
        Both adopted by producer. `002-vyos-inotify-stackable-filesystems.patch` dropped.
      - VyOS fragment count: 21 (producer re-vendors `release/vyos-base/`).
      - Producer Phase 0.D TBDs (3-way overlap table) still in progress.
- [ ] If producer drops `050-ask-conntrack-offload.patch` per D2 (flowtable
      substitution), assess consumer-side impact:
      - `cmm` registers conntrack offload rules via `fci.ko`. With the patch
        deleted, the `fp_info` extension on `nf_conn` is gone. Verify
        whether `cmm`'s conntrack path survives or needs a consumer-side
        `ASK/cmm/` edit. File as TODO if edit needed.
- [ ] If producer rewrites `040-ask-xfrm-ipsec-offload.patch` per D1
      (XFRM_OFFLOAD provider), assess consumer-side impact:
      - `cdx_dpa_ipsec.c` currently calls legacy hooks. The producer handles
        this in Phase 3 (OOT module re-port). Consumer-side: `dpa_app` and
        `fmc` IPsec policy paths use `libfm_ext.a` (from `fmlib`) which
        operates via ioctl — these are not affected by xfrm API changes.
        No consumer-side edit expected, but verify `cmm`'s IPsec setup path
        in `ASK/cmm/src/cmmd_ipsec.c` does not reference `fp_ipsec_state`.

## Phase B — Hardware boot test of `kernel-6.18.26-ask1`

**Triggered by:** producer publishing GitHub Release `kernel-6.18.26-ask1`.

Tasks:

- [ ] Verify the producer release exists and has all expected assets:
      ```bash
      gh release view kernel-6.18.26-ask1 \
          --repo mihakralj/kernel-ls1046a-build --json assets \
          | jq '.assets[].name'
      ```
      Expected: `linux-image-6.18.26-vyos_*_arm64.deb`,
      `linux-headers-6.18.26-vyos_*_arm64.deb`,
      `ask-modules-6.18.26-vyos_*_arm64.deb`,
      iptables debs, ppp debs, `SHA256SUMS`, `manifest.json`.
- [ ] Bump pin: `echo kernel-6.18.26-ask1 > data/ask-kernel.pin` (commit
      with `pin: kernel-6.18.26-ask1` subject; do not push yet — see step
      below).
- [ ] Local dry-run on the build VM (NOT a CI run): pull the new release
      with `bin/ci-consume-ask-kernel.sh` and inspect the resulting
      `packages/` directory:
  - [ ] `linux-image-6.18.26-vyos_*_arm64.deb` is present.
  - [ ] `ask-modules-6.18.26-vyos_*_arm64.deb` is present and contains
        `cdx.ko`, `fci.ko`, `auto_bridge.ko` (verify with
        `dpkg-deb -c ask-modules-6.18.26-vyos_*_arm64.deb | grep '\.ko'`).
  - [ ] `dpkg-deb --info linux-image-6.18.26-vyos_*_arm64.deb` shows the
        expected `Depends:` lines.
  - [ ] SHA256SUMS verifies clean.
- [ ] Verify `bin/ci-setup-kernel-ask.sh` symbol injection still lands
      correctly: extract `build-kernel.sh` from the dry-run workspace and
      grep for the injected block (`ASK.*Forcing critical kernel configs`).
- [ ] Push pin commit to `main`. CI dispatches `auto-build.yml`.
- [ ] When auto-build completes, smoke-test the resulting ISO in
      `qemu-system-aarch64` for boot-to-login (cosmetic check; FMan/DPAA
      are silicon-only so this only validates "kernel boots and userspace
      lives").
- [ ] Hardware test on Mono Gateway (real silicon):
  - [ ] DTB loads (U-Boot console: `Booting kernel from Legacy Image…`).
  - [ ] Console output reaches login prompt on `ttyS0` @ 115200.
  - [ ] `ip link` shows eth0..eth4 (all 5 ports recognised).
  - [ ] `ask-check` Chain 1 passes: `cmm.service active`, NETLINK_KEY=32
        registered (`cat /proc/net/netlink | awk '{print $2}' | sort -u`
        includes `32`), no `EPROTONOSUPPORT` from `cmm`.
  - [ ] `ask-check` Chain 2 passes: `dpa_app applied PCD configuration`
        succeeds, `BMan fragment buffer pool located by CDX` succeeds,
        no `MURAM allocation failed` and no `copy_td_to_ccbase` oops.
  - [ ] All 5 ports pass ping + iperf3 to upstream router (eth0..eth2 1G,
        eth3..eth4 10G SFP+).
  - [ ] SFP+ modules detected (10GBASE-T rollball: allow up to 17 min
        for link-up on Rollball-type modules if used).
  - [ ] `dmesg` shows `INA234` sensor readings (power/voltage monitors);
        confirms `CONFIG_SENSORS_INA2XX` resolved correctly.
  - [ ] LED daemon operational (LP5812 `lp5812-1`/`lp5812-2` reachable via
        `ls /sys/class/leds/`).
  - [ ] VPP starts cleanly via `systemctl start vpp`; AF_XDP RX/TX
        functional on the 10G ports.
  - [ ] `CONFIG_NR_CPUS=4` confirmed: `nproc` returns 4; no soft lockup
        in dmesg from DPAA TX FQ oversizing.
  - [ ] CPU frequency: `cat /sys/devices/system/cpu/cpu0/cpufreq/scaling_cur_freq`
        reports > 700 MHz (confirms `QORIQ_CPUFREQ=y` active).
- [ ] Throughput regression check: iperf3 single-stream and 8-stream
      against the 6.6.137-ask42 baseline numbers (see `plans/CHANGELOG.md`
      or capture fresh baseline if not present). Target: within ±5%.
- [ ] Conntrack offload smoke-test: run `cmm add route` and verify
      conntrack entries appear in `conntrack -L`; test throughput through
      the fast-path. (This validates the D2 flowtable substitution if
      it landed in the producer.)

**Exit criteria:** all sub-tasks above green. If any fail, route per the
two-chain failure model:
- Chain 1 failure (NETLINK_KEY=32, `cmm.service`, SDK driver probe) →
  file against producer (`kernel-ls1046a-build`).
- Chain 2 failure (MURAM, PCD config, CDX BMan pool, fmc/fmlib) →
  consumer (`vyos-ls1046a-build` `ASK/` or `data/fmc/`) if it is
  PCD/XML-related; otherwise producer per
  `.clinerules/05-workspace-layout.md`.

## Phase C — VyOS upstream sync re-enable

**Goal:** un-pause the consumer's "track VyOS rolling" behaviour now
that we're aligned on 6.18.

Tasks:

- [ ] Confirm `vyos-1x` in upstream `current` is built against the same
      6.18.26 kernel ABI we ship. (If VyOS bumps to 6.18.27 mid-flight,
      we either bump producer in lockstep or apt pin holds us at 6.18.26
      with `vyos-1x` still resolving via ABI compat.)
- [ ] Confirm `jool`, `nat-rtsp`, `vyos-ipt-netflow`, `vyos-drivers-realtek-r8152`
      (the four packages that triggered this migration) now install cleanly
      in the 6.18.26 chroot.
- [ ] Re-enable any auto-build cron/dispatch that we paused in Phase A.
- [ ] Update `plans/CHANGELOG.md` with the migration entry (kernel
      version, ASK iteration, hardware-test PASS date, any caveats).
- [ ] Update `plans/PORTING.md` "currently shipping" and "kernel config
      additions" sections: replace `6.6.137` → `6.18.26` where describing
      current state.

**Exit criteria:** `auto-build.yml` consumes the latest VyOS rolling
without manual intervention; consumer is back in steady-state.

## Phase D — Decommission 6.6.137 references (housekeeping)

**Optional — do once 6.18 is stable on hardware for ≥4 weeks.**

Tasks:

- [ ] Move `data/ask-kernel.pin` historical (6.6.137-ask42) reference
      to a code comment in `bin/ci-consume-ask-kernel.sh` "previously
      pinned at" rather than a `.bak` file.
- [ ] Audit `data/dtb/mono-gateway-dk.dts` and `data/dtb/mono-gateway-dk-sdk.dts`
      for any `linux,boot-image` hints carrying `6.6.137`; remove.
- [ ] Search-and-replace remaining `6.6.137` literals in `plans/` docs
      (`PORTING.md`, `BOOT-PROCESS.md`, `DEV-LOOP.md`) with `6.18.26`
      where they describe current state, leaving historical references
      intact.
- [x] Remove the explicit `6.6.137` version string from the echo block
      at the bottom of `bin/ci-setup-kernel-ask.sh` — done 2026-05-07,
      replaced with `<VERSION>` placeholder.
- [ ] Producer-side: the `lts-6.6-ls1046a` branch is fallback-only.
      Consumer never re-pins to it unless a 6.18 regression we can't
      reproduce blocks the consumer for >48h.

**Exit criteria:** `git grep -E '6\.6\.13[57]'` in consumer returns
only historical/changelog hits.

## What we are NOT doing (consumer-side non-goals)

1. We are **not** maintaining a parallel `data/ask-kernel-6.6.pin` for
   anyone wanting to stay on 6.6. The pin is single-valued; rollback
   means temporarily reverting the pin commit, not branching the file.
2. We are **not** changing the apt pin pattern. `linux-image-*-vyos`
   from `packages.vyos.net` stays at Pin-Priority -1 forever (this
   is the structural mechanism that makes `packages.chroot/` win).
3. We are **not** splitting `auto-build.yml` per kernel branch. One
   workflow, one build path, pin file is the only switch.
4. We are **not** re-deriving `cdx_pcd.xml` for 6.18. The consumer-side
   2026-05-06 fix (cdx_cfg_mono_gw.xml 10G ports + DTS vsp-window
   restoration in commit `28a76ef4`) is kernel-version-agnostic and
   carries forward.
5. We are **not** building VPP/DPDK from source as part of this
   migration. Trixie packages are sufficient. If they regress, that's
   a Trixie issue, not a 6.18 issue.
6. We are **not** rebuilding `fmlib`/`fmc`/`cmm`/`dpa_app` from source
   during the migration unless the Phase B hardware test reveals an ABI
   mismatch. These components are already pinned to compatible tags and
   carry forward unchanged.

## Rollback procedure

If Phase B hardware test is fatal and cannot be unblocked within 48h:

1. `echo kernel-6.6.137-ask42 > data/ask-kernel.pin`
2. Revert the pin commit: `git revert HEAD` (single commit — pin bump
   was isolated per consumer commit discipline).
3. Push the revert to `main`.
4. File a producer-side issue with the Chain 1 or Chain 2 symptom log.
5. Do **not** push a `lts-6.6-ls1046a` re-pin to the producer unless
   the block lasts >48h and is a confirmed producer-side regression.

## Companion plan

Producer-side master plan: `kernel-ls1046a-build/plans/MIGRATION-PLAN-6.18.md`.

The two plans are kept in sync via cross-references (this doc's Phase B
mirrors the producer's Phase 5). Edit ONE side per change, then update
the cross-references on the other side as a follow-up commit in that
repo. **Never** stage producer + consumer changes in a single commit
(per `.clinerules/05-workspace-layout.md`).