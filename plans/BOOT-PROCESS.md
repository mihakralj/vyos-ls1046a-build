# Boot Process Specification — VyOS LS1046A (Mono Gateway DK)

## Overview

Two distinct boot paths share the same U-Boot environment:

| Path | Trigger | Use case |
|------|---------|---------|
| **USB live boot** | USB FAT32 drive present | Initial install, recovery |
| **eMMC installed boot** | No USB, eMMC p3 ext4 present | Normal operation |

Both paths use `booti` (raw ARM64 `Image` format). `bootm` (uImage) and `bootefi` (EFI) are not used. GRUB is not involved in the boot process on this board.

---

## U-Boot Environment

Stored in SPI NOR flash at `/dev/mtd3` ("uboot-env", QSPI, 4 KiB erase sector, 8 KiB env size). Written automatically by `vyos-postinstall` Phase 1 (`setup_uboot_env_once`) via `fw_setenv` on first boot. Manual U-Boot console setup (INSTALL.md Step 4) is available as a fallback if `fw_setenv` fails. Config: `/etc/fw_env.config` → `/dev/mtd3 0x0 0x2000 0x1000`.

### Variables

```
bootcmd    = run usb_vyos || run vyos || run recovery
```

```
usb_vyos   = usb start;
             if fatload usb 0:2 ${kernel_addr_r} live/vmlinuz; then
               fatload usb 0:2 ${fdt_addr_r} mono-gw.dtb;
               fatload usb 0:2 ${ramdisk_addr_r} live/initrd.img;
               setenv bootargs "BOOT_IMAGE=/live/vmlinuz
                 console=ttyS0,115200 earlycon=uart8250,mmio,0x21c0500
                 boot=live live-media=/dev/sda components noeject
                 nopersistence noautologin nonetworking union=overlay
                 net.ifnames=0 fsl_dpaa_fman.fsl_fm_max_frm=9600 quiet";
               booti ${kernel_addr_r} ${ramdisk_addr_r}:${filesize} ${fdt_addr_r};
             fi
```

```
vyos = ext4load mmc 0:3 ${load_addr} /boot/vyos.env;
              env import -t ${load_addr} ${filesize};
              ext4load mmc 0:3 ${kernel_addr_r} /boot/${vyos_image}/vmlinuz;
              ext4load mmc 0:3 ${fdt_addr_r}    /boot/${vyos_image}/mono-gw.dtb;
              ext4load mmc 0:3 ${ramdisk_addr_r} /boot/${vyos_image}/initrd.img;
              setenv bootargs "BOOT_IMAGE=/boot/${vyos_image}/vmlinuz
                console=ttyS0,115200 earlycon=uart8250,mmio,0x21c0500
                net.ifnames=0 boot=live rootdelay=5 noautologin
                fsl_dpaa_fman.fsl_fm_max_frm=9600
                panic=60
                vyos-union=/boot/${vyos_image}";
              booti ${kernel_addr_r} ${ramdisk_addr_r}:${filesize} ${fdt_addr_r}
```

```
recovery    = sf probe 0:0;
              sf read ${kernel_addr_r} ${kernel_addr} ${kernel_size};
              sf read ${fdt_addr_r} ${fdt_addr} ${fdt_size};
              booti ${kernel_addr_r} - ${fdt_addr_r}
```

### Memory Map

| Variable | Address | Size | Contents |
|----------|---------|------|---------|
| `kernel_addr_r` | `0x82000000` | ~30 MB | Kernel `Image` |
| `fdt_addr_r` | `0x88000000` | ~100 KB | DTB |
| `ramdisk_addr_r` | `0x88080000` | ~200 MB | initrd |
| `load_addr` | `0xa0000000` | 4 KB | `vyos.env` scratch |

> `fdt_addr_r = 0x88000000` is fixed. Never use `0x90000000` (`kernel_comp_addr_r`) for the DTB — that is the kernel decompression scratch space and will be overwritten.

### Load Ordering Constraint

**initrd must always be loaded last.** U-Boot's `${filesize}` variable holds the byte count of the most recently loaded file. `booti` requires `${ramdisk_addr_r}:${filesize}` (address:size format). If initrd is not loaded last, `${filesize}` captures the wrong file's size and `booti` fails with "Wrong Ramdisk Image Format".

---

## Path A: USB Live Boot

### Prerequisites

- USB drive contains a whole-disk FAT32 image (no MBR partition table, `usb 0:0`) with:
  ```
  /live/vmlinuz              ← kernel Image
  /live/initrd.img           ← initrd
  /live/filesystem.squashfs  ← VyOS squashfs (live root)
  /mono-gw.dtb               ← compiled device tree blob
  /boot.scr                  ← U-Boot boot script (one-line manual boot shortcut)
  ```
