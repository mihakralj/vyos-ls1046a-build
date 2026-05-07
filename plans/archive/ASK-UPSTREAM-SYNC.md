# ASK Repo Improvement Plan — Sync with upstream `mt-6.12.y`

Comparison of our local `ask-ls1046a-6.6/` fork against upstream
[`we-are-mono/ASK@mt-6.12.y`](https://github.com/we-are-mono/ASK/tree/mt-6.12.y),
and a proposal for what to adopt, keep, or diverge on.

## 1. Summary of the two repos

| Aspect | Local `ask-ls1046a-6.6` | Upstream `mt-6.12.y` |
|---|---|---|
| Target kernel | Linux 6.6 LTS (mainline) | Linux 6.12 (NXP `lf-6.12.49-2.2.0`) |
| FMAN microcode | ASK v210.10.1 | ASK v210.10.1 (same) |
| Core kernel modules | `cdx.ko`, `fci.ko`, `auto_bridge.ko` | `cdx.ko`, `fci.ko`, `auto_bridge.ko` (identical set) |
| Userspace | `fmc`, `cmm`, `dpa_app` + vendored `libnfnetlink` / `libnetfilter_conntrack` | `fmc`, `cmm`, `dpa_app` (libs fetched at build) |
| Build driver | Per-subdir `Makefile`, orchestrated by VyOS CI (`bin/ci-build-ask-userspace.sh`, `bin/ci-setup-kernel-ask.sh`) | Top-level `Makefile` with stamp-file pipeline, `build/setup.sh`, `build/sources.mk`, `build/toolchain.mk`; `KDIR=` override |
| Kernel patch layout | Single `patches/kernel/003-ask-kernel-hooks.patch` (75 files, manually rebased to 6.6) + in-tree Python patchers under `data/kernel-patches/` | Two patches: `002-mono-gateway-ask-kernel_linux_6_12.patch` (primary) + `999-layerscape-ask-kernel_linux_5_4_3_00_0.patch` (historical reference) |
| Config system | `config/` fragments + VyOS `data/kernel-config/ls1046a-ask.config` appended to defconfig | `config/` fragments + top-level `Kconfig` / `Kbuild.mk` for in-tree build |
| Docs | 10 markdown docs (analysis, code review, userspace plan, fixes, bootstrap, test, etc.) | Only `README.md` + `LICENSE` |
| CI integration | Fully integrated into VyOS ISO build (`auto-build.yml`) | None — manual build only |

## 2. What upstream has that we don't

1. **Top-level `Makefile` with stamp-file orchestration.** Targets: `all setup sources modules userspace kernel dist serve clean clean-all` plus per-component (`cdx fci auto_bridge fmc cmm dpa_app`). Our build is imperative shell scripts (`ci-build-ask-userspace.sh`) that re-do work on every CI run.
2. **`build/` helpers** (`setup.sh`, `sources.mk`, `toolchain.mk`) — clean separation of toolchain + source fetch. Our equivalent is inlined in `bin/ci-*.sh`.
3. **`Kconfig` + `Kbuild.mk` at root** — allows in-tree merge into a kernel source (so `cdx`/`fci`/`auto_bridge` can be selected via `make menuconfig` once symlinked under `drivers/`). We currently build them out-of-tree against `$KSRC`.
4. **Newer NXP base** — `002-mono-gateway-ask-kernel_linux_6_12.patch` is a freshly rebased patch against 6.12 that may include bugfixes our 6.6 port has re-invented. Touches the same files we touch (bridge, netfilter, xfrm, ipv4/6, sk_buff, etc.) — we should diff it against our `003-ask-kernel-hooks.patch`.
5. **Source fetch at build time** — `fmlib`/`fmc` pinned to tag `lf-6.12.49-2.2.0`, `libnfnetlink` / `libnetfilter_conntrack` downloaded as tarballs. We vendor everything in `data/ask-userspace/` (bloats the repo, but gives reproducibility without internet).

## 3. What we have that upstream doesn't

1. **6.6 LTS port.** Substantial engineering value: mainline 6.6 is the VyOS baseline. Upstream's 6.12 requires SDK kernel. Our `003-ask-kernel-hooks.patch` at 75 files is the authoritative artifact.
2. **VyOS ISO integration.** `data/hooks/97-ask-userspace.chroot`, `ask-modules-load.service`, `ask-conntrack-fix.service`, `fman-fq-qdisc.service`, udev rules (`10-fman-port-order.rules`), `vyos-postinstall` wiring.
3. **DPAA1 kernel fixes** packaged alongside ASK:
   - `4005-dpaa-eth-fix-soft-lockup-in-probe.patch` (NR_CPUS=4 lockup)
   - `4003-sfp-rollball-phylink-einval-fallback.patch`
   - `4004-swphy-support-10g-fixed-link-speed.patch`
   - `patch-dpaa-probe-fix.py`, `patch-dpaa-xdp-queue-index.py`, `patch-phylink.py`
   - `ask-cdx-bugfixes.patch` (our CDX-specific fixes)
4. **SDK driver injection.** `ask-nxp-sdk-sources.tar.gz` extracted into kernel tree by `ci-setup-kernel-ask.sh` — upstream assumes NXP SDK kernel is already the base, we graft it onto mainline.
5. **Rich documentation** — 10 `.md` design docs. Upstream has README only.
6. **Board bring-up specifics** — DTS (`mono-gateway-dk.dts`, `mono-gateway-dk-sdk.dts`), EMC2305 fancontrol, SFP TX-enable, INA234 hwmon, LP5812 LEDs, FMan port-order udev, CAAM IPsec offload configs.

## 4. Proposed improvements (prioritized)

### P0 — cross-check and harvest bugfixes from upstream 6.12 patch

**Goal:** ensure our 6.6 port isn't missing any bugfix that upstream has added since the 5.4 → 6.12 rebase.

Task list:
- [ ] Download `patches/kernel/002-mono-gateway-ask-kernel_linux_6_12.patch` from `mt-6.12.y`
- [ ] For each file both patches touch (bridge, netfilter, xfrm, ipv4/6, sk_buff, dev.c, skbuff.c, ip_output.c, xfrm_input.c, xfrm_output.c, etc.), diff the hunks
- [ ] Flag any hunks present upstream but not in our `003-ask-kernel-hooks.patch` — especially around `nf_conn`, `xfrm_state`, `ipsec_flow.c`, `comcerto_fp_netfilter.c`, CAAM changes
- [ ] Capture findings in `ask-ls1046a-6.6/ASK-UPSTREAM-DIFF.md`
- [ ] Forward-port any missing fixes into our patch

Risk: low. Effort: 1–2 days. Payoff: removes latent bugs.

### P1 — adopt upstream's `Makefile` / `build/` structure

**Goal:** replace the ad-hoc `ci-build-ask-userspace.sh` with a proper Makefile so local rebuilds are incremental and CI becomes a one-liner `make -C ask-ls1046a-6.6 all KDIR=$KSRC`.

Task list:
- [ ] Copy upstream `Makefile`, `build/setup.sh`, `build/sources.mk`, `build/toolchain.mk` into `ask-ls1046a-6.6/`
- [ ] Retarget the `sources` target from download-tarballs to symlink-to-`data/ask-userspace/` (keep vendored copies as the source of truth — reproducibility trumps freshness)
- [ ] Pin `fmlib`/`fmc` to `lf-6.6.52-2.2.0` (our tag) instead of `lf-6.12.49-2.2.0`
- [ ] Add `make dist` target that produces the `.debs` we currently ship in the ISO
- [ ] Convert `bin/ci-build-ask-userspace.sh` to: `make -C ask-ls1046a-6.6 userspace KDIR=$KSRC && cp .../*.deb $OUT`
- [ ] Keep `bin/ci-setup-kernel-ask.sh` as the kernel-tree grafting driver — it does things upstream doesn't need (SDK tar extraction)

Risk: medium (CI regressions). Effort: 2–3 days. Payoff: reproducible local builds, faster iteration, less shell.

### P2 — restructure kernel patch the upstream way

**Goal:** replace our single 75-file `003-ask-kernel-hooks.patch` with a 6.6-equivalent of `002-mono-gateway-ask-kernel_linux_6_12.patch` — i.e. a single top-level authored patch, not a merge of many upstream hunks.

Task list:
- [ ] Rename `003-ask-kernel-hooks.patch` → `002-mono-gateway-ask-kernel_linux_6_6.patch` for symmetry with upstream
- [ ] Split out the DPAA1-bringup patches (`4002`–`4005`) from the ASK patches — already done, keep as-is
- [ ] Consider carrying `999-layerscape-ask-kernel_linux_5_4_3_00_0.patch` in-tree as read-only historical reference (useful when rebasing)
- [ ] Add `patches/kernel/README.md` documenting patch order and ownership

Risk: trivial. Effort: 1 hour. Payoff: easier cross-reference with upstream.

### P3 — add `Kconfig` / `Kbuild.mk` for optional in-tree builds

**Goal:** allow developers to symlink `cdx/`, `fci/`, `auto_bridge/` into the kernel tree and build them via `make M=` or in-tree. Aids debugging with `gdb`/`kgdb` and `CONFIG_DEBUG_INFO`.

Task list:
- [ ] Copy upstream `Kconfig` + `Kbuild.mk` into `ask-ls1046a-6.6/`
- [ ] Verify symbols work against 6.6 (likely untouched — Kconfig is kernel-version agnostic)
- [ ] Document in `ASK-BUILD.md`

Risk: low. Effort: 2 hours. Payoff: better debuggability, less out-of-tree friction.

### P4 — consolidate docs, delete obsolete plans

Our repo has 10 markdown docs; several overlap or describe completed work.

Task list:
- [ ] Merge `ASK-USERSPACE-PLAN.md` into `ASK-USERSPACE.md` (done-plan → reference)
- [ ] Merge `ASK-BOOTSTRAP.md` into `ASK-USERSPACE.md` (both describe the same bring-up)
- [ ] Move `ASK-FIX-PLAN.md`, `ASK-6.12-VS-6.6-COMPARISON.md` to `archive/plans/` (tasks complete) — `ASK-6.12-VS-6.6-COMPARISON.md` is already in archive per `AGENTS.md`; delete the duplicate under `ask-ls1046a-6.6/`
- [ ] Keep `ASK-ANALYSIS.md`, `ASK-CODE-REVIEW.md`, `ASK-CODE-QUALITY.md`, `FIXES.md`, `ASK-TEST.md` — these are reference material
- [ ] Add a `ASK-UPSTREAM-DIFF.md` (result of P0)

Risk: zero. Effort: 1 hour.

### P5 — version-pin and document the upstream relationship

Task list:
- [ ] Add to top of `ask-ls1046a-6.6/README.md`:
  ```
  Forked from: github.com/we-are-mono/ASK @ <commit-sha>  (branch mt-6.12.y)
  Target kernel: 6.6 LTS (this fork) — upstream targets 6.12 NXP SDK
  Last synced: YYYY-MM-DD
  ```
- [ ] Record the upstream commit SHA we last rebased against
- [ ] Add `ASK-REBASE.md` with the procedure for pulling future upstream changes

Risk: zero. Effort: 30 min.

### P6 — evaluate jumping to 6.12 (long-term, optional)

Upstream is actively developed on 6.12 NXP SDK. VyOS mainline kernel is currently 6.6 LTS. If/when VyOS moves to 6.12:

- Our 6.6 port becomes a maintenance burden.
- The upstream 6.12 patch is the authoritative source — we should contribute our DPAA1-bringup and CAAM IPsec fixes upstream rather than re-rebase.

Task list (only when VyOS kernel track moves):
- [ ] Rebase `003-ask-kernel-hooks.patch` onto 6.12 → delete it and use `002-mono-gateway-ask-kernel_linux_6_12.patch` directly
- [ ] Upstream our DPAA1 fixes (`4003`, `4004`, `4005`) to NXP / mainline
- [ ] Contribute `patch-dpaa-xdp-queue-index.py` logic to NXP DPAA ETH (real bug)

Risk: N/A until VyOS moves. Effort: ~2 weeks when triggered.

## 5. What NOT to adopt from upstream

- **Download-at-build** for `libnfnetlink` / `libnetfilter_conntrack` — we vendor these intentionally (air-gapped builds, reproducibility, Debian `.deb` availability).
- **`lf-6.12.49-2.2.0` fmlib tag** — stay on `lf-6.6.52-2.2.0` while our kernel is 6.6.
- **`999-layerscape-ask-kernel_linux_5_4_3_00_0.patch`** — historical, do not apply.

## 6. Sequencing

Recommended order:
1. P5 (pin upstream reference) — 30 min, unblocks everything else
2. P0 (harvest bugfixes) — 1–2 days, highest value
3. P4 (docs cleanup) — 1 hour, piggybacks on P0 output
4. P2 (patch rename/README) — 1 hour
5. P3 (Kconfig/Kbuild.mk) — 2 hours, nice-to-have
6. P1 (Makefile restructure) — 2–3 days, biggest change — schedule separately
7. P6 — only when VyOS kernel moves

Total effort for P0–P5: ~1 week. P1 alone: ~3 days. P6: defer.

## 7. Addendum — observations from latest USB live boot (2026-04-16)

Boot log of build `6.6.133-vyos #1 SMP PREEMPT_DYNAMIC Thu Apr 16 17:19:28 UTC 2026` confirms the ASK stack works end-to-end on current hardware, but exposes several actionable items that tie directly into the upstream-sync plan above.

### 7.1 Confirmed working (no action)

- `OF: reserved mem: initialized node bman-fbpr / qman-fqd / qman-pfdr / usdpaa-mem` — SDK reserved-memory compatible strings correct.
- `Bman ver:0a02,02,01` + `Qman ver:0a01,03,02,01` + `Qman portal initialised, cpu 0..3` — BMan/QMan SDK stack fully up.
- `FM_Config/FM_Init FMan-Controller code (ver 210.10.1)` — ASK microcode loaded by U-Boot from QSPI.
- `fsl_dpa: Probed interface eth0..eth4` (5 MACs), `fsl_mac: FMan MAC address: e8:f6:d7:00:15:ff..00:16:03` — all five FMan ports up.
- `Maxlinear Ethernet GPY115C … Firmware Version: 8.111` — RJ45 PHYs bound to correct driver.
- `ipsec_flow_init` + `ASK fp_netfilter: hooks registered + conntrack force-enabled` — ASK kernel hooks from `003-ask-kernel-hooks.patch` initialized successfully.
- `8 INA234 power monitors` — kernel patch `4002-hwmon-ina2xx-add-INA234-support.patch` working.
- `sfp sfp-xfi0: module OEM SFP-10G-T` + `sfp sfp-xfi1: module OEM SFP-H10GB-CU1M` — both SFP+ cages detect modules.
- `caam 1700000.crypto: … job rings = 3, qi = 1` + `caam pkc algorithms registered` — CAAM IPsec offload ready.

### 7.2 Noisy debug printks from SDK `fsl_dpa` (new P0.1 — low-hanging fruit)

The boot log is dominated by SDK driver debug prints that should be demoted to `pr_debug` or removed. Per-interface repetition × 5 ports:

```
*********dpa_set_buffers_layout(677) internal buffer offset 0
dpaa_eth_priv_probe::bpid 0, count 640
DPAA_PROBE: bp_create done
DPAA_PROBE: get_channel=1025
DPAA_PROBE: add_channel done
DPAA_FQ_SETUP: enter
fsl_dpa … : No Qman software (affine) channels found
DPAA_FQ_SETUP: fq #1 type=2    (× 44 FQs per port)
DPAA_FQ_SETUP: main loop done, 44 FQs, egress_cnt=4/4
DPAA_PROBE: fq_setup done
DPAA_PROBE: cgr_init done
DPAA_PROBE: fqs_init done
fsl_dpa: fsl_dpa: Probed interface ethN
```

This is ~250 lines of spam per boot, slowing serial boot (~600ms on 115200) and obscuring real warnings.

- [ ] **Cross-check upstream `mt-6.12.y`**: examine whether `002-mono-gateway-ask-kernel_linux_6_12.patch` or the SDK driver sources already demote these to `pr_debug`. If yes, port those cleanups into our SDK tarball (`ask-nxp-sdk-sources.tar.gz`) — rolls into **P0** (harvest upstream fixes).
- [ ] Alternatively add a local patch under `data/kernel-patches/` that converts `printk`/`pr_info` → `pr_debug` in `drivers/net/ethernet/freescale/sdk_dpaa/dpaa_eth.c` and `drivers/net/ethernet/freescale/sdk_dpaa/dpaa_eth_base.c`.

### 7.3 Interface naming regression (new P0.2 — functional bug)

Observed rename at `init-premount` stage:
```
fsl_dpa … ethernet@9 e3: renamed from eth1
fsl_dpa … ethernet@8 e2: renamed from eth0
fsl_dpa … ethernet@1 e4: renamed from eth2
fsl_dpa … ethernet@4 e5: renamed from eth3
fsl_dpa … ethernet@5 e6: renamed from eth4
```

Physical ports become `e2..e6` instead of `eth0..eth4`. Per `AGENTS.md` rule "**Port order requires udev remap + VyOS hw-id**":
- Our udev rule `data/scripts/10-fman-port-order.rules` + helper script `data/scripts/fman-port-name` + `data/scripts/00-fman.link` (`NamePolicy=keep`) are supposed to map DT `cell-index` → physical ethN, but they are not effective during live boot's initramfs `init-premount` stage.
- On **installed** systems, VyOS `vyos_net_name` hw-id matching from `config.boot` takes precedence — the udev rule is primarily for live boot. The fact that live boot still shows `e2..e6` means either:
  1. The udev rule is being installed into the live-build chroot but not the initramfs, or
  2. The `.link` file's `NamePolicy=keep` is not being honored because systemd-udev inside the initramfs fires the `NAME=` predictable rule before the ASK rule, or
  3. `data/hooks/97-ask-userspace.chroot` / `10-fman-port-order.rules` install path is wrong.

Task list:
- [ ] Verify `10-fman-port-order.rules` is present in `/lib/udev/rules.d/` **inside the initramfs** (check `lsinitramfs` of `/boot/initrd.img-*`).
- [ ] Add the udev rule + `fman-port-name` helper to the initramfs via a `/etc/initramfs-tools/hooks/fman-port-name` copy hook.
- [ ] Validate `00-fman.link` is shipped into the initramfs `/etc/systemd/network/`.
- [ ] Confirm `config.boot.default` uses `hw-id` matching for the default config so installed systems are deterministic.

This is not an ASK upstream-sync issue — it's a VyOS integration gap. But it's a blocker for users of the live ISO. Rolls into its own work item separate from P0–P6 above.

### 7.4 Transient xHCI error (cosmetic)

```
xhci-hcd xhci-hcd.0.auto: WARNING: Host System Error
usb usb1-port1: couldn't allocate usb_device
```

Reported after xHCI enumeration; second pass succeeds (`hub 1-0:1.0: USB hub found` + mmcblk0 enumerated). Low priority — add to "Boot Diagnostics (Ignore These)" in `AGENTS.md` if reproducible.

### 7.5 Priority adjustment

Two new items feeding into existing priorities:

| New item | Priority | Rolls into |
|---|---|---|
| 7.2 Demote SDK debug printks | P0.1 | P0 (upstream harvest) |
| 7.3 FMan port-name udev not in initramfs | P-blocker (separate) | Not upstream — VyOS live-build integration |

Items 7.1/7.4 are informational only.
</content>
