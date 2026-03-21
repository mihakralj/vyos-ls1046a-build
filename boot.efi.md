# U-Boot & Boot Reference — Mono Gateway LS1046A

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

## Default Boot Commands

```bash
# Default: try eMMC OpenWrt, then SPI recovery
bootcmd=run emmc || run recovery

# eMMC (OpenWrt on partition 1)
emmc=setenv bootargs "${bootargs_console} root=/dev/mmcblk0p1 rw rootwait rootfstype=ext4";
    ext4load mmc 0:1 ${kernel_addr_r} /boot/Image.gz &&
    ext4load mmc 0:1 ${fdt_addr_r} /boot/mono-gateway-dk-sdk.dtb &&
    booti ${kernel_addr_r} - ${fdt_addr_r}

# SPI flash recovery
recovery=sf probe 0:0; sf read ${kernel_addr_r} ${kernel_addr} ${kernel_size};
    sf read ${fdt_addr_r} ${fdt_addr} ${fdt_size};
    booti ${kernel_addr_r} - ${fdt_addr_r}
```

## VyOS Boot (eMMC live-boot — OBSOLETE)

> **This section is obsolete.** The eMMC was repartitioned by `install image`.
> Old layout (OpenWrt p1 + VyOS ext4 p2) is gone. See "VyOS Installed Boot" below.

```bash
# OLD — no longer valid after install image rewrote GPT
setenv vyos 'setenv bootargs "console=ttyS0,115200 earlycon=uart8250,mmio,0x21c0500 boot=live live-media=/dev/mmcblk0p2 components noeject nopersistence noautologin nonetworking union=overlay net.ifnames=0 quiet"; ext4load mmc 0:2 ${kernel_addr_r} /live/vmlinuz-6.6.128-vyos; ext4load mmc 0:2 ${fdt_addr_r} /mono-gw.dtb; ext4load mmc 0:2 ${ramdisk_addr_r} /live/initrd.img-6.6.128-vyos; booti ${kernel_addr_r} ${ramdisk_addr_r}:${filesize} ${fdt_addr_r}'
```

## VyOS Boot from USB (for install image)

Write ISO to USB with Rufus (ISO Image mode). Copy `mono-gw.dtb` to USB root. Then:

```bash
usb start
setenv bootargs "console=ttyS0,115200 earlycon=uart8250,mmio,0x21c0500 boot=live live-media=/dev/sda1 components noeject nopersistence noautologin nonetworking union=overlay net.ifnames=0 quiet"
fatload usb 0:1 ${kernel_addr_r} live/vmlinuz-6.6.128-vyos
fatload usb 0:1 ${fdt_addr_r} mono-gw.dtb
fatload usb 0:1 ${ramdisk_addr_r} live/initrd.img-6.6.128-vyos
booti ${kernel_addr_r} ${ramdisk_addr_r}:${filesize} ${fdt_addr_r}
```

**Critical:** Initrd must be loaded LAST so `${filesize}` captures the initrd size (not kernel/DTB).
The ramdisk arg MUST be `${ramdisk_addr_r}:${filesize}` (with colon+size), not just `${ramdisk_addr_r}`.

## Failed Boot Attempts

### `booti` without `:${filesize}` on ramdisk
```bash
fatload usb 0:1 ${kernel_addr_r} live/vmlinuz
fatload usb 0:1 ${ramdisk_addr_r} live/initrd.img
booti ${kernel_addr_r} ${ramdisk_addr_r} ${fdt_addr_r}
# "Wrong Ramdisk Image Format / Ramdisk image is corrupt or invalid"
# Fix: use ${ramdisk_addr_r}:${filesize} — booti needs addr:size format
```

### `booti` kernel-only (no initrd, stale bootargs)
```bash
booti ${kernel_addr_r} - ${fdt_addr_r}
# Kernel boots fine (all 5 FMan MACs probe!) but hangs:
#   "Waiting for root device /dev/mmcblk0p1..."
# Cause: bootargs still "root=/dev/mmcblk0p1" from default env.
#   No initrd loaded = no live-boot initramfs = can't mount squashfs.
```

### GRUB config console bug (ISO grub.cfg)

The ISO's `/boot/grub/grub.cfg` hardcodes `console=ttyAMA0,115200` (PL011 UART)
in every `menuentry`. On this board the UART is 8250 at `ttyS0`. Serial output
will be silent during and after GRUB if booted this way.

