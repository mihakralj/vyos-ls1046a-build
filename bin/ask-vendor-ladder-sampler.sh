#!/bin/sh
# ask-vendor-ladder-sampler.sh: busybox/ash port of bin/ask2-ladder-sampler.py for the vendor (ask-flowtable/cdx)
# DUT, which has no python3. One line per second on the DUT until killed; run as root (copied by testrig-flow-ladder.sh).
#
# Columns match bin/ask2-ladder-sampler.py: epoch phase offloaded_flows cpu_busy_pct running blocked
# kpath_pps ct_entries ct_inserts_per_s load1 kworkers
#   offloaded_flows   `ask-flowtable status` backend.entries / 2 (directions -> bidirectional flows)
#   cpu_busy_pct      all-CPU busy share of the last second (/proc/stat)
#   running           runnable tasks (/proc/loadavg field 4, before the "/")
#   blocked           tasks with STAT starting "D" (busybox ps STAT column)
#   kpath_pps         packets the kernel network stack processed (/proc/net/softnet_stat hex column 1, summed)
#   ct_entries        nf_conntrack_count
#   ct_inserts_per_s  conntrack insertions per second (/proc/net/stat/nf_conntrack "insert" column, summed over CPUs)
#   load1             1-minute load average (/proc/loadavg field 1)
#   kworkers          kernel worker threads alive (kworker/*)
set -u

# busybox awk has no strtonum() and busybox ash has no base#value arithmetic; use printf %d 0xHEX instead.
cpu_fields() { awk '/^cpu /{print $2+$3+$4+$7+$8, $2+$3+$4+$5+$6+$7+$8}' /proc/stat; }
kpath_total() {
  s=0
  while read -r h _; do s=$((s + $(printf '%d' "0x$h"))); done < /proc/net/softnet_stat
  echo "$s"
}
ct_inserts_total() {
  col=$(awk '{for(i=1;i<=NF;i++) if($i=="insert") print i; exit}' /proc/net/stat/nf_conntrack)
  s=0; n=0
  while read -r line; do
    n=$((n + 1))
    [ "$n" -eq 1 ] && continue
    v=$(echo "$line" | awk -v c="$col" '{print $c}')
    [ -n "$v" ] && s=$((s + $(printf '%d' "0x$v")))
  done < /proc/net/stat/nf_conntrack
  echo "$s"
}
blocked_count() { ps | awk '$4 ~ /^D/{n++} END{print n+0}'; }
kworkers_count() { ps | awk '$0 ~ /\[kworker\//{n++} END{print n+0}'; }
flows_now() {
  e=$(ask-flowtable status 2>/dev/null | jq -r '.backend.entries // 0' 2>/dev/null || echo 0)
  echo $(( ${e:-0} / 2 ))
}

set -- $(cpu_fields); pb=$1 pt=$2
pk=$(kpath_total); pi=$(ct_inserts_total)
while true; do
  sleep 1
  set -- $(cpu_fields); b=$1 t=$2
  k=$(kpath_total); i=$(ct_inserts_total)
  flows=$(flows_now)
  phase=$(cat /tmp/ladder-phase 2>/dev/null); [ -n "$phase" ] || phase="-"
  la=$(cat /proc/loadavg)
  load1=$(echo "$la" | awk '{print $1}')
  running=$(echo "$la" | awk '{split($4,a,"/"); print a[1]}')
  blocked=$(blocked_count)
  nkw=$(kworkers_count)
  ct=$(cat /proc/sys/net/netfilter/nf_conntrack_count 2>/dev/null || echo 0)
  dt=$((t - pt)); [ "$dt" -gt 0 ] || dt=1
  busy=$(( 100 * (b - pb) / dt ))
  echo "$(date +%s) $phase $flows $busy $running $blocked $((k - pk)) $ct $((i - pi)) $load1 $nkw"
  pb=$b pt=$t pk=$k pi=$i
done
