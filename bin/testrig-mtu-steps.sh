#!/bin/bash
# testrig-mtu-steps.sh: P4 MTU steps with the generator MTU raised to match (runtime only).
# For each MTU in STEPS, DUT eth3/eth4 and both generator ports are set to that MTU, a
# don't-fragment ping of full size crosses the DUT gen1 -> gen2, then iperf3 runs and the
# hardware hit counter must grow. Everything is restored to 1500 on exit.
#   bin/testrig-mtu-steps.sh       (hosts and key from bin/testrig-env.sh)
# Never run while the A8 soak owns the rig.
set -u
. "$(dirname "$0")/testrig-env.sh"
DUT=${DUT:-$(rig_dut ask2)} || exit 2
rig_require GEN1; rig_require GEN2
STEPS=${STEPS:-"1280 2500 3600 1500"}
GEN2_IP=${GEN2_IP:-10.99.2.113}; SRC_IP=${SRC_IP:-10.99.1.112}
G1IF=${G1IF:-enp1s0f0np0}; G2IF=${G2IF:-enp1s0f0np0}
SSHO="-o BatchMode=yes -o ConnectTimeout=8"
d(){ ssh -n $SSHO $RIG_SSHK "$DUT" "$@"; }
g1(){ ssh -n $SSHO $RIG_SSHK "$GEN1" "$@"; }
g2(){ ssh -n $SSHO $RIG_SSHK "$GEN2" "$@"; }
used(){ d "sudo cat /sys/kernel/debug/fman_pcd/0/muram_budget | awk '/^used/{print \$2}'"; }
hits(){ d "sudo cat /sys/kernel/debug/fman_pcd/0/fe_ehash_stats" | sed -n 's/.*pkt_count=\([0-9]*\).*/\1/p' | awk '{s+=$1}END{print s+0}'; }
kerr(){ d "sudo dmesg | tail -300 | grep -ciE 'oops|BUG:|WARNING:|call trace'"; }
fail=0
chk(){ if [ "$2" = "$3" ]; then echo "PASS $1 ($3)"; else echo "FAIL $1: got '$2' want '$3'"; fail=$((fail+1)); fi; }
setmtu(){
  # generators first when growing is not required: set all four, errors are checked by readback
  d "sudo ip link set eth3 mtu $1; sudo ip link set eth4 mtu $1"
  g1 "sudo ip link set $G1IF mtu $1"; g2 "sudo ip link set $G2IF mtu $1"
  sleep 4
}
restore(){ setmtu 1500 >/dev/null 2>&1; }
trap restore EXIT

BASE=$(used); K0=$(kerr)
for m in $STEPS; do
  echo "== MTU $m"
  setmtu "$m"
  chk "dut-eth3-mtu-$m" "$(d 'cat /sys/class/net/eth3/mtu')" "$m"
  chk "dut-eth4-mtu-$m" "$(d 'cat /sys/class/net/eth4/mtu')" "$m"
  chk "gen1-mtu-$m" "$(g1 "cat /sys/class/net/$G1IF/mtu")" "$m"
  chk "gen2-mtu-$m" "$(g2 "cat /sys/class/net/$G2IF/mtu")" "$m"
  r=$(g1 "ping -c 3 -W 2 -I $SRC_IP $GEN2_IP >/dev/null 2>&1; echo \$?")
  chk "small-ping-$m" "$r" 0
  [ "$r" = 0 ] || { echo "ABORT: path dead after MTU $m (deaf port?); needs cold boot"; break; }
  sz=$((m-28))
  r=$(g1 "ping -M do -c 3 -W 2 -s $sz -I $SRC_IP $GEN2_IP >/dev/null 2>&1; echo \$?")
  chk "df-ping-$sz" "$r" 0
  g1 "nohup iperf3 -c $GEN2_IP -B $SRC_IP -P 4 -t 14 -J >/tmp/mtu.json 2>&1 &"
  sleep 7; H0=$(hits); sleep 3; H1=$(hits); sleep 8
  [ "$((H1-H0))" -gt 10000 ] && echo "PASS hw-hits-$m (+$((H1-H0)) pkts/3s)" || { echo "FAIL hw-hits-$m (+$((H1-H0)))"; fail=$((fail+1)); }
  g1 "python3 -c 'import json;j=json.load(open(\"/tmp/mtu.json\"));print(\"iperf3 %.2f Gbit/s\" % (j[\"end\"][\"sum_received\"][\"bits_per_second\"]/1e9))'; rm -f /tmp/mtu.json"
  chk "muram-$m" "$(used)" "$BASE"
done
chk kernel-errors "$(kerr)" "$K0"
d "sudo ask-check 2>&1 | grep -E 'FAIL|Summary' | head -3"
echo "RESULT mtu-steps failures=$fail"; exit $((fail>0))
