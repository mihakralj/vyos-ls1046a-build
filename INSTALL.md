# Installing VyOS on Mono Gateway Development Kit

VyOS installs onto the second eMMC partition (`mmcblk0p2`) alongside the
factory OpenWrt on `mmcblk0p1`. Both operating systems coexist — U-Boot
selects which one boots.

The entire installation can be done directly from OpenWrt over the network.
Serial console access is only needed once, to configure U-Boot.

## Prerequisites

| Item | Details |
|------|---------|
| Hardware | Mono Gateway Development Kit (NXP LS1046A) |
| Network | Ethernet cable to any RJ-45 port, internet access on OpenWrt |
| Serial cable | USB-to-TTL, **115200 8N1** — needed only for U-Boot setup |
| Terminal | `tio`, `minicom`, `picocom`, PuTTY, or equivalent |

### Status LED

| Color | Meaning |
|-------|---------|
| Green (solid) | All hardware tests passed |
| Red (solid) | Hardware test failure — check serial output |
| Orange (pulsing) | Recovery Linux |
| White (solid) | OpenWrt booted |

---

## Step 1: Connect to OpenWrt

Connect an Ethernet cable to any port. OpenWrt is at **192.168.1.1** by
default. SSH in:

```bash
ssh root@192.168.1.1
```

> Default credentials: `root` with **no password**.

