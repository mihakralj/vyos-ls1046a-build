# Install VyOS on Mono Gateway without USB (console-only)

> **STATUS: EXPERIMENTAL, NOT YET VALIDATED ON HARDWARE.**
> This guide is published so testers can try it and report back. Every step ends with a **checkpoint**: if the output does not match, stop and file a report (template at the bottom) instead of improvising.

## Overview

This installs the published VyOS LS1046A ISO onto a Mono Gateway that currently runs OPNsense, using **only the serial console and the ability to power-cycle the board**. No USB stick, no DIP switch changes, no PC-side imaging.

| You need | Why |
|----------|-----|
| Serial console, **115200 8N1** | All interaction happens here |
| Power on/off control | To restart into U-Boot |
| Ethernet cable with internet on one copper port (eth0 to eth2) | Recovery Linux downloads the ISO |
| DIP switch already on **NOR** (RIGHT) | Gate 0 verifies this; you cannot continue without it |

**What gets destroyed:** everything on the eMMC (OPNsense and its config). U-Boot, its environment and recovery Linux live in NOR flash and are not touched until VyOS writes its own `bootcmd` at the end.

---

## How it works

1. **The board boots from NOR.** OPNsense's official install procedure ends with the DIP switch on NOR, with `bootcmd` set to `run opnsense || run recovery`. Because U-Boot and recovery Linux live in NOR, wiping the eMMC cannot brick the board.
2. **Recovery Linux can download.** It is reached with `run recovery` and has `udhcpc`, a full `curl`, `dd`, `sha256sum` and a RAM root of about 3.8 GB, enough for the roughly 571 MiB ISO.
3. **The ISO carries its own partition table.** The release ISO is hybrid: an MBR with two partitions.

   | Partition | Start | Size | Type | Contents |
   |-----------|-------|------|------|----------|
   | p1 | 0 MiB | 495 MiB | 0x17 | ISO9660 with `live/filesystem.squashfs` |
   | p2 | 495 MiB | 76 MiB | 0x0C FAT32 | `live/vmlinuz`, `live/initrd.img`, `mono-gw.dtb`, `boot.scr` |

   (Measured on `2026.10.16-2149-rolling`. Later builds may differ in size; the layout is the same.)

   Writing the ISO raw to `/dev/mmcblk0` turns the eMMC into the equivalent of the USB stick in [INSTALL.md](INSTALL.md). U-Boot can then `fatload mmc 0:2`.
4. **`toram` lets VyOS install onto the disk it booted from.** It copies the squashfs into RAM before userspace starts, so `install image` can repartition the eMMC under the running live system. (`boot.cmd` warns against `toram`, but that warning is about LS1046A USB xHCI stalls. It does not apply to eMMC.)

```
NOR:  [U-Boot + env] [recovery Linux]          untouched until VyOS install writes bootcmd
eMMC: OPNsense  ->  raw ISO (p1 ISO9660, p2 FAT32)  ->  VyOS (64 MiB reserved, p1, p2, p3 ext4)
              Step 2                           Step 4
```

---

## Gate 0: Confirm the board boots from NOR

Power-cycle the board and **press any key** during the U-Boot countdown.

```
=> printenv bootcmd
```

**Checkpoint 0**
- The output contains `opnsense` (normally `bootcmd=run opnsense || run recovery`): you are on NOR. Continue.
- **Anything else: STOP.** If the board boots U-Boot from eMMC, Step 2 makes it unbootable until someone physically flips the DIP switch.

---

## Step 0: Back up OPNsense

Turn on session logging in your terminal program. Boot OPNsense, choose **8) Shell**, and run:

```sh
cat /conf/config.xml
```

Save the logged output. The network served by this box is **down from Step 2 until VyOS is configured**. Plan the downtime.

---

## Step 1: Recovery Linux, network, download, verify

Power-cycle and interrupt U-Boot, then:

```
=> run recovery
```

Log in as `root` (no password). Find the port with a live cable and get an address:

```sh
ip -br link                                    # look for LOWER_UP on eth0, eth1 or eth2
ip link set eth0 up && udhcpc -i eth0 -n -q    # replace eth0 with your port
ip -4 addr; ip route                           # expect an address and a default route
ntpd -q -n -p pool.ntp.org                     # recovery can boot with a stale clock; TLS needs a correct one
df -h /tmp                                     # need at least ~600 MB free
```

Download the latest release named in the project's update feed:

```sh
cd /tmp
FEED=https://raw.githubusercontent.com/mihakralj/vyos-ls1046a-build/main/version.json
URL=$(curl -fsSL $FEED | sed -n 's/.*"url": *"\([^"]*\)".*/\1/p')
VER=$(curl -fsSL $FEED | sed -n 's/.*"version": *"\([^"]*\)".*/\1/p')
echo "$VER  $URL"
curl -fL -o vyos.iso "$URL"
```

