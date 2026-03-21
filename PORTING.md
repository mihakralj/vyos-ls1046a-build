# Porting VyOS ARM64 to NXP LS1046A

Technical analysis of what breaks when you put a generic VyOS ARM64 ISO on NXP Layerscape silicon, and the exact fixes applied.

## The Problem

"Generic ARM64" is a kernel configuration covering Raspberry Pi, AWS Graviton, Apple M-series guests, and Qualcomm server silicon -- via `make defconfig` plus whatever the maintainer cared about last Tuesday. It does not cover QorIQ Layerscape. Not because the drivers don't exist (they've been in mainline Linux since 4.14), but because nobody building VyOS for cloud VMs needed DPAA1 Ethernet or Freescale eSDHC. The config symbols sit in the kernel source, untouched.

Five things kill the generic ARM64 ISO on this board. All five are kernel configuration.

### 1. No eMMC

The LS1046A eMMC controller is a Freescale eSDHC (`fsl,esdhc`). The generic ARM64 `vyos_defconfig` ships with:

```text
# CONFIG_MMC_SDHCI_OF_ESDHC is not set
```

No driver, no `mmcblk0`. U-Boot loads the kernel and initrd fine -- it has its own eSDHC driver. The VyOS kernel then boots from RAM, `live-boot` searches every block device for `filesystem.squashfs`, finds nothing, and panics. Quietly.

### 2. No Networking

The LS1046A uses NXP DPAA1 (Data Path Acceleration Architecture, first generation). Five physical Ethernet ports managed by the Frame Manager and DPAA Ethernet glue. Generic VyOS ARM64 kernel:

```text
# CONFIG_FSL_FMAN is not set
# CONFIG_FSL_DPAA is not set
```

Zero interfaces. A router with no interfaces is a very expensive space heater.

### 3. Wrong Serial Console

The generic ARM64 image hardcodes `console=ttyAMA0,115200` (PL011 UART -- Raspberry Pi, QEMU virt, ARM Juno). The LS1046A speaks 8250 on `ttyS0`. You get a kernel that boots in complete silence.

### 4. CPU Stuck at 700 MHz

The upstream VyOS kernel ships `CONFIG_QORIQ_CPUFREQ=m` (module). The module loads at T+28s, but the clock framework runs `clk: Disabling unused clocks` at T+12s. By the time the cpufreq module initializes, only `cg-pll2-div2` (700 MHz) is available as a CMUX parent. The CPU is locked at **39% of maximum speed** (700 MHz instead of 1800 MHz).

---

## The Fixes

Five targeted modifications to `vyos-build`. Nothing else.

### Fix 1: Enable eSDHC Driver

The eMMC config is added to `vyos_defconfig` before building:

```text
CONFIG_MMC_SDHCI_OF_ESDHC=y
CONFIG_FSL_EDMA=y
CONFIG_DEVTMPFS_MOUNT=y
```

`CONFIG_DEVTMPFS_MOUNT=y` ensures `/dev/console` exists before init runs -- without it, the initramfs init script fails with "unable to open an initial console."

### Fix 2: Enable DPAA1 Networking Stack

The full DPAA1 stack, appended to `vyos_defconfig`:

