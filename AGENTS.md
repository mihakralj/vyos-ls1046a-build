# AGENTS.md

This file provides guidance to agents when working with code in this repository.

## Project

VyOS ARM64 build scripts for NXP LS1046A (Mono Gateway Development Kit). Single workflow (`auto-build.yml`) builds a custom VyOS ISO with LS1046A-specific kernel config on an ARM64 GitHub Actions runner. No local build possible — everything runs in CI via `workflow_dispatch`.

## Critical Non-Obvious Rules

- **VyOS config syntax:** Uses `/* */` comments only — `//` causes silent parse failures at boot
- **Branch:** `main` only (not `master`). Never create feature branches.
- **Kernel config symbols:** Verify against actual Kconfig files — invalid symbols are silently ignored (e.g., `CONFIG_SERIAL_8250_OF` does not exist; the correct symbol is `CONFIG_SERIAL_OF_PLATFORM`)
- **U-Boot boot order:** initrd must load LAST so `${filesize}` captures the initrd size, not kernel/DTB size
- **eMMC layout:** `mmcblk0p1` = OpenWrt (do not touch), `mmcblk0p2` = VyOS. The `dd` of the eMMC image IS the installation — no `install image` step needed
- **FMan firmware:** U-Boot injects from SPI flash `mtd4` into DTB before kernel boot. Not loaded via `request_firmware()`, no `/lib/firmware/` files needed
- **Builder image:** Use `ghcr.io/huihuimoe/vyos-arm64-build/vyos-builder:current-arm64` — do NOT fork or rebuild
- **Live device SSH:** OpenWrt is at `root@192.168.1.234` (not the default 192.168.1.1)
- **Git on Windows:** `core.filemode=false` required — NTFS can't represent Unix permissions

## Files

| File | Purpose |
|------|---------|
| `.github/workflows/auto-build.yml` | THE build — kernel config overrides, ISO creation, eMMC image, release |
| `data/config.boot.default` | Default VyOS config baked into ISO (uses `/* */` comments!) |
| `data/dtb/mono-gw.dtb` | Device tree blob for Mono Gateway hardware |
| `data/vyos-1x-*.patch` | Patches applied to vyos-1x during build |
| `data/vyos-build-*.patch` | Patches applied to vyos-build during build |
| `version.json` | Update-check version file (served via GitHub raw) |

## Commands

```bash
# Trigger build (no local build)
gh workflow run "VyOS LS1046A build" --ref main

# Check build status
gh run list --limit 3

# Push triggers nothing — workflow_dispatch only
git push  # then manually trigger build
```
