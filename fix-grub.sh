#!/bin/bash
set -ex

# Remount if needed
mountpoint -q /mnt/efi  || mount /dev/mmcblk0p2 /mnt/efi
mountpoint -q /mnt/root || mount /dev/mmcblk0p3 /mnt/root

CFG_D=/mnt/root/boot/grub/grub.cfg.d

# 1. Fix default console: tty -> ttyS
sed -i 's/set console_type="tty"/set console_type="ttyS"/' "$CFG_D/20-vyos-defaults-autoload.cfg"
echo "--- console_type ---"
grep console_type "$CFG_D/20-vyos-defaults-autoload.cfg"

# 2. Fix ARM64 serial remapping: ttyAMA -> ttyS
sed -i 's/set serial_console="ttyAMA"/set serial_console="ttyS"/' "$CFG_D/50-vyos-options.cfg"
echo "--- serial_console ---"
grep serial_console "$CFG_D/50-vyos-options.cfg"

# 3. Add earlycon to boot entry (only if not already there)
VER_CFG="$CFG_D/vyos-versions/2026.03.20-2209-rolling.cfg"
if ! grep -q earlycon "$VER_CFG"; then
    sed -i 's|set boot_opts="boot=live|set boot_opts="earlycon=uart8250,mmio,0x21c0500 boot=live|' "$VER_CFG"
fi
echo "--- boot_opts ---"
grep boot_opts "$VER_CFG" | head -3

# 4. Copy mono-gw.dtb to EFI partition root (for bootefi DTB arg)
cp /usr/lib/live/mount/medium/mono-gw.dtb /mnt/efi/mono-gw.dtb
echo "--- EFI DTB ---"
ls -la /mnt/efi/mono-gw.dtb

# 5. Copy mono-gw.dtb into boot image dir (for booti direct-load fallback)
cp /usr/lib/live/mount/medium/mono-gw.dtb /mnt/root/boot/2026.03.20-2209-rolling/mono-gw.dtb
echo "--- boot image DTB ---"
ls -la /mnt/root/boot/2026.03.20-2209-rolling/mono-gw.dtb

sync
echo "=== ALL DONE ==="