```text
CONFIG_FSL_FMAN=y
CONFIG_FSL_DPAA=y
CONFIG_FSL_DPAA_ETH=y
CONFIG_FSL_DPAA_MACSEC=y
CONFIG_FSL_XGMAC_MDIO=y
CONFIG_PHY_FSL_LYNX_28G=y
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

### Fix 4: CPU Frequency Scaling

```text
CONFIG_QORIQ_CPUFREQ=y                          # built-in, claims PLLs before clk cleanup
CONFIG_CPU_FREQ_DEFAULT_GOV_PERFORMANCE=y        # router: always max frequency
# CONFIG_CPU_FREQ_DEFAULT_GOV_SCHEDUTIL is not set
```

Building the cpufreq driver as `=y` (built-in) ensures it registers with the clock mux before `late_initcall` disables unused clock parents. Confirmed: raid6 neonx8 jumped from 2056→4816 MB/s (2.3× improvement).

### Fix 5: Maxlinear GPY115C PHY Driver

The board uses three Maxlinear GPY115C PHYs (PHY ID `0x67C9DF10`) for the RJ45 SGMII ports. Without the proper driver, the kernel falls back to "Generic PHY" — which lacks the GPY-specific SGMII auto-negotiation re-trigger logic. The GPY2xx has a hardware design constraint where SGMII AN between PHY and MAC is only triggered on speed *change*. If the link partner's speed is unchanged after a link down/up cycle, no new in-band message flows from PHY to MAC, and the link never comes up. The Generic PHY driver cannot work around this.

```text
CONFIG_HWMON=y                          # dependency for MAXLINEAR_GPHY
CONFIG_MAXLINEAR_GPHY=y                 # Maxlinear GPY115/211/215 PHY driver (mxl-gpy.c)
```

This ensures all three RJ45 PHYs bind to `mxl-gpy` instead of `genphy`, enabling proper SGMII AN re-trigger on link events. Previously, eth2 (center RJ45, MAC `1aea000`, PHY `1afd000:01`) never established link because its initial SGMII AN failed and Generic PHY could not retry.

> **Hardware confirmed working:** OpenWrt's factory configuration had all three RJ45 ports as `br-lan` members — eth0, eth1, and eth2 all carried traffic. The NXP SDK DPAA driver (`fsl_dpa`) handles GPY115C PHY initialization differently, which is why eth2 worked under OpenWrt but not under VyOS's mainline `fsl_dpaa_eth` with Generic PHY.

---

## The Board

**NXP QorIQ LS1046A** is a 2016-era network SoC targeting small enterprise routers and industrial gateways. It ships inside things that run for seven years in a telco closet without anyone noticing.

```
CPU:        4x ARM Cortex-A72 (ARMv8-A), 1.8 GHz
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
CPU part        : 0xd08     <- Cortex-A72
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
    optional: fsl-edma.ko     <- required for HS200 DMA
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

You cannot skip any layer. Each depends on the one below. `DPAA_ETH` without `FMAN` is a null pointer reference. `FMAN` without `BMAN` and `QMAN` never initializes. The kernel does not crash -- it just silently fails to register any network interfaces. No errors. No warnings. Five Ethernet ports simply do not exist.

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

The Frame Manager requires firmware: a microcode blob loaded from `mtd4` (the `fman-ucode` NOR flash partition, 1 MB) at offset `0x400000` in SPI flash. U-Boot injects this into the DTB before kernel handoff. The kernel does NOT load FMan firmware via `request_firmware()` -- no `/lib/firmware/` files are needed.

**MDIO and PCS (the hidden dependency):**

Even with the entire DPAA1 stack enabled, Ethernet MACs will not probe without the MDIO bus driver. The probe chain is:

```
fsl_dpaa_mac (mac.c)
  -> memac_initialization (fman_memac.c)
    -> memac_pcs_create()
      -> of_parse_phandle("pcsphy-handle")
      -> lynx_pcs_create_fwnode()
        -> fwnode_mdio_find_device()   <-- needs MDIO bus registered
```

Each MAC's DTB node has a `pcsphy-handle` pointing to a PCS device on an MDIO bus. The MDIO buses (`fsl,fman-memac-mdio` compatible) are driven by `xgmac_mdio.c` (`CONFIG_FSL_XGMAC_MDIO`). Without it, nine MDIO platform devices exist but no driver claims them. `fwnode_mdio_find_device()` returns NULL, `lynx_pcs_create_fwnode()` returns `-EPROBE_DEFER`, and every MAC defers forever with `"missing pcs"` errors. The PCS devices themselves need `CONFIG_PHY_FSL_LYNX_28G` for the Lynx 28G SerDes PHY driver that handles SGMII/QSGMII/XFI link negotiation.

---

## Ethernet Interface Mapping

