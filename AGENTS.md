# AGENTS.md

This file provides guidance to agents when working with code in this repository.

## Project

VyOS ARM64 build scripts for NXP LS1046A (Mono Gateway Development Kit). Single workflow (`auto-build.yml`) builds a custom VyOS ISO with LS1046A-specific kernel config on an ARM64 GitHub Actions runner. No local build possible — everything runs in CI via `workflow_dispatch`.

## Critical Non-Obvious Rules

- **VyOS config syntax:** No comments allowed inside config blocks — `//` and `/* */` both cause parse failures. Comments are only safe at the top level outside `{}` blocks
- **Branch:** `main` only (not `master`). Never create feature branches.
- **Kernel config symbols:** Verify against actual Kconfig files — invalid symbols are silently ignored (e.g., `CONFIG_SERIAL_8250_OF` does not exist; the correct symbol is `CONFIG_SERIAL_OF_PLATFORM`)
- **DPAA1 MDIO dependency:** `CONFIG_FSL_XGMAC_MDIO=y` is required for FMan networking — without it, all MACs defer with "missing pcs" and zero network interfaces appear. Not obvious from Kconfig dependencies.
- **DPAA1 must be `=y` not `=m`:** The entire DPAA1 stack (FMAN, DPAA, BMAN, QMAN, PAMU) must be built-in. If built as modules, FMan initializes too late and interfaces never appear. No errors — just silent failure.
- **CPU frequency:** `CONFIG_QORIQ_CPUFREQ=y` (not `=m`). Module loads after clock cleanup at T+12s, locking CPU at 700 MHz. Built-in claims PLLs first → 1800 MHz.
- **U-Boot boot order:** initrd must load LAST so `${filesize}` captures the initrd size, not kernel/DTB size
- **U-Boot `booti` ramdisk format:** MUST use `${ramdisk_addr_r}:${filesize}` (colon+size), not just the address — otherwise "Wrong Ramdisk Image Format"
- **Boot method is `booti` only:** `bootefi` with GRUB permanently OOMs due to DPAA1 reserved-memory nodes in DTB. No EFI boot path exists. Image upgrades require `fw_setenv` to update `vyos_direct`.
- **eMMC layout (after `install image`):** GPT with p1=BIOS boot (1MiB), 16MiB gap, p2=EFI (256MiB FAT32, GRUB — unused), p3=Linux root (ext4, VyOS). OpenWrt is destroyed. Use `install image` from USB live session.
- **USB boot uses FAT, eMMC uses ext4:** `fatload usb 0:1` vs `ext4load mmc 0:3` — different U-Boot commands. Rufus "ISO Image mode" creates FAT32 on USB.
- **kexec double-boot (LIVE-BOOT ONLY):** USB live boot always does a kexec reboot after first config mount — this is normal VyOS live-boot behavior, NOT a bug. First boot establishes the squashfs+overlay, config loading triggers a reboot, second boot succeeds with migration. `kexec-load.service` and `kexec.service` are masked but the reboot is triggered by `vyos-router` itself reaching `kexec.target`. Does NOT affect installed systems (after `install image` to eMMC). The ~70s penalty is a one-time cost during initial USB install only.
- **Port order reversed:** Physical RJ45 leftmost = eth1 (NOT eth0). Rightmost RJ45 = eth0. PCB routes MACs in reverse DT address order.
- **RJ45 PHYs are Maxlinear GPY115C:** PHY ID `0x67C9DF10`. Requires `CONFIG_MAXLINEAR_GPHY=y` (driver: `mxl-gpy.c`). Without it, "Generic PHY" is used and SGMII AN re-trigger fails — eth2 (center RJ45) never gets link. The GPY2xx has a hardware constraint where SGMII AN only triggers on speed *change*; the proper driver works around this.
- **FMan firmware:** U-Boot injects from SPI flash `mtd4` into DTB before kernel boot. Not loaded via `request_firmware()`, no `/lib/firmware/` files needed
- **Builder image:** Use `ghcr.io/huihuimoe/vyos-arm64-build/vyos-builder:current-arm64` — do NOT fork or rebuild
- **Live device SSH:** OpenWrt is at `root@192.168.1.234` (not the default 192.168.1.1)
- **Git on Windows:** `core.filemode=false` required — NTFS can't represent Unix permissions
- **Don't push during builds:** The workflow updates `version.json` — pushing while a build is running causes merge conflicts. Use `git pull --rebase` if this happens.

