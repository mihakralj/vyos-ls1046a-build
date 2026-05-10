# INTEGRATION PLAN: Single-Repo VyOS LS1046A with Three Flavors

**Status:** Draft for review
**Date:** 2026-05-09
**Scope:** Merge `kernel-ls1046a-build` (producer) into `vyos-ls1046a-build` (consumer) and introduce a `FLAVOR` switch with three values: `default | ask | vpp`. Retain existing CI patterns (self-hosted Cobalt 100 VM, no `workflow_dispatch` on `auto-build.yml`, idle-deallocator on the VM, `--no-backup-if-mismatch -p1` patches, `${RUNNER_NAME}` namespacing).

---

## 1. Goals and Non-Goals

### Goals
1. **Single repository** for VyOS ARM64 builds targeting the NXP LS1046A (Mono Gateway). Consolidates kernel patches, SDK source drops, ASK out-of-tree modules, ASK userspace, VyOS deltas, ISO assembly, and CI under one tree.
2. **Three flavors** selectable at build time via a `FLAVOR` workflow input / env var:
   - `default` — mainline DPAA only, no SDK, minimal board-bringup patches (boot, eMMC, console, fan, LED, mono board specifics).
   - `ask` — SDK FMan/DPAA/QBMan + ASK fast-path hooks + ASK userspace (cdx, fci, auto_bridge, dpa_app, fmc, fmlib, cmm).
   - `vpp` — SDK or mainline + VPP/AF_XDP-oriented patches, accel-ppp-ng, and userspace (specifics TBD; placeholder slot wired in CI).