- **Live USB session via GRUB** — no serial console, but SSH works fine
- **Installed GRUB** — correct: `default-union-grub-entry` is patched to
  `ttyS0` by the build workflow (`sed -i 's/ttyAMA0/ttyS0/g'`), so the
  post-`install image` GRUB config will have the right console
- **Workaround for ISO** — always use U-Boot `booti` with explicit
  `console=ttyS0,115200` bootargs (current working method)

### EFI/GRUB boot (OOM — confirmed broken even with fdt_high)
```bash
# Attempted with fdt_high=0xffffffffffffffff (set inside vyos_efi):
# fatload mmc 0:2 ${fdt_addr_r} mono-gw.dtb
# fatload mmc 0:2 ${kernel_addr_r} EFI/BOOT/BOOTAA64.EFI
# bootefi ${kernel_addr_r} ${fdt_addr_r}
# "Failed to load EFI variables"
# "out of memory" x2 / "Loading image failed"
#
# Root cause confirmed: DTB reserved-memory nodes for DPAA1:
#   qman-pfdr: 0x9fc000000..0x9fdffffff (32 MB) nomap
#   qman-fqd:  0x9fe800000..0x9feffffff (8 MB)  nomap
#   bman-fbpr: 0x9ff000000..0x9ffffffff (16 MB) nomap
# These sit at the top of Bank 1 (0x880000000-0x9ffffffff).
# U-Boot's bootefi cannot initialize EFI properly with this layout —
# GRUB starts but immediately OOMs during its own heap setup.
# fdt_high does not fix this; it is a U-Boot EFI limitation on LS1046A.
# Use vyos_direct (booti) as the permanent boot method.
```

## VyOS Installed Boot (after `install image` to eMMC)

After `install image` creates GPT on eMMC:

- `mmcblk0p1`: BIOS boot (1 MiB)
- gap: 16 MiB (bootloader payload, per our patch)
- `mmcblk0p2`: EFI System (256 MiB, FAT32)
- `mmcblk0p3`: Linux root (ext4, rest of disk)

```bash
# Boot installed VyOS — correct bootargs for VyOS squashfs+overlay
setenv vyos_direct 'setenv bootargs "console=ttyS0,115200 earlycon=uart8250,mmio,0x21c0500 net.ifnames=0 boot=live rootdelay=5 noautologin vyos-union=/boot/2026.03.20-2209-rolling"; ext4load mmc 0:3 ${kernel_addr_r} /boot/2026.03.20-2209-rolling/vmlinuz; ext4load mmc 0:3 ${fdt_addr_r} /boot/2026.03.20-2209-rolling/mono-gw.dtb; ext4load mmc 0:3 ${ramdisk_addr_r} /boot/2026.03.20-2209-rolling/initrd.img; booti ${kernel_addr_r} ${ramdisk_addr_r}:${filesize} ${fdt_addr_r}'
run vyos_direct
```

**Critical bootargs for installed VyOS:**
- `boot=live` — tells the initramfs to use live-boot mode
- `vyos-union=/boot/<IMAGE>` — path to the squashfs overlay dir on p3
- Missing either parameter drops to an initramfs BusyBox shell

Replace `2026.03.20-2209-rolling` with the actual image name.

Post-install, `mono-gw.dtb` must be placed in two locations (done once):

```bash
# Run from live USB session before rebooting:
sudo mkdir -p /mnt/efi /mnt/root
sudo mount /dev/mmcblk0p2 /mnt/efi
sudo mount /dev/mmcblk0p3 /mnt/root

# EFI partition root: U-Boot fatload reads from here for bootefi
sudo cp /usr/lib/live/mount/medium/mono-gw.dtb /mnt/efi/mono-gw.dtb

# Boot image dir: U-Boot ext4load reads from here for booti fallback
sudo cp /usr/lib/live/mount/medium/mono-gw.dtb \
    /mnt/root/boot/2026.03.20-2209-rolling/mono-gw.dtb

sudo umount /mnt/efi /mnt/root
```

**GRUB config fixes applied post-install** (these are already done on this board,
but must be re-applied after each `install image` or new image version):

