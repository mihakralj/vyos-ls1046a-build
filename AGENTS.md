# AGENTS.md

This file provides guidance to agents when working with code in this repository.

## Project

VyOS ARM64 build scripts for NXP LS1046A (Mono Gateway Development Kit). Two build paths:
1. **CI (production):** `auto-build.yml` builds signed VyOS ISO on ARM64 GitHub Actions runner via `workflow_dispatch`
2. **Local dev loop (iteration):** `bin/build-local.sh` cross-compiles kernel on LXC 200 (heidi, 192.168.1.137) → TFTP boot on Mono Gateway (~2 min incremental). See `plans/DEV-LOOP.md`

## Critical Non-Obvious Rules

- **No auto-commit/push:** Never commit or push to origin without explicit user request. Stage changes and present them for review first.
- **VyOS config syntax:** No comments allowed inside config blocks — `//` and `/* */` both cause parse failures. Comments are only safe at the top level outside `{}` blocks
- **Branch:** `main` only (not `master`). Never create feature branches.
- **Kernel config symbols:** Verify against actual Kconfig files — invalid symbols are silently ignored (e.g., `CONFIG_SERIAL_8250_OF` does not exist; the correct symbol is `CONFIG_SERIAL_OF_PLATFORM`)
- **DPAA1 MDIO dependency:** `CONFIG_FSL_XGMAC_MDIO=y` is required for FMan networking — without it, all MACs defer with "missing pcs" and zero network interfaces appear. Not obvious from Kconfig dependencies.
- **DPAA1 must be `=y` not `=m`:** The entire DPAA1 stack (FMAN, DPAA, BMAN, QMAN, PAMU) must be built-in. If built as modules, FMan initializes too late and interfaces never appear. No errors — just silent failure.
- **CPU frequency:** `CONFIG_QORIQ_CPUFREQ=y` (not `=m`). Module loads after clock cleanup at T+12s, locking CPU at 700 MHz. Built-in claims PLLs first → 1800 MHz.
- **U-Boot boot order:** initrd must load LAST so `${filesize}` captures the initrd size, not kernel/DTB size
- **U-Boot `booti` ramdisk format:** MUST use `${ramdisk_addr_r}:${filesize}` (colon+size), not just the address — otherwise "Wrong Ramdisk Image Format"
- **U-Boot DTB address:** Use `${fdt_addr_r}` (0x88000000) for DTB, NEVER `0x90000000`. That address is `kernel_comp_addr_r` — decompression scratch space. Kernel decompresses from `0xa0000000` → `0x0` using `0x90000000` as workspace, destroying any DTB there → `ERROR: Did not find a cmdline Flattened Device Tree`
- **Boot method is `booti` only:** `bootefi` with GRUB permanently OOMs due to DPAA1 reserved-memory nodes in DTB. No EFI boot path exists. Image upgrades write `/boot/vyos.env` — no `fw_setenv` needed after initial setup.
- **`/boot/vyos.env` is the boot image selector:** U-Boot reads this single-line text file (`vyos_image=<name>`) via `ext4load` + `env import -t`. Written automatically by patched `grub.set_default()` on every install/upgrade/set-default. Never edit manually unless recovering.
- **eMMC layout (after `install image`):** GPT with p1=BIOS boot (1MiB), 16MiB gap, p2=EFI (256MiB FAT32, GRUB — unused), p3=Linux root (ext4, VyOS). OpenWrt is destroyed. Use `install image` from USB live session.
- **USB boot uses FAT, eMMC uses ext4:** `fatload usb 0:1` vs `ext4load mmc 0:3` — different U-Boot commands. Rufus "ISO Image mode" creates FAT32 on USB. NEVER use `dd` to write the ISO — it preserves ISO9660 which U-Boot cannot read. Linux: format FAT32 + `7z x` to extract. macOS: `diskutil eraseDisk FAT32` + `cp -R`.
- **kexec double-boot (bootargs mismatch):** VyOS `system_option.py` compares `/proc/cmdline` against `MANAGED_PARAMS` from config.boot (hugepages, panic, mitigations, etc.). If they differ during boot (before config_status file exists), it loads a new kernel via `kexec -l` and reboots with `systemctl kexec`. On GRUB systems this is self-healing (grub.cfg gets updated). On U-Boot boards, bootargs must include ALL managed params matching config.boot defaults. Our fix: `hugepagesz=2M hugepages=512 panic=60` added to U-Boot bootargs and `vyos-postinstall` UBOOT_BOOTARGS_TAIL. `kexec-load.service` and `kexec.service` are still masked (forces cold reboots for DPAA1 hardware reinit).
- **is_live_boot() broken on U-Boot boards:** VyOS `is_live_boot()` in `python/vyos/system/image.py` checks for `BOOT_IMAGE=/boot/` or `BOOT_IMAGE=/live/` in `/proc/cmdline`. U-Boot's `booti` doesn't set `BOOT_IMAGE=` (it's GRUB-specific), so the regex never matches and the function always returns `True` (live mode). This blocks `add system image`. Fix: patch `vyos-1x-009` adds a `vyos-union=/boot/` fallback check. Also, `vyos-postinstall` now prepends `BOOT_IMAGE=/boot/<IMAGE>/vmlinuz` to bootargs so future builds work without the fallback.
- **DPAA1 XDP maximum MTU is 3290:** `fsl_dpaa_mac` enforces a hard XDP MTU limit. AF_XDP socket creation (`xsk_socket__create()`) fails with `EINVAL` if the interface MTU exceeds 3290. Must lower MTU before creating AF_XDP sockets. This means VPP SFP+ ports are limited to ~3304-byte max frame size (3290 MTU + 14 Ethernet header), NOT jumbo. Kernel-managed RJ45 ports retain full 9578 MTU jumbo capability.
- **VPP managed via VyOS native CLI:** VPP is configured through `set vpp settings ...` commands (NOT custom systemd services). Patch `vyos-1x-010-vpp-platform-bus.patch` enables platform-bus NIC support (DPAA1 `fsl_dpa` driver → AF_XDP mode). VPP runs with AF_XDP on eth3/eth4 (10G SFP+), LCP tap mirrors for VyOS visibility. Default config baked into ISO.
- **VPP hugepages requirement:** VPP needs ~416MB of 2M hugepages (256M heap + 128M statseg + 32M buffers). Default config reserves 512×2MB (1024MB) via `set system option kernel memory hugepage-size 2M hugepage-count 512`. With insufficient hugepages, VPP fails with "Not enough free memory to start VPP!"
- **VyOS VPP hugepage syntax:** Use `hugepage-count` NOT `hugepage-number` — the wrong keyword silently fails with no error.
- **SSH vbash configure sessions HANG:** Interactive `vbash -c 'source /opt/vyatta/etc/functions/script-template; configure; set ...; commit'` via SSH hangs indefinitely. Workaround: write a vbash script using `vyatta-cfg-cmd-wrapper` commands, SCP it, then execute. Also kill stale vbash sessions and restart configd if locks occur.
- **VPP configd caching:** After patching VyOS Python files on a live system, must `systemctl restart vyos-configd` AND `find / -name __pycache__ -path '*/vyos/*' -exec rm -rf {} +` — configd caches compiled Python modules.
- **VPP thermal protection is mandatory:** Poll-mode VPP triggers `HARDWARE PROTECTION shutdown (Temperature too high)` on both `ddr-controller` (thermal_zone0) and `core-cluster` (thermal_zone3) within ~30 minutes of idle polling. `set vpp settings poll-sleep-usec 100` is MANDATORY. AF_XDP does NOT support adaptive rx-mode — `set interface rx-mode` fails with "unable to set". The only fix is preventing worker thread creation entirely (`cpu-cores 1`, no workers).
- **EMC2305 fan thermal binding is broken:** DTS defines cooling-maps binding fan0 to core-cluster thermal zone trip points (40–60°C), but the thermal OF framework doesn't bind the cooling device to the zone (no `cdev*` in sysfs). Workaround: standard `fancontrol` daemon with `/etc/fancontrol` config mapping EMC2305 PWM to core-cluster thermal zone. EMC2305 quantizes PWM — minimum effective value is ~51 (~1700 RPM). Fan drops temperature from 87°C → 43°C.
- **SFP+ ports are 10G-only:** Both SFP+ cages (eth3, eth4) only support 10G modules (SFP-10G-T, SFP-10G-SR, etc). 1G SFP modules fail with "unsupported SFP module: no common interface modes". Root cause: LS1046A DTB has no serdes PHY provider, so `fman_memac.c` can't query multi-rate support via `phy_validate()`, and `memac_supports()` only allows the DTS-specified mode (10GBASER after xgmii conversion).
- **SFP-10G-T rollball PHY delay:** Copper 10G SFP modules with RTL8261 rollball PHY take ~17 minutes to negotiate link after boot. Interface shows `u/D` during this period — this is normal, not a failure.
- **DTS thermal-zones path:** `mono-gateway-dk.dts` must reference `/thermal-zones/core-cluster/trips` (not `cluster-thermal`) to match kernel 6.6's `fsl-ls1046a.dtsi`. Wrong path causes DTB compilation failure, silently falling back to SDK DTB (no SFP nodes).
- **DTS `phy-connection-type` for 10G:** Must use `"xgmii"`, not `"10gbase-r"`. The `fman_memac.c` PCS assignment fallback needs XGMII to assign PCS to `xfi_pcs`. Using `10gbase-r` directly misassigns to `sgmii_pcs` → broken link.
- **Port order remapped via udev rule:** Physical RJ45 leftmost = eth0, center = eth1, rightmost = eth2. Udev rule `64-fman-port-order.rules` sets `ENV{VYOS_IFNAME}` by matching `DEVPATH` to FMan MAC addresses. VyOS's `65-vyos-net.rules` honors `VYOS_IFNAME` as a "predefined" name, bypassing its `biosdevname` fallback. Without this rule, kernel's DT address probe order gives rightmost = eth0. Note: systemd `.link` files do NOT work — VyOS's `vyos_net_name` overrides them.
- **RJ45 PHYs are Maxlinear GPY115C:** PHY ID `0x67C9DF10`. Requires `CONFIG_MAXLINEAR_GPHY=y` (driver: `mxl-gpy.c`). Without it, "Generic PHY" is used and SGMII AN re-trigger fails — eth2 (center RJ45) never gets link. The GPY2xx has a hardware constraint where SGMII AN only triggers on speed *change*; the proper driver works around this.
- **DTS must match nix reference:** The data/dtb/mono-gateway-dk.dts must have compatible = "mono,gateway-dk", "fsl,ls1046a" (not just "fsl,ls1046a") and ethernet aliases. The canonical DTS source is nix/pkgs/kernel/dts/mono-gateway-dk.dts.
- **INA234 power sensors need kernel patch:** The 8x INA234 power sensors use a non-mainline hwmon driver variant. The nix repo carries 01-hwmon-ina2xx-Add-INA234-support.patch. Without it, sensors bind as INA226 (wrong voltage LSB: 1250 vs 1600 uV).
- **FMan firmware:** U-Boot injects from SPI flash `mtd4` into DTB before kernel boot. Not loaded via `request_firmware()`, no `/lib/firmware/` files needed
- **Builder image:** Use `ghcr.io/huihuimoe/vyos-arm64-build/vyos-builder:current-arm64` — do NOT fork or rebuild
- **Live device SSH:** OpenWrt is at `root@192.168.1.234` (not the default 192.168.1.1)
- **Git on Windows:** `core.filemode=false` required — NTFS can't represent Unix permissions
- **Don't push during builds:** The workflow updates `version.json` — pushing while a build is running causes merge conflicts. Use `git pull --rebase` if this happens.
- **NEVER `install image` from an installed system:** Use `add system image <url>` instead. `install image` is for USB live boot ONLY — it repartitions the eMMC and looks for `/usr/lib/live/mount/medium/live/filesystem.squashfs` which doesn't exist on installed systems. Running it from eMMC DESTROYS the existing installation.
- **DPAA1 offloads are limited:** TSO/LRO/hw-tc-offload are hardware-impossible (`[fixed]` off). Maximum VyOS offloads: `gro gso sg rfs rps`. Do not attempt to enable TSO.
- **Jumbo frame module parameter is `fsl_dpaa_fman`:** The FMan driver's `KBUILD_MODNAME` is `fsl_dpaa_fman`, NOT `fman`. Use `fsl_dpaa_fman.fsl_fm_max_frm=9600` in bootargs. The wrong name silently has no effect (max MTU stays at 1500).
- **QSPI flash needs `CONFIG_SPI_FSL_QSPI=y`:** Without it, `/dev/mtd*` devices don't appear and `fw_setenv` cannot modify U-Boot environment. The DTS defines 8 partitions on the 64MB QSPI NOR flash.
- **`libubootenv-tool` vs `u-boot-tools`:** VyOS ships `libubootenv-tool` which provides `/usr/bin/fw_setenv` but uses a different config file format than classic `u-boot-tools`. The `fw_env.config` must match the installed tool's expectations.

## Local Dev Loop Rules

- **Only edit `auto-build.yml` for build changes** — it is the single workflow; there are no other CI files
- **Kernel config appended, not replaced:** New `CONFIG_*` lines go at the END of the `printf` block in `auto-build.yml` — `vyos_defconfig` is upstream and our additions are appended after checkout
- **`scripts/config --enable` does NOT upgrade `=m` to `=y`:** Use `scripts/config --set-val X y` to force built-in. This is critical for TFTP boot where modules are unavailable.
- **VyOS kernel requires config fragment merging:** 7 files in `vyos-build/scripts/package-build/linux-kernel/config/*.config` must be `cat`'d into `.config` after `vyos_defconfig` copy. Without them, SQUASHFS/OVERLAY_FS/netfilter rules are missing.
- **`boot=live` is REQUIRED in bootargs** even for installed eMMC systems. VyOS initramfs scripts depend on this parameter for squashfs overlay mount.
- **`vyos-union=/boot/<IMAGE>` points to squashfs** on eMMC partition 3. Must match installed image name (`show system image` in VyOS).
- **TFTP boot kexec prevention:** TFTP dev_boot bootargs MUST include `hugepagesz=2M hugepages=512 panic=60` to match config.boot.default `MANAGED_PARAMS`. Without them, `system_option.py` triggers a kexec reboot on first boot. See kexec rule above.
- **mono-gateway-dk.dts fails on mainline 6.6:** Thermal-zones path incompatible. Always use pre-built `data/dtb/mono-gw.dtb` as fallback.
- **Separate `make Image` from `make dtbs`:** A broken DTS in `arch/arm64/boot/dts/freescale/` kills the entire `dtbs` target. Build Image alone, attempt DTS separately with `|| true`.
- **binfmt-support must be on Proxmox HOST, not LXC:** `qemu-user-static` won't register interpreters inside unprivileged LXC containers.
- **Loop mount not permitted in LXC:** Use `7z` for ISO extraction instead of `mount -o loop`.
- **Patch numbering:** `data/vyos-1x-NNN-*.patch` and `data/vyos-build-NNN-*.patch` use 3-digit sequential numbering with gaps (001, 003, 005, 006, 007, 008). Pick the next available number; existing patches are applied in filesystem sort order
- **Patches must use `--no-backup-if-mismatch`** — the workflow applies them with `patch --no-backup-if-mismatch -p1 -d`
- **config.boot.default has NO comments inside blocks** — VyOS config parser fails on `//` and `/* */` inside `{}`. Comments only at file-level outside blocks
- **Console must be `ttyS0` not `ttyAMA0`** — the workflow does `sed -i 's/ttyAMA0/ttyS0/g'` on two upstream files. If adding new serial references, use ttyS0
- **All DPAA1 configs must be `=y`** — never `=m`. FMan needs early init before rootfs mount
- **version.json is CI-managed** — do not manually edit; it's overwritten every build by the publish job
- **DTB goes in `data/dtb/`** — copied to `includes.binary/` during build, lands at ISO root
- **DTS must match nix reference:** `data/dtb/mono-gateway-dk.dts` must have `compatible = "mono,gateway-dk", "fsl,ls1046a"` and ethernet aliases. Canonical source: `nix/pkgs/kernel/dts/mono-gateway-dk.dts`
- **MOK.key is a secret** — only `MOK.pem` is in the repo; the private key comes from `${{ secrets.MOK_KEY }}`
- **vyos-postinstall is board-gated** — the script checks `/proc/device-tree/compatible` for `fsl,ls1046a` and exits early on non-matching hardware. Safe to include in every ISO.
- **vyos-postinstall does NOT run fw_setenv on upgrades** — only on first install (when `vyos_direct` doesn't yet reference `vyos.env`). All upgrades just write `/boot/vyos.env`.

## DPAA1 DPDK PMD Kernel Patches (data/kernel-patches/)

A 6-patch series adds `/dev/fsl-usdpaa` chardev support to **mainline** kernel 6.6, enabling DPDK DPAA1 PMD userspace drivers for 10G wire-speed. This is a clean rewrite — NOT the NXP SDK kernel fork (which was evaluated and rejected for code quality/maintainability reasons). The NXP ioctl ABI is preserved for binary compatibility with DPDK `process.c`.

- **Patch application order matters:** Patches 0001–0004 export kernel symbols; Patch 0005 is the new module that depends on those exports; Patch 0006 adds DTS reserved-memory. Apply in numeric order.
- **Kernel config required:** `CONFIG_FSL_USDPAA_MAINLINE=y` (built-in, not module — needed before rootfs). Add to the `printf` block in `auto-build.yml` alongside other DPAA1 configs.
- **Portal budget:** LS1046A has 10 BMan + 10 QMan portals. Kernel claims 4 (one per CPU). The remaining 6 sit idle and are available for DPDK userspace via the reservation API added in patches 0002/0003.
- **Reserved memory:** Patch 0006 adds a 256MB CMA region at `0xc0000000` in the DTS. DPDK allocates DMA-safe buffers from this pool via `USDPAA_IOCTL_DMA_MAP`. U-Boot `mem=` parameter may need adjustment if it conflicts.
- **ioctl ABI:** The module implements 20 ioctls matching NXP's `fsl_usdpaa.h` numbering (`0x01`–`0x14`). CEETM ioctls (unused on LS1046A) return `-ENOSYS`. Full ABI spec: `plans/USDPAA-IOCTL-SPEC.md`.
- **Portal physical addresses:** Mainline kernel discards phys addrs after `ioremap()` during probe. Patches 0002/0003 store `addr_phys_ce/ci` + `size_ce/ci` in portal config structs and expose reservation functions (`bman_portal_reserve()`/`qman_portal_reserve()`).
- **`bm_alloc_bpid_range()` was static:** Patch 0001 removes `static` and adds `EXPORT_SYMBOL()`. Without this, DPDK cannot allocate buffer pool IDs.
- **`qman_set_sdest()` not exported:** Patch 0004 adds `EXPORT_SYMBOL()`. DPDK uses this to set stashing destination (CPU affinity) for QMan portals.
- **Per-FD cleanup:** The module tracks all resources (portals, DMA maps, BPID/FQID/CGRID ranges) per file descriptor. `usdpaa_release()` frees everything on close — no resource leaks even if DPDK crashes.
- **NXP SDK kernel approach was discarded:** The NXP `lf-6.6.36-2.1.0` fork was built and TFTP-tested but rejected due to: 2,623-line monolithic driver, `#ifdef` spaghetti for 15+ SoC variants, `CONFIG_FSL_SDK_*` symbols conflicting with mainline `CONFIG_FSL_DPAA_*`, and poor separation of concerns. See `plans/DPAA1-DPDK-PMD.md` for the original (now superseded) NXP approach.

## Workflow-Specific Gotchas

- **reftree.cache:** Internal blob required for vyos-1x build but missing from upstream repo — must be copied from `data/reftree.cache`
- **Makefile copyright hack:** `sed -i 's/all: clean copyright/all: clean/'` removes copyright target that fails in CI
- **Only 2 packages rebuilt:** Only `linux-kernel` and `vyos-1x` are built from source; all other packages come from upstream VyOS repos
- **linux-headers stripped:** `rm -rf packages/linux-headers-*` before ISO build to save space on the runner
- **Secure Boot chain:** MOK.pem/MOK.key for kernel module signing, minisign for ISO signing, `grub-efi-arm64-signed` + `shim-signed` packages included
- **Weekly schedule:** Cron runs Friday 01:00 UTC. Also triggered manually via `workflow_dispatch`
- **Boot optimizations:** `kexec-load.service`, `kexec.service`, `acpid.service`, `acpid.socket`, `acpid.path` are masked in the ISO via a chroot hook (`99-mask-services.chroot`) that creates symlinks to `/dev/null` inside the build chroot AND removes SysV init scripts (`/etc/init.d/kexec-load`, `/etc/init.d/kexec`). The old `ln -sf /dev/null` in `includes.chroot` approach was broken — live-build dereferences absolute symlinks to paths outside the chroot, producing empty files instead. The SysV scripts regenerate systemd units via `systemd-sysv-generator`, bypassing the mask. ACPI masking saves ~2s. kexec masking forces full cold reboots on installed systems (ensures DPAA1/SFP/I2C hardware re-initializes cleanly). Does NOT prevent the live-boot kexec double-boot — that is triggered by `vyos-router` reaching `kexec.target` (a systemd target, not a service). `CONFIG_DEBUG_PREEMPT` suppression saves ~20s. Installed system boot time: ~82s to login prompt.

## Boot Diagnostics (Ignore These)

- **`smp_processor_id() in preemptible code: python3`** — Suppressed via `# CONFIG_DEBUG_PREEMPT is not set` in defconfig. If seen on older builds: cosmetic only, PREEMPT_DYNAMIC on Cortex-A72.
- **`could not generate DUID ... failed!`** — Expected on live boot without persistence (no stable machine-id)
- **`WARNING failed to get smmu node: FDT_ERR_NOTFOUND`** — DTB lacks SMMU/IOMMU nodes. Harmless.
- **`PCIe: no link` / `disabled`** — No PCIe devices on the board. Normal.
- **`bridge: filtering via arp/ip/ip6tables is no longer available`** — `br_netfilter` not loaded. VyOS loads it when needed.
- **`nfct v1.4.7: netlink error: Invalid argument`** — Conntrack helper setup during TFTP dev boot. Cosmetic, first boot only — kexec replaces with eMMC kernel.
- **`binfmt_misc.mount` FAILED** — Expected on ARM64 target hardware. No binfmt emulation needed.
- **`mount: /live/persistence/ failed: No such device`** — Non-persistence partitions probed and rejected during live-boot. Normal.
- **`sfp-xfi0: deferred probe pending`** — SFP cages wait for PHY driver. Resolves after full boot.
- **`can't get pinctrl, bus recovery not supported`** — I2C pinctrl not in DTB. Harmless.

## Files

| File | Purpose |
|------|---------|
| `.github/workflows/auto-build.yml` | THE build — kernel config overrides, ISO creation, release |
| `README.md` | Project overview: hardware, fixes, release assets, boot method |
| `INSTALL.md` | Complete 11-step install guide: USB → serial → U-Boot → install image → GRUB fixes → verify |
| `PORTING.md` | Deep technical analysis: driver archaeology, DPAA1 architecture, CPU freq, boot flow |
| `UBOOT.md` | U-Boot serial console reference: memory map, boot commands, failed attempts, clock tree, MTD layout |
| `captured_boot.md` | Raw boot log from USB live session (build 2026.03.21-0419-rolling) showing full boot + kexec |
| `CHANGELOG.md` | Project changelog — manually maintained, NOT overwritten by CI |
| `AGENTS.md` | This file — agent guidance and non-obvious rules |
| `data/config.boot.default` | Default VyOS config baked into ISO (NO comments allowed inside blocks!) |
| `data/config.boot.dhcp` | Alternative DHCP-enabled boot config |
| `data/dtb/mono-gw.dtb` | Device tree blob for Mono Gateway hardware (extracted from live OpenWrt, 94KB) |
| `data/dtb/mono-gateway-dk.dts` | Custom DTS source — compiled during kernel build, includes ethernet aliases + SFP nodes |
| `data/scripts/vyos-postinstall` | Post-install helper: writes `/boot/vyos.env` + one-time `fw_setenv` for static `vyos_direct` |
| `data/scripts/fw_env.config` | U-Boot env access config for fw_printenv/fw_setenv (/dev/mtd3) — only used once during first install |
| `data/scripts/vpp-setup-interfaces.sh` | Legacy VPP AF_XDP setup script (superseded by VyOS native `set vpp` CLI via patch 010) |
| `data/scripts/vpp-setup.service` | Legacy systemd oneshot for VPP interface setup (superseded by VyOS native VPP service) |
| `data/scripts/startup.conf` | Legacy VPP startup config (superseded by VyOS-generated `/etc/vpp/startup.conf`) |
| `data/scripts/fancontrol.conf` | Standard Linux fancontrol config: EMC2305 PWM → core-cluster thermal zone (installed as `/etc/fancontrol`) |
| `data/reftree.cache` | Required vyos-1x build artifact missing from upstream — must copy manually |
| `data/vyos-1x-*.patch` | Patches applied to vyos-1x during build (11 patches: console, vyshim timeout, podman, install gap, eMMC default, RAID default no, U-Boot live-boot detection, VPP platform-bus, vyos.env boot) |
| `data/vyos-build-*.patch` | Patches applied to vyos-build during build (2 patches: vim link, no sbsign) |
| `data/mok/MOK.pem` | Machine Owner Key certificate for Secure Boot kernel signing |
| `data/vyos-ls1046a.minisign.pub` | Public key for ISO signature verification |
| `version.json` | Update-check version file (served via GitHub raw, auto-updated by CI) |
| `bin/build-local.sh` | Fast local build: `kernel`, `dtb`, `extract`, `vyos1x`, `iso` modes |
| `bin/setup-heidi.sh` | One-time: provisions LXC 200 on Proxmox with cross-toolchain + TFTP |
| `VPP.md` | VPP native integration: VyOS `set vpp` CLI with AF_XDP on SFP+ (eth3/eth4), thermal management, DPAA1 PMD roadmap |
| `VPP-SETUP.md` | User-facing VPP setup guide: step-by-step enablement, configuration reference, troubleshooting, hardware constraints |
| `plans/DEV-LOOP.md` | Dev-test loop architecture doc — TFTP boot procedure, lessons learned |
| `plans/DPAA1-DPDK-PMD.md` | Original NXP SDK PMD build plan (superseded by mainline rewrite — kept for reference) |
| `plans/MAINLINE-PATCH-SPEC.md` | Mainline kernel patch specification: export audit, DPDK call trace, 6-patch design |
| `plans/USDPAA-IOCTL-SPEC.md` | Complete NXP USDPAA ioctl ABI spec (20 ioctls, all structs, mmap, cleanup) |
| `plans/fsl_usdpaa.c` | NXP SDK reference source (2,623 lines — read-only reference, not used in build) |
| `plans/fsl_usdpaa.h` | NXP SDK ioctl header (read-only reference for ABI compatibility verification) |
| `data/kernel-patches/9001-usdpaa-bman-qman-exports-and-driver.patch` | Combined kernel patch: BMan/QMan symbol exports, portal phys addr + reservation, allocator-only frees, Kconfig+Makefile for `CONFIG_FSL_USDPAA_MAINLINE` |
| `data/kernel-patches/fsl_usdpaa_mainline.c` | Clean `/dev/fsl-usdpaa` + `/dev/fsl-usdpaa-irq` chardevs (1453 lines, 20 ioctls, NXP ABI-compatible, allocator-only cleanup) — copied to kernel tree during build |
| `data/kernel-patches/0006-*.patch` | DTS reserved-memory reference (already applied in `mono-gateway-dk.dts`) |
| `data/dpdk-portal-mmap.patch` | DPDK `process.c` patch: adds portal mmap after PORTAL_MAP ioctl (CE=64KB WB-NS, CI=16KB Device-nGnRnE) |
| `data/scripts/run-testpmd.sh` | Safe testpmd launcher: takes all interfaces DOWN, runs testpmd with timeout, reboots (no interface restore) |
| `data/dtb/mono-gateway-dk-sdk.dts` | NXP SDK DTS variant (reference only — not used in mainline builds) |

## Commands

```bash
# === CI (production releases) ===
# Trigger build
gh workflow run "VyOS LS1046A build" --ref main

# Check build status
gh run list --limit 3

# Push triggers nothing — workflow_dispatch only
git push  # then manually trigger build

# === Local dev loop (fast iteration) ===
# Deploy build script + run kernel build on LXC 200
scp bin/build-local.sh admin@heidi:/tmp/ ; ssh admin@heidi "sudo pct push 200 /tmp/build-local.sh /opt/vyos-dev/build-local.sh && sudo pct exec 200 -- chmod +x /opt/vyos-dev/build-local.sh && sudo pct exec 200 -- bash -c 'cd /opt/vyos-dev && ./build-local.sh kernel 2>&1'"

# From U-Boot serial (PuTTY 115200 8N1): TFTP boot
run dev_boot
```
