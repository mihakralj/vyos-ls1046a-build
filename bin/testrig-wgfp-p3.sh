#!/usr/bin/env bash
# testrig-wgfp-p3.sh — WireGuard P3 receive offload gate (plans/ASK2-WIREGUARD-FASTPATH-PLAN.md, stage 3).
#
# Checks, on a live tunnel and with the fast path bound:
#   1. a receive record is admitted only after the exact outer tuple was seen on
#      the stack, and frames then take the sink path (rx advances);
#   2. removing the family from the port the outer flow arrives on withdraws the
#      RX record under traffic: rx freezes, fe_flow loses one record, the flow
#      keeps running in software, and no stale sink flow is left;
#   3. restoring the family relearns and reinstalls the record, and rx advances
#      again.
# The G3 rate (>= 2.6 Gbit/s on four flows) is measured separately:
#   echo 1 > /sys/module/wireguard/parameters/wgfp_rx
#   WGFP=1 REPS=3 PORT=eth4 DIRS=down CELLS=wg-v4 bin/wg-line.sh
#
# Prereq: KEEP=1 bin/testrig-wireguard.sh setup wg-v4 (tunnel wg0, endpoint
# dell2 10.99.2.113), ASK engaged on eth3/eth4, an image with the P3 build.
# Usage: [EV=8] [PORT=eth4] [RXP=0x11] bin/testrig-wgfp-p3.sh
set -u
EV=${EV:-8}
PORT=${PORT:-eth4}; RXP=${RXP:-0x11}
SSHO="-o BatchMode=yes -o ConnectTimeout=8"
dut(){ ssh -n $SSHO -i $HOME/.ssh/vyos_key vyos@192.168.1.185 "$1"; }
D1="ssh -n $SSHO admin@192.168.1.112"; D2="ssh -n $SSHO admin@192.168.1.113"
WP=/sys/module/wireguard/parameters/wgfp_peer
RX=/sys/module/wireguard/parameters/wgfp_rx
FE=/sys/kernel/debug/fman_pcd/0/fe_flow
DBG=/sys/kernel/debug/dpaa_wgfp
ASK=/usr/local/bin/vyos-offload-ask
fails=0; ok(){ echo "  PASS: $*"; }; bad(){ echo "  FAIL: $*"; fails=$((fails+1)); }
st(){ dut "sudo cat $WP" | sed 's/ tmpl=[0-9]//'; }
cnt(){ dut "sudo cat $WP" | grep -o "$1=[0-9]*" | cut -d= -f2 | head -1; }
records(){ dut "sudo cat $FE" | grep -c 'flow\['; }
rxon(){ dut "echo 1 | sudo tee $RX >/dev/null" || { echo "ABORT: no wgfp_rx parameter (image predates P3)"; exit 1; }; }
rxoff(){ dut "echo 0 | sudo tee $RX >/dev/null"; }
on(){ rxoff; dut "echo off | sudo tee $WP >/dev/null"; dut "echo wg0 | sudo tee $WP >/dev/null" || { echo "ABORT: wgfp_peer"; exit 1; }; }
fam(){ dut "sudo $ASK --port $RXP family $1 >/dev/null"; }
srv(){ $D2 'sudo pkill -f "^iperf3 -s -p 5241"; iperf3 -s -p 5241 -D'; }
dstart(){ # dstart <seconds> <json>
  local cp=$((42000 + RANDOM % 8000))
  $D1 "iperf3 -c 10.99.200.113 -B 10.99.1.112 --cport $cp -p 5241 -R -t $1 -i 1 -J > $2 2>&1 &"
}
dwait(){ $D1 "while pgrep -f 'iperf3 .*-c 10.99.200.113' >/dev/null; do sleep 1; done"; }
div(){ $D1 "python3 -c \"import json;d=json.load(open('$1'));print(' '.join('%.2f'%(i['sum']['bits_per_second']/1e9) for i in d['intervals']))\"" 2>/dev/null; }
drtx(){ $D1 "python3 -c \"import json;print(json.load(open('$1'))['end']['sum_sent'].get('retransmits','?'))\"" 2>/dev/null; }
dmean(){ echo "$1" | awk -v a="$2" -v b="$3" '{s=0;n=0;for(i=a;i<=b&&i<=NF;i++){s+=$i;n++} printf "%.2f", n?s/n:0}'; }

echo "== setup: tunnel wg0, fast path bound, P3 off"
on; srv; dut 'ping -c 1 -W 1 10.99.200.113 >/dev/null' || { echo "ABORT: no tunnel"; exit 1; }

