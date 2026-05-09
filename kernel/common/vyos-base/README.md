# `release/vyos-base/` — vendored VyOS kernel configuration

Read-only snapshot of VyOS's kernel configuration artefacts, copied verbatim from
the VyOS `vyos-build` repo (`scripts/package-build/linux-kernel/config/`).

## Files

Re-vendored from `vyos-build@2413b092` (Phase-0 of the 6.18 migration). The
fragment count grew 7 → 25: VyOS 1.5+ split the previous monolithic
`11-encapsulation.config` into 13 protocol-specific files (50–62) and added
fragments for net-sched, MPLS, wireless, BPF tracing, modern crypto, and
debug knobs.

| Path | Origin |
|---|---|
| `arm64/vyos_defconfig` | `config/arm64/vyos_defconfig` — arm64 base defconfig |
| `00-filesystems.config` | `config/00-filesystems.config` |
| `01-executable-file-formats.config` | `config/01-executable-file-formats.config` |
| `02-module-signing.config` | `config/02-module-signing.config` |
| `10-networking.config` | `config/10-networking.config` |
| `12-wwan.config` | `config/12-wwan.config` |
| `13-net-sched.config` | `config/13-net-sched.config` |
| `14-mpls.config` | `config/14-mpls.config` |
| `15-wireless.config` | `config/15-wireless.config` |
| `20-netfilter.config` | `config/20-netfilter.config` |
| `30-pwru.config` | `config/30-pwru.config` |
| `40-crypto.config` | `config/40-crypto.config` |
| `50-bond.config`..`62-wireguard.config` | per-encapsulation splits replacing legacy `11-encapsulation.config` |
| `90-debug.config` | `config/90-debug.config` |

## How VyOS composes them

VyOS's `build-kernel.sh` runs something equivalent to:
```
merge_config.sh arch/arm64/configs/vyos_defconfig config/*.config
```
then `make olddefconfig`. The snippets override/extend the arm64 defconfig
with the features VyOS's CLI depends on at runtime.

## How ASK consumes them

`scripts/build-kernel.sh` (wet path) uses the same `merge_config.sh` chain,
then appends our LS1046A-specific delta `release/ask.config` last so it wins.
That way every VyOS-visible kernel knob matches stock VyOS 1.5, while we keep
the DPAA/FMan SDK drivers the LS1046A needs.

## Updating

Do not hand-edit. Re-vendor after bumping the pinned VyOS commit with:
```
./scripts/sync-vyos-base.sh     # (to be added — see lts_6.6_ls1046a roadmap)
```
