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

At the `=>` prompt, paste as a single line (or line by line — either works).

> **Version note:** check `fatls usb 0:1 live` for the exact kernel filename
> and replace `6.6.128-vyos` with whatever version is shown. All four load
> commands must use the same version string.

```
usb start; setenv bootargs "console=ttyS0,115200 earlycon=uart8250,mmio,0x21c0500 boot=live live-media=/dev/sda1 components noeject nopersistence noautologin nonetworking union=overlay net.ifnames=0 quiet"; fatload usb 0:1 ${kernel_addr_r} live/vmlinuz-6.6.128-vyos; fatload usb 0:1 ${fdt_addr_r} mono-gw.dtb; fatload usb 0:1 ${ramdisk_addr_r} live/initrd.img-6.6.128-vyos; booti ${kernel_addr_r} ${ramdisk_addr_r}:${filesize} ${fdt_addr_r}
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

**Do not reboot yet.** If you reboot now, U-Boot will fail to find `mono-gw.dtb`
and fall through to recovery Linux. See the **Recovery Rescue** section below
if this happens.

---

## Step 8 — Post-Install Fixes

Three things must be done before the first eMMC boot. **Step 8a is mandatory** —
without the DTB, VyOS will not boot from eMMC.

### 8a. Copy DTB to eMMC (MANDATORY)

U-Boot loads the kernel and initrd from eMMC p3 but has no access to the squashfs.
It needs `mono-gw.dtb` in the boot image directory on p3, loaded via `ext4load mmc 0:3`.
**Without this file, U-Boot cannot boot VyOS and falls to recovery Linux.**

The DTB source is `/sys/firmware/fdt` — the live, U-Boot-patched device tree that
describes the full 8 GB memory map. It is always available in any running Linux
system on this board (VyOS live, recovery Linux, or installed VyOS).

```bash
sudo mkdir -p /mnt/root
sudo mount /dev/mmcblk0p3 /mnt/root

# Detect installed image name
IMG=$(ls /mnt/root/boot/ | grep -v grub | grep -v efi | head -1)
echo "Image name: $IMG"

# Copy live U-Boot-patched DTB to boot image directory
sudo cp /sys/firmware/fdt /mnt/root/boot/${IMG}/mono-gw.dtb
ls -la /mnt/root/boot/${IMG}/mono-gw.dtb

sudo sync
echo "DTB copied."
```

> **Why `/sys/firmware/fdt`?** This is the exact DTB blob that U-Boot passed to
> the currently running kernel. It contains the full 8 GB memory map and all
> hardware nodes specific to this board unit. It is always present — in VyOS
> live boot, recovery Linux, or installed VyOS. No USB required.

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

Paste these three commands. Replace `2026.03.20-2209-rolling` with the actual
image name from Step 8c:

```
setenv vyos_direct 'setenv bootargs "console=ttyS0,115200 earlycon=uart8250,mmio,0x21c0500 net.ifnames=0 boot=live rootdelay=5 noautologin vyos-union=/boot/2026.03.20-2209-rolling"; ext4load mmc 0:3 ${kernel_addr_r} /boot/2026.03.20-2209-rolling/vmlinuz; ext4load mmc 0:3 ${fdt_addr_r} /boot/2026.03.20-2209-rolling/mono-gw.dtb; ext4load mmc 0:3 ${ramdisk_addr_r} /boot/2026.03.20-2209-rolling/initrd.img; booti ${kernel_addr_r} ${ramdisk_addr_r}:${filesize} ${fdt_addr_r}'
setenv bootcmd 'run vyos_direct || run recovery'
saveenv
```

> **Note:** EFI/GRUB boot (`bootefi`) is permanently broken on this board due to
> NXP DPAA1 `reserved-memory` nodes in the DTB preventing U-Boot EFI initialization.
> `vyos_direct` (booti) is the permanent boot method.

| Variable | Purpose |
|----------|---------|
| `vyos_direct` | Loads kernel + initrd + DTB directly from eMMC p3 via booti |
| `bootcmd` | Auto-boot: try vyos_direct, fall to SPI recovery on failure |

Type `boot` or let the countdown finish — both work:
```
boot
```

---

## Step 10 — Verify Boot from eMMC

Expected serial console output:
```
9210147 bytes read in 381 ms (23.1 MiB/s)     <- vmlinuz
94208 bytes read in 5 ms (18 MiB/s)           <- mono-gw.dtb
33287447 bytes read in 1373 ms (23.1 MiB/s)   <- initrd.img
   Uncompressing Kernel Image to 0
   Loading Ramdisk to f8c42000 ...
