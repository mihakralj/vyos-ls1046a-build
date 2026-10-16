#!/usr/bin/env bash
# wg-rps-ab.sh — A/B of the RPS/RFS configuration on .185 for the WireGuard cells (wg-parity.sh).
# Run it ON dell2 (scripts live in ~/testrig); every ssh to the DUT originates there.
# Usage: wg-rps-ab.sh VARIANT [wg-parity args]      VARIANT = default | vendorlike | M3:M4 (hex rps_cpus masks for eth3:eth4, RFS off)
#   default    = leave the shipped VyOS setting (rps_cpus=e, rps_sock_flow_entries=32768) untouched
#   vendorlike = eth3 rps_cpus=2, eth4 rps_cpus=1, rps_sock_flow_entries=0 (what .106 does: one CPU per port, RFS off)
# Env: as wg-parity.sh (REPS, PORTS, DIRS, CELLS, TAG, ...). TAG defaults to wgrps-<variant>.
# The original per-queue rps_cpus / rps_flow_cnt and rps_sock_flow_entries are saved on the DUT and restored on exit.
set -u
HERE=$(cd "$(dirname "$0")" && pwd)
V=${1:?usage: wg-rps-ab.sh default|vendorlike|M3:M4}
SSHI="ssh -n -o BatchMode=yes -i $HOME/.ssh/vyos_key vyos@192.168.1.185"
SAVE=/tmp/wg-rps-ab.save
case $V in
  default) M3=; M4= ;;
  vendorlike) M3=2; M4=1 ;;
  *:*) M3=${V%%:*}; M4=${V##*:} ;;
  *) echo "bad variant $V"; exit 2 ;;
esac
export RUN_ON=dell2 TAG=${TAG:-wgrps-${V//:/-}}
dump(){ $SSHI 'for d in eth3 eth4; do for q in /sys/class/net/$d/queues/rx-*; do echo "$d $(basename $q) $(cat $q/rps_cpus) $(cat $q/rps_flow_cnt)"; done; done; echo "sock $(cat /proc/sys/net/core/rps_sock_flow_entries)"'; }
restore(){
  [ -n "$M3$M4" ] || return 0
  $SSHI "while read d q c f; do if [ \$d = sock ]; then echo \$q | sudo tee /proc/sys/net/core/rps_sock_flow_entries >/dev/null; else echo \$f | sudo tee /sys/class/net/\$d/queues/\$q/rps_flow_cnt >/dev/null; echo \$c | sudo tee /sys/class/net/\$d/queues/\$q/rps_cpus >/dev/null; fi; done < $SAVE"
  if [ "$(dump | md5sum)" = "$ORIG_SUM" ]; then echo "RPS restored (identical to the saved state)"; else echo "WARNING: RPS state differs from the saved one"; fi
}
ORIG=$(dump); ORIG_SUM=$(printf '%s\n' "$ORIG" | md5sum)
printf '%s\n' "$ORIG" | ssh -o BatchMode=yes -i "$HOME/.ssh/vyos_key" vyos@192.168.1.185 "cat > $SAVE"
[ "$($SSHI "wc -l < $SAVE")" -gt 0 ] || { echo "refusing: RPS save file is empty"; exit 3; }
echo "original: $(printf '%s\n' "$ORIG" | awk '$1=="eth3"&&$2=="rx-0"||$1=="eth4"&&$2=="rx-0"||$1=="sock"{printf "%s ",$0}')"
if [ -n "$M3$M4" ]; then
  trap restore EXIT
  $SSHI "echo 0 | sudo tee /proc/sys/net/core/rps_sock_flow_entries >/dev/null
for q in /sys/class/net/eth3/queues/rx-*; do echo 0 | sudo tee \$q/rps_flow_cnt >/dev/null; echo $M3 | sudo tee \$q/rps_cpus >/dev/null; done
for q in /sys/class/net/eth4/queues/rx-*; do echo 0 | sudo tee \$q/rps_flow_cnt >/dev/null; echo $M4 | sudo tee \$q/rps_cpus >/dev/null; done"
  echo "applied: eth3 rps_cpus=$M3 eth4 rps_cpus=$M4 rps_sock_flow_entries=0"
fi
"$HERE/wg-parity.sh" 185
