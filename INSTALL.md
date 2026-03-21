# Installing VyOS on Mono Gateway Development Kit

Complete guide: factory board (OpenWrt on eMMC) → VyOS installed to eMMC with GRUB,
ready for standard `add system image` upgrades.

**Time required:** ~20 minutes  
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
| SSH client | For post-boot steps (optional but convenient) |

> **Use only ISOs from this repository.** Generic VyOS ARM64 ISOs do not include
> the LS1046A drivers (DPAA1/FMan, eSDHC) and will boot with no networking and
> no eMMC.

---

## Board Overview

```
CPU:     4x ARM Cortex-A72 @ 1.8 GHz (NXP LS1046A)
RAM:     8 GB DDR4 (Bank 0: 0x80000000, Bank 1: 0x880000000)
eMMC:    29.6 GB (mmcblk0)
Serial:  ttyS0, 115200 8N1, MMIO 0x21c0500
U-Boot:  SPI flash mtd2, version 2025.04
```

**Factory eMMC layout (before this guide):**
```
mmcblk0p1   511 MB   OpenWrt root (ext4)   <- factory OS
mmcblk0p2  29.1 GB   empty or prior VyOS
```

**After this guide:**
```
mmcblk0p1     1 MB   BIOS Boot  (EF02)   <- raw, no filesystem
mmcblk0p2   256 MB   EFI System (EF00)   <- FAT32, GRUB lives here
mmcblk0p3  29.4 GB   Linux      (8300)   <- ext4, VyOS squashfs + data
```

The 16 MB gap between p1 (ends sector 4095) and p2 (starts sector 36864) is
reserved bootloader clearance built into the patched VyOS installer.

> **OpenWrt is destroyed.** `install image` rewrites the entire GPT on mmcblk0.
> There is no recovery back to OpenWrt without reflashing eMMC.
> The SPI flash (U-Boot, recovery kernel) is untouched.

---

## Step 1 — Prepare USB Drive

Write the ISO to the USB drive:

**Windows — Rufus:**
1. Open Rufus, select the USB drive
2. Click **SELECT** and choose the `.iso` file
3. Boot selection: **Disk or ISO image**
4. Partition scheme: **MBR**
5. Click **START** — when prompted, choose **Write in ISO Image mode**
6. Wait for completion

**Linux / macOS:**
```bash
# Replace /dev/sdX with your USB device
sudo dd if=vyos-*-LS1046A-arm64.iso of=/dev/sdX bs=4M status=progress
sudo sync
```

> Write to the whole device, not a partition (`/dev/sdX` not `/dev/sdX1`).

After writing, the USB root will contain `mono-gw.dtb`, `live/vmlinuz-*`, and
`EFI/boot/bootaa64.efi`. No manual file copying is needed.

---

## Step 2 — Connect Serial Console

Connect the USB-to-TTL adapter to the board's serial header.
Configure your terminal at **115200 8N1**, no flow control:

**Windows — plink:**
```
plink -serial COM7 -sercfg 115200,8,n,1,N
```
Replace `COM7` with the actual port (Device Manager → Ports).

**Windows — PuTTY:**
Connection type: Serial | Speed: 115200 | Serial line: COMx

**Linux / macOS:**
```bash
tio /dev/ttyUSB0 -b 115200
```

Power on the board. U-Boot messages appear within 1–2 seconds.

---

## Step 3 — Interrupt U-Boot

U-Boot counts down 5 seconds before autobooting. **Press any key** to stop it:

```
U-Boot 2025.04-g26d27571ac82-dirty (Jan 18 2026 - 17:54:35 +0000)
...
Hit any key to stop autoboot:  5 ^
=>
```

If you miss the window, power-cycle and try again.

---

## Step 4 — Insert USB and Verify Detection

Plug the USB drive into any USB port on the board:

```
=> usb start
=> usb info
```

Expected output:
```
       Device 0: Vendor: SanDisk  Rev: 1.00 Prod: Ultra
            Type: Removable Hard Disk
            Capacity: nnn GB = nnn MB
```

If no device found, try a different USB port. Some USB 3.x drives have XHCI
compatibility issues on this board; a USB 2.0 drive is a reliable fallback.

Verify the ISO files are accessible:
```
=> fatls usb 0:1 live
```

You should see `vmlinuz-6.6.128-vyos`, `initrd.img-6.6.128-vyos`, and
`filesystem.squashfs`. Check the DTB is at the root:
```
=> fatls usb 0:1
```
Confirm `mono-gw.dtb` is listed.

---

## Step 5 — Boot VyOS Live from USB

