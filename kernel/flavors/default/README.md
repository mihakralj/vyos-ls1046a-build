# Flavor: `default`

Mainline-only LS1046A kernel build (no NXP SDK, no ASK fast-path, no VPP).

## Inputs

- **Kernel base:** `linux-6.18.28` (from `versions.lock` once wired in PR 3+).
- **Patches applied** (in order): see [INTEGRATION-PLAN §3.4](../../../plans/INTEGRATION-PLAN.md).
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

PR 1+2 (scaffold + verbatim absorption) — **done**. Default flavor is the **first to be green-built** in PR 3+ before ASK and VPP are wired.