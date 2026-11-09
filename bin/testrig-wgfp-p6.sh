#!/usr/bin/env bash
# testrig-wgfp-p6.sh — WireGuard P6 (zero-skb decrypt) correctness gate, plans/ASK2-WIREGUARD-FASTPATH-PLAN.md 4b.
#
# On a live tunnel with the fast path bound and P3 on (download = dell2 -> wg -> DUT eth4 -> eth3 -> dell1):
#   1. wgfp_rx_fast 2: frames are decrypted in the sink and forwarded directly (p6_fwd), inner flows are
#      learned from the stack (p6_learn), no authentication or frame errors;
#   2. wgfp_rx_fast 1: the decrypted packets go to the stack instead (p6_stack), none forwarded directly;
#   3. duplicates (netem duplicate on dell2) are refused by the shared replay window (p6_replay > 0);
#   4. reordering within the window (netem reorder on dell2) is accepted: no replay drops;
#   5. a forward drop rule added mid-flow stops the flow within one record lifetime (1 s), and the flow
#      comes back after the rule is removed;
#   6. switching wgfp_rx_fast 2 -> 0 -> 2 under traffic keeps the flow running;
#   7. unbinding the fast path under traffic leaves no kernel warning.
# The G6 rate is measured separately:
#   WGFP=1 WGFP_RX=1 WGFP_RX_FAST=2 bin/wg-matrix.sh ...   (4 tunnels down >= 4.0, 1 tunnel >= 3.0)
#
# Prereq: KEEP=1 bin/testrig-wireguard.sh setup wg-v4 (tunnel wg0, endpoint dell2 10.99.2.113, inner
# 10.99.200.113), ASK engaged on eth3/eth4, an image with P6 (wireguard.wgfp_rx_fast exists).
# P7=1 runs the same gate for the P7 tail instead (wgfp_rx_tail=1, wgfp_rx_fast=0): steps 1 (p7_fwd), 5, 6
# (wgfp_rx_tail 1 -> 0 -> 1) and 7; steps 2-4 test P6's own decrypt and are skipped.
# Usage: [P7=1] [GEN_IF=enp1s0f0np0] bin/testrig-wgfp-p6.sh
set -u
GEN_IF=${GEN_IF:-enp1s0f0np0}
SSHO="-o BatchMode=yes -o ConnectTimeout=8"
dut(){ ssh -n $SSHO -i $HOME/.ssh/vyos_key vyos@192.168.1.185 "$1"; }
D1="ssh -n $SSHO admin@192.168.1.112"; D2="ssh -n $SSHO admin@192.168.1.113"
WP=/sys/module/wireguard/parameters/wgfp_peer
RX=/sys/module/wireguard/parameters/wgfp_rx
FAST=/sys/module/wireguard/parameters/wgfp_rx_fast
TAIL=/sys/module/wireguard/parameters/wgfp_rx_tail
P7=${P7:-0}
if [ "$P7" = 1 ]; then FWD=p7_fwd; else FWD=p6_fwd; fi
WL=/sys/module/wireguard/parameters/wgfp_peer_lease
fails=0; ok(){ echo "  PASS: $*"; }; bad(){ echo "  FAIL: $*"; fails=$((fails+1)); }
cnt(){ dut "sudo cat $WP" | grep -o " $1=[0-9]*" | cut -d= -f2 | head -1; }
snap(){ dut "sudo cat $WP"; }
of(){ echo "$1" | grep -o " $2=[0-9]*" | cut -d= -f2 | head -1; }
d(){ echo $(( $(of "$2" "$1") - $(of "$3" "$1") )); }	# d <counter> <after> <before>
TOK=${WG_LEASE_TOKEN:-$(cat "${WG_STATE_PATH:-/tmp/wg-rig}.single-lease/token" 2>/dev/null)}
[[ $TOK =~ ^[0-9a-f]{32}$ ]] || { echo "ABORT: no wgfp lease token (run KEEP=1 bin/testrig-wireguard.sh setup wg-v4)"; exit 1; }
off(){ [ "$(dut "sudo cat $WP")" = off ] || dut "echo 'stop $TOK' | sudo tee $WL >/dev/null"; }
on(){ off; dut "echo 'bind $TOK wgo-$TOK-dut' | sudo tee $WL >/dev/null" || { echo "ABORT: wgfp_peer_lease bind"; exit 1; }; }
fast(){ dut "echo $1 | sudo tee $FAST >/dev/null"; }
tail_(){ dut "echo $1 | sudo tee $TAIL >/dev/null"; }
# the mode under test: on (2 for P6, 1 for P7) or off
mode(){ if [ "$P7" = 1 ]; then tail_ "$1"; else fast "$([ "$1" = 0 ] && echo 0 || echo 2)"; fi; }
srv(){ $D2 'sudo pkill -f "^iperf3 -s -p 5241"; iperf3 -s -p 5241 -D'; }
dstart(){ # dstart <seconds> <json>
  local cp=$((42000 + RANDOM % 8000))
  $D1 "iperf3 -c 10.99.200.113 -B 10.99.1.112 --cport $cp -p 5241 -R -t $1 -i 1 -J > $2 2>&1 &"
}
dwait(){ $D1 "while pgrep -f '[i]perf3 .*-c 10.99.200.113' >/dev/null; do sleep 1; done"; }
div(){ $D1 "python3 -c \"import json;d=json.load(open('$1'));print(' '.join('%.2f'%(i['sum']['bits_per_second']/1e9) for i in d['intervals']))\"" 2>/dev/null; }
drtx(){ $D1 "python3 -c \"import json;print(json.load(open('$1'))['end']['sum_sent'].get('retransmits','?'))\"" 2>/dev/null; }
dmean(){ echo "$1" | awk -v a="$2" -v b="$3" '{s=0;n=0;for(i=a;i<=b&&i<=NF;i++){s+=$i;n++} printf "%.2f", n?s/n:0}'; }
dmax(){ echo "$1" | awk -v a="$2" -v b="$3" '{m=0;for(i=a;i<=b&&i<=NF;i++) if($i>m) m=$i; printf "%.2f", m}'; }
netem(){ $D2 "sudo tc qdisc replace dev $GEN_IF root netem $1"; }
nonetem(){ $D2 "sudo tc qdisc del dev $GEN_IF root 2>/dev/null; true"; }
FW=p6gate
fwon(){ dut "sudo nft 'add table inet $FW; add chain inet $FW p6fwd { type filter hook forward priority 0; }; add rule inet $FW p6fwd ip saddr 10.99.200.113 ip daddr 10.99.1.112 tcp sport 5241 drop'"; }
fwoff(){ dut "sudo nft delete table inet $FW 2>/dev/null; true"; }

