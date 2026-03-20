# Porting VyOS ARM64 to NXP LS1046A

Technical analysis of what breaks when you put a generic VyOS ARM64 ISO on NXP Layerscape silicon, and the exact fixes applied.

## The Problem

"Generic ARM64" is a kernel configuration covering Raspberry Pi, AWS Graviton, Apple M-series guests, and Qualcomm server silicon — via `make defconfig` plus whatever the maintainer cared about last Tuesday. It does not cover QorIQ Layerscape. Not because the drivers don't exist (they've been in mainline Linux since 4.14), but because nobody building VyOS for cloud VMs needed DPAA1 Ethernet or Freescale eSDHC. The config symbols sit in the kernel source, untouched.

Three things kill the generic ARM64 ISO on this board. All three are kernel configuration.

### 1. No eMMC

The LS1046A eMMC controller is a Freescale eSDHC (`fsl,esdhc`). The generic ARM64 `vyos_defconfig` ships with:

```text
# CONFIG_MMC_SDHCI_OF_ESDHC is not set
```

No driver, no `mmcblk0`. U-Boot loads the kernel and initrd fine — it has its own eSDHC driver. The VyOS kernel then boots from RAM, `live-boot` searches every block device for `filesystem.squashfs`, finds nothing, and panics. Quietly.

### 2. No Networking

The LS1046A uses NXP DPAA1 (Data Path Acceleration Architecture, first generation). Five physical Ethernet ports managed by the Frame Manager and DPAA Ethernet glue. Generic VyOS ARM64 kernel:

```text
# CONFIG_FSL_FMAN is not set
# CONFIG_FSL_DPAA is not set
```

Zero interfaces. A router with no interfaces is a very expensive space heater.

### 3. Wrong Serial Console

The generic ARM64 image hardcodes `console=ttyAMA0,115200` (PL011 UART — Raspberry Pi, QEMU virt, ARM Juno). The LS1046A speaks 8250 on `ttyS0`. You get a kernel that boots in complete silence.

---

## The Fixes

Three targeted modifications to `vyos-build`. Nothing else.

### Fix 1: Enable eSDHC Driver

The eMMC config is added to `vyos_defconfig` before building:

```text
CONFIG_MMC_SDHCI_OF_ESDHC=y
CONFIG_FSL_EDMA=y
CONFIG_DEVTMPFS_MOUNT=y
```

`CONFIG_DEVTMPFS_MOUNT=y` ensures `/dev/console` exists before init runs — without it, the initramfs init script fails with "unable to open an initial console."

### Fix 2: Enable DPAA1 Networking Stack

The full DPAA1 stack, appended to `vyos_defconfig`:

```text
CONFIG_FSL_FMAN=y
CONFIG_FSL_DPAA=y
CONFIG_FSL_DPAA_ETH=y
CONFIG_FSL_DPAA_MACSEC=y
CONFIG_FSL_BMAN=y
CONFIG_FSL_QMAN=y
CONFIG_FSL_PAMU=y
```

All `=y` (built-in), not `=m`. The Frame Manager initializes during early boot, before the rootfs is mounted and module loading begins. If built as modules, they load too late and the interfaces never appear.

### Fix 3: Revert Console Device

```bash
sed -i 's/ttyAMA0/ttyS0/g' \
  vyos-build/data/live-build-config/hooks/live/01-live-serial.binary \
  vyos-build/data/live-build-config/includes.chroot/opt/vyatta/etc/grub/default-union-grub-entry
```

U-Boot bootargs also set `console=ttyS0,115200 earlycon=uart8250,mmio,0x21c0500`.

---

## The Board

**NXP QorIQ LS1046A** is a 2016-era network SoC targeting small enterprise routers and industrial gateways. It ships inside things that run for seven years in a telco closet without anyone noticing.

```
CPU:        4× ARM Cortex-A72 (ARMv8-A), 1.8 GHz
L1 cache:   32 KB I + 32 KB D per core
L2 cache:   1 MB shared
DRAM:       8 GB DDR4-2100 ECC (Mono Gateway DK)
SoC class:  QorIQ Layerscape (fsl,ls1046a)
DT model:   Mono Gateway Development Kit (mono,gateway-dk)
```

Verified from `/proc/cpuinfo` on the running OpenWrt system:

```
CPU implementer : 0x41
CPU architecture: 8
CPU variant     : 0x0
CPU part        : 0xd08     ← Cortex-A72
CPU revision    : 2
```

