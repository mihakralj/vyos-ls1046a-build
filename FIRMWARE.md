# Mono Gateway DK — Firmware Update Guide

This guide covers updating the NXP LS1046A Mono Gateway DK factory firmware (SPI NOR + eMMC). Most VyOS users **never need this** — if the board already boots to a U-Boot prompt, skip straight to [INSTALL.md](INSTALL.md).

## When You Need This

- Board is bricked (no U-Boot prompt on serial)
- Restoring a board from OpenWrt back to factory state before VyOS install
- SPI NOR corruption (U-Boot env variables lost or invalid)

## Recommended Order

**Always update firmware BEFORE installing VyOS.** Firmware flashing writes the first 32 MB of eMMC, destroying the GPT partition table. If you flash firmware after VyOS install, you must run `install image` again from USB.

> **If you must re-flash firmware after VyOS is installed:** VyOS data (p3, ext4 root, starting at ~273 MiB) survives the eMMC firmware write. Only p1 (BIOS boot, 1 MiB) and p2 (EFI FAT32, 17 MiB) are destroyed. Boot from USB and run `install image` to rebuild the GPT — your VyOS images remain intact on p3.

## Partition Offset Note

NXP documentation states custom OS images should place all partitions at ≥ 32 MiB from the eMMC start. Our VyOS GPT layout does **not** follow this rule (p1 starts at 1 MiB, p2 at 17 MiB). This is intentional and safe because:

- VyOS boots from **SPI NOR** (DIP switch on NOR position) — the eMMC bootloader region is never executed
- The 32 MiB rule only matters for eMMC-boot configurations where the SoC ROM reads the bootloader from eMMC
- p3 (VyOS root, ext4) starts at ~273 MiB — well beyond 32 MiB — and survives any eMMC firmware re-flash

## Equipment Required

- Serial console: USB-to-UART adapter, 115200 8N1 (J6 header)
- USB-A storage: ≥ 1 GB, formatted FAT32, with firmware files copied to root
- Firmware files: `firmware-nor.bin` + `firmware-emmc.bin` (from Mono Gateway support)

## Procedure

### Step 1 — Prepare USB

Copy both firmware files to the root of a FAT32 USB drive:

```
firmware-nor.bin
firmware-emmc.bin
```

Insert the USB drive into one of the board's USB-A ports.

### Step 2 — Enter U-Boot (NOR recovery)

Connect serial console. Power on the board. Press a key within 3 seconds to stop autoboot at the U-Boot prompt.

If U-Boot is dead (board won't boot), enter NOR recovery mode by holding the **NOR recovery button** while powering on. This boots a minimal rescue U-Boot from a protected read-only NOR partition.

### Step 3 — Flash eMMC

At the U-Boot prompt:

```
usb start
fatload usb 0:1 $loadaddr firmware-emmc.bin
setexpr blkcnt $filesize / 0x200
mmc dev 0
mmc write $loadaddr 0 $blkcnt
```

This writes the eMMC firmware image starting at block 0 (byte 0). The first 4 KB (GPT primary header) is overwritten — all existing partitions are destroyed.

### Step 4 — Verify eMMC

```
mmc read $loadaddr 0 8
md.b $loadaddr 0x200
```

Confirm the first bytes match `firmware-emmc.bin` (typically starts with `0x00000001` protective MBR or GPT signature `EFI PART`).

### Step 5 — Flash SPI NOR

```
sf probe
fatload usb 0:1 $loadaddr firmware-nor.bin
sf erase 0 +$filesize
sf write $loadaddr 0 $filesize
```

### Step 6 — Verify NOR

```
sf read $loadaddr 0 0x100
md.b $loadaddr 0x100
```

### Step 7 — Power Cycle

Remove USB drive. Power cycle the board. Confirm U-Boot boots normally and reaches the autoboot prompt.

Now proceed to [INSTALL.md](INSTALL.md) to install VyOS.

## DIP Switch Reference

| Boot Source | SW1 Setting |
|-------------|-------------|
| SPI NOR (VyOS normal) | NOR position (factory default) |
| eMMC | eMMC position |
| NOR recovery | Hold recovery button + power on |

VyOS always runs with the DIP switch in **NOR position**. The eMMC position is only used during firmware recovery when SPI NOR is corrupt.

## SPI NOR Layout Reference

| MTD | Offset | Size | Content |
|-----|--------|------|---------|
| mtd0 | 0x000000 | 1 MiB | RCW + PBI |
| mtd1 | 0x100000 | 4 MiB | U-Boot |
| mtd2 | 0x500000 | 256 KiB | U-Boot DTB |
| mtd3 | 0x540000 | 256 KiB | U-Boot env |
| mtd4 | 0x580000 | 4 MiB | FMan firmware |
| mtd5 | 0x980000 | remainder | Unused |