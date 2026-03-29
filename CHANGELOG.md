# Changelog

All notable changes to the VyOS LS1046A build. Forked from [huihuimoe/vyos-arm64-build](https://github.com/huihuimoe/vyos-arm64-build), specialized for the Mono Gateway Development Kit (NXP LS1046A).

Entries are factual. The humor is in the bugs.

## Unreleased

### Added
- **USDPAA kernel module in CI build**: `CONFIG_FSL_USDPAA_MAINLINE=y` added to kernel defconfig. Consolidated kernel patch (`9001-usdpaa-bman-qman-exports-and-driver.patch`, 428 lines) replaces old template patches 0001–0005. Exports BMan/QMan symbols, adds portal reservation API, builds `/dev/fsl-usdpaa` chardev for DPDK DPAA1 PMD userspace access. `fsl_usdpaa_mainline.c` (1453 lines) injected into kernel tree during build via sed hook on `build-kernel.sh`
- **VPP DPAA mempool ordering fix**: `bin/patch-vpp-dpaa-mempool.sh` updated with root cause #29 — BMan mempool must be created BEFORE `dpdk_lib_init()` so the pool exists when DPAA devices probe during EAL init. Uses `rte_pktmbuf_pool_create_by_ops("dpaa")` instead of checking `dm->devices` (empty before init)
- **VyOS native VPP integration**: `vyos-1x-010-vpp-platform-bus.patch` patches VyOS's `set vpp` CLI to support DPAA1 platform-bus NICs via AF_XDP. Auto-detects `fsl_dpa` driver → XDP mode (not DPDK). Enables `af_xdp_plugin.so`, disables `dpdk_plugin.so` when no PCI NICs present. Lowers resource minimums for embedded ARM64 (2 CPUs, 256M heap)
- Default config: `hugepage-size 2M hugepage-count 512` (1024MB) — pre-allocated for VPP memory (heap + statseg + buffers on 2M pages)
- VPP is **off by default** — users enable via `set vpp settings interface eth3` etc. in VyOS configurator. Patch 010 enables the capability; default config only pre-allocates hugepages
- Default config: SFP+ MTU set to 3290 (DPAA1 XDP maximum)
- Kernel configs: `PHYLINK`, `PHY_FSL_LYNX_10G` (10G PCS layer for SFP+)
- Kernel configs: `SENSORS_EMC2305` (fan controller), `RTC_DRV_PCF2127` (RTC)
- Kernel config: `CONFIG_REALTEK_PHY=y` (Realtek PHY driver for RTL821x/RTL822x)
- Kernel config: `CONFIG_SPI_FSL_QSPI=y` (QSPI controller for SPI NOR flash access from Linux)
- DTS: ethernet aliases (`ethernet0`–`ethernet4`) for deterministic interface naming
- DTS: `compatible = "mono,gateway-dk", "fsl,ls1046a"` board identification
- DTS: QSPI NOR flash MTD partition definitions (8 partitions: RCW, BL2, U-Boot, env, fman-ucode, optee, recovery, user)
- Bootarg: `fsl_dpaa_fman.fsl_fm_max_frm=9600` enables jumbo frames (MTU up to 9578) on all interfaces — FMan hardware supports it, but the kernel defaults to 1522
- Default offloads enabled on all interfaces: GRO, GSO, SG, RFS, RPS — maximum set supported by DPAA1 FMan hardware (TSO/LRO/hw-tc-offload are hardware-impossible `[fixed]`)
- VPP v25.10.0 AF_XDP on SFP+ ports: AF_XDP interfaces on eth3/eth4 with Linux CP plugin, 2.47M polls/sec on Cortex-A72 worker thread, zero drops. RJ45 ports (eth0-eth2) stay in direct kernel control
- `vyos-1x-009-uboot-live-boot-detection.patch` — fixes `is_live_boot()` detection for U-Boot boards (adds `vyos-union=` fallback since U-Boot doesn't set `BOOT_IMAGE=`)
- CAAM hardware crypto: 128 algorithms (AES, SHA, RSA, HMAC, authenc for IPsec) via 3 Job Rings + QI interface
- PTP hardware timestamping via `ptp_qoriq` driver (`/dev/ptp0`)

### Fixed
- **CRITICAL: All 11 vyos-1x patches silently never applied in ANY build**: `build.py` does `git checkout current` after workflow applied patches to the cloned repo, reverting ALL changes. Every build since patch introduction shipped unpatched vyos-1x. Fix: replaced direct `patch -p1` calls with `pre_build_hook` in `package.toml` — hook executes AFTER `git checkout` but BEFORE `dpkg-buildpackage`, ensuring patches persist through the build
- **Patch 010 missing `{% endif %}` for dpdk block**: startup.conf.j2 hunk added `{% if has_dpdk %}` before the `dpdk { }` stanza but was truncated — no closing `{% endif %}`. VPP crashed parsing the unconditional `dpdk { dev 0000:00:00.0 }` block even when dpdk_plugin.so was disabled. Fix: expanded hunk to cover entire dpdk block (29 context lines) with both `{% if has_dpdk %}` and `{% endif %}`
- **vyos-postinstall.service not starting**: systemd ignored the WantedBy symlink ("not a symlink, ignoring") because `ln -sf` in includes.chroot gets dereferenced by live-build into an empty file. Fix: use `systemctl enable` inside 98-fancontrol.chroot hook where it runs inside the chroot
- **Fan control "Device path changed" failure**: hwmon numbering is unstable across boots — `fancontrol` refused to start when EMC2305 moved from hwmon8 to hwmon9. Fix: `fancontrol-setup.sh` dynamically discovers emc2305 and core_cluster by scanning `/sys/class/hwmon/*/name` and regenerates `/etc/fancontrol` before daemon start (ExecStartPre)
- **DTB missing after `add system image` upgrade**: `add_image()` only copied `initrd*` and `vmlinuz*` from ISO `/live/` directory — mono-gw.dtb (at ISO root) was never copied to the new image's boot directory. U-Boot would fail to find the DTB on next boot. Fix: patch 011 now also copies `.dtb` files from ISO root to `{root_dir}/boot/{image_name}/` during upgrades
- **VPP "Configuration error" on boot**: Patch 010 hunks for `config_verify.py` and `resource_defaults.py` were silently failing to apply — insufficient context lines (1–2 lines instead of required 3). Result: VPP verify still required 1G main-heap-size while our config specifies 256M → `ERROR_COMMIT` on every boot → config-status=1. Fix: rewrote patch hunks with 3+ lines of context. `min_cpus` now correctly 2 (was stuck at 4), `reserved_cpu_cores` now 1 (was 2), `main_heap_size` minimum now 256M (was 1G)
- **Kexec double-boot eliminated**: Root cause identified in `system_option.py:generate_cmdline_for_kexec()` — compares `/proc/cmdline` against config.boot `MANAGED_PARAMS` (hugepages, panic). U-Boot bootargs were missing `hugepagesz=2M hugepages=512 panic=60` that config.boot.default requests → mismatch → kexec reboot on every boot (~70s penalty). Fix: added params to `vyos-postinstall` UBOOT_BOOTARGS_TAIL. Boot time: ~165s → ~82s
- **TFTP DTB address corruption**: DTB loaded at `0x90000000` destroyed during kernel decompression (that address is `kernel_comp_addr_r`, scratch space for decompressing kernel from `0xa0000000` → `0x0`). Fix: use `${fdt_addr_r}` (0x88000000) for DTB in all TFTP boot commands
- **U-Boot live-boot detection**: VyOS `is_live_boot()` checks `BOOT_IMAGE=` in cmdline (GRUB-specific). U-Boot's `booti` doesn't set this. Added `vyos-union=/boot/` fallback check — system now correctly detected as installed. `show system image` and `add system image` now work
- **Jumbo frame bootarg**: Was `fman.fsl_fm_max_frm=9600` (silently ignored). Correct module name from Makefile is `fsl_dpaa_fman` → `fsl_dpaa_fman.fsl_fm_max_frm=9600`
- **Kexec masking broken by live-build**: `ln -sf /dev/null` in `includes.chroot` gets converted to empty files when live-build creates squashfs (absolute symlinks outside chroot are dereferenced). Empty files don't mask services. Additionally `kexec-load` comes from SysV init script — `systemd-sysv-generator` creates a unit, bypassing our mask. Fix: chroot hook creates proper symlinks AND removes SysV init scripts
- **vyos-postinstall BOOT_IMAGE=**: Now prepends `BOOT_IMAGE=/boot/IMAGE/vmlinuz` as first bootarg (VyOS regex uses `^` anchor). Also fixed `fsl_dpaa_fman` module name
- **SFP+ TX_DISABLE GPIO polarity**: DTS used `GPIO_ACTIVE_HIGH` on `tx-disable-gpios` for both SFP+ nodes, but the board has a hardware inverter between GPIO2 and the SFP cage TX_DISABLE pins. Changed to `GPIO_ACTIVE_LOW` — both SFP+ ports now link correctly (eth3 SFP-10G-T at 1G/10G, eth4 SFP-10G-SR at 10G)
- **SFP+ link DOWN**: DTS used `phy-connection-type = "10gbase-r"` which caused `fman_memac.c` to misassign PCS to `sgmii_pcs` instead of `xfi_pcs`. Changed to `"xgmii"` — kernel converts XGMII→10GBASER after correct PCS assignment
- **SFP-10G-T rate adaptation documented**: SFP-10G-T copper modules with RTL8261 rollball PHY support multi-rate (10G/5G/2.5G/1G) via internal rate adaptation

### Changed
- Renamed `boot.efi.md` → `UBOOT.md`, stripped duplicated content (kept unique U-Boot/MTD/clock data)
- CHANGELOG.md now manually maintained — CI no longer overwrites it (upstream changes go to GitHub release body only)
- Updated PORTING.md, README.md, AGENTS.md with cross-repo findings from nix/OpenWrt
- Kexec masking moved from `includes.chroot` symlinks to chroot hook (`99-mask-services.chroot`)

### Removed
- `fix-grub.sh` — dead file, all fixes now handled at build time (patches + vyos-postinstall)

## 2026.03.21-2144-rolling

### Added
- SFP+ transceiver support — kernel configs + custom DTS with SFP nodes
- DTB auto-copy on install via `vyos-postinstall` helper script
- `vyos-postinstall.service` — systemd oneshot for DTB + U-Boot env on first boot
- `vyos-1x-007-prefer-emmc-default.patch` — default `install image` to eMMC
- Port remapping via udev `VYOS_IFNAME` rule (physical left-to-right = eth0–eth2)
- I2C and GPIO kernel configs required for SFP cage control
- `u-boot-tools` package for `fw_setenv` support
- `fw_env.config` for U-Boot environment access via `/dev/mtd3`
- Hardened default configs for headless embedded operation

### Fixed
- Port remapping: replaced DTS-only approach with udev `64-fman-port-order.rules` (hooks into VyOS naming)
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
