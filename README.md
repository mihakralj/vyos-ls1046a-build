[![VyOS LS1046A build](https://github.com/mihakralj/vyos-ls1046a-build/actions/workflows/auto-build.yml/badge.svg)](https://github.com/mihakralj/vyos-ls1046a-build/actions/workflows/auto-build.yml)

# VyOS for NXP LS1046A (Mono Gateway)

Generic VyOS ARM64 ISO on NXP LS1046A: no eMMC, no network, wrong serial console, CPU stuck at 39% speed. This repo fixes the kernel config so VyOS runs on Layerscape silicon at full performance.

## Current Status

⚠️ 4 of 5 Ethernet interfaces working — eth2 (center RJ45) requires GPY115C PHY driver fix (`CONFIG_MAXLINEAR_GPHY=y`, added to next build). SSH access on eth1, CPU at 1.8 GHz.

## Hardware

| | |
|---|---|
| **SoC** | NXP QorIQ LS1046A — 4× Cortex-A72 @ 1.8 GHz, 8 GB DDR4 ECC |
| **Network** | 5× DPAA1/FMan Ethernet — 3× RJ45 SGMII, 2× SFP+ 10GBase-R |
| **Storage** | eMMC via Freescale eSDHC (`mmcblk0`) — 29.6 GB Kingston iNAND |
| **Console** | 8250 UART at `0x21c0500`, 115200 baud (`ttyS0`) |
| **Board** | [Mono Gateway Development Kit](https://github.com/ryneches/mono-gateway-docs) |

> ⚠️ **Physical RJ45 port order does NOT match ethN numbering.** The PCB routes
> FMan MACs in reverse address order. See [INSTALL.md § Network Interfaces](INSTALL.md#network-interfaces)
> for the verified port mapping.

## Quick Start

**[→ INSTALL.md](INSTALL.md)** — download, write to USB, boot live, `install image` to eMMC, configure U-Boot

Default credentials: `vyos` / `vyos`

## What This Build Fixes

| Problem | Cause | Fix |
|---------|-------|-----|
| No eMMC | `CONFIG_MMC_SDHCI_OF_ESDHC` not set | Enabled as `=y` |
| No network | DPAA1 drivers not enabled | `FSL_FMAN`, `FSL_DPAA`, `FSL_DPAA_ETH`, `FSL_BMAN`, `FSL_QMAN` all `=y` + `FSL_XGMAC_MDIO` + `PHY_FSL_LYNX_28G` for MDIO/PCS |
| No console | Boot uses `ttyAMA0` (PL011) | Reverted to `ttyS0` (8250) + `earlycon` |
| CPU at 700 MHz | `QORIQ_CPUFREQ=m` loads after clock cleanup | Changed to `=y` (built-in) + `CPU_FREQ_DEFAULT_GOV_PERFORMANCE=y` |
| eth2 no link | GPY115C PHY uses Generic PHY (no SGMII AN re-trigger) | `MAXLINEAR_GPHY=y` — proper Maxlinear PHY driver with SGMII workaround |

**[→ PORTING.md](PORTING.md)** — full technical analysis: driver archaeology, DPAA1 architecture, boot flow, DTB, CPU frequency

## Build

Automated weekly (Friday 01:00 UTC) or manual via `workflow_dispatch`.

## Release Assets

| File | Purpose |
|------|---------|
| `*-LS1046A-arm64.iso` | VyOS ISO — boot from USB, run `install image` to eMMC |
| `*-LS1046A-arm64.iso.minisig` | Cryptographic signature ([public key](data/vyos-ls1046a.minisign.pub)) |
| `vyos-packages.tar` | Built kernel & package debs |

## Boot Method

This board uses U-Boot `booti` (direct kernel load) — **not** EFI/GRUB. U-Boot's `bootefi` OOMs due to DPAA1 `reserved-memory` nodes in the DTB. Image upgrades require updating the U-Boot `vyos_direct` variable. See [INSTALL.md § Future Image Upgrades](INSTALL.md#future-image-upgrades).

## License

Based on [VyOS](https://vyos.io) sources (GPLv2). ARM64 builder image from [huihuimoe/vyos-arm64-build](https://github.com/huihuimoe/vyos-arm64-build).
