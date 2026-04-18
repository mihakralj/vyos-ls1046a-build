# boot.cmd — U-Boot script for VyOS USB live boot on Mono Gateway
#
# U-Boot loads and executes boot.scr from USB before anything else.
# This script ONLY boots the USB live image. It does NOT modify
# any U-Boot environment variables or write to SPI flash.
# The eMMC boot setup is handled by vyos-postinstall after install.
#
# Compile: mkimage -C none -A arm64 -T script -d boot.cmd boot.scr
#
# CRITICAL: Every setenv line must be <500 chars. U-Boot CONFIG_SYS_CBSIZE
# on LS1046A may be as low as 512 bytes.

echo "=== VyOS LS1046A USB Live Boot ==="
echo ""

# --- Load kernel, DTB, initrd from FAT32 USB ---

usb start
fatload usb 0:2 ${kernel_addr_r} live/vmlinuz
fatload usb 0:2 ${fdt_addr_r} mono-gw.dtb
fatload usb 0:2 ${ramdisk_addr_r} live/initrd.img
usb stop

# --- Set bootargs for live session ---

# DO NOT add 'toram' here — it makes things WORSE on LS1046A.
#   toram triggers a single ~680 MiB sustained sequential read at
#   initramfs time, which immediately stalls the LS1046A xHCI
#   ("bad transfer trb length", "Event TRB ... no TDs queued").
#   Verified by experiment 2026-04-17: toram → reset every 30s
#   starting at T+35s, never finishes. Without toram → small lazy
#   reads succeed and boot reaches Multi-User target.
# USB live boot is intentionally a transient path. Install to eMMC
# (`install image`) as soon as you reach a usable shell — eMMC boot
# uses ext4 from mmcblk0p3 and never touches USB after boot.
#
# usbcore.autosuspend=-1: disable USB autosuspend globally.
#   LS1046A DWC3 xHCI bulk transfers stall when a device auto-suspends.
#   Without this, the kernel suspends the stick during the ~10s
#   rootdelay; on resume, the port enters a 30s reset loop.
#
# XHCI_AVOID_BEI + XHCI_TRUST_TX_LENGTH are applied by kernel patch
# 4006-xhci-plat-ls1046a-avoid-bei.patch (see drivers/usb/host/xhci-plat.c).
# The cmdline parameter xhci_hcd.quirks= is NOT consumed by xhci-plat —
# it only works on the PCI-attached xhci path — which is why it was
# ineffective for weeks. Diagnosed 2026-04-17 from a boot log showing
# quirks = 0x0000008002008410 (no AVOID_BEI bit) and the xHCI host
# dying at T+17s mid USB-storage probe.
#
# Console verbosity: earlycon enabled, `quiet` removed for full kernel
# diagnostics during USB live boot. USB boot is a one-time install path —
# verbose output helps diagnose QMan/BMan portal init, FMan MAC probe,
# and PHY initialization. Re-add `quiet` once USB boot is stable.
setenv bootargs console=ttyS0,115200 earlycon=uart8250,mmio,0x21c0500 boot=live rootdelay=10 components noeject nopersistence noautologin nonetworking union=overlay net.ifnames=0 fsl_dpaa_fman.fsl_fm_max_frm=9600 panic=60 usbcore.autosuspend=-1

# --- Boot ---

echo "Booting VyOS live from USB..."
booti ${kernel_addr_r} ${ramdisk_addr_r}:${filesize} ${fdt_addr_r}
