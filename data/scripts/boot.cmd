# boot.cmd — U-Boot auto-setup script for Mono Gateway VyOS USB installer
#
# U-Boot loads and executes boot.scr from USB before anything else.
# This script:
#   1. Sets vyos_load/vyos_args/vyos (eMMC boot vars)
#   2. Sets usb_vyos_load/usb_vyos_args/usb_vyos (USB live boot vars)
#   3. Sets bootcmd (try USB → eMMC → recovery)
#   4. Saves to SPI flash (saveenv)
#   5. Boots the USB live image
#
# Compile: mkimage -C none -A arm64 -T script -d boot.cmd boot.scr
#
# CRITICAL: Every setenv line must be <500 chars. U-Boot CONFIG_SYS_CBSIZE
# on LS1046A may be as low as 512 bytes. All vars are split into load/args
# sub-commands chained via 'run' to stay under the limit.
#
# Single-quoted setenv values preserve ${vars} for runtime expansion.
# Double quotes inside single quotes are parsed when 'run' executes the var.

echo "=== VyOS LS1046A USB Installer ==="
echo ""

# --- eMMC boot vars (read /boot/vyos.env for image name) ---

setenv vyos_load 'ext4load mmc 0:3 ${load_addr} /boot/vyos.env; env import -t ${load_addr} ${filesize}; ext4load mmc 0:3 ${kernel_addr_r} /boot/${vyos_image}/vmlinuz; ext4load mmc 0:3 ${fdt_addr_r} /boot/${vyos_image}/mono-gw.dtb; ext4load mmc 0:3 ${ramdisk_addr_r} /boot/${vyos_image}/initrd.img'

setenv vyos_args 'setenv bootargs BOOT_IMAGE=/boot/${vyos_image}/vmlinuz console=ttyS0,115200 earlycon=uart8250,mmio,0x21c0500 net.ifnames=0 boot=live rootdelay=5 noautologin fsl_dpaa_fman.fsl_fm_max_frm=9600 hugepagesz=2M hugepages=512 panic=60 vyos-union=/boot/${vyos_image}'

setenv vyos 'run vyos_load; run vyos_args; booti ${kernel_addr_r} ${ramdisk_addr_r}:${filesize} ${fdt_addr_r}'

# --- USB live boot vars (FAT32 USB stick) ---
# Uses && chains so failure at any step aborts cleanly (no booti with bad addrs).
# bootcmd's || then falls through to eMMC boot.

setenv usb_vyos_load 'usb start && fatload usb 0:0 ${kernel_addr_r} live/vmlinuz && fatload usb 0:0 ${fdt_addr_r} mono-gw.dtb && fatload usb 0:0 ${ramdisk_addr_r} live/initrd.img'

setenv usb_vyos_args 'setenv bootargs BOOT_IMAGE=/live/vmlinuz console=ttyS0,115200 earlycon=uart8250,mmio,0x21c0500 boot=live live-media=/dev/sda components noeject nopersistence noautologin nonetworking union=overlay net.ifnames=0 fsl_dpaa_fman.fsl_fm_max_frm=9600 hugepagesz=2M hugepages=512 panic=60 quiet'

setenv usb_vyos 'run usb_vyos_load && run usb_vyos_args && booti ${kernel_addr_r} ${ramdisk_addr_r}:${filesize} ${fdt_addr_r}'

# --- bootcmd: USB first, then eMMC, then SPI recovery ---

setenv bootcmd 'run usb_vyos || run vyos || run recovery'

echo "Saving U-Boot environment to SPI flash..."
saveenv
echo ""
echo "U-Boot vars saved: vyos, usb_vyos, bootcmd"
echo "Remove USB after install to boot from eMMC."
echo ""

# --- Boot the USB live image now ---

echo "Booting VyOS live from USB..."
run usb_vyos