```bash
# Fix 1: default console tty -> ttyS
sed -i 's/set console_type="tty"/set console_type="ttyS"/' \
    /boot/grub/grub.cfg.d/20-vyos-defaults-autoload.cfg

# Fix 2: ARM64 ttyAMA -> ttyS (GRUB hardcodes ttyAMA for arm64)
sed -i 's/set serial_console="ttyAMA"/set serial_console="ttyS"/' \
    /boot/grub/grub.cfg.d/50-vyos-options.cfg

# Fix 3: add earlycon to boot entry
sed -i 's|set boot_opts="boot=live|set boot_opts="earlycon=uart8250,mmio,0x21c0500 boot=live|' \
    /boot/grub/grub.cfg.d/vyos-versions/<IMAGE_NAME>.cfg
```

In U-Boot, save the persistent boot command (try EFI first, fall back to booti):

```
setenv vyos_efi 'setenv fdt_high 0xffffffffffffffff; fatload mmc 0:2 ${fdt_addr_r} mono-gw.dtb; fatload mmc 0:2 ${kernel_addr_r} EFI/BOOT/BOOTAA64.EFI; bootefi ${kernel_addr_r} ${fdt_addr_r}'
setenv vyos_direct 'setenv bootargs "console=ttyS0,115200 earlycon=uart8250,mmio,0x21c0500 net.ifnames=0 boot=live rootdelay=5 noautologin vyos-union=/boot/2026.03.20-2209-rolling"; ext4load mmc 0:3 ${kernel_addr_r} /boot/2026.03.20-2209-rolling/vmlinuz; ext4load mmc 0:3 ${fdt_addr_r} /boot/2026.03.20-2209-rolling/mono-gw.dtb; ext4load mmc 0:3 ${ramdisk_addr_r} /boot/2026.03.20-2209-rolling/initrd.img; booti ${kernel_addr_r} ${ramdisk_addr_r}:${filesize} ${fdt_addr_r}'
setenv bootcmd 'run vyos_efi || run vyos_direct || run recovery'
saveenv
```

EFI path (via GRUB) enables `add system image`. Direct booti path is the fallback
if EFI OOM is unresolved — requires manually updating `vyos_direct` after each upgrade.

## Running `install image`

From the live USB session (currently running at `vyos@192.168.1.120`):

```
install image
```

When asked for the target disk, select `/dev/mmcblk0`.

> **eMMC current state:** GPT exists but all 3 partitions are empty (no
> filesystems). `install image` will recreate the partition table and format
> everything. OpenWrt is already gone from a prior interrupted install.

Expected layout after completion:

```
/dev/mmcblk0p1   2048–4095        1 MB    BIOS Boot  (EF02)  raw
/dev/mmcblk0p2  36864–561151     256 MB    EFI System (EF00)  FAT32 ← GRUB
/dev/mmcblk0p3 561152–end        29.4 GB   Linux      (8300)  ext4  ← VyOS
```

Gap at sectors 4096–36863 (16 MB) is from `vyos-1x-006-install-image-reserve-gap.patch`.

After `install image` completes, do NOT reboot yet. Copy `mono-gw.dtb` as
described in the section above, then update U-Boot env via serial before rebooting.

## Future Image Updates

Once GRUB is managing boot from eMMC:

```
add system image <url>
```

GRUB automatically adds the new image to its menu. U-Boot's `bootefi` or
`booti` command in `bootcmd` does not change — GRUB handles image selection.

## Hardware Info

| Field | Value |
|-------|-------|
| Board | `gateway_dk` (Mono Gateway Development Kit) |
| Model | Mono Gateway Development Kit (`mono,gateway-dk`, `fsl,ls1046a`) |
| SoC | QorIQ LS1046A, SVR `0x87070010`, Revision 1.0 |
| CPU | 4× ARM Cortex-A72 (ARMv8-A), `0xd08` rev 2 |
| Features | `fp asimd evtstrm aes pmull sha1 sha2 crc32 cpuid` |
| DRAM | 8 GB DDR4 ECC (Bank 0: 1982 MB, Bank 1: 6144 MB) |
| eMMC | Kingston iNAND 0IM20E, 29.6 GB MMC |
| Serial | `MT-R01A-0326-00308` |
| Console | `serial@21c0500` (8250 UART, 115200,8n1) |
| USB | XHCI at `usb@2f00000` |
| Ethernet | 5× FMan MEMAC (DPAA1) |
| Crypto | CAAM hardware accelerator (AES, SHA) |
| Thermal | 42°C (via SoC thermal zone) |
| PCIe | 3 controllers, no devices connected |