Verify it against GitHub's published asset digest:

```sh
curl -fsSL https://api.github.com/repos/mihakralj/vyos-ls1046a-build/releases/tags/$VER \
  | grep -E '"name"|"digest"'
sha256sum vyos.iso
wc -c vyos.iso
```

**Checkpoint 1**
- `sha256sum` equals the `sha256:` digest printed under the `vyos-...-LS1046A-arm64.iso` name.
- `wc -c` equals the ISO size on the release page (598736896 for `2026.10.16-2149-rolling`).

> **Stronger check (optional, on your PC):** download the `.iso` and `.iso.minisig`, then run
> `minisign -Vm vyos-*.iso -P RWQoyCUx+dYqmHM24TcYg31sZvu0v6pYRkkq9cmPVrmurl/iV4ILyYOL`
> (key from [data/vyos-ls1046a.minisign.pub](data/vyos-ls1046a.minisign.pub)) and compare the PC's `sha256sum` with the device's.

**Nothing has been changed on the board yet.** You can stop here safely.

---

## Step 2: Write the ISO to the eMMC (point of no return)

```sh
dd if=/tmp/vyos.iso of=/dev/mmcblk0 bs=4M conv=fsync && sync
head -c $(wc -c < /tmp/vyos.iso) /dev/mmcblk0 | sha256sum
fdisk -l /dev/mmcblk0
```

**Checkpoint 2**
- The read-back `sha256sum` equals the ISO hash from Checkpoint 1.
- `fdisk -l` shows two partitions: `mmcblk0p1` (about 495M) and `mmcblk0p2` (about 76M, FAT32).

