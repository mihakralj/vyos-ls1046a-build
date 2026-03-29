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

Stored in SPI NOR flash at `/dev/mtd3` (QSPI, 64 KiB sector, 64 KiB env). Written once during the first USB live boot by `vyos-postinstall.service` via `fw_setenv` (`libubootenv-tool`).

### Variables

```
bootcmd    = run usb_vyos || run vyos_direct || run recovery
```

```
usb_vyos   = usb start;
             if fatload usb 0:1 ${kernel_addr_r} live/vmlinuz; then
               fatload usb 0:1 ${fdt_addr_r} mono-gw.dtb;
               fatload usb 0:1 ${ramdisk_addr_r} live/initrd.img;
               setenv bootargs "BOOT_IMAGE=/live/vmlinuz
                 console=ttyS0,115200 earlycon=uart8250,mmio,0x21c0500
                 boot=live live-media=/dev/sda1 components noeject
                 nopersistence noautologin nonetworking union=overlay
                 net.ifnames=0 fsl_dpaa_fman.fsl_fm_max_frm=9600 quiet";
               booti ${kernel_addr_r} ${ramdisk_addr_r}:${filesize} ${fdt_addr_r};
             fi
```

```
vyos_direct = ext4load mmc 0:3 ${load_addr} /boot/vyos.env;
              env import -t ${load_addr} ${filesize};
              ext4load mmc 0:3 ${kernel_addr_r} /boot/${vyos_image}/vmlinuz;
              ext4load mmc 0:3 ${fdt_addr_r}    /boot/${vyos_image}/mono-gw.dtb;
              ext4load mmc 0:3 ${ramdisk_addr_r} /boot/${vyos_image}/initrd.img;
              setenv bootargs "BOOT_IMAGE=/boot/${vyos_image}/vmlinuz
                console=ttyS0,115200 earlycon=uart8250,mmio,0x21c0500
                net.ifnames=0 boot=live rootdelay=5 noautologin
                fsl_dpaa_fman.fsl_fm_max_frm=9600
                hugepagesz=2M hugepages=512 panic=60
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

- USB drive contains a FAT32 filesystem (partition 1, `usb 0:1`) with:
  ```
  /live/vmlinuz              ← kernel Image
  /live/initrd.img           ← initrd
  /live/filesystem.squashfs  ← VyOS squashfs (live root)
  /mono-gw.dtb               ← compiled device tree blob
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
  ├─ fatload usb 0:1 live/vmlinuz    → 0x82000000
  ├─ fatload usb 0:1 mono-gw.dtb    → 0x88000000
  ├─ fatload usb 0:1 live/initrd.img → 0x88080000  ← LAST (captures filesize)
  ├─ setenv bootargs "BOOT_IMAGE=/live/vmlinuz ... boot=live
  │    live-media=/dev/sda1 ..."
  └─ booti 0x82000000 0x88080000:${filesize} 0x88000000
       │
       ▼
  Linux kernel decompresses at 0x0
  initramfs mounts
  live-boot scripts:
    ├─ mount /dev/sda1 as live medium (FAT32)
    ├─ find /live/filesystem.squashfs on /dev/sda1
    ├─ loopback-mount squashfs → /run/live/rootfs/
    └─ overlay: squashfs (ro) + tmpfs (rw) → /
       │
       ▼
  systemd starts
  vyos-postinstall.service (After=local-fs.target)
    ├─ Phase 1: setup_uboot_env_once()
    │    ├─ fw_printenv vyos_direct → check if "vyos.env" present
    │    ├─ If NOT present: fw_setenv vyos_direct / usb_vyos / bootcmd
    │    └─ Sets up SPI NOR env for all future eMMC boots
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
live-media=/dev/sda1
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
- `live-media=/dev/sda1` — USB FAT32 partition containing squashfs
- `BOOT_IMAGE=/live/vmlinuz` — required for VyOS `is_live_boot()` detection (patch 009 adds `vyos-union=` fallback for older builds)
- `nonetworking` — skips DHCP on live boot; user configures manually
- `fsl_dpaa_fman.fsl_fm_max_frm=9600` — enables jumbo frames on RJ45 ports (FMan module parameter; wrong modname = silently no effect)

