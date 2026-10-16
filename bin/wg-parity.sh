#!/usr/bin/env bash
# wg-parity.sh — the WireGuard part of the ASK2-vs-vendor parity battery for one DUT.
#   tunnel on eth3 and on eth4 (WG_PORT)  x  inner IPv4 and IPv6  x  up / down / bidir
#   1 tunnel, 4 TCP streams, MTU 1420, 30 s runs (first 10 s discarded), a NEW tunnel every rep
#   (the serial tx worker's CPU rotates with each new wg0, so single-tunnel results are bimodal).
#   up = the DUT encrypts, down = the DUT decrypts, bidir = both at once.
# Usage: bin/wg-parity.sh vendor|ask2  (106|185 accepted)      Env: REPS=3 PORTS="eth3 eth4" DUR=30 STEADY_FROM=10 OUTDIR=... TAG=wgpar
#   DIRS="up down bidir" CELLS="wg-v4 wg-v6" narrow the run; STREAMS=4 sets the iperf3 stream count; PROF=1 also writes a
#   per-thread/softirq profile per run (bin/wg-prof-summary.py)
# Output: $OUTDIR/$TAG-<board>-<port>.csv ; the table: bin/wg-parity-table.py [outdir] [tag]
# Never run it next to other rig traffic. Hosts and key: bin/testrig-env.sh. ask2 only: the VyOS conntrack ignore rules for the tunnel ports are applied
# through VyOS config for the run, as bin/wg-matrix.sh does (the shipped image does it for VyOS-configured WireGuard).
set -u
HERE=$(cd "$(dirname "$0")" && pwd)
. "$HERE/testrig-env.sh"
WHO=${1:?usage: wg-parity.sh vendor|ask2}
case $WHO in vendor) WHO=106 ;; ask2) WHO=185 ;; esac   # file labels stay 106/185 for wg-parity-table.py
RUN_ON=${RUN_ON:-agent}
REPS=${REPS:-3}; PORTS=${PORTS:-"eth3 eth4"}; DUR=${DUR:-30}; STEADY_FROM=${STEADY_FROM:-10}
OUTDIR=${OUTDIR:-$RIG_OUT}
TAG=${TAG:-wgpar}
export DUR STEADY_FROM RUN_ON OUTDIR
case $WHO in
  106) export DUT_KIND=vendor ;;
  185) export DUT_KIND=ask2 ;;
  *) echo "vendor or ask2"; exit 2 ;;
esac
if [ "$WHO" = 185 ]; then rig_require ASK2_DUT; fi
SSHI="ssh -o BatchMode=yes $RIG_SSHK ${ASK2_DUT:-}"
cfg(){ { echo '#!/bin/vbash'; echo 'source /opt/vyatta/etc/functions/script-template'; echo 'configure'; printf '%s\n' "$1"; echo 'commit'; echo 'exit'; } \
  | $SSHI 'cat > /tmp/cfg.sh; chmod +x /tmp/cfg.sh; /tmp/cfg.sh 2>&1 | tail -2'; }
if [ "$WHO" = 185 ]; then
  $SSHI 'sudo sysctl -qw kernel.softlockup_panic=1 kernel.panic_on_rcu_stall=1'
  trap "cfg 'delete system conntrack ignore'; $SSHI 'sudo sysctl -qw kernel.softlockup_panic=0 kernel.panic_on_rcu_stall=0'" EXIT
  cfg 'set system conntrack ignore ipv4 rule 10 protocol udp
set system conntrack ignore ipv4 rule 10 destination port 51820-51833
set system conntrack ignore ipv4 rule 20 protocol udp
set system conntrack ignore ipv4 rule 20 source port 51820-51833'
fi
for P in $PORTS; do
  echo "##### $WHO: tunnel on $P, 1 tunnel, 4 streams, $REPS reps with a fresh tunnel each, $(date +%T)"
  WG_PORT=$P REPS=$REPS RECREATE=1 DIRS="${DIRS:-up down bidir}" STREAMS=${STREAMS:-4} OUT=$OUTDIR/$TAG-$WHO-$P.csv \
    "$HERE/testrig-wireguard.sh" run ${CELLS:-wg-v4 wg-v6} 2>&1 | grep -E "^wg-v|REFUS|ABORT|failed|SKIP|no WireGuard|handshake"
done
echo "##### $WHO done $(date +%T)"
