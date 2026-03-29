# Install Guide — VyOS on Mono Gateway DK (LS1046A)

## Overview

The install process uses two separate artifacts:

| Artifact | Use case |
|----------|---------|
| `vyos-...-LS1046A-arm64-usb.img` | **Initial install** — write to USB, boot, run `install image` |
| `vyos-...-LS1046A-arm64.iso` | **Upgrade only** — passed to `add system image <url>` |

U-Boot reads FAT32. The USB image is a raw FAT32 filesystem — write it with `dd` and U-Boot reads it directly. Never use the ISO for USB boot.

---

## Before You Install

Review the [open issues](https://github.com/mihakralj/vyos-ls1046a-build/issues) before proceeding. This is an experimental port with known limitations.

---

## Requirements

- USB flash drive ≥ 4 GB
- Serial console access: USB-to-serial adapter, **115200 8N1**, connected to the Mono Gateway's RJ45 console port
- Linux, macOS, or Windows (Rufus) host

---

## Step 1 — Write USB boot image

Download the latest `vyos-...-LS1046A-arm64-usb.img` from [Releases](https://github.com/mihakralj/vyos-ls1046a-build/releases).

> **Important:** The `.img` file is a raw FAT32 disk image — write it directly with `dd` or Rufus. Do **not** use the `.iso` file for USB boot — U-Boot cannot read ISO 9660.

### Windows - Rufus

1. Download [Rufus](https://rufus.ie/)
2. Select the `.img` file — Rufus detects DD Image mode automatically — just click **Start**

### macOS - dd

```bash
# Identify USB device
diskutil list    # Look for your USB (e.g., /dev/disk2)

# Unmount (do NOT eject — just unmount)
diskutil unmountDisk /dev/diskN

# Write directly (use rdiskN for raw device — 10x faster than diskN)
sudo dd if=vyos-*-LS1046A-arm64-usb.img of=/dev/rdiskN bs=4m
```

> **Verify the write:** After writing, the USB should show as a FAT32 volume named `VYOSBOOT` containing `/live/vmlinuz`, `/live/initrd.img`, `/live/filesystem.squashfs`, `mono-gw.dtb`, and `boot.scr`.

### Linux - dd

```bash
# Identify USB device (look for your USB drive size — NOT a partition like sdb1)
lsblk

# Unmount any auto-mounted partitions
sudo umount /dev/sdX* 2>/dev/null

# Write directly (replace /dev/sdX with your USB device)
sudo dd if=vyos-*-LS1046A-arm64-usb.img of=/dev/sdX bs=4M status=progress conv=fsync
```


---

## Step 2 — Boot from USB

1. Insert the USB drive into the Mono Gateway
2. Connect serial console (115200 8N1)
3. Power on and **press any key** during the U-Boot countdown to stop autoboot

Factory U-Boot boots OpenWrt from eMMC (`bootcmd=run emmc || run recovery`). It has no USB boot command, so you must manually tell it to boot from USB.

At the `=>` prompt, paste this single line:

```
usb start; fatload usb 0:0 ${load_addr} boot.scr; source ${load_addr}
```

This loads `boot.scr` from the USB - a U-Boot script that handles everything (kernel, DTB, initrd, bootargs, `booti`). Nothing is written to SPI flash — it's a one-shot boot.

Watch the boot log for 60–90 seconds until system gets to VyOS login prompt.

> **If `usb start` hangs or shows no devices:** Try a USB 2.0 drive. Some USB 3.0 drives aren't detected by the LS1046A USB controller.

> **USB addressing:** The USB image is whole-disk FAT32 with no MBR partition table. U-Boot accesses it as `usb 0:0` (whole disk), not `usb 0:1` (first partition). The kernel sees it as `/dev/sda` (not `/dev/sda1`).

---

## Step 3 — Install to eMMC

From the live VyOS shell:

Login with **vyos** / **vyos**.

```
install image
```

- Select installation target: `mmcblk0` (`mmcblk0` is the eMMC, `sda` is the USB)
- Enter a root password
- Accept defaults for the rest

After installation completes, the system automatically:
- Writes `/boot/vyos.env` on eMMC p3 pointing to the new image
- Writes `vyos`, `usb_vyos`, and `bootcmd` to U-Boot SPI flash via `fw_setenv`

> This one-time SPI flash setup makes all future boots automatic — U-Boot tries USB first, then eMMC, then SPI recovery. No more manual U-Boot commands.

---

## Step 4 — Reboot from eMMC

Remove the USB drive and reboot:

```
reboot
```

**U-Boot boot sequence:**

1. `run usb_vyos` — fails (no USB) → falls through
2. `run vyos` — reads `/boot/vyos.env` from eMMC p3 → loads `vmlinuz`, `mono-gw.dtb`, `initrd.img` → `booti` ✓

VyOS will boot from eMMC. Login with the password you set during install.

---

## Upgrading

From the VyOS CLI:

```
add system image latest
reboot
```

`latest` is a built-in alias that checks the update server for the newest release. No need to find or paste a URL.

Alternatively, specify a URL directly (e.g. for a specific version):

```
add system image https://github.com/mihakralj/vyos-ls1046a-build/releases/latest/download/vyos-YYYY.MM.DD-HHMM-rolling-LS1046A-arm64.iso
```

After the upgrade completes, `/boot/vyos.env` is automatically updated to the new image. Reboot when ready.

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
| `bootcmd` | `run usb_vyos \|\| run vyos \|\| run recovery` |
| `usb_vyos` | FAT32 USB live boot — loads `live/vmlinuz`, `mono-gw.dtb`, `live/initrd.img` |
| `vyos` | eMMC boot — reads `/boot/vyos.env`, loads image, calls `booti` |
| `recovery` | SPI NOR fallback — loads factory firmware |

| Address variable | Value | Role |
|-----------------|-------|------|
| `kernel_addr_r` | `0x82000000` | Kernel `Image` load address |
| `fdt_addr_r` | `0x88000000` | DTB load address |
| `ramdisk_addr_r` | `0x88080000` | initrd load address |
| `load_addr` | `0xa0000000` | Scratch (used for `vyos.env` import) |

---

## Manual U-Boot Console Setup

**Use this only if** the board fails to boot from USB automatically, or if U-Boot still has the factory OpenWrt env (no `vyos` variable referencing `vyos.env`).

Connect the serial console, interrupt the boot countdown (press any key), then paste these commands at the `=>` prompt:

```
setenv vyos 'ext4load mmc 0:3 ${load_addr} /boot/vyos.env; env import -t ${load_addr} ${filesize}; ext4load mmc 0:3 ${kernel_addr_r} /boot/${vyos_image}/vmlinuz; ext4load mmc 0:3 ${fdt_addr_r} /boot/${vyos_image}/mono-gw.dtb; ext4load mmc 0:3 ${ramdisk_addr_r} /boot/${vyos_image}/initrd.img; setenv bootargs "BOOT_IMAGE=/boot/${vyos_image}/vmlinuz console=ttyS0,115200 earlycon=uart8250,mmio,0x21c0500 net.ifnames=0 boot=live rootdelay=5 noautologin fsl_dpaa_fman.fsl_fm_max_frm=9600 hugepagesz=2M hugepages=512 panic=60 vyos-union=/boot/${vyos_image}"; booti ${kernel_addr_r} ${ramdisk_addr_r}:${filesize} ${fdt_addr_r}'

setenv usb_vyos 'usb start; if fatload usb 0:0 ${kernel_addr_r} live/vmlinuz; then fatload usb 0:0 ${fdt_addr_r} mono-gw.dtb; fatload usb 0:0 ${ramdisk_addr_r} live/initrd.img; setenv bootargs "BOOT_IMAGE=/live/vmlinuz console=ttyS0,115200 earlycon=uart8250,mmio,0x21c0500 boot=live live-media=/dev/sda components noeject nopersistence noautologin nonetworking union=overlay net.ifnames=0 fsl_dpaa_fman.fsl_fm_max_frm=9600 hugepagesz=2M hugepages=512 panic=60 quiet"; booti ${kernel_addr_r} ${ramdisk_addr_r}:${filesize} ${fdt_addr_r}; fi'

setenv bootcmd 'run usb_vyos || run vyos || run recovery'

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

- **[FIRMWARE.md](FIRMWARE.md)** — Board firmware update (NOR + eMMC flash procedure, partition offset details, recovery)
- **[BOOT-PROCESS.md](BOOT-PROCESS.md)** — Complete technical specification: U-Boot variable definitions, annotated boot sequences for both USB and eMMC paths, `vyos.env` write paths, DTB delivery, kexec prevention, SPI NOR layout, and all documented failure modes
- **[UBOOT.md](UBOOT.md)** — U-Boot serial console reference: memory map, working boot commands, clock tree, MTD layout
- **[PORTING.md](PORTING.md)** — Why 13 things were broken and how each was fixed