---

## Path B: eMMC Installed Boot

### Prerequisites

- eMMC (`mmc 0`) partitioned by `install image`:

  | Partition | U-Boot | Type | Size | Contents |
  |-----------|--------|------|------|---------|
  | p1 | `mmc 0:1` | raw | 1 MiB | BIOS boot gap — no filesystem |
  | *(gap)* | — | — | 16 MiB | unallocated (preserves SPI U-Boot env region) |
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
  └─ fatload usb 0:1 live/vmlinuz → FAIL (no USB)
       │
       ▼ (falls through via || operator)
bootcmd: run vyos_direct
  │
  ├─ ext4load mmc 0:3 0xa0000000 /boot/vyos.env   (4 bytes: "vyos_image=...")
  ├─ env import -t 0xa0000000 ${filesize}
  │    └─ sets ${vyos_image} = "2026.03.27-0142-rolling"
  ├─ ext4load mmc 0:3 0x82000000 /boot/${vyos_image}/vmlinuz
  ├─ ext4load mmc 0:3 0x88000000 /boot/${vyos_image}/mono-gw.dtb
  ├─ ext4load mmc 0:3 0x88080000 /boot/${vyos_image}/initrd.img  ← LAST
  ├─ setenv bootargs "BOOT_IMAGE=/boot/${vyos_image}/vmlinuz
  │    ... boot=live hugepagesz=2M hugepages=512 panic=60
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
    │    └─ fw_printenv vyos_direct → "vyos.env" found → SKIP (noop)
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
hugepagesz=2M
hugepages=512
panic=60
vyos-union=/boot/2026.03.27-0142-rolling
```

**Key parameters:**
- `BOOT_IMAGE=/boot/<image>/vmlinuz` — required first argument; enables `is_live_boot()` detection (patch 009). Must be first in bootargs.
- `boot=live` — required even on installed system; VyOS initramfs depends on this
- `vyos-union=/boot/<image>` — tells live-boot where the squashfs is on the installed ext4 partition
- `hugepagesz=2M hugepages=512` — reserves 1 GiB huge pages for VPP. Must be in bootargs or VPP fails. Also a `MANAGED_PARAMS` parameter — must match `config.boot` default to prevent kexec double-boot
- `panic=60` — also a `MANAGED_PARAMS` parameter

**MANAGED_PARAMS:** VyOS `system_option.py` compares `/proc/cmdline` against `config.boot` values for `hugepagesz`, `hugepages`, and `panic`. If they differ on boot (before config is applied), a kexec reboot is triggered. The bootargs above match `config.boot.default` to prevent this.

---

## Image Selection Mechanism (`vyos.env`)

`/boot/vyos.env` is a plain-text key=value file read by U-Boot's `env import -t`. This is the entire image selection mechanism — there is no GRUB, no `grub.cfg`, no EFI variables involved.

### Write paths (all call `vyos-postinstall` or `grub.set_default()`):

| Event | Trigger | What writes vyos.env |
|-------|---------|---------------------|
| First USB boot | `vyos-postinstall.service` | Phase 1 only (SPI flash); no vyos.env (no images) |
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
| `/live/mono-gw.dtb` on USB FAT32 | U-Boot `usb_vyos` (`fatload usb 0:1 mono-gw.dtb`) | Build: `mcopy` into FAT32 USB image |
| `/boot/<image>/mono-gw.dtb` on eMMC p3 | U-Boot `vyos_direct` (`ext4load mmc 0:3 /boot/${vyos_image}/mono-gw.dtb`) | `install_image()`: copies all files from squashfs `/boot/` (where `mono-gw.dtb` was placed at build time) |

For `add system image` (upgrade), patch 011 copies all `.dtb` files from the ISO root into the new image directory during `add_image()`.

---

## kexec / Double-Boot Prevention

VyOS `system_option.py` may trigger a kexec reboot if `/proc/cmdline` doesn't match `MANAGED_PARAMS` from `config.boot`. On this board:

- `kexec-load.service` and `kexec.service` are **masked** (symlinked to `/dev/null` via `99-mask-services.chroot` hook). This forces full cold reboots — ensuring DPAA1, SFP, and I2C hardware re-initialize cleanly via U-Boot.
- The `kexec.target` (systemd target) is still reached by `vyos-router` during boot. When reached, systemd attempts `systemctl kexec` which fails gracefully because `kexec.service` is masked.
- The managed params (`hugepagesz=2M hugepages=512 panic=60`) are pre-baked into the U-Boot bootargs so they match `config.boot.default`. No mismatch → no kexec trigger.

---

## SPI NOR Flash Layout

QSPI NOR flash (64 MiB, `/dev/mtd*`):

| MTD | Name | Size | Contents |
|-----|------|------|---------|
| mtd0 | `rcw` | 2 MiB | Reset Configuration Word |
| mtd1 | `u-boot` | 1 MiB | U-Boot binary |
| mtd2 | `u-boot-env-redundant` | 64 KiB | Redundant U-Boot env |
| **mtd3** | **`u-boot-env`** | **64 KiB** | **Active U-Boot environment** |
| mtd4 | `fman-firmware` | 1 MiB | FMan microcode (injected by U-Boot into DTB) |
| mtd5 | `kernel` | varies | Recovery kernel |
| mtd6 | `dtb` | varies | Recovery DTB |
| mtd7 | `rootfs` | remainder | Recovery rootfs |

`/etc/fw_env.config` points to `/dev/mtd3` with 64 KiB size and 64 KiB sector. Used by `fw_setenv`/`fw_printenv` from `libubootenv-tool`. The classic `u-boot-tools` package uses a different config format and must not be used.

---

## Failure Modes

| Symptom | Cause | Fix |
|---------|-------|-----|
| `Can't set block device` | `emmc=mmc 0:1` — wrong partition (p1 is raw BIOS boot, no ext4) | Run manual U-Boot setup from INSTALL.md |
| `Bad Linux ARM64 Image magic!` | Factory env uses `bootm` (expects uImage). VyOS kernel requires `booti` | Run manual U-Boot setup |
| `ERROR: Did not find a cmdline Flattened Device Tree` | DTB loaded at `0x90000000` (`kernel_comp_addr_r`) — overwritten by kernel decompression | Always use `${fdt_addr_r}` = `0x88000000` |
| `Wrong Ramdisk Image Format` | `booti addr addr:size fdt` — missing `:${filesize}` colon-size suffix | Ensure initrd loads last; use `${ramdisk_addr_r}:${filesize}` |
| Boot loops (kexec) | `hugepagesz`/`hugepages`/`panic` in bootargs don't match `config.boot` | Verify bootargs include `hugepagesz=2M hugepages=512 panic=60` |
| `fw_setenv` fails | `/dev/mtd3` missing — `CONFIG_SPI_FSL_QSPI` not built-in | Verify `CONFIG_SPI_FSL_QSPI=y` in kernel config |
| `vyos-postinstall` skips SPI setup | `fw_printenv` not found or `/etc/fw_env.config` missing | Verify `libubootenv-tool` installed and `fw_env.config` copied into squashfs |
| No network interfaces after boot | DPAA1 stack built as modules instead of `=y` | All `CONFIG_FSL_FMAN/DPAA/BMAN/QMAN/PAMU` must be `=y` |
| CPU locked at 700 MHz | `CONFIG_QORIQ_CPUFREQ=m` — module loads too late, PLLs released | Set `CONFIG_QORIQ_CPUFREQ=y` |