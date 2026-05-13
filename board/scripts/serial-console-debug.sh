#!/bin/bash
# serial-console-debug.sh — one-shot diagnostic for serial-getty@ttyS0
#
# Runs once late in boot (after multi-user.target). Captures everything
# we need to debug the "first prompt doesn't accept keystrokes" /
# "no prompt at all" class of bugs:
#
#   1. Effective unit + drop-ins systemd loaded for serial-getty@ttyS0
#   2. Current service state, restart counter, PID of agetty
#   3. agetty command line + open file descriptors
#   4. /dev/ttyS0 owners (lsof / fuser)
#   5. Current termios state of /dev/ttyS0 (cooked vs raw, echo, etc.)
#   6. /proc/consoles + /proc/tty/driver/serial state
#   7. Recent journal lines for serial-getty + tail of dmesg
#   8. Snapshot of /proc/cmdline so we can confirm console= flags
#
# Everything is written to the systemd journal under unit name
# `serial-console-debug.service`, so the operator can retrieve it
# via SSH with:
#
#     journalctl -u serial-console-debug --no-pager
#
# No state is written to disk, no side-effects. Self-disables after
# one shot (Type=oneshot + RemainAfterExit=yes).

set +e   # never fail — diagnostic must complete every section

echo "==== serial-console-debug $(date -Iseconds) ===="

echo "---- 1. kernel cmdline (console= flags) ----"
cat /proc/cmdline

echo "---- 2. /proc/consoles ----"
cat /proc/consoles

echo "---- 3. /proc/tty/driver/serial ----"
cat /proc/tty/driver/serial 2>/dev/null || echo "(unavailable — needs root or driver not loaded)"

echo "---- 4. systemd unit state for serial-getty@ttyS0.service ----"
systemctl status serial-getty@ttyS0.service --no-pager --lines=0 2>&1 || true
echo "--"
systemctl show serial-getty@ttyS0.service \
  --property=ActiveState,SubState,Result,ExecMainPID,ExecMainStartTimestamp,ExecMainExitTimestamp,NRestarts,Restart,RestartUSec,StartLimitIntervalUSec,StartLimitBurst \
  --no-pager 2>&1 || true

echo "---- 5. systemd-analyze cat-config (effective merged unit) ----"
systemctl cat serial-getty@ttyS0.service --no-pager 2>&1 || true

echo "---- 6. drop-ins on disk ----"
ls -la /etc/systemd/system/serial-getty@ttyS0.service.d/ 2>&1 || true
ls -la /etc/systemd/system/serial-getty@.service.d/ 2>&1 || true

echo "---- 7. running agetty processes ----"
ps -eo pid,ppid,etime,stat,wchan,args | grep -E 'agetty|login' | grep -v grep || echo "(no agetty/login process running)"

echo "---- 8. /dev/ttyS0 owners (fuser / lsof) ----"
fuser -v /dev/ttyS0 2>&1 || true
lsof /dev/ttyS0 2>&1 || true

echo "---- 9. /dev/ttyS0 termios (stty -a) ----"
# stty needs to read termios from a fd that is the tty itself.
# We can't open /dev/ttyS0 for read here (that would race agetty),
# but we can use `stty -F /dev/ttyS0 -a` which uses TCGETS ioctl
# without claiming the line.
stty -F /dev/ttyS0 -a 2>&1 || true

echo "---- 10. journal tail for serial-getty in this boot ----"
journalctl -b -u 'serial-getty@ttyS0.service' --no-pager --lines=50 2>&1 || true

echo "---- 11. dmesg tail (last 40 lines, may show tty churn) ----"
dmesg --ctime 2>/dev/null | tail -40 || dmesg | tail -40

echo "---- 12. boot timing for serial-getty ----"
systemd-analyze blame --no-pager 2>/dev/null | grep -E 'serial-getty|getty\.target' || true

echo "==== serial-console-debug done $(date -Iseconds) ===="