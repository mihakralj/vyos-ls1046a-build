# Getting mainline VyOS to initially boot

What breaks when you drop a generic VyOS ARM64 ISO onto NXP/Layerscape silicon, and the exact fixes that survived contact with the hardware. Thirteen things were broken out of the box. Most of the failures are silent, which is the part that costs you an afternoon. The worst ones looked like they worked but quietly haemorrhaged performance or dropped interfaces without a trace in dmesg.


| #   | Problem                  | Root Cause                                        | Fix                                                                          |
| --- | ------------------------ | ------------------------------------------------- | ---------------------------------------------------------------------------- |
| 1   | No eMMC                  | `MMC_SDHCI_OF_ESDHC` not set                      | `=y`                                                                         |
| 2   | No network               | DPAA1 stack not enabled                           | `FSL_FMAN`, `DPAA`, `DPAA_ETH`, `BMAN`, `QMAN` `=y` + `XGMAC_MDIO`           |
| 3   | No console               | `ttyAMA0` (PL011) instead of `ttyS0` (8250)       | Patch + `earlycon` bootarg                                                   |
| 4   | CPU at 700 MHz           | `QORIQ_CPUFREQ=m` loads too late                  | `=y` + `CPU_FREQ_DEFAULT_GOV_PERFORMANCE`                                    |
| 5   | eth2 no link             | Generic PHY, no SGMII AN workaround               | `MAXLINEAR_GPHY=y` (GPY115C)                                                 |
| 6   | No SFP+                  | SFP framework + SerDes PHY missing                | `SFP=y`, `PHYLINK=y`, `PHY_FSL_LYNX_10G=y`                                   |
| 7   | Wrong port order         | DT probe order mismatched physical layout         | DTS aliases + firmware-native MAC probe order (udev rule removed 2026-03-29) |
| 8   | No auto-boot             | `install image` only updates GRUB                 | `vyos-postinstall` + `fw_setenv`                                             |
| 9   | Jumbo frames broken      | Module param used `fman` (wrong `KBUILD_MODNAME`) | `fsl_dpaa_fman.fsl_fm_max_frm=9600`                                          |
| 10  | Live mode false positive | `is_live_boot()` needs `BOOT_IMAGE=` (GRUB-only)  | Patch 009: `vyos-union=/boot/` fallback                                      |
| 11  | kexec breaks HW init     | `ln -sf /dev/null` broken by live-build           | Chroot hook + SysV script removal                                            |
| 12  | No QSPI flash access     | `CONFIG_SPI_FSL_QSPI` not set                     | `=y` + DTS partition map                                                     |
| 13  | VPP capped at 3290 MTU   | AF_XDP max frame ~3304 bytes on DPAA1             | Split-plane: VPP on SFP+ (no jumbo), kernel on RJ45 (full 9578 MTU)          |

For a full post-mortem with driver archaeology and DPAA1 architecture deep-dives, see: [plans/PORTING.md](plans/PORTING.md).