- USB image is written with `dd` (or Rufus DD mode). ISO9660 format is not readable by U-Boot.

### Boot Sequence

```
Power on
  │
  ▼
U-Boot POST + memory init
  │
  ▼
bootcmd: run usb_vyos
  │
  ├─ usb start               ← enumerate USB devices
  ├─ fatload usb 0:2 live/vmlinuz    → 0x82000000
  ├─ fatload usb 0:2 mono-gw.dtb    → 0x88000000
  ├─ fatload usb 0:2 live/initrd.img → 0x88080000  ← LAST (captures filesize)
  ├─ setenv bootargs "BOOT_IMAGE=/live/vmlinuz ... boot=live
  │    live-media=/dev/sda ..."
  └─ booti 0x82000000 0x88080000:${filesize} 0x88000000
       │
       ▼
  Linux kernel decompresses at 0x0
  initramfs mounts
  live-boot scripts:
    ├─ mount /dev/sda as live medium (whole-disk FAT32)
    ├─ find /live/filesystem.squashfs on /dev/sda
    ├─ loopback-mount squashfs → /run/live/rootfs/
    └─ overlay: squashfs (ro) + tmpfs (rw) → /
       │
       ▼
  systemd starts
  vyos-postinstall.service (After=local-fs.target)
    ├─ Phase 1: setup_uboot_env_once()
    │    ├─ fw_printenv vyos → check if "vyos.env" in value
    │    ├─ If NOT present: fw_setenv vyos / usb_vyos / bootcmd
    │    └─ Writes SPI NOR env for all future eMMC boots
    └─ Phase 2: find_root() → returns "" (no installed images on USB)
         └─ Skips vyos.env write, prints informational message
       │
       ▼
  VyOS login: vyos / vyos
```

### Kernel Bootargs (USB live)

```
BOOT_IMAGE=/live/vmlinuz
console=ttyS0,115200
earlycon=uart8250,mmio,0x21c0500
boot=live
live-media=/dev/sda
components
noeject
nopersistence
noautologin
nonetworking
union=overlay
net.ifnames=0
fsl_dpaa_fman.fsl_fm_max_frm=9600
quiet
```

**Key parameters:**
- `boot=live` — activates live-boot initramfs scripts
- `live-media=/dev/sda` — USB whole-disk FAT32 containing squashfs (no partition table)
- `BOOT_IMAGE=/live/vmlinuz` — required for VyOS `is_live_boot()` detection (patch 009 adds `vyos-union=` fallback for older builds)
- `nonetworking` — skips DHCP on live boot; user configures manually
- `fsl_dpaa_fman.fsl_fm_max_frm=9600` — enables jumbo frames on RJ45 ports (FMan module parameter; wrong modname = silently no effect)

---

## Path B: eMMC Installed Boot

### Prerequisites

- eMMC (`mmc 0`) partitioned by `install image`:

  | Partition | U-Boot | Type | Size | Contents |
  |-----------|--------|------|------|---------|
  | *(reserved)* | — | — | 32 MiB | NXP firmware boundary — no partitions |
  | p1 | `mmc 0:1` | raw | 1 MiB | BIOS boot gap — no filesystem |
  | p2 | `mmc 0:2` | FAT32 | 256 MiB | EFI — present but unused |
  | **p3** | **`mmc 0:3`** | **ext4** | remainder | **VyOS root** |

- eMMC p3 ext4 layout:
  ```
  /boot/
  ├── vyos.env                         ← image selector (plain text)
  └── 2026.03.27-0142-rolling/         ← image directory (name varies)
      ├── vmlinuz                      ← kernel Image
      ├── mono-gw.dtb                  ← device tree blob
      ├── initrd.img                   ← initrd
      └── 2026.03.27-0142-rolling.squashfs  ← VyOS squashfs
  ```

- `/boot/vyos.env` content (single line, written by `vyos-postinstall`):
  ```
  vyos_image=2026.03.27-0142-rolling
  ```

### Boot Sequence

