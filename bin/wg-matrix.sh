#!/usr/bin/env bash
# wg-matrix.sh — full WireGuard throughput matrix for one DUT, then the median table.
#   1 tunnel : bin/testrig-wireguard.sh        (4 TCP streams, a NEW tunnel every rep so serial-worker placement is sampled)
#   2, 4 tunnels : bin/testrig-wireguard-multi.sh (2 TCP streams per tunnel, one tunnel set per row)
#   each x up / down / bidir, IPv4 inner, MTU 1420, 30 s runs (first 10 s discarded)
# Usage: bin/wg-matrix.sh 106|185      Env: REPS1=5 REPSN=3 DUR=30 STEADY_FROM=10 OUTDIR=/mnt/builds/ask2-review/oracle
# .185 only: the VyOS conntrack ignore rules for the tunnel ports are applied through VyOS config for the run (the shipped
# image does this automatically for VyOS-configured WireGuard interfaces; this rig creates the tunnels with `ip link`).
set -u
cd "$(dirname "$0")/.."
WHO=${1:?usage: wg-matrix.sh 106|185}
REPS1=${REPS1:-5}; REPSN=${REPSN:-3}; DUR=${DUR:-30}; STEADY_FROM=${STEADY_FROM:-10}
OUTDIR=${OUTDIR:-/mnt/builds/ask2-review/oracle}; TAG=${TAG:-wgfull}
export DUR STEADY_FROM
case $WHO in
  106) export DUT_KIND=vendor ;;
  185) export DUT_KIND=ask2 ;;
  *) echo "106 or 185"; exit 2 ;;
esac
SSHI="ssh -o BatchMode=yes -i $HOME/.ssh/vyos_key vyos@192.168.1.185"
cfg(){ { echo '#!/bin/vbash'; echo 'source /opt/vyatta/etc/functions/script-template'; echo 'configure'; printf '%s\n' "$1"; echo 'commit'; echo 'exit'; } \
  | $SSHI 'cat > /tmp/cfg.sh; chmod +x /tmp/cfg.sh; /tmp/cfg.sh 2>&1 | tail -2'; }
if [ "$WHO" = 185 ]; then
  $SSHI 'sudo sysctl -qw kernel.softlockup_panic=1 kernel.panic_on_rcu_stall=1'
  trap "cfg 'delete system conntrack ignore'" EXIT
  cfg 'set system conntrack ignore ipv4 rule 10 protocol udp
set system conntrack ignore ipv4 rule 10 destination port 51820-51833
set system conntrack ignore ipv4 rule 20 protocol udp
set system conntrack ignore ipv4 rule 20 source port 51820-51833'
fi
echo "##### $WHO: 1 tunnel, 4 streams, $REPS1 reps with a fresh tunnel each"
REPS=$REPS1 RECREATE=1 DIRS="up down bidir" STREAMS=4 OUT=$OUTDIR/$TAG-$WHO-t1.csv bin/testrig-wireguard.sh run wg-v4 2>&1 | grep -E "^wg-v4|REFUS|ABORT|failed"
for A in 2 4; do
  echo "##### $WHO: $A tunnels, 2 streams each, $REPSN reps"
  REPS=$REPSN DIRS="up down bidir" TUNNELS=$A ACTIVE=$A OUT=$OUTDIR/$TAG-$WHO-t$A.csv bin/testrig-wireguard-multi.sh run 2>&1 | grep -E "^tunnels|REFUS|failed"
done
echo "##### $WHO done"
