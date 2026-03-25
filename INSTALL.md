# Installing VyOS on Mono Gateway

## What You Need

- USB drive (≥ 2 GB, will be overwritten)
- [Latest ISO](https://github.com/mihakralj/vyos-ls1046a-build/releases/latest) — `vyos-*-LS1046A-arm64.iso`
- Serial console at **115200 8N1**

> **Use only ISOs from this repository.** Generic VyOS ARM64 ISOs lack the
> LS1046A kernel drivers and will boot with no networking and no eMMC.

---

## 1. Write ISO to USB

**Windows (Rufus):** Select USB → SELECT iso → MBR → START → ISO Image mode

**Linux / macOS:**

```bash
sudo dd if=vyos-*-LS1046A-arm64.iso of=/dev/sdX bs=4M status=progress && sync
```

## 2. in U-Boot - Boot VyOS Live from USB

Insert USB, power on, press **any key** during U-Boot countdown.

> **Returning users:** If U-Boot is already configured (you've previously
> installed VyOS with `usb_vyos` set), just insert the USB and power on —
> U-Boot auto-detects the USB and boots VyOS Live. Skip to step 3.

> **Optional — wipe eMMC** (removes OpenWrt or any previous OS):

> ```uboot
> mmc dev 0 && mmc erase 0 0x10000
> ```
> This erases the first 32 MB of eMMC (partition table + headers). Takes ~1 second.

Paste the live USB boot command:

``` uboot
usb start; setenv bootargs "console=ttyS0,115200 earlycon=uart8250,mmio,0x21c0500 boot=live live-media=/dev/sda1 components noeject nopersistence noautologin nonetworking union=overlay net.ifnames=0 fsl_dpaa_fman.fsl_fm_max_frm=9600 quiet"; fatload usb 0:1 ${kernel_addr_r} live/vmlinuz; fatload usb 0:1 ${fdt_addr_r} mono-gw.dtb; fatload usb 0:1 ${ramdisk_addr_r} live/initrd.img; booti ${kernel_addr_r} ${ramdisk_addr_r}:${filesize} ${fdt_addr_r}
```

Wait 60–90 seconds for VyOS login prompt.

> **If `fatload` says "File not found":** run `fatls usb 0:1 live` — if the
> kernel has a version suffix (e.g. `vmlinuz-6.6.128-vyos`), use the full name.

## 3. in VyOS Live - Install VyOS to eMMC

Login `vyos` / `vyos`, then:

``` bash
install image
```

Accept defaults for most prompts. When asked:

- **RAID-1 mirroring:** `n`
- **Console type:** `S` (serial)
- **Which disk:** `/dev/mmcblk0`

Wait 2–4 minutes. The installer automatically:
- Copies the DTB to the boot directory
- Writes `/boot/vyos.env` (tells U-Boot which image to boot)
- Sets up U-Boot environment via `fw_setenv` (one-time, on first install)

## 4. Reboot into eMMC

Remove the USB drive and reboot:

``` bash
reboot
```

U-Boot auto-boots into VyOS on eMMC. No manual U-Boot commands needed.

> **All subsequent reboots are automatic.** U-Boot reads `/boot/vyos.env`
> to find the default image. After upgrades, this file is updated automatically.

## 5. Initial Config

The default config includes DHCP on all interfaces and SSH enabled.
Login `vyos` / `vyos`.

No manual configuration needed for basic connectivity.

---

## Network Interfaces

Physical port order on the back panel:

| Port | VyOS | Type | Notes |
|------|------|------|-------|
| RJ45 Left | `eth0` | 1G SGMII | GPY115C PHY |
| RJ45 Center | `eth1` | 1G SGMII | GPY115C PHY |
| RJ45 Right | `eth2` | 1G SGMII | GPY115C PHY |
| SFP+ Left | `eth3` | 10G XFI | SFP-10G-T, SFP-10G-SR/LR |
| SFP+ Right | `eth4` | 10G XFI | SFP-10G-T, SFP-10G-SR/LR |

All interfaces are preconfigured with DHCP in the default config.

### SFP+ Port Notes

- **SFP-10G-T copper modules** negotiate any speed (10G/5G/2.5G/1G) via the internal RTL8261 rollball PHY. Rate adaptation between the 10G host XFI lane and the copper link speed happens inside the module — verified working at 1G with a 1G switch
- **SFP-10G-SR/LR fiber modules** work at 10G only (fixed 10GBASE-R)
- **1G SFP modules** (SFP-GE-T, SFP-GE-SX) are **not compatible** — the kernel rejects them with `"unsupported SFP module"` because the MAC only advertises 10GBASE-R mode
- **SFP-10G-T boot delay**: Rollball PHY negotiation takes **~17 minutes** after boot. The interface shows `u/D` during this period — this is normal, not a failure
- **Hot-plug after failure**: If you swap an incompatible SFP for a compatible one, bounce the interface: `sudo ip link set eth3 down && sudo ip link set eth3 up`

---

## Upgrading

``` bash
add system image https://github.com/mihakralj/vyos-ls1046a-build/releases/download/<version>/vyos-<version>-LS1046A-arm64.iso
reboot
```

The image installer automatically updates `/boot/vyos.env` so U-Boot boots the
new image on next reboot. No manual `vyos-postinstall` needed.

> **USB re-install:** After initial setup, insert a USB with the new ISO and
> power cycle — U-Boot auto-detects the USB and boots VyOS Live. No U-Boot
> interaction required.

> **NEVER run `install image` from an installed system.** It repartitions the
> eMMC and then fails because `/usr/lib/live/mount/medium/live/filesystem.squashfs`
> does not exist outside of a USB live session. The result is a **destroyed eMMC**
> requiring USB live boot recovery.  Use `add system image` for all upgrades.

---

## Recovery

### Missing DTB

If U-Boot can't find the DTB, it drops to SPI flash recovery Linux.
Log in as `root` (no password):

```bash
mount /dev/mmcblk0p3 /mnt
IMG=$(ls /mnt/boot/ | grep -vE 'grub|efi|lost' | head -1)
cp /sys/firmware/fdt /mnt/boot/${IMG}/mono-gw.dtb
sync && umount /mnt && reboot
```

### Destroyed eMMC (ran `install image` from installed system)

If `install image` was run from an eMMC-installed VyOS (not from USB live),
the eMMC is repartitioned but empty. Recovery requires a **full USB reinstall**:

1. Download the [latest ISO](https://github.com/mihakralj/vyos-ls1046a-build/releases/latest)
2. Write to USB with Rufus (ISO Image mode) or `dd`
3. Boot from USB via U-Boot — follow steps 1–5 above
4. `install image` from the USB live session installs fresh to eMMC

---

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| USB not detected | `usb reset`, or try USB 2.0 drive |
| Silent after "Starting kernel..." | Verify `earlycon=uart8250,mmio,0x21c0500` in bootargs |
| No networking | Wrong ISO — use only ISOs from this repo |
| Upgrade boots old image | Check `/boot/vyos.env` contains correct `vyos_image=`. If missing, run `sudo vyos-postinstall` |
| `fw_setenv` warning during install | Non-fatal — `vyos-postinstall.service` retries on boot. Only needed once for initial U-Boot setup |
| SFP shows `u/D` for 17 min | Normal — rollball PHY negotiation (SFP-10G-T only) |
| SFP "unsupported module" | Only 10G SFP modules work — replace with SFP-10G-SR/T/LR |
| `install image` failed, eMMC empty | See "Destroyed eMMC" recovery above |