## Workflow-Specific Gotchas

- **reftree.cache:** Internal blob required for vyos-1x build but missing from upstream repo — must be copied from `data/reftree.cache`
- **Makefile copyright hack:** `sed -i 's/all: clean copyright/all: clean/'` removes copyright target that fails in CI
- **Only 2 packages rebuilt:** Only `linux-kernel` and `vyos-1x` are built from source; all other packages come from upstream VyOS repos
- **linux-headers stripped:** `rm -rf packages/linux-headers-*` before ISO build to save space on the runner
- **Secure Boot chain:** MOK.pem/MOK.key for kernel module signing, minisign for ISO signing, `grub-efi-arm64-signed` + `shim-signed` packages included
- **Weekly schedule:** Cron runs Friday 01:00 UTC. Also triggered manually via `workflow_dispatch`
- **Boot optimizations:** `kexec-load.service`, `kexec.service`, `acpid.service`, `acpid.socket`, `acpid.path` are masked in the ISO via symlinks to `/dev/null`. ACPI masking saves ~2s. kexec masking forces full cold reboots on installed systems (ensures DPAA1/SFP/I2C hardware re-initializes cleanly). Does NOT prevent the live-boot kexec double-boot — that is triggered by `vyos-router` reaching `kexec.target` (a systemd target, not a service). `CONFIG_DEBUG_PREEMPT` suppression saves ~20s. Installed system boot time: ~82s to login prompt.

## Boot Diagnostics (Ignore These)

- **`smp_processor_id() in preemptible code: python3`** — Suppressed via `# CONFIG_DEBUG_PREEMPT is not set` in defconfig. If seen on older builds: cosmetic only, PREEMPT_DYNAMIC on Cortex-A72.
- **`could not generate DUID ... failed!`** — Expected on live boot without persistence (no stable machine-id)
- **`WARNING failed to get smmu node: FDT_ERR_NOTFOUND`** — DTB lacks SMMU/IOMMU nodes. Harmless.
- **`PCIe: no link` / `disabled`** — No PCIe devices on the board. Normal.
- **`bridge: filtering via arp/ip/ip6tables is no longer available`** — `br_netfilter` not loaded. VyOS loads it when needed.

## Files

| File | Purpose |
|------|---------|
| `.github/workflows/auto-build.yml` | THE build — kernel config overrides, ISO creation, release |
| `README.md` | Project overview: hardware, fixes, release assets, boot method |
| `INSTALL.md` | Complete 11-step install guide: USB → serial → U-Boot → install image → GRUB fixes → verify |
| `PORTING.md` | Deep technical analysis: driver archaeology, DPAA1 architecture, CPU freq, boot flow |
| `boot.efi.md` | U-Boot reference: memory map, boot commands, failed attempts, hardware info, live state |
| `captured_boot.md` | Raw boot log from USB live session (build 2026.03.21-0419-rolling) showing full boot + kexec |
| `CHANGELOG.md` | Upstream VyOS changes tracking |
| `AGENTS.md` | This file — agent guidance and non-obvious rules |
| `fix-grub.sh` | Helper script for GRUB console fixes after install |
| `data/config.boot.default` | Default VyOS config baked into ISO (NO comments allowed inside blocks!) |
| `data/config.boot.dhcp` | Alternative DHCP-enabled boot config |
| `data/dtb/mono-gw.dtb` | Device tree blob for Mono Gateway hardware (extracted from live OpenWrt, 94KB) |
| `data/reftree.cache` | Required vyos-1x build artifact missing from upstream — must copy manually |
| `data/vyos-1x-*.patch` | Patches applied to vyos-1x during build (4 patches: console, vyshim timeout, podman, install gap) |
| `data/vyos-build-*.patch` | Patches applied to vyos-build during build (2 patches: vim link, no sbsign) |
| `data/mok/MOK.pem` | Machine Owner Key certificate for Secure Boot kernel signing |
| `data/vyos-ls1046a.minisign.pub` | Public key for ISO signature verification |
| `version.json` | Update-check version file (served via GitHub raw, auto-updated by CI) |

## Commands

```bash
# Trigger build (no local build)
gh workflow run "VyOS LS1046A build" --ref main

# Check build status
gh run list --limit 3

# Push triggers nothing — workflow_dispatch only
git push  # then manually trigger build
```
