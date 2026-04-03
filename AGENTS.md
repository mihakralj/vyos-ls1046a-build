# AGENTS.md

This file provides guidance to agents when working with code in this repository.

## Project

VyOS ARM64 build scripts for NXP LS1046A (Mono Gateway Development Kit). Two build paths:
1. **CI (production):** `auto-build.yml` builds signed VyOS ISO on ARM64 GitHub Actions runner via `workflow_dispatch`
2. **Local dev loop (iteration):** `bin/build-local.sh` cross-compiles kernel on LXC 200 (192.168.1.137) → TFTP boot on Mono Gateway (~2 min incremental). See `plans/DEV-LOOP.md`

## Critical Non-Obvious Rules

- **No auto-commit/push:** Never commit or push to origin without explicit user request. Stage changes and present them for review first.
- **VyOS config syntax:** No comments allowed inside config blocks — `//` and `/* */` both cause parse failures. Comments are only safe at the top level outside `{}` blocks
- **Branch:** `main` only (not `master`). Never create feature branches.
- **Kernel config symbols:** Verify against actual Kconfig files — invalid symbols are silently ignored (e.g., `CONFIG_SERIAL_8250_OF` does not exist; the correct symbol is `CONFIG_SERIAL_OF_PLATFORM`)
- **DPAA1 MDIO dependency:** `CONFIG_FSL_XGMAC_MDIO=y` is required for FMan networking — without it, all MACs defer with "missing pcs" and zero network interfaces appear. Not obvious from Kconfig dependencies.
- **DPAA1 driver split:** Two drivers manage each FMan MAC: `fsl_dpaa_mac` (hardware MAC control, PHY/link via PHYLINK) and `fsl_dpa` (netdev layer, creates eth0-ethN). `fsl_dpaa_eth` is a different driver — it has zero devices bound on LS1046A. The platform devices are `dpaa-ethernet.N` under `fsl_dpa`. Do NOT confuse these three drivers.
- **DPAA1 kernel↔VPP handoff (AF_XDP):** VPP uses AF_XDP on DPAA1 ports — no unbind needed. The kernel retains full ownership of the netdev (fsl_dpa stays bound), and VPP creates AF_XDP sockets on top. Patch `vyos-1x-010` routes `fsl_dpa` interfaces to `driver='xdp'` (not `'dpdk'`). DPDK DPAA PMD is blocked by RC#31 (bus-level init kills all kernel interfaces). AF_XDP achieves ~3.5 Gbps on 10G SFP+.
- **DPAA PMD bus init is GLOBAL (RC#31 — BLOCKED):** DPDK's `dpaa_bus` probe (`rte_bus_probe()`) initializes ALL BMan buffer pools and QMan frame queues system-wide, not just the ports assigned to VPP. This disrupts kernel-managed interfaces (eth0 management) within seconds of VPP startup, killing SSH/networking. Confirmed on hardware 2026-04-03 and 2026-03-29. **DPAA PMD cannot coexist with kernel FMan drivers in mixed mode.** Unbinding `fsl_dpa` from VPP ports is necessary but NOT sufficient — the bus-level init corrupts shared QBMan hardware state. AF_XDP remains the only viable mixed kernel+VPP path (~3.5 Gbps). DPAA PMD would require ALL interfaces under DPDK (no kernel management — impractical without serial-only access). Potential fix requires DPDK code changes to scope `dpaa_bus` init to specific portals/FQs only.
- **All ports start under kernel control:** At boot, ALL FMan MACs (eth0-eth4, including SFP+ ports) are owned by the kernel via `fsl_dpaa_mac` + `fsl_dpa`. Only ports explicitly added to `set vpp settings interface ethX` in VyOS config are handed to VPP on next apply/reboot.
- **DPAA1 must be `=y` not `=m`:** The entire DPAA1 stack (FMAN, DPAA, BMAN, QMAN, PAMU) must be built-in. If built as modules, FMan initializes too late and interfaces never appear. No errors — just silent failure.
- **CPU frequency:** `CONFIG_QORIQ_CPUFREQ=y` (not `=m`). Module loads after clock cleanup at T+12s, locking CPU at 700 MHz. Built-in claims PLLs first → 1600 MHz.
- **U-Boot boot order:** initrd must load LAST so `${filesize}` captures the initrd size, not kernel/DTB size
- **U-Boot `booti` ramdisk format:** MUST use `${ramdisk_addr_r}:${filesize}` (colon+size), not just the address — otherwise "Wrong Ramdisk Image Format"
- **U-Boot DTB address:** Use `${fdt_addr_r}` (0x88000000) for DTB, NEVER `0x90000000`. That address is `kernel_comp_addr_r` — decompression scratch space. Kernel decompresses from `0xa0000000` → `0x0` using `0x90000000` as workspace, destroying any DTB there → `ERROR: Did not find a cmdline Flattened Device Tree`
- **Boot method is `booti` only:** `bootefi` with GRUB permanently OOMs due to DPAA1 reserved-memory nodes in DTB. No EFI boot path exists. Image upgrades write `/boot/vyos.env` — no `fw_setenv` needed after initial setup.
- **`/boot/vyos.env` is the boot image selector:** U-Boot reads this single-line text file (`vyos_image=<name>`) via `ext4load` + `env import -t`. Written automatically by patched `grub.set_default()` on every install/upgrade/set-default. Never edit manually unless recovering.
- **eMMC layout (after `install image`):** GPT with 32MiB firmware reserved zone, p1=BIOS boot (1MiB at 32MiB), p2=EFI (256MiB FAT32 at 33MiB, GRUB — unused), p3=Linux root (ext4, VyOS at ~289MiB). OpenWrt is destroyed. All partitions beyond NXP 32MiB firmware boundary — firmware re-flash is non-destructive. Use `install image` from USB live session.
- **USB boot uses FAT, eMMC uses ext4:** `fatload usb 0:1` vs `ext4load mmc 0:3` — different U-Boot commands. Rufus "ISO Image mode" creates FAT32 on USB. NEVER use `dd` to write the ISO — it preserves ISO9660 which U-Boot cannot read. Linux: format FAT32 + `7z x` to extract. macOS: `diskutil eraseDisk FAT32` + `cp -R`.
- **kexec double-boot (bootargs mismatch):** VyOS `system_option.py` compares `/proc/cmdline` against `MANAGED_PARAMS` from config.boot (hugepages, panic, mitigations, etc.). If they differ during boot (before config_status file exists), it loads a new kernel via `kexec -l` and reboots with `systemctl kexec`. On GRUB systems this is self-healing (grub.cfg gets updated). On U-Boot boards, bootargs must include ALL managed params matching config.boot defaults. Our fix: `panic=60` in U-Boot bootargs and `vyos-postinstall` UBOOT_BOOTARGS_TAIL. Hugepages are NOT in bootargs by default — they are added dynamically when VPP is configured via `set vpp settings`, which triggers a one-time kexec to apply them. `kexec-load.service` and `kexec.service` are NOT masked — mainline 6.6 QBMan kexec fix (`bman_requires_cleanup()` in `drivers/soc/fsl/qbman/`) allows kexec on DPAA1. VyOS managed-params self-healing works normally (issue #7 resolved).
- **is_live_boot() broken on U-Boot boards:** VyOS `is_live_boot()` in `python/vyos/system/image.py` checks for `BOOT_IMAGE=/boot/` or `BOOT_IMAGE=/live/` in `/proc/cmdline`. U-Boot's `booti` doesn't set `BOOT_IMAGE=` (it's GRUB-specific), so the regex never matches and the function always returns `True` (live mode). This blocks `add system image`. Fix: patch `vyos-1x-009` adds a `vyos-union=/boot/` fallback check. Also, `vyos-postinstall` now prepends `BOOT_IMAGE=/boot/<IMAGE>/vmlinuz` to bootargs so future builds work without the fallback.
- **DPAA1 XDP maximum MTU is 3290:** `fsl_dpaa_mac` enforces a hard XDP MTU limit. AF_XDP socket creation (`xsk_socket__create()`) fails with `EINVAL` if the interface MTU exceeds 3290. Must lower MTU before creating AF_XDP sockets. This means VPP SFP+ ports are limited to ~3304-byte max frame size (3290 MTU + 14 Ethernet header), NOT jumbo. Kernel-managed RJ45 ports retain full 9578 MTU jumbo capability.
- **VPP managed via VyOS native CLI:** VPP is configured through `set vpp settings ...` commands (NOT custom systemd services). Patch `vyos-1x-010-vpp-platform-bus.patch` enables platform-bus NIC support (DPAA1 `fsl_dpa` driver → AF_XDP). By default NO ports are assigned to VPP — all ports remain as kernel netdevs. User assigns SFP+ ports to VPP with `set vpp settings interface eth3` and `set vpp settings interface eth4`. AF_XDP works WITH the kernel driver — no unbind/rebind needed. VPP creates AF_XDP sockets on configured ports; removing a port releases the socket and kernel retains full control. MTU must be ≤3290 on AF_XDP ports.
- **VPP hugepages requirement:** VPP needs ~416MB of 2M hugepages (256M heap + 128M statseg + 32M buffers). Hugepages are NOT pre-allocated — they are dynamically added when `set vpp settings` is configured, which sets `hugepage-size 2M hugepage-count 512` and triggers a one-time kexec. With insufficient hugepages, VPP fails with "Not enough free memory to start VPP!"
- **VyOS VPP hugepage syntax:** Use `hugepage-count` NOT `hugepage-number` — the wrong keyword silently fails with no error.
- **SSH vbash configure sessions HANG:** Interactive `vbash -c 'source /opt/vyatta/etc/functions/script-template; configure; set ...; commit'` via SSH hangs indefinitely. Workaround: write a vbash script using `vyatta-cfg-cmd-wrapper` commands, SCP it, then execute. Also kill stale vbash sessions and restart configd if locks occur.
- **VPP sysfs device hierarchy:** `/sys/class/net/eth3/device` points to the PARENT `fsl_dpaa_mac` device, NOT the child `dpaa-ethernet.N` (which is the `fsl_dpa` netdev driver). The `net/` directory lives on the parent MAC device. To find the child platform device for unbinding, walk `/sys/class/net/<iface>/device/` for entries matching `dpaa-ethernet.*`. This is critical for VPP port handoff — the `vyos-1x-010` patch's `_dpaa_find_platform_dev()` must use this walk pattern.
- **DPDK GROUP linker script drops constructors:** DPDK bus/PMD drivers self-register via `__attribute__((constructor))` (`RTE_REGISTER_BUS`, `RTE_PMD_REGISTER`). A GROUP linker script (`GROUP ( -l:librte_*.a )`) only pulls `.o` files that satisfy unresolved references — since constructors are self-contained, they get silently dropped. Result: zero buses, zero scan, zero interfaces. Fix: `ld -r --whole-archive` to pre-link all `.o` into a single fat relocatable object preserving all constructors.
- **binutils needs `apt-mark manual` in ISO:** VyOS live-build cleanup auto-removes binutils even when added via `--custom-package`. The `97-dpaa-dpdk-plugin.chroot` hook must run `apt-mark manual binutils` to prevent autoremove. Without this, `nm`/`objdump`/`readelf` are unavailable on the target for DPAA plugin diagnostics.
- **VPP configd caching:** After patching VyOS Python files on a live system, must `systemctl restart vyos-configd` AND `find / -name __pycache__ -path '*/vyos/*' -exec rm -rf {} +` — configd caches compiled Python modules.
- **VPP thermal protection is mandatory:** Poll-mode VPP triggers `HARDWARE PROTECTION shutdown (Temperature too high)` on both `ddr-controller` (thermal_zone0) and `core-cluster` (thermal_zone3) within ~30 minutes of idle polling. `set vpp settings poll-sleep-usec 100` is MANDATORY. AF_XDP does NOT support adaptive rx-mode — `set interface rx-mode` fails with "unable to set". The only fix is preventing worker thread creation entirely (`cpu-cores 1`, no workers).
- **EMC2305 fan thermal binding is broken:** DTS defines cooling-maps binding fan0 to core-cluster thermal zone trip points (40–60°C), but the thermal OF framework doesn't bind the cooling device to the zone (no `cdev*` in sysfs). Workaround: standard `fancontrol` daemon with `/etc/fancontrol` config mapping EMC2305 PWM to core-cluster thermal zone. EMC2305 quantizes PWM — minimum effective value is ~51 (~1700 RPM). Fan drops temperature from 87°C → 43°C.
- **SFP+ ports are 10G-only:** Both SFP+ cages (eth3, eth4) only support 10G modules (SFP-10G-T, SFP-10G-SR, etc). 1G SFP modules fail with "unsupported SFP module: no common interface modes". Root cause: LS1046A DTB has no serdes PHY provider, so `fman_memac.c` can't query multi-rate support via `phy_validate()`, and `memac_supports()` only allows the DTS-specified mode (10GBASER after xgmii conversion).
- **SFP-10G-T rollball PHY:** Copper 10G SFP modules with RTL8261 rollball PHY get link immediately after boot with kernel patch 4003 and correct DTS LOS GPIO configuration. Carrier requires a 10GBASE-T capable switch on the other end; a 1G-only switch will never establish copper link and LOS will stay permanently HIGH.
- **SFP-10G-T rollball PHY EINVAL failure (kernel patch 4003):** When an SFP-10G-T copper rollball module is inserted with `managed = "in-band-status"` on the FMan 10G MAC, `sfp_sm_probe_for_phy()` calls `sfp_add_phy()` → `phylink_attach_phy()` which returns `-EINVAL` (attaching a PHY to a MAC already in INBAND mode is rejected). Without kernel patch `4003-sfp-rollball-phylink-einval-fallback.patch`, `sfp.c` treats this as fatal → `SFP_S_FAIL` state → link never comes up. The patch converts `-EINVAL` from `sfp_add_phy()` into a non-fatal "proceed without PHY" (identical to exhausting `-ENODEV` retries). The MAC's in-band 10GBASE-R sync detection then correctly determines carrier once the RTL8261 completes copper negotiation. Modules that report as "SR" in their EEPROM (e.g. 10Gtek ASF-10G-T) are unaffected because sfp.c skips rollball PHY probe for SR EEPROM type.
- **SFP `los-gpios` must be present with `GPIO_ACTIVE_HIGH`:** The LOS GPIO (GPIO2 pins 9/11) correctly reflects module signal status. When copper link is NOT yet established, LOS is HIGH (active) → state machine waits in `wait_los`. Once 10GBASE-T copper negotiation completes, LOS goes LOW → state advances to `link_up` with actual carrier. In practice, SFP-10G-T modules establish link immediately after boot — no prolonged delay. WITHOUT `los-gpios`, the state machine races to `link_up` before copper is established; phylink then fails to detect carrier via in-band PCS polling on FMan 10G MACs in kernel 6.6.130+ (polling works on older kernels but breaks in newer ones). Result: permanent no-carrier despite copper being physically up. Both ASF-10G-T and SFP-10G-T modules properly deassert LOS once copper link is established.
- **SFP `tx-disable-gpios` polarity is `GPIO_ACTIVE_LOW`:** The Mono Gateway board has a hardware signal inverter between GPIO2 pins and the SFP cage TX_DISABLE pins. DTS must use `GPIO_ACTIVE_LOW`. With the inverter: `gpiod_set_value(0)` (assert disable) → physical GPIO LOW → inverter → SFP TX_DISABLE HIGH → TX disabled (correct per SFF-8472). To enable TX: `gpiod_set_value(0)` in "TX enabled" state → physical LOW → inverter → TX_DISABLE LOW → TX on. Using `GPIO_ACTIVE_HIGH` (no-inverter assumption) reverses the logic: the SFP stays TX-disabled at boot and never transmits.
- **DTS thermal-zones path:** `mono-gateway-dk.dts` must reference `/thermal-zones/core-cluster/trips` (not `cluster-thermal`) to match kernel 6.6's `fsl-ls1046a.dtsi`. Wrong path causes DTB compilation failure, silently falling back to SDK DTB (no SFP nodes).
- **DTS `phy-connection-type` for 10G:** Must use `"xgmii"`, not `"10gbase-r"`. The `fman_memac.c` PCS assignment fallback needs XGMII to assign PCS to `xfi_pcs`. Using `10gbase-r` directly misassigns to `sgmii_pcs` → broken link.
- **DTS 10G MAC status must be `"okay"`:** `ethernet@f0000` (MAC9) and `ethernet@f2000` (MAC10) in `mono-gateway-dk.dts` must have `status = "okay"` so the kernel creates eth3/eth4 netdevs at boot. Setting `status = "disabled"` prevents kernel ownership AND leaves those MACs in a limbo (DPDK does not auto-claim them unless VPP is running). The `fsl,dpaa` container in the DTS is used ONLY by DPDK userspace when VPP is configured — the kernel safely ignores it (no mainline driver matches `compatible = "fsl,dpaa"`).
- **Port order requires udev remap + VyOS hw-id:** FMan MACs probe by DT unit-address order (e2000→first, e8000→second, ea000→third, f0000→fourth, f2000→fifth) which does NOT match physical port positions. Additionally, systemd predictable naming renames interfaces to e2-e6 (based on FMan MAC cell-index). Fix: `10-fman-port-order.rules` udev rule calls `/usr/local/bin/fman-port-name` which reads each interface's `/sys/class/net/<iface>/device/of_node` to map the FMan MAC DT address to the correct physical port name. `00-fman.link` systemd .link file prevents systemd from overriding the udev-assigned name. **On installed systems, VyOS's `vyos_net_name` (hw-id matching from config.boot) takes precedence over the udev rule** — the fman-port-name script is primarily effective during live boot and serves as documentation. Physical mapping (confirmed via DT local-mac-address): eth0=left RJ45 (MAC5/e8000/`16:00`), eth1=center RJ45 (MAC6/ea000/`16:01`), eth2=right RJ45 (MAC2/e2000/`15:ff`), eth3=left SFP+ (MAC9/f0000/`16:02`), eth4=right SFP+ (MAC10/f2000/`16:03`).
- **DPDK DPAA PMD requires `STRICT_DEVMEM` disabled:** DPDK's `fman_init()` mmaps FMan CCSR registers via `/dev/mem`. With `CONFIG_STRICT_DEVMEM=y` the mmap gets `EPERM` → "FMAN driver init failed". Both `CONFIG_STRICT_DEVMEM` and `CONFIG_IO_STRICT_DEVMEM` must be `is not set` in the kernel config.
- **RJ45 PHYs are Maxlinear GPY115C:** PHY ID `0x67C9DF10`. Requires `CONFIG_MAXLINEAR_GPHY=y` (driver: `mxl-gpy.c`). Without it, "Generic PHY" is used and SGMII AN re-trigger fails — eth2 (center RJ45) never gets link. The GPY2xx has a hardware constraint where SGMII AN only triggers on speed *change*; the proper driver works around this.
- **DTS must match nix reference:** The data/dtb/mono-gateway-dk.dts must have compatible = "mono,gateway-dk", "fsl,ls1046a" (not just "fsl,ls1046a") and ethernet aliases. The canonical DTS source is nix/pkgs/kernel/dts/mono-gateway-dk.dts.
- **INA234 power sensors kernel patch included:** The 8x INA234 power sensors are supported via `data/kernel-patches/4002-hwmon-ina2xx-add-INA234-support.patch`. INA234 is register-compatible with INA226 but uses different scaling: bus voltage LSB 1600 µV (not 1250 µV), power coefficient 32 (not 25). Without the patch, sensors don't bind at all (no `ti,ina234` in upstream OF match table). The patch adds `ti,ina234` to the kernel 6.6 ina2xx driver enum, config table, i2c_device_id, and of_device_id. `CONFIG_SENSORS_INA2XX=y` is appended to the defconfig in `auto-build.yml`. Patch prefix `4002-` avoids collision with upstream VyOS `0002-inotify` kernel patch.
- **FMan firmware:** U-Boot injects from SPI flash `mtd4` into DTB before kernel boot. Not loaded via `request_firmware()`, no `/lib/firmware/` files needed
- **Builder image:** Use `ghcr.io/huihuimoe/vyos-arm64-build/vyos-builder:current-arm64` — do NOT fork or rebuild
- **Live device SSH:** OpenWrt is at `root@192.168.1.234` (not the default 192.168.1.1)
- **Git on Windows:** `core.filemode=false` required — NTFS can't represent Unix permissions
- **Don't push during builds:** The workflow updates `version.json` — pushing while a build is running causes merge conflicts. Use `git pull --rebase` if this happens.
- **NEVER `install image` from an installed system:** Use `add system image <url>` instead. `install image` is for USB live boot ONLY — it repartitions the eMMC and looks for `/usr/lib/live/mount/medium/live/filesystem.squashfs` which doesn't exist on installed systems. Running it from eMMC DESTROYS the existing installation.
- **DPAA1 offloads are limited:** TSO/LRO/hw-tc-offload are hardware-impossible (`[fixed]` off). Maximum VyOS offloads: `gro gso sg rfs rps`. Do not attempt to enable TSO.
- **Jumbo frame module parameter is `fsl_dpaa_fman`:** The FMan driver's `KBUILD_MODNAME` is `fsl_dpaa_fman`, NOT `fman`. Use `fsl_dpaa_fman.fsl_fm_max_frm=9600` in bootargs. The wrong name silently has no effect (max MTU stays at 1500).
- **Watchdog is IMX2 WDT, not SP805:** The LS1046A watchdog at `0x2ad0000` has compatible `"fsl,ls1046a-wdt", "fsl,imx21-wdt"`. The correct kernel driver is `CONFIG_IMX2_WDT=y` (driver: `imx2_wdt.c`). SP805 and SBSA watchdog drivers are for different ARM platforms. The node is defined in `fsl-ls1046a.dtsi` (inherited via `#include`) with no `status = "disabled"`, so it's always present — only the driver was missing. After adding `CONFIG_IMX2_WDT=y`, `/sys/class/watchdog/watchdog0` appears at boot.
- **QSPI flash needs `CONFIG_SPI_FSL_QUADSPI=y`:** Without it, `/dev/mtd*` devices don't appear and `fw_setenv` cannot modify U-Boot environment. The DTS defines 9 partitions on the 64MB QSPI NOR flash. U-Boot env is at `/dev/mtd2` ("uboot-env", 1MB partition, 4KB erase sector). CONFIG_ENV_SIZE = 0x2000 (8KB). NOTE: partition numbering changed — older builds had uboot-env at mtd3, current builds have it at mtd2. Always verify with `cat /proc/mtd` on the target.
- **`libubootenv-tool` config format:** VyOS ships `libubootenv-tool` which provides `/usr/bin/fw_setenv`. It accepts the classic `fw_env.config` legacy format: `Device Offset Env_size Sector_size`. The `/etc/fw_env.config` must point to `/dev/mtd2 0x0 0x2000 0x1000`. Env_size 0x2000 (8KB) and sector 0x1000 (4KB) were confirmed by brute-force CRC test on live hardware. Previous versions incorrectly pointed to `/dev/mtd3` — fixed 2026-04-03.
- **`vyos-postinstall` Phase 1 always runs on `install image`, is idempotent on boot service:** Phase 1 (`setup_uboot_env_once`) is called with `force=1` when invoked from `install image` (detected by presence of `--root` flag) — it **always** rewrites `vyos`, `usb_vyos`, and `bootcmd` in SPI flash regardless of current state. When called from `vyos-postinstall.service` on every boot (no `--root`), it checks `fw_printenv -n vyos` for "vyos.env" and skips if already correct. Boot order written: USB first (`usb start; if fatload usb 0:0 ${load_addr} boot.scr; then source ${load_addr}; fi`) → eMMC (`run vyos`) → SPI recovery. Manual U-Boot console setup (INSTALL.md Step 4) is the fallback if `fw_setenv` fails.
- **Transient U-Boot ext4 failure on first boot after fresh eMMC format:** After `install image` on a freshly wiped eMMC, the first boot may print `Failed to load '/boot/vyos.env'` and fall through to SPI recovery. This is a known U-Boot ext4 driver quirk: the driver cannot reliably read a freshly-formatted ext4 partition on first access. `/boot/vyos.env` was written correctly by the installer (via grub.py hook). Just reboot from the recovery shell (`reboot`) and the second boot will load eMMC correctly. This is not a bug in postinstall or the installer.

## Local Dev Loop Rules

- **Only edit `auto-build.yml` for build changes** — it is the single workflow; there are no other CI files
- **Kernel config appended, not replaced:** New `CONFIG_*` lines go at the END of the `printf` block in `auto-build.yml` — `vyos_defconfig` is upstream and our additions are appended after checkout
- **`scripts/config --enable` does NOT upgrade `=m` to `=y`:** Use `scripts/config --set-val X y` to force built-in. This is critical for TFTP boot where modules are unavailable.
- **VyOS kernel requires config fragment merging:** 7 files in `vyos-build/scripts/package-build/linux-kernel/config/*.config` must be `cat`'d into `.config` after `vyos_defconfig` copy. Without them, SQUASHFS/OVERLAY_FS/netfilter rules are missing.
- **`boot=live` is REQUIRED in bootargs** even for installed eMMC systems. VyOS initramfs scripts depend on this parameter for squashfs overlay mount.
- **`vyos-union=/boot/<IMAGE>` points to squashfs** on eMMC partition 3. Must match installed image name (`show system image` in VyOS).
- **TFTP boot kexec prevention:** TFTP dev_boot bootargs MUST include `panic=60` to match config.boot.default `MANAGED_PARAMS`. Without it, `system_option.py` triggers a kexec reboot on first boot. Hugepages are NOT needed in TFTP bootargs unless VPP is configured in config.boot.default. See kexec rule above.
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
- **vyos-postinstall Phase 1 runs on every `install image`** — forced (`force=1`) when `--root` is provided by the installer. On boot service calls (no `--root`), Phase 1 skips if SPI already has correct config. Phase 2 (writing `/boot/vyos.env`) runs on every boot of the installed system to keep it in sync with the running image.

## DPAA1 DPDK PMD (ARCHIVED — see `archive/dpaa-pmd/`)

The DPDK DPAA1 PMD infrastructure has been **archived** (2026-04-03) due to RC#31: `dpaa_bus` probe kills all kernel FMan interfaces globally. See `plans/VPP-DPAA-PMD-VS-AFXDP.md` for full analysis.

**Current production path:** AF_XDP via `vyos-1x-010-vpp-platform-bus.patch` (~3.5 Gbps on 10G SFP+).

All DPDK/USDPAA files moved to `archive/dpaa-pmd/` with restoration guide in `archive/dpaa-pmd/RESTORE.md`. Future paths: all-DPDK+LCP mode, CDX-assisted DPAA PMD, or upstream DPDK fix to scope `dpaa_bus` init.

## Workflow-Specific Gotchas

- **reftree.cache:** Internal blob required for vyos-1x build but missing from upstream repo — must be copied from `data/reftree.cache`
- **Makefile copyright hack:** `sed -i 's/all: clean copyright/all: clean/'` removes copyright target that fails in CI
- **Only 2 packages rebuilt:** Only `linux-kernel` and `vyos-1x` are built from source; all other packages come from upstream VyOS repos
- **linux-headers stripped:** `rm -rf packages/linux-headers-*` before ISO build to save space on the runner
- **Secure Boot chain:** MOK.pem/MOK.key for kernel module signing, minisign for ISO signing, `grub-efi-arm64-signed` + `shim-signed` packages included
- **Weekly schedule:** Cron runs daily 05:00 UTC. Also triggered manually via `workflow_dispatch`
- **DPDK PMD build archived:** The "Build DPDK + VPP DPAA Plugin" CI step has been removed (RC#31). Infrastructure preserved in `archive/dpaa-pmd/` — see `archive/dpaa-pmd/RESTORE.md` to re-enable.
- **Boot optimizations:** `acpid.service`, `acpid.socket`, `acpid.path` are masked in the ISO via `99-mask-services.chroot`. `kexec-load.service` and `kexec.service` are NOT masked — mainline 6.6 QBMan kexec fix enables VyOS managed-params self-healing on DPAA1. SysV init scripts (`/etc/init.d/kexec-load`, `/etc/init.d/kexec`) are removed to prevent `systemd-sysv-generator` from creating duplicate units that would bypass systemd. The old `ln -sf /dev/null` in `includes.chroot` approach was broken — live-build dereferences absolute symlinks to paths outside the chroot, producing empty files instead. ACPI masking saves ~2s. `CONFIG_DEBUG_PREEMPT` suppression saves ~20s. Installed system boot time: ~82s to login prompt.

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
| `data/scripts/vyos-postinstall` | Post-install helper: Phase 1 = `fw_setenv` for `vyos`/`usb_vyos`/`bootcmd` (forced on `install image` via `--root`, idempotent on boot service); Phase 2 = writes `/boot/vyos.env` on every boot to sync running image |
| `data/scripts/fw_env.config` | U-Boot env access config for fw_printenv/fw_setenv (/dev/mtd2) — only used once during first install |
| `data/scripts/fancontrol.conf` | Standard Linux fancontrol config: EMC2305 PWM → core-cluster thermal zone (installed as `/etc/fancontrol`) |
| `data/reftree.cache` | Required vyos-1x build artifact missing from upstream — must copy manually |
| `data/vyos-1x-*.patch` | Patches applied to vyos-1x during build (11 patches: console, vyshim timeout, podman, install gap, eMMC default, U-Boot live-boot detection, VPP platform-bus, vyos.env boot, LS1046A MOTD, hide live-boot disk from install) |
| `data/vyos-build-*.patch` | Patches applied to vyos-build during build (2 patches: vim link, no sbsign) |
| `data/mok/MOK.pem` | Machine Owner Key certificate for Secure Boot kernel signing |
| `data/vyos-ls1046a.minisign.pub` | Public key for ISO signature verification |
| `version.json` | Update-check version file (served via GitHub raw, auto-updated by CI) |
| `bin/build-local.sh` | Fast local build: `kernel`, `dtb`, `extract`, `vyos1x`, `iso` modes |
| `VPP.md` | VPP native integration: VyOS `set vpp` CLI with AF_XDP on SFP+ (eth3/eth4), thermal management, DPAA1 PMD roadmap |
| `VPP-SETUP.md` | User-facing VPP setup guide: step-by-step enablement, configuration reference, troubleshooting, hardware constraints |
| `plans/DEV-LOOP.md` | Dev-test loop architecture doc — TFTP boot procedure, lessons learned |
| `plans/VPP-DPAA-PMD-VS-AFXDP.md` | DPAA1 DPDK PMD vs AF_XDP technical assessment: RC#31 analysis, cost-benefit, infrastructure inventory, recommendation |
| `plans/DPAA1-DPDK-PMD.md` | Original NXP SDK PMD build plan (superseded by mainline rewrite — kept for reference) |
| `plans/MAINLINE-PATCH-SPEC.md` | Mainline kernel patch specification: export audit, DPDK call trace, 6-patch design |
| `plans/USDPAA-IOCTL-SPEC.md` | Complete NXP USDPAA ioctl ABI spec (20 ioctls, all structs, mmap, cleanup) |
| `plans/fsl_usdpaa.c` | NXP SDK reference source (2,623 lines — read-only reference, not used in build) |
| `plans/fsl_usdpaa.h` | NXP SDK ioctl header (read-only reference for ABI compatibility verification) |
| `data/scripts/fman-port-name` | Script called by udev: reads `/sys/class/net/<iface>/device/of_node` to map FMan MAC DT address → physical port name (eth0-eth4) |
| `data/scripts/10-fman-port-order.rules` | Udev rule: calls `fman-port-name` on net device add, sets `NAME=` to correct ethN (installed to `/etc/udev/rules.d/`) |
| `data/scripts/00-fman.link` | Systemd .link file: `NamePolicy=keep` for `dpaa_eth` driver — prevents predictable naming override (installed to `/etc/systemd/network/`) |

| `bin/ci-setup-kernel.sh` | Kernel config: removes conflicting defconfig entries, appends `data/kernel-config/ls1046a-*.config` fragments, copies kernel patches, injects phylink patch into build-kernel.sh |
| `bin/ci-setup-vyos1x.sh` | Applies vyos-1x patches, copies reftree.cache, sets up vyos-1x build |
| `bin/ci-setup-vyos-build.sh` | Applies vyos-build patches, configures live-build for ARM64 |
| `bin/ci-build-packages.sh` | Builds linux-kernel and vyos-1x packages |
| `bin/ci-build-iso.sh` | Final ISO assembly with live-build |
| `data/kernel-config/` | Modular kernel config fragments (ls1046a-board, dpaa1, i2c-gpio, sfp, usb, watchdog) — appended to vyos_defconfig |
| `archive/dpaa-pmd/` | Archived DPDK DPAA1 PMD infrastructure (RC#31) — see `archive/dpaa-pmd/RESTORE.md` |
| `data/hooks/98-fancontrol.chroot` | Live-build hook: installs fancontrol config for EMC2305 thermal management |
| `data/hooks/99-mask-services.chroot` | Live-build hook: masks acpid services, removes SysV kexec scripts |

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
# Build kernel on LXC 200 (SSH in or use VS Code Remote-SSH)
ssh root@192.168.1.137 "cd /opt/vyos-dev && ./build-local.sh kernel 2>&1"

# From U-Boot serial (PuTTY 115200 8N1): TFTP boot
run dev_boot
```
