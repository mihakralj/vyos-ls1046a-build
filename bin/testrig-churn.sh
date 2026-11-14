#!/usr/bin/env bash
# testrig-churn.sh <dut-ip> <outdir> : ASK2 mice-churn matrix (plans/ASK2-FLOW-SCALING-PLAN.md §3.0, S1 churn).
# Runs on the orchestrator (dell2) against the dell1 TRex (bin/trex/ask2_churn.py + ask2_churn_run.py installed in
# /opt/trex). For each age gate (ask.offload_delay_ms) in DELAYS and each mice rate in RATES: wait for conntrack to
# drain, start bin/ask2-ladder-sampler.py on the DUT, run 4 elephants (1518 B, ELEPH_PPS per direction) from t=0 and
# DNS-like mice (one query + one reply per new flow) for SECS from t=8 s. One CSV row per run: elephant loss over the
# mice window and the DUT's view of that window (offloaded flows, conntrack size and insert rate, CPU, blocked tasks,
# kernel-path packets). The age gate is restored to its starting value at the end.
# Env: DELAYS="500 0" RATES="0 5000 20000" SECS=60 ELEPH_PPS=200000
set -u
DIP=$1 O=$2
DELAYS=${DELAYS:-500 0} RATES=${RATES:-0 5000 20000} SECS=${SECS:-60} ELEPH_PPS=${ELEPH_PPS:-200000}
P=/mnt/builds/wgfp/plan; mkdir -p "$O"; CSV=$O/churn.csv
. /mnt/builds/vyos-ls1046a-build/bin/testrig-env.sh
st(){ printf '%s %s\n' "$(date -u +%FT%TZ)" "$*" | tee -a $P/STATUS; }
D="ssh -n -o BatchMode=yes -o ConnectTimeout=20 -o IdentitiesOnly=yes -i $HOME/.ssh/vyos_key vyos@$DIP"
T="ssh -n -o BatchMode=yes -o ConnectTimeout=20 $RIG_SSHK $GEN1"
scp -q -o IdentitiesOnly=yes -i $HOME/.ssh/vyos_key "$(dirname "$0")/ask2-ladder-sampler.py" vyos@$DIP:/tmp/ladder-sampler.py \
  || { st "churn: cannot copy sampler to $DIP"; exit 2; }
$D "sudo ip neigh replace ${GEN_IP3:-10.99.1.112} lladdr ${GEN_MAC3:-3c:fd:fe:2a:f0:20} dev eth3 nud permanent && sudo ip neigh replace ${GEN_IP4:-10.99.2.113} lladdr ${GEN_MAC4:-3c:fd:fe:2a:f0:22} dev eth4 nud permanent" \
  || { st "churn: cannot pin generator neighbours"; exit 2; }
DELAY0=$($D 'cat /sys/module/ask/parameters/offload_delay_ms')
[ -s "$CSV" ] || echo "ts,delay_ms,mice_rate,secs,eleph_tx,eleph_rx,eleph_loss_pct,total_loss_pct,offloaded_avg,offloaded_max,ct_max,ct_inserts_avg,cpu_avg,cpu_max,blocked_max,kpath_pps_avg" > "$CSV"
st "churn: start, DUT $DIP, delays [$DELAYS], mice rates [$RATES], ${SECS}s windows -> $CSV"
for d in $DELAYS; do for r in $RATES; do
  for i in $(seq 1 80); do c=$($D 'cat /proc/sys/net/netfilter/nf_conntrack_count'); [ "${c:-99999}" -lt 300 ] && break; sleep 15; done
  $D "echo $d | sudo tee /sys/module/ask/parameters/offload_delay_ms >/dev/null; echo warm > /tmp/ladder-phase"
  S=$O/samples-d$d-r$r.txt
  $D 'sudo python3 /tmp/ladder-sampler.py' > "$S" 2>/dev/null & samp=$!
  ( sleep 8; $D 'echo mice > /tmp/ladder-phase' ) &
  out=$($T "cd /opt/trex && sudo timeout $((SECS + 90)) python3 ask2_churn_run.py $r $SECS $ELEPH_PPS 2>&1 | grep -E '^(eleph|total) '")
  $D 'echo post > /tmp/ladder-phase'; sleep 3; kill $samp 2>/dev/null; wait $samp 2>/dev/null
  $D 'sudo pkill -f "[l]adder-sampler.py"; rm -f /tmp/ladder-phase' >/dev/null 2>&1
  echo "$out" > "$O/trex-d$d-r$r.txt"
  el=$(echo "$out" | awk '/^eleph/{for(i=1;i<=NF;i++){split($i,a,"="); k[a[1]]=a[2]}; print k["tx"]+0, k["rx"]+0, k["loss_pct"]+0}')
  tl=$(echo "$out" | awk '/^total/{for(i=1;i<=NF;i++){split($i,a,"="); k[a[1]]=a[2]}; print k["loss_pct"]+0}')
  set -- ${el:-0 0 100}
  m=$(awk '$2=="mice" { n++; of+=$3; if($3>om) om=$3; if($8>cm) cm=$8; ins+=$9; cpu+=$4; if($4>cx) cx=$4; if($6>bm) bm=$6; kp+=$7 }
      END { if(!n) n=1; printf "%.0f,%d,%d,%.0f,%.0f,%d,%d,%.0f", of/n, om, cm, ins/n, cpu/n, cx, bm, kp/n }' "$S")
  echo "$(date -u +%FT%TZ),$d,$r,$SECS,$1,$2,$3,${tl:-100},$m" >> "$CSV"
  st "churn delay=${d}ms mice=${r}/s: eleph_loss=$3% total_loss=${tl:-?}% [offl_avg,offl_max,ct_max,ct_ins/s,cpu_avg,cpu_max,blocked_max,kpath] $m"
done; done
$D "echo $DELAY0 | sudo tee /sys/module/ask/parameters/offload_delay_ms >/dev/null"
st "CHURN-DONE (offload_delay_ms restored to $DELAY0)"
