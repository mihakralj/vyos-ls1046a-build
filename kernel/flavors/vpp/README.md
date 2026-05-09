# Flavor: `vpp`

VPP / AF_XDP-oriented LS1046A kernel build. **Status: scaffold only, deferred to last per user sequencing ("default → ask → vpp").**

## Inputs

- **Kernel base:** `linux-6.18.28` (from `versions.lock` once wired in PR 3+).
- **Patches applied** (in order):
  1. `kernel/common/patches/vyos/*.patch` — 2 patches.
  2. `kernel/common/patches/board/*.patch` — 0 patches (reserved 100..199).
  3. `kernel/common/patches/fixes/*.patch` — 4 patches (093, 094, 095, 120).
  4. `kernel/flavors/vpp/patches/*.patch` — to be defined in a future PR (AF_XDP optimizations, XDP socket helpers, etc.).
- **No SDK source drop.** This flavor uses mainline DPAA + AF_XDP, not NXP SDK.

## Driver stack

Mainline DPAA + AF_XDP socket family (no NXP SDK):

| Symbol | Path |
|---|---|
| `CONFIG_FSL_FMAN` | `drivers/net/ethernet/freescale/fman/` |
| `CONFIG_FSL_DPAA_ETH` | `drivers/net/ethernet/freescale/dpaa/` |
| `CONFIG_XDP_SOCKETS=y` | `net/xdp/` |
| `CONFIG_BPF_SYSCALL=y`, `CONFIG_BPF_JIT=y` | `kernel/bpf/` |

## Defconfig

Sourced from `kernel/common/vyos-base/*.config` plus `kernel/flavors/vpp/kernel-config/*.config` (per-flavor overrides; to be authored in a future PR — XDP / AF_XDP / busy-poll knobs).

## Validate locally

```bash
bash kernel/common/scripts/patch-health.sh --flavor vpp
```

Expected (today, before flavor patches are added): `Pass: 6  Fail: 0`, no SDK section. Equivalent to `--flavor default` until vpp-specific patches land.

## Status

PR 1+2 — scaffold only (empty `patches/` and `kernel-config/` dirs). VPP-specific work scheduled **after** both `default` and `ask` flavors are fully green per user instruction. Tracking issues to be filed in the consumer repo.