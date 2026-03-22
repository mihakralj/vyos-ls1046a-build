# Changelog

All notable changes to the VyOS LS1046A build are documented here.
This project is a fork of [huihuimoe/vyos-arm64-build](https://github.com/huihuimoe/vyos-arm64-build), specialized for the Mono Gateway Development Kit (NXP LS1046A).

## Unreleased

### Added
- Kernel configs: `PHYLINK`, `PHY_FSL_LYNX_10G` (10G PCS layer for SFP+)
- Kernel configs: `SENSORS_EMC2305` (fan controller), `RTC_DRV_PCF2127` (RTC)
- DTS: ethernet aliases (`ethernet0`–`ethernet4`) for deterministic interface naming
- DTS: `compatible = "mono,gateway-dk", "fsl,ls1046a"` board identification

### Changed
- Renamed `boot.efi.md` → `UBOOT.md`, stripped duplicated content (kept unique U-Boot/MTD/clock data)
- CHANGELOG.md now manually maintained — CI no longer overwrites it (upstream changes go to GitHub release body only)
- Updated PORTING.md, README.md, AGENTS.md with cross-repo findings from nix/OpenWrt

### Removed
- `fix-grub.sh` — dead file, all fixes now handled at build time (patches + vyos-postinstall)

## 2026.03.21-2144-rolling

### Added
- SFP+ transceiver support — kernel configs + custom DTS with SFP nodes
- DTB auto-copy on install via `vyos-postinstall` helper script
- `vyos-postinstall.service` — systemd oneshot for DTB + U-Boot env on first boot
- `vyos-1x-007-prefer-emmc-default.patch` — default `install image` to eMMC
- Port remapping via systemd `.link` files (physical left-to-right = eth0–eth2)
- I2C and GPIO kernel configs required for SFP cage control
- `u-boot-tools` package for `fw_setenv` support
- `fw_env.config` for U-Boot environment access via `/dev/mtd3`
- Hardened default configs for headless embedded operation

### Fixed
- Port remapping: replaced DTS-only approach with `.link` files (works with mainline)
- `vyos-postinstall` auto-detects latest image version string
- Relative symlink for `vyos-postinstall.service` (copytree compatibility)

## 2026.03.21-1930-rolling

### Added
- `CONFIG_MAXLINEAR_GPHY=y` — GPY115C PHY driver for RJ45 ports
- Comprehensive documentation update for all kernel fixes

### Fixed
- Physical port-to-ethN mapping corrected (eth1 DHCP default)
- Removed `--uefi-secure-boot` from grub-install (not supported on this board)
- config.boot ports aligned with physical layout

## 2026.03.21-0419-rolling

### Added
- eMMC image removed in favor of `install image` workflow
- `kexec` double-boot documented as live-boot-only behavior (not a bug)

### Fixed
- Publish step hardened to survive `version.json` merge conflicts

## 2026.03.20-2209-rolling

### Added
- `CONFIG_FSL_XGMAC_MDIO=y` — XGMAC MDIO bus driver (required for FMan MACs)
- `CONFIG_PCS_LYNX=y` — Lynx PCS driver for SGMII/QSGMII link

### Changed
- AGENTS.md updated with MDIO dependency gotcha

## 2026.03.20-1911-rolling / 2026.03.20-1654-rolling

### Added
- FMan diagnostics and SDK vs mainline DTB analysis in PORTING.md
- Built mainline LS1046A RDB DTB for networking debug comparison

### Fixed
- config.boot.default comment syntax (VyOS parser rejects `//` inside blocks)

## 2026.03.20-1516-rolling

### Added
- AGENTS.md — non-obvious project rules for AI assistants and contributors

## 2026.03.20-0619-rolling

### Fixed
- `CONFIG_SERIAL_OF_PLATFORM=y` (replaced non-existent `CONFIG_SERIAL_8250_OF`)
- FMan microcode docs corrected — U-Boot injects via DTB, not `request_firmware()`

## 2026.03.20-0456-rolling

### Added
- Release assets renamed from `generic-arm64` to `LS1046A-arm64`

### Changed
- README refactored (concise), PORTING.md absorbs all technical details

## 2026.03.20-0344-rolling

### Fixed
- U-Boot boot: load initrd LAST so `${filesize}` is correct for `booti`
- `CONFIG_DEVTMPFS_MOUNT=y` — fix missing `/dev/console` on boot
- `live-media=/dev/mmcblk0p2` bootarg for live-boot squashfs discovery

## 2026.03.20-ls1046a-rolling

### Added — Initial LS1046A port
- **DPAA1 networking stack**: FMan, DPAA, BMAN, QMAN, PAMU (all `=y`, not `=m`)
- **eMMC support**: `CONFIG_MMC_SDHCI_OF_ESDHC=y`
- **CPU frequency scaling**: `CONFIG_QORIQ_CPUFREQ=y` (built-in, not module)
- **Serial console**: PL011 UART for LS1046A (`ttyS0`)
- Custom device tree: `mono-gateway-dk.dts`
- Default boot config with SSH enabled
- `INSTALL.md` — complete step-by-step install guide
- `PORTING.md` — deep technical analysis of the port
- Recovery tarball / dd-able eMMC image (later removed)
- Workflow renamed to "VyOS LS1046A build"
- Builder image switched to `ghcr.io/huihuimoe/vyos-arm64-build/vyos-builder:current-arm64`

### Changed
- Default branch renamed `master` → `main`
- Forked from huihuimoe/vyos-arm64-build generic ARM64 build

---

## Pre-fork history (huihuimoe/vyos-arm64-build)

Generic ARM64 VyOS build maintained by [@huihuimoe](https://github.com/huihuimoe).
Weekly automated builds with upstream VyOS tracking. See [upstream repo](https://github.com/huihuimoe/vyos-arm64-build) for full history.

### Notable upstream milestones
- **2025.02.06** — Initial ARM64 build with Scaleway serial console support
- **2025.02.19** — Kernel module signing with MOK
- **2025.05.03** — Custom VyOS Builder container
- **2025.09.03** — Build linux-kernel from source
- **2025.10.10** — Package archival in releases
- **2025.11.14** — `install image` gap reservation patch
- **2026.01.23** — Podman service fix patch
