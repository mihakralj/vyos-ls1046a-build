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

> **Optional — wipe eMMC** (removes OpenWrt or any previous OS):

> ```uboot
> mmc dev 0 && mmc erase 0 0x10000
> ```
> This erases the first 32 MB of eMMC (partition table + headers). Takes ~1 second.

Paste the live usb boot command:

``` uboot
usb start; setenv bootargs "console=ttyS0,115200 earlycon=uart8250,mmio,0x21c0500 boot=live live-media=/dev/sda1 components noeject nopersistence noautologin nonetworking union=overlay net.ifnames=0 quiet"; fatload usb 0:1 ${kernel_addr_r} live/vmlinuz; fatload usb 0:1 ${fdt_addr_r} mono-gw.dtb; fatload usb 0:1 ${ramdisk_addr_r} live/initrd.img; booti ${kernel_addr_r} ${ramdisk_addr_r}:${filesize} ${fdt_addr_r}
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

Wait 2–4 minutes. The DTB is copied automatically.

## 4. in VyOS Live - Configure U-Boot

**Still in the live USB session** (do NOT reboot yet), configure U-Boot to auto-boot from eMMC:

```bash
sudo mount /dev/mmcblk0p3 /mnt
sudo vyos-postinstall
sudo umount /mnt
```

You should see output like:

```
Auto-detected image: 2026.03.22-0150-rolling
✓ DTB: /boot/mono-gw.dtb → /mnt/boot/2026.03.22-0150-rolling/mono-gw.dtb
✓ U-Boot: vyos_direct → boot/2026.03.22-0150-rolling/
✓ U-Boot: bootcmd → 'run vyos_direct || run recovery'
```

## 5. Reboot into eMMC

Remove the USB drive and reboot:

``` bash
reboot
```

U-Boot auto-boots into VyOS on eMMC. No manual U-Boot commands needed.

> **All subsequent reboots are automatic.** `vyos-postinstall` also runs on
> every boot via systemd service, keeping U-Boot in sync after upgrades.

## 6. Initial Config

The default config includes DHCP on all interfaces and SSH enabled.
Login `vyos` / `vyos` — SSH key authentication is preconfigured for helga.

No manual configuration needed for basic connectivity.

---

## Network Interfaces

Physical port order on the back panel:

| Port | VyOS | Type | Notes |
|------|------|------|-------|
| RJ45 Left | `eth0` | 1G SGMII | GPY115C PHY |
| RJ45 Right | `eth1` | 1G SGMII | GPY115C PHY |
| RJ45 Center | `eth2` | 1G SGMII | GPY115C PHY |
| SFP+ Left | `eth3` | 10G XFI | **10G modules only** |
| SFP+ Right | `eth4` | 10G XFI | **10G modules only** |

All interfaces are preconfigured with DHCP in the default config.

### SFP+ Port Notes

- **10G-only**: Both SFP+ cages support only 10G modules (SFP-10G-T, SFP-10G-SR, SFP-10G-LR). 1G SFP modules (SFP-GE-T, SFP-GE-SX) are **not compatible** — the LS1046A has no serdes PHY provider in mainline Linux, so `memac_supports()` only allows 10GBASE-R
- **SFP-10G-T copper modules** with RTL8261 rollball PHY take **~17 minutes** after boot to negotiate link. The interface shows `u/D` (link down) during this period — this is normal, not a failure
- **Hot-plug after failure**: If you swap an incompatible SFP for a compatible one, bounce the interface: `sudo ip link set eth3 down && sudo ip link set eth3 up`

---

## Upgrading

``` bash
add system image https://github.com/mihakralj/vyos-ls1046a-build/releases/download/<version>/vyos-<version>-LS1046A-arm64.iso
sudo vyos-postinstall
reboot
```

The `vyos-postinstall` auto-detects the latest image by version and updates U-Boot.
It also runs automatically on every boot via systemd service.

---

## Recovery

If U-Boot can't find the DTB, it drops to SPI flash recovery Linux.
Log in as `root` (no password):

```bash
mount /dev/mmcblk0p3 /mnt
IMG=$(ls /mnt/boot/ | grep -vE 'grub|efi|lost' | head -1)
cp /sys/firmware/fdt /mnt/boot/${IMG}/mono-gw.dtb
sync && umount /mnt && reboot
```

---

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| USB not detected | `usb reset`, or try USB 2.0 drive |
| Silent after "Starting kernel..." | Verify `earlycon=uart8250,mmio,0x21c0500` in bootargs |
| No networking | Wrong ISO — use only ISOs from this repo |
| `fw_setenv not found` | `sudo apt-get install u-boot-tools` |
| SFP shows `u/D` for 17 min | Normal — rollball PHY negotiation (SFP-10G-T only) |
| SFP "unsupported module" | Only 10G SFP modules work — replace with SFP-10G-SR/T/LR |
