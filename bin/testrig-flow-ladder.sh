#!/usr/bin/env bash
# testrig-flow-ladder.sh <dut-ip> <outdir> <flows>... : ASK2 flow-count ladder, harness v2 (plans/ASK2-FLOW-SCALING-PLAN.md S0).
# Runs on the orchestrator (dell2) against the dell1 TRex (stl/ask2_udp.py, ask2_fixed.py).
# Per step: wait for the previous flows to drain; hold N flows at HOLD_RATE per port for HOLD_S; then one TRIAL_S trial
# at TRIAL_RATE per port. A DUT-side sampler streams one line per second for the whole step:
#   epoch phase offloaded_flows cpu_busy_pct running blocked kpath_pps
# offloaded_flows = ASK get-info num-flows / 2 (directions -> flows); kpath_pps = packets the kernel network stack
# processed per second (/proc/net/softnet_stat), i.e. frames that did not stay in silicon. No conntrack dump runs
# during a step. The generator hosts' neighbour entries are pinned on the DUT (TRex answers ARP only in service mode).
# Env: HOLD_RATE=100kpps HOLD_S=120 TRIAL_RATE=1.5mpps TRIAL_S=30 GEN_MAC3/GEN_IP3 GEN_MAC4/GEN_IP4.
set -u
DIP=$1 O=$2; shift 2
HOLD_RATE=${HOLD_RATE:-100kpps} HOLD_S=${HOLD_S:-120} TRIAL_RATE=${TRIAL_RATE:-1.5mpps} TRIAL_S=${TRIAL_S:-30}
GEN_IP3=${GEN_IP3:-10.99.1.112} GEN_MAC3=${GEN_MAC3:-3c:fd:fe:2a:f0:20}
GEN_IP4=${GEN_IP4:-10.99.2.113} GEN_MAC4=${GEN_MAC4:-3c:fd:fe:2a:f0:22}
P=/mnt/builds/wgfp/plan; mkdir -p "$O"; CSV=$O/ladder2.csv
. /mnt/builds/vyos-ls1046a-build/bin/testrig-env.sh
st(){ printf '%s %s\n' "$(date -u +%FT%TZ)" "$*" | tee -a $P/STATUS; }
D="ssh -n -o BatchMode=yes -o ConnectTimeout=20 -o IdentitiesOnly=yes -i $HOME/.ssh/vyos_key vyos@$DIP"
T="ssh -n -o BatchMode=yes -o ConnectTimeout=20 $RIG_SSHK $GEN1"

# DUT-side sampler: bin/ask2-ladder-sampler.py (one line per second; columns documented there).
SAMPLER=$(dirname "$0")/ask2-ladder-sampler.py
scp -q -o IdentitiesOnly=yes -i $HOME/.ssh/vyos_key "$SAMPLER" vyos@$DIP:/tmp/ladder-sampler.py || { st "flowladder2: cannot copy sampler to $DIP"; exit 2; }

$D "sudo ip neigh replace $GEN_IP3 lladdr $GEN_MAC3 dev eth3 nud permanent && sudo ip neigh replace $GEN_IP4 lladdr $GEN_MAC4 dev eth4 nud permanent" \
  || { st "flowladder2: cannot pin generator neighbours on $DIP; stopped"; exit 2; }
[ -s "$CSV" ] || echo "ts,flows,offloaded_max,t_full_s,blocked_max,running_max,cpu_busy_max,hold_kpath_pps_max,trial_kpath_pps_avg,trial_tx,trial_rx,trial_loss_pct,hold_rate,trial_rate,verdict" > "$CSV"
st "flowladder2: start, DUT $DIP, hold $HOLD_RATE x ${HOLD_S}s, trial $TRIAL_RATE x ${TRIAL_S}s, flows $* -> $CSV"
for N in "$@"; do
  for i in $(seq 1 80); do c=$($D 'cat /proc/sys/net/netfilter/nf_conntrack_count'); [ "${c:-99999}" -lt 300 ] && break; sleep 15; done
  S=$O/samples2-$N.txt; $D 'echo hold > /tmp/ladder-phase'
  $D 'sudo python3 /tmp/ladder-sampler.py' > "$S" 2>/dev/null & samp=$!
  t0=$(date +%s)
  $T "cd /opt/trex && sudo timeout $((HOLD_S + 60)) python3 ask2_fixed.py 64 $N $HOLD_RATE $HOLD_S 2>&1 | grep -E '^size'" > "$O/hold2-$N.txt"
  $D 'echo trial > /tmp/ladder-phase'
  r=$($T "cd /opt/trex && sudo timeout $((TRIAL_S + 60)) python3 ask2_fixed.py 64 $N $TRIAL_RATE $TRIAL_S 2>&1 | grep -E '^size'")
  $D 'echo post > /tmp/ladder-phase'; sleep 5; kill $samp 2>/dev/null; wait $samp 2>/dev/null
  $D 'sudo pkill -f "[l]adder-sampler.py"; rm -f /tmp/ladder-phase' >/dev/null 2>&1
  echo "$r" > "$O/trial2-$N.txt"
  set -- $(echo "$r" | awk '{for(i=1;i<=NF;i++){split($i,a,"="); k[a[1]]=a[2]}; d=k["drop"]; sub("%","",d); print k["total_tx"]+0, k["total_rx"]+0, d+0}')
  tx=${1:-0} rx=${2:-0} loss=${3:-100}
  read -r omax tfull bmax rmax cmax hk tk < <(awk -v n=$N -v t0=$t0 '
    $2=="hold"||$2=="trial" { if ($3>om) om=$3; if (tf=="" && $3>=n) tf=$1-t0; if ($6>bm) bm=$6; if ($5>rm) rm=$5; if ($4>cm) cm=$4 }
    $2=="hold"  { if ($7>hk) hk=$7 }
    $2=="trial" { ts+=$7; tc++ }
    END { printf "%d %s %d %d %d %d %d\n", om, (tf==""?"never":tf), bm, rm, cm, hk, (tc?ts/tc:0) }' "$S")
  v=PASS; [ "$omax" -lt "$N" ] && v=FAIL; awk -v d=$loss 'BEGIN{exit !(d>0.001)}' && v=FAIL
  echo "$(date -u +%FT%TZ),$N,$omax,$tfull,$bmax,$rmax,$cmax,$hk,$tk,$tx,$rx,$loss,$HOLD_RATE,$TRIAL_RATE,$v" >> "$CSV"
  st "flowladder2 $N flows: $v offloaded_max=$omax full_after=${tfull}s blocked_max=$bmax cpu_max=$cmax% trial_kpath=${tk}pps loss=${loss}%"
done
st "FLOWLADDER2-DONE $*"
