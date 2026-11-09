#!/usr/bin/env bash
# testrig-ask2-9k.sh — the -9k rows of the ASK2 matrix (plans/ASK2-PERF-MATRIX-PLAN.md): unicast, nat,
#   vlan-vlan and vlan-port at MTU 9000 on every endpoint, REPS fastest-of runs, unidir then BIDIR=1.
#   PPPoE is excluded (needs RFC 4638 on both ends).
# Usage: OUTDIR=<matrix run dir> bin/testrig-ask2-9k.sh     Env: REPS=3 MTU=9000 DUR=10 STEADY_FROM=3
# Output: quick9k-uni-r<N>.csv / quick9k-bidir-r<N>.csv in OUTDIR (bin/ask2-matrix-table.py reads them).
# MTU is changed at runtime only and restored to 1500 on exit. Never run it next to other rig traffic.
set -u
HERE=$(cd "$(dirname "$0")" && pwd)
. "$HERE/testrig-env.sh"
rig_require ASK2_DUT GEN1 GEN2
REPS=${REPS:-3}; MTU=${MTU:-9000}
export DUR=${DUR:-10} STEADY_FROM=${STEADY_FROM:-3}
BIDIR_DUR=${BIDIR_DUR:-20}; BIDIR_STEADY_FROM=${BIDIR_STEADY_FROM:-12}
RUN=${OUTDIR:?set OUTDIR to the matrix run directory}
SSHO=(-n -o BatchMode=yes -o ConnectTimeout=8 $RIG_SSHK)
d(){ ssh "${SSHO[@]}" "$ASK2_DUT" "$@"; }
g1(){ ssh "${SSHO[@]}" "$GEN1" "$@"; }
g2(){ ssh "${SSHO[@]}" "$GEN2" "$@"; }
# parents first when growing, uppers first when shrinking (an upper cannot exceed its parent)
setmtu(){
  local m=$1
  if [ "$m" -gt 1500 ]; then
    d "sudo ip link set eth3 mtu $m && sudo ip link set eth4 mtu $m && sudo ip link set eth3.10 mtu $m && sudo ip link set eth4.20 mtu $m"
    g1 "sudo ip link set $GEN_IF mtu $m && sudo ip link set $GEN_IF.10 mtu $m"
    g2 "sudo ip link set $GEN_IF mtu $m && sudo ip link set $GEN_IF.20 mtu $m"
  else
    d "sudo ip link set eth3.10 mtu $m; sudo ip link set eth4.20 mtu $m; sudo ip link set eth3 mtu $m; sudo ip link set eth4 mtu $m"
    g1 "sudo ip link set $GEN_IF.10 mtu $m; sudo ip link set $GEN_IF mtu $m"
    g2 "sudo ip link set $GEN_IF.20 mtu $m; sudo ip link set $GEN_IF mtu $m"
  fi
}
trap 'setmtu 1500; echo "MTU restored to 1500"' EXIT
setmtu "$MTU" || { echo "ABORT: could not set MTU $MTU"; exit 1; }
sleep 4
echo "DUT: $(d 'cat /sys/class/net/eth3/mtu /sys/class/net/eth4/mtu /sys/class/net/eth3.10/mtu /sys/class/net/eth4.20/mtu' | tr '\n' ' ')"
s4=$((MTU - 28)); s6=$((MTU - 48)); bad=0
for p in "10.99.1.112 10.99.2.113" "10.99.10.112 10.99.20.113"; do
  set -- $p
  g1 "ping -M do -c 2 -W 2 -s $s4 -I $1 $2 >/dev/null" || { echo "FAIL: DF ping $s4 B $1 -> $2"; bad=1; }
done
for p in "fd99:1::112 fd99:2::113" "fd99:10::112 fd99:20::113"; do
  set -- $p
  g1 "ping -6 -M do -c 2 -W 2 -s $s6 -I $1 $2 >/dev/null" || { echo "FAIL: DF ping $s6 B $1 -> $2"; bad=1; }
done
[ "$bad" = 0 ] || { echo "ABORT: a path does not carry MTU $MTU frames"; exit 1; }
echo "DF pings at MTU $MTU: v4 and v6, routed and VLAN, OK"
UNI="unicast-v4 unicast-v6 unicast-v4-rev unicast-v6-rev nat-v4 nat-v6 nat-v4-rev nat-v6-rev
vlan-v4 vlan-v6 vlan-v4-rev vlan-v6-rev vlanport-v4 vlanport-v6 portvlan-v4 portvlan-v6"
BI="unicast-v4 unicast-v6 nat-v4 nat-v6 vlan-v4 vlan-v6 vlanport-v4 vlanport-v6"
for r in $(seq 1 "$REPS"); do
  echo "##### 9k unidir rep $r $(date +%T)"
  DUT_KIND=ask2 OUT=$RUN/quick9k-uni-r$r.csv J=$RUN/json9k-r$r "$HERE/testrig-offload-quick.sh" $UNI
  echo "##### 9k bidir rep $r $(date +%T)"
  DUT_KIND=ask2 BIDIR=1 DUR=$BIDIR_DUR STEADY_FROM=$BIDIR_STEADY_FROM OUT=$RUN/quick9k-bidir-r$r.csv J=$RUN/json9k-r$r "$HERE/testrig-offload-quick.sh" $BI
done
echo "##### 9k done $(date +%T)"
