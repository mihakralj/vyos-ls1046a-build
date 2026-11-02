#!/usr/bin/env bash
# testrig-wgfp-soak.sh — WireGuard fast path robustness gates (plans/ASK2-WIREGUARD-FASTPATH-PLAN.md, A3).
# Usage: bin/testrig-wgfp-soak.sh overload|soak
#   overload  P2a lab seal unpaced (sinks saturated: ~850k pkt/s offered, ~500k sealed) while dell1 pings dell2
#             through the same ingress port (eth3). Without sink tail drop the backlog drains eth3's buffer pool and
#             the unrelated ping loses (2026-11-02 baseline: 34% loss). Gate: ping loss <= 1%, eth3 bpool recovers.
#             Leaves lab records in the ehash tables: reboot afterwards.
#   soak      4 automatic TCP flows for DUR (default 600 s) through a live tunnel (KEEP=1 bin/testrig-wireguard.sh
#             setup wg-v4). Samples eth3/eth4 bpool, sink frm_cnt and xmit_fail every 30 s; checks pcd-snapshot
#             before binding vs after unbinding. Gate: bpool back within 64 of the start, xmit_fail 0, no kernel
#             errors, fe_flow empty, pcd-snapshot diff clean.
set -u
MODE=${1:-}; DUR=${DUR:-600}
SSHO="-o BatchMode=yes -o ConnectTimeout=8"
dut(){ ssh -n $SSHO -i $HOME/.ssh/vyos_key vyos@192.168.1.185 "$1"; }
D1="ssh -n $SSHO admin@192.168.1.112"; D2="ssh -n $SSHO admin@192.168.1.113"
WP=/sys/module/wireguard/parameters/wgfp_peer; DBG=/sys/kernel/debug/dpaa_wgfp
bp(){ dut "sudo /usr/sbin/ethtool -S $1 | sed -n 's/.*bpool \[TOTAL\]: //p'"; }
fails=0; ok(){ echo "  PASS: $*"; }; bad(){ echo "  FAIL: $*"; fails=$((fails+1)); }
dut 'sudo dmesg -C'

case $MODE in
overload)
  b0=$(bp eth3)
  (PPS_LIST=0 SECS=20 TBLS=3 timeout 240 "$(dirname "$0")/testrig-wgfp-p2a.sh" > /tmp/wgfp-overload-p2a.log 2>&1 &)
  for i in $(seq 1 60); do grep -q 'inserted 16 records' /tmp/wgfp-overload-p2a.log 2>/dev/null && break; sleep 1; done
  sleep 7
  b1=$(bp eth3)
  out=$($D1 'ping -c 100 -i 0.1 -W 1 10.99.2.113 | grep -o "[0-9.]*% packet loss"')
  b2=$(bp eth3)
  wait; sleep 25; b3=$(bp eth3)
  dut "sudo cat $DBG/stats | head -5"
  echo "  side-flow ping: $out; eth3 bpool start $b0, saturated $b1 / $b2, after $b3"
  loss=${out%%%*}
  awk -v l="${loss:-100}" 'BEGIN{exit !(l <= 1)}' && ok "unrelated traffic on the ingress port unaffected" || bad "side-flow loss ${loss}%"
  [ $(( ${b3:-0} - ${b0:-0} )) -ge -64 ] && ok "eth3 buffer pool recovered" || bad "eth3 bpool $b0 -> $b3"
  echo "  NOTE: reboot .185 (lab records stay in the ehash tables)"
  ;;
soak)
  dut 'sudo pcd-snapshot capture -o /tmp/pcd-before.json >/dev/null' || bad "pcd-snapshot capture"
  e3=$(bp eth3); e4=$(bp eth4)
  dut "echo wg0 | sudo tee $WP >/dev/null" || { echo "ABORT: wgfp_peer"; exit 1; }
  $D2 'for p in 5241 5242 5243 5244; do sudo pkill -f "^iperf3 -s -p $p"; iperf3 -s -p $p -D; done'
  for k in 0 1 2 3; do
    $D1 "iperf3 -c 10.99.200.113 -B 10.99.1.112 --cport $((44000 + RANDOM % 4000 + k)) -p $((5241+k)) -t $DUR -J > /tmp/soak$k.json 2>&1 &"
  done
  t=0
  while [ $t -lt $DUR ]; do
    sleep 30; t=$((t + 30))
    echo "  t=${t}s bpool eth3=$(bp eth3) eth4=$(bp eth4) $(dut "sudo cat $DBG/stats" | grep -o 'xmit_fail=[0-9]*') frm_cnt=$(dut "sudo cat $DBG/stats" | grep -o 'frm_cnt=[0-9]*' | cut -d= -f2 | tr '\n' ' ')$(dut "sudo cat $WP" | grep -oE ' sealed=[0-9]+| punted=[0-9]+' | tr -d '\n')"
  done
  sleep 8
  tot=0; for k in 0 1 2 3; do
    b=$($D1 "python3 -c \"import json;d=json.load(open('/tmp/soak$k.json'));print(d['end']['sum_sent']['bits_per_second'] if d.get('end') else 0)\"" 2>/dev/null)
    tot=$(awk -v a=$tot -v b=${b:-0} 'BEGIN{print a+b}')
  done
  xf=$(dut "sudo cat $DBG/stats" | grep -o 'xmit_fail=[0-9]*' | cut -d= -f2)
  dut "echo off | sudo tee $WP >/dev/null"; sleep 2
  left=$(dut "sudo cat /sys/kernel/debug/fman_pcd/0/fe_flow" | grep -c 'flow\[')
  sleep 5; e3b=$(bp eth3); e4b=$(bp eth4)
  errs=$(dut 'sudo dmesg --level=err,warn | grep -iE "bug|oops|stall|lockup|refcount|ern|underflow" | grep -v "callbacks suppressed" | head -3')
  diffout=$(dut 'sudo pcd-snapshot diff /tmp/pcd-before.json 2>&1; echo rc=$?')
  echo "  mean up over ${DUR}s: $(awk -v t=$tot 'BEGIN{printf "%.3f", t/1e9}') Gbit/s; bpool eth3 $e3 -> $e3b, eth4 $e4 -> $e4b; xmit_fail $xf; fe_flow left $left"
  [ $(( e3b - e3 )) -ge -64 ] && [ $(( e4b - e4 )) -ge -64 ] && ok "buffer pools intact" || bad "bpool drift eth3 $e3->$e3b eth4 $e4->$e4b"
  [ "${xf:-1}" = 0 ] && ok "no transmit failures" || bad "xmit_fail $xf"
  [ "$left" = 0 ] && ok "every record withdrawn" || bad "$left records left"
  [ -z "$errs" ] && ok "no kernel errors" || { bad "kernel messages"; echo "$errs"; }
  echo "$diffout" | grep -q 'rc=0' && ok "pcd-snapshot diff clean" || { bad "pcd-snapshot diff"; echo "$diffout" | tail -8; }
  ;;
*) echo "usage: $0 overload|soak" >&2; exit 2 ;;
esac
echo "gate: $fails failure(s)"; exit $((fails > 0))