3. **Patch-set composition is layered:** every flavor inherits a `common/` set; flavor-specific patches and config fragments stack on top.
4. **CI parity with current state:** self-hosted Cobalt 100 ARM64 VM, idle-deallocator pattern, runner namespacing on `${RUNNER_NAME}`, `--no-backup-if-mismatch -p1` patch application, no `workflow_dispatch` re-added to reusable workflows.
5. **No tag-format regressions on the producer side** (the askN tag discipline currently lives in `kernel-ls1046a-build`'s `.clinehooks/block-dual-ref-push.sh`). The merged repo replaces the per-tag GitHub Release contract with in-tree builds; consumer pinning becomes a no-op (the patches and SDK drops live in the same tree as the ISO build).

### Non-Goals (this plan, this iteration)
- **VPP flavor implementation details** beyond the patch/config slots and a documented placeholder. The current VPP integration in this repo (AF_XDP via `vyos-1x-010-vpp-platform-bus.patch`) is preserved; the `vpp` flavor initially differs from `default` only by enabling the accel-ppp-ng / VPP packages and AF_XDP-tuned defconfig fragments. No DPDK PMD work (RC#31 remains blocked).
- **Retiring `kernel-ls1046a-build`** in this PR. The producer repo stays as a frozen reference; the merged tree absorbs its content but the producer repo is not deleted.
- **Renaming `main` branch or introducing flavor branches.** Everything stays on `main`.
- **Changing the boot path, U-Boot env layout, or `vyos.env` mechanism.**

---

## 2. Single-Repo Directory Layout

The merged repo absorbs `kernel-ls1046a-build/release/`, `kernel-ls1046a-build/scripts/`, and `kernel-ls1046a-build/.clinerules/` (where applicable to producer-side rules) into a new top-level `kernel/` directory split into `common/` and per-flavor subtrees.

```text
vyos-ls1046a-build/
├── AGENTS.md                      # consumer + producer rules merged; new FLAVOR section
├── README.md                      # add FLAVOR matrix and selection guidance
├── INSTALL.md, UBOOT.md, ...      # existing docs unchanged
├── plans/
│   ├── INTEGRATION-PLAN.md        # this file
│   └── ...                        # existing planning docs
├── kernel/
│   ├── common/                    # applies to ALL flavors
│   │   ├── patches/
│   │   │   ├── vyos/              # 3 patches (was kernel-ls1046a-build/release/patches/vyos/)
│   │   │   ├── fixes/             # 6.6.y-specific repairs that are flavor-agnostic
│   │   │   │   ├── 093-netlink-name-L2FLOW-cb-mutex.patch
│   │   │   │   ├── 094-swphy-10g-fixed-link.patch    # used by ASK and VPP only — but harmless on default
│   │   │   │   ├── 095-leds-lp5812-register.patch
│   │   │   │   └── 110-...kasan-sanitize-off.patch    # SDK-only effect; harmless if SDK absent
│   │   │   └── board/             # NEW bucket: boot, eMMC, console, fan, LED, mono DTS
│   │   │       ├── 100-uart-ttyS0-console.patch
│   │   │       ├── 101-emmc-host-controller.patch    # MMC_SDHCI_OF_ESDHC bringup notes
│   │   │       ├── 102-mono-gateway-dts.patch        # if not handled via data/dtb/
│   │   │       └── 103-fan-emc2305-thermal.patch
│   │   ├── kernel-config/         # config fragments common to all flavors
│   │   │   ├── 00-board.config            # NR_CPUS=4, QORIQ_CPUFREQ=y, console, watchdog, QSPI
│   │   │   ├── 10-leds.config             # LP5812 + GPIO LEDs
│   │   │   ├── 20-thermal.config          # thermal zones + EMC2305
│   │   │   └── 30-storage.config          # MMC, eMMC, MTD, etc.
│   │   ├── vyos-base/             # VyOS defconfig fragments (was release/vyos-base/)
│   │   └── scripts/               # build-pipeline scripts (was kernel-ls1046a-build/scripts/)
│   │       ├── apply-to-tree.sh
│   │       ├── build-kernel.sh
│   │       ├── patch-health.sh
│   │       ├── normalize-patch.awk
│   │       └── common.sh
│   └── flavors/
│       ├── default/
│       │   ├── patches/           # (empty initially — board patches in common/board/)
│       │   ├── kernel-config/
│       │   │   └── ls1046a-mainline-dpaa.config   # FSL_FMAN/FSL_DPAA/FSL_DPAA_ETH/FSL_XGMAC_MDIO=y
│       │   └── README.md
│       ├── ask/
│       │   ├── patches/
│       │   │   ├── ask/                          # 8 patches (was release/patches/ask/)
│       │   │   └── fixes/                        # ASK-specific fixes only
│       │   │       ├── 097-ask-fci-nlkey-narrow-gate.patch
│       │   │       └── 102-arm64-ioremap-cache-ns-shim.patch
│       │   ├── sdk-sources/                      # 266 NXP SDK files (was release/patches/kernel/sdk-sources/)
│       │   ├── kernel-config/
│       │   │   └── ls1046a-ask.config            # SDK enable + ASK fast-path + CAAM stub
│       │   ├── ask.config                        # was release/ask.config
│       │   ├── manifest.json                     # was release/manifest.json
│       │   ├── oot-modules/                      # was release/oot-modules/{auto_bridge,cdx,fci,iptables-extensions}
│       │   ├── userspace-patches/                # was release/userspace-patches/{ppp,rp-pppoe}
│       │   └── README.md
│       └── vpp/
│           ├── patches/                          # AF_XDP / VPP-specific kernel patches if any
│           ├── kernel-config/
│           │   └── ls1046a-vpp.config            # AF_XDP, hugepages, mainline DPAA tunings
│           ├── userspace/                        # accel-ppp-ng build inputs, VPP startup configs
│           └── README.md
├── data/
│   ├── userspace/
│   │   ├── default/               # VyOS userspace deltas common to default
│   │   ├── ask/                   # ASK userspace artifacts (fmlib/fmc inputs, etc.)
│   │   └── vpp/                   # VPP startup.conf templates, AF_XDP scripts
│   ├── vyos-1x-*.patch            # unchanged (consumer-side VyOS patches)
│   ├── vyos-build-*.patch         # unchanged
│   ├── dtb/                       # mono-gateway-dk.dts + sdk-dtsi/
│   ├── kernel-config/             # consumer-side fragments (kept; merged with kernel/* via apply order)
│   ├── kernel-patches/            # consumer-side legacy kernel patches (audit + migrate to kernel/common/patches/)
│   ├── hooks/                     # live-build chroot hooks
│   ├── scripts/                   # postinstall, fancontrol, fman-port-name, etc.
│   ├── systemd/                   # service unit files
│   └── mok/                       # MOK.pem
├── bin/                           # consumer-side CI scripts (FLAVOR-aware after migration)
└── .github/workflows/
    ├── self-hosted-build.yml      # entry point: workflow_dispatch with `flavor` input
    └── auto-build.yml             # reusable: workflow_call with `flavor` input
```

### Migration of `data/kernel-patches/` and `data/kernel-config/`

These exist today on the consumer side and overlap conceptually with `kernel/common/patches/board/` and `kernel/common/kernel-config/`. The migration policy:

- **Anything board-bringup, flavor-agnostic** (boot, eMMC, console, fan, LED, mono DTS, INA234 power sensors, ttyS0 console, watchdog, QSPI partitions) → moves to `kernel/common/patches/board/` and `kernel/common/kernel-config/`.
- **Anything SDK-related or ASK-specific** (`ls1046a-ask.config`, `ls1046a-sdk.config`, `003-ask-kernel-hooks.patch`, `ask-nxp-sdk-sources.tar.gz`) → moves to `kernel/flavors/ask/` and is replaced by the producer's bucketed equivalents under `kernel/flavors/ask/patches/{ask,fixes}/` + `kernel/flavors/ask/sdk-sources/`. The tarball form is retired; verbatim source drops are committed as files (matches the producer's existing `release/patches/kernel/sdk-sources/` layout — 266 files, with `/* ASK-edit (askNN) */` markers grep-auditable).
- **Anything mainline-DPAA-specific** (`ls1046a-dpaa1.config`) → moves to `kernel/flavors/default/kernel-config/`.
- **Anything XDP/AF_XDP/VPP-specific** (`patch-dpaa-xdp-queue-index.py`, AF_XDP tunings) → moves to `kernel/flavors/vpp/`.

The audit of which existing file belongs where is a sub-task of the implementation phase.

---

## 3. The `FLAVOR` Switch

### 3.1. Workflow input

`self-hosted-build.yml`:

```yaml
on:
  workflow_dispatch:
    inputs:
      flavor:
        description: 'Build flavor: default | ask | vpp'
        required: true
        default: 'ask'
        type: choice
        options: [default, ask, vpp]
```

`auto-build.yml`:

```yaml
on:
  workflow_call:
    inputs:
      flavor:
        required: true
        type: string
```

### 3.2. Matrix option (deferred)

The plan keeps the workflow as a **single-flavor-per-run** dispatch. A matrix expansion (`strategy.matrix.flavor: [default, ask, vpp]`) is a follow-up once each flavor is independently green. This avoids burning self-hosted VM minutes on three parallel builds while the integration is still bedding in.

### 3.3. Script convention

Every CI script that consumes flavor-specific inputs reads `${FLAVOR}` from env. `auto-build.yml` exports it once near the top of the build job:

```yaml
env:
  FLAVOR: ${{ inputs.flavor }}
```

Scripts then use:

```bash
KERNEL_COMMON="kernel/common"
KERNEL_FLAVOR="kernel/flavors/${FLAVOR}"
```

### 3.4. Patch application order

`bin/ci-setup-kernel.sh` (FLAVOR-aware) applies in this fixed order:

1. `kernel/common/patches/vyos/*.patch`
2. `kernel/common/patches/board/*.patch`
3. `kernel/common/patches/fixes/*.patch`
4. `kernel/flavors/${FLAVOR}/patches/ask/*.patch`         (only present for `ask`)
5. `kernel/flavors/${FLAVOR}/patches/fixes/*.patch`        (flavor-specific fixes)
6. `kernel/flavors/${FLAVOR}/patches/*.patch`             (everything else under flavor root)
7. SDK source drops (only for `ask`): `cp -r kernel/flavors/ask/sdk-sources/* $KSRC/`

All patches use `patch --no-backup-if-mismatch -p1 -d $KSRC` (consistent with current `data/vyos-*.patch` application convention).

### 3.5. Defconfig fragment merge order

```bash
cat \
  kernel/common/vyos-base/arm64/vyos_defconfig \
  kernel/common/vyos-base/*.config \
  kernel/common/kernel-config/*.config \
  kernel/flavors/${FLAVOR}/kernel-config/*.config \
  > $KSRC/.config
```

The flavor fragment wins last. For ASK specifically, `kernel/flavors/ask/ask.config` (the LS1046A/DPAA delta) is appended after `ls1046a-ask.config` to preserve the current "ASK config wins last" invariant documented in `AGENTS.md` ("**SDK DPAA config must apply LAST**" rule). The `ci-setup-kernel.sh` script must explicitly re-append `kernel/flavors/ask/ask.config` last, the same way today's `ci-setup-kernel-ask.sh` re-applies SDK config after all fragments.

### 3.6. Userspace package selection

`bin/ci-build-packages.sh` consumes `${FLAVOR}` to decide which userspace packages to build:

| Flavor | accel-ppp-ng | ASK userspace (fmlib/fmc/dpa_app/cdx/fci/auto_bridge/cmm) | OOT kernel modules | iptables/QOSMARK |
|---|---|---|---|---|
| `default` | yes (already built) | no | no | no |
| `ask` | yes | yes (rebuilt from `mihakralj/ask-ls1046a-6.6` per existing `bin/ci-build-fmlib.sh`/`bin/ci-build-fmc.sh`/`bin/ci-build-ask-userspace.sh` flow) | yes (signed against in-tree kernel certs per `AGENTS.md` rule) | yes (patched iptables from `kernel/flavors/ask/userspace-patches/`) |
| `vpp` | yes | no | no | no |

The `97-ask-userspace.chroot` live-build hook is **conditional** on `${FLAVOR} == ask`; `ci-setup-vyos-build.sh` only copies it into `$HOOKS/` when building the ASK flavor. Same gating for `sfp-tx-enable-sdk.service`, `ask-conntrack-fix.service`, and the SDK-specific DTS overrides.

### 3.7. ISO artifact naming

Build artifacts get the flavor suffix:

```text
vyos-1.5-rolling-2026XXXX-LS1046A-arm64-default.iso
vyos-1.5-rolling-2026XXXX-LS1046A-arm64-ask.iso
vyos-1.5-rolling-2026XXXX-LS1046A-arm64-vpp.iso
```

`bin/ci-build-iso.sh` reads `${FLAVOR}` and appends the suffix to the output filename. The `version.json` file gains a `flavor` field; if multiple flavors are released in the same dispatch, the file is keyed per-flavor (deferred until matrix builds land).

---

## 4. Per-Flavor Definitions

### 4.1. `default` flavor

**Intent:** Boot a stock VyOS ISO on the Mono Gateway with mainline drivers only. No NXP SDK. No ASK. No VPP. Suitable for users who want vanilla VyOS on this hardware and don't need fast-path offload.

**Patch set:**
- `kernel/common/patches/vyos/*.patch` (3)
- `kernel/common/patches/board/*.patch` (boot, eMMC, console, fan, LED, mono DTS, INA234, watchdog, QSPI)
- `kernel/common/patches/fixes/*.patch` (flavor-agnostic 6.6.y repairs)
- No flavor-specific patches initially.

**Defconfig fragments:**
- `kernel/common/vyos-base/*` (VyOS upstream defconfig + standard fragments)
- `kernel/common/kernel-config/*.config` (board, leds, thermal, storage)
- `kernel/flavors/default/kernel-config/ls1046a-mainline-dpaa.config` (mainline DPAA: `FSL_FMAN=y`, `FSL_DPAA=y`, `FSL_DPAA_ETH=y`, `FSL_BMAN=y`, `FSL_QMAN=y`, `FSL_XGMAC_MDIO=y`, `MAXLINEAR_GPHY=y`)

**Userspace:** standard VyOS + accel-ppp-ng. No ASK, no VPP plugins.

**Validation criteria for first green build:**
- Boots on Mono Gateway hardware (USB live + eMMC install).
- All 5 NICs (eth0–eth4) appear via mainline DPAA.
- SFP+ link comes up with phylink + MAXLINEAR_GPHY (RJ45) and 10GBASE-R (SFP+).
- Console on `ttyS0`, fan controlled by EMC2305, LEDs functional.
- `vyos.env` boot-image selector works.

### 4.2. `ask` flavor

**Intent:** Today's ASK-enabled build. Drop-in equivalent of the current `kernel-ls1046a-build` `kernel-6.6.137-askN` artifact + the consumer-side ASK userspace integration that lives in `mihakralj/ask-ls1046a-6.6`.

**Patch set:** as listed in `kernel-ls1046a-build/README.md` Patch Inventory (vyos/3 + ask/8 + fixes/6 = 17 patches), plus `kernel/common/patches/board/*` and the consumer-side ASK kernel hooks if not yet absorbed into the SDK source tree.

**SDK sources:** 266 verbatim NXP SDK files under `kernel/flavors/ask/sdk-sources/`, with `/* ASK-edit (askNN) */` direct-edit markers preserved. Audit surface unchanged: `grep -rn 'ASK-edit' kernel/flavors/ask/sdk-sources/`.

**Defconfig fragments:** as today, plus the reference-aligned invariants:
- `CONFIG_NET_KEY=y`, `CONFIG_INET_IPSEC_OFFLOAD=n`, `CONFIG_CPE_FAST_PATH=y`, `CONFIG_ASK_FCI_NLKEY=y`.
- `kernel/flavors/ask/ask.config` re-appended LAST after all fragments.

**OOT modules:** cdx, fci, auto_bridge — built from `kernel/flavors/ask/oot-modules/` against in-tree kernel headers, signed with `$KSRC/scripts/sign-file sha512` per `MODULE_SIG_FORCE` requirement.

**Userspace:** fmlib + fmc rebuilt in CI from `github.com/nxp-qoriq/{fmlib,fmc}` at tag `lf-6.18.2-1.0.0` with mono patches applied (preserves the ABI-mismatch fix documented in `AGENTS.md`); dpa_app + cmm built from `mihakralj/ask-ls1046a-6.6` at the pinned `ask-userspace-audit-vN` tag.

**Validation criteria:** preserves both current `ask-check` chains (Chain 1 kernel-side: NETLINK_KEY=32 registers, `cmm.service` active; Chain 2 userspace-side: `dpa_app` PCD apply succeeds, BMan fragment buffer pool located).

**Patch-health invariant:** `Pass: 17  Fail: 0   0 SDK conflicts (266 files to install)` must remain after the move (the producer's `scripts/patch-health.sh` is moved to `kernel/common/scripts/patch-health.sh` and gains a `--flavor ask` flag).

### 4.3. `vpp` flavor

**Intent:** AF_XDP-accelerated VyOS with VPP managed via `set vpp settings`. Initially differs from `default` only by enabling the AF_XDP/hugepages defconfig and shipping VPP startup defaults; the actual VPP path is the existing `vyos-1x-010-vpp-platform-bus.patch` plus runtime configuration via VyOS native CLI.

**Patch set:** same as `default` initially. Future iterations may add VPP-specific kernel tunings.

**Defconfig fragments:**
- `kernel/common/*` (board + storage + thermal + leds)
- `kernel/flavors/default/kernel-config/ls1046a-mainline-dpaa.config` (VPP uses AF_XDP on top of mainline DPAA — there is no SDK requirement for VPP on this hardware)
- `kernel/flavors/vpp/kernel-config/ls1046a-vpp.config` (HUGETLB_PAGE, BPF_SYSCALL, XDP_SOCKETS, etc., plus the documented VPP-required configs)

**Userspace:** VPP + DPDK (with the AF_XDP PMD only — DPAA PMD is RC#31-blocked), accel-ppp-ng with VPP plugin built (deferred — currently VPP plugin is not built per `bin/ci-build-accel-ppp.sh` notes; flavor `vpp` is the future home for adding it).

**Validation criteria:** Boots, AF_XDP socket creation succeeds on eth3/eth4 with MTU≤3290, `set vpp settings interface eth3` works, ~3.5 Gbps measured AF_XDP throughput per existing baseline.

**Note:** The split between `default` and `vpp` is intentionally narrow at the start. The two flavors converge on kernel-level driver choice (mainline DPAA) and diverge only on (a) installed userspace packages and (b) defconfig tunings for AF_XDP/hugepages. As VPP-specific kernel patches accumulate (e.g., the `patch-dpaa-xdp-queue-index.py` that fixes the FQID/queue_index AF_XDP issue), they migrate from `data/kernel-patches/` to `kernel/flavors/vpp/patches/`.

---

## 5. CI Workflow Changes

### 5.1. `auto-build.yml` (reusable)

Add `flavor` input. Job steps re-organize:

```yaml
on:
  workflow_call:
    inputs:
      flavor:
        required: true
        type: string

jobs:
  build:
    runs-on: [self-hosted, vm-runner-2]
    env:
      FLAVOR: ${{ inputs.flavor }}
      RUNNER_NAMESPACED_DIR: /work/${{ runner.name }}/vyos-build
    steps:
      - uses: actions/checkout@v4
      - run: bin/ci-install-deps.sh
      - run: bin/ci-setup-kernel.sh           # FLAVOR-aware: picks common + flavor patches/configs
      - run: bin/ci-build-packages.sh         # FLAVOR-aware: gates ASK/VPP userspace
      - run: bin/ci-setup-vyos1x.sh
      - run: bin/ci-setup-vyos-build.sh       # FLAVOR-aware: gates ASK hooks/services
      - run: bin/ci-build-iso.sh              # FLAVOR-aware: appends -${FLAVOR} suffix
      - uses: actions/upload-artifact@v4
        with:
          name: vyos-${{ inputs.flavor }}-${{ github.sha }}
          path: build/*.iso
```

**Hard rules preserved:**
- No `workflow_dispatch:` on `auto-build.yml` (must remain reusable-only — re-adding it re-enables hosted-runner builds and burns Actions minutes).
- No `stop-vm` step (idle-deallocator on the VM owns deallocation).
- Runner namespacing on `${RUNNER_NAME}` for any fixed disk path.

### 5.2. `self-hosted-build.yml` (entry point)

Add `flavor` choice input; pass through to `auto-build.yml`:

```yaml
on:
  workflow_dispatch:
    inputs:
      flavor: { description: ..., required: true, default: 'ask', type: choice, options: [default, ask, vpp] }

jobs:
  start-vm:
    # unchanged: az vm start (idempotent)
  build:
    needs: start-vm
    uses: ./.github/workflows/auto-build.yml
    with:
      flavor: ${{ inputs.flavor }}
  # NO stop-vm job — idle-deallocator handles it
```

### 5.3. Producer workflow `build-and-release.yml` retirement

Once the merge is complete and the consumer build can produce all three flavors from a single dispatch, `kernel-ls1046a-build/.github/workflows/build-and-release.yml` is no longer the source of `linux-*.deb` for ASK. The consumer no longer downloads release artifacts via `gh release download`; it builds the kernel in-tree.

The producer repo is **frozen, not deleted**, in this iteration. Its tags remain valid pin targets for any external consumer that hasn't migrated. `kernel-ls1046a-build/AGENTS.md` gets a deprecation note pointing to this plan.

---

## 6. Documentation Updates

### 6.1. `AGENTS.md` additions

A new **"FLAVOR switch"** section documents:
- The three flavors and their intent.
- The patch / config / userspace inheritance order (common → flavor).
- The hard rules: where common patches end and flavor patches begin; how to decide which bucket a new patch belongs in.
- The audit surfaces: `grep -rn 'ASK-edit' kernel/flavors/ask/sdk-sources/` for direct SDK edits; `git log --grep '^audit-b' --oneline` in `mihakralj/ask-ls1046a-6.6` for ASK userspace audits.
- The `patch-health` invariant per flavor (ASK: `Pass: 17  Fail: 0   0 SDK conflicts (266 files to install)`; default + vpp: TBD on first green build).

A new **"Producer-absorbed rules"** section folds in (with attribution) the relevant rules from `kernel-ls1046a-build/.clinerules/`:
- `00-tag-discipline.md` — adapted: tag discipline now applies to flavor-suffixed release tags (`v1.5-rolling-2026XXXX-default`, `-ask`, `-vpp`), no longer to per-kernel `kernel-6.6.137-askN`.
- `10-patch-authoring.md` — patch authoring + hunk-validator rules preserved verbatim.
- `20-sdk-driver-rules.md` — preserved verbatim under the ASK flavor section.
- `30-kconfig-defconfig.md` — merged with existing kernel-config rules.
- `40-commit-style.md` — merged with consumer commit style; new prefixes added: `kernel-common:`, `flavor-default:`, `flavor-ask:`, `flavor-vpp:`.
- `50-thresholds-are-authoritative.md` — preserved verbatim under ASK invariants.

### 6.2. `README.md` additions

- A "Build flavors" section near the top, with the three-flavor matrix from §4.
- The `gh workflow run` command updated to show the `--field flavor=<value>` selection.
- The release-assets table extended with the per-flavor ISO names.
- The "What This Build Fixes" thirteen-fix table noted as applying to all flavors (board bringup is common); ASK and VPP additions documented separately.

### 6.3. Producer-side `kernel-ls1046a-build/AGENTS.md` and `README.md`

A short deprecation header pointing to:

> This repo's content has been merged into `vyos-ls1046a-build` under `kernel/flavors/ask/` (sdk-sources, patches, oot-modules, userspace-patches) and `kernel/common/` (vyos patches, scripts, vyos-base fragments). New ASK iterations land in `vyos-ls1046a-build` against the `ask` flavor. This repo is preserved as a frozen reference; tag pulls of `kernel-6.6.137-askN` for N ≤ <last-released> remain valid.

### 6.4. New per-flavor `README.md` files

Each `kernel/flavors/<flavor>/README.md` describes:
- What this flavor does and doesn't enable.
- Which patches and configs apply.
- Validation criteria / smoke tests.
- Known issues specific to the flavor.

---

## 7. Migration Sequence

The migration is broken into reviewable PRs, each independently green:

### PR 1 — Scaffolding (this plan + empty layout)
- Add `plans/INTEGRATION-PLAN.md` (this file).
- Create empty directories: `kernel/common/{patches/{vyos,board,fixes},kernel-config,vyos-base,scripts}`, `kernel/flavors/{default,ask,vpp}/{patches,kernel-config}`.
- Add `.gitkeep` files where needed.
- No CI changes yet; nothing built differently.

### PR 2 — Absorb `kernel-ls1046a-build` content (verbatim copy)
- `cp -r kernel-ls1046a-build/release/patches/vyos/* vyos-ls1046a-build/kernel/common/patches/vyos/`
- `cp -r kernel-ls1046a-build/release/patches/ask/* kernel/flavors/ask/patches/ask/`
- `cp -r kernel-ls1046a-build/release/patches/fixes/* kernel/flavors/ask/patches/fixes/` (then split out the flavor-agnostic fixes into `kernel/common/patches/fixes/` per §4.1)
- `cp -r kernel-ls1046a-build/release/patches/kernel/sdk-sources/* kernel/flavors/ask/sdk-sources/`
- `cp -r kernel-ls1046a-build/release/{ask.config,manifest.json,oot-modules,userspace-patches,vyos-base} kernel/flavors/ask/` (vyos-base goes to `kernel/common/vyos-base/` instead)
- `cp -r kernel-ls1046a-build/scripts/* kernel/common/scripts/`
- Verify `kernel/common/scripts/patch-health.sh --source kernel/common --flavor ask` reports `Pass: 17  Fail: 0   0 SDK conflicts (266 files to install)`.
- No CI changes yet; the new tree exists in parallel with the old `data/kernel-patches/` and consumer-side ASK paths.

### PR 3 — Consumer-side audit and migration
- Walk `data/kernel-patches/` and `data/kernel-config/`. For each file, decide: common-board, flavor-default, flavor-ask, or flavor-vpp. Move accordingly.
- Walk `data/scripts/`, `data/hooks/`, `data/systemd/`. Tag each as universal vs flavor-specific. Flavor-specific items get gated in `bin/ci-setup-vyos-build.sh` on `${FLAVOR}`.
- Walk `bin/ci-*.sh`. Identify scripts that hardcode ASK assumptions (`ci-build-fmlib.sh`, `ci-build-fmc.sh`, `ci-build-ask-userspace.sh`, `ci-setup-kernel-ask.sh`, `ci-setup-kernel-sdk.sh`). Either gate on `${FLAVOR}` or rename to `bin/flavors/ask/setup-kernel.sh` for clarity.
- Add `${FLAVOR}` to `bin/common.sh` (new file or augment existing); have all CI scripts source it.

### PR 4 — Wire the FLAVOR switch into CI
- Add `flavor` input to `auto-build.yml` and `self-hosted-build.yml`.
- Update all `bin/ci-*.sh` scripts to read `${FLAVOR}` and dispatch to the correct patch/config/userspace tree.
- First green build: `flavor=ask` (must reproduce the existing artifact byte-for-byte modulo build-time stamps).

### PR 5 — Validate `default` flavor
- First green `default` build.
- Boot test on Mono Gateway hardware.
- Document boot results in `kernel/flavors/default/README.md`.

### PR 6 — Validate `vpp` flavor
- First green `vpp` build.
- Boot test, AF_XDP smoke test on eth3.
- Document results in `kernel/flavors/vpp/README.md`.

### PR 7 — Producer deprecation
- Add deprecation header to `kernel-ls1046a-build/README.md` and `AGENTS.md`.
- Final tag on producer: `kernel-6.6.137-ask<final>-frozen`.
- Update consumer `AGENTS.md` to remove references to "pin against producer tag".

### PR 8 — (Optional) Workflow matrix
- Once all three flavors are independently green, add a matrix dispatch option to `self-hosted-build.yml` for parallel three-flavor releases.

---

## 8. Guardrails (do not violate)

These rules survive the merge and apply to the integrated repo:

1. **Branch:** `main` only. No flavor branches. No feature branches.
2. **No `workflow_dispatch:` on `auto-build.yml`.** Reusable-only. Re-adding it re-enables hosted-runner builds.
3. **No `stop-vm` step in any workflow.** The idle-deallocator on the Cobalt 100 VM owns deallocation.
4. **All fixed disk paths in CI scripts namespaced on `${RUNNER_NAME}`** (or live under `${GITHUB_WORKSPACE}`).
5. **Patches use `--no-backup-if-mismatch -p1`.**
6. **No comments inside VyOS config blocks** (`config.boot.default` parser breaks on `//` and `/* */` inside `{}`).
7. **DPAA1 configs are `=y`, never `=m`** (FMan needs early init before rootfs).
8. **`NR_CPUS=4`** (LS1046A has 4× Cortex-A72; SDK driver uses NR_CPUS to size TX FQ arrays — 256 triggers soft lockup).
9. **Console is `ttyS0`** (8250), not `ttyAMA0` (PL011).
10. **Boot method is `booti`** (not `bootefi` — DPAA1 reserved-memory OOMs GRUB).
11. **No auto-commit/push** without explicit user approval; stage and present for review.
12. **`patch-health` invariant for ASK flavor:** `Pass: 17  Fail: 0   0 SDK conflicts (266 files to install)`. Lowering the assertion to make a build pass is forbidden.
13. **Audit surfaces preserved:**
    - `grep -rn 'ASK-edit' kernel/flavors/ask/sdk-sources/` for direct SDK edits.
    - `git -C ask-ls1046a-6.6 log --grep '^audit-b' --oneline` for ASK userspace audits.
14. **Per-flavor patch buckets are ordered:** `vyos/` → `board/` → `fixes/` (common) → `ask/` (flavor) → `fixes/` (flavor) → `*` (flavor root). SDK source drops happen LAST, after all patches.
15. **`ASK config wins last` rule:** `kernel/flavors/ask/ask.config` is appended after `ls1046a-ask.config` so the SDK DPAA / mainline DPAA mutex resolves in favor of SDK on the ASK flavor.
16. **OOT modules signed against in-tree kernel certs** (`MODULE_SIG_FORCE=y`); never ship pre-built unsigned modules.
17. **Producer-side rules** (`.clinerules/00..60` in `kernel-ls1046a-build/`) folded into consumer `AGENTS.md` retain their authority for the `ask` flavor sub-tree.

---

## 9. Open Questions (resolve before PR 4)

1. **Patch numbering across buckets.** Current producer scheme: `0XX` per bucket (vyos: 001..003; ask: 010..080; fixes: 093..110). New common/board bucket needs a non-colliding range. Proposal: `100..199` for board (boot, eMMC, console, fan, LED, mono DTS).
2. **`fixes/` split criteria.** Is `094-swphy-10g-fixed-link.patch` truly common or ASK-only? It's used today by both SDK fixed-link and (potentially) mainline if mono-gateway-dk-sdk.dts ever lands in `default`. Proposal: keep in `kernel/common/patches/fixes/` and let the patch be a no-op when the relevant code isn't compiled.
3. **`kernel/common/scripts/patch-health.sh` flavor flag.** New `--flavor <name>` flag iterates the common + flavor patches. Default flavor for `--flavor` if unset: `ask` (preserves the current invariant assertion on producer-style runs).
4. **Per-flavor release tags.** Proposal: `v<vyos-version>-<date>-<flavor>` (e.g., `v1.5-rolling-2026.05.09-ask`). Producer-style `kernel-6.6.137-askN` retires as a public tag format.
5. **`mihakralj/ask-ls1046a-6.6` integration.** Currently consumed as a separate git checkout in `bin/ci-build-ask-userspace.sh`. Stays external for now; the merged repo continues to clone it at the pinned `ask-userspace-audit-vN` tag. Future option: vendor it as `kernel/flavors/ask/userspace-src/` to reduce external dependencies.
6. **VPP flavor scope.** Does `vpp` build the VPP plugin against accel-ppp-ng (currently disabled per `bin/ci-build-accel-ppp.sh`)? Proposal: yes — it's the natural home for the plugin once ARM64 build is sorted. Track in `kernel/flavors/vpp/README.md`.
7. **DTB selection per flavor.** `default` and `vpp` use mainline `mono-gateway-dk.dts`; `ask` uses `mono-gateway-dk-sdk.dts` with SDK port compatible strings. CI must select the right DTS per flavor in `bin/ci-setup-kernel.sh`.

---

## 10. Success Criteria

The integration is complete when:

1. `gh workflow run "VyOS LS1046A build (self-hosted)" --field flavor=ask` produces an ISO functionally equivalent to today's ASK build (cmm.service active, dpa_app PCD apply succeeds, ask-check passes both chains).
2. `--field flavor=default` produces a stock VyOS ISO that boots on the Mono Gateway with mainline DPAA, all 5 NICs up, console on ttyS0, fan controlled by EMC2305.
3. `--field flavor=vpp` produces a VPP-capable ISO; `set vpp settings interface eth3` succeeds; AF_XDP socket creation succeeds; throughput ≥ 3.5 Gbps.
4. `kernel/common/scripts/patch-health.sh --flavor ask` reports `Pass: 17  Fail: 0   0 SDK conflicts (266 files to install)`.
5. The `kernel-ls1046a-build` repo carries a deprecation header pointing to the merged tree.
6. `AGENTS.md` documents the FLAVOR switch and all preserved rules.
7. `README.md` documents the three-flavor build option.
8. No production CI burns hosted-runner Actions minutes (no `workflow_dispatch` on `auto-build.yml`; the self-hosted Cobalt 100 VM idle-deallocator runs cleanly between builds).

---

## 11. Out of Scope (future plans)

- Multi-board support (other LS1046A reference boards beyond the Mono Gateway).
- Multi-SoC support (LS1043A, LS2088A, etc.).
- DPDK DPAA PMD revival (RC#31 remains blocked; AF_XDP is the only path).
- Replacing live-build with a different ISO assembly tool.
- Moving off VyOS 1.5/1.6 to 2.x.
- Any change to the consumer/producer pinning model that affects external users of `kernel-ls1046a-build` releases.