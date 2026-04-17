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
# xhci_hcd.quirks=0x8400: XHCI_TRUST_TX_LENGTH (BIT(10)=0x400) +
#   XHCI_AVOID_BEI (BIT(15)=0x8000). Required on LS1046A DWC3:
#   - TRUST_TX_LENGTH suppresses "bad transfer trb length 28 in event trb"
#     by trusting the controller's reported residue length even when it
#     looks bogus (DWC3 errata: residue field is unreliable).
#   - AVOID_BEI disables Block Event Interrupt on transfer TRBs, which
#     stops the "Event TRB for slot X ep Y with no TDs queued?" warnings
#     that come from stale events arriving after the TD ring rewinds.
#   Without these, every bulk-IN command (SCSI INQUIRY, READ_CAPACITY,
#   etc.) stalls for ~30s before xHCI resets the port — boot takes
#   forever and never finishes mounting root.
#
# NOTE: Console quieting is NOT done via kernel cmdline `loglevel=` here.
# Early attempts with loglevel=4 silenced too much — all the useful early
# boot logs disappeared. Instead, console is quieted AFTER userspace
# starts via /etc/sysctl.d/99-ls1046a-quiet-console.conf (kernel.printk).
# Early boot remains verbose; only the SDK fsl_dpa "phy device not
# initialized" pr_err spam at T+62s is suppressed before agetty prints
# the login banner.
setenv bootargs console=ttyS0,115200 earlycon=uart8250,mmio,0x21c0500 boot=live rootdelay=10 components noeject nopersistence noautologin nonetworking union=overlay net.ifnames=0 fsl_dpaa_fman.fsl_fm_max_frm=9600 panic=60 usbcore.autosuspend=-1 xhci_hcd.quirks=0x8400

# --- Boot ---

echo "Booting VyOS live from USB..."
booti ${kernel_addr_r} ${ramdisk_addr_r}:${filesize} ${fdt_addr_r}
