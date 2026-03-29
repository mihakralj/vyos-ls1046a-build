# Install Guide — VyOS on Mono Gateway DK (LS1046A)

## Overview

The install process uses two separate artifacts:

| Artifact | Use case |
|----------|---------|
| `vyos-...-LS1046A-arm64-usb.img.zst` | **Initial install** — write to USB, boot, run `install image` |
| `vyos-...-LS1046A-arm64.iso` | **Upgrade only** — passed to `add system image <url>` |

U-Boot reads FAT32. The USB image is a raw FAT32 filesystem — write it with `dd` and U-Boot reads it directly. Never use the ISO for USB boot.

---

## Requirements

- USB flash drive ≥ 4 GB
- Serial console access: USB-to-serial adapter, **115200 8N1**, connected to the Mono Gateway's RJ45 console port
- Linux, macOS, or Windows (Rufus) host

---

## Step 1 — Write USB boot image

Download the latest `vyos-...-LS1046A-arm64-usb.img.zst` from [Releases](https://github.com/mihakralj/vyos-ls1046a-build/releases).

### Linux / macOS

```bash
# Decompress
zstd -d vyos-*-LS1046A-arm64-usb.img.zst

# Find your USB device (e.g. /dev/sdb or /dev/disk2)
lsblk        # Linux
diskutil list  # macOS

# Write (replace /dev/sdX with your USB device — NOT a partition)
sudo dd if=vyos-*-LS1046A-arm64-usb.img of=/dev/sdX bs=4M status=progress
sync
```

### Windows (Rufus)

1. Download [Rufus](https://rufus.ie/)
2. Select the `.img.zst` file
3. When Rufus asks for the write mode, choose **DD Image** (not ISO Image)
4. Click Start

> **Critical:** Rufus "ISO Image" mode restructures the filesystem. DD mode writes it byte-for-byte as U-Boot expects.

---

## Step 2 — Boot from USB

1. Insert the USB drive into the Mono Gateway
2. Connect serial console (115200 8N1)
3. Power on the board

U-Boot will try USB first (`run usb_vyos`). Watch the serial console — you should see:

```
U-Boot ...
...
Hit any key to stop autoboot:  3
...
USB:   USB0:  scanning bus 0 for devices... 1 USB Device(s) found
...
Loading: vmlinuz ... done
Loading: mono-gw.dtb ... done
Loading: initrd.img ... done
...
[    0.000000] Booting Linux on physical CPU 0x0000000000 [0x410fd083]
```

VyOS will boot in live mode. Login with:

- **Username:** `vyos`
- **Password:** `vyos`

**On first USB boot,** the U-Boot SPI flash environment is configured automatically — `bootcmd`, `vyos_direct`, and `usb_vyos` are written to SPI NOR. This is the one-time setup that makes all future eMMC boots work without any U-Boot console interaction.

> **If U-Boot doesn't boot from USB automatically** (board still has factory OpenWrt env), interrupt the boot countdown and type at the U-Boot prompt:
> ```
> run usb_vyos
> ```
> If `usb_vyos` is also not set, see [Manual U-Boot console setup](#manual-u-boot-console-setup) at the end of this guide.

---

## Step 3 — Install to eMMC

From the live VyOS shell:

```
install image
```

The installer uses the squashfs already on the USB — **no internet download needed**. Follow the prompts:

- Select installation target: `sda` is the USB (don't use this), `mmcblk0` is the eMMC — select `mmcblk0`
- Enter a root password
- Accept defaults for the rest

After installation completes, the system automatically:
- Writes `/boot/vyos.env` on eMMC p3 pointing to the new image
- Confirms U-Boot SPI flash env is configured (already done in Step 2)

---

## Step 4 — Reboot from eMMC

Remove the USB drive and reboot:

```
reboot
```

**U-Boot boot sequence:**

1. `run usb_vyos` — fails (no USB) → falls through
2. `run vyos_direct` — reads `/boot/vyos.env` from eMMC p3 → loads `vmlinuz`, `mono-gw.dtb`, `initrd.img` → `booti` ✓

VyOS will boot from eMMC. Login with the password you set during install.

---

## Upgrading

Use `add system image` with the ISO URL (not the USB image):

```
add system image https://github.com/mihakralj/vyos-ls1046a-build/releases/latest/download/vyos-YYYY.MM.DD-HHMM-rolling-LS1046A-arm64.iso
```

After the upgrade completes, `/boot/vyos.env` is automatically updated to the new image name. Reboot when ready:

```
set system image default-boot <new-image-name>
reboot
```

---

## eMMC Partition Layout

After `install image`, the Mono Gateway eMMC (`mmcblk0`) has:

| Partition | U-Boot ref | Type | Contents |
|-----------|-----------|------|---------|
| p1 | `mmc 0:1` | Raw (1 MiB) | BIOS boot gap — no filesystem |
| *(gap)* | — | 16 MiB unallocated | U-Boot environment (SPI NOR, not eMMC) |
| p2 | `mmc 0:2` | FAT32 (256 MiB) | EFI partition — exists but unused on this board |
| **p3** | **`mmc 0:3`** | **ext4** | **VyOS root — kernel, DTB, initrd, squashfs** |

`/boot/vyos.env` lives on p3 (ext4). U-Boot loads it with `ext4load mmc 0:3`.

---

## Boot Variable Reference

| U-Boot variable | Purpose |
|----------------|---------|
| `bootcmd` | `run usb_vyos \|\| run vyos_direct \|\| run recovery` |
| `usb_vyos` | FAT32 USB live boot — loads `live/vmlinuz`, `mono-gw.dtb`, `live/initrd.img` |
| `vyos_direct` | eMMC boot — reads `/boot/vyos.env`, loads image, calls `booti` |
| `recovery` | SPI NOR fallback — loads factory firmware |

| Address variable | Value | Role |
|-----------------|-------|------|
| `kernel_addr_r` | `0x82000000` | Kernel `Image` load address |
| `fdt_addr_r` | `0x88000000` | DTB load address |
| `ramdisk_addr_r` | `0x88080000` | initrd load address |
| `load_addr` | `0xa0000000` | Scratch (used for `vyos.env` import) |

---

## Manual U-Boot Console Setup

**Use this only if** the board fails to boot from USB automatically, or if U-Boot still has the factory OpenWrt env (no `vyos_direct` variable referencing `vyos.env`).

Connect the serial console, interrupt the boot countdown (press any key), then paste these commands at the `=>` prompt:

```
setenv vyos_direct 'ext4load mmc 0:3 ${load_addr} /boot/vyos.env; env import -t ${load_addr} ${filesize}; ext4load mmc 0:3 ${kernel_addr_r} /boot/${vyos_image}/vmlinuz; ext4load mmc 0:3 ${fdt_addr_r} /boot/${vyos_image}/mono-gw.dtb; ext4load mmc 0:3 ${ramdisk_addr_r} /boot/${vyos_image}/initrd.img; setenv bootargs "BOOT_IMAGE=/boot/${vyos_image}/vmlinuz console=ttyS0,115200 earlycon=uart8250,mmio,0x21c0500 net.ifnames=0 boot=live rootdelay=5 noautologin fsl_dpaa_fman.fsl_fm_max_frm=9600 hugepagesz=2M hugepages=512 panic=60 vyos-union=/boot/${vyos_image}"; booti ${kernel_addr_r} ${ramdisk_addr_r}:${filesize} ${fdt_addr_r}'

setenv usb_vyos 'usb start; if fatload usb 0:1 ${kernel_addr_r} live/vmlinuz; then fatload usb 0:1 ${fdt_addr_r} mono-gw.dtb; fatload usb 0:1 ${ramdisk_addr_r} live/initrd.img; setenv bootargs "BOOT_IMAGE=/live/vmlinuz console=ttyS0,115200 earlycon=uart8250,mmio,0x21c0500 boot=live live-media=/dev/sda1 components noeject nopersistence noautologin nonetworking union=overlay net.ifnames=0 fsl_dpaa_fman.fsl_fm_max_frm=9600 quiet"; booti ${kernel_addr_r} ${ramdisk_addr_r}:${filesize} ${fdt_addr_r}; fi'

setenv bootcmd 'run usb_vyos || run vyos_direct || run recovery'

saveenv
reset
```

### Emergency eMMC boot (one-shot, no saveenv)

If VyOS is installed but `vyos.env` is missing or corrupt, boot manually. First find the image name:

```
ext4ls mmc 0:3 /boot
```

Then boot (replace `2026.03.27-0142-rolling` with the directory name you saw):

```
setenv vyos_image 2026.03.27-0142-rolling
ext4load mmc 0:3 ${kernel_addr_r} /boot/${vyos_image}/vmlinuz
ext4load mmc 0:3 ${fdt_addr_r} /boot/${vyos_image}/mono-gw.dtb
ext4load mmc 0:3 ${ramdisk_addr_r} /boot/${vyos_image}/initrd.img
setenv bootargs "BOOT_IMAGE=/boot/${vyos_image}/vmlinuz console=ttyS0,115200 earlycon=uart8250,mmio,0x21c0500 net.ifnames=0 boot=live rootdelay=5 noautologin fsl_dpaa_fman.fsl_fm_max_frm=9600 hugepagesz=2M hugepages=512 panic=60 vyos-union=/boot/${vyos_image}"
booti ${kernel_addr_r} ${ramdisk_addr_r}:${filesize} ${fdt_addr_r}
```

Once VyOS boots, reboot normally — the U-Boot environment and `vyos.env` are fixed automatically on the next clean boot.

---

## See Also

- **[BOOT-PROCESS.md](BOOT-PROCESS.md)** — Complete technical specification: U-Boot variable definitions, annotated boot sequences for both USB and eMMC paths, `vyos.env` write paths, DTB delivery, kexec prevention, SPI NOR layout, and all documented failure modes
- **[UBOOT.md](UBOOT.md)** — U-Boot serial console reference: memory map, working boot commands, clock tree, MTD layout
- **[PORTING.md](PORTING.md)** — Why 13 things were broken and how each was fixed
