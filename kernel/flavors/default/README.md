# Flavor: `default`

Mainline-only LS1046A kernel build (no NXP SDK, no ASK fast-path, no VPP).

## Inputs

- **Kernel base:** `linux-6.18.28` (from `versions.lock` once wired in PR 3+).
- **Patches applied** (in order): see [plans/archive/INTEGRATION-PLAN.md](../../../plans/archive/INTEGRATION-PLAN.md) §3.4 (historical merge plan).
  1. `kernel/common/patches/vyos/*.patch` — VyOS deltas (linkstate attr, perf .deb).
  2. `kernel/common/patches/board/*.patch` — board-specific (currently empty; reserved 100..199 for board patches).
  3. `kernel/common/patches/fixes/*.patch` — flavor-agnostic 6.x repairs (lockdep mutex name, swphy, lp5812, libperf headers).
  4. `kernel/flavors/default/patches/*.patch` — none (this flavor adds nothing on top of common).

Total patch count: **6** (verified by `kernel/common/scripts/patch-health.sh --flavor default`).

## Driver stack

Mainline DPAA only:

| Symbol | Path |
|---|---|
| `CONFIG_FSL_FMAN` | `drivers/net/ethernet/freescale/fman/` |
| `CONFIG_FSL_DPAA_ETH` | `drivers/net/ethernet/freescale/dpaa/` |
| `CONFIG_FSL_BMAN` / `CONFIG_FSL_QMAN` | `drivers/soc/fsl/qbman/` |

No `sdk_fman/`, no `sdk_dpaa/`, no `staging/fsl_qbman/`. Userspace USDPAA / FMC / `dpa_ipsec` is **not** available.

## Defconfig

Sourced from `kernel/common/vyos-base/*.config` plus `kernel/flavors/default/kernel-config/*.config` (per-flavor overrides; currently empty).

`CONFIG_INET_IPSEC_OFFLOAD=n`, `CONFIG_CPE_FAST_PATH=n`, `CONFIG_ASK_FCI_NLKEY=n`, `CONFIG_NET_KEY=m` (mainline default).

## Validate locally

```bash
bash kernel/common/scripts/patch-health.sh --flavor default
```

Expected: `Pass: 6  Fail: 0`, no SDK section.

## Status

| PR | Deliverable | Status |
|---|---|---|
| PR 1+2 | scaffold + verbatim absorption from the archived kernel-build repo | **done** |
| PR 3   | migrate `data/kernel-{patches,config}` → `kernel/`, add `stage-kernel.sh` | **done** (commit `5cb81f3`) |
| PR 4   | wire `FLAVOR` switch into `bin/common.sh` + workflows | **done** (commits `b5e68ec`, `3bca73c`) |
| PR 5   | gate ASK-only CI steps on `FLAVOR=ask`; first green default build | **done** (commit `172b513`) |

### PR 5 validation (2026-05-09)

Local `kernel/common/scripts/integration-test.sh` (full run, no skips):

- 7 patches apply cleanly against `linux-6.18.28` (`Pass: 7 Fail: 0`, no SDK section).
- `stage-kernel.sh --flavor default` → exit 0, `.config` carries the four required mainline-DPAA symbols (`CONFIG_FSL_FMAN=y`, `CONFIG_FSL_DPAA_ETH=y`, `CONFIG_LEDS_LP5812`, `CONFIG_FSL_FMD_SHIM`).
- Native ARM64 build of `Image`: **112 s** on 32 vCPU, 32 524 800 bytes, validated as `Linux kernel ARM64 boot executable Image`.
- `vmlinux` Linux version banner matches `6.18.28` (the upstream-pinned kernel version from `vyos-build/data/defaults.toml`).
- Full integration test verdict: **Pass: 69 Fail: 0** (Checkpoints A through G).

Boot test on Mono Gateway hardware is the next step (requires CI dispatch + USB-boot the produced ISO).

### Building locally

```bash
bash kernel/common/scripts/patch-health.sh --flavor default          # ~2 s
SKIP_PATCH_HEALTH=0 SKIP_BUILD=0 \
    bash kernel/common/scripts/integration-test.sh                   # ~3 min on 32 vCPU
# or just stage + build by hand:
bash kernel/common/scripts/stage-kernel.sh --flavor default
( cd work/linux-6.18.28 && make ARCH=arm64 -j"$(nproc)" Image )
```

### CI

Default flavor is the workflow dropdown default (per `self-hosted-build.yml`'s `flavor: { default: 'default' }`). Dispatching `self-hosted-build.yml` with `flavor=default` runs the full ISO build through `bin/ci-build-packages.sh linux-kernel` (the upstream-tracked vanilla VyOS kernel path), skipping all ASK consume / setup-kernel-ask / SFP-SDK / ASK-userspace steps via the PR 5 FLAVOR gates.
