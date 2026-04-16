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

setenv bootargs console=ttyS0,115200 earlycon=uart8250,mmio,0x21c0500 boot=live rootdelay=10 components noeject nopersistence noautologin nonetworking union=overlay net.ifnames=0 fsl_dpaa_fman.fsl_fm_max_frm=9600 panic=60

# --- Boot ---

echo "Booting VyOS live from USB..."
booti ${kernel_addr_r} ${ramdisk_addr_r}:${filesize} ${fdt_addr_r}
