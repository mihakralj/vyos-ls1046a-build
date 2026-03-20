# Porting VyOS ARM64 to NXP LS1046A

## The Archaeology

Here is something the ARM64 embedded world does not advertise: "generic ARM64" is a polite fiction. It is a kernel configuration that covers Raspberry Pi 5, AWS Graviton, Apple M-series virtualization guests, and Qualcomm server silicon — all at once, via the miracle of `make defconfig` plus whatever the maintainer cared about last Tuesday.

It does not cover QorIQ Layerscape. Not because the drivers do not exist. They do, in mainline Linux, since 4.14. It is because nobody teaching a laptop to run VyOS needed DPAA1 Ethernet or Freescale eSDHC. So those options sit in the kernel source, untouched, waiting for someone to flip a `Kconfig` symbol.

This document is the forensic record of finding which symbols needed flipping, and why.

## The Board

**NXP QorIQ LS1046A** is a 2016-era network SoC targeting small enterprise routers and industrial gateways. Not glamorous. It is the kind of silicon that ships inside things that run for seven years in a telco closet without anyone noticing. Which is, frankly, the goal.

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

## Storage: The eSDHC Problem

The LS1046A eMMC interface is a Freescale "enhanced Secure Digital Host Controller" (eSDHC). It is compatible with SDHCI at the register level, but requires a specific OF binding driver to initialize.

The driver is `drivers/mmc/host/sdhci-of-esdhc.c`. It has been in mainline Linux since 3.6. It binds to device tree nodes with `compatible = "fsl,ls1046a-esdhc"` and related strings.

**Confirming the DMA dependency:**

The eSDHC HS200 (200 MHz high-speed) mode uses DMA transfers via the FSL enhanced DMA engine. The dependency chain:

```
sdhci-of-esdhc.ko
    depends: sdhci-pltfm.ko
    depends: sdhci.ko
    depends: mmc_core.ko
    optional: fsl-edma.ko     ← required for HS200 DMA
```

`fsl-edma.ko` was already compiled as `CONFIG_FSL_EDMA=m` in the VyOS kernel (it appears in the squashfs at `/lib/modules/6.6.128-vyos/kernel/drivers/dma/fsl-edma.ko`). Good. The gap was only the eSDHC driver itself.

**What was confirmed from the VyOS 6.6.128-vyos kernel config:**

```text
CONFIG_MMC_SDHCI=m                       ✓ present
CONFIG_MMC_SDHCI_PLTFM=m                 ✓ present
CONFIG_MMC_CQHCI=m                       ✓ present
CONFIG_FSL_EDMA=m                        ✓ present (in squashfs)

# CONFIG_MMC_SDHCI_OF_ESDHC is not set   ✗ absent
```

One symbol. Everything else was ready.

**The initrd module list also requested this driver:**

The VyOS initrd `conf/modules` explicitly lists `sdhci-of-esdhc` as a module to load at boot. The initrd was asking for a driver the kernel was not shipping. This is the embedded equivalent of ordering a meal that is not on the menu and then waiting quietly for forty minutes before anyone says anything.

## Networking: The DPAA1 Architecture

This is the larger problem, and it deserves the longer explanation.

DPAA1 (Data Path Acceleration Architecture, first generation) is NXP's packet processing framework for pre-2018 QorIQ SoCs. It is not a simple NIC driver. It is a complete hardware packet processing subsystem with its own memory manager, queue manager, and buffer manager. Ethernet becomes an application running on top of that subsystem.

The component stack, bottom to top:

```
FSL_PAMU          IOMMU/memory partitioning for DMA isolation
FSL_BMAN          Buffer Manager: hardware memory pool allocator
FSL_QMAN          Queue Manager: hardware work-queue scheduler
FSL_FMAN          Frame Manager: packet parser, classifier, policer
FSL_DPAA_ETH      Ethernet netdev layer sitting on top of FMan
```

You cannot skip any layer. Each depends on the one below. `DPAA_ETH` without `FMAN` is a referencing a null pointer. `FMAN` without `BMAN` and `QMAN` never initializes. The kernel does not crash — it just silently fails to register any network interfaces. No errors. No warnings. Five Ethernet ports simply do not exist.

**Why `FSL_FMAN=y` and not `=m`:**

The Frame Manager initializes during kernel early boot, before the root filesystem is mounted and before module loading begins. If built as a module, it loads too late: the DPAA1 Ethernet devices probe and fail against an uninitialized FMan, and the interfaces never appear. This is not a theoretical concern. It was observed on OpenWrt, where the entire DPAA1 stack is built-in (`=y`):

```
# From OpenWrt /lib/modules/6.12.66/modules.builtin:
kernel/drivers/soc/fsl/dpaa2-console.ko
kernel/drivers/mmc/host/sdhci-of-esdhc.ko
kernel/drivers/dma/fsl-edma.ko
kernel/drivers/tty/serial/8250/8250_fsl.ko
```

These are built-in. Not modules. The lesson taken from OpenWrt's working configuration.

**FMan microcode:**

The Frame Manager requires firmware: a microcode blob loaded by the driver from `mtd4` (the `fman-ucode` NOR flash partition, 1 MB). On this board, the microcode is stored in SPI flash at offset `0x400000`. The kernel loads it via the firmware loader subsystem at FMan initialization time.

The DTB correctly describes the MTD layout including `fman-ucode`. On the VyOS side, `CONFIG_FW_LOADER=y` is already enabled. The microcode will load from flash automatically on first use.

