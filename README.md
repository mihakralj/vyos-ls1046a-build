[![VyOS LS1046A build](https://github.com/mihakralj/vyos-ls1046a-build/actions/workflows/auto-build.yml/badge.svg)](https://github.com/mihakralj/vyos-ls1046a-build/actions/workflows/auto-build.yml)

# VyOS for NXP LS1046A (Mono Gateway)

Generic VyOS ARM64 ISO on NXP LS1046A: no eMMC, no network, wrong serial console. This repo fixes the kernel config so VyOS runs on Layerscape silicon.

## Hardware

| | |
|---|---|
| **SoC** | NXP QorIQ LS1046A — 4× Cortex-A72 @ 1.8 GHz, 8 GB DDR4 ECC |
| **Network** | 5× DPAA1/FMan Ethernet (eth0–eth4) |
| **Storage** | eMMC via Freescale eSDHC (`mmcblk0p1` OpenWrt, `mmcblk0p2` VyOS) |
| **Console** | 8250 UART at `0x21c0500`, 115200 baud (`ttyS0`) |
| **Board** | [Mono Gateway Development Kit](https://github.com/ryneches/mono-gateway-docs) |

## Quick Start

**[→ INSTALL.md](INSTALL.md)** — download, write to eMMC, configure U-Boot, boot VyOS

Default credentials: `vyos` / `vyos`

## What This Build Fixes

| Problem | Cause | Fix |
|---------|-------|-----|
| No eMMC | `CONFIG_MMC_SDHCI_OF_ESDHC` not set | Enabled as `=y` |
| No network | DPAA1 drivers not enabled | `FSL_FMAN`, `FSL_DPAA`, `FSL_DPAA_ETH`, `FSL_BMAN`, `FSL_QMAN` all `=y` |
| No console | Boot uses `ttyAMA0` (PL011) | Reverted to `ttyS0` (8250) |

**[→ PORTING.md](PORTING.md)** — full technical analysis: driver archaeology, DPAA1 architecture, boot flow, DTB, MTD layout

## Build

Automated weekly (Friday 01:00 UTC)

## Release Assets

| File | Purpose |
|------|---------|
| `*-generic-arm64.iso` | VyOS ISO for `add system image` upgrades |
| `*-generic-arm64.iso.minisig` | Cryptographic signature |
| `*-emmc.img.gz` | dd-able eMMC image for fresh install |
| `vyos-packages.tar` | Built kernel & package debs |

## License

Based on [VyOS](https://vyos.io) sources (GPLv2). ARM64 builder image from [huihuimoe/vyos-arm64-build](https://github.com/huihuimoe/vyos-arm64-build).
