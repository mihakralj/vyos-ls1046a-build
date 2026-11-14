#!/usr/bin/env bash
# kdump-arm.sh — arm kdump on a VyOS LS1046A DUT for one boot, so a hang or crash leaves a dump.
#
# Why: ramoops does not survive a reset on the Mono Gateway DK (firmware rewrites DDR), and the
# serial console shows nothing at a hard freeze, so kdump is the only crash record. The capture
# kernel must not probe the crashed DPAA hardware (it dies silently before root), so it boots with
# a copy of the live device tree in which QMan, BMan, their portals, FMan and fsl,dpaa are disabled.
#
# Prerequisite (once per DUT, persists in config): configure; set system option kdump; commit; save;
# then reboot (VyOS kexecs once at boot to add crashkernel=). Check: grep crashkernel /proc/cmdline.
#
# Every boot undoes this script: VyOS re-renders /etc/default/kdump-tools (dropping the --dtb
# argument, which `kdump-config load` rejects anyway) and systemd resets the hardware watchdog to
# 10 s, too short for the capture kernel. Run it again after each reboot.
#
# Usage: DUT=vyos@192.168.1.185 bin/kdump-arm.sh      Env: RIG_KEY (default ~/.ssh/vyos_key)
# Dumps land in /var/crash/<timestamp>/{dmesg.*,dump.*} on the DUT after it comes back.
set -euo pipefail
DUT=${DUT:?set DUT=user@host}
KEY=${RIG_KEY:-$HOME/.ssh/vyos_key}
SSH=(ssh -o BatchMode=yes -o ConnectTimeout=8 -o IdentitiesOnly=yes -i "$KEY")
W=$(mktemp -d); trap 'rm -rf "$W"' EXIT

"${SSH[@]}" -n "$DUT" 'grep -q crashkernel= /proc/cmdline && test -s /var/crash/kexec_cmd' ||
  { echo "ERROR: no crashkernel= reservation or kdump-tools state on $DUT; see the prerequisite above" >&2; exit 1; }

"${SSH[@]}" -n "$DUT" 'sudo cat /sys/firmware/fdt' > "$W/live.dtb"
cp "$W/live.dtb" "$W/capture.dtb"
for n in /soc/qman@1880000 /soc/bman@1890000 /soc/qman-portals-bus@500000000 \
         /soc/bman-portals-bus@508000000 /soc/fman@1a00000 /soc/fsl,dpaa; do
  fdtput -t s "$W/capture.dtb" "$n" status disabled
done
fdtput -r "$W/capture.dtb" /chosen
fdtput -c "$W/capture.dtb" /chosen

"${SSH[@]}" "$DUT" 'cat > /tmp/kdump-capture.dtb' < "$W/capture.dtb"
"${SSH[@]}" -n "$DUT" 'sudo sh -euc "
install -m 644 /tmp/kdump-capture.dtb /var/lib/kdump/capture.dtb
kexec -p -u 2>/dev/null || true
sh -c \"\$(sed \"s| --dtb=[^ ]*||\" /var/crash/kexec_cmd) --dtb=/var/lib/kdump/capture.dtb\"
busctl set-property org.freedesktop.systemd1 /org/freedesktop/systemd1 org.freedesktop.systemd1.Manager RuntimeWatchdogUSec t 120000000
sysctl -q -w kernel.softlockup_panic=1 kernel.hardlockup_panic=1 kernel.watchdog_thresh=5 kernel.panic_on_oops=1 \
  kernel.softlockup_all_cpu_backtrace=1 kernel.hardlockup_all_cpu_backtrace=1 kernel.panic_on_rcu_stall=1
echo 10 > /sys/module/rcupdate/parameters/rcu_cpu_stall_timeout
echo crash_loaded=\$(cat /sys/kernel/kexec_crash_loaded) wdt=\$(cat /sys/class/watchdog/watchdog0/timeout)s \
  watchdog_thresh=\$(sysctl -n kernel.watchdog_thresh) softlockup_panic=\$(sysctl -n kernel.softlockup_panic)
"'
