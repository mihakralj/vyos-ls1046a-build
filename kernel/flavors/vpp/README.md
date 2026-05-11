# Flavor: `vpp`

VPP / AF_XDP-oriented LS1046A build. This flavor keeps `eth0` under the
kernel as the management port and enables VyOS native VPP on `eth1` through
`eth4` via AF_XDP.

## Inputs

- **Kernel base:** `linux-6.18.28` (from `versions.lock` once wired in PR 3+).
- **Patches applied** (in order):
  1. `kernel/common/patches/vyos/*.patch` — 2 patches.
  2. `kernel/common/patches/board/*.patch` — 0 patches (reserved 100..199).
  3. `kernel/common/patches/fixes/*.patch` — 4 patches (093, 094, 095, 120).
  4. `kernel/flavors/vpp/patches/*.patch` — flavor-specific patches, if needed.
- **No SDK source drop.** This flavor uses mainline DPAA + AF_XDP, not NXP SDK.

## Driver stack

Mainline DPAA + AF_XDP socket family (no NXP SDK):

| Symbol | Path |
|---|---|
| `CONFIG_FSL_FMAN` | `drivers/net/ethernet/freescale/fman/` |
| `CONFIG_FSL_DPAA_ETH` | `drivers/net/ethernet/freescale/dpaa/` |
| `CONFIG_XDP_SOCKETS=y` | `net/xdp/` |
| `CONFIG_BPF_SYSCALL=y`, `CONFIG_BPF_JIT=y` | `kernel/bpf/` |

## Default VyOS config

`bin/ci-setup-vyos-build.sh` stages `data/config.boot.vpp` as
`/opt/vyatta/etc/config.boot.default` when `FLAVOR=vpp`. That config:

- keeps `eth0` on DHCP for management
- enables `set vpp settings interface eth1` through `eth4`
- sets `poll-sleep-usec 100` and `cpu-cores 1` for thermal safety
- allocates `hugepage-size 2M hugepage-count 512`
- rewrites update-check to `version-vpp.json` at build time

## Defconfig

Sourced from `kernel/common/vyos-base/*.config` plus `kernel/flavors/vpp/kernel-config/*.config`. The VPP flavor fragment restates the AF_XDP, BPF, hugepage, and mainline DPAA symbols required for this path.

## Validate locally

```bash
bash kernel/common/scripts/patch-health.sh --flavor vpp
```

Expected: all discovered common/default-compatible patches pass, no SDK section. The VPP flavor currently adds a config fragment but no kernel patch files of its own.

## CI verification

`bin/ci-verify-vpp-iso.sh` runs after ISO creation for `FLAVOR=vpp` and fails the build if VPP packages, the VPP default config, hugepage settings, or AF_XDP helper drop-ins are missing.