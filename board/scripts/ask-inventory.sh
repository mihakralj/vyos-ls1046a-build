#!/bin/sh
# NXP ASK SDK inventory + hardware-offload verdict
# Compatible: sergioaguayo 25.12.2 + cvandesande 25.12.4 + any NXP ASK board
# Run as root.  Fetch:  wget -qO- URL | sh
#                      curl -s  URL | sh

# ── helpers ───────────────────────────────────────────────────────────
hextodec() { awk -- '{ printf "%d", "0x"$1 }' 2>/dev/null || printf '%d' "$(( 0x$1 ))" 2>/dev/null || echo "${1:-0}"; }
filesize() { wc -c <"$1" 2>/dev/null || echo 0; }
pidof_q() { pidof "$1" 2>/dev/null; }

# ── 1. SYSTEM ─────────────────────────────────────────────────────────
echo "═══ SYSTEM ═══"
echo "kernel  : $(uname -r)"
head -1 /proc/version 2>/dev/null
echo "dtmodel : $(cat /proc/device-tree/model 2>/dev/null | tr '\0' ' ')"
echo "cmdline : $(cat /proc/cmdline)"

# ── 2. FMAN UCODE ─────────────────────────────────────────────────────
echo; echo "═══ FMAN UCODE ═══"
dmesg 2>/dev/null | grep 'FMan-Controller code' | head -1 | sed 's/.*(ver \([^)]*\)).*/ver \1/'
ue=$(find /proc/device-tree -name 'fman-ucode*' 2>/dev/null | head -1)
[ -n "$ue" ] && { ucs=$(cat "$ue" 2>/dev/null | wc -c); echo "ucode-dt: ${ucs} bytes"; } || echo "ucode-dt: via dmesg only"

