#!/usr/bin/env bash
# testrig-ask2-policer.sh — the "policer 1G" row of the ASK2 matrix (plans/ASK2-PERF-MATRIX-PLAN.md).
#   The FMan hardware ingress policer meters a port's ingress, but ASK2 offload bypasses it on an
#   engaged port, so eth3's offload is removed for the run. Upload cells (dell1 -> eth3 -> eth4 ->
#   dell2) run REPS times per family; the table shows the delivered rate (configured BW +-15 % = pass).
# Usage: OUTDIR=<matrix run dir> bin/testrig-ask2-policer.sh    Env: REPS=3 BW=1gbit BURST=<e.g. 1m> TAG=policer DUR=10 STEADY_FROM=3
# Output: policer-r<N>.csv in OUTDIR. Changes the DUT's running config only (never saved) and restores
# eth3 (policer removed, offload ipv4/ipv6 back) on exit. Never run it next to other rig traffic.
set -u
HERE=$(cd "$(dirname "$0")" && pwd)
. "$HERE/testrig-env.sh"
rig_require ASK2_DUT
REPS=${REPS:-3}; BW=${BW:-1gbit}; BURST=${BURST:-}; TAG=${TAG:-policer}
export DUR=${DUR:-10} STEADY_FROM=${STEADY_FROM:-3}
RUN=${OUTDIR:?set OUTDIR to the matrix run directory}
SSHO=(-o BatchMode=yes -o ConnectTimeout=8 $RIG_SSHK)
# Run VyOS config commands through a vbash script (AGENTS.md: never `vbash -c`).
cfg(){
  { echo '#!/bin/vbash'; echo 'source /opt/vyatta/etc/functions/script-template'; echo 'configure'
    printf '%s\n' "$@"; echo 'commit'; echo 'exit'; } |
    ssh "${SSHO[@]}" "$ASK2_DUT" 'cat > /tmp/policer-cfg.sh; chmod +x /tmp/policer-cfg.sh; /tmp/policer-cfg.sh 2>&1 | tail -3'
}
restore(){
  cfg "delete interfaces ethernet eth3 ingress-policer" \
      "set interfaces ethernet eth3 offload ipv4" "set interfaces ethernet eth3 offload ipv6"
  ssh -n "${SSHO[@]}" "$ASK2_DUT" 'echo "eth3 police filters: $(sudo tc filter show dev eth3 ingress | grep -c police)"; /opt/vyatta/bin/vyatta-op-cmd-wrapper show configuration commands | grep -E "eth3 (offload ipv|ingress-policer)"'
}
trap restore EXIT
cfg "delete interfaces ethernet eth3 offload ipv4" "delete interfaces ethernet eth3 offload ipv6" \
    "set interfaces ethernet eth3 ingress-policer bandwidth $BW" \
    ${BURST:+"set interfaces ethernet eth3 ingress-policer burst $BURST"}
sleep 3
ssh -n "${SSHO[@]}" "$ASK2_DUT" 'sudo tc -s filter show dev eth3 ingress | grep -E "police|in_hw|rate" | head -4'
for r in $(seq 1 "$REPS"); do
  echo "##### policer $BW ${BURST:+burst $BURST }${MODE:-tcp} rep $r $(date +%T)"
  if [ "${MODE:-tcp}" = udp ]; then
    # fixed-rate UDP above the policed rate: delivered = sent x (1 - receiver loss)
    for f in 4 6; do
      if [ $f = 4 ]; then src=10.99.1.112; dst=10.99.2.113; else src=fd99:1::112; dst=fd99:2::113; fi
      ssh -n -o BatchMode=yes $RIG_SSHK "$GEN1" "iperf3 -c $dst -B $src -p 5201 -u -b ${UDP_RATE:-2G} -l 1400 -t $DUR -J" |
        python3 -c 'import json,sys; e=json.load(sys.stdin)["end"]["sum"]; print("%s udp sent %.3f delivered %.3f Gbit/s loss %.1f%%" % (sys.argv[1], e["bits_per_second"]/1e9, e["bits_per_second"]*(1-e["lost_percent"]/100)/1e9, e["lost_percent"]))' "unicast-v$f" |
        tee -a "$RUN/$TAG-udp.txt"
    done
  else
    DUT_KIND=ask2 OUT=$RUN/$TAG-r$r.csv J=$RUN/json-$TAG-r$r "$HERE/testrig-offload-quick.sh" unicast-v4 unicast-v6
  fi
done
echo "##### policer done $(date +%T)"
