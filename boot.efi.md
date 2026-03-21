# U-Boot & Boot Reference — Mono Gateway LS1046A

Low-level reference for U-Boot configuration, boot commands, memory map,
hardware details, and known failure modes. Updated 2026-03-21 from live
eMMC-installed system running build `2026.03.21-0419-rolling`.

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

After `install image` creates GPT on eMMC, the permanent boot method is `booti` (direct kernel load):

```bash
# Saved bootcmd — try VyOS, fall back to SPI recovery
setenv bootcmd 'run vyos_direct || run recovery'

# VyOS direct boot from eMMC p3
setenv vyos_direct 'setenv bootargs "console=ttyS0,115200 earlycon=uart8250,mmio,0x21c0500 net.ifnames=0 boot=live rootdelay=5 noautologin vyos-union=/boot/<IMAGE>"; ext4load mmc 0:3 ${kernel_addr_r} /boot/<IMAGE>/vmlinuz; ext4load mmc 0:3 ${fdt_addr_r} /boot/<IMAGE>/mono-gw.dtb; ext4load mmc 0:3 ${ramdisk_addr_r} /boot/<IMAGE>/initrd.img; booti ${kernel_addr_r} ${ramdisk_addr_r}:${filesize} ${fdt_addr_r}'
saveenv
```

Replace `<IMAGE>` with the actual image name (e.g., `2026.03.21-0419-rolling`).

**Critical bootargs for installed VyOS:**
- `boot=live` — tells the initramfs to use live-boot mode
- `vyos-union=/boot/<IMAGE>` — path to the squashfs overlay dir on p3
- Missing either parameter drops to an initramfs BusyBox shell

**Critical load order:**
- Initrd must be loaded **LAST** so `${filesize}` captures the initrd size
- The ramdisk arg MUST be `${ramdisk_addr_r}:${filesize}` (with colon+size)

## Boot from USB (for initial install)

Write ISO to USB with Rufus (ISO Image mode). Then:

```bash
usb start
setenv bootargs "console=ttyS0,115200 earlycon=uart8250,mmio,0x21c0500 boot=live live-media=/dev/sda1 components noeject nopersistence noautologin nonetworking union=overlay net.ifnames=0 quiet"
fatload usb 0:1 ${kernel_addr_r} live/vmlinuz-6.6.128-vyos
fatload usb 0:1 ${fdt_addr_r} mono-gw.dtb
fatload usb 0:1 ${ramdisk_addr_r} live/initrd.img-6.6.128-vyos
booti ${kernel_addr_r} ${ramdisk_addr_r}:${filesize} ${fdt_addr_r}
```

> **Note:** USB live boot triggers a kexec double-boot (~70s penalty). This is
> normal VyOS live-boot behavior and only happens during initial installation.
> After `install image` to eMMC, boot is single-pass (~82s).

## Default Boot Commands (Factory — OpenWrt)

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

## EFI/GRUB Boot — Permanently Broken

`bootefi` with GRUB OOMs on this board. Confirmed root cause: DTB `reserved-memory`
nodes for DPAA1 prevent U-Boot EFI initialization.

```
reserved-memory:
  qman-pfdr: 0x9fc000000..0x9fdffffff (32 MB) nomap
  qman-fqd:  0x9fe800000..0x9feffffff (8 MB)  nomap
  bman-fbpr: 0x9ff000000..0x9ffffffff (16 MB) nomap
```

These sit at the top of Bank 1. U-Boot's `bootefi` cannot initialize EFI
properly — GRUB starts but immediately OOMs during heap setup. `fdt_high`
does not fix it. **Use `vyos_direct` (booti) as the permanent boot method.**