OPNsense is now gone. This also overwrote the eMMC firmware region (first 32 MiB). A NOR-booted board does not read it, and a VyOS install breaks eMMC boot anyway ([issue #24](https://github.com/mihakralj/vyos-ls1046a-build/issues/24)). See "Optional: restore the eMMC firmware region" below.

---

## Step 3: Live-boot VyOS from the eMMC

Power-cycle and **press any key** during the U-Boot countdown. Paste these **one line at a time**. Keep the initrd load **last**, so `${filesize}` holds the initrd size for `booti`.

```
setenv ramdisk_addr_r 0xa4000000
mmc dev 0
fatls mmc 0:2 live
fatload mmc 0:2 ${kernel_addr_r} live/vmlinuz
fatload mmc 0:2 ${fdt_addr_r} mono-gw.dtb
fatload mmc 0:2 ${ramdisk_addr_r} live/initrd.img
setenv bootargs console=ttyS0,115200 earlycon=uart8250,mmio,0x21c0500 boot=live live-media=/dev/mmcblk0p1 toram rootdelay=10 components noeject nopersistence noautologin nonetworking union=overlay net.ifnames=0 fsl_dpaa_fman.fsl_fm_max_frm=9600
booti ${kernel_addr_r} ${ramdisk_addr_r}:${filesize} ${fdt_addr_r}
```

These bootargs are the ones from [board/scripts/boot.cmd](board/scripts/boot.cmd), plus `live-media=/dev/mmcblk0p1` (where the squashfs lives) and `toram`. Expect a pause during early boot while the squashfs is copied into RAM.

**Checkpoint 3**
- `fatls` lists `vmlinuz` and `initrd.img`.
- Each `fatload` prints `N bytes read`.
- The boot ends at a `vyos login:` prompt.

---

## Step 4: Install to eMMC

Log in as **vyos** / **vyos**. Before installing, **prove the live system runs from RAM**:

```sh
findmnt -n -o SOURCE /run/live/medium
grep -o toram /proc/cmdline
```

**Checkpoint 4a**
- `findmnt` prints anything **except** `/dev/mmcblk0p1` (typically `tmpfs`).
- `grep` prints `toram`.
- **If `findmnt` shows `/dev/mmcblk0p1`: STOP. Do not run `install image`.** You would overwrite the running system. Reboot and repeat Step 3, checking the `bootargs` line for typos.

```
install image
```

- Select installation target: **`mmcblk0`**
- Enter a root password
- Accept defaults for the rest

**Checkpoint 4b**
- `mmcblk0` is offered as a target. If it is not, the installer still sees it as the live boot medium, which means `toram` did not take effect. Report this.
- The installer completes without errors and mentions writing the U-Boot environment (`fw_setenv`).

---

## Step 5: Reboot into installed VyOS

```
reboot
```

The installer's `vyos-postinstall` has written this to the NOR U-Boot environment, replacing OPNsense's `bootcmd`:

```
bootcmd=run usb_vyos || run vyos || run recovery
```

**Checkpoint 5**
- The board boots VyOS from eMMC to a `vyos login:` prompt, and the password from Step 4 works.
- `show version` reports the version from Step 1.

> **First boot may fail once.** After a fresh eMMC format, U-Boot's ext4 driver occasionally fails with `Failed to load '/boot/vyos.env'` and falls through to recovery. Power-cycle again. This is the same known quirk as in [INSTALL.md](INSTALL.md).

---

## Step 6: Remove the shipped SSH key (do this before connecting WAN)

The shipped images preconfigure user `vyos` with a public key whose **private key is published** in [INSTALL.md](INSTALL.md). Replace it with your own:

```
show system login user vyos authentication public-keys
configure
delete system login user vyos authentication public-keys <name-shown-above>
set system login user vyos authentication public-keys me key <your-ed25519-public-key-body>
set system login user vyos authentication public-keys me type ssh-ed25519
commit
save
exit
```

---

## Troubleshooting

### Recovery Linux: `curl` fails with a TLS or certificate error
The clock is wrong. Rerun `ntpd -q -n -p pool.ntp.org` and check `date -u`. If NTP is blocked on your network, set the time by hand: `date -u -s "2026-10-19 05:00:00"`.

### Recovery Linux: no DHCP lease
Try the other copper ports (`eth0`, `eth1`, `eth2`). The Mono's physical-to-logical port mapping is not intuitive; see [HARDWARE.md](HARDWARE.md) section 3. Static fallback:
```sh
ip addr add 192.168.1.50/24 dev eth0
ip route add default via 192.168.1.1
echo "nameserver 1.1.1.1" > /etc/resolv.conf
```

### U-Boot: `booti` says `kernel_comp_addr_r or kernel_comp_size is not provided`
Your NOR environment is missing the decompression scratch area. Set it and retry the `booti` line:
```
setenv kernel_comp_addr_r 0x90000000
setenv kernel_comp_size 0x1600000
```
Never load the DTB at `0x90000000`: decompression overwrites it.

### U-Boot: `fatls mmc 0:2` shows nothing or errors
Run `mmc rescan` and `mmc part`. You should see two DOS partitions. If not, go back to recovery (`run recovery`) and repeat Step 2.

### Live boot drops to an initramfs shell, unable to find the live medium
Check the `live-media=/dev/mmcblk0p1` spelling in `bootargs`. As a fallback, remove `live-media=...` and let live-boot scan all block devices.

### After install, the board lands in recovery instead of VyOS
If it persists after a second power-cycle, `fw_setenv` probably failed. Interrupt U-Boot and paste the block from [INSTALL.md, "Manual U-Boot environment setup"](INSTALL.md#manual-u-boot-environment-setup-recovery-fallback).

---

## Rollback

| Where you stopped | State | How to recover |
|-------------------|-------|----------------|
| Before Step 2 | OPNsense intact | Power-cycle; autoboot runs `run opnsense` |
| After Step 2 | eMMC holds the raw ISO, OPNsense gone | Repeat Step 3, or reinstall OPNsense from recovery Linux (for example with [mono-imager](https://github.com/HAHermsen/mono-imager)'s OPNsense LAN journey) |
| After Step 4 | VyOS installed | Normal VyOS operation; `run recovery` is still the fallback in `bootcmd` |

Recovery Linux remains reachable from the U-Boot prompt with `run recovery` at every stage, because both live in NOR.

## Optional: restore the eMMC firmware region

A NOR-booted board does not need it. If you want eMMC boot capability back (for example to go back to OpenWRT or Armbian later), boot recovery from NOR with `boot_medium=nor` and run `firmware update`, which rewrites the eMMC firmware region (4 KiB to 32 MiB). VyOS keeps its partitions above 64 MiB, so this does not touch the install. mono-imager's **Update eMMC firmware** menu option automates it. Details: [FIRMWARE.md](FIRMWARE.md).

---

## Test report template

Please open an issue (or post in the Discord thread) with this filled in. A failed run is as useful as a successful one.

```
### Console-only install test report

- VyOS version (from Step 1):
- Previous OS on eMMC: OPNsense <version> / other:
- Board: Mono Gateway DK / other:
- `printenv bootcmd` output (Gate 0):
- Network port used in recovery (eth0/1/2), DHCP or static:

| Checkpoint | Pass/Fail | Notes (paste the relevant console lines) |
|------------|-----------|------------------------------------------|
| 0 NOR boot confirmed       | | |
| 1 Download + hash match    | | |
| 2 dd + read-back hash      | | |
| 3 Live boot to login       | | |
| 4a toram active (findmnt)  | | `findmnt` output: |
| 4b install image completes | | |
| 5 Boots from eMMC          | | needed 2nd power-cycle? y/n |
| 6 SSH key replaced         | | |

- Anything you had to do differently:
- Full console log attached: y/n
```
