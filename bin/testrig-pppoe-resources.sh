#!/usr/bin/env bash
# PPPoE resource gates (plans/ASK2-PPPOE-OFFLOAD-PLAN.md, gate "Resources"): fragment-pool
# exhaustion and the long mixed-size soak. ASK2 DUT only. The rig must already carry the PPPoE
# session used by testrig-pppoe-churn.sh: DUT pppoe10 (MTU 1492) <-> gen1 concentrator CONC,
# gen2 (GEN2_IP) behind DUT eth4, return routes in place.
#
# One UDP flow gen2 GEN2_IP:40000 -> CONC:5999 is warmed (a gen1 receiver answers, so the flow
# becomes ASSURED and offloaded; [HW_OFFLOAD] is required) and then driven by kernel pktgen on gen2
# (iperf3 UDP does not work through the DUT on this rig).
#   MODE=fragpool: 1500-byte IP datagrams (DF clear) that the DUT must fragment into the 1492 MTU, at
#     RATE pps per run (0 = line rate) for DURATION s. The F-262 fragment pool (BMan bpid from the
#     boot log, read via the pool-content register) drains; at line rate two fragments need about
#     10.6 Gbit/s, so delivery is capped by the 10G egress near 93%. Delivery = fragment pairs
#     counted by the gen1 NIC size counters (i40e port.rx_size_64 / port.rx_size_1522).
#   MODE=soak: bidirectional TCP over pppoe10 (encap from gen2, decap from gen1, -P 4 -b TCP_RATE -Z)
#     plus pktgen IMIX (64:7, 570:4, 1514:1) at RATE pps on the UDP flow for DURATION s, with a
#     DUT state sample every SAMPLE_S s.
# PASS requires afterwards: pool back to its start level, MURAM `used` and the record count back to
# baseline (after deleting the test conntrack entries), a clean pcd-snapshot diff, no new kernel
# errors, no oversize frames at gen1 (i40e port.rx_oversize / rx_size_big), ping to CONC without
# loss and a working TCP flow; fragpool also needs delivery >= MIN_DELIV.
#
# Usage: bin/testrig-pppoe-resources.sh
# Env:   MODE=fragpool|soak DURATION RATE THREADS (fragpool) TCP_RATE SAMPLE_S MIN_DELIV
#        CONC (10.99.50.1) GEN2_IP (10.99.2.113) DUT_EGRESS (eth4) BPID (frag pool; default: kernel log)
#        RIG_NS=$VENDOR_NS runs both generators' commands inside that netns on GEN_IF_NS
#        (otherwise GEN_IF in the root namespace). Hosts and key: bin/testrig-env.sh.
# Exit: 0 PASS, 1 FAIL, 2 setup error.
set -u
. "$(dirname "$0")/testrig-env.sh"
rig_require ASK2_DUT GEN1 GEN2
MODE=${MODE:-fragpool}
case $MODE in
  fragpool) DURATION=${DURATION:-60}; RATE=${RATE:-0}; THREADS=${THREADS:-2} ;;
  soak) DURATION=${DURATION:-1800}; RATE=${RATE:-300000}; THREADS=1 ;;
  *) echo "MODE must be fragpool or soak" >&2; exit 2 ;;
esac
TCP_RATE=${TCP_RATE:-1500M}; SAMPLE_S=${SAMPLE_S:-60}
if [ "$RATE" = 0 ]; then MIN_DELIV=${MIN_DELIV:-0.90}; else MIN_DELIV=${MIN_DELIV:-0.99}; fi
CONC=${CONC:-10.99.50.1}; GEN2_IP=${GEN2_IP:-10.99.2.113}; DUT_EGRESS=${DUT_EGRESS:-eth4}
RIG_NS=${RIG_NS:-}
if [ -n "$RIG_NS" ]; then IFG=$GEN_IF_NS; NSX="ip netns exec $RIG_NS "; else IFG=$GEN_IF; NSX=""; fi

