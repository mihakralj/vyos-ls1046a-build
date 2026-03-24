# U-Boot Reference — Mono Gateway LS1046A

Low-level U-Boot reference for serial console debugging.
Updated 2026-03-22 from live eMMC-installed system running build `2026.03.21-2144-rolling`.

For boot architecture and kernel config rationale, see [PORTING.md](PORTING.md).
For install instructions, see [INSTALL.md](INSTALL.md).

## U-Boot Version

```
U-Boot 2025.04-g26d27571ac82-dirty (Jan 18 2026 - 17:54:35 +0000)
aarch64-oe-linux-gcc (GCC) 14.3.0
```

## Memory Map

| Variable | Address | Notes |
|----------|---------|-------|
| `kernel_addr_r` | `0x82000000` | Kernel load address |
| `fdt_addr_r` | `0x88000000` | Device tree load address |
| `ramdisk_addr_r` | `0x88080000` | Initrd load address (512KB after FDT) |
| `kernel_comp_addr_r` | `0x90000000` | Compressed kernel decompress area |
| `fdt_size` | `0x100000` | 1 MB reserved for FDT |
| `load_addr` | `0xa0000000` | Generic load address |

**DRAM:** 8 GB total

- Bank 0: `0x80000000` – `0xfbdfffff` (1982 MB)
- Bank 1: `0x880000000` – `0x9ffffffff` (6144 MB)

## Boot Commands (Current — Installed VyOS)

```bash
# Saved bootcmd — try VyOS, fall back to SPI recovery
setenv bootcmd 'run vyos_direct || run recovery'

# VyOS direct boot from eMMC p3
setenv vyos_direct 'setenv bootargs "BOOT_IMAGE=/boot/<IMAGE>/vmlinuz console=ttyS0,115200 earlycon=uart8250,mmio,0x21c0500 net.ifnames=0 boot=live rootdelay=5 noautologin vyos-union=/boot/<IMAGE> fsl_dpaa_fman.fsl_fm_max_frm=9600"; ext4load mmc 0:3 ${kernel_addr_r} /boot/<IMAGE>/vmlinuz; ext4load mmc 0:3 ${fdt_addr_r} /boot/<IMAGE>/mono-gw.dtb; ext4load mmc 0:3 ${ramdisk_addr_r} /boot/<IMAGE>/initrd.img; booti ${kernel_addr_r} ${ramdisk_addr_r}:${filesize} ${fdt_addr_r}'
saveenv
```

Replace `<IMAGE>` with the actual image name (e.g., `2026.03.21-2144-rolling`).

**Critical bootargs:**
- `BOOT_IMAGE=/boot/<IMAGE>/vmlinuz` — must be FIRST arg; VyOS `is_live_boot()` regex requires it (U-Boot's `booti` doesn't set it like GRUB does)
- `boot=live` — initramfs uses live-boot mode
- `vyos-union=/boot/<IMAGE>` — squashfs overlay dir on p3 (also used as `is_live_boot()` fallback for U-Boot boards)
- `fsl_dpaa_fman.fsl_fm_max_frm=9600` — enables jumbo frames (max MTU 9578). Module name is `fsl_dpaa_fman`, NOT `fman`
- Missing `boot=live` or `vyos-union=` → drops to initramfs BusyBox shell

**Critical load order:**
- Initrd must be loaded **LAST** so `${filesize}` captures the initrd size
- Ramdisk arg MUST be `${ramdisk_addr_r}:${filesize}` (colon+size format)

## Boot from USB (for initial install)

```bash
usb start
setenv bootargs "console=ttyS0,115200 earlycon=uart8250,mmio,0x21c0500 boot=live live-media=/dev/sda1 components noeject nopersistence noautologin nonetworking union=overlay net.ifnames=0 quiet"
fatload usb 0:1 ${kernel_addr_r} live/vmlinuz-6.6.128-vyos
fatload usb 0:1 ${fdt_addr_r} mono-gw.dtb
fatload usb 0:1 ${ramdisk_addr_r} live/initrd.img-6.6.128-vyos
booti ${kernel_addr_r} ${ramdisk_addr_r}:${filesize} ${fdt_addr_r}
```

