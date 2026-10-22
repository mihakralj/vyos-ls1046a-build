#!/bin/bash
# testrig-neigh-change.sh: P4 neighbour-change test on the ASK2 DUT (eth3 -> eth4 routed flow).
# gen1 -> gen2 iperf3 runs through the DUT while the DUT's neighbour entry for gen2 is
#   (1) flushed (re-resolve), (2) pointed at a wrong MAC (traffic must stop), then
#   (3) restored (traffic must resume through a rebuilt record, hardware-hit again).
# Per-second receiver rates are printed; verdicts use the 1 s samples.
#   bin/testrig-neigh-change.sh        (hosts and key from bin/testrig-env.sh)
# Never run while the A8 soak owns the rig.
set -u
. "$(dirname "$0")/testrig-env.sh"
DUT=${DUT:-$(rig_dut ask2)} || exit 2
rig_require GEN1
GEN2_IP=${GEN2_IP:-10.99.2.113}; SRC_IP=${SRC_IP:-10.99.1.112}; OUTIF=${OUTIF:-eth4}
SSHO="-o BatchMode=yes -o ConnectTimeout=8"
d(){ ssh -n $SSHO $RIG_SSHK "$DUT" "$@"; }
g1(){ ssh -n $SSHO $RIG_SSHK "$GEN1" "$@"; }
used(){ d "sudo cat /sys/kernel/debug/fman_pcd/0/muram_budget | awk '/^used/{print \$2}'"; }
hits(){ d "sudo cat /sys/kernel/debug/fman_pcd/0/fe_ehash_stats" | sed -n 's/.*pkt_count=\([0-9]*\).*/\1/p' | awk '{s+=$1}END{print s+0}'; }
kerr(){ d "sudo dmesg | tail -300 | grep -ciE 'oops|BUG:|WARNING:|call trace'"; }
fail=0
chk(){ if [ "$2" = "$3" ]; then echo "PASS $1 ($3)"; else echo "FAIL $1: got '$2' want '$3'"; fail=$((fail+1)); fi; }

REAL=$(d "ip neigh show $GEN2_IP dev $OUTIF" | awk '{print $3}')
[ -n "$REAL" ] || { d "ping -c 2 -W 2 -I $OUTIF $GEN2_IP >/dev/null"; REAL=$(d "ip neigh show $GEN2_IP dev $OUTIF" | awk '{print $3}'); }
[ -n "$REAL" ] || { echo "no neighbour for $GEN2_IP"; exit 2; }
BOGUS=02:00:00:aa:bb:cc
BASE=$(used); K0=$(kerr)
echo "== neighbour $GEN2_IP real=$REAL muram_base=$BASE"
trap 'd "sudo ip neigh replace $GEN2_IP dev $OUTIF lladdr $REAL nud permanent; sudo ip neigh change $GEN2_IP dev $OUTIF lladdr $REAL nud reachable" >/dev/null 2>&1' EXIT

LOG=/tmp/neigh-iperf.json
g1 "rm -f $LOG; nohup iperf3 -c $GEN2_IP -B $SRC_IP -P 4 -t 45 -i 1 -J >$LOG 2>&1 &" 
sleep 8
H0=$(hits); sleep 3; H1=$(hits)
[ "$((H1-H0))" -gt 10000 ] && echo "PASS hw-hit-before (+$((H1-H0)) pkts/3s)" || { echo "FAIL hw-hit-before (+$((H1-H0)))"; fail=$((fail+1)); }
T0=$(date +%s)

echo "== 1. flush neighbour (re-resolve under load)"
d "sudo ip neigh flush $GEN2_IP dev $OUTIF"; sleep 8
echo "== 2. wrong MAC (1.5 s blackhole; longer lets TCP RTO backoff mask recovery)"
d "sudo ip neigh replace $GEN2_IP dev $OUTIF lladdr $BOGUS nud permanent; sleep 1.5"
echo "== 3. restore real MAC"
d "sudo ip neigh replace $GEN2_IP dev $OUTIF lladdr $REAL nud permanent"; sleep 10
R0=$(hits); sleep 3; R1=$(hits)
[ "$((R1-R0))" -gt 10000 ] && echo "PASS hw-hit-after-restore (+$((R1-R0)) pkts/3s)" || { echo "FAIL hw-hit-after-restore (+$((R1-R0)))"; fail=$((fail+1)); }
d "sudo ip neigh change $GEN2_IP dev $OUTIF lladdr $REAL nud reachable" >/dev/null 2>&1

sleep 12
OUTJ=$(g1 "cat $LOG")
echo "$OUTJ" | python3 -c '
import json,sys
j=json.load(sys.stdin)
iv=[round(i["sum"]["bits_per_second"]/1e9,2) for i in j["intervals"]]
print("per-second Gbit/s:",iv)
' 2>&1
g1 "pkill iperf3" >/dev/null 2>&1
sleep 2
chk muram-after "$(used)" "$BASE"
chk kernel-errors "$(kerr)" "$K0"
d "sudo ask-check 2>&1 | grep -E 'FAIL|Summary' | head -3"
echo "RESULT neigh-change failures=$fail"; exit $((fail>0))
