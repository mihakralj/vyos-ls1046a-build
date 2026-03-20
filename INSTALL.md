# Installing VyOS on Mono Gateway Development Kit

VyOS installs onto the second eMMC partition (`mmcblk0p2`) alongside the
factory OpenWrt on `mmcblk0p1`. Both operating systems coexist — U-Boot
selects which one boots.

## Prerequisites

| Item | Details |
|------|---------|
| Hardware | Mono Gateway Development Kit (NXP LS1046A) |
| Network | Ethernet cable on eth0 (leftmost port) with internet access |
| Serial | USB-to-TTL, **115200 8N1** — needed only for U-Boot setup |
| Terminal | `tio`, `minicom`, `picocom`, `PuTTY`, `Termius` or equivalent |

---

## Step 1: Get a Shell

Choose **one** column based on what's currently running on your board:

<table>
<tr>
<th>From working OpenWrt (SSH over network)</th>
<th>From Recovery Linux (serial console)</th>
</tr>
<tr><td>

Connect Ethernet to any port.
SSH into OpenWrt if it is already configured:

```bash
ssh root@192.168.1.1
```

Default: `root` with **no password**.

Verify: `ping -c 2 github.com`

</td><td>

In serial console get the U-Boot `=>` prompt:

```
=> run recovery
```

Login as `root` (no password).

Configure networking:

```bash
# DHCP
udhcpc -i eth0

# — or static —
ip link set eth0 up
ip addr add 10.0.0.199/24 dev eth0
ip route add default via 10.0.0.1
```

Verify: `ping -c 2 github.com`

</td></tr>
</table>

---

## Step 2: Download and Write VyOS to eMMC

Copy-paste this script to either OpenWrt or Recovery Linux:

```bash
# Get the latest VyOS eMMC image URL from GitHub
IMG_URL=$(wget --no-check-certificate -qO- \
  https://api.github.com/repos/mihakralj/vyos-ls1046a-build/releases/latest \
  | grep -o '"browser_download_url": "[^"]*emmc\.img\.gz"' | cut -d'"' -f4)

echo "Downloading: $IMG_URL"

# Download and write directly to partition 2 (does NOT touch OpenWrt on p1)
wget --no-check-certificate -qO- "$IMG_URL" | gunzip | dd of=/dev/mmcblk0p2 bs=4M
sync

# Extract kernel version for U-Boot
mkdir -p /mnt/vyos
mount -r /dev/mmcblk0p2 /mnt/vyos
KV=$(ls /mnt/vyos/live/vmlinuz-* | sed 's/.*vmlinuz-//')
umount /mnt/vyos

# Print the U-Boot command with kernel version filled in
echo ""
echo "=== Copy this U-Boot command (one line) ==="
echo "setenv vyos 'setenv bootargs \"console=ttyS0,115200 earlycon=uart8250,mmio,0x21c0500 boot=live live-media=/dev/mmcblk0p2 components noeject nopersistence noautologin nonetworking union=overlay net.ifnames=0 quiet\"; ext4load mmc 0:2 \${kernel_addr_r} /live/vmlinuz-${KV}; ext4load mmc 0:2 \${fdt_addr_r} /mono-gw.dtb; ext4load mmc 0:2 \${ramdisk_addr_r} /live/initrd.img-${KV}; booti \${kernel_addr_r} \${ramdisk_addr_r}:\${filesize} \${fdt_addr_r}'"
echo "==========================================="
```

The wget will take a bit to pull the full ISO over - give it time.

Then *Copy* the command shown - you will need it in U-Boot.

---

## Step 3: Configure U-Boot

Reboot with `reboot`, then press a key to interrupt U-Boot autoboot.

**Paste** the `setenv vyos '...'` command printed at the end of Step 2. Then:

```
=> run vyos
```

You should see VyOS booting:

```
Starting kernel ...
[    0.000000] Booting Linux on physical CPU 0x0000000000 [0x410fd083]
[    0.000000] Linux version 6.6.xxx-vyos ...
[    0.000000] Machine model: Mono Gateway Development Kit
...
```

Once confirmed working, save the boot order so VyOS starts automatically:

```
=> setenv bootcmd 'run vyos || run emmc || run recovery'
=> saveenv
```

---

## Step 4: First VyOS Login

Login on the serial console:

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

> **Note:** VyOS runs from squashfs+overlay — this is normal and is how VyOS
> operates in production. The eMMC image you dd'd **is** the installed system.
> Running `install image` may work (U-Boot has EFI support) but would
> repartition `mmcblk0p2` and add GRUB. Test carefully — it will not affect
> OpenWrt on `mmcblk0p1` but may change the partition layout on p2.