At the `=>` prompt, paste these five lines.

> **Version note:** check `fatls usb 0:1 live` for the exact kernel filename
> and replace `6.6.128-vyos` with whatever version is shown. All four load
> commands must use the same version string.

```
setenv bootargs "console=ttyS0,115200 earlycon=uart8250,mmio,0x21c0500 boot=live live-media=/dev/sda1 components noeject nopersistence noautologin nonetworking union=overlay net.ifnames=0 quiet"
fatload usb 0:1 ${kernel_addr_r} live/vmlinuz-6.6.128-vyos
fatload usb 0:1 ${fdt_addr_r} mono-gw.dtb
fatload usb 0:1 ${ramdisk_addr_r} live/initrd.img-6.6.128-vyos
booti ${kernel_addr_r} ${ramdisk_addr_r}:${filesize} ${fdt_addr_r}
```

**Load order is mandatory.** Each `fatload` overwrites `${filesize}`. The `booti`
command uses `${ramdisk_addr_r}:${filesize}` to tell the kernel the initrd size.
Loading initrd last ensures `${filesize}` holds the initrd size, not the
kernel or DTB size. Wrong order causes a kernel ramdisk panic.

Expected U-Boot output:
```
9210147 bytes read in ...    <- kernel
94208 bytes read in ...      <- dtb
33287447 bytes read in ...   <- initrd
Starting kernel ...
[    0.000000] Booting Linux on physical CPU 0x0000000000 [0x410fd083]
[    0.000000] Machine model: Mono Gateway Development Kit
```

VyOS boots in 60–90 seconds and shows a login prompt on serial.

---

## Step 6 — Log In to VyOS Live

Serial console:
```
Username: vyos
Password: vyos
```

Check which interface got a DHCP address:
```
show interfaces
```

Once you have an IP address, SSH from your workstation is more comfortable
for the remaining steps:
```bash
ssh vyos@<ip-address>
```

---

## Step 7 — Run `install image`

```
install image
```

Answer each prompt:

```
Welcome to VyOS installation!
Would you like to continue? [y/N]                               y

What would you like to name this image? (Default: 2026.xx.xx)  <Enter>

Please enter a password for the "vyos" user:                   vyos
Please confirm password for the "vyos" user:                   vyos

What console should be used? (K: KVM, S: Serial)? (Default: K) S

Would you like to configure RAID-1 mirroring? [Y/n]            n

The following disks were found:
  Drive: /dev/sda   (USB drive)
  Drive: /dev/mmcblk0 (29.6 GB)  <- eMMC
Which one should be used? (Default: /dev/sda)                  /dev/mmcblk0

Installation will delete all data on the drive. Continue? [y/N] y

Would you like to use all the free space on the drive? [Y/n]   <Enter>

Which file would you like as boot config? (Default: 1)         <Enter>
```

> **Serial console selection (`S`) is critical.** If you accept the default `K`
> (KVM), GRUB will boot silently with no serial output. Fix 1 in Step 8b
> corrects this after the fact.

The installer formats the partitions, copies ~600 MB of data, and installs GRUB.
This takes 2–4 minutes. Completion message:

```
The image installed successfully.
Before rebooting, ensure any required bootloader (e.g. U-Boot) is written to the disk.
```

**Do not reboot yet.**

---

## Step 8 — Post-Install Fixes

Three things must be done before the first eMMC boot.

### 8a. Copy DTB to eMMC

U-Boot has no filesystem access to the squashfs. It needs `mono-gw.dtb` placed
explicitly in two locations:
- EFI partition (p2): for the `bootefi` path (GRUB), loaded via `fatload mmc 0:2`
- Boot image directory (p3): for the `booti` direct fallback, loaded via `ext4load mmc 0:3`

```bash
sudo mkdir -p /mnt/efi /mnt/root
sudo mount /dev/mmcblk0p2 /mnt/efi
sudo mount /dev/mmcblk0p3 /mnt/root

# Detect installed image name
IMG=$(ls /mnt/root/boot/ | grep -v grub | grep -v efi | head -1)
echo "Image name: $IMG"

# Copy to EFI partition root (bootefi path)
sudo cp /usr/lib/live/mount/medium/mono-gw.dtb /mnt/efi/mono-gw.dtb

# Copy to boot image directory (booti path)
sudo cp /usr/lib/live/mount/medium/mono-gw.dtb /mnt/root/boot/${IMG}/mono-gw.dtb

sudo sync
echo "DTBs copied."
```

### 8b. Fix GRUB Console Settings

Three bugs in the installed GRUB config affect this board:

| Bug | File | Symptom |
|-----|------|---------|
| Default console `tty` (KVM) | `20-vyos-defaults-autoload.cfg` | No serial output |
| ARM64 remaps `ttyS` → `ttyAMA` | `50-vyos-options.cfg` | Wrong UART, silent boot |
| Missing LS1046A `earlycon` | `vyos-versions/${IMG}.cfg` | No output until late boot |

```bash
CFG=/mnt/root/boot/grub/grub.cfg.d

# Fix 1: default console type tty -> ttyS
sudo sed -i 's/set console_type="tty"/set console_type="ttyS"/' \
    $CFG/20-vyos-defaults-autoload.cfg

# Fix 2: ARM64 ttyAMA -> ttyS
sudo sed -i 's/set serial_console="ttyAMA"/set serial_console="ttyS"/' \
    $CFG/50-vyos-options.cfg

# Fix 3: Add LS1046A earlycon to boot entry
sudo sed -i 's|set boot_opts="boot=live|set boot_opts="earlycon=uart8250,mmio,0x21c0500 boot=live|' \
    $CFG/vyos-versions/${IMG}.cfg

sudo sync
```

Verify all three fixes applied:
```bash
grep console_type   $CFG/20-vyos-defaults-autoload.cfg
grep serial_console $CFG/50-vyos-options.cfg
grep earlycon       $CFG/vyos-versions/${IMG}.cfg
```

Expected output:
```
set console_type="ttyS"
                set serial_console="ttyS"
    set boot_opts="earlycon=uart8250,mmio,0x21c0500 boot=live ...
```

Unmount:
```bash
sudo umount /mnt/efi /mnt/root
```

### 8c. Note the Image Name

```bash
echo "Image name: $IMG"
```

Write this down. You will need it in Step 9 to configure U-Boot.

---

## Step 9 — Configure U-Boot for Permanent Boot

Reboot the live system:
```
reboot
```

**Remove the USB drive** as the board restarts. Press any key within 5 seconds
to reach the U-Boot `=>` prompt.

Paste these four commands. Replace `2026.03.20-2209-rolling` with the actual
image name from Step 8c:

```
setenv vyos_efi 'setenv fdt_high 0xffffffffffffffff; fatload mmc 0:2 ${fdt_addr_r} mono-gw.dtb; fatload mmc 0:2 ${kernel_addr_r} EFI/BOOT/BOOTAA64.EFI; bootefi ${kernel_addr_r} ${fdt_addr_r}'
setenv vyos_direct 'setenv bootargs "console=ttyS0,115200 earlycon=uart8250,mmio,0x21c0500 net.ifnames=0 boot=live rootdelay=5 noautologin vyos-union=/boot/2026.03.20-2209-rolling"; ext4load mmc 0:3 ${kernel_addr_r} /boot/2026.03.20-2209-rolling/vmlinuz; ext4load mmc 0:3 ${fdt_addr_r} /boot/2026.03.20-2209-rolling/mono-gw.dtb; ext4load mmc 0:3 ${ramdisk_addr_r} /boot/2026.03.20-2209-rolling/initrd.img; booti ${kernel_addr_r} ${ramdisk_addr_r}:${filesize} ${fdt_addr_r}'
setenv bootcmd 'run vyos_efi || run vyos_direct || run recovery'
saveenv
```

> If `bootefi` OOM persists, `bootcmd` falls through to `vyos_direct` automatically.
| `vyos_direct` | U-Boot → kernel directly via booti | Fallback; confirmed working; bypasses GRUB |
| `bootcmd` | EFI first, direct second, recovery third | Automatic fallthrough on failure |

Boot:
```
boot
```

---

## Step 10 — Verify Boot from eMMC

**Via EFI / GRUB (preferred path):**
```
=> run vyos_efi
Loading mono-gw.dtb ... 94208 bytes
Loading EFI/BOOT/BOOTAA64.EFI ... 990600 bytes
...
GNU GRUB  version 2.12
...
[    0.000000] Machine model: Mono Gateway Development Kit
[    0.000000] Linux version 6.6.128-vyos
```

**Via direct booti (fallback path):**
```
=> run vyos_direct
Loading /boot/2026.03.20-2209-rolling/vmlinuz ...
Loading /boot/2026.03.20-2209-rolling/initrd.img ...
[    0.000000] Machine model: Mono Gateway Development Kit
```

Both paths produce a fully working VyOS system. Login: `vyos` / `vyos`.

---

## Step 11 — Initial VyOS Configuration

```
configure
set interfaces ethernet eth0 address dhcp
set service ssh
commit
save
```