```
Power on
  │
  ▼
U-Boot POST + memory init
  │
  ▼
bootcmd: run usb_vyos
  │
  ├─ usb start
  └─ fatload usb 0:2 live/vmlinuz → FAIL (no USB)
       │
       ▼ (falls through via || operator)
bootcmd: run vyos
  │
  ├─ ext4load mmc 0:3 0xa0000000 /boot/vyos.env   (4 bytes: "vyos_image=...")
  ├─ env import -t 0xa0000000 ${filesize}
  │    └─ sets ${vyos_image} = "2026.03.27-0142-rolling"
  ├─ ext4load mmc 0:3 0x82000000 /boot/${vyos_image}/vmlinuz
  ├─ ext4load mmc 0:3 0x88000000 /boot/${vyos_image}/mono-gw.dtb
  ├─ ext4load mmc 0:3 0x88080000 /boot/${vyos_image}/initrd.img  ← LAST
  ├─ setenv bootargs "BOOT_IMAGE=/boot/${vyos_image}/vmlinuz
  │    ... boot=live panic=60
  │    vyos-union=/boot/${vyos_image}"
  └─ booti 0x82000000 0x88080000:${filesize} 0x88000000
       │
       ▼
  Linux kernel decompresses at 0x0
  initramfs mounts
  live-boot scripts:
    ├─ boot=live → activates live-boot path
    ├─ vyos-union=/boot/2026.03.27-0142-rolling
    │    → overlayfs: squashfs (ro) + ext4 persistent (rw) → /
    └─ squashfs at /boot/${vyos_image}/${vyos_image}.squashfs
       │
       ▼
  systemd starts
  vyos-postinstall.service (After=local-fs.target)
    ├─ Phase 1: setup_uboot_env_once()
    │    └─ fw_printenv vyos → "vyos.env" found → SKIP (already configured)
    └─ Phase 2: find_root() → returns "/" (installed system)
         └─ write_vyos_env(image_name, "/")
              └─ writes /boot/vyos.env with current image name
       │
       ▼
  VyOS fully boots (~82s to login prompt)
```

### Kernel Bootargs (eMMC installed)

```
BOOT_IMAGE=/boot/2026.03.27-0142-rolling/vmlinuz
console=ttyS0,115200
earlycon=uart8250,mmio,0x21c0500
net.ifnames=0
boot=live
rootdelay=5
noautologin
fsl_dpaa_fman.fsl_fm_max_frm=9600
panic=60
vyos-union=/boot/2026.03.27-0142-rolling
```

**Key parameters:**
- `BOOT_IMAGE=/boot/<image>/vmlinuz` — required first argument; enables `is_live_boot()` detection (patch 009). Must be first in bootargs.
- `boot=live` — required even on installed system; VyOS initramfs depends on this
- `vyos-union=/boot/<image>` — tells live-boot where the squashfs is on the installed ext4 partition
- `panic=60` — a `MANAGED_PARAMS` parameter; must match `config.boot` default to prevent kexec double-boot

**Hugepages:** Not pre-allocated in bootargs. VPP dynamically adds `hugepagesz=2M hugepages=512` via `set vpp settings`, which triggers a one-time kexec to apply them. Without VPP configured, no hugepages are needed.

**MANAGED_PARAMS:** VyOS `system_option.py` compares `/proc/cmdline` against `config.boot` values for `panic` (and `hugepagesz`/`hugepages` if VPP is configured). If they differ on boot (before config is applied), a kexec reboot is triggered. The bootargs include `panic=60` to match `config.boot.default`.

---

## Image Selection Mechanism (`vyos.env`)

`/boot/vyos.env` is a plain-text key=value file read by U-Boot's `env import -t`. This is the entire image selection mechanism — there is no GRUB, no `grub.cfg`, no EFI variables involved.

### Write paths (all call `vyos-postinstall` or `grub.set_default()`):

| Event | Trigger | What writes vyos.env |
|-------|---------|---------------------|
| First USB boot | (none) | No vyos.env written — live boot only, no installed images yet |
| `install image` | `image_installer.install_image()` | `grub.set_default()` patch + `run('vyos-postinstall')` |
| `add system image` | `image_installer.add_image()` | `grub.set_default()` patch |
| `set system image default-boot` | VyOS CLI → `grub.set_default()` | `grub.set_default()` patch |
| Every boot | `vyos-postinstall.service` | Phase 2 writes current image name |

### Format

```
vyos_image=2026.03.27-0142-rolling
```

Single line, LF-terminated. `env import -t` treats `\0` as end-of-file and `\n` as field separator. The file is always overwritten atomically by `printf 'vyos_image=%s\n' "$name"`.

---

## DTB Delivery

The device tree blob `mono-gw.dtb` must be present in two locations:

| Location | Used by | Written by |
|----------|---------|-----------|
| `/live/mono-gw.dtb` on USB FAT32 | U-Boot `usb_vyos` (`fatload usb 0:2 mono-gw.dtb`) | Build: `mcopy` into FAT32 USB image |
| `/boot/<image>/mono-gw.dtb` on eMMC p3 | U-Boot `vyos` (`ext4load mmc 0:3 /boot/${vyos_image}/mono-gw.dtb`) | `install_image()`: copies all files from squashfs `/boot/` (where `mono-gw.dtb` was placed at build time) |

For `add system image` (upgrade), patch 011 copies all `.dtb` files from the ISO root into the new image directory during `add_image()`.

