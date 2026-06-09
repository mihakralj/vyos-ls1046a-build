# Mono Gateway DK — Firmware Update Guide
**Version 1.0.0** · 2026-06-09 · HADS 1.0.0

---

## AI READING INSTRUCTION

Read `[SPEC]` and `[BUG]` blocks for authoritative facts.
Read `[NOTE]` only if additional context is needed.
`[?]` blocks are unverified — treat with lower confidence.

---

## 1. SCOPE

**[SPEC]**
- Covers updating NXP LS1046A Mono Gateway DK factory firmware (SPI NOR + eMMC).
- If the board already boots to a U-Boot prompt, skip to `INSTALL.md`.

**[NOTE]**
Most VyOS users never need this guide — it exists only for recovery and factory-restore scenarios.

---

## 2. WHEN YOU NEED THIS

**[SPEC]**
Use this procedure when:
- Board is bricked (no U-Boot prompt on serial)
- Restoring a board from OpenWrt back to factory state before VyOS install
- SPI NOR corruption (U-Boot env variables lost or invalid)

---

## 3. ORDERING REQUIREMENT

**[SPEC]**
- Always update firmware BEFORE installing VyOS.
- Firmware flashing writes the first 32 MB of eMMC, destroying the GPT partition table.
- If firmware is flashed AFTER VyOS install, re-run `install image` from USB.

**[SPEC]**
Re-flash after VyOS install (data survival):
- All VyOS data survives a firmware re-flash.
- The entire GPT and all partitions (p1 at 32 MiB, p2 at 33 MiB, p3 at ~289 MiB) sit beyond the 32 MiB firmware zone — no reinstall needed, just reboot.

---

## 4. PARTITION OFFSET COMPLIANCE

**[SPEC]**
- NXP requires custom OS images place all partitions ≥ 32 MiB from eMMC start.
- VyOS GPT layout complies: p1 (BIOS boot) at 32 MiB, p2 (EFI) at 33 MiB, p3 (VyOS root) at ~289 MiB.
- Firmware re-flash (`dd` to first 32 MiB) destroys nothing — all partitions and data survive.
- No need to re-run `install image` after a firmware update.
- VyOS boots from SPI NOR (DIP switch on NOR position); the eMMC bootloader region is never executed.

---

## 5. EQUIPMENT REQUIRED

**[SPEC]**
- Serial console: USB-to-UART adapter, 115200 8N1 (J6 header)
- USB-A storage: ≥ 1 GB, FAT32, firmware files at root
- Firmware files: `firmware-nor.bin` + `firmware-emmc.bin` (from Mono Gateway support)

---

## 6. PROCEDURE

### 6.1 Prepare USB

**[SPEC]**
Copy both firmware files to the root of a FAT32 USB drive, then insert into a board USB-A port:

```
firmware-nor.bin
firmware-emmc.bin
```

### 6.2 Enter U-Boot (NOR recovery)

**[SPEC]**
- Connect serial console, power on, press a key within 3 seconds to stop autoboot at the U-Boot prompt.
- If U-Boot is dead, hold the NOR recovery button while powering on to boot the minimal rescue U-Boot from the protected read-only NOR partition.

### 6.3 Flash eMMC

**[SPEC]**
At the U-Boot prompt:

```
usb start
fatload usb 0:1 $loadaddr firmware-emmc.bin
setexpr blkcnt $filesize / 0x200
mmc dev 0
mmc write $loadaddr 0 $blkcnt
```

Writes the eMMC firmware image from block 0 (byte 0); the first 4 KB (GPT primary header) is overwritten and all existing partitions are destroyed.

### 6.4 Verify eMMC

**[SPEC]**
```
mmc read $loadaddr 0 8
md.b $loadaddr 0x200
```
Confirm the first bytes match `firmware-emmc.bin` (typically a protective MBR `0x00000001` or GPT signature `EFI PART`).

### 6.5 Flash SPI NOR

**[SPEC]**
```
sf probe
fatload usb 0:1 $loadaddr firmware-nor.bin
sf erase 0 +$filesize
sf write $loadaddr 0 $filesize
```

### 6.6 Verify NOR

**[SPEC]**
```
sf read $loadaddr 0 0x100
md.b $loadaddr 0x100
```

### 6.7 Power Cycle

**[SPEC]**
- Remove USB drive, power cycle, confirm U-Boot boots normally and reaches the autoboot prompt.
- Then proceed to `INSTALL.md` to install VyOS.

---

## 7. DIP SWITCH REFERENCE

**[SPEC]**

| Boot Source | SW1 Setting |
|-------------|-------------|
| SPI NOR (VyOS normal) | NOR position (factory default) |
| eMMC | eMMC position |
| NOR recovery | Hold recovery button + power on |

- VyOS always runs with the DIP switch in NOR position.
- The eMMC position is used only during firmware recovery when SPI NOR is corrupt.

---

## 8. SPI NOR LAYOUT REFERENCE

**[SPEC]**
Verified against live `/proc/mtd` (2026-03-29, U-Boot 2025.04). Matches the `fixed-partitions` in `data/dtb/mono-gateway-dk.dts`. Linux creates `/dev/mtd0` (whole flash) + `/dev/mtd1`–`/dev/mtd8`. Erase sector = 4 KiB.

| MTD | Offset | Size | Content |
|-----|--------|------|---------|
| mtd0 | `0x000000` | 1 MiB | RCW + BL2 |
| mtd1 | `0x100000` | 2 MiB | U-Boot |
| **mtd2** | **`0x300000`** | **1 MiB** | **U-Boot env** — `fw_setenv` target (8 KiB env, 4 KiB sector) |
| mtd3 | `0x400000` | 1 MiB | FMan microcode |
| mtd4 | `0x500000` | 1 MiB | Recovery DTB |
| mtd5 | `0x600000` | 4 MiB | Backup |
| mtd6 | `0xa00000` | 22 MiB | Recovery kernel + initramfs |
| mtd7 | `0x2000000` | 32 MiB | Unallocated |

- `/etc/fw_env.config` → `/dev/mtd2 0x0 0x2000 0x1000` (8 KiB env, 4 KiB sector).

**[BUG] MTD partition numbering drift across builds**
- Symptom: `fw_setenv` writes to the wrong MTD; the U-Boot env is not updated or becomes corrupted.
- Cause: partition numbering changed between builds — older builds placed `uboot-env` at `mtd3`, current builds at `mtd2`.
- Fix: always verify with `cat /proc/mtd` before pointing `fw_env.config` at a device.