#!/usr/bin/env bash
# testrig-flow-ladder.sh <dut-ip> <outdir> <flows>... : ASK2 flow-count ladder, harness v3 (plans/ASK2-FLOW-SCALING-PLAN.md S0).
# Runs on the orchestrator (dell2) against the dell1 TRex (stl/ask2_udp.py, ask2_fixed.py).
# Per step: wait for the previous flows to drain; hold N flows at HOLD_RATE per port for HOLD_S; then one TRIAL_S trial
# at TRIAL_RATE per port. A DUT-side sampler streams one line per second for the whole step:
#   epoch phase offloaded_flows cpu_busy_pct running blocked kpath_pps
# offloaded_flows = ASK get-info num-flows / 2 (directions -> flows); kpath_pps = packets the kernel network stack
# processed per second (/proc/net/softnet_stat), i.e. frames that did not stay in silicon. No conntrack dump runs
# during a step. The generator hosts' neighbour entries are pinned on the DUT (TRex answers ARP only in service mode).
# v3 (ladder3.csv): full-after is also counted from the first packet (first hold sample with kpath_pps > 1000), the
# hold (setup) loss is reported, the sampler runs on after the trial until offloaded_flows reaches 0 (teardown drain_s,
# capped at DRAIN_MAX), each step runs REPS times, and a step above the DUT's ask max_flows budget passes when the
# budget is full and the trial loses nothing (the flows over budget are forwarded in software).
# Env: HOLD_RATE=100kpps HOLD_S=120 TRIAL_RATE=1.5mpps TRIAL_S=30 REPS=1 DRAIN_MAX=300 GEN_MAC3/GEN_IP3 GEN_MAC4/GEN_IP4.
set -u
DIP=$1 O=$2; shift 2
HOLD_RATE=${HOLD_RATE:-100kpps} HOLD_S=${HOLD_S:-120} TRIAL_RATE=${TRIAL_RATE:-1.5mpps} TRIAL_S=${TRIAL_S:-30}
REPS=${REPS:-1} DRAIN_MAX=${DRAIN_MAX:-300}
GEN_IP3=${GEN_IP3:-10.99.1.112} GEN_MAC3=${GEN_MAC3:-3c:fd:fe:2a:f0:20}
GEN_IP4=${GEN_IP4:-10.99.2.113} GEN_MAC4=${GEN_MAC4:-3c:fd:fe:2a:f0:22}
P=/mnt/builds/wgfp/plan; mkdir -p "$O"; CSV=$O/ladder3.csv
. /mnt/builds/vyos-ls1046a-build/bin/testrig-env.sh
st(){ printf '%s %s\n' "$(date -u +%FT%TZ)" "$*" | tee -a $P/STATUS; }
D="ssh -n -o BatchMode=yes -o ConnectTimeout=20 -o IdentitiesOnly=yes -i $HOME/.ssh/vyos_key vyos@$DIP"
T="ssh -n -o BatchMode=yes -o ConnectTimeout=20 $RIG_SSHK $GEN1"
kv(){ awk -v k="$2" '{for(i=1;i<=NF;i++){split($i,a,"="); if(a[1]==k){v=a[2]; sub("%","",v); print v+0}}}' <<< "$1"; }

# DUT-side sampler: bin/ask2-ladder-sampler.py (one line per second; columns documented there).
SAMPLER=$(dirname "$0")/ask2-ladder-sampler.py
scp -q -o IdentitiesOnly=yes -i $HOME/.ssh/vyos_key "$SAMPLER" vyos@$DIP:/tmp/ladder-sampler.py || { st "flowladder3: cannot copy sampler to $DIP"; exit 2; }

$D "sudo ip neigh replace $GEN_IP3 lladdr $GEN_MAC3 dev eth3 nud permanent && sudo ip neigh replace $GEN_IP4 lladdr $GEN_MAC4 dev eth4 nud permanent" \
  || { st "flowladder3: cannot pin generator neighbours on $DIP; stopped"; exit 2; }
