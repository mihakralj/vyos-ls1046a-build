# Architect Mode Rules (Non-Obvious Only)

- **DPAA1 is NOT a NIC driver — it's a hardware subsystem:** PAMU (IOMMU) → BMAN (buffer pools) → QMAN (work queues) → FMAN (packet engine) → DPAA_ETH (netdev). Skip any layer → silent failure, zero interfaces, no errors.
- **FMan probe chain has a hidden MDIO dependency:** MAC → memac_pcs_create → of_parse_phandle("pcsphy-handle") → fwnode_mdio_find_device(). Without `CONFIG_FSL_XGMAC_MDIO=y`, all MACs defer forever with "missing pcs". Not in Kconfig dependency chain.
- **Two boot architectures coexist on eMMC:** (1) Factory: `mmcblk0p1`=OpenWrt, `mmcblk0p2`=VyOS squashfs (`dd` image); (2) Post-install: `mmcblk0p1`=BIOS boot, 16MiB gap (our patch), `mmcblk0p2`=EFI FAT32, `mmcblk0p3`=root ext4. These are incompatible layouts — you cannot mix boot commands.
- **U-Boot memory map constraint:** fdt_addr_r (0x88000000) to ramdisk_addr_r (0x88080000) is only 512KB. Any EFI bootloader (GRUB) exceeds this. `bootefi` will OOM. Direct `booti` is the only viable boot method.
- **Kernel module ABI incompatibility:** OpenWrt runs 6.12.66, VyOS ships 6.6.128-vyos. Modules are NOT interchangeable. Fix issues via `vyos_defconfig`, not module loading.
- **NXP SDK DTB vs mainline DTB:** `mono-gw.dtb` has SDK nodes (`fsl,dpaa`, `fsl,dpa-oh`, `fman-extended-args`) that mainline kernel ignores. Mainline DPAA_ETH creates platform devices programmatically, not via OF matching on `fsl,dpaa` bus.
- **Build only modifies 2 packages:** Only `linux-kernel` and `vyos-1x` are rebuilt; everything else is upstream VyOS. Architectural changes must fit within these two packages or as patches.
- **install-image reserve-gap patch:** Our `vyos-1x-006` patch reserves 16MiB after the BIOS boot partition for bootloader payload — without this, the gap is too small and U-Boot can't find the bootloader.
