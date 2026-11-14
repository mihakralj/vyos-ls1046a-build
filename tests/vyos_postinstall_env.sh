#!/usr/bin/env bash
# vyos_postinstall_env.sh — check that board/scripts/vyos-postinstall carries the VyOS-managed kernel options
# (the image's GRUB boot_opts minus BOOT_OPTS_STEM) into /boot/vyos.env as vyos_extra_args, and that the U-Boot
# vyos_boot script appends them. Without that, U-Boot boots a kernel without crashkernel/isolcpus/hugepages and
# system_option.py kexecs on every boot. Run: tests/vyos_postinstall_env.sh
set -eu
HERE=$(cd "$(dirname "$0")/.." && pwd)
T=$(mktemp -d); trap 'rm -rf "$T"' EXIT
img=2026.11.12-0524-rolling
mkdir -p "$T/boot/$img" "$T/boot/grub/grub.cfg.d/vyos-versions"
cat > "$T/boot/grub/grub.cfg.d/vyos-versions/$img.cfg" <<EOF
menuentry "$img" --id x {
    set boot_opts="boot=live rootdelay=5 noautologin net.ifnames=0 biosdevname=0 vyos-union=/boot/$img panic=60 crashkernel=1G-8G:512M,8G-64G:768M,64G-:1G isolcpus=2-3 hugepagesz=2M hugepages=512"
    linux "/boot/$img/vmlinuz" \${boot_opts}
}
EOF
# shellcheck source=/dev/null
. "$HERE/board/scripts/vyos-postinstall"
write_vyos_env "$img" "$T" >/dev/null
want="vyos_image=$img
vyos_extra_args=panic=60 crashkernel=1G-8G:512M,8G-64G:768M,64G-:1G isolcpus=2-3 hugepagesz=2M hugepages=512"
got=$(cat "$T/boot/vyos.env")
[ "$got" = "$want" ] || { printf 'FAIL vyos.env\n--- want\n%s\n--- got\n%s\n' "$want" "$got"; exit 1; }
# an image without a GRUB entry still gets a plain vyos_image line
img2=2026.11.12-0433-rolling; mkdir -p "$T/boot/$img2"
write_vyos_env "$img2" "$T" >/dev/null
[ "$(cat "$T/boot/vyos.env")" = "vyos_image=$img2" ] || { echo "FAIL vyos.env without GRUB entry: $(cat "$T/boot/vyos.env")"; exit 1; }
grep -qF 'setenv bootargs "${bootargs} ${vyos_extra_args}"; booti' "$HERE/board/scripts/vyos-postinstall" ||
  { echo "FAIL vyos_boot does not append vyos_extra_args"; exit 1; }
echo PASS
