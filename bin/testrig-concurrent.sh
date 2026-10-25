#!/bin/bash
# testrig-concurrent.sh: P4 concurrent eth3<->eth4 load on the ASK2 DUT.
# Baseline: gen1 -> gen2 alone. Then iperf3 --bidir (both directions at once, 4 streams
# each) for DUR s. Pass: each direction >= MIN_FRAC of the single-direction rate, hardware
# hits grow, MURAM returns to baseline, no kernel errors, ask-check clean.
#   bin/testrig-concurrent.sh      (hosts and key from bin/testrig-env.sh)
# Never run while the A8 soak owns the rig.
set -u
. "$(dirname "$0")/testrig-env.sh"
DUT=${DUT:-$(rig_dut ask2)} || exit 2
rig_require GEN1
DUR=${DUR:-30}; MIN_FRAC=${MIN_FRAC:-0.90}
GEN2_IP=${GEN2_IP:-10.99.2.113}; SRC_IP=${SRC_IP:-10.99.1.112}
SSHO="-o BatchMode=yes -o ConnectTimeout=8"
d(){ ssh -n $SSHO $RIG_SSHK "$DUT" "$@"; }
g1(){ ssh -n $SSHO $RIG_SSHK "$GEN1" "$@"; }
used(){ d "sudo cat /sys/kernel/debug/fman_pcd/0/muram_budget | awk '/^used/{print \$2}'"; }
hits(){ d "sudo cat /sys/kernel/debug/fman_pcd/0/fe_ehash_stats" | sed -n 's/.*pkt_count=\([0-9]*\).*/\1/p' | awk '{s+=$1}END{print s+0}'; }
kerr(){ d "sudo dmesg | tail -300 | grep -ciE 'oops|BUG:|WARNING:|call trace'"; }
fail=0
chk(){ if [ "$2" = "$3" ]; then echo "PASS $1 ($3)"; else echo "FAIL $1: got '$2' want '$3'"; fail=$((fail+1)); fi; }
rate(){ python3 -c '
import json,sys
j=json.load(sys.stdin); e=j["end"]
print("%.2f %.2f %d" % (e.get("sum_sent",{}).get("bits_per_second",0)/1e9, e.get("sum_received",{}).get("bits_per_second",0)/1e9, e.get("sum_sent",{}).get("retransmits",0)))
'; }

BASE=$(used); K0=$(kerr)
echo "== single direction gen1 -> gen2"
S=$(g1 "iperf3 -c $GEN2_IP -B $SRC_IP -P 4 -t $DUR -O 3 -J" | python3 -c '
import json,sys
j=json.load(sys.stdin); print("%.2f" % (j["end"]["sum_received"]["bits_per_second"]/1e9))')
echo "single: $S Gbit/s"

echo "== bidirectional"
g1 "rm -f /tmp/conc.json; nohup iperf3 -c $GEN2_IP -B $SRC_IP -P 4 --bidir -t $DUR -O 3 -J >/tmp/conc.json 2>&1 &"
sleep 10; H0=$(hits); sleep 3; H1=$(hits)
sleep $((DUR-13+6))
J=$(g1 "cat /tmp/conc.json; rm -f /tmp/conc.json")
echo "$J" | python3 -c '
import json,sys
j=json.load(sys.stdin); e=j["end"]
print("fwd(gen1->gen2): %.2f Gbit/s  rev(gen2->gen1): %.2f Gbit/s" % (e["sum_received"]["bits_per_second"]/1e9, e["sum_received_bidir_reverse"]["bits_per_second"]/1e9))
print("rev:", round(e["sum_received_bidir_reverse"]["bits_per_second"]/1e9,2))' | tee /tmp/conc.out
F=$(sed -n 's/^fwd.*: \([0-9.]*\) Gbit\/s  rev.*: \([0-9.]*\) Gbit.*/\1/p' /tmp/conc.out)
R=$(sed -n 's/^fwd.*: \([0-9.]*\) Gbit\/s  rev.*: \([0-9.]*\) Gbit.*/\2/p' /tmp/conc.out)
for v in "fwd $F" "rev $R"; do
  set -- $v
  ok=$(python3 -c "print(1 if $2 >= $MIN_FRAC*$S else 0)")
  [ "$ok" = 1 ] && echo "PASS $1 rate $2 >= ${MIN_FRAC}x$S" || { echo "FAIL $1 rate $2 < ${MIN_FRAC}x$S"; fail=$((fail+1)); }
done
[ "$((H1-H0))" -gt 10000 ] && echo "PASS hw-hits grew mid-run (+$((H1-H0)) pkts/3s)" || { echo "FAIL hw-hits flat"; fail=$((fail+1)); }
sleep 3
chk muram-after "$(used)" "$BASE"
chk kernel-errors "$(kerr)" "$K0"
d "sudo ask-check 2>&1 | grep -E 'FAIL|Summary' | head -3"
rm -f /tmp/conc.out
echo "RESULT concurrent failures=$fail"; exit $((fail>0))
