# MIGRATION PLAN — Mainline Linux 6.18 Uplift (Consumer-side companion)

> **Status:** PLAN (not started). Owner: consumer (`vyos-ls1046a-build`).
> **Producer-side master plan:** `kernel-ls1046a-build/plans/MIGRATION-PLAN-6.18.md`.
> **This document covers ONLY consumer-side work.** Read the producer plan
> first for context, strategic decisions, and the kernel-side phasing.

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
   with VyOS rolling. Phase 4 of the producer plan will pop these as
   regressions if they appear.
6. **Userspace ASK components (`cmm`, `dpa_app`, `fmc`, `fmlib`, `libcli`)
   under `ASK/` stay in-tree.** They build against userspace headers, not
   kernel headers; the 6.18 move is invisible to them.

## Phase A — Hold pattern until producer cuts `kernel-6.18.26-ask1`

**Goal:** keep producing buildable images on 6.6.137-ask42 while the
producer runs phases 0..4 of its plan. Default to "no consumer commits
that move kernel-versioned surface area until the producer is ready."

Tasks:

- [ ] Pause auto-build runs that consume the latest VyOS rolling
      kernel-version drift (i.e. anything that reads `defaults.toml`
      kernel_version without our self-pinning override).
- [ ] Document the producer's reconnaissance findings here once they
      land in `kernel-ls1046a-build/plans/PHASE-0-FINDINGS.md`.
- [ ] If any consumer-side change is required for phase 0/1/2/3, file
      it as a TODO entry below; do not implement yet.

## Phase B — Hardware boot test of `kernel-6.18.26-ask1`

**Triggered by:** producer publishing GitHub Release `kernel-6.18.26-ask1`.

Tasks:

- [ ] Verify the producer release exists and has all 9 expected assets:
      ```
      gh release view kernel-6.18.26-ask1 \
          --repo mihakralj/kernel-ls1046a-build --json assets
      ```
- [ ] Bump pin: `echo kernel-6.18.26-ask1 > data/ask-kernel.pin` (commit
      with `pin: kernel-6.18.26-ask1` subject; do not push yet — see step
      below).
- [ ] Local dry-run on the build VM (NOT a CI run): pull the new release
      with `bin/ci-consume-ask-kernel.sh` and inspect the resulting
      `packages/` directory:
  - [ ] `linux-image-6.18.26-vyos_*_arm64.deb` is present.
  - [ ] `ask-modules-6.18.26-vyos_*_arm64.deb` is present and contains
        `cdx.ko`, `fci.ko`, `auto_bridge.ko`.
  - [ ] `dpkg-deb --info linux-image-6.18.26-vyos_*_arm64.deb` shows the
        expected `Depends:` lines.
  - [ ] SHA256SUMS verifies clean.
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
        registered, no `EPROTONOSUPPORT` from `cmm`.
  - [ ] `ask-check` Chain 2 passes: `dpa_app applied PCD configuration`
        succeeds, `BMan fragment buffer pool located by CDX` succeeds,
        no `MURAM allocation failed` and no `copy_td_to_ccbase` oops.
  - [ ] All 5 ports pass ping + iperf3 to upstream router (eth0 1G, eth1
        1G, eth2 1G, eth3 10G SFP+, eth4 10G SFP+).
  - [ ] VPP starts cleanly via `systemctl start vpp`; AF_XDP RX/TX
        functional on the 10G ports.
- [ ] Throughput regression check: iperf3 single-stream and 8-stream
      against the 6.6.137-ask42 baseline numbers (which live in
      `vyos-ls1046a-build/CHANGELOG.md` after the producer publishes
      ask42's measured numbers — TODO: capture if not present).
      Target: within ±5%.

**Exit criteria:** all sub-tasks above green. If any fail, file as a
producer-side ticket (Chain 1 → producer; Chain 2 → consumer if it's
PCD/FMC/cdx_pcd-related, otherwise producer per
`.clinerules/05-workspace-layout.md` two-chain failure model).

## Phase C — VyOS upstream sync re-enable

**Goal:** un-pause the consumer's "track VyOS rolling" behaviour now
that we're aligned on 6.18.

Tasks:

- [ ] Confirm `vyos-1x` in upstream `current` is built against the same
      6.18.26 kernel ABI we ship. (If VyOS bumps to 6.18.27 mid-flight,
      we either bump producer in lockstep or apt pin holds us at 6.18.26
      with `vyos-1x` still resolving via ABI compat.)
- [ ] Re-enable any auto-build cron/dispatch that we paused in Phase A.
- [ ] Update `plans/CHANGELOG.md` with the migration entry (kernel
      version, ASK iteration, hardware-test PASS date, any caveats).
- [ ] Update `plans/PORTING.md` "currently shipping" line.

**Exit criteria:** `auto-build.yml` consumes the latest VyOS rolling
without manual intervention; consumer is back in steady-state.

## Phase D — Decommission 6.6.137 references (housekeeping)

**Optional — do once 6.18 is stable on hardware for ≥4 weeks.**

Tasks:

- [ ] Move `data/ask-kernel.pin` historical (6.6.137-ask42) reference
      to a code comment in `bin/ci-consume-ask-kernel.sh` "previously
      pinned at" rather than a `.bak` file.
- [ ] Audit `data/dtb/mono-gateway-dk.dts` for any `linux,boot-image`
      hints carrying `6.6.137`; remove.
- [ ] Search-and-replace remaining `6.6.137` literals in `plans/` docs
      (`PORTING.md`, `BOOT-PROCESS.md`, `DEV-LOOP.md`) with `6.18.26`
      where they describe current state, leaving historical references
      intact.
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

## Companion plan

Producer-side master plan: `kernel-ls1046a-build/plans/MIGRATION-PLAN-6.18.md`.

The two plans are kept in sync via cross-references (this doc's Phase B
mirrors the producer's Phase 5). Edit ONE side per change, then update
the cross-references on the other side as a follow-up commit in that
repo. **Never** stage producer + consumer changes in a single commit
(per `.clinerules/05-workspace-layout.md`).