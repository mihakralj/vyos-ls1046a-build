#!/usr/bin/env bash
# testrig-wireguard-gro-check.sh — validate dpaa RX GRO (patch 4012) on .185 with 4 WireGuard tunnels.
#   A: recipe applied (conntrack ignore for the outer ports), GRO on  eth3/eth4
#   B: same, GRO off on eth3/eth4
# Sets kernel.softlockup_panic=1 and panic_on_rcu_stall=1 first, so a lockup panics and the board reboots after
# panic=60 with the trace preserved in pstore (ls /sys/fs/pstore) instead of staying hung.
# Also prints the kernel, lockdep state and recent kernel warnings, and restores everything on exit.
# Usage: bin/testrig-wireguard-gro-check.sh      Env: SERIAL_CPUS=10 (wireguard.serial_cpus bitmask), REPS=3
set -u
cd "$(dirname "$0")/.."
REPS=${REPS:-3}; SERIAL_CPUS=${SERIAL_CPUS:-10}
SSHI="ssh -o BatchMode=yes -i $HOME/.ssh/vyos_key vyos@192.168.1.185"
SSHV="ssh -n -o BatchMode=yes -i $HOME/.ssh/vyos_key vyos@192.168.1.185"
D1="ssh -n -o BatchMode=yes admin@192.168.1.112"
cfg(){ { echo '#!/bin/vbash'; echo 'source /opt/vyatta/etc/functions/script-template'; echo 'configure'; printf '%s\n' "$1"; echo 'commit'; echo 'exit'; } | $SSHI 'cat > /tmp/cfg.sh; chmod +x /tmp/cfg.sh; /tmp/cfg.sh 2>&1 | tail -2'; }
IGN='set system conntrack ignore ipv4 rule 10 protocol udp
set system conntrack ignore ipv4 rule 10 destination port 51830-51833
set system conntrack ignore ipv4 rule 20 protocol udp
set system conntrack ignore ipv4 rule 20 source port 51830-51833'
restore(){ $SSHV 'sudo ethtool -K eth3 gro on; sudo ethtool -K eth4 gro on'; cfg 'delete system conntrack ignore'; TUNNELS=4 bin/testrig-wireguard-multi.sh teardown; }
trap restore EXIT
$SSHV 'sudo sysctl -qw kernel.softlockup_panic=1 kernel.panic_on_rcu_stall=1; echo "kernel: $(uname -r) $(uname -v | cut -c1-40)"; echo "lockdep: $([ -e /proc/lockdep ] && echo PRESENT || echo absent)"; sudo dmesg | grep -iE "warn|bug|oops|dpaa.*err|rx.*drop" | tail -3; echo "gro: $(sudo ethtool -k eth3 | grep generic-receive)"; echo '"$SERIAL_CPUS"' | sudo tee /sys/module/wireguard/parameters/serial_cpus >/dev/null; cat /sys/module/wireguard/parameters/serial_cpus'
KEEP=1 TUNNELS=4 bin/testrig-wireguard-multi.sh setup >/dev/null
cfg "$IGN"
m(){ for r in $(seq 1 $REPS); do
  for k in 0 1 2 3; do $D1 "iperf3 -c 10.99.$((210+k)).113 -B 10.99.1.112 -p $((5240+k)) -P 2 -t 20 -Z -J" > /tmp/g4t-$k.json & done; wait
  python3 - "$1" $r <<'PY'
import json,sys
t=0
for k in range(4):
    j=json.load(open('/tmp/g4t-%d.json'%k)); iv=[i['sum']['bits_per_second'] for i in j['intervals'] if i['sum']['start']>=6]; t+=sum(iv)/len(iv)/1e9
print("%-36s rep%s %.3f Gbit/s aggregate"%(sys.argv[1],sys.argv[2],t))
PY
  sleep 4; done; }
m "A gro on (eth3,eth4)"
$SSHV 'sudo ethtool -K eth3 gro off; sudo ethtool -K eth4 gro off'
m "B gro off"
$SSHV 'sudo dmesg | grep -iE "warn|bug|oops" | tail -3'