If OpenWrt is not running or not reachable, see
[Alternative: Recovery Linux](#alternative-recovery-linux) below.

---

## Step 2: Download and Install VyOS

Run this entire block on the Mono Gateway (via SSH to OpenWrt):

```bash
# Fetch the latest eMMC image URL from GitHub
IMG_URL=$(wget -qO- https://api.github.com/repos/mihakralj/vyos-ls1046a-build/releases/latest \
  | jq -r '.assets[] | select(.name | endswith("-emmc.img.gz")) | .browser_download_url')

echo "Downloading: $IMG_URL"

# Download and write directly to partition 2 (does NOT touch OpenWrt on p1)
wget -qO- "$IMG_URL" | gunzip | dd of=/dev/mmcblk0p2 bs=4M
sync

# Read back the kernel version for U-Boot configuration
mkdir -p /mnt/vyos
mount -r /dev/mmcblk0p2 /mnt/vyos
KV=$(ls /mnt/vyos/live/vmlinuz-* | sed 's/.*vmlinuz-//')
umount /mnt/vyos

# Print the U-Boot command with kernel version filled in
echo ""
echo "=== Copy this U-Boot command (one line) ==="
echo "setenv vyos 'setenv bootargs \"console=ttyS0,115200 earlycon=uart8250,mmio,0x21c0500 boot=live components noeject nopersistence noautologin nonetworking union=overlay net.ifnames=0 quiet\"; ext4load mmc 0:2 \${kernel_addr_r} /live/vmlinuz-${KV}; ext4load mmc 0:2 \${ramdisk_addr_r} /live/initrd.img-${KV}; ext4load mmc 0:2 \${fdt_addr_r} /mono-gw.dtb; booti \${kernel_addr_r} \${ramdisk_addr_r}:\${filesize} \${fdt_addr_r}'"
echo "==========================================="
```

The eMMC image is a pre-formatted ext4 filesystem containing the VyOS
kernel, initramfs, root filesystem, and device tree. A single `dd`
command writes everything — no `mkfs`, `mount`, or `cp` needed.

The script prints the complete U-Boot `setenv` command with the correct
kernel version filled in — ready to copy-paste into the serial console.

---

## Step 3: Configure U-Boot

This step requires a **serial console**. U-Boot runs before any OS and
cannot be reached over the network.

### Connect serial

Connect the USB-TTL adapter to the board's **rightmost** header. Open a
terminal:

```bash
# Linux
tio /dev/ttyUSB0

# macOS
tio /dev/cu.usbserial-*
```

Settings: **115200 8N1**, no flow control.

### Interrupt U-Boot

Power cycle the board. Press any key within **5 seconds**:

```
U-Boot 2025.04 (...)
...
Hit any key to stop autoboot:  5
=>
```

### Set the VyOS boot variable

Paste the `setenv vyos` command that was printed at the end of Step 2
(or Step R4). It already has the correct kernel version filled in.

If you don't have the output anymore, the command format is:

```
setenv vyos 'setenv bootargs "console=ttyS0,115200 earlycon=uart8250,mmio,0x21c0500 boot=live components noeject nopersistence noautologin nonetworking union=overlay net.ifnames=0 quiet"; ext4load mmc 0:2 ${kernel_addr_r} /live/vmlinuz-<KV>; ext4load mmc 0:2 ${ramdisk_addr_r} /live/initrd.img-<KV>; ext4load mmc 0:2 ${fdt_addr_r} /mono-gw.dtb; booti ${kernel_addr_r} ${ramdisk_addr_r}:${filesize} ${fdt_addr_r}'
```

Replace `<KV>` with the actual kernel version (e.g., `6.6.128-vyos`).
You can check it by mounting the partition: `ls /mnt/vyos/live/vmlinuz-*`

### Set boot order

Choose your preferred default OS:

**VyOS first** (recommended after testing):

```
setenv bootcmd 'run vyos || run emmc || run recovery'
saveenv
```

**OpenWrt first** (safe — VyOS as fallback):

```
setenv bootcmd 'run emmc || run vyos || run recovery'
saveenv
```

### Test VyOS boot

```
=> run vyos
```

You should see:

```
Starting kernel ...
[    0.000000] Booting Linux on physical CPU 0x0000000000 [0x410fd083]
[    0.000000] Linux version 6.6.xxx-vyos ...
[    0.000000] Machine model: Mono Gateway Development Kit
```

---

## Step 4: First VyOS Login

VyOS boots in live mode. Login on the serial console:

```
Username: vyos
Password: vyos
```

Enable network access:

```
configure
set interfaces ethernet eth0 address dhcp
set service ssh
commit
save
```

Check the assigned IP:

```
show interfaces ethernet eth0
```

Connect over SSH from your workstation.

### Install to eMMC permanently

```
install image
```

Follow the prompts. When asked for console type, select **Serial** and
enter `ttyS0`.

---

## Upgrading VyOS

After `install image`, upgrades use the standard VyOS command — no serial
console or manual file copying required:

```
add system image latest
```

The default configuration includes an update-check URL that points to this
repo's releases. VyOS notifies you at login when a new build is available.

You can also upgrade from a specific URL:

```
add system image https://github.com/mihakralj/vyos-ls1046a-build/releases/download/<VERSION>/<ISO>
```

### Image management

```
show system image                        # list installed images
set system image default-boot <name>     # choose which boots next
delete system image <name>               # remove old images
reboot                                   # activate new image
```

> **Only use ISOs from this repository.** Generic VyOS ARM64 ISOs lack the
> LS1046A kernel drivers (DPAA1, FMan, eSDHC) and will boot with no
> networking and no eMMC support.

---

## Alternative: Install from Recovery Linux

If OpenWrt is broken or unreachable, VyOS can be installed from Recovery
Linux. This path requires a **serial console** for the entire process.

Recovery Linux is a BusyBox-based environment stored in SPI NOR flash
(`mtd7`). It has `curl`, `dd`, `ip`, and `gunzip` — everything needed
to write the eMMC image. No SSH server is available.

### Step R1: Boot into Recovery Linux

Connect the USB-TTL serial adapter to the **rightmost** header. Open a
terminal:

```bash
# Linux
tio /dev/ttyUSB0

# macOS
tio /dev/cu.usbserial-*
```

Power cycle the board. Press any key within **5 seconds** to interrupt
U-Boot:

```
Hit any key to stop autoboot:  5
=>
```

Boot Recovery Linux:

```
=> run recovery
```

Login as `root` (no password). The LED turns **orange (pulsing)**.

### Step R2: Configure networking

```bash
# Option A: DHCP (if your network has a DHCP server)
udhcpc -i eth0

# Option B: Static IP
ip link set eth0 up
ip addr add 10.0.0.199/24 dev eth0
ip route add default via 10.0.0.1 dev eth0
```

> Adjust the IP address and gateway to match your network. The device
> needs internet access to reach `github.com`.

Verify connectivity:

```bash
ping -c 2 github.com
```

### Step R3: Download and write eMMC image

```bash
# Get the latest eMMC image URL
IMG_URL=$(curl -skL https://api.github.com/repos/mihakralj/vyos-ls1046a-build/releases/latest \
  | grep -o '"browser_download_url": "[^"]*emmc\.img\.gz"' | cut -d'"' -f4)

echo "Downloading and writing: $IMG_URL"

# Download, decompress, and write directly to partition 2
curl -kL "$IMG_URL" | gunzip | dd of=/dev/mmcblk0p2 bs=4M
sync
```

> The `-k` flag skips TLS verification — Recovery Linux may not have
> up-to-date CA certificates. This takes 2-5 minutes depending on
> network speed.

### Step R4: Configure U-Boot

Since you are already on the serial console, reboot and interrupt U-Boot:

```bash
reboot
```

Press any key within 5 seconds. Check the kernel version:

```
=> ext4ls mmc 0:2 /live/
```

Note the kernel version from the `vmlinuz-*` filename, then set the boot
variable per [Step 3: Configure U-Boot](#step-3-configure-u-boot).

After `saveenv`, test with `run vyos` — VyOS should boot directly.

---

## Network Interfaces

The LS1046A has 5 Ethernet ports via NXP DPAA1/FMan. MAC addresses are
unique per device — read yours from the board label or `show interfaces`:

| Port | Position | VyOS name | Notes |
|------|----------|-----------|-------|
| 1 | Leftmost | `eth0` | Management / DHCP |
| 2 | | `eth1` | |
| 3 | | `eth2` | |
| 4 | | `eth3` | WAN in OpenWrt |
| 5 | Rightmost | `eth4` | |

---

## eMMC Partition Layout

```
mmcblk0       ~29.6 GB total
├─ mmcblk0p1  ~511 MB   OpenWrt (ext4) — factory OS, do not touch
├─ mmcblk0p2  ~29.1 GB  VyOS (ext4)
├─ mmcblk0boot0  32 MB  hardware boot partition (unused)
└─ mmcblk0boot1  32 MB  hardware boot partition (unused)
```

---

## Troubleshooting

**Kernel hangs after "Starting kernel..."**
→ Confirm bootargs: `earlycon=uart8250,mmio,0x21c0500`

**live-boot cannot find filesystem.squashfs**
→ eMMC driver missing. Must use ISOs from this repo, not generic ARM64.
→ Check: `dmesg | grep -i 'mmc\|esdhc\|mmcblk'` — `mmcblk0` must appear.

**No network interfaces (eth0–eth4)**
→ DPAA1/FMan init failed: `dmesg | grep -i 'fman\|dpaa'`
→ FMan must show firmware loaded. If `firmware not available`, the DTB may
  be wrong.

**U-Boot prompt not reachable**
→ Press key within 5 seconds. Plug USB-TTL adapter in before powering on.

**`ext4load` fails with "File not found"**
→ Files not on `mmcblk0p2`. Mount and check: `mount /dev/mmcblk0p2 /mnt; ls /mnt/live/`

---

## See Also

- [Mono Gateway Getting Started](https://github.com/ryneches/mono-gateway-docs/blob/master/gateway-development-kit/getting-started.md) — factory setup, serial console, Recovery Linux
- [PORTING.md](PORTING.md) — technical LS1046A porting notes
- [README.md](README.md) — what this build changes and why