echo "== baseline download, P3 off (software RX)"
RX0=$(cnt rx); R0=$(cnt rx_stack); I0=$(cnt installs); W0=$(cnt withdraws); F0=$(records)
dstart 20 /tmp/p3-base.json; dwait
IV=$(div /tmp/p3-base.json); BASE=$(dmean "$IV" 5 18)
echo "  records $F0; rx $RX0 rx_stack $R0 installs $I0 withdraws $W0; download mean $BASE Gbit/s, retransmits $(drtx /tmp/p3-base.json)"
awk -v b="$BASE" 'BEGIN{exit !(b > 0.1)}' && ok "baseline flow ran" || bad "baseline produced no traffic"

echo "== P3 on: a download should install the RX record and advance rx"
rxon
IFX=$(dut "cat /sys/class/net/$PORT/ifindex")
dut "echo reset | sudo tee $DBG/ctl >/dev/null"
dstart 20 /tmp/p3-on.json; dwait
SK=$(dut "sudo cat $DBG/stats")
echo "$SK" | sed -n '1p;/kind=dist/p;/kind=worker/p' | sed 's/ state=.*//' | sed 's/^/  /'
FAN=$(echo "$SK" | head -1 | grep -o 'fanout=[0-9]*' | cut -d= -f2)
DIST=$(echo "$SK" | awk -v p="port=$IFX" '/kind=dist/ && $2 == p {for(i=1;i<=NF;i++) if($i ~ /^frames=/){split($i,a,"=");s+=a[2]}} END{print s+0}')
BUSY=$(echo "$SK" | awk -v p="port=$IFX" '/kind=worker/ && $2 == p {for(i=1;i<=NF;i++) if($i ~ /^frames=/){split($i,a,"="); if(a[2]>1000) n++}} END{print n+0}')
echo "  fanout=$FAN; on $PORT (ifindex $IFX): distributor frames=$DIST, workers with >1000 frames: $BUSY"
if [ "${FAN:-0}" -gt 0 ]; then
  [ "$DIST" -gt 0 ] && ok "RX record steers to the distributor" || bad "distributor saw no frames"
  [ "$BUSY" -ge 2 ] && ok "decrypt spread over $BUSY workers" || bad "frames stayed on one worker"
fi
IV=$(div /tmp/p3-on.json); P1=$(dmean "$IV" 5 18)
R1=$(cnt rx); S1=$(cnt rx_stack); F1=$(records); I1=$(cnt installs)
echo "  records $F0 -> $F1; installs $I0 -> $I1; rx $RX0 -> $R1 (delta $((R1-RX0))), rx_stack $R0 -> $S1; download mean $P1 Gbit/s, retransmits $(drtx /tmp/p3-on.json)"
[ "$F1" -gt "$F0" ] && ok "RX record installed" || bad "no RX record in fe_flow"
[ "${R1:-0}" -gt "${RX0:-0}" ] && ok "frames took the receive fast path" || bad "rx did not advance (P3 not engaged)"

echo "== withdrawal under traffic: download spans the family change on $PORT (0x$RXP)"
dstart 26 /tmp/p3-wd.json
sleep "$EV"
RXB=$(cnt rx); FB=$(records)
echo "  before: $(st)"
fam 0; sleep 6
RXA=$(cnt rx); FA=$(records)
echo "  after family 0 (6 s): records $FB -> $FA; rx $RXB -> $RXA"
[ "$FA" -lt "$FB" ] && ok "RX record withdrawn" || bad "RX record not withdrawn"
[ "$RXA" = "$RXB" ] && ok "no frames reached the withdrawn record" || bad "rx still advancing after withdrawal"
sleep 8
fam 3
dwait
IV=$(div /tmp/p3-wd.json)
WPRE=$(dmean "$IV" 2 $((EV+1)))
WPOST=$(dmean "$IV" $((EV+3)) $((EV+8)))
echo "  download before ${WPRE} Gbit/s, software after withdrawal ${WPOST} Gbit/s"
awk -v a="$WPRE" -v b="$WPOST" 'BEGIN{exit !(b >= 0.5*a)}' && ok "flow continued in software" || bad "flow stalled"

echo "== restore: relearn and reinstall the RX record"
sleep 2
RX2=$(cnt rx); F2=$(records)
dstart 8 /tmp/p3-back.json; dwait
sleep 2
RX3=$(cnt rx); F3=$(records)
echo "  records $F2 -> $F3; rx $RX2 -> $RX3"
[ "$F3" -gt "$F2" ] && ok "RX record relearned" || bad "RX record not reinstalled"
[ "${RX3:-0}" -gt "${RX2:-0}" ] && ok "fast path resumed" || bad "rx did not resume"

echo "== G3 (separate): echo 1 > $RX; WGFP=1 REPS=3 PORT=$PORT DIRS=down CELLS=wg-v4 bin/wg-line.sh"
rxoff
[ "$fails" = 0 ] && echo "P3 WITHDRAWAL GATE: PASS" || echo "P3 WITHDRAWAL GATE: $fails FAILURE(S)"
exit "$fails"