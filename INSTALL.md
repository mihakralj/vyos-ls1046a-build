# Installing VyOS on Mono Gateway Development Kit

This guide installs VyOS onto the second eMMC partition (`mmcblk0p2`) alongside
the factory OpenWrt installation on `mmcblk0p1`. Both operating systems coexist;
U-Boot decides which one boots.

Serial console access is required once, to configure U-Boot. After that, the
device boots VyOS automatically.

## What You Need

- Mono Gateway Development Kit (NXP LS1046A, `mono,gateway-dk`)
- Serial console cable: USB-to-TTL, **115200 8N1**, connected to the board's UART header
- A terminal: minicom, picocom, PuTTY, or any equivalent
- Network: the device running OpenWrt, reachable at `192.168.1.234`
- SSH access: `root@192.168.1.234` (password `auckland`, or public key)
- Build machine with Linux and `ssh`/`scp`

The VyOS ISO for this board: [GitHub Releases](https://github.com/mihakralj/vyos-ls1046a-build/releases/latest)

---

## Step 1: Download the ISO

On your build machine:

```bash
ISO_URL=$(curl -s https://api.github.com/repos/mihakralj/vyos-ls1046a-build/releases/latest \
  | python3 -c "import json,sys; r=json.load(sys.stdin); print(next(a['browser_download_url'] for a in r['assets'] if a['name'].endswith('.iso')))")

curl -L "$ISO_URL" -o vyos-ls1046a.iso
echo "Downloaded: $ISO_URL"
```

Verify the sha256:

```bash
curl -sL "$(dirname $ISO_URL)/sha256sum.txt" | grep '\.iso' | sha256sum -c
```

---

## Step 2: Extract ISO Contents

```bash
mkdir -p /tmp/vyos-iso
sudo mount -o loop,ro vyos-ls1046a.iso /tmp/vyos-iso
ls /tmp/vyos-iso/live/
ls /tmp/vyos-iso/*.dtb
```

Expected files:

```
/tmp/vyos-iso/live/vmlinuz-6.6.128-vyos
/tmp/vyos-iso/live/initrd.img-6.6.128-vyos
/tmp/vyos-iso/live/filesystem.squashfs
/tmp/vyos-iso/mono-gw.dtb
```

If `mono-gw.dtb` is missing, the board will not boot. Do not proceed.

---

## Step 3: Prepare eMMC Partition 2

SSH into the running OpenWrt:

```bash
ssh root@192.168.1.234
```

Check the partition layout — confirm `mmcblk0p2` exists and is the right size:

```bash
cat /proc/partitions | grep mmcblk0
```

Expected output:
```
179    0  31080448 mmcblk0       ← ~29.6 GB total
179    1    523264 mmcblk0p1     ← ~511 MB  OpenWrt (do not touch)
179    2  30555136 mmcblk0p2     ← ~29 GB   VyOS target
```

Format `mmcblk0p2`. This erases it completely:

```bash
mke2fs -t ext4 -L vyos /dev/mmcblk0p2
```

Mount it:

```bash
mount /dev/mmcblk0p2 /mnt
mkdir -p /mnt/live
```

---

## Step 4: Copy VyOS Files to eMMC

From your **build machine** (not the device), transfer the ISO contents.
This takes a few minutes — `filesystem.squashfs` is ~450 MB.

```bash
KV=6.6.128-vyos

scp -i ~/.ssh/dropbear_key \
    /tmp/vyos-iso/live/vmlinuz-${KV} \
    root@192.168.1.234:/mnt/live/

scp -i ~/.ssh/dropbear_key \
    /tmp/vyos-iso/live/initrd.img-${KV} \
    root@192.168.1.234:/mnt/live/

scp -i ~/.ssh/dropbear_key \
    /tmp/vyos-iso/live/filesystem.squashfs \
    root@192.168.1.234:/mnt/live/

scp -i ~/.ssh/dropbear_key \
    /tmp/vyos-iso/mono-gw.dtb \
    root@192.168.1.234:/mnt/
```

Verify the transfer on the device:

```bash
ls -lh /mnt/live/ /mnt/*.dtb
sync
```

Expected:

```
/mnt/mono-gw.dtb          92K
/mnt/live/filesystem.squashfs   ~450M
/mnt/live/initrd.img-6.6.128-vyos  ~22M
/mnt/live/vmlinuz-6.6.128-vyos     ~9M
```

Unmount cleanly:

```bash
umount /mnt
```

---

## Step 5: Configure U-Boot

This step requires the **serial console**. U-Boot runs before any OS and cannot
be reached over the network.

### Connect serial

Connect your USB-TTL adapter to the UART header. Open a terminal:

```bash
# Linux
minicom -D /dev/ttyUSB0 -b 115200

# macOS
minicom -D /dev/cu.usbserial-* -b 115200
```

Settings: **115200 8N1, no flow control**.

### Interrupt the boot

Power cycle the board. Within **5 seconds** of U-Boot printing output, press
any key. You will see the `=>` prompt.

```
U-Boot 2025.04 (Jan 18 2026)
...
Hit any key to stop autoboot:  5
=>
```

If you miss the window, the board boots OpenWrt. Power cycle and try again.

### Set the VyOS boot variable

At the `=>` prompt, enter these commands exactly. The `setenv vyos` command is
one long line — copy-paste it whole:

```
setenv vyos 'setenv bootargs "console=ttyS0,115200 earlycon=uart8250,mmio,0x21c0500 boot=live components noeject nopersistence noautologin nonetworking union=overlay net.ifnames=0 quiet"; ext4load mmc 0:2 ${kernel_addr_r} /live/vmlinuz-6.6.128-vyos; ext4load mmc 0:2 ${ramdisk_addr_r} /live/initrd.img-6.6.128-vyos; ext4load mmc 0:2 ${fdt_addr_r} /mono-gw.dtb; booti ${kernel_addr_r} ${ramdisk_addr_r}:${filesize} ${fdt_addr_r}'
```

Update the boot order — OpenWrt boots first, VyOS as fallback:

```
setenv bootcmd 'run emmc || run vyos || run recovery'
saveenv
```

`saveenv` writes to SPI flash (`mtd3: uboot-env`). Confirm:

```
=> saveenv
Saving Environment to SPIFlash... Erasing SPI flash...Writing to SPI flash...done
```

### Test VyOS boot manually

Before rebooting, confirm VyOS loads:

```
run vyos
```

You should see the kernel start:

```
Starting kernel ...
[    0.000000] Booting Linux on physical CPU 0x0000000000 [0x410fd083]
[    0.000000] Linux version 6.6.128-vyos ...
[    0.000000] Machine model: Mono Gateway Development Kit
[    0.000000] SoC family: QorIQ LS1046A
```

If the kernel does not start, see [Troubleshooting](#troubleshooting).

---

## Step 6: Normal Boot

Once U-Boot is configured, normal operation is:

```
Power on
  └─ U-Boot runs emmc → boots OpenWrt on mmcblk0p1 (normal operation)
     └─ if that fails → runs vyos → boots VyOS on mmcblk0p2
        └─ if that fails → runs recovery → boots from SPI flash
```

To switch the default boot to VyOS instead of OpenWrt, change the boot order
from the U-Boot console:

```
setenv bootcmd 'run vyos || run emmc || run recovery'
saveenv
```

---

## Step 7: First VyOS Login

VyOS boots in live mode. Login on the serial console:

```
Username: vyos
Password: vyos
```

SSH access is not enabled by default in live mode. Enable it:

```
configure
set service ssh
set interfaces ethernet eth0 address dhcp
commit
```

Then connect over the network. VyOS assigns DHCP on `eth0`.

### Install to eMMC permanently

To install VyOS from live mode to the eMMC partition (replacing the live
squashfs with an installed system):

```
install image
```

Follow the prompts. When asked for console type, select **S (Serial)** or enter
`ttyS0` for the Mono Gateway.

---

## Network Interfaces

The LS1046A has 5 physical Ethernet ports via NXP DPAA1/FMan:

| VyOS name | Physical | MAC prefix | Notes |
|-----------|----------|------------|-------|
| `eth0` | Port 1 | e8:f6:d7:00:15:ff | Management (br-lan in OpenWrt) |
| `eth1` | Port 2 | e8:f6:d7:00:16:00 | |
| `eth2` | Port 3 | e8:f6:d7:00:16:01 | |
| `eth3` | Port 4 | e8:f6:d7:00:16:02 | WAN in OpenWrt default config |
| `eth4` | Port 5 | e8:f6:d7:00:16:03 | 192.168.0.1 in OpenWrt |

---

## Troubleshooting

**Kernel starts but hangs with no output after "Starting kernel..."**

earlycon is not initializing. Confirm U-Boot `bootargs` contains:
`earlycon=uart8250,mmio,0x21c0500`

**live-boot cannot find filesystem.squashfs**

eMMC driver not loaded. The kernel must be built from this repo (not the
generic VyOS ARM64 ISO). Confirm build version matches.

Verify on a booted system:
```bash
dmesg | grep -i 'mmc\|esdhc\|mmcblk'
```

`mmcblk0` must appear. If it does not, the `sdhci-of-esdhc` driver is absent.

**No network interfaces (eth0–eth4 missing)**

DPAA1 initialization failed. Check:
```bash
dmesg | grep -i 'fman\|dpaa\|fsl'
```

FMan must show successful init. If it shows `firmware not available`, the
`mono-gw.dtb` embedded firmware is not being read correctly.

**U-Boot prompt not reachable**

The 5-second window is short. Keep the terminal focused during power-on.
Some USB-TTL adapters introduce 1–2 second startup latency — plug in before
powering the board.

**`ext4load` fails during `run vyos`**

```
** File not found /live/vmlinuz-6.6.128-vyos **
```

Either the files were not copied to `mmcblk0p2`, or the partition is not
ext4-formatted. From OpenWrt, check:

```bash
mount /dev/mmcblk0p2 /mnt
ls /mnt/live/
```