SSHO="-o BatchMode=yes -o ConnectTimeout=8"
dut(){ ssh -n $SSHO $RIG_SSHK "$ASK2_DUT" "$1"; }
# generator commands go in on stdin and run as root (inside RIG_NS when set)
g1(){ ssh $SSHO $RIG_SSHK "$GEN1" "sudo ${NSX}bash -s" <<< "$1"; }
g2(){ ssh $SSHO $RIG_SSHK "$GEN2" "sudo ${NSX}bash -s" <<< "$1"; }
PCD=/sys/kernel/debug/fman_pcd/0
ERR='F-256|SYNC timed out|DMA bus error|WARNING:|BUG:|Oops|Call trace|circular locking|suspicious RCU|re-engage failed|RX-deaf|Err FD'
TAG=pppoe-res-$$; FAILS=0
fail(){ echo "FAIL: $*"; FAILS=$((FAILS+1)); }

# the F-262 pool is set up on the first offloaded PPPoE flow, so it is resolved after the warm-up
POOLREG=""
find_pool(){
  [ -n "${BPID:-}" ] || BPID=$(dut "sudo journalctl -k -b --no-pager -o cat | grep -o 'frag pool bpid [0-9]*' | tail -1 | awk '{print \$4}'")
  [ -n "$BPID" ] || return 1
  POOLREG=$(printf '0x%x' $((0x1890600 + BPID*4)))
}
# BMan pool-content register reads byte-swapped through devmem (0x00080000 = 2048)
pool(){ dut "v=\$(sudo busybox devmem $POOLREG 32); echo \$(( (v>>24)&0xff | ((v>>16)&0xff)<<8 ))"; }
recs(){ dut "sudo grep -c 'keysz=' $PCD/fe_ehash_stats"; }
muram(){ dut "sudo awk '/^used/{print \$2}' $PCD/muram_budget"; }
st(){ dut "v=\$(sudo busybox devmem $POOLREG 32); printf 'pool=%d ' \$(( (v>>24)&0xff | ((v>>16)&0xff)<<8 ))
  sudo awk '/^used/{printf \"muram=%s \", \$2}' $PCD/muram_budget; printf 'recs=%s ' \$(sudo grep -c keysz= $PCD/fe_ehash_stats)
  for i in eth3 eth4; do printf '%s_krx=%s ' \$i \$(sudo ethtool -S \$i | awk '/rx packets \\[TOTAL\\]/{print \$NF}'); done
  for i in eth3 eth4; do sudo ethtool -S \$i | grep -iE 'err|drop|fifo|overflow|discard|congest' | grep -v interrupts | grep -v ': 0\$' | sed \"s/^ */\$i:/\" | tr -s ' ' | tr '\n' ' '; done
  printf 'load=%s' \$(cut -d' ' -f1 /proc/loadavg); echo"; }
x710(){ g1 "ethtool -S $IFG" | awk '/port\.rx_(size_64|size_1522|size_big|oversize):/{gsub(":","");printf "%s=%s ",$1,$2}'; }
xget(){ echo "$1" | tr ' ' '\n' | awk -F= -v k="$2" '$1==k{print $2}'; }
ct_flush(){ dut "sudo conntrack -D -d $CONC >/dev/null 2>&1; sudo conntrack -D -s $CONC >/dev/null 2>&1; true"; }

MAC=$(dut "cat /sys/class/net/$DUT_EGRESS/address")
M0=$(muram); R0=$(recs)
CUR=$(dut "sudo journalctl -k -n0 --show-cursor --no-pager | sed -n 's/^-- cursor: //p'")
dut "sudo pcd-snapshot capture -o /tmp/$TAG.json >/dev/null" || { echo "pcd-snapshot capture failed" >&2; exit 2; }
echo "mode $MODE, ${DURATION}s, rate ${RATE} pps, MURAM $M0, records $R0, dst MAC $MAC"

g1 "pkill -f '^python3 -c .*5999' 2>/dev/null; setsid nohup python3 -c 'import socket,time
s=socket.socket(socket.AF_INET,socket.SOCK_DGRAM); s.setsockopt(socket.SOL_SOCKET,socket.SO_RCVBUF,32<<20)
s.bind((\"$CONC\",5999)); s.settimeout(3); n=0; t=time.time()+$DURATION+180
while time.time()<t:
  try: d,a=s.recvfrom(9000)
  except socket.timeout: continue
  n+=1
  if n<200 or n%2000==0: s.sendto(b\"x\",a)' </dev/null >/dev/null 2>&1 &"
sleep 1
W=$(g2 "python3 -c 'import socket,time
s=socket.socket(socket.AF_INET,socket.SOCK_DGRAM); s.bind((\"$GEN2_IP\",40000)); s.settimeout(0.2); g=0
for i in range(100):
  s.sendto(b\"w\"*64,(\"$CONC\",5999))
  try: s.recv(10); g+=1
  except Exception: pass
  time.sleep(0.02)
print(g)'")
HW=$(dut "sudo conntrack -L -p udp --dport 5999 2>/dev/null | grep -c HW_OFFLOAD")
echo "warm replies ${W:-0}, HW_OFFLOAD conntracks $HW"
[ "${HW:-0}" -ge 1 ] || { fail "UDP test flow not offloaded"; g1 "pkill -f '^python3 -c .*5999'"; exit 1; }
find_pool || { echo "no 'frag pool bpid' line in the DUT kernel log (set BPID=)" >&2; g1 "pkill -f '^python3 -c .*5999'"; exit 2; }
POOL0=$(pool); echo "frag pool bpid $BPID ($POOLREG): $POOL0 buffers"
[ "$POOL0" -gt 0 ] || { echo "frag pool bpid $BPID is empty before the run" >&2; g1 "pkill -f '^python3 -c .*5999'"; exit 2; }
echo "pre:  $(st)"
X0=$(x710)

# pktgen job on gen2 (runs for DURATION, then prints per-thread totals)
PGJOB="modprobe pktgen; PGD=/proc/net/pktgen
echo reset > \$PGD/pgctrl
for t in \$(seq 0 $((THREADS-1))); do
  echo rem_device_all > \$PGD/kpktgend_\$t 2>/dev/null; echo add_device $IFG@\$t > \$PGD/kpktgend_\$t
  f=\$PGD/$IFG@\$t
  for c in 'count 0' $( [ "$MODE" = soak ] && echo "'imix_weights 64,7 570,4 1514,1' 'clone_skb 100'" || echo "'pkt_size 1514' 'clone_skb 1000'" ) \\
      'dst $CONC' 'src_min $GEN2_IP' 'src_max $GEN2_IP' 'udp_src_min 40000' 'udp_src_max 40000' \\
      'udp_dst_min 5999' 'udp_dst_max 5999' 'dst_mac $MAC' \"queue_map_min \$t\" \"queue_map_max \$t\" 'burst 1' \\
      'delay 0' $( [ "$RATE" -gt 0 ] && echo "'ratep $((RATE/THREADS))'" ); do echo \"\$c\" > \$f; done
done
echo start > \$PGD/pgctrl & sleep $DURATION; echo stop > \$PGD/pgctrl; wait
for t in \$(seq 0 $((THREADS-1))); do awk '/^Result:/{print}' \$PGD/$IFG@\$t; grep -o 'sofar: [0-9]*' \$PGD/$IFG@\$t; done"
OUT=$(mktemp); g2 "$PGJOB" > "$OUT" 2>&1 &
PGPID=$!
if [ "$MODE" = soak ]; then
  g2 "iperf3 -c $CONC -B $GEN2_IP -t $DURATION -P 4 -b $TCP_RATE -Z 2>&1 | grep -E 'SUM.*(sender|receiver)'" | sed 's/^/encap /' &
  g1 "iperf3 -c $GEN2_IP -B $CONC -t $DURATION -P 4 -b $TCP_RATE -Z 2>&1 | grep -E 'SUM.*(sender|receiver)'" | sed 's/^/decap /' &
  end=$(( $(date +%s) + DURATION ))
  while [ "$(date +%s)" -lt $((end-5)) ]; do sleep "$SAMPLE_S"; echo "$(date -u +%T) $(st)"; done
else
  PMIN=$POOL0; s=""
  for _ in $(seq 1 $((DURATION/2))); do sleep 2; p=$(pool); s="$s $p"; [ "$p" -lt "$PMIN" ] && PMIN=$p; done
  echo "pool samples:$s"; echo "pool minimum $PMIN of $POOL0"
fi
wait; sleep 5
sed 's/^/pktgen /' "$OUT"
SENT=$(grep -o 'sofar: [0-9]*' "$OUT" | awk '{s+=$2} END{print s+0}'); rm -f "$OUT"
X1=$(x710)
echo "gen1 pre:  $X0"; echo "gen1 post: $X1"
d(){ echo $(( $(xget "$X1" "$1") - $(xget "$X0" "$1") )); }
if [ -n "$(xget "$X0" port.rx_oversize)" ]; then
  OV=$(( $(d port.rx_oversize) + $(d port.rx_size_big) ))
  [ "$OV" -eq 0 ] || fail "$OV oversize frames at gen1"
  if [ "$MODE" = fragpool ]; then
    P1=$(d port.rx_size_1522); P2=$(d port.rx_size_64); PAIRS=$(( P1 < P2 ? P1 : P2 ))
    echo "sent $SENT, first fragments $P1, second fragments $P2"
    awk -v p="$PAIRS" -v s="$SENT" -v m="$MIN_DELIV" 'BEGIN{r=(s>0)?p/s:0; printf "delivered %.1f%% (minimum %.0f%%)\n", r*100, m*100; exit !(r>=m)}' \
      || fail "fragment-pair delivery below $MIN_DELIV"
  fi
else
  echo "gen1 NIC has no i40e port.rx_* counters: oversize/delivery checks skipped"
fi

g1 "pkill -f '^python3 -c .*5999'"
ct_flush
for _ in $(seq 1 30); do [ "$(pool)" -ge "$POOL0" ] && [ "$(recs)" -le "$R0" ] && break; sleep 1; done
echo "post-drain: $(st)"
P=$(pool); [ "$P" -ge "$POOL0" ] || fail "fragment pool $P, started at $POOL0"
R=$(recs); [ "$R" -le "$R0" ] || fail "records $R, baseline $R0"
M=$(muram); [ "$M" = "$M0" ] || fail "MURAM used $M, baseline $M0"
dut "sudo pcd-snapshot diff /tmp/$TAG.json 2>&1 | tail -3; sudo rm -f /tmp/$TAG.json" | tee /dev/stderr | grep -q '\[OK\]' || fail "pcd-snapshot diff"
KE=$(dut "sudo journalctl -k --no-pager --after-cursor='$CUR' | grep -E '$ERR'")
[ -z "$KE" ] || { echo "$KE" | head -5; fail "new kernel errors"; }
PL=$(g2 "ping -c5 -i0.2 -W1 $CONC | grep -o '[0-9.]*% packet loss'"); echo "ping: $PL"
[ "${PL%%%*}" = 0 ] || fail "ping to $CONC: $PL"
TP=$(g2 "iperf3 -c $CONC -B $GEN2_IP -t 5 -P 8 -Z 2>&1 | grep -E 'SUM.*receiver'"); echo "tcp: $TP"
[ -n "$TP" ] || fail "post-run TCP did not complete"
ct_flush

echo "== result: $FAILS failure(s)"
[ "$FAILS" -eq 0 ] && { echo PASS; exit 0; } || { echo FAIL; exit 1; }