```
show interfaces
ping 8.8.8.8 count 3
```

---

## Future Image Upgrades

Once running from eMMC with GRUB installed, standard VyOS upgrades work:

```
add system image https://github.com/mihakralj/vyos-ls1046a-build/releases/download/<version>/vyos-<version>-LS1046A-arm64.iso
```

GRUB automatically adds the new image to its boot menu. On reboot, both images
are listed with a 5-second selection timeout.

**After each new image, apply the earlycon fix** (Fixes 1 and 2 persist across
image additions; only Fix 3 needs repeating for each new image):

```bash
# From running VyOS — /boot is the installed partition, no mount needed:
NEW_IMG=<new-image-name>
sudo sed -i 's|set boot_opts="boot=live|set boot_opts="earlycon=uart8250,mmio,0x21c0500 boot=live|' \
    /boot/grub/grub.cfg.d/vyos-versions/${NEW_IMG}.cfg
```

> **Use only ISOs from this repository** — generic ARM64 ISOs lack the LS1046A
> kernel drivers and will fail to find the eMMC.

---

## Network Interfaces

| Port | Position | VyOS name | Notes |
|------|----------|-----------|-------|
| 1 | Leftmost | `eth0` | Recommended management port |
| 2 | | `eth1` | |
| 3 | | `eth2` | |
| 4 | | `eth3` | |
| 5 | Rightmost | `eth4` | |

All five ports are NXP DPAA1/FMan. MAC addresses are unique per board —
read from the board label or `show interfaces`.

---

## Troubleshooting

**USB not detected after `usb start`**
Try `usb reset`. If still nothing, try a different USB port or a USB 2.0 drive.

**`fatload` fails — "File not found"**
The ISO was written incorrectly. Re-write with Rufus in ISO Image mode, targeting
the whole device, not a partition.

**Kernel hangs after "Starting kernel..."**
Verify `printenv bootargs` contains `earlycon=uart8250,mmio,0x21c0500`.

**Serial console silent after GRUB loads**
Fix 1 or Fix 2 from Step 8b was not applied, or the install was done with `K`
(KVM) console. Boot from USB again, mount mmcblk0p3, and apply the sed commands.

**`bootefi` fails with "out of memory"**
Known issue — see [boot.efi.md](boot.efi.md). The `vyos_direct` fallback in
`bootcmd` handles this automatically. To test the fix: `setenv fdt_high 0xffffffffffffffff; run vyos_efi`.

**No networking after boot (eth0–eth4 missing)**
Wrong ISO (generic ARM64 without DPAA1 drivers). Use only ISOs from this repo.
Diagnose: `dmesg | grep -iE 'fman|dpaa|memac'`.

**VyOS cannot find its squashfs on eMMC**
The image name in the GRUB entry must match the directory under `/boot/`.
Verify with: `ext4ls mmc 0:3 /boot/` at the U-Boot prompt.

---

## U-Boot Memory Map

| Variable | Address | Use |
|----------|---------|-----|
| `kernel_addr_r` | `0x82000000` | Kernel or EFI binary load address |
| `fdt_addr_r` | `0x88000000` | Device tree (DTB) |
| `ramdisk_addr_r` | `0x88080000` | Initrd (booti only) |
| `kernel_comp_addr_r` | `0x90000000` | Compressed kernel decompress area |

DRAM Bank 0: `0x80000000`–`0xFBDFFFFF` (1982 MB)
DRAM Bank 1: `0x880000000`–`0x9FFFFFFFF` (6144 MB)

---

## SPI Flash Layout (read-only reference)

```
mtd1    1 MB    rcw-bl2           ARM Trusted Firmware BL2
mtd2    2 MB    uboot             U-Boot
mtd3    1 MB    uboot-env         U-Boot environment (saveenv target)
mtd4    1 MB    fman-ucode        FMan microcode (injected to DTB at boot)
mtd5    1 MB    recovery-dtb      Recovery boot device tree
mtd6    4 MB    (unallocated)
mtd7   22 MB    kernel-initramfs  Recovery kernel + initramfs
```

`run recovery` boots from `mtd7` — a minimal Linux initrd for eMMC repair.

---

## See Also

- [PORTING.md](PORTING.md) — LS1046A kernel driver requirements and boot architecture
- [boot.efi.md](boot.efi.md) — U-Boot EFI analysis, confirmed commands, failure modes
- [Mono Gateway Getting Started](https://github.com/ryneches/mono-gateway-docs/blob/master/gateway-development-kit/getting-started.md) — hardware setup, serial console pinout