> USB live boot triggers a kexec double-boot (~70s penalty). Normal for
> VyOS live-boot, only during initial install. eMMC boot is single-pass (~82s).

## Factory Boot Commands (OpenWrt — Pre-Install)

```bash
# Factory default: try eMMC OpenWrt, then SPI recovery
bootcmd=run emmc || run recovery

# eMMC (OpenWrt on partition 1) — destroyed after install image
emmc=setenv bootargs "${bootargs_console} root=/dev/mmcblk0p1 rw rootwait rootfstype=ext4";
    ext4load mmc 0:1 ${kernel_addr_r} /boot/Image.gz &&
    ext4load mmc 0:1 ${fdt_addr_r} /boot/mono-gateway-dk-sdk.dtb &&
    booti ${kernel_addr_r} - ${fdt_addr_r}

# SPI flash recovery (always available)
recovery=sf probe 0:0; sf read ${kernel_addr_r} ${kernel_addr} ${kernel_size};
    sf read ${fdt_addr_r} ${fdt_addr} ${fdt_size};
    booti ${kernel_addr_r} - ${fdt_addr_r}
```

## EFI/GRUB — Permanently Broken

`bootefi` with GRUB OOMs on this board. DTB `reserved-memory` nodes for DPAA1
prevent U-Boot EFI initialization:

```
reserved-memory:
  qman-pfdr: 0x9fc000000..0x9fdffffff (32 MB) nomap
  qman-fqd:  0x9fe800000..0x9feffffff (8 MB)  nomap
  bman-fbpr: 0x9ff000000..0x9ffffffff (16 MB) nomap
```

**Use `vyos_direct` (booti) as the permanent boot method.**

## Failed Boot Attempts (Reference)

### `booti` without `:${filesize}` on ramdisk
```bash
booti ${kernel_addr_r} ${ramdisk_addr_r} ${fdt_addr_r}
# "Wrong Ramdisk Image Format / Ramdisk image is corrupt or invalid"
# Fix: use ${ramdisk_addr_r}:${filesize} — booti needs addr:size format
```

### `booti` kernel-only (no initrd, stale bootargs)
```bash
booti ${kernel_addr_r} - ${fdt_addr_r}
# Kernel boots (all 5 FMan MACs probe!) but hangs:
#   "Waiting for root device /dev/mmcblk0p1..."
# Cause: bootargs still "root=/dev/mmcblk0p1" from factory env.
#   No initrd = no live-boot initramfs = can't mount squashfs.
```

## Ethernet Interfaces

> ⚠️ Physical RJ45 port order differs from DT node address order.
> Port remapping is handled by udev rule `64-fman-port-order.rules` setting `VYOS_IFNAME`.

| Physical Position | DT Node | MAC Address | PHY Addr | VyOS Name | Type |
|-------------------|---------|-------------|----------|-----------|------|
| Port 1 (leftmost RJ45) | `1ae8000.ethernet` | `E8:F6:D7:00:15:FF` | MDIO :00 | **eth0** | SGMII |
| Port 2 (center RJ45) | `1aea000.ethernet` | `E8:F6:D7:00:16:00` | MDIO :01 | **eth1** | SGMII |
| Port 3 (right RJ45) | `1ae2000.ethernet` | `E8:F6:D7:00:16:01` | MDIO :02 | **eth2** | SGMII |
| SFP1 | `1af0000.ethernet` | `E8:F6:D7:00:16:02` | fixed-link | **eth3** | XGMII 10GBase-R |
| SFP2 | `1af2000.ethernet` | `E8:F6:D7:00:16:03` | fixed-link | **eth4** | XGMII 10GBase-R |

### MAC Addresses (from U-Boot env)