---

## Storage: The eSDHC Problem

The LS1046A eMMC interface is a Freescale "enhanced Secure Digital Host Controller" (eSDHC). It is compatible with SDHCI at the register level but requires a specific OF binding driver to initialize.

The driver is `drivers/mmc/host/sdhci-of-esdhc.c`, in mainline Linux since 3.6. It binds to device tree nodes with `compatible = "fsl,ls1046a-esdhc"`.

**DMA dependency chain:**

```
sdhci-of-esdhc.ko
    depends: sdhci-pltfm.ko
    depends: sdhci.ko
    depends: mmc_core.ko
    optional: fsl-edma.ko     ← required for HS200 DMA
```

The VyOS initrd `conf/modules` explicitly lists `sdhci-of-esdhc` as a module to load at boot. The initrd was asking for a driver the kernel was not shipping.

---

## Networking: The DPAA1 Architecture

DPAA1 is not a simple NIC driver. It is a complete hardware packet processing subsystem with its own memory manager, queue manager, and buffer manager. Ethernet becomes an application running on top of that subsystem.

The component stack, bottom to top:

```
FSL_PAMU          IOMMU/memory partitioning for DMA isolation
FSL_BMAN          Buffer Manager: hardware memory pool allocator
FSL_QMAN          Queue Manager: hardware work-queue scheduler
FSL_FMAN          Frame Manager: packet parser, classifier, policer
FSL_DPAA_ETH      Ethernet netdev layer sitting on top of FMan
```

You cannot skip any layer. Each depends on the one below. `DPAA_ETH` without `FMAN` is a null pointer reference. `FMAN` without `BMAN` and `QMAN` never initializes. The kernel does not crash — it just silently fails to register any network interfaces. No errors. No warnings. Five Ethernet ports simply do not exist.

**Why `=y` and not `=m`:**

The Frame Manager initializes during kernel early boot, before the root filesystem is mounted. If built as a module, it loads too late: the DPAA1 Ethernet devices probe against an uninitialized FMan, and the interfaces never appear. This was confirmed by OpenWrt's working configuration where the entire DPAA1 stack is built-in:

```
# From OpenWrt /lib/modules/6.12.66/modules.builtin:
kernel/drivers/soc/fsl/dpaa2-console.ko
kernel/drivers/mmc/host/sdhci-of-esdhc.ko
kernel/drivers/dma/fsl-edma.ko
kernel/drivers/tty/serial/8250/8250_fsl.ko
```

**FMan microcode:**

The Frame Manager requires firmware: a microcode blob loaded from `mtd4` (the `fman-ucode` NOR flash partition, 1 MB) at offset `0x400000` in SPI flash. The DTB correctly describes the MTD layout including `fman-ucode`. On the VyOS side, `CONFIG_FW_LOADER=y` is already enabled — the microcode loads from flash automatically.

---

## Serial Console: PL011 vs 8250

The LS1046A serial UART is an 8250-compatible device at MMIO address `0x21c0500`, IRQ 57, base baud 18,750,000 Hz. It registers as `ttyS0`. The earlycon probe string is:

```
earlycon=uart8250,mmio,0x21c0500
```

The upstream `vyos-build` changed the default console from `ttyS0` to `ttyAMA0` (PL011, ARM AMBA) — correct for Raspberry Pi 4 and QEMU, but produces zero output on LS1046A. After the console handoff, the live-boot initrd and all subsequent output go to `ttyAMA0`, which does not exist. Silence.

---

## Boot Flow

```
Power on
  NOR Flash (SPI)
    RCW + BL2 (mtd1: rcw-bl2, 1 MB)
      BL31 / ATF (EL3 runtime, PSCI)
        U-Boot (mtd2: uboot, 2 MB) [EL2]
          bootcmd = "run emmc || run vyos || run recovery"
          │
          ├─ emmc:     ext4load mmc 0:1 → OpenWrt (mmcblk0p1)
          │
          ├─ vyos:     ext4load mmc 0:2 → VyOS (mmcblk0p2)
          │            loads: vmlinuz, mono-gw.dtb, initrd.img
          │            IMPORTANT: initrd must be loaded LAST
          │            so ${filesize} is correct for booti
          │
          └─ recovery: sf read from mtd7 → recovery kernel
```