> ⚠️ **Physical RJ45 port order is REVERSED from DT node address order (PCB routing).**

Verified by cable-plug testing on board #308, eMMC installed boot:

| Physical Position | Type | VyOS name | MAC Address | DT Node | PHY |
|-------------------|------|-----------|-------------|---------|-----|
| Port 1 (leftmost RJ45) | SGMII | **eth1** | `E8:F6:D7:00:15:FF` | `1ae8000` | MDIO :00 |
| Port 2 (center RJ45) | SGMII | **eth2** | `E8:F6:D7:00:16:00` | `1aea000` | MDIO :01 |
| Port 3 (right RJ45) | SGMII | **eth0** | `E8:F6:D7:00:16:01` | `1ae2000` | MDIO :02 |
| SFP1 (left cage) | 10GBase-R | **eth3** | `E8:F6:D7:00:16:02` | `1af0000` | fixed-link |
| SFP2 (right cage) | 10GBase-R | **eth4** | `E8:F6:D7:00:16:03` | `1af2000` | fixed-link |

- Leftmost RJ45 = **eth1** (NOT eth0). Port 3 (rightmost) = **eth0**.
- SFP ports always show "Link is Up — 10Gbps/Full" with fixed-link, regardless of transceiver presence.
- All 5 interfaces probe at T+12.2s during kernel boot.
- The rename dance: kernel assigns eth0-4 → udev renames to e2-e6 → `net.ifnames=0` bootarg renames back to eth0-4 (final order identical to probe order).
- **eth1** (leftmost, MAC :15:FF) is the recommended management port.