## Serial Console: PL011 vs 8250

This one is administrative rather than architectural, but it caused confusing silence at the worst times.

The LS1046A serial UART is an 8250-compatible device at MMIO address `0x21c0500`, IRQ 57, base baud 18,750,000 Hz. It registers as `ttyS0`. The earlycon probe string is:

```
earlycon=uart8250,mmio,0x21c0500
```

The VyOS ARM64 generic build, following `commit ff2a5df` in upstream `vyos-build`, changed the default console from `ttyS0` to `ttyAMA0` (PL011, ARM AMBA). This is correct for Raspberry Pi 4, QEMU virt machine, and ARM Juno. It produces zero output on LS1046A.

The kernel boots. The earlycon probe finds the right device based on the U-Boot bootargs. But after the console handoff, the live-boot initrd and all subsequent output go to `ttyAMA0`, which does not exist. Silence.

Fix is four bytes per occurrence: `ttyAMA0` becomes `ttyS0`.

## Kernel Version Delta

The working OpenWrt system runs `6.12.66`. VyOS ships `6.6.128-vyos`. Both kernels have the required DPAA1 drivers in their source trees. The version difference matters only in one respect: module ABI compatibility. Modules built for `6.6.128-vyos` cannot be borrowed from the OpenWrt `6.12.66` tree. They must be built from the same source that produced the running kernel. This is why the only correct fix is to modify `vyos_defconfig` and rebuild.

## Boot Flow

Understanding boot order prevents mistakes during deployment.

```
Power on
  NOR Flash (SPI)
    RCW + BL2 (mtd1: rcw-bl2, 1 MB)
      BL31 / ATF (EL3 runtime, PSCI)
        U-Boot (mtd2: uboot, 2 MB) [EL2]
          bootcmd = "run emmc || run vyos || run recovery"
          │
          ├─ emmc:     ext4load mmc 0:1 /boot/Image.gz
          │            ext4load mmc 0:1 /boot/mono-gateway-dk-sdk.dtb
          │            booti → Linux 6.12.66 (OpenWrt, mmcblk0p1)
          │
          ├─ vyos:     ext4load mmc 0:2 /live/vmlinuz-6.6.128-vyos
          │            ext4load mmc 0:2 /live/initrd.img-6.6.128-vyos
          │            ext4load mmc 0:2 /mono-gw.dtb
          │            booti → Linux 6.6.128-vyos (VyOS, mmcblk0p2)
          │
          └─ recovery: sf read from mtd7 (kernel-initramfs, 22 MB)
                       booti → recovery kernel
```

U-Boot key addresses for this board:

```
kernel_addr_r   = 0x82000000
fdt_addr_r      = 0x88000000
ramdisk_addr_r  = 0x88080000
kernel_comp_addr_r = 0x90000000
```

The `booti` command expects a raw ARM64 `Image` (or `.gz` compressed). VyOS ships `vmlinuz` which is a gzip-compressed `Image`. U-Boot decompresses to `kernel_comp_addr_r` if needed.

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

The `fw_printenv` tool requires `/etc/fw_env.config` pointing at `mtd3`. Without it, U-Boot environment is read-only from Linux. This is why environment changes must be made from the U-Boot console directly.

## eMMC Layout

```
mmcblk0       31,080,448 × 512 B  ≈ 29.6 GB total
mmcblk0p1        523,264 × 512 B  ≈ 511 MB  (OpenWrt root, ext4)
mmcblk0p2     30,555,136 × 512 B  ≈ 29.1 GB (VyOS target, ext4)
mmcblk0boot0      32,256 × 512 B  ≈ 32 MB   (hardware boot partition)
mmcblk0boot1      32,256 × 512 B  ≈ 32 MB   (hardware boot partition)
```

The hardware boot partitions (`mmcblk0boot0/1`) are not used by U-Boot on this board. U-Boot reads from the user area (`mmcblk0p1`).

## Device Tree

The DTB used is `mono-gw.dtb`, extracted live from the running OpenWrt system via `/sys/firmware/fdt`. This is the U-Boot-patched version (94,208 bytes) that includes the actual memory map (8 GB DDR4) applied by U-Boot before kernel handoff.

The ITB-embedded DTB (39,472 bytes) lacks the `/memory` nodes. Using it causes the kernel to see no RAM. This was confirmed experimentally. Use the live-extracted DTB.

Key DT properties confirmed:

```
compatible: "mono,gateway-dk", "fsl,ls1046a"
model:      "Mono Gateway Development Kit"
serial:     uart8250, mmio, 0x21c0500, 115200
```

## What Still Needs Work

One item remains open: the FMan microcode loading path needs validation on first VyOS boot. The microcode lives in `mtd4` on SPI flash. The kernel will attempt to load it via `request_firmware()` during FMan initialization. If the firmware request path does not include MTD devices, FMan initialization silently fails and DPAA1 interfaces do not appear.

Mitigation options:

1. Extract the microcode blob from `mtd4` on OpenWrt and embed it in the VyOS filesystem at `/lib/firmware/fsl_fman_ucode_ls1046_r1.0_106_4_18.bin` (the standard NXP firmware filename).

2. Alternatively, configure `CONFIG_EXTRA_FIRMWARE` to embed the blob directly in the kernel image.

The OpenWrt build for this board embeds the microcode at a fixed SPI flash offset and the kernel reads it via a custom DTS node rather than the generic firmware loader. VyOS will use the standard firmware loader path. Whether that path correctly traverses to the MTD device is the open question.

