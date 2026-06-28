#!/bin/sh
# NXP ASK SDK inventory — compatible with sergioaguayo 25.12.2 + cvandesande 25.12.4
# Run as root. Fetch: wget -qO- URL | sh

echo "=== SYSTEM ==="
echo "kernel  : $(uname -r)"
head -1 /proc/version 2>/dev/null
echo "dtmodel : $(cat /proc/device-tree/model 2>/dev/null | tr '\0' ' ')"
echo "cmdline : $(cat /proc/cmdline)"
echo "uname -m: $(uname -m)"
echo

echo "=== FMAN UCODE ==="
dmesg 2>/dev/null|grep 'FMan-Controller code'|head -1|sed 's/.*(ver \([^)]*\)).*/ver \1/'
echo "ucode dt probe: $(find /proc/device-tree -name 'fman-ucode*' 2>/dev/null|head -1|xargs cat 2>/dev/null|tr -d '\0'|head -c 40||echo 'in-DT-only')"
echo

echo "=== ASK KERNEL MODULES ==="
grep -E '^(cdx|fci|auto_bridge|sdk_dpaa|fp_netfilter) ' /proc/modules 2>/dev/null|while read mod sz rest _ _ _ _; do
  kosz=0
  for k in /lib/modules/*/${mod}.ko; do [ -f "$k" ] && kosz=$(wc -c <"$k"); done
  printf "%-16s /proc=%-8s file=%-8d\n" "$mod" "$sz" "$kosz"
done
[ $(grep -c '^auto_bridge ' /proc/modules 2>/dev/null) -eq 0 ] && echo "auto_bridge: NOT LOADED (good for SFP+ ports)"
echo

echo "=== FP_NETFILTER ==="
fp=$(grep -c "fp_netfilter\|comcerto_fpp" /proc/kallsyms 2>/dev/null)
[ -d /sys/module/fp_netfilter ] && echo "fp_netfilter: separate module ($fp symbols)" || echo "fp_netfilter: $( [ $fp -gt 0 ] && echo "$fp symbols in cdx.ko" || echo "NOT FOUND")"
echo

echo "=== USERSPACE BINARIES ==="
for b in /usr/sbin/cmm /usr/bin/dpa_app /usr/bin/fmc;do
  [ -f "$b" ] && printf "%-20s %8d\n" "$(basename $b)" $(wc -c <"$b") || echo "$b: MISSING"
done
echo "cmm_nfct: $(readelf -s /usr/sbin/cmm 2>/dev/null | grep -c nfct)"
cpid=$(pidof cmm 2>/dev/null)
echo "cmm_pid : ${cpid:-NOT_RUNNING}"
[ -n "$cpid" ] && cat /proc/$cpid/cmdline 2>/dev/null|tr '\0' ' ' && echo
echo

echo "=== LIBRARIES ==="
for l in libfci.so libcmm.so libnetfilter_conntrack.so libnfnetlink.so;do
  f=$(find /usr/lib /lib -name "${l}*" ! -type l 2>/dev/null|head -1)
  [ -n "$f" ] && printf "%-35s %8d\n" "$(basename $f)" $(wc -c <"$f") || echo "$l: MISSING"
done
echo

echo "=== DEVICE NODES ==="
ls -1 /dev/cdx_ctrl /dev/fm0 /dev/fm0-pcd 2>/dev/null|wc -l|awk '{print "fm/cdx nodes: "$1}'
echo

echo "=== CONFIG FILES ==="
for cfg in cdx_pcd.xml cdx_cfg.xml; do
  sz=0; loc="/etc/$cfg"
  [ -f "$loc" ] && sz=$(wc -c <"$loc")
  [ $sz -eq 0 ] && [ -f "/usr/share/ask-dpa-app/$cfg" ] && { sz=$(wc -c <"/usr/share/ask-dpa-app/$cfg"); loc="/usr/share/ask-dpa-app/$cfg"; }
  printf "%-40s %6d\n" "$loc" $sz
done
echo

echo "=== FQID STATS ==="
for d in pcd rx tx sa;do
  echo -n "$d: "
  ls /proc/fqid_stats/$d/ 2>/dev/null|tr '\n' ' '; echo
done
echo

echo "=== CONNTRACK ==="
echo "count=$(cat /proc/sys/net/netfilter/nf_conntrack_count 2>/dev/null)  events=$(cat /proc/sys/net/netfilter/nf_conntrack_events 2>/dev/null)  max=$(cat /proc/sys/net/netfilter/nf_conntrack_max 2>/dev/null)"
# BusyBox awk handles hex if prefixed or auto-detected; try both
awk 'NR>1{printf("CPU%d: entries=%d new=%d insert=%d found=%d\n",NR-2,$1+0,$4+0,$11+0,$3+0)}' /proc/net/stat/nf_conntrack 2>/dev/null
echo

echo "=== CMM STATE ==="
if [ -n "$cpid" ]; then
  echo "FDs=$(ls /proc/$cpid/fd/ 2>/dev/null|wc -l)"
  nlcount=$(for fd in /proc/$cpid/fd/*; do readlink $fd 2>/dev/null; done | grep -c netlink)
  echo "netlink_sockets=$nlcount"
  echo "ctnetlink_refcnt=$(cat /sys/module/nf_conntrack_netlink/refcnt 2>/dev/null||echo 0)"
  awk -v pid="$cpid" '$3==pid{printf("  nl: protocol=%d groups=0x%x drops=%d\n",$2,$4,$9)}' /proc/net/netlink 2>/dev/null
  echo "cmm_start_log: $(cat /tmp/cmm-start.log 2>/dev/null|tr '\n' ' ')"
fi
echo

echo "=== FCI / CDX ==="
head -6 /proc/fci 2>/dev/null | grep -E "^Sent:|^Received:" | tr '\n' ' '
echo
echo "cdx_ctrl: $(ls -la /dev/cdx_ctrl 2>/dev/null|awk '{print $3,$4,$1}')"
echo "fppmode: $(cat /proc/fppmode 2>/dev/null||echo 'no-proc-entry')"
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
dmesg 2>/dev/null|grep -iE 'FMan-Controller code|FM_PCD_Init.*ext timers|fp_netfilter.*hook|cdx.*dpa_app|cdx_dpaa_ingress|cc-root'|head -8
echo

echo "=== DONE ==="