> **OpenWrt uses different naming:** The [official Mono Gateway docs](https://github.com/we-are-mono/meta-mono)
> list left-to-right as eth0, eth1, eth2 — this is the NXP SDK DPAA driver (`fsl_dpa`) probe order.
> VyOS uses mainline `fsl_dpaa_eth` which probes in DT address order, producing the reversed mapping above.
> The NXP SDK also uses udev rules (`72-fsl-dpaa-persistent-networking.rules`) mapping addresses to
> `fm1-mac1` through `fm1-mac10` — `1aea000` = `fm1-mac6`.

---

## Serial Console: PL011 vs 8250

The LS1046A serial UART is an 8250-compatible device at MMIO address `0x21c0500`, IRQ 57, base baud 18,750,000 Hz. It registers as `ttyS0`. The earlycon probe string is:

```
earlycon=uart8250,mmio,0x21c0500
```

The upstream `vyos-build` changed the default console from `ttyS0` to `ttyAMA0` (PL011, ARM AMBA) -- correct for Raspberry Pi 4 and QEMU, but produces zero output on LS1046A. After the console handoff, the live-boot initrd and all subsequent output go to `ttyAMA0`, which does not exist. Silence.

**CONFIG_SERIAL_OF_PLATFORM is critical:** This config option (maps to `drivers/tty/serial/8250/8250_of.c`) enables the DT-based 8250 platform driver needed for the LS1046A's `serial@21c0500` node. Without it, `earlycon` works (direct hardware access) but `/dev/ttyS0` is never created. Init's final `exec run-init ... < ${rootmnt}/dev/console` fails → init exits → kernel panic. The config symbol `CONFIG_SERIAL_8250_OF` does NOT exist — it must be `CONFIG_SERIAL_OF_PLATFORM`.

---

## Boot Flow

```
Power on
  NOR Flash (SPI)
    RCW + BL2 (mtd1: rcw-bl2, 1 MB)
      BL31 / ATF (EL3 runtime, PSCI)
        U-Boot (mtd2: uboot, 2 MB) [EL2]
          bootcmd = "run vyos_direct || run recovery"
          |
          +-- vyos_direct: ext4load mmc 0:3 -> VyOS (mmcblk0p3)
          |                loads: vmlinuz, mono-gw.dtb, initrd.img
          |                IMPORTANT: initrd must be loaded LAST
          |                so ${filesize} is correct for booti
          |
          +-- recovery:    sf read from mtd7 -> recovery kernel
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

`fw_printenv` requires `/etc/fw_env.config` pointing at `/dev/mtd3`. Without it, U-Boot environment is read-only from Linux. Config: `/dev/mtd3 0x0 0x20000 0x20000`.

---

## eMMC Layout (After `install image`)

```
mmcblk0       ~29.6 GB total (GPT)
+-- mmcblk0p1     1 MB    BIOS Boot  (EF02)  raw, no filesystem
+-- (16 MB gap)           bootloader clearance (our patch)
+-- mmcblk0p2   256 MB    EFI System (EF00)  FAT32, GRUB (unused — bootefi broken)
+-- mmcblk0p3  29.4 GB    Linux root (8300)  ext4, VyOS squashfs + data
```

> **Factory layout (before install):** mmcblk0p1 = 511 MB OpenWrt root (ext4), mmcblk0p2 = rest empty. `install image` destroys this. No recovery back to OpenWrt without reflashing eMMC.

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

## DTB Compatibility: NXP SDK vs Mainline

The `mono-gw.dtb` was extracted from an OpenWrt build that uses the NXP
Layerscape SDK kernel. This DTB contains SDK-specific nodes that are
**not recognized** by the mainline Linux kernel's DPAA1 drivers:

**SDK-specific nodes present in `mono-gw.dtb`:**

| Node | Compatible | Issue |
|------|-----------|-------|
| `fman@1a00000` | `"fsl,fman", "simple-bus"` | Mainline uses `"fsl,fman"` only |
| DPAA bus | `"fsl,dpaa", "fsl,ls1043a-dpaa", "simple-bus"` | No mainline driver |
| `dpa-fman0-oh@2` | `"fsl,dpa-oh"` | SDK offline-port, no mainline support |
| `fman0-extended-args` | `"fsl,fman-extended-args"` | SDK extension, ignored by mainline |
| Port extended-args | `"fsl,fman-port-*-extended-args"` | SDK extension, ignored by mainline |

**Mainline kernel driver requirements (from `qoriq-fman3-0.dtsi`):**

```dts
fman0: fman@1a00000 {
    compatible = "fsl,fman";      /* NO "simple-bus" fallback */
    clocks = <&clockgen QORIQ_CLK_FMAN 0>;
    clock-names = "fmanclk";
    fsl,qman-channel-range = <0x800 0x10>;
    dma-coherent;
    /* Child nodes: muram, ports, mdio, ethernet MACs */
};
```

The mainline FMan driver (`fman.c`) matches `compatible = "fsl,fman"` and
uses `pr_debug()`/`dev_dbg()` for all output -- a successful probe produces
**zero visible dmesg messages** unless dynamic debug is enabled.

The DPAA Ethernet driver (`dpaa_eth.c`) does NOT use OF matching. It
expects `fman_mac.c` to create `"dpaa-ethernet"` platform devices
programmatically. The SDK's `"fsl,dpaa"` bus node is irrelevant to mainline.

Despite the SDK-format DTB, networking works because the FMan driver finds
the essential nodes (`fsl,fman`, MAC subnodes, MDIO buses) and ignores
the unrecognized SDK extensions.

---

## CPU Frequency Scaling

The LS1046A QorIQ clockgen provides multiple PLL sources for CPU frequency scaling. The `qoriq-cpufreq` driver reads available clock parents from `cg-cmux0` (the CPU clock mux) and populates the cpufreq frequency table.

**Clock tree (from live system):**

```
sysclk (100 MHz oscillator)
├── cg-pll0 (Platform PLL)
│   └── div2 = 300 MHz (SPI/DSPI)
├── cg-pll1 (CGA PLL1)
│   ├── div1 = 1600 MHz  ← max CPU frequency
│   ├── div2 = 800 MHz
│   ├── div3 = 533 MHz
│   └── div4 = 400 MHz
├── cg-pll2 (CGA PLL2)
│   ├── div1 = 1400 MHz  (hwaccel1)
│   ├── div2 = 700 MHz   ← minimum CPU clock
│   ├── div3 = 466 MHz
│   └── div4 = 350 MHz
└── cg-cmux0 → cg-pll1-div1 (1600 MHz) ← FIXED ✅
    ├── cpu@0 .. cpu@3
    └── cg-hwaccel0 → FMan
```

The `t1040_cmux` mux definition in `clk-qoriq.c` (used for LS1046A) allows 4 parents:

| CLKSEL | Source | Rate |
|--------|--------|------|
| 0 | CGA_PLL1 / DIV1 | 1600 MHz |
| 1 | CGA_PLL1 / DIV2 | 800 MHz |
| 2 | CGA_PLL2 / DIV1 | 1400 MHz |
| 3 | CGA_PLL2 / DIV2 | 700 MHz |

**The bug:** The upstream VyOS kernel ships `CONFIG_QORIQ_CPUFREQ=m` (module). The module loads at T+28s, but the clock framework runs `clk: Disabling unused clocks` at T+12s. By the time the cpufreq module initializes, only `cg-pll2-div2` (700 MHz) is available as a CMUX parent. The CPU is locked at **39% of maximum speed**.

**The fix:** Two kernel config changes:

```text
CONFIG_QORIQ_CPUFREQ=y                          # built-in, claims PLLs before clk cleanup
CONFIG_CPU_FREQ_DEFAULT_GOV_PERFORMANCE=y        # router: always max frequency
```

Building the cpufreq driver as `=y` (built-in) ensures it registers with the clock mux before `late_initcall` disables unused clock parents. Setting the default governor to `performance` is appropriate for a network router (no power-saving needed). Confirmed working: raid6 neonx8 jumped from 2056→4816 MB/s (2.3×).

---

## Boot Optimizations

### Services Masked in ISO

The build workflow masks unnecessary services via symlinks to `/dev/null` in `includes.chroot/etc/systemd/system/`:

| Service | Why masked | Savings |
|---------|-----------|---------|
| `kexec-load.service` | Prevents loading kexec kernel at boot | Minor |
| `kexec.service` | Prevents kexec reboot trigger | Minor |
| `acpid.service` | No ACPI on ARM64/DeviceTree | ~2s |
| `acpid.socket` | No ACPI on ARM64/DeviceTree | — |
| `acpid.path` | No ACPI on ARM64/DeviceTree | — |

### Kernel Config Optimizations

| Config | Effect | Savings |
|--------|--------|---------|
| `# CONFIG_DEBUG_PREEMPT is not set` | Suppresses `smp_processor_id()` BUG spam | ~20s |
| `CONFIG_QORIQ_CPUFREQ=y` | CPU runs at 1.8 GHz instead of 700 MHz | 2.3× throughput |

### Kexec Double-Boot (USB Live Only)

USB live boot triggers a kexec reboot after first config mount — this is normal VyOS live-boot behavior, NOT a bug. `vyos-router` itself triggers the reboot via `kexec.target` (a built-in systemd target, different from the masked `kexec.service`). The ~70s penalty is a one-time cost during initial USB install only. **Installed systems boot in ~82s with no kexec.**

### Boot Timeline (Installed System)

```
T+0.0s   Kernel start
T+0.8s   4 CPUs online, BMan/QMan portals initialized
T+12.2s  Serial driver replaces earlycon, 5 FMan MACs probed
T+12.6s  Clocks claimed, cpufreq driver registered
T+17.5s  systemd starts
T+26.8s  VyOS Router service starts
T+41.2s  NICs settled, config mounted
T+81.3s  Config migration + success, login prompt
```

---

## Kernel Version Delta

OpenWrt runs `6.12.66`. VyOS ships `6.6.128-vyos`. Both have the required DPAA1 drivers in their source trees. Module ABI is incompatible -- modules from one kernel cannot be used on the other. The only correct fix is modifying `vyos_defconfig` and rebuilding.

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
CONFIG_FSL_XGMAC_MDIO=y        # FMan MDIO bus driver (xgmac_mdio.c) -- required for PCS discovery
CONFIG_PHY_FSL_LYNX_28G=y      # Lynx 28G SerDes PHY -- PCS for SGMII/QSGMII/XFI links
CONFIG_FSL_BMAN=y               # Buffer Manager
CONFIG_FSL_QMAN=y               # Queue Manager
CONFIG_FSL_PAMU=y               # IOMMU for DMA isolation
CONFIG_HWMON=y                  # hardware monitoring (dependency for MAXLINEAR_GPHY)
CONFIG_MAXLINEAR_GPHY=y         # Maxlinear GPY115C PHY driver (mxl-gpy.c) -- SGMII AN re-trigger
CONFIG_MMC_SDHCI_OF_ESDHC=y     # eMMC controller
CONFIG_FSL_EDMA=y               # DMA engine (eSDHC HS200)
CONFIG_SERIAL_OF_PLATFORM=y     # 8250 UART device tree probe (8250_of.c)
CONFIG_MTD=y                    # MTD subsystem (SPI flash access)
CONFIG_MTD_SPI_NOR=m            # SPI NOR flash driver
CONFIG_SPI=y                    # SPI subsystem
CONFIG_SPI_FSL_DSPI=y           # Freescale DSPI controller
CONFIG_CDX_BUS=y                # CDX bus (DPAA dependency)
# CONFIG_DEBUG_PREEMPT is not set  # suppress smp_processor_id() BUG spam on Cortex-A72
CONFIG_QORIQ_CPUFREQ=y          # QorIQ CPU frequency scaling (built-in, not module)
CONFIG_CPU_FREQ_DEFAULT_GOV_PERFORMANCE=y  # router: always max frequency
# CONFIG_CPU_FREQ_DEFAULT_GOV_SCHEDUTIL is not set
```

> **Why `QORIQ_CPUFREQ=y`:** The upstream VyOS kernel ships this as `=m` (module).
> The module loads ~16s after the clock framework runs `clk: Disabling unused clocks`,
> which can result in the CPU being locked at the minimum frequency (700 MHz instead
> of 1600 MHz). Building it in ensures the cpufreq driver claims PLL clock parents
> before they are disabled as "unused."

---

## Cosmetic Boot Messages (Ignore)

| Message | Cause | Impact |
|---------|-------|--------|
| `smp_processor_id() in preemptible code: python3` | `PREEMPT_DYNAMIC` on Cortex-A72 | Suppressed by `CONFIG_DEBUG_PREEMPT=n` |
| `could not generate DUID ... failed!` | No persistent machine-id on live boot | Expected, harmless |
| `WARNING failed to get smmu node: FDT_ERR_NOTFOUND` | DTB lacks SMMU/IOMMU nodes | Harmless |
| `PCIe: no link` / `disabled` | No PCIe devices on board | Normal |
| `bridge: filtering via arp/ip/ip6tables is no longer available` | `br_netfilter` not loaded | VyOS loads it when needed |

---

## See Also

- [INSTALL.md](INSTALL.md) -- step-by-step installation guide
- [boot.efi.md](boot.efi.md) -- U-Boot reference: memory map, boot commands, hardware details
- [README.md](README.md) -- project overview
- [Mono Gateway Getting Started](https://github.com/we-are-mono/meta-mono/blob/master/gateway-development-kit/getting-started.md) -- factory setup, serial console, Recovery Linux
- [Mono Gateway Hardware Description](https://github.com/we-are-mono/meta-mono/blob/master/gateway-development-kit/hardware-description.md) -- port pinouts, GPIO, M.2, fan headers, PCB dimensions
- [NXP LS1046A DPDK/VPP Development](https://github.com/we-are-mono/meta-mono/blob/master/tutorials/development-set-up.md) -- cross-compilation for DPAA acceleration
- [NXP SDK kernel](https://github.com/nxp-qoriq/linux) -- NXP's fork with DPAA extensions (branch `lf-6.6.3-1.0.0`)