BUDGET=$(( $($D 'cat /sys/module/ask/parameters/max_flows') / 2 ))   # directions -> flows; 0 = unlimited
[ -s "$CSV" ] || echo "ts,flows,rep,expected,offloaded_max,t_full_s,t_full_from_first_s,blocked_max,running_max,cpu_busy_max,hold_cpu_avg,hold_kpath_pps_max,trial_kpath_pps_avg,hold_lost,hold_loss_pct,trial_tx,trial_rx,trial_loss_pct,drain_s,hold_rate,trial_rate,verdict" > "$CSV"
st "flowladder3: start, DUT $DIP, hold $HOLD_RATE x ${HOLD_S}s, trial $TRIAL_RATE x ${TRIAL_S}s, reps $REPS, budget $BUDGET flows, flows $* -> $CSV"
for N in "$@"; do for rep in $(seq 1 "$REPS"); do
  E=$N; [ "$BUDGET" -gt 0 ] && [ "$N" -gt "$BUDGET" ] && E=$BUDGET
  for i in $(seq 1 80); do c=$($D 'cat /proc/sys/net/netfilter/nf_conntrack_count'); [ "${c:-99999}" -lt 300 ] && break; sleep 15; done
  S=$O/samples3-$N-r$rep.txt; $D 'echo hold > /tmp/ladder-phase'
  $D 'sudo python3 /tmp/ladder-sampler.py' > "$S" 2>/dev/null & samp=$!
  t0=$(date +%s)
  h=$($T "cd /opt/trex && sudo timeout $((HOLD_S + 60)) python3 ask2_fixed.py 64 $N $HOLD_RATE $HOLD_S 2>&1 | grep -E '^size'")
  echo "$h" > "$O/hold3-$N-r$rep.txt"
  $D 'echo trial > /tmp/ladder-phase'
  r=$($T "cd /opt/trex && sudo timeout $((TRIAL_S + 60)) python3 ask2_fixed.py 64 $N $TRIAL_RATE $TRIAL_S 2>&1 | grep -E '^size'")
  echo "$r" > "$O/trial3-$N-r$rep.txt"
  # teardown: traffic has stopped; time until the last record is withdrawn
  $D 'echo drain > /tmp/ladder-phase'; td=$(date +%s)
  for i in $(seq 1 "$DRAIN_MAX"); do sleep 1; tail -1 "$S" | awk '$2=="drain" && $3==0 {f=1} END{exit !f}' && break; done
  drain=$(awk -v td=$td '$2=="drain" && $3==0 {print $1-td; exit}' "$S"); drain=${drain:-">$DRAIN_MAX"}
  kill $samp 2>/dev/null; wait $samp 2>/dev/null
  $D 'sudo pkill -f "[l]adder-sampler.py"; rm -f /tmp/ladder-phase' >/dev/null 2>&1
  tx=$(kv "$r" total_tx) rx=$(kv "$r" total_rx) loss=$(kv "$r" drop); tx=${tx:-0} rx=${rx:-0} loss=${loss:-100}
  hlost=$(kv "$h" lost) hloss=$(kv "$h" drop)
  read -r omax tfull tfirst bmax rmax cmax hcpu hk tk < <(awk -v n=$E -v t0=$t0 '
    $2=="hold" && $7>1000 && t1=="" { t1=$1 }
    $2=="hold"||$2=="trial" { if ($3>om) om=$3; if (tf=="" && $3>=n) { tf=$1-t0; tfa=$1 }; if ($6>bm) bm=$6; if ($5>rm) rm=$5; if ($4>cm) cm=$4 }
    $2=="hold"  { if ($7>hk) hk=$7; hc+=$4; hn++ }
    $2=="trial" { ts+=$7; tc++ }
    END { printf "%d %s %s %d %d %d %.1f %d %d\n", om, (tf==""?"never":tf), (tf==""||t1==""?"never":tfa-t1), bm, rm, cm, (hn?hc/hn:0), hk, (tc?ts/tc:0) }' "$S")
  v=PASS; [ "$omax" -lt "$E" ] && v=FAIL; awk -v d=$loss 'BEGIN{exit !(d>0.001)}' && v=FAIL
  echo "$(date -u +%FT%TZ),$N,$rep,$E,$omax,$tfull,$tfirst,$bmax,$rmax,$cmax,$hcpu,$hk,$tk,${hlost:-},${hloss:-},$tx,$rx,$loss,$drain,$HOLD_RATE,$TRIAL_RATE,$v" >> "$CSV"
  st "flowladder3 $N flows rep $rep: $v offloaded_max=$omax/$E full_after=${tfull}s (${tfirst}s from first packet) blocked_max=$bmax hold_loss=${hloss}% trial_loss=${loss}% drain=${drain}s"
done; done
st "FLOWLADDER3-DONE $*"
