# Changelog

All notable changes to the VyOS LS1046A build. Forked from [huihuimoe/vyos-arm64-build](https://github.com/huihuimoe/vyos-arm64-build), specialized for the Mono Gateway Development Kit (NXP LS1046A).

Entries are factual. The humor is in the bugs.

## Unreleased

### Fixed
- **`ipsec_flow_fini` kernel panic on reboot** — ASK `ipsec_flow_fini()` operates on a global table (`ipsec_flow_table_global`) but is called per-network-namespace via `xfrm_net_exit()`. When init_net and Docker namespaces both call fini, the second call does `kfree()` on already-freed `hash_table` pointer → BUG at `mm/slub.c:448`. Fixed by NULLing the pointer after free and guarding against double-free.
- **ASK `fp_netfilter_init` boot crash** — `comcerto_fp_netfilter.c` used `module_init()` which runs at `device_initcall` level 6, but was linked before `nf_conntrack` in the Makefile. `nf_ct_netns_get(&init_net)` called before conntrack per-net data existed → NULL pointer dereference. Fixed by changing to `late_initcall()`.
- **`accel-ppp-ng` ARM64 dependency failure** — VyOS upstream added `accel-ppp-ng` as a `vyos-1x` dependency, but no ARM64 build exists. `ci-setup-vyos1x.sh` now strips it from `debian/control` via sed. **TODO:** re-add when ARM64 package becomes available.

### Added
- **Kernel config: KVM, NFS, VFIO, CMA, thermal** — New `ls1046a-extras.config` fragment brings CI build kernel in line with dev kernel: KVM virtualization, NFSv4.1 client, VFIO framework, 32MB CMA for DMA/USDPAA, `power_allocator` thermal governor. Removes unnecessary `fair_share`/`bang_bang` thermal governors and `ladder` cpuidle governor. Disables `STRICT_DEVMEM` for DPDK DPAA PMD `/dev/mem` access.
- **ASK (Application Solutions Kit) SDK kernel integration** — NXP SDK FMan/QBMan/DPAA drivers ported to mainline kernel 6.6 for ASK hardware offload support. Split into two artifacts: `ask-nxp-sdk-sources.tar.gz` (67 files, static) and `003-ask-kernel-hooks.patch` (75 files, adapts to kernel updates). All verified against mainline v6.6 with zero patch failures.

### Improved
- **SDK driver modernization — BUG_ON → WARN_ON_ONCE** across all runtime code:
  - `sdk_dpaa/dpaa_eth.h`: `DPA_BUG_ON` macro now uses `WARN_ON_ONCE` (affects 21 call sites)
  - `sdk_dpaa/offline_port.c`: 4× `BUG_ON` converted to `WARN_ON_ONCE` + graceful error returns
  - `sdk_dpaa/dpaa_eth_common.c`: 7× `BUG_ON` → `WARN_ON_ONCE` + `continue` in FQ init loop
  - `sdk_dpaa/dpaa_debugfs.c`: `BUG_ON` → `WARN_ON_ONCE` + `return -EINVAL`
  - `sdk_dpaa/mac.c`: `BUG_ON` → `WARN_ON_ONCE` + `break`
  - `fsl_qbman/` (6 files): ~30× runtime `BUG_ON` → `WARN_ON_ONCE`
  - `sdk_dpaa/offline_port.c`: 2× `BUG()` stubs → `return -ENOSYS`
- **SDK dead code removal**: duplicate `oh_port_driver_get_port_info()` function + `EXPORT_SYMBOL` (28 lines) and duplicate `offline_port_info` static array removed from `offline_port.c`
- **SDK deprecated API cleanup**: `__devinit`/`__devexit` comment artifacts removed from `lnxwrp_fm.c`/`lnxwrp_fm_port.c`; bare `printk()` → `dev_dbg()`/`dev_info()`/`dev_warn()`/`pr_err()` in `offline_port.c`
- **All 3 SDK components build with 0 errors + 0 warnings**: `sdk_fman/` (56 C files, 74K lines), `sdk_dpaa/` (13 files), `fsl_qbman/` (19 files)
- **SDK legacy printk modernization** — all `printk(KERN_*)` calls replaced with `pr_*()` macros across 13 files (24 instances: `KERN_ERR`→`pr_err`, `KERN_WARNING`→`pr_warn`, `KERN_INFO`→`pr_info`, `KERN_DEBUG`→`pr_debug`, `KERN_CRIT`→`pr_crit`)
- **`__FUNCTION__` → `__func__`** — deprecated GCC extension replaced with C99 standard across 8 files (35 instances in `sdk_dpaa/`, `fsl_qbman/`, `sdk_fman/`, `comcerto_fp_netfilter.c`)
- **Dead version guards removed** — 14× `LINUX_VERSION_CODE >= KERNEL_VERSION(4,x,0)` blocks stripped from `comcerto_fp_netfilter.c` (always true on 6.6, 72 dead lines removed)
- **Dead function/debug code removed** — `FM_Get_Api_Version()` orphaned definition (8 lines), 3× FMC-TRACE/FMC-SIZES debug print blocks (~30 lines), stale 3.1MB `lnxwrp_ioctls_fm.i` preprocessor artifact deleted
- **EHASH debug diagnostic demoted** — `pr_err("EHASH ioctl:")` → `pr_debug()` (only emits when `CONFIG_DYNAMIC_DEBUG` or `DEBUG` is set)
- **Unused variable/function warnings suppressed** — `__maybe_unused` added to `cdx_get_ipsec_fq_hookfn`, `ipsec_offload_pkt_cnt`, `percpu_priv` in `dpaa_eth_sg.c` and `LnxwrpFmPcdIOCTL` in `lnxwrp_ioctls_fm.c` (all used only under `CONFIG_INET_IPSEC_OFFLOAD` which is disabled)
- **Zero warnings achieved** — full `make ARCH=arm64 Image` produces 0 warnings, 0 errors after all optimizations

### Fixed
- **eMMC partition layout now past 32 MiB firmware boundary** (`vyos-1x-006-install-image-reserve-gap.patch`):
  All partitions moved beyond the NXP 32 MiB firmware zone. p1 (BIOS boot) at sector 65536 (32 MiB),
  p2 (EFI) at sector 67584 (33 MiB), p3 (VyOS root) at ~289 MiB. Firmware re-flash via `dd` to first
  32 MiB no longer destroys the GPT — no reinstall needed. `CONST_RESERVED_SPACE` updated to 289 MiB
  (32+1+256). Closes #4.
- **`install image` shows USB disk as install target** (`vyos-1x-013-hide-live-boot-disk.patch`):
  Patch used wrong live-boot mount path `/lib/live/mount/medium` — VyOS actually mounts at
  `/usr/lib/live/mount/medium` (defined by `FILE_ROOTFS_SRC` in `image_installer.py`). `findmnt`
  returned nothing, hit the `except: pass` fallback, USB disk was never excluded. Fix: try all
  three known live-boot mount paths in preference order (`/usr/lib/live/mount/medium`,
  `/run/live/medium`, `/lib/live/mount/medium`). Also separated findmnt/lsblk into independent
  try blocks for more granular error handling. The "Found data from previous installation" prompt
  is expected upstream behavior — `search_previous_installation()` runs BEFORE `create_partitions()`
  so config/SSH keys can be preserved across reinstalls.
- **eth3/eth4 SFP+ kernel visibility** (`mono-gateway-dk.dts`): MAC9 (`ethernet@f0000`) and
  MAC10 (`ethernet@f2000`) had `status = "disabled"` which prevented `fsl_dpaa_mac` + `fsl_dpa`
  from binding and creating kernel netdevs. Changed to `status = "okay"` — all 5 ports (eth0–eth4)
  are visible to the kernel at boot regardless of VPP configuration. The `fsl,dpaa` DT container
  (DPDK resource descriptor) remains and is harmlessly ignored by the mainline kernel.
- **DTS QSPI partition table wrong**: Speculative partition layout didn't match actual `/proc/mtd` on live hardware. Rewritten to match: rcw-bl2 1MB, uboot 2MB, uboot-env 1MB, fman-ucode 1MB, recovery-dtb 1MB, backup 4MB, kernel-initramfs 22MB. Hardware-verified via hexdump + CRC test
- **`fw_env.config` wrong env_size/sector_size**: Was `0x20000`/`0x10000` (caused "Cannot read environment"). Brute-force CRC test on all powers-of-2 confirmed only `0x2000` (8KB env) with `0x1000` (4KB sector) produces valid CRC. `fw_printenv bootcmd` now works
- **`fw_setenv` "doesn't work" misconception**: Previous issue #7 comment stated fw_setenv doesn't work due to MTD mismatch. Root cause was wrong `fw_env.config` parameters. With correct 0x2000/0x1000, `fw_setenv` works perfectly — hardware verified
- **CRITICAL: All 11 vyos-1x patches silently never applied in ANY build**: `build.py` does `git checkout current` after workflow applied patches to the cloned repo, reverting ALL changes. Every build since patch introduction shipped unpatched vyos-1x. Fix: replaced direct `patch -p1` calls with `pre_build_hook` in `package.toml` — hook executes AFTER `git checkout` but BEFORE `dpkg-buildpackage`, ensuring patches persist through the build
- **Patch 010 missing `{% endif %}` for dpdk block**: startup.conf.j2 hunk added `{% if has_dpdk %}` before the `dpdk { }` stanza but was truncated — no closing `{% endif %}`. VPP crashed parsing the unconditional `dpdk { dev 0000:00:00.0 }` block even when dpdk_plugin.so was disabled. Fix: expanded hunk to cover entire dpdk block (29 context lines) with both `{% if has_dpdk %}` and `{% endif %}`
- **vyos-postinstall.service not starting**: systemd ignored the WantedBy symlink ("not a symlink, ignoring") because `ln -sf` in includes.chroot gets dereferenced by live-build into an empty file. Fix: use `systemctl enable` inside 98-fancontrol.chroot hook where it runs inside the chroot
- **Fan control "Device path changed" failure**: hwmon numbering is unstable across boots — `fancontrol` refused to start when EMC2305 moved from hwmon8 to hwmon9. Fix: `fancontrol-setup.sh` dynamically discovers emc2305 and core_cluster by scanning `/sys/class/hwmon/*/name` and regenerates `/etc/fancontrol` before daemon start (ExecStartPre)
- **`/boot/vyos.env` not written during `install image`** (`vyos-1x-011-vyos-env-boot.patch`):
  Rewritten with three hooks: (1) `grub.set_default()` now writes `vyos.env` on every call
  (install, upgrade, set-default, rename) — single convergence point; (2) `install_image()`
  writes `vyos.env` directly to eMMC mount as belt-and-suspenders guarantee, then calls
  `vyos-postinstall --root <mount> <image>` for one-time SPI NOR `fw_setenv`; (3) `add_image()`
  copies `.dtb` files from ISO root to new image boot dir. Previous version was never applied
  (see `pre_build_hook` fix above). Without `vyos.env`, U-Boot cannot find the installed image
  and falls back to USB recovery
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
- **VPP/DPAA1 kernel↔VPP handoff** (`vyos-1x-010-vpp-platform-bus.patch`, `auto-build.yml`):
  Added `_dpaa_find_platform_dev()` and `_dpaa_unbind_ifaces()` to `vpp.py`. When VPP starts with
  a DPAA1 port assigned, the port's `dpaa-ethernet.N` device is unbound from `fsl_dpa` before DPDK
  DPAA PMD initialises (prevents FMan frame queue conflict → kernel panic). State persisted to
  `/run/vpp-dpaa-unbound.json`. Added `/usr/local/bin/vpp-dpaa-rebind` script and
  `vpp.service.d/dpaa-rebind.conf` (`ExecStopPost`) so removing a port from VPP config and
  committing restores kernel control without reboot.
- **Default port ownership**: All ports (eth0–eth4, including SFP+ eth3/eth4) start under kernel
  `fsl_dpa` at boot. Only ports explicitly assigned via `set vpp settings interface ethX` in VyOS
  config are handed to VPP on next apply/reboot. Changing port assignment takes effect immediately
  on commit (rebind path) or after reboot (unbind path).
- Renamed `boot.efi.md` → `UBOOT.md`, stripped duplicated content (kept unique U-Boot/MTD/clock data)
- CHANGELOG.md now manually maintained — CI no longer overwrites it (upstream changes go to GitHub release body only)
- Updated PORTING.md, README.md, AGENTS.md with cross-repo findings from nix/OpenWrt
- Kexec masking moved from `includes.chroot` symlinks to chroot hook (`99-mask-services.chroot`)

### Added
- **Automatic U-Boot SPI flash configuration**: `vyos-postinstall` Phase 1 now writes `vyos`, `usb_vyos`, and `bootcmd` to SPI NOR flash via `fw_setenv` on first boot. Eliminates manual U-Boot serial console Step 4. Idempotent — skips if already configured. Hardware-verified on live board: `fw_printenv` reads back all 3 variables correctly
- **`fw_env.config` hardware-verified**: Brute-force CRC test on live hardware confirmed `CONFIG_ENV_SIZE=0x2000` (8KB) and erase sector `0x1000` (4KB). Only `env_size=0x2000` produces valid CRC against `/dev/mtd3`
- **Hide live boot USB from `install image`** (`vyos-1x-013`): `find_disks()` now detects and excludes the USB live media via `findmnt` + `lsblk PKNAME`. Only eMMC shown as install target — no RAID-1 prompt, no accidental USB overwrite. Supersedes patch 008 (RAID default no)
- **USDPAA kernel module in CI build**: `CONFIG_FSL_USDPAA_MAINLINE=y` added to kernel defconfig. Consolidated kernel patch (`9001-usdpaa-bman-qman-exports-and-driver.patch`, 428 lines) replaces old template patches 0001–0005. Exports BMan/QMan symbols, adds portal reservation API, builds `/dev/fsl-usdpaa` chardev for DPDK DPAA1 PMD userspace access. `fsl_usdpaa_mainline.c` (1453 lines) injected into kernel tree during build via sed hook on `build-kernel.sh`
- **VPP DPAA mempool ordering fix**: `bin/patch-vpp-dpaa-mempool.sh` updated with root cause #29 — BMan mempool must be created BEFORE `dpdk_lib_init()` so the pool exists when DPAA devices probe during EAL init. Uses `rte_pktmbuf_pool_create_by_ops("dpaa")` instead of checking `dm->devices` (empty before init)
- **VyOS native VPP integration**: `vyos-1x-010-vpp-platform-bus.patch` patches VyOS's `set vpp` CLI to support DPAA1 platform-bus NICs via AF_XDP. Auto-detects `fsl_dpa` driver → XDP mode (not DPDK). Enables `af_xdp_plugin.so`, disables `dpdk_plugin.so` when no PCI NICs present. Lowers resource minimums for embedded ARM64 (2 CPUs, 256M heap)
- Default config: `hugepage-size 2M hugepage-count 512` (1024MB) — pre-allocated for VPP memory (heap + statseg + buffers on 2M pages)
- VPP is **off by default** — users enable via `set vpp settings interface eth3` etc. in VyOS configurator. Patch 010 enables the capability; default config only pre-allocates hugepages
- Default config: SFP+ MTU set to 3290 (DPAA1 XDP maximum)
- Kernel configs: `PHYLINK`, `PHY_FSL_LYNX_10G` (10G PCS layer for SFP+)
- **INA234 power sensor support**: `data/kernel-patches/4002-hwmon-ina2xx-add-INA234-support.patch` adds `ti,ina234` to the kernel 6.6 `ina2xx` hwmon driver. INA234 is register-compatible with INA226 but has different scaling: bus voltage LSB 1.6 mV (not 1.25 mV), power coefficient 32 (not 25). Without the patch, INA234 sensors don't bind (no `ti,ina234` in upstream OF match table). `CONFIG_SENSORS_INA2XX=y` added to defconfig. Patch prefix `4002-` avoids collision with upstream VyOS `0002-inotify` kernel patch. Adapted from upstream LKML v3 submission (Ian Ray, 2026-02-20) to match kernel 6.6 driver structure
- Kernel configs: `SENSORS_EMC2305` (fan controller), `SENSORS_INA2XX` (8x INA234 power sensors), `RTC_DRV_PCF2127` (RTC)
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

### Removed
- `fix-grub.sh` — dead file, all fixes now handled at build time (patches + vyos-postinstall)
- `vyos-1x-008-raid-default-no.patch` — superseded by patch 013 (hide live boot disk). With USB filtered from disk list, only 1 disk remains so RAID prompt never triggers

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