| Variable | Address | Interface |
|----------|---------|-----------|
| `ethaddr` | `E8:F6:D7:00:15:FF` | eth0 |
| `eth1addr` | `E8:F6:D7:00:16:00` | eth1 |
| `eth2addr` | `E8:F6:D7:00:16:01` | eth2 |
| `eth3addr` | `E8:F6:D7:00:16:02` | eth3 |
| `eth4addr` | `E8:F6:D7:00:16:03` | eth4 |

MAC addresses are unique per board — yours will differ.

## Clock Tree & CPU Frequency

**sysclk:** 100 MHz (oscillator)

| Clock | Rate | Source | Notes |
|-------|------|--------|-------|
| `cg-pll1-div1` | 1600 MHz | PLL1 | Max CPU frequency |
| `cg-pll1-div2` | 800 MHz | PLL1 | |
| `cg-pll1-div3` | 533 MHz | PLL1 | |
| `cg-pll1-div4` | 400 MHz | PLL1 | |
| `cg-pll2-div1` | 1400 MHz | PLL2 | HWACCEL1 |
| `cg-pll2-div2` | 700 MHz | PLL2 | Minimum CPU clock |
| `cg-pll2-div3` | 466 MHz | PLL2 | |
| `cg-pll2-div4` | 350 MHz | PLL2 | |
| `cg-cmux0` | 1600 MHz | PLL1-div1 | **CPU clock mux (all 4 cores)** ✅ |
| `cg-hwaccel0` | 700 MHz | PLL2-div2 | FMan clock |
| `cg-pll0-div2` | 300 MHz | PLL0 | SPI (DSPI controller) |

`CONFIG_QORIQ_CPUFREQ=y` (built-in) claims PLL clock parents before
`clk: Disabling unused clocks` runs at T+12s. Confirmed: raid6 neonx8 2056→4816 MB/s.

## SPI Flash (MTD) Layout

```
1550000.spi (accessed via U-Boot sf commands only):
  1M(rcw-bl2)          — Reset Config Word + BL2
  2M(uboot)            — U-Boot
  1M(uboot-env)        — U-Boot environment (saveenv / fw_setenv target)
  1M(fman-ucode)       — FMan microcode (injected to DTB at boot)
  1M(recovery-dtb)     — Recovery device tree
  4M(unallocated)
 22M(kernel-initramfs) — Recovery kernel + initramfs
```

> **MTD visibility requires `CONFIG_SPI_FSL_QSPI=y`.** Without it, `/proc/mtd` is empty and
> `fw_setenv` fails with "Configuration file wrong or corrupted." With QSPI enabled,
> 8 MTD partitions appear (`/dev/mtd0`–`/dev/mtd7`). `fw_setenv` uses `/dev/mtd3`
> (uboot-env, 1 MB). VyOS ships `libubootenv-tool` (not classic `u-boot-tools`),
> which requires its own `/etc/fw_env.config` format.

## USB Device Detection

```
SanDisk 3.2Gen1 (USB 2.10 mode on XHCI)
VID:PID = 0x0781:0x5581
Partition: usb 0:1 (FAT32, single partition from Rufus ISO mode)
```

### ISO Contents on USB (from `fatls usb 0:1`)

```
live/vmlinuz-6.6.128-vyos    (9.2 MB)
live/initrd.img-6.6.128-vyos (33.3 MB)
live/filesystem.squashfs     (526 MB)
mono-gw.dtb                  (94 KB)
EFI/boot/bootaa64.efi        (990 KB)
EFI/boot/grubaa64.efi        (3.9 MB)
```

## Live System State (2026-03-21, eMMC installed)

**Version:** 2026.03.21-0419-rolling
**Kernel:** 6.6.128-vyos `#1 SMP PREEMPT_DYNAMIC`
**FRRouting:** 10.5.2
**Boot source:** eMMC installed (`vyos_direct` booti from mmcblk0p3)

| Resource | Value |
|----------|-------|
| CPU frequency | 1800 MHz ✅ |
| CPU governor | performance |
| Memory total | 7.8 GB |
| Memory used | ~800 MB (10%) |
| Temperature | 42°C |
| Boot time | ~82s to login (single boot, no kexec) |