RX_ORIG=$(dut "sudo cat $RX") || exit 1
FAST_ORIG=$(dut "sudo cat $FAST") || { echo "ABORT: no $FAST (image predates P6)"; exit 1; }
TAIL_ORIG=N
if [ "$P7" = 1 ]; then TAIL_ORIG=$(dut "sudo cat $TAIL") || { echo "ABORT: no $TAIL (image predates P7)"; exit 1; }; fi
cleanup(){
  trap - EXIT INT TERM
  nonetem; fwoff
  $D1 "pkill -f '[i]perf3 .*-c 10.99.200.113'" 2>/dev/null
  $D2 "pkill -f '^iperf3 -s -p 5241'" 2>/dev/null
  dut "echo $FAST_ORIG | sudo tee $FAST >/dev/null; echo $RX_ORIG | sudo tee $RX >/dev/null"
  [ "$P7" = 1 ] && dut "echo $TAIL_ORIG | sudo tee $TAIL >/dev/null"
  off
  echo "restored wgfp_rx=$RX_ORIG wgfp_rx_fast=$FAST_ORIG, fast path unbound"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
KMSG0=$(dut "sudo dmesg | wc -l")

echo "== setup: tunnel wg0, fast path bound, P3 on"
on; dut "echo 1 | sudo tee $RX >/dev/null"; srv
dut 'ping -c 1 -W 1 10.99.200.113 >/dev/null' || { echo "ABORT: no tunnel"; exit 1; }

echo "== 1. $([ "$P7" = 1 ] && echo "wgfp_rx_tail 1: direct forward at the end of the upstream path" || echo "wgfp_rx_fast 2: direct forward")"
mode 1; sleep 2
A=$(snap); dstart 20 /tmp/p6-fwd.json; dwait; B=$(snap)
IV=$(div /tmp/p6-fwd.json); M2=$(dmean "$IV" 5 18)
echo "  download mean $M2 Gbit/s, retransmits $(drtx /tmp/p6-fwd.json); $FWD +$(d $FWD "$B" "$A") p6_stack +$(d p6_stack "$B" "$A") p6_punt +$(d p6_punt "$B" "$A") p6_learn +$(d p6_learn "$B" "$A") rx(P3) +$(d rx "$B" "$A") rflows=$(of "$B" rflows)"
[ "$(d $FWD "$B" "$A")" -gt 10000 ] && ok "frames forwarded directly ($FWD)" || bad "$FWD did not advance"
[ "$(d p6_learn "$B" "$A")" -gt 0 ] && ok "inner flow learned from the stack" || bad "no inner flow learned"
[ "$(d p6_auth "$B" "$A")" = 0 ] && [ "$(d p6_err "$B" "$A")" = 0 ] && [ "$(d p6_aip "$B" "$A")" = 0 ] &&
  ok "no authentication, frame or AllowedIPs errors" || bad "p6_auth/p6_err/p6_aip moved"
awk -v m="$M2" 'BEGIN{exit !(m > 0.5)}' && ok "flow ran ($M2 Gbit/s)" || bad "flow too slow ($M2 Gbit/s)"

if [ "$P7" != 1 ]; then
echo "== 2. wgfp_rx_fast 1: decrypt, then the stack"
fast 1; sleep 2
A=$(snap); dstart 15 /tmp/p6-stack.json; dwait; B=$(snap)
M1=$(dmean "$(div /tmp/p6-stack.json)" 4 13)
echo "  download mean $M1 Gbit/s; p6_fwd +$(d p6_fwd "$B" "$A") p6_stack +$(d p6_stack "$B" "$A")"
[ "$(d p6_stack "$B" "$A")" -gt 10000 ] && [ "$(d p6_fwd "$B" "$A")" = 0 ] &&
  ok "plaintext handed to the stack, nothing forwarded directly" || bad "mode 1 counters wrong"

echo "== 3. duplicates from the peer are refused by the shared replay window"
fast 2; netem "duplicate 1%"; sleep 1
A=$(snap); dstart 12 /tmp/p6-dup.json; dwait; B=$(snap); nonetem
FW_=$(d p6_fwd "$B" "$A"); RP=$(d p6_replay "$B" "$A")
echo "  p6_fwd +$FW_ p6_replay +$RP; download mean $(dmean "$(div /tmp/p6-dup.json)" 3 10) Gbit/s"
[ "$RP" -gt 0 ] && ok "duplicates dropped by the window ($RP)" || bad "no replay drop seen with 1% duplicates"
awk -v r="$RP" -v f="$FW_" 'BEGIN{exit !(f > 0 && r <= 0.05 * f)}' && ok "replay drops bounded by the duplicate rate" || bad "replay drops out of proportion"

echo "== 4. reordering within the window is accepted"
netem "delay 2ms reorder 30% 50%"; sleep 1
A=$(snap); dstart 12 /tmp/p6-reo.json; dwait; B=$(snap); nonetem
RP=$(d p6_replay "$B" "$A")
echo "  p6_fwd +$(d p6_fwd "$B" "$A") p6_stack +$(d p6_stack "$B" "$A") p6_replay +$RP; download mean $(dmean "$(div /tmp/p6-reo.json)" 3 10) Gbit/s"
[ "$RP" = 0 ] && ok "no replay drop under reordering" || bad "reordered packets refused ($RP)"
[ "$(d p6_fwd "$B" "$A")" -gt 0 ] && ok "reordered flow still on the fast path" || bad "no P6 frames under reordering"
fast 2
fi

echo "== 5. a forward drop rule added mid-flow stops the flow"
dstart 30 /tmp/p6-fw.json
sleep 8; fwon; sleep 2; F0=$(cnt $FWD); sleep 3; F1=$(cnt $FWD); sleep 1; fwoff
dwait
IV=$(div /tmp/p6-fw.json)
PRE=$(dmean "$IV" 3 7); BLK=$(dmax "$IV" 11 13); POST=$(dmax "$IV" 20 29)
echo "  download before ${PRE}, max while blocked ${BLK}, max after removal ${POST} Gbit/s; $FWD while blocked $F0 -> $F1"
awk -v b="$BLK" 'BEGIN{exit !(b < 0.05)}' && ok "flow stopped within a record lifetime" || bad "flow kept running past the drop rule ($BLK)"
[ "$F0" = "$F1" ] && ok "no direct forwarding while the firewall drops" || bad "$FWD advanced under the drop rule"
awk -v p="$POST" 'BEGIN{exit !(p > 0.1)}' && ok "flow back after the rule was removed" || bad "flow did not recover"

echo "== 6. mode on -> off -> on under traffic"
dstart 21 /tmp/p6-tog.json
sleep 7; mode 0; sleep 7; mode 1; dwait
IV=$(div /tmp/p6-tog.json)
echo "  download: P6 $(dmean "$IV" 3 6), P3 $(dmean "$IV" 9 13), P6 again $(dmean "$IV" 16 20) Gbit/s"
LOW=$(echo "$IV" | awk '{m=99;for(i=3;i<=20&&i<=NF;i++) if($i<m) m=$i; print m}')
awk -v l="$LOW" 'BEGIN{exit !(l > 0.1)}' && ok "flow ran through both switches" || bad "flow stalled around a switch (min $LOW)"

echo "== 7. unbind under traffic"
dstart 12 /tmp/p6-unbind.json; sleep 5; off; dwait
on; dut "echo 1 | sudo tee $RX >/dev/null"
NEW=$(dut "sudo dmesg | tail -n +$((KMSG0 + 1)) | grep -E 'WARNING|BUG:|Oops:|refcount|leak|[^_]wgfp: |fastpath: '| grep -v 'fastpath peer mode on' | head -5")
[ -z "$NEW" ] && ok "no kernel warnings" || { bad "kernel messages:"; echo "$NEW" | sed 's/^/    /'; }

[ "$fails" = 0 ] && echo "P6 GATE: PASS" || echo "P6 GATE: $fails FAILURE(S)"