# ── 3. ASK KERNEL MODULES ─────────────────────────────────────────────
echo; echo "═══ KERNEL MODULES ═══"
for mod in cdx fci; do
  psz=$(awk -v m="$mod" '$1==m{print $2}' /proc/modules 2>/dev/null)
  if [ -n "$psz" ]; then
    ksz=0
    for k in /lib/modules/*/${mod}.ko; do [ -f "$k" ] && ksz=$(filesize "$k"); done
    printf "%-12s /proc=%7s  file=%d\n" "$mod.ko" "$psz" "${ksz:-0}"
  else
    echo "$mod.ko: NOT LOADED"
  fi
done
# auto_bridge is the crash vector — note its absence
if grep -q '^auto_bridge ' /proc/modules 2>/dev/null; then
  echo "auto_bridge.ko: LOADED (will crash with eth3/4 UP)"
else
  echo "auto_bridge.ko: absent (safe for SFP+)"
fi
# fp_netfilter — separate module or embedded?
fp=$(grep -c "fp_netfilter\|comcerto_fpp" /proc/kallsyms 2>/dev/null)
if [ -d /sys/module/fp_netfilter ]; then
  echo "fp_netfilter:  separate module ($fp kallsyms)"
elif [ "$fp" -gt 0 ]; then
  echo "fp_netfilter:  $fp symbols inside cdx.ko"
else
  echo "fp_netfilter:  NOT FOUND"
fi

# ── 4. USERSPACE ──────────────────────────────────────────────────────
echo; echo "═══ USERSPACE ═══"
for b in /usr/sbin/cmm /usr/bin/dpa_app /usr/bin/fmc; do
  [ -f "$b" ] && printf "%-12s %8d  %s\n" "$(basename $b)" "$(filesize "$b")" "$b" || echo "$b: MISSING"
done
cpid=$(pidof_q cmm)
echo "cmm-pid  : ${cpid:-NOT_RUNNING}"
[ -n "$cpid" ] && { printf "cmm-cmd  : "; tr '\0' ' ' < /proc/$cpid/cmdline 2>/dev/null; echo; }

# ── 5. LIBRARIES ──────────────────────────────────────────────────────
echo; echo "═══ LIBRARIES ═══"
for l in libfci.so libcmm.so libnetfilter_conntrack.so libnfnetlink.so; do
  f=$(find /usr/lib /lib -name "${l}*" ! -type l 2>/dev/null | head -1)
  [ -n "$f" ] && printf "%-38s %8d\n" "$(basename $f)" "$(filesize "$f")" \
              || echo "$l: MISSING"
done

# ── 6. DEVICE NODES ───────────────────────────────────────────────────
echo; echo "═══ DEVICE NODES ═══"
cnt=$(ls -1 /dev/cdx_ctrl /dev/fm0 /dev/fm0-pcd 2>/dev/null | wc -l)
echo "fm/cdx: $cnt nodes"
[ -c /dev/cdx_ctrl ] && echo "cdx_ctrl: $(ls -l /dev/cdx_ctrl 2>/dev/null | awk '{print $3,$4,$1}')"

# ── 7. CDX CONFIG ─────────────────────────────────────────────────────
echo; echo "═══ CDX CONFIG ═══"
for cfg in cdx_pcd.xml cdx_cfg.xml; do
  sz=0; loc=""
  for d in /etc "/usr/share/ask-dpa-app"; do
    [ -f "$d/$cfg" ] && { sz=$(filesize "$d/$cfg"); loc="$d/$cfg"; break; }
  done
  printf "%-40s %6d\n" "${loc:-MISSING}" $sz
done

# ── 8. CONNTRACK ──────────────────────────────────────────────────────
echo; echo "═══ CONNTRACK ═══"
ct_events=$(cat /proc/sys/net/netfilter/nf_conntrack_events 2>/dev/null)
ct_count=$(cat /proc/sys/net/netfilter/nf_conntrack_count 2>/dev/null)
ct_max=$(cat /proc/sys/net/netfilter/nf_conntrack_max 2>/dev/null)
echo "count=$ct_count  events=$ct_events  max=$ct_max"
awk 'NR>1{
  e=sprintf("%d","0x"$1); n=sprintf("%d","0x"$4); i=sprintf("%d","0x"$11)
  printf "CPU%d: entries=%-4d new=%-2d insert=%d\n",NR-2,e,n,i
}' /proc/net/stat/nf_conntrack 2>/dev/null || \
awk 'NR>1{printf "CPU%d: entries=%s new=%s insert=%s\n",NR-2,$1,$4,$11}' /proc/net/stat/nf_conntrack 2>/dev/null
ctref=$(cat /sys/module/nf_conntrack_netlink/refcnt 2>/dev/null || echo 0)
echo "ctnetlink-users: $ctref"
[ "$ctref" -eq 0 ] && echo "→ CMM not consuming ctnetlink events"

# ── 9. CMM STATE ──────────────────────────────────────────────────────
echo; echo "═══ CMM ═══"
if [ -n "$cpid" ]; then
  echo "FDs      : $(ls /proc/$cpid/fd/ 2>/dev/null | wc -l)"
  nls=$(awk -v pid="$cpid" '$3==pid{print}' /proc/net/netlink 2>/dev/null | wc -l)
  echo "netlink  : $nls sockets"
  awk -v pid="$cpid" '$3==pid{printf("           proto=%-2d  groups=0x%-6x  drops=%d\n",$2,$4,$9)}' /proc/net/netlink 2>/dev/null
  sl=$(cat /tmp/cmm-start.log 2>/dev/null | tr '\n' ' ')
  echo "log      : ${sl:-no log}"
  case "$sl" in
    *manual*) echo "→ Bridge manual mode — conntrack auto-detection OFF";;
  esac
else
  echo "cmm: NOT RUNNING"
fi

# ── 10. FCI / CDX ─────────────────────────────────────────────────────
echo; echo "═══ FCI · CDX ═══"
awk '/^Sent:|^Received:/{printf "%s ",$0}' /proc/fci 2>/dev/null; echo
echo "fppmode  : $(cat /proc/fppmode 2>/dev/null || echo 'no-entry')"

# ── 11. FQID STATS — payload counters ─────────────────────────────────
echo; echo "═══ FQID COUNTERS ═══"
hit=0
for d in pcd rx tx sa; do
  echo -n "$d: "
  for port in /proc/fqid_stats/$d/*; do
    [ -d "$port" ] || continue
    n=$(basename "$port")
    total=0
    for f in "$port"/*; do
      [ -f "$f" ] && total=$((total + $(filesize "$f")))
    done
    [ "$total" -gt 0 ] && { echo -n "$n($total B) "; hit=$((hit+1)); } || echo -n "$n "
  done
  echo
done
echo "--- non-zero ports: $hit"

# ── 12. INTERFACES ────────────────────────────────────────────────────
echo; echo "═══ INTERFACES ═══"
ip -br link 2>/dev/null | grep eth

# ── 13. PACKAGES ──────────────────────────────────────────────────────
echo; echo "═══ PACKAGES ═══"
for p in kmod-ask-cdx kmod-ask-fci kmod-ask-auto-bridge \
         ask-cmm ask-dpa-app fmc fmlib libfci kmod-nft-offload; do
  [ -f "/lib/apk/packages/${p}.list" ] && echo "  $p"
done

# ── 14. ASK DMESG ─────────────────────────────────────────────────────
echo; echo "═══ BOOT LOG ═══"
dmesg 2>/dev/null | grep -iE \
  'FMan-Controller code|FM_PCD_Init.*ext timers|fp_netfilter.*hook|cdx.*dpa_app|cdx_dpaa_ingress' \
  | head -6

# ── 15. HARDWARE OFFLOAD VERDICT ──────────────────────────────────────
echo; echo "═══ HARDWARE OFFLOAD ═══"
v=1  # assume broken

# Check PCD counters (sum all sub-files; any byte = flow installed)
any=0
for port in /proc/fqid_stats/pcd/*; do
  [ -d "$port" ] && for f in "$port"/*; do
    [ -f "$f" ] && [ "$(filesize "$f")" -gt 0 ] && any=1
  done
done

# Check conntrack new events
new=0
[ -f /proc/net/stat/nf_conntrack ] && {
  new=$(awk 'NR==2{printf "%d","0x"$4}' /proc/net/stat/nf_conntrack 2>/dev/null || echo 0)
}

# Check CMM ctnetlink subscription
ct_grp=0
[ -n "$cpid" ] && {
  ct_grp=$(awk -v pid="$cpid" '$3==pid && $2==12{print $4}' /proc/net/netlink 2>/dev/null | head -1)
  ct_grp=${ct_grp:-0}
}

[ "$any" -gt 0 ] && { echo "[PASS] PCD counter non-zero — flows in silicon"; v=0; } || echo "[FAIL] PCD counters all zero"
[ "$new" -gt 0 ] && { echo "[PASS] ctnetlink events flowing (new=$new)"; } || echo "[FAIL] ctnetlink events not generated"
[ "$ct_grp" -gt 0 ] && { echo "[PASS] CMM subscribed to ctnetlink groups=0x$(printf '%x' "$ct_grp")"; } || echo "[FAIL] CMM not subscribed to ctnetlink groups"
[ "$ctref" -gt 0 ] && { echo "[PASS] nf_conntrack_netlink has $ctref user(s)"; } || echo "[FAIL] nf_conntrack_netlink refcnt=0"

echo "---"
if [ "$any" -gt 0 ] && [ "$new" -gt 0 ]; then
  echo "VERDICT: OFFLOAD FULLY WORKING (PCD hits + ctnetlink events)"
elif [ "$any" -gt 0 ]; then
  echo "VERDICT: OFFLOAD PARTIAL — PCD has $hit port(s) with data"
  echo "  bridge/L2 offload is active, conntrack-based offload is not"
  echo "  root cause: ctnetlink events not reaching CMM ($new new, groups=$ct_grp)"
else
  echo "VERDICT: OFFLOAD NOT WORKING"
  echo "  root cause: conntrack events not reaching CMM ($new new, groups=$ct_grp)"
fi

echo; echo "═══ DONE ═══"
