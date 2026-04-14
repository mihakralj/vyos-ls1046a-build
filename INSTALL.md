# Install Guide — VyOS on Mono Gateway DK (LS1046A)

## Overview

A single hybrid ISO serves both purposes:

| Use case | How |
|----------|-----|
| **Initial install** | `dd` the ISO to USB → boot → `install image` |
| **Upgrade** | `add system image <url>` from a running VyOS system |

The ISO is a hybrid image: valid ISO9660 (for VyOS image upgrades) with an appended FAT32 boot partition and MBR (for U-Boot USB boot). One file, two boot paths.

---

## Before You Install

Review the [open issues](https://github.com/mihakralj/vyos-ls1046a-build/issues) before proceeding. This is an experimental port with known limitations.

---

## Requirements

- USB flash drive ≥ 2 GB
- Serial console access: USB-to-serial adapter, **115200 8N1**, connected to the Mono Gateway's RJ45 console port
- Linux, macOS, or Windows host

---

## Step 1 — Write ISO to USB

Download the latest `vyos-...-LS1046A-arm64.iso` from [Releases](https://github.com/mihakralj/vyos-ls1046a-build/releases).

> **Note:** The ISO is a hybrid image — write it as a raw disk image, not as extracted files. No decompression or FAT32 formatting needed.

### Linux

```bash
# Identify USB device (look for your USB drive size — NOT a partition like sdb1)
lsblk

# Unmount any auto-mounted partitions
sudo umount /dev/sdX* 2>/dev/null

# Write ISO directly to USB (replace /dev/sdX with your USB device)
sudo dd if=vyos-*-LS1046A-arm64.iso of=/dev/sdX bs=4M status=progress conv=fsync
```

### macOS

```bash
# Identify USB device
diskutil list    # Look for your USB (e.g., /dev/disk2)

# Unmount (do NOT eject — just unmount)
diskutil unmountDisk /dev/diskN

# Write ISO directly (use rdiskN — 10x faster than diskN)
sudo dd if=vyos-*-LS1046A-arm64.iso of=/dev/rdiskN bs=4m
```

### Windows

1. Download [Rufus](https://rufus.ie/)
2. Select the `.iso` file and your USB drive
3. Click **START**
4. **When Rufus asks how to write** — select **"Write in DD Image mode"**, NOT "Write in ISO Image mode"
5. Wait for completion

---

## Step 2 — Boot from USB

1. Insert the USB drive into the Mono Gateway
2. Connect serial console (115200 8N1)
3. Power on and **press any key** during the U-Boot countdown to stop autoboot

Factory U-Boot boots OpenWrt from eMMC. It has no USB boot command, so you must tell it to run the boot script from USB.

At the `=>` prompt, paste this single line:

```
usb start; fatload usb 0:2 ${load_addr} boot.scr; source ${load_addr}
```

This loads `boot.scr` from the USB FAT32 partition, which loads the kernel, DTB, and initrd, sets temporary bootargs, and boots VyOS live via `booti`.

Watch the boot log for 60–90 seconds until the VyOS login prompt appears.

> **If `usb start` hangs or shows no devices:** Try a USB 2.0 drive. Some USB 3.0 drives aren't detected by the LS1046A USB controller.

> **USB layout:** The hybrid ISO has two partitions when written to USB: partition 1 (ISO9660 with squashfs) and partition 2 (FAT32 with boot files). U-Boot loads from partition 2 explicitly via `fatload usb 0:2`. After kernel boot, live-boot finds the squashfs on the ISO9660 partition.

---

## Step 3 — Install to eMMC

From the live VyOS shell, login with **vyos** / **vyos**, then run:

```
install image
```

- Select installation target: `mmcblk0` (the eMMC — not `sda` which is the USB)
- Enter a root password
- Accept defaults for the rest

The installer automatically:
1. Partitions the eMMC (GPT with 32 MiB firmware reserved zone)
2. Copies the VyOS image to eMMC partition 3 (ext4)
3. Writes `/boot/vyos.env` pointing to the installed image
4. **Configures U-Boot for eMMC boot** via `fw_setenv` — writes `vyos`, `usb_vyos`, and `bootcmd` to SPI flash

After installation completes, remove the USB drive and reboot:

```
reboot
```

The board will boot VyOS from eMMC automatically. No manual U-Boot configuration needed.

> **First boot may fail once:** After a fresh eMMC format, U-Boot's ext4 driver occasionally fails to read the new filesystem on the very first attempt (`Failed to load '/boot/vyos.env'`). This is a known U-Boot quirk — just reboot again from the recovery shell and it will work.

---

## Upgrading

From the VyOS CLI:

```
show system updates
add system image latest
```

`latest` is a built-in alias that checks the update server for the newest release. No need to find or paste a URL.

Alternatively, specify a URL directly (e.g. for a specific version):

```
add system image <url>
```

After the upgrade completes, `/boot/vyos.env` is automatically updated to the new image. Reboot when ready.

> **Never use `install image` on an already-installed system.** Use `add system image` instead. `install image` repartitions the eMMC and is only for fresh installs from USB live sessions.

---

## eMMC Partition Layout

After `install image`, the Mono Gateway eMMC (`mmcblk0`) has:

| Partition | U-Boot ref | Type | Contents |
|-----------|-----------|------|---------|
| *(reserved)* | — | 32 MiB firmware zone | NXP firmware boundary — no partitions here |
| p1 | `mmc 0:1` | Raw (1 MiB) | BIOS boot gap — no filesystem |
| p2 | `mmc 0:2` | FAT32 (256 MiB) | EFI partition — exists but unused on this board |
| **p3** | **`mmc 0:3`** | **ext4** | **VyOS root — kernel, DTB, initrd, squashfs** |

`/boot/vyos.env` lives on p3 (ext4). U-Boot loads it with `ext4load mmc 0:3`.

---

## Boot Sequence

After installation, every boot follows this order (configured automatically by `vyos-postinstall`):

1. **`run usb_vyos`** — if a VyOS USB is inserted, boot from it (live mode)
2. **`run vyos`** — reads `/boot/vyos.env` from eMMC p3 → loads the named image → `booti`
3. **`run recovery`** — falls back to factory SPI firmware

`install image`, `add system image`, and `set system image default-boot` all update `/boot/vyos.env` automatically — U-Boot reads it dynamically on every boot.

---

## Troubleshooting

### Emergency eMMC boot (one-shot, no saveenv)

If VyOS is installed but `vyos.env` is missing or corrupt, boot manually from the U-Boot console. First find the image name:

```
ext4ls mmc 0:3 /boot
```

Then boot (replace `2026.03.27-0142-rolling` with the directory name you saw):

```
setenv vyos_image 2026.03.27-0142-rolling
ext4load mmc 0:3 ${kernel_addr_r} /boot/${vyos_image}/vmlinuz
ext4load mmc 0:3 ${fdt_addr_r} /boot/${vyos_image}/mono-gw.dtb
ext4load mmc 0:3 ${ramdisk_addr_r} /boot/${vyos_image}/initrd.img
setenv bootargs "BOOT_IMAGE=/boot/${vyos_image}/vmlinuz console=ttyS0,115200 earlycon=uart8250,mmio,0x21c0500 net.ifnames=0 boot=live rootdelay=5 noautologin fsl_dpaa_fman.fsl_fm_max_frm=9600 panic=60 vyos-union=/boot/${vyos_image}"
booti ${kernel_addr_r} ${ramdisk_addr_r}:${filesize} ${fdt_addr_r}
```

Once VyOS boots, reboot normally — `vyos-postinstall` fixes U-Boot environment and `vyos.env` automatically on the next clean boot.

### Manual U-Boot environment setup (recovery fallback)

If `fw_setenv` failed during install (e.g., `/dev/mtd3` not accessible), you can configure U-Boot manually from the serial console. Power on, press any key during the countdown, and paste:

```
setenv vyos 'ext4load mmc 0:3 ${load_addr} /boot/vyos.env; env import -t ${load_addr} ${filesize}; ext4load mmc 0:3 ${kernel_addr_r} /boot/${vyos_image}/vmlinuz; ext4load mmc 0:3 ${fdt_addr_r} /boot/${vyos_image}/mono-gw.dtb; ext4load mmc 0:3 ${ramdisk_addr_r} /boot/${vyos_image}/initrd.img; \
    setenv bootargs "BOOT_IMAGE=/boot/${vyos_image}/vmlinuz console=ttyS0,115200 earlycon=uart8250,mmio,0x21c0500 net.ifnames=0 boot=live rootdelay=5 noautologin fsl_dpaa_fman.fsl_fm_max_frm=9600 panic=60 vyos-union=/boot/${vyos_image}"; \
    booti ${kernel_addr_r} ${ramdisk_addr_r}:${filesize} ${fdt_addr_r}'

setenv usb_vyos 'usb start; if fatload usb 0:2 ${kernel_addr_r} live/vmlinuz; then fatload usb 0:2 ${fdt_addr_r} mono-gw.dtb; fatload usb 0:2 ${ramdisk_addr_r} live/initrd.img; setenv bootargs "BOOT_IMAGE=/live/vmlinuz console=ttyS0,115200 earlycon=uart8250,mmio,0x21c0500 boot=live live-media=/dev/sda rootdelay=5 components noeject nopersistence noautologin nonetworking union=overlay net.ifnames=0 fsl_dpaa_fman.fsl_fm_max_frm=9600 panic=60"; booti ${kernel_addr_r} ${ramdisk_addr_r}:${filesize} ${fdt_addr_r}; fi'

setenv bootcmd 'run usb_vyos || run vyos || run recovery'

saveenv
reset
```

This is the same configuration that `vyos-postinstall` writes automatically — you only need this if the automated setup failed.

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

## See Also

- **[FIRMWARE.md](FIRMWARE.md)** — Board firmware update (NOR + eMMC flash procedure, partition offset details, recovery)
- **[BOOT-PROCESS.md](BOOT-PROCESS.md)** — Complete technical specification: U-Boot variable definitions, annotated boot sequences for both USB and eMMC paths, `vyos.env` write paths, DTB delivery, kexec prevention, SPI NOR layout, and all documented failure modes
- **[UBOOT.md](UBOOT.md)** — U-Boot serial console reference: memory map, working boot commands, clock tree, MTD layout
- **[PORTING.md](PORTING.md)** — Why 13 things were broken and how each was fixed
