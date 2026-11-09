#!/usr/bin/env bash
# testrig-ask2-bridge.sh — the bridge and bridge-9k rows of the ASK2 matrix (plans/ASK2-PERF-MATRIX-PLAN.md).
#   Bridges DUT eth3+eth4 (bridge_offload=1) outside the VyOS config, the way bin/testrig-bridge-b6.sh
#   does, and runs dell1 <-> dell2 iperf3 across it: up (dell1 -> dell2), down (-R) and bidir, IPv4 and
#   IPv6, at MTU 1500 and MTU 9000, REPS runs each; the table keeps the fastest.
# Usage: OUTDIR=<matrix run dir> bin/testrig-ask2-bridge.sh   Env: REPS=3 DUR=10 STEADY_FROM=3
#        BIDIR_DUR=20 BIDIR_STEADY_FROM=12 STREAMS=8 MTUS="1500 9000" NOREBOOT=1 (skip the final reboot)
# Output: bridge-<mtu>.csv in OUTDIR (ts,mtu,fam,dir,rep,gbps,dut_busy). DUT busy is the offload
#   check: a software bridge at these rates keeps the CPUs busy, the offloaded path does not.
# It deletes eth3.10/eth4.20 and the eth3/eth4 addresses, so the DUT is REBOOTED at the end to get its
# saved routed configuration back. Never run it next to other rig traffic.
set -u
HERE=$(cd "$(dirname "$0")" && pwd)
. "$HERE/testrig-env.sh"
rig_require ASK2_DUT GEN1 GEN2
REPS=${REPS:-3}; DUR=${DUR:-10}; STEADY_FROM=${STEADY_FROM:-3}; STREAMS=${STREAMS:-8}
BIDIR_DUR=${BIDIR_DUR:-20}; BIDIR_STEADY_FROM=${BIDIR_STEADY_FROM:-12}; MTUS=${MTUS:-"1500 9000"}
RUN=${OUTDIR:?set OUTDIR to the matrix run directory}; mkdir -p "$RUN/json-bridge"
SSHO=(-n -o BatchMode=yes -o ConnectTimeout=8 $RIG_SSHK)
d(){ ssh "${SSHO[@]}" "$ASK2_DUT" "$@"; }
g1(){ ssh "${SSHO[@]}" "$GEN1" "$@"; }
g2(){ ssh "${SSHO[@]}" "$GEN2" "$@"; }
BR=br-mx; A1=10.99.77.112; A2=10.99.77.113; B1=fd99:77::112; B2=fd99:77::113
cpu(){ d "grep '^cpu ' /proc/stat"; }
busy(){ printf '%s\n%s\n' "$1" "$2" | awk '{for(i=2;i<=11;i++)a[NR,i]=$i} END{for(i=2;i<=11;i++){x=a[2,i]-a[1,i];T+=x;if(i==5||i==6)id+=x}; printf "%.1f",100*(T-id)/T}'; }
setmtu(){
  d "sudo ip link set eth3 mtu $1; sudo ip link set eth4 mtu $1; sudo ip link set $BR mtu $1"
  g1 "sudo ip link set $GEN_IF mtu $1"; g2 "sudo ip link set $GEN_IF mtu $1"; sleep 3
}
cleanup(){
  g1 "sudo ip addr del $A1/24 dev $GEN_IF; sudo ip -6 addr del $B1/64 dev $GEN_IF; sudo ip link set $GEN_IF mtu 1500" 2>/dev/null
  g2 "sudo ip addr del $A2/24 dev $GEN_IF; sudo ip -6 addr del $B2/64 dev $GEN_IF; sudo ip link set $GEN_IF mtu 1500" 2>/dev/null
  d "sudo ip link set eth3 nomaster; sudo ip link set eth4 nomaster; sudo ip link del $BR" 2>/dev/null
  if [ "${NOREBOOT:-0}" != 1 ]; then echo "rebooting the DUT to restore its routed configuration"; d "sudo systemctl reboot"; fi
}
trap cleanup EXIT

d "test -e /sys/module/ask/parameters/bridge_offload" || { echo "ABORT: ask.ko without bridge_offload"; exit 1; }
d "echo 1 | sudo tee /sys/module/ask/parameters/bridge_offload >/dev/null
for v in eth3.10 eth4.20; do sudo ip link del \$v 2>/dev/null; done
sudo ip addr flush dev eth3; sudo ip addr flush dev eth4
sudo ip link add $BR type bridge vlan_filtering 0
sudo ip link set eth3 master $BR; sudo ip link set eth4 master $BR; sudo ip link set $BR up"
g1 "sudo ip addr add $A1/24 dev $GEN_IF; sudo ip -6 addr add $B1/64 dev $GEN_IF nodad"
g2 "sudo ip addr add $A2/24 dev $GEN_IF; sudo ip -6 addr add $B2/64 dev $GEN_IF nodad"
g2 "pgrep -f '^iperf3 -s -p 5277' >/dev/null || iperf3 -s -p 5277 -D"
sleep 4
for m in $MTUS; do
  setmtu "$m"
  s4=$((m - 28)); s6=$((m - 48))
  g1 "ping -M do -c3 -W2 -s $s4 -I $A1 $A2 >/dev/null && ping -6 -M do -c3 -W2 -s $s6 -I $B1 $B2 >/dev/null" ||
    { echo "ABORT: no MTU $m path across $BR"; exit 1; }
  out=$RUN/bridge-$m.csv; echo "ts,mtu,fam,dir,rep,gbps,dut_busy" > "$out"
  for fam in 4 6; do
    if [ "$fam" = 4 ]; then src=$A1; dst=$A2; else src=$B1; dst=$B2; fi
    for dir in up down bidir; do
      case $dir in up) f=""; t=$DUR; sf=$STEADY_FROM;; down) f="-R"; t=$DUR; sf=$STEADY_FROM;;
                   bidir) f="--bidir"; t=$BIDIR_DUR; sf=$BIDIR_STEADY_FROM;; esac
      for r in $(seq 1 "$REPS"); do
        j=$RUN/json-bridge/bridge-$m-v$fam-$dir-r$r.json
        c0=$(cpu)
        g1 "timeout $((t + 30)) iperf3 -c $dst -B $src -p 5277 -P $STREAMS -t $t -i 1 -Z -J $f" > "$j"
        c1=$(cpu)
        gb=$(python3 - "$j" "$sf" <<'PY'
import json, sys
j = json.load(open(sys.argv[1])); sf = float(sys.argv[2])
iv = [i for i in j.get("intervals", []) if i["sum"]["start"] >= sf - 0.01]
v = [i["sum"]["bits_per_second"] + i.get("sum_bidir_reverse", {}).get("bits_per_second", 0) for i in iv]
print("%.3f" % (sum(v) / len(v) / 1e9 if v else 0))
PY
)
        b=$(busy "$c0" "$c1")
        printf 'bridge mtu %-4s v%s %-5s r%s %7s Gbit/s  DUT busy %5s%%\n' "$m" "$fam" "$dir" "$r" "$gb" "$b"
        echo "$(date +%H:%M:%S),$m,$fam,$dir,$r,$gb,$b" >> "$out"
      done
    done
  done
done
g2 "pkill -f '^iperf3 -s -p 5277'" || true