### Ethernet Interfaces (confirmed by cable-plug testing, eMMC boot)

> ⚠️ Physical RJ45 port order is REVERSED from DT node address order (PCB routing).

| Physical Position | DT Node | MAC Address | PHY Addr | VyOS Name | Type |
|-------------------|---------|-------------|----------|-----------|------|
| Port 1 (leftmost RJ45) | `1ae8000.ethernet` | `E8:F6:D7:00:15:FF` | MDIO :00 | **eth1** | SGMII |
| Port 2 (center RJ45) | `1aea000.ethernet` | `E8:F6:D7:00:16:00` | MDIO :01 | **eth2** | SGMII |
| Port 3 (right RJ45) | `1ae2000.ethernet` | `E8:F6:D7:00:16:01` | MDIO :02 | **eth0** | SGMII |
| SFP1 | `1af0000.ethernet` | `E8:F6:D7:00:16:02` | fixed-link | **eth3** | XGMII 10GBase-R |
| SFP2 | `1af2000.ethernet` | `E8:F6:D7:00:16:03` | fixed-link | **eth4** | XGMII 10GBase-R |

SFP ports always report "Link is Up — 10Gbps/Full" (fixed-link, no PHY polling).

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
| `cg-pll2-div2` | 700 MHz | PLL2 | **Current CPU clock** (too slow!) |
| `cg-pll2-div3` | 466 MHz | PLL2 | |
| `cg-pll2-div4` | 350 MHz | PLL2 | |
| `cg-cmux0` | 700 MHz | PLL2-div2 | CPU clock mux (all 4 cores) |
| `cg-hwaccel0` | 700 MHz | PLL2-div2 | FMan clock |
| `cg-pll0-div2` | 300 MHz | PLL0 | SPI (DSPI controller) |

**CPU frequency scaling bug:** The `qoriq-cpufreq` driver (built as module, `=m`)
loads at T+28s, but `clk: Disabling unused clocks` runs at T+12s. By the time
the cpufreq module loads, the CMUX only reports 1 available frequency (700 MHz).
The hardware supports 4 frequencies via `t1040_cmux`: 1600, 800, 1400, 700 MHz.
**Fix:** `CONFIG_QORIQ_CPUFREQ=y` + `CONFIG_CPU_FREQ_DEFAULT_GOV_PERFORMANCE=y`

### SPI Flash (MTD) Layout

```
1550000.spi (U-Boot only — not exposed to Linux via /proc/mtd):
  1M(rcw-bl2)         — Reset Config Word + BL2
  2M(uboot)           — U-Boot
  1M(uboot-env)       — U-Boot environment
  1M(fman-ucode)      — FMan microcode (injected to DTB at boot)
  1M(recovery-dtb)    — Recovery device tree
  4M(unallocated)
  -(kernel-initramfs)  — Recovery kernel + initramfs
```

> **Note:** MTD is not visible from VyOS (`/proc/mtd` is empty) because the
> `CONFIG_MTD_SPI_NOR=m` module loads but the SPI flash may already be claimed
> by U-Boot or the DTB doesn't expose it to Linux. SPI flash is only accessed
> through U-Boot (`sf` commands).

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

### eMMC Layout (post-install image)

```
mmcblk0       29.6 GB total (GPT)
├─ mmcblk0p1      1 MB    BIOS boot (EF02)  — raw
├─ (16 MB gap)            bootloader payload (our patch)
├─ mmcblk0p2    256 MB    EFI System (EF00) — FAT32, GRUB
└─ mmcblk0p3   29.4 GB   Linux root (8300) — ext4, VyOS
```

### Running Services

Key services: `ssh`, `frr`, `chrony`, `fastnetmon`, `dhclient@eth{0-4}`,
`vyos-configd`, `vyos-commitd`, `vyos-hostsd`, `vyos-system-update`

### System Resources

| Resource | Value |
|----------|-------|
| CPU frequency | 1800 MHz ✅ (`QORIQ_CPUFREQ=y` fix) |
| CPU governor | performance |
| Memory total | 7.8 GB |
| Memory used | ~800 MB (10%) |
| Load average | 0.29 |
| Root filesystem | squashfs + overlay (eMMC) |
| Temperature | 42°C |
| Boot time | ~82s to login (no kexec double-boot) |
