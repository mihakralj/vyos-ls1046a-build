#!/bin/sh
# NXP ASK SDK inventory — paste into Discord, run as root on OpenWrt-ASK
# Output: compact version + file + state report

echo "=== SYSTEM ==="
echo "kernel  : $(uname -r)"
head -1 /proc/version 2>/dev/null
echo "dtmodel : $(cat /proc/device-tree/model 2>/dev/null | tr '\0' ' ')"
echo "cmdline : $(cat /proc/cmdline)"
echo

echo "=== FMAN UCODE ==="
dmesg 2>/dev/null|grep 'FMan-Controller code'|head -1|sed 's/.*(ver \([^)]*\)).*/ver \1/'
f=$(find /proc/device-tree -name 'fman-ucode*' 2>/dev/null|head -1)
[ -n "$f" ] && { ucode=$(cat "$f" 2>/dev/null|tr -d '\0'); echo "fman-ucode partition: present (${#ucode} bytes)"; } || echo "fman-ucode: in DT only"
echo

echo "=== ASK KERNEL MODULES ==="
grep -E '^(cdx|fci|auto_bridge|sdk_dpaa|fp_netfilter) ' /proc/modules 2>/dev/null|awk '{printf "%-16s %7s  %s\n",$1,$2,$4}'
echo

echo "=== USERSPACE BINARIES ==="
for b in /usr/sbin/cmm /usr/bin/dpa_app /usr/bin/fmc;do
  [ -f "$b" ] && printf "%-20s %8d\n" "$(basename $b)" $(wc -c <"$b") || echo "$b: MISSING"
done
echo "cmm_nfct: $(readelf -s /usr/sbin/cmm 2>/dev/null | grep -c nfct)"
echo "cmm_pid : $(pidof cmm 2>/dev/null||echo NOT_RUNNING)"
echo

echo "=== LIBRARIES ==="
for l in libfci.so libcmm.so libnetfilter_conntrack.so libnfnetlink.so;do
  f=$(find /usr/lib -name "${l}*" -not -type l 2>/dev/null|head -1)
  [ -n "$f" ] && printf "%-35s %8d\n" "$(basename $f)" $(wc -c <"$f") || echo "$l: MISSING"
done
echo

echo "=== DEVICE NODES ==="
ls -1 /dev/cdx_ctrl /dev/fm0 /dev/fm0-pcd 2>/dev/null|wc -l|awk '{print "fm/cdx nodes: "$1}'
echo

echo "=== CONFIG FILES ==="
printf "%-30s %6d\n" cdx_pcd.xml $(wc -c </etc/cdx_pcd.xml 2>/dev/null||echo 0)
printf "%-30s %6d\n" cdx_cfg.xml $(wc -c </etc/cdx_cfg.xml 2>/dev/null||echo 0)
echo

echo "=== FQID STATS ==="
for d in pcd rx tx sa;do
  echo -n "$d: "
  ls /proc/fqid_stats/$d/ 2>/dev/null|tr '\n' ' '; echo
done
echo

echo "=== CONNTRACK ==="
echo "count=$(cat /proc/sys/net/netfilter/nf_conntrack_count 2>/dev/null)  events=$(cat /proc/sys/net/netfilter/nf_conntrack_events 2>/dev/null)  max=$(cat /proc/sys/net/netfilter/nf_conntrack_max 2>/dev/null)"
awk 'NR>1{printf "CPU%d: entries=%d new=%d insert=%d found=%d\n",NR-2,$1,$4,$11,$3}' /proc/net/stat/nf_conntrack 2>/dev/null
echo

echo "=== CMM FDs ==="
p=$(pidof cmm 2>/dev/null)
[ -n "$p" ] && { echo "PID=$p FDs=$(ls /proc/$p/fd/ 2>/dev/null|wc -l)"; cat /proc/$p/cmdline 2>/dev/null|tr '\0' ' '; echo; } || echo "cmm NOT_RUNNING"
echo

echo "=== INTERFACES ==="
ip -br link 2>/dev/null|grep eth
echo

echo "=== PACKAGES (APK) ==="
for p in kmod-ask-cdx kmod-ask-fci kmod-ask-auto-bridge ask-cmm ask-dpa-app fmc fmlib libfci kmod-nft-offload;do
  f="/lib/apk/packages/${p}.list"
  [ -f "$f" ] && echo "$p" || true
done
echo

echo "=== ASK DMESG ==="
dmesg 2>/dev/null|grep -iE 'FMan-Controller code|FM_PCD_Init.*ext timers|fp_netfilter.*hook|cdx.*dpa_app|FM_PORT_PcdOpen|cc-root'|head -8
echo

echo "=== DONE ==="