---

## kexec / Double-Boot Prevention

VyOS `system_option.py` may trigger a kexec reboot if `/proc/cmdline` doesn't match `MANAGED_PARAMS` from `config.boot`. On this board:

- `kexec-load.service` and `kexec.service` are **NOT masked** — mainline 6.6 QBMan kexec fix (`bman_requires_cleanup()` in `drivers/soc/fsl/qbman/`) allows kexec on DPAA1. VyOS managed-params self-healing works normally.
- The managed param `panic=60` is pre-baked into U-Boot bootargs to match `config.boot.default`. Hugepages are NOT in bootargs by default — they are added dynamically when VPP is configured via `set vpp settings`, which triggers a one-time kexec to apply them.

---

## SPI NOR Flash Layout

QSPI NOR flash (Micron mt25qu512a, 64 MiB, 4 KiB erase sector). Verified against live `/proc/mtd` (2026-03-29). mtd0 is the whole flash device; mtd1–mtd8 are DTS-defined partitions.

| MTD | Offset | Name | Size | Contents |
|-----|--------|------|------|---------|
| mtd1 | `0x000000` | `rcw-bl2` | 1 MiB | RCW + BL2 |
| mtd2 | `0x100000` | `uboot` | 2 MiB | U-Boot |
| **mtd3** | **`0x300000`** | **`uboot-env`** | **1 MiB** | **U-Boot environment** (8 KiB env, 4 KiB sector) |
| mtd4 | `0x400000` | `fman-ucode` | 1 MiB | FMan microcode (injected by U-Boot into DTB) |
| mtd5 | `0x500000` | `recovery-dtb` | 1 MiB | Recovery device tree |
| mtd6 | `0x600000` | `backup` | 4 MiB | Backup |
| mtd7 | `0xa00000` | `kernel-initramfs` | 22 MiB | Recovery kernel + initramfs |
| mtd8 | `0x2000000` | `unallocated` | 32 MiB | Remaining space |

`/etc/fw_env.config` → `/dev/mtd3 0x0 0x2000 0x1000` (8 KiB env size, 4 KiB sector). Used by `fw_setenv`/`fw_printenv` from `libubootenv-tool`.

---

## Failure Modes

| Symptom | Cause | Fix |
|---------|-------|-----|
| `No partition table - usb 0` / `Couldn't find partition usb 0:1` | Old `usb_vyos` env uses `0:1` but USB image is whole-disk FAT32 (no MBR) | Use `usb 0:0` or boot via `boot.scr`: `usb start; fatload usb 0:2 ${load_addr} boot.scr; source ${load_addr}` |
| `Kernel stuck after earlycon enabled` | `live-media=/dev/sda1` in bootargs — no partition 1 on whole-disk FAT USB | Use `live-media=/dev/sda` (whole disk). Fix `usb_vyos` env from U-Boot console per INSTALL.md Step 4. |
| `Can't set block device` | `emmc=mmc 0:1` — wrong partition (p1 is raw BIOS boot, no ext4) | Run manual U-Boot setup from INSTALL.md |
| `Bad Linux ARM64 Image magic!` | Factory env uses `bootm` (expects uImage). VyOS kernel requires `booti` | Run manual U-Boot setup |
| `ERROR: Did not find a cmdline Flattened Device Tree` | DTB loaded at `0x90000000` (`kernel_comp_addr_r`) — overwritten by kernel decompression | Always use `${fdt_addr_r}` = `0x88000000` |
| `Wrong Ramdisk Image Format` | `booti addr addr:size fdt` — missing `:${filesize}` colon-size suffix | Ensure initrd loads last; use `${ramdisk_addr_r}:${filesize}` |
| Boot loops (kexec) | `panic` in bootargs doesn't match `config.boot` | Verify bootargs include `panic=60`. Hugepages are added dynamically by VPP — no need in base bootargs |
| `fw_setenv` fails | `/dev/mtd3` missing — `CONFIG_SPI_FSL_QSPI` not built-in | Verify `CONFIG_SPI_FSL_QSPI=y` in kernel config |
| `fw_printenv` CRC error | DTS partition offsets don't match actual flash layout | Verify `cat /proc/mtd` matches DTS; check `fw_env.config` env_size matches U-Boot `CONFIG_ENV_SIZE` |
| No network interfaces after boot | DPAA1 stack built as modules instead of `=y` | All `CONFIG_FSL_FMAN/DPAA/BMAN/QMAN/PAMU` must be `=y` |
| CPU locked at 700 MHz | `CONFIG_QORIQ_CPUFREQ=m` — module loads too late, PLLs released | Set `CONFIG_QORIQ_CPUFREQ=y` |