---

## Upgrading VyOS

Re-run the download script from Step 2 (from OpenWrt SSH or VyOS SSH),
then reboot. This overwrites the squashfs image on `mmcblk0p2`.

If you ran `install image` and have GRUB managing images, the standard
`add system image <url>` command should work for subsequent upgrades.

> **Only use images from this repository.** Generic VyOS ARM64 ISOs lack
> the LS1046A kernel drivers (DPAA1, FMan, eSDHC) and will boot with no
> networking and no eMMC support.

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
Check bootargs: `earlycon=uart8250,mmio,0x21c0500`

**live-boot cannot find filesystem.squashfs**
eMMC driver missing. Must use ISOs from this repo, not generic ARM64.
Check: `dmesg | grep -i 'mmc\|esdhc\|mmcblk'` — `mmcblk0` must appear.

**No network interfaces (eth0-eth4)**
Run the FMan diagnostic sequence below.

**U-Boot prompt not reachable**
Press key within 5 seconds. Plug USB-TTL adapter in before powering on.

**`ext4load` fails with "File not found"**
Files not on `mmcblk0p2`. Mount and check: `mount /dev/mmcblk0p2 /mnt; ls /mnt/live/`

---

## FMan / DPAA1 Network Diagnostics

If no Ethernet interfaces appear after boot, run these commands on the
VyOS serial console to diagnose:

```bash
# Check if drivers registered and bound to devices
ls /sys/bus/platform/drivers/fsl-fman/
ls /sys/bus/platform/drivers/dpaa-ethernet/
ip link show

# Verify kernel config includes DPAA/FMan
zcat /proc/config.gz | grep -iE 'FMAN|DPAA|LAYERSCAPE'

# Check dmesg for FMan/DPAA messages (may be empty if probe succeeded silently)
dmesg | grep -iE 'fman|dpaa|fsl_dpa|memac|defer|probe'

# Check for deferred probes
mount -t debugfs none /sys/kernel/debug 2>/dev/null
cat /sys/kernel/debug/devices_deferred 2>/dev/null
```

FMan uses `dev_dbg()` — success messages only appear with dynamic debug:

```bash
echo "file fman.c +p" > /sys/kernel/debug/dynamic_debug/control
echo "file dpaa_eth.c +p" > /sys/kernel/debug/dynamic_debug/control
echo "file mac.c +p" > /sys/kernel/debug/dynamic_debug/control
dmesg | tail -30
```

---

## Testing with Mainline RDB DTB

Recent builds include a mainline `fsl-ls1046a-rdb.dtb` compiled from the
kernel source tree. If networking fails with `mono-gw.dtb`, test with the
RDB DTB to determine whether the issue is DTB-specific:

At the U-Boot `=>` prompt, load the RDB DTB instead of `mono-gw.dtb`:

```
setenv vyos_rdb 'setenv bootargs "console=ttyS0,115200 earlycon=uart8250,mmio,0x21c0500 boot=live live-media=/dev/mmcblk0p2 components noeject nopersistence noautologin nonetworking union=overlay net.ifnames=0 quiet"; ext4load mmc 0:2 ${kernel_addr_r} /live/vmlinuz-6.6.128-vyos; ext4load mmc 0:2 ${fdt_addr_r} /fsl-ls1046a-rdb.dtb; ext4load mmc 0:2 ${ramdisk_addr_r} /live/initrd.img-6.6.128-vyos; booti ${kernel_addr_r} ${ramdisk_addr_r}:${filesize} ${fdt_addr_r}'
run vyos_rdb
```

> **Note:** The RDB DTB is designed for the NXP LS1046A-RDB reference board.
> Hardware-specific peripherals (I2C devices, SPI flash layout, GPIO) may differ
> from the Mono Gateway, but DPAA1/FMan networking should function identically
> since both boards use the same SoC and FMan v3.

If networking works with the RDB DTB but not `mono-gw.dtb`, the issue is in the
custom DTB. The mono-gw DTB contains NXP SDK-specific nodes (`fsl,dpaa`,
`fsl,dpa-oh`, `fman-extended-args`) that are not recognized by the mainline
kernel. A new DTB based on the mainline `qoriq-fman3-0.dtsi` include files
is required.

---

## See Also

- [Mono Gateway Getting Started](https://github.com/ryneches/mono-gateway-docs/blob/master/gateway-development-kit/getting-started.md) — factory setup, serial console, Recovery Linux
- [PORTING.md](PORTING.md) — technical LS1046A porting notes
- [README.md](README.md) — what this build changes and why
