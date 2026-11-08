#!/usr/bin/env bash
# testrig-ask2-matrix.sh — ASK2-only flow-type matrix (plans/ASK2-PERF-MATRIX-PLAN.md, Layer A, first cut).
#   unicast, nat, vlan-vlan, vlan-port, pppoe: bin/testrig-offload-quick.sh cells, unidir then BIDIR=1
#   wireguard: bin/wg-parity.sh ask2 (1 tunnel on eth4, fresh tunnel per rep)
#   ipsec, bridge, broadcast, multicast, fragments, policer: not wired yet, printed as "-"
# Every cell runs REPS short reps (DUR s, steady window from STEADY_FROM s); the table keeps the FASTEST rep.
# Usage: bin/testrig-ask2-matrix.sh            Env: REPS=3 WG_REPS=$REPS DUR=10 STEADY_FROM=3 BIDIR_DUR=20 BIDIR_STEADY_FROM=12 WGFP=1|0 OUTDIR=...
# Output: the table on stdout; raw CSVs, JSON and matrix.csv (with verdicts) in $OUTDIR/ask2-matrix-<stamp>/.
# Never run it next to other rig traffic.
set -u
HERE=$(cd "$(dirname "$0")" && pwd)
. "$HERE/testrig-env.sh"
rig_require ASK2_DUT
REPS=${REPS:-3}; WG_REPS=${WG_REPS:-$REPS}
export DUR=${DUR:-10} STEADY_FROM=${STEADY_FROM:-3}
# Bidir cells run longer and measure late: ask.ko's adaptive RX alignment needs ~4-8 s to
# retune a port when the edit class changes between cells (vlan-port <-> the others).
BIDIR_DUR=${BIDIR_DUR:-20}; BIDIR_STEADY_FROM=${BIDIR_STEADY_FROM:-12}
# WireGuard offload follows the port offload, so the ASK2 row measures the fast path (WGFP=0: software A/B).
export WGFP=${WGFP:-1}
RUN=${OUTDIR:-$RIG_OUT}/ask2-matrix-$(date +%Y%m%d-%H%M)
mkdir -p "$RUN"
UNI="unicast-v4 unicast-v6 unicast-v4-rev unicast-v6-rev nat-v4 nat-v6 nat-v4-rev nat-v6-rev
vlan-v4 vlan-v6 vlan-v4-rev vlan-v6-rev vlanport-v4 vlanport-v6 portvlan-v4 portvlan-v6
pppoe-up-v4 pppoe-up-v6 pppoe-down-v4 pppoe-down-v6"
BI="unicast-v4 unicast-v6 nat-v4 nat-v6 vlan-v4 vlan-v6 vlanport-v4 vlanport-v6 pppoe-up-v4 pppoe-up-v6"
for r in $(seq 1 "$REPS"); do
  echo "##### quick unidir rep $r $(date +%T)"
  DUT_KIND=ask2 OUT=$RUN/quick-uni-r$r.csv J=$RUN/json-r$r "$HERE/testrig-offload-quick.sh" $UNI
  echo "##### quick bidir rep $r $(date +%T)"
  DUT_KIND=ask2 BIDIR=1 DUR=$BIDIR_DUR STEADY_FROM=$BIDIR_STEADY_FROM OUT=$RUN/quick-bidir-r$r.csv J=$RUN/json-r$r "$HERE/testrig-offload-quick.sh" $BI
done
echo "##### wireguard $(date +%T)"
OUTDIR=$RUN REPS=$WG_REPS PORTS=eth4 TAG=wg CELLS="wg-v4 wg-v6" DIRS="up down bidir" "$HERE/wg-parity.sh" ask2
echo "##### done $(date +%T), raw results in $RUN"
python3 "$HERE/ask2-matrix-table.py" "$RUN"