Starting kernel ...
[    0.000000] Booting Linux on physical CPU 0x0000000000 [0x410fd083]
[    0.000000] Machine model: Mono Gateway Development Kit
[    0.000000] Linux version 6.6.128-vyos
```

VyOS boots in ~60 seconds. Login: `vyos` / `vyos`.

**If you see `Failed to load '...mono-gw.dtb'` and boot falls to recovery:**
See the Recovery rescue section below.

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

## Recovery Rescue: DTB Missing After Reboot

If you rebooted before running Step 8a (copy DTB), U-Boot will fail to boot
VyOS. The serial console shows:

```
9208868 bytes read in 381 ms (23.1 MiB/s)
Failed to load '/boot/<image>/mono-gw.dtb'
33277271 bytes read in 1373 ms (23.1 MiB/s)
ERROR: Did not find a cmdline Flattened Device Tree
```

U-Boot falls through to SPI flash recovery Linux and you see:

```
Mono Recovery Linux 1.0 recovery /dev/ttyS0
```

Log in as `root` (no password) and copy the DTB from the running kernel's
device tree:

```bash
mkdir -p /tmp/vyos
mount /dev/mmcblk0p3 /tmp/vyos
IMG=$(ls /tmp/vyos/boot/ | grep -v grub | grep -v efi | head -1)
echo "Image: $IMG"
cp /sys/firmware/fdt /tmp/vyos/boot/${IMG}/mono-gw.dtb
ls -la /tmp/vyos/boot/${IMG}/mono-gw.dtb
sync
umount /tmp/vyos
reboot
```

`/sys/firmware/fdt` is the live U-Boot-patched DTB — it has the full 8 GB memory
map and is always present in recovery Linux. No USB required.

After reboot, U-Boot's saved `bootcmd` will autoboot VyOS from eMMC. No need
to interrupt U-Boot or re-run `setenv` commands — they were already saved to
SPI flash by `saveenv` in Step 9.

---

## Future Image Upgrades

Because EFI/GRUB is broken on this board, after each `add system image` you
must update U-Boot's `vyos_direct` to point to the new image name, and copy
the DTB into the new image directory.

**Step 1:** Install the new image from running VyOS:

```
add system image https://github.com/mihakralj/vyos-ls1046a-build/releases/download/<version>/vyos-<version>-LS1046A-arm64.iso
```

**Step 2:** Copy DTB to new image directory:

```bash
NEW=<new-image-name>   # e.g. 2026.04.15-1200-rolling
sudo cp /sys/firmware/fdt /boot/${NEW}/mono-gw.dtb
```

**Step 3:** Update U-Boot to boot the new image.

Setup `fw_env.config` once (tells `fw_setenv` where U-Boot env is on SPI flash):

```bash
echo "/dev/mtd3 0x0 0x20000 0x20000" | sudo tee /etc/fw_env.config
```

Then update `vyos_direct`:

```bash
sudo fw_setenv vyos_direct "setenv bootargs \"console=ttyS0,115200 earlycon=uart8250,mmio,0x21c0500 net.ifnames=0 boot=live rootdelay=5 noautologin vyos-union=/boot/${NEW}\"; ext4load mmc 0:3 \${kernel_addr_r} /boot/${NEW}/vmlinuz; ext4load mmc 0:3 \${fdt_addr_r} /boot/${NEW}/mono-gw.dtb; ext4load mmc 0:3 \${ramdisk_addr_r} /boot/${NEW}/initrd.img; booti \${kernel_addr_r} \${ramdisk_addr_r}:\${filesize} \${fdt_addr_r}"
sudo fw_printenv vyos_direct | grep vyos-union
```

**Step 4:** Reboot.

> **Use only ISOs from this repository** — generic ARM64 ISOs lack the LS1046A
> kernel drivers.

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

**`Failed to load '...mono-gw.dtb'` — falls to recovery**
DTB was not copied in Step 8a. See the Recovery Rescue section above.

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