**U-Boot `${filesize}` gotcha:** Each `ext4load` overwrites the `${filesize}` variable. The `booti` command uses `${ramdisk_addr_r}:${filesize}` to tell the kernel the initrd size. If DTB is loaded after initrd, `${filesize}` = DTB size (94KB) instead of initrd size (~33MB), causing "ZSTD-compressed data is truncated" kernel panic.

U-Boot key addresses for this board:

```
kernel_addr_r   = 0x82000000
fdt_addr_r      = 0x88000000
ramdisk_addr_r  = 0x88080000
kernel_comp_addr_r = 0x90000000
```

---

## MTD Flash Layout

```
mtd0  flash           64 MB  (full NOR flash)
mtd1  rcw-bl2          1 MB  ARM Trusted Firmware stage 1
mtd2  uboot            2 MB  U-Boot
mtd3  uboot-env        1 MB  fw_printenv/setenv storage
mtd4  fman-ucode       1 MB  Frame Manager microcode (required for DPAA1)
mtd5  recovery-dtb     1 MB  DTB for recovery boot
mtd6  backup           4 MB  Unused
mtd7  kernel-initramfs 22 MB Recovery kernel+initramfs (fallback)
mtd8  unallocated      32 MB
```

`fw_printenv` requires `/etc/fw_env.config` pointing at `mtd3`. Without it, U-Boot environment is read-only from Linux.

---

## eMMC Layout

```
mmcblk0       ~29.6 GB total
├─ mmcblk0p1  ~511 MB   OpenWrt root (ext4) — factory OS
├─ mmcblk0p2  ~29.1 GB  VyOS (ext4)
├─ mmcblk0boot0  32 MB  hardware boot partition (unused)
└─ mmcblk0boot1  32 MB  hardware boot partition (unused)
```

The hardware boot partitions are not used by U-Boot on this board.

---

## Device Tree

The DTB used is `mono-gw.dtb`, extracted live from the running OpenWrt system via `/sys/firmware/fdt`. This is the U-Boot-patched version (94,208 bytes) that includes the actual memory map (8 GB DDR4) applied by U-Boot before kernel handoff.

The ITB-embedded DTB (39,472 bytes) lacks the `/memory` nodes. Using it causes the kernel to see no RAM. This was confirmed experimentally. Use the live-extracted DTB.

Key DT properties:

```
compatible: "mono,gateway-dk", "fsl,ls1046a"
model:      "Mono Gateway Development Kit"
serial:     uart8250, mmio, 0x21c0500, 115200
```

---

## Kernel Version Delta

OpenWrt runs `6.12.66`. VyOS ships `6.6.128-vyos`. Both have the required DPAA1 drivers in their source trees. Module ABI is incompatible — modules from one kernel cannot be used on the other. The only correct fix is modifying `vyos_defconfig` and rebuilding.

---

## Kernel Config Additions

Complete list of config options appended to `vyos_defconfig`:

```text
# LS1046A / NXP Layerscape DPAA1 (Mono Gateway DK)
CONFIG_DEVTMPFS_MOUNT=y         # auto-mount /dev before init
CONFIG_FSL_FMAN=y               # Frame Manager (packet processing)
CONFIG_FSL_DPAA=y               # DPAA1 framework
CONFIG_FSL_DPAA_ETH=y           # DPAA1 Ethernet driver
CONFIG_FSL_DPAA_MACSEC=y        # MACsec offload
CONFIG_FSL_BMAN=y               # Buffer Manager
CONFIG_FSL_QMAN=y               # Queue Manager
CONFIG_FSL_PAMU=y               # IOMMU for DMA isolation
CONFIG_MMC_SDHCI_OF_ESDHC=y     # eMMC controller
CONFIG_FSL_EDMA=y               # DMA engine (eSDHC HS200)
CONFIG_SERIAL_OF_PLATFORM=y     # 8250 UART device tree probe (8250_of.c)
CONFIG_MTD=y                    # MTD subsystem (SPI flash access)
CONFIG_MTD_SPI_NOR=m            # SPI NOR flash driver
CONFIG_SPI=y                    # SPI subsystem
CONFIG_SPI_FSL_DSPI=y           # Freescale DSPI controller
CONFIG_CDX_BUS=y                # CDX bus (DPAA dependency)
```

---

## See Also

- [INSTALL.md](INSTALL.md) — step-by-step installation guide
- [README.md](README.md) — project overview
- [Mono Gateway Getting Started](https://github.com/ryneches/mono-gateway-docs/blob/master/gateway-development-kit/getting-started.md) — factory setup, serial console, Recovery Linux
