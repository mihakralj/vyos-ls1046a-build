# Installing VyOS on Mono Gateway Development Kit

Complete guide: factory board (OpenWrt on eMMC) → VyOS installed to eMMC,
booting via U-Boot direct load (`booti`).

**Time required:** ~15 minutes
**Serial console required:** yes, for U-Boot access
**Internet required:** no (everything is on the USB)

---

## Prerequisites

| Item | Details |
|------|---------|
| Hardware | Mono Gateway Development Kit (NXP LS1046A) |
| USB drive | Any size ≥ 2 GB — will be overwritten |
| VyOS ISO | Download from [Releases](https://github.com/mihakralj/vyos-ls1046a-build/releases/latest) — filename `vyos-*-LS1046A-arm64.iso` |
| Serial cable | USB-to-TTL adapter — **115200 8N1**, no flow control |
| Serial software | Windows: PuTTY / plink; Linux/macOS: `tio`, `screen`, `minicom` |

> **Use only ISOs from this repository.** Generic VyOS ARM64 ISOs do not include
> the LS1046A drivers (DPAA1/FMan, eSDHC) and will boot with no networking and
> no eMMC.

---

## Board Overview

```
CPU:     4x ARM Cortex-A72 @ 1.8 GHz (NXP LS1046A)
RAM:     8 GB DDR4
eMMC:    29.6 GB (mmcblk0)
Serial:  ttyS0, 115200 8N1
U-Boot:  SPI flash, version 2025.04
Boot:    booti (direct kernel load, NOT EFI/GRUB)
```

**After installation:**
```
mmcblk0p1     1 MB   BIOS Boot  (EF02)
mmcblk0p2   256 MB   EFI System (EF00)   <- unused (GRUB installed but U-Boot ignores it)
mmcblk0p3  29.4 GB   Linux      (8300)   <- ext4: VyOS images, config, data
```

> **OpenWrt is destroyed.** `install image` rewrites the entire GPT on mmcblk0.
> There is no recovery back to OpenWrt without reflashing eMMC.
> The SPI flash (U-Boot, recovery kernel) is untouched.

---

## Step 1 — Prepare USB Drive

Write the ISO to the USB drive:

**Windows — Rufus:**
1. Open Rufus, select the USB drive
2. Click **SELECT** and choose the `.iso` file
3. Partition scheme: **MBR**
4. Click **START** — when prompted, choose **Write in ISO Image mode**

**Linux / macOS:**
```bash
sudo dd if=vyos-*-LS1046A-arm64.iso of=/dev/sdX bs=4M status=progress
sudo sync
```

---

## Step 2 — Connect Serial Console

Connect the USB-to-TTL adapter to the board's serial header.
Configure your terminal at **115200 8N1**, no flow control.

**Windows:**
```
plink -serial COM7 -sercfg 115200,8,n,1,N
```

**Linux / macOS:**
```bash
tio /dev/ttyUSB0 -b 115200
```

Power on the board. U-Boot messages appear within 1–2 seconds.

---

## Step 3 — Boot VyOS Live from USB

Insert the USB drive. Press **any key** during the 5-second U-Boot countdown
to reach the `=>` prompt.

Paste this single line:

```
usb start; setenv bootargs "console=ttyS0,115200 earlycon=uart8250,mmio,0x21c0500 boot=live live-media=/dev/sda1 components noeject nopersistence noautologin nonetworking union=overlay net.ifnames=0 quiet"; fatload usb 0:1 ${kernel_addr_r} live/vmlinuz; fatload usb 0:1 ${fdt_addr_r} mono-gw.dtb; fatload usb 0:1 ${ramdisk_addr_r} live/initrd.img; booti ${kernel_addr_r} ${ramdisk_addr_r}:${filesize} ${fdt_addr_r}
```

Expected output:
```
9210147 bytes read in ...    <- kernel
94208 bytes read in ...      <- dtb
33287447 bytes read in ...   <- initrd
Starting kernel ...
[    0.000000] Booting Linux on physical CPU 0x0000000000 [0x410fd083]
[    0.000000] Machine model: Mono Gateway Development Kit
```

VyOS boots in 60–90 seconds and shows a login prompt.

> **Load order matters.** `booti` uses `${filesize}` for the initrd size, so
> initrd must be loaded last. Wrong order causes a ramdisk panic.

> **If `fatload` fails with "File not found":** check the filenames with
> `fatls usb 0:1 live` — if the kernel has a version suffix like
> `vmlinuz-6.6.128-vyos`, use the full filename instead of `live/vmlinuz`.

---

## Step 4 — Install to eMMC

Log in with `vyos` / `vyos`, then:

```
install image
```

Answer the prompts:

| Prompt | Answer |
|--------|--------|
| Would you like to continue? | `y` |
| Image name? | Press Enter (accept default) |
| Password for "vyos" user | `vyos` (or your choice) |
| Console type? (K/S) | `S` |
| Configure RAID-1? | `n` |
| Which disk? | `/dev/mmcblk0` |
| Delete all data? | `y` |
| Use all free space? | Press Enter |
| Boot config? | Press Enter |

Wait 2–4 minutes for installation to complete.

> **DTB is copied automatically.** The ISO includes `mono-gw.dtb` inside the
> squashfs at `/boot/`, and `install image` copies all files from `/boot/` to the
> target. No manual DTB copy is needed.

---

## Step 5 — Configure U-Boot for eMMC Boot

After `install image` completes, run:

```bash
sudo vyos-postinstall
```

This script:
1. Detects the LS1046A board
2. Verifies the DTB was copied to the boot directory
3. Updates U-Boot environment via `fw_setenv` so the board boots the new image

Expected output:
```
Auto-detected image: 2026.03.21-2155-rolling
=== VyOS Post-Install for LS1046A ===
Image:  2026.03.21-2155-rolling
Root:   /

✓ DTB copied: /boot/mono-gw.dtb → /boot/2026.03.21-2155-rolling/mono-gw.dtb
✓ U-Boot env updated: vyos_direct → boot/2026.03.21-2155-rolling/
✓ U-Boot env updated: bootcmd → 'run vyos_direct || run recovery'

=== Done. You can now reboot. ===
```

---

## Step 6 — Reboot and Verify

```
reboot
```

**Remove the USB drive** as the board restarts. U-Boot will automatically boot
VyOS from eMMC. Expected serial output:

```
9210147 bytes read in 381 ms (23.1 MiB/s)     <- vmlinuz
94208 bytes read in 5 ms (18 MiB/s)           <- mono-gw.dtb
33287447 bytes read in 1373 ms (23.1 MiB/s)   <- initrd.img
Starting kernel ...
```

Login: `vyos` / `vyos` (or your chosen password from Step 4).

---

## Step 7 — Initial Configuration

```
configure
set interfaces ethernet eth0 address dhcp
set service ssh
commit
save
```

Verify connectivity:
```
show interfaces
ping 8.8.8.8 count 3
```

---

## Network Interfaces

The udev rules in this build remap FMan MAC addresses to match
physical port order (left to right on the back panel):

| Physical Position | Type | VyOS Name | FMan Address | Notes |
|-------------------|------|-----------|--------------|-------|
| Port 1 (leftmost RJ45) | RJ45 | `eth0` | `1ae8000` | Management port (default config) |
| Port 2 (center RJ45) | RJ45 | `eth1` | `1aea000` | |
| Port 3 (rightmost RJ45) | RJ45 | `eth2` | `1ae2000` | |
| SFP+ slot 1 | SFP+ | `eth3` | `1af0000` | 10GBase-R |
| SFP+ slot 2 | SFP+ | `eth4` | `1af2000` | 10GBase-R |

MAC addresses are unique per board — read yours from `show interfaces`.

---

## Image Upgrades

After each `add system image`, run `vyos-postinstall` to copy the DTB and
update U-Boot:

```
add system image https://github.com/mihakralj/vyos-ls1046a-build/releases/download/<version>/vyos-<version>-LS1046A-arm64.iso
```

```bash
sudo vyos-postinstall <new-image-name>
```

```
reboot
```

> **Use only ISOs from this repository** — generic ARM64 ISOs lack the LS1046A
> kernel drivers.

---

## Recovery

If U-Boot cannot find `mono-gw.dtb`, it falls to SPI flash recovery Linux:

```
Mono Recovery Linux 1.0 recovery /dev/ttyS0
```

Log in as `root` (no password) and copy the DTB from the running kernel:

```bash
mkdir -p /tmp/vyos
mount /dev/mmcblk0p3 /tmp/vyos
IMG=$(ls /tmp/vyos/boot/ | grep -vE 'grub|efi|lost' | head -1)
cp /sys/firmware/fdt /tmp/vyos/boot/${IMG}/mono-gw.dtb
sync
umount /tmp/vyos
reboot
```

U-Boot's saved `bootcmd` will autoboot VyOS — no need to re-enter `setenv`
commands (they were saved to SPI flash by `vyos-postinstall`).

---

## Troubleshooting

| Symptom | Cause | Fix |
|---------|-------|-----|
| USB not detected after `usb start` | USB 3.x compatibility | Try `usb reset` or a USB 2.0 drive |
| `fatload` "File not found" | ISO written incorrectly | Re-write with Rufus in ISO Image mode |
| Silent after "Starting kernel..." | Missing `earlycon` in bootargs | Verify `printenv bootargs` includes `earlycon=uart8250,mmio,0x21c0500` |
| `Failed to load '...mono-gw.dtb'` | DTB not in boot dir | See Recovery section above |
| No networking (eth0–eth4 missing) | Wrong ISO (no DPAA1 drivers) | Use only ISOs from this repo |
| `vyos-postinstall` says "fw_setenv not found" | u-boot-tools not installed | `sudo apt-get install u-boot-tools` (should be pre-installed) |

---

## Reference

### U-Boot Memory Map

| Variable | Address |
|----------|---------|
| `kernel_addr_r` | `0x82000000` |
| `fdt_addr_r` | `0x88000000` |
| `ramdisk_addr_r` | `0x88080000` |

### SPI Flash Layout

```
mtd1    1 MB    rcw-bl2           ARM Trusted Firmware BL2
mtd2    2 MB    uboot             U-Boot
mtd3    1 MB    uboot-env         U-Boot environment (fw_setenv target)
mtd4    1 MB    fman-ucode        FMan microcode
mtd5    1 MB    recovery-dtb      Recovery boot device tree
mtd7   22 MB    kernel-initramfs  Recovery kernel + initramfs
```

---

## See Also

- [PORTING.md](PORTING.md) — LS1046A kernel driver requirements and boot architecture
- [boot.efi.md](boot.efi.md) — U-Boot EFI analysis, confirmed commands, failure modes
