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

## 2. Boot from USB

Insert USB, power on, press **any key** during U-Boot countdown. Paste:

```
usb start; setenv bootargs "console=ttyS0,115200 earlycon=uart8250,mmio,0x21c0500 boot=live live-media=/dev/sda1 components noeject nopersistence noautologin nonetworking union=overlay net.ifnames=0 quiet"; fatload usb 0:1 ${kernel_addr_r} live/vmlinuz; fatload usb 0:1 ${fdt_addr_r} mono-gw.dtb; fatload usb 0:1 ${ramdisk_addr_r} live/initrd.img; booti ${kernel_addr_r} ${ramdisk_addr_r}:${filesize} ${fdt_addr_r}
```

Wait 60–90 seconds for VyOS login prompt.

> **If `fatload` says "File not found":** run `fatls usb 0:1 live` — if the
> kernel has a version suffix (e.g. `vmlinuz-6.6.128-vyos`), use the full name.

## 3. Install to eMMC

Login `vyos` / `vyos`, then:

```
install image
```

Accept defaults for most prompts. When asked:
- **Console type:** `S` (serial)
- **Which disk:** `/dev/mmcblk0`

Wait 2–4 minutes. The DTB is copied automatically.

## 4. Configure U-Boot

```bash
sudo vyos-postinstall
```

This updates U-Boot to boot VyOS from eMMC on every reboot.

## 5. Reboot

```
reboot
```

Remove the USB drive. VyOS boots from eMMC automatically.

## 6. Initial Config

```
configure
set interfaces ethernet eth0 address dhcp
set service ssh
commit
save
```

---

## Network Interfaces

Physical port order, left to right on the back panel:

| Port | VyOS | Type |
|------|------|------|
| Leftmost RJ45 | `eth0` | 1G SGMII |
| Center RJ45 | `eth1` | 1G SGMII |
| Rightmost RJ45 | `eth2` | 1G SGMII |
| SFP+ slot 1 | `eth3` | 10G XFI |
| SFP+ slot 2 | `eth4` | 10G XFI |

---

## Upgrading

```
add system image https://github.com/mihakralj/vyos-ls1046a-build/releases/download/<version>/vyos-<version>-LS1046A-arm64.iso
sudo vyos-postinstall <new-image-name>
reboot
```

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
