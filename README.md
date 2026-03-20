[![VyOS LS1046A build](https://github.com/mihakralj/vyos-ls1046a-build/actions/workflows/auto-build.yml/badge.svg)](https://github.com/mihakralj/vyos-ls1046a-build/actions/workflows/auto-build.yml)

# VyOS ARM64 for NXP LS1046A

This is a fork of [huihuimoe/vyos-arm64-build](https://github.com/huihuimoe/vyos-arm64-build), patched to survive contact with real NXP Layerscape silicon.

Stock VyOS ARM64 ISO boots fine on Proxmox, Hetzner, and the usual cloud furniture. Drop it on an LS1046A and it chokes quietly: no eMMC, no network, wrong serial device. The kernel strains against the hardware and finds nothing. This repo fixes that.

## Target Hardware

**NXP LS1046A** (QorIQ Layerscape, `fsl,ls1046a`)

Validated on: Mono Gateway Development Kit (`mono,gateway-dk`)

| Component | Spec |
|-----------|------|
| CPU | 4× Cortex-A72 @ 1.8 GHz |
| Memory | 8 GB DDR4 ECC |
| Storage | eMMC via Freescale eSDHC controller |
| Ethernet | 5× DPAA1/FMan MEMAC (eth0–eth4) |
| Serial | 8250-compatible UART at `0x21c0500`, 115200 baud |
| Boot | U-Boot → ext4 on mmcblk0p1 |

## The Problem, Exactly

Three things kill the generic ARM64 ISO on this board. All three are kernel configuration.

**1. No eMMC.** The LS1046A eMMC controller is a Freescale eSDHC (`fsl,esdhc`). The generic ARM64 `vyos_defconfig` ships with:

```text
# CONFIG_MMC_SDHCI_OF_ESDHC is not set
```

No driver, no `mmcblk0`. U-Boot loads the kernel and initrd fine — it has its own eSDHC driver. The VyOS kernel then boots from RAM, `live-boot` searches every block device for `filesystem.squashfs`, finds nothing, and panics. Quietly. At 3 AM.

**2. No networking.** The LS1046A uses NXP DPAA1 (Data Path Acceleration Architecture, first generation). Five physical Ethernet ports (eth0–eth4) are managed by the Frame Manager (`fsl-fman`) and the DPAA Ethernet glue layer. Generic VyOS ARM64 kernel:

```text
# CONFIG_FSL_FMAN is not set
# CONFIG_FSL_DPAA is not set
```

Zero interfaces. A router with no interfaces is a very expensive space heater.

**3. Wrong serial console.** The generic ARM64 image was built for PL011 UART (Raspberry Pi, ARM Juno, etc.). Boot parameters hardcode `console=ttyAMA0,115200`. The LS1046A speaks 8250 on `ttyS0`. You get a kernel that boots in complete silence, which is either Zen or a bug, depending on your temperament.

## What This Repo Changes

Three targeted modifications to `vyos-build`. Nothing else. Surgical.

### Fix 1: Re-enable the kernel config patch

`data/vyos-build-001-kernel_config.patch` (already in the upstream repo, authored by the upstream maintainer) adds `CONFIG_MMC_SDHCI_OF_ESDHC=m`. It was commented out in the workflow. Uncommented.

```yaml
# Before:
#patch --no-backup-if-mismatch -p1 -d vyos-build < data/vyos-build-001-kernel_config.patch

# After:
patch --no-backup-if-mismatch -p1 -d vyos-build < data/vyos-build-001-kernel_config.patch
```

This alone unblocks eMMC. The driver is present in the kernel source; the config was simply never flipped.

### Fix 2: Inject DPAA1/FMan networking config

After the existing patch runs, we append LS1046A-specific config to `vyos_defconfig` before the kernel build. The `make olddefconfig` step then resolves all transitive dependencies automatically.

```bash
printf '%s\n' \
  'CONFIG_FSL_FMAN=y' \
  'CONFIG_FSL_DPAA=y' \
  'CONFIG_FSL_DPAA_ETH=y' \
  'CONFIG_FSL_BMAN=y' \
  'CONFIG_FSL_QMAN=y' \
  'CONFIG_FSL_PAMU=y' \
  'CONFIG_SERIAL_8250_OF=y' \
  >> "$DEFCONFIG"
```

`FSL_FMAN=y` is mandatory (not `=m`) because the Frame Manager initializes before the rootfs is available and module loading is possible. `FSL_BMAN` and `FSL_QMAN` are the Buffer Manager and Queue Manager: DPAA1 Ethernet cannot function without both. `FSL_PAMU` is the hardware IOMMU that DPAA1 uses for DMA isolation.

### Fix 3: Revert the console device

The upstream build introduced `console=ttyAMA0,115200` for generic ARM boards. We revert it to `ttyS0` for this board.

```bash
sed -i 's/ttyAMA0/ttyS0/g' \
  vyos-build/data/live-build-config/hooks/live/01-live-serial.binary
sed -i 's/ttyAMA0/ttyS0/g' \
  vyos-build/data/live-build-config/includes.chroot/opt/vyatta/etc/grub/default-union-grub-entry
```

U-Boot bootargs also set the correct console: `console=ttyS0,115200 earlycon=uart8250,mmio,0x21c0500`.

## Build

Builds run on GitHub's native ARM64 runner (`ubuntu-24.04-arm`). Trigger manually:

```bash
gh workflow run auto-build.yml \
  --repo mihakralj/vyos-ls1046a-build \
  --ref master \
  --field build_version="$(date -u +%Y.%m.%d)-ls1046a-rolling"
```

Or push a commit to `master`. Build takes 45–75 minutes. Result publishes as a GitHub Release.

## Deploy to LS1046A

### Prepare eMMC partition 2

From the running OpenWrt on the device (`root@192.168.1.234`):

```bash
mke2fs -t ext4 /dev/mmcblk0p2
mount /dev/mmcblk0p2 /mnt
mkdir -p /mnt/live
```

### Copy VyOS files from build machine

Download the release ISO, extract, then transfer:

```bash
# Extract ISO contents
ISO=/path/to/vyos-2026.03.20-ls1046a-rolling-arm64.iso
mkdir /tmp/vyos-iso && sudo mount -o loop "$ISO" /tmp/vyos-iso

# Push to device
scp -i ~/.ssh/dropbear_key /tmp/vyos-iso/live/vmlinuz-6.6.*-vyos root@192.168.1.234:/mnt/live/
scp -i ~/.ssh/dropbear_key /tmp/vyos-iso/live/initrd.img-6.6.*-vyos root@192.168.1.234:/mnt/live/
scp -i ~/.ssh/dropbear_key /tmp/vyos-iso/live/filesystem.squashfs root@192.168.1.234:/mnt/live/
scp -i ~/.ssh/dropbear_key /tmp/vyos-iso/mono-gw.dtb root@192.168.1.234:/mnt/
```

### Configure U-Boot

Interrupt U-Boot during the 5-second boot delay. Then:

```
setenv vyos 'setenv bootargs "console=ttyS0,115200 earlycon=uart8250,mmio,0x21c0500 boot=live components noeject nopersistence noautologin nonetworking union=overlay net.ifnames=0"; ext4load mmc 0:2 ${kernel_addr_r} /live/vmlinuz-6.6.128-vyos; ext4load mmc 0:2 ${ramdisk_addr_r} /live/initrd.img-6.6.128-vyos; ext4load mmc 0:2 ${fdt_addr_r} /mono-gw.dtb; booti ${kernel_addr_r} ${ramdisk_addr_r}:${filesize} ${fdt_addr_r}'

setenv bootcmd 'run emmc || run vyos || run recovery'
saveenv
```

OpenWrt on mmcblk0p1 remains untouched and boots first. `run vyos` as fallback.

## Partition Layout

```
mmcblk0p1  ~512 MB  ext4   OpenWrt root (active, untouched)
mmcblk0p2  ~29 GB   ext4   VyOS target
```

U-Boot's `emmc` variable always boots mmcblk0p1. The `vyos` variable boots mmcblk0p2. Both coexist.

## Default Credentials

Live boot (installer mode): `vyos` / `vyos`

The included `config.boot.dhcp` enables DHCP on eth0 and SSH with `vyos` / `a_strong-p@ssword` as a recovery fallback.

## See Also

[PORTING.md](./PORTING.md): Full technical analysis of the LS1046A porting work — kernel driver archaeology, DPAA1 architecture, module dependency chains, and boot flow dissection.