Image upgrades require manually updating the `vyos_direct` variable via
`fw_setenv` or U-Boot serial. See [INSTALL.md § Future Image Upgrades](INSTALL.md#future-image-upgrades).

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

### GRUB config console bug (ISO grub.cfg)

The ISO's `/boot/grub/grub.cfg` hardcodes `console=ttyAMA0,115200` (PL011 UART).
On this board the UART is 8250 at `ttyS0`. Serial output is silent during and
after GRUB. Workaround: always use U-Boot `booti` with explicit `console=ttyS0,115200`.

## Post-Install eMMC Layout

```
mmcblk0       29.6 GB total (GPT)
├─ mmcblk0p1      1 MB    BIOS boot (EF02)  — raw
├─ (16 MB gap)            bootloader clearance (our patch)
├─ mmcblk0p2    256 MB    EFI System (EF00) — FAT32, GRUB (unused — bootefi broken)
└─ mmcblk0p3   29.4 GB   Linux root (8300) — ext4, VyOS squashfs + data
```

Gap at sectors 4096–36863 (16 MB) is from `vyos-1x-006-install-image-reserve-gap.patch`.

Post-install, `mono-gw.dtb` must be placed in the boot image directory on p3:

```bash
# From VyOS live session before first eMMC boot:
sudo mount /dev/mmcblk0p3 /mnt/root
IMG=$(ls /mnt/root/boot/ | grep -v grub | grep -v efi | head -1)
sudo cp /sys/firmware/fdt /mnt/root/boot/${IMG}/mono-gw.dtb
sudo sync && sudo umount /mnt/root
```

## GRUB Config Fixes (Post-Install)

Three bugs in installed GRUB config must be fixed after each `install image`:

```bash
CFG=/boot/grub/grub.cfg.d   # or /mnt/root/boot/grub/grub.cfg.d if mounted

# Fix 1: default console tty -> ttyS
sudo sed -i 's/set console_type="tty"/set console_type="ttyS"/' \
    $CFG/20-vyos-defaults-autoload.cfg

# Fix 2: ARM64 ttyAMA -> ttyS
sudo sed -i 's/set serial_console="ttyAMA"/set serial_console="ttyS"/' \
    $CFG/50-vyos-options.cfg

# Fix 3: add earlycon to boot entry
sudo sed -i 's|set boot_opts="boot=live|set boot_opts="earlycon=uart8250,mmio,0x21c0500 boot=live|' \
    $CFG/vyos-versions/${IMG}.cfg
```

> These fixes are only needed if EFI/GRUB boot becomes viable in the future.
> Currently `vyos_direct` bypasses GRUB entirely.

## Hardware Info

| Field | Value |
|-------|-------|
| Board | `gateway_dk` (Mono Gateway Development Kit) |
| Model | Mono Gateway Development Kit (`mono,gateway-dk`, `fsl,ls1046a`) |
| SoC | QorIQ LS1046A, SVR `0x87070010`, Revision 1.0 |
| CPU | 4× ARM Cortex-A72 (ARMv8-A), `0xd08` rev 2 |
| Features | `fp asimd evtstrm aes pmull sha1 sha2 crc32 cpuid` |
| DRAM | 8 GB DDR4 ECC (Bank 0: 1982 MB, Bank 1: 6144 MB) |
| eMMC | Kingston iNAND 0IM20E, 29.6 GB MMC, HS200 mode |
| Serial | `MT-R01A-0326-00308` |
| Console | `serial@21c0500` (8250 UART, 115200,8n1) |
| USB | XHCI at `usb@2f00000` |
| Ethernet | 5× FMan MEMAC (DPAA1) |
| Crypto | CAAM hardware accelerator (AES, SHA) |
| Thermal | ~42°C (via SoC thermal zone) |
| PCIe | 3 controllers, no devices connected |

### Ethernet Interfaces (confirmed by cable-plug testing, eMMC installed boot)

> ⚠️ Physical RJ45 port order is REVERSED from DT node address order (PCB routing).

| Physical Position | DT Node | MAC Address | PHY Addr | VyOS Name | Type |
|-------------------|---------|-------------|----------|-----------|------|
| Port 1 (leftmost RJ45) | `1ae8000.ethernet` | `E8:F6:D7:00:15:FF` | MDIO :00 | **eth1** | SGMII |
| Port 2 (center RJ45) | `1aea000.ethernet` | `E8:F6:D7:00:16:00` | MDIO :01 | **eth2** | SGMII |
| Port 3 (right RJ45) | `1ae2000.ethernet` | `E8:F6:D7:00:16:01` | MDIO :02 | **eth0** | SGMII |
| SFP1 | `1af0000.ethernet` | `E8:F6:D7:00:16:02` | fixed-link | **eth3** | XGMII 10GBase-R |
| SFP2 | `1af2000.ethernet` | `E8:F6:D7:00:16:03` | fixed-link | **eth4** | XGMII 10GBase-R |

SFP ports always report "Link is Up — 10Gbps/Full" (fixed-link, no PHY polling).
MAC addresses are unique per board — yours will differ. Read from `show interfaces`.

### MAC Addresses (from U-Boot env)

| Variable | Address | VyOS Interface | Physical Position |
|----------|---------|----------------|-------------------|
| `ethaddr` | `E8:F6:D7:00:15:FF` | eth1 | Leftmost RJ45 |
| `eth1addr` | `E8:F6:D7:00:16:00` | eth2 | Center RJ45 |
| `eth2addr` | `E8:F6:D7:00:16:01` | eth0 | Right RJ45 |
| `eth3addr` | `E8:F6:D7:00:16:02` | eth3 | SFP1 |
| `eth4addr` | `E8:F6:D7:00:16:03` | eth4 | SFP2 |

### Clock Tree & CPU Frequency

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

**Fix applied:** `CONFIG_QORIQ_CPUFREQ=y` (built-in) claims PLL clock parents before
`clk: Disabling unused clocks` runs at T+12s. `CONFIG_CPU_FREQ_DEFAULT_GOV_PERFORMANCE=y`
locks all cores at max frequency. Confirmed working: raid6 neonx8 jumped from 2056→4816 MB/s.

### SPI Flash (MTD) Layout

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

> **Note:** MTD is not visible from VyOS (`/proc/mtd` is empty). SPI flash
> is only accessed through U-Boot (`sf` commands) or `fw_setenv` from Linux
> when `/etc/fw_env.config` points at `/dev/mtd3`.

### eMMC Info

```
Model:    Kingston iNAND 0IM20E
Type:     MMC (eMMC)
Size:     29.6 GB
CID:      13014e30494d323045103502d4b58c00
Speed:    HS200
```

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
fsl-ls1046a-rdb.dtb           (27 KB)
```

## Live VyOS State (2026-03-21, eMMC installed)

**Version:** 2026.03.21-0419-rolling
**Kernel:** 6.6.128-vyos `#1 SMP PREEMPT_DYNAMIC`
**FRRouting:** 10.5.2
**Boot source:** eMMC installed (`vyos_direct` booti from mmcblk0p3)
**Boot args:** `console=ttyS0,115200 earlycon=uart8250,mmio,0x21c0500 net.ifnames=0 boot=live rootdelay=5 noautologin vyos-union=/boot/...`

### Interfaces

| Interface | Physical Position | MAC | State | Address |
|-----------|-------------------|-----|-------|---------|
| eth0 | Right RJ45 (port 3) | `E8:F6:D7:00:16:01` | u/D | — |
| eth1 | Leftmost RJ45 (port 1) | `E8:F6:D7:00:15:FF` | u/u | `192.168.1.122/16` |
| eth2 | Center RJ45 (port 2) | `E8:F6:D7:00:16:00` | u/D | — |
| eth3 | SFP1 | `E8:F6:D7:00:16:02` | u/u | — (10Gbps fixed-link) |
| eth4 | SFP2 | `E8:F6:D7:00:16:03` | u/u | — (10Gbps fixed-link) |

### System Resources

| Resource | Value |
|----------|-------|
| CPU frequency | 1800 MHz ✅ (`QORIQ_CPUFREQ=y` fix confirmed) |
| CPU governor | performance |
| Memory total | 7.8 GB |
| Memory used | ~800 MB (10%) |
| Load average | 0.29 |
| Root filesystem | squashfs + overlay (eMMC) |
| Temperature | 42°C |
| Boot time | ~82s to login (single boot, no kexec) |
