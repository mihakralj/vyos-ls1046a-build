#!/usr/bin/env bash
# testrig-wireguard-multi.sh — N parallel WireGuard tunnels through the DUT (stage 2b of plans/ask2-wireguard-plan.md).
#
# One tunnel is one outer UDP 5-tuple and one serial tx/rx stage, i.e. one core. N tunnels with distinct listen ports
# give N serial stages that the kernel homes on different CPUs (peer id % online CPUs, consecutive ids), and N outer
# flows that RSS can spread. This measures how far aggregate throughput scales before crypto or the DPAA path limits it.
#
#   dell1 -> DUT eth3 -> DUT wgm<K> (encrypt) -> UDP 51830+K -> DUT eth4 -> dell2 wgm<K> (decrypt)      K = 0..N-1
#   inner 10.99.(210+K).0/24: DUT .1, dell2 .113; one iperf3 pair per tunnel (dell2 ports 5240+K), all started together
#
# Usage: bin/testrig-wireguard-multi.sh [run|setup|teardown]
# Env:   TUNNELS=4 ACTIVE=<n>  (n of the tunnels carry traffic; default all)   DIRS="up down"  DUR=30 STEADY_FROM=10
#        STREAMS=2 (per tunnel)  REPS=3  DUT_KIND=ask2|vendor  OUT=<csv>
# Everything is runtime-only and removed by an EXIT trap (KEEP=1 leaves it up).
set -u
DUT_KIND=${DUT_KIND:-ask2}
if [ "$DUT_KIND" = vendor ]; then DUT=${DUT:-root@192.168.1.106}; RIG_NS=${RIG_NS:-n106}
else DUT=${DUT:-vyos@192.168.1.185}; RIG_NS=${RIG_NS:-}; fi
TUNNELS=${TUNNELS:-4}; ACTIVE=${ACTIVE:-$TUNNELS}; DIRS=${DIRS:-"up down"}; DUR=${DUR:-30}; STEADY_FROM=${STEADY_FROM:-10}
STREAMS=${STREAMS:-2}; REPS=${REPS:-3}; WGMTU=${WGMTU:-1420}
OUT=${OUT:-/mnt/builds/ask2-review/oracle/wgm-$(date +%Y%m%d-%H%M)-${DUT_KIND}.csv}
J=${J:-/mnt/builds/ask2-review/oracle/json-wg}; mkdir -p "$J"
SSHO="-o BatchMode=yes -o ConnectTimeout=8"
NSX="sudo"; [ -n "$RIG_NS" ] && NSX="sudo ip netns exec $RIG_NS"
dut(){ local c=$1; [ "$DUT_KIND" = vendor ] && c=${c//sudo /}; ssh -n $SSHO -i $HOME/.ssh/vyos_key $DUT "$c"; }
duti(){ local c=$1; [ "$DUT_KIND" = vendor ] && c=${c//sudo /}; ssh $SSHO -i $HOME/.ssh/vyos_key $DUT "$c"; }
D1="ssh -n $SSHO admin@192.168.1.112"; D2="ssh -n $SSHO admin@192.168.1.113"; D2I="ssh $SSHO admin@192.168.1.113"
DUT_OUT=10.99.2.185; D2_OUT=10.99.2.113
sub(){ echo $((210+$1)); }          # inner third octet of tunnel K
wport(){ echo $((51830+$1)); }      # outer UDP port of tunnel K
sport(){ echo $((5240+$1)); }       # iperf3 server port of tunnel K

teardown(){
  $D2 "for k in \$(seq 0 15); do p=\$((5240+k)); f=/tmp/wgm-iperf-\$k.pid; [ -f \$f ] && sudo kill \$(cat \$f) 2>/dev/null; sudo rm -f \$f; done; true" 2>/dev/null
  dut 'for k in $(seq 0 15); do sudo ip link del wgm$k 2>/dev/null; done; rm -f /tmp/wgmk;
       for h in $(nft -a list chain inet fw4 forward 2>/dev/null | sed -n "s/.*wgmrig.*# handle \([0-9]*\)/\1/p"); do nft delete rule inet fw4 forward handle $h; done; true' 2>/dev/null
  $D2 "for k in \$(seq 0 15); do $NSX ip link del wgm\$k 2>/dev/null; $NSX ip rule del priority \$((12200+k)) 2>/dev/null; $NSX ip route flush table \$((220+k)) 2>/dev/null; done; rm -f /tmp/wgmk; true" 2>/dev/null
  $D1 "for k in \$(seq 0 15); do $NSX ip route del 10.99.\$((210+k)).0/24 2>/dev/null; done; true" 2>/dev/null
}

setup(){
  teardown
  dut 'sudo modprobe wireguard 2>/dev/null; true'
  local k s p k1 k2 p1 p2
  for k in $(seq 0 $((TUNNELS-1))); do
    s=$(sub $k); p=$(wport $k)
    k1=$(dut 'wg genkey'); p1=$(printf '%s\n' "$k1" | duti 'wg pubkey')
    k2=$($D2 'wg genkey'); p2=$(printf '%s\n' "$k2" | $D2I 'wg pubkey')
    dut "sudo ip link add wgm$k type wireguard" || return 1
    printf '%s\n' "$k1" | duti 'umask 077; cat > /tmp/wgmk'
    dut "sudo wg set wgm$k private-key /tmp/wgmk listen-port $p peer $p2 allowed-ips 10.99.$s.113/32 endpoint $D2_OUT:$p; rm -f /tmp/wgmk; sudo ip addr add 10.99.$s.1/24 dev wgm$k; sudo ip link set wgm$k mtu $WGMTU up"
    printf '%s\n' "$k2" | $D2I 'umask 077; cat > /tmp/wgmk'
    $D2 "$NSX ip link add wgm$k type wireguard && $NSX wg set wgm$k private-key /tmp/wgmk listen-port $p peer $p1 allowed-ips 10.99.$s.1/32,10.99.1.0/24 endpoint $DUT_OUT:$p && rm -f /tmp/wgmk &&
         $NSX ip addr add 10.99.$s.113/24 dev wgm$k && $NSX ip link set wgm$k mtu $WGMTU up &&
         $NSX ip rule add priority $((12200+k)) from 10.99.$s.113 lookup $((220+k)) && $NSX ip route replace 10.99.1.0/24 dev wgm$k table $((220+k))"
    $D1 "$NSX ip route replace 10.99.$s.0/24 via 10.99.1.185 mtu $WGMTU"
    $D2 "$NSX iperf3 -s -p $(sport $k) -D -I /tmp/wgm-iperf-$k.pid"
  done
  if [ "$DUT_KIND" = vendor ]; then
    dut 'nft insert rule inet fw4 forward oifname "wgm*" counter accept comment "wgmrig"; nft insert rule inet fw4 forward iifname "wgm*" counter accept comment "wgmrig"'
  fi
  for k in $(seq 0 $((TUNNELS-1))); do $D1 "$NSX ping -c2 -W2 -I 10.99.1.112 10.99.$(sub $k).113 >/dev/null 2>&1"; done
  dut 'sudo wg show all latest-handshakes' | awk '{print "handshake "$1" "$3}'
}

steady(){ python3 - "$STEADY_FROM" "$@" <<'PY'
import json,sys
f=float(sys.argv[1]); tot=0; per=[]; retr=0
for p in sys.argv[2:]:
    try: j=json.load(open(p))
    except Exception: per.append(0.0); continue
    a=[i['sum']['bits_per_second']+i.get('sum_bidir_reverse',{}).get('bits_per_second',0) for i in j.get('intervals',[]) if i['sum']['start']>=f-0.01 and not i['sum'].get('omitted')]
    g=sum(a)/len(a)/1e9 if a else 0; per.append(g); tot+=g
    retr+=j.get('end',{}).get('sum_sent',{}).get('retransmits',0) or 0
print("%.3f %d %s"%(tot,retr,",".join("%.2f"%x for x in per)))
PY
}
cpu_summary(){ awk '
 /^--$/{sec++; next}
 /^cpu[0-9]/{ t=0; for(i=2;i<=11;i++) t+=$i; idle=$5+$6; if(sec==0){T0[$1]=t;I0[$1]=idle} else {T1[$1]=t;I1[$1]=idle} }
 /^cpu /{ t=0; for(i=2;i<=11;i++) t+=$i; idle=$5+$6; if(sec==0){TA0=t;IA0=idle} else {TA1=t;IA1=idle} }
 END{ d=TA1-TA0; busy=d>0?100*(d-(IA1-IA0))/d:0; hot="";
      for(k in T1){ dd=T1[k]-T0[k]; b=dd>0?100*(dd-(I1[k]-I0[k]))/dd:0; if(b>=90) hot=hot k "+" }
      printf "%.1f %s\n", busy, (hot==""?"-":hot) }' "$1"; }

cmd=${1:-run}
case $cmd in teardown) teardown; echo "torn down"; exit 0;; setup) setup; exit $?;; run) : ;; *) echo "usage: $0 [run|setup|teardown]" >&2; exit 2;; esac

ENVTXT=$(dut 'echo "kernel: $(uname -r)"; echo "lockdep: $([ -e /proc/lockdep ] && echo PRESENT || echo absent)"; echo "gov: $(cat /sys/devices/system/cpu/cpu0/cpufreq/scaling_governor 2>/dev/null) $(cat /sys/devices/system/cpu/cpu0/cpufreq/scaling_cur_freq 2>/dev/null)"' 2>/dev/null); echo "$ENVTXT"
if echo "$ENVTXT" | grep -q "lockdep: PRESENT"; then echo "REFUSING: lock debugging kernel" >&2; exit 5; fi
[ "${KEEP:-0}" = 1 ] || trap teardown EXIT
setup || { echo "setup failed" >&2; exit 4; }
echo "ts,tunnels,active,dir,rep,gbps,retrans,dut_busy,hot_cpus,per_tunnel_gbps" > "$OUT"
echo "DUT=$DUT kind=$DUT_KIND tunnels=$TUNNELS active=$ACTIVE streams/tunnel=$STREAMS dur=$DUR"
win=$((DUR-STEADY_FROM-2))
for dir in $DIRS; do
  case $dir in up) flag="";; down) flag="-R";; bidir) flag="--bidir";; esac
  for rep in $(seq 1 $REPS); do
    ssh $SSHO -i $HOME/.ssh/vyos_key $DUT sh -s -- $STEADY_FROM $win > /tmp/wgm-dut.stat <<'EOS' &
sf=$1; w=$2
snap(){ grep '^cpu' /proc/stat; echo --; }
sleep $sf; snap; sleep $w; snap
EOS
    sp=$!; files=""; pids=""
    for k in $(seq 0 $((ACTIVE-1))); do
      f="$J/wgm-$dir-n$ACTIVE-k$k-r$rep.json"; files="$files $f"
      $D1 "$NSX iperf3 -c 10.99.$(sub $k).113 -B 10.99.1.112 -p $(sport $k) -P $STREAMS -t $DUR -i 1 -Z -J $flag" > "$f" &
      pids="$pids $!"
    done
    wait $pids; wait $sp; sleep 1
    read gbps retr per < <(steady $files)
    read busy hot < <(cpu_summary /tmp/wgm-dut.stat)
    printf 'tunnels %s active %s %-4s r%s %7s Gbit/s  retr %-8s dutbusy %5s%%  hot %s  per-tunnel %s\n' "$TUNNELS" "$ACTIVE" "$dir" "$rep" "$gbps" "$retr" "$busy" "$hot" "$per"
    echo "$(date +%H:%M:%S),$TUNNELS,$ACTIVE,$dir,$rep,$gbps,$retr,$busy,$hot,$per" >> "$OUT"
    sleep 5
  done
done
python3 - "$OUT" <<'PY'
import csv,sys,statistics as st,collections
g=collections.OrderedDict()
for r in csv.DictReader(open(sys.argv[1])):
    if float(r['gbps'])>0.05: g.setdefault((r['active'],r['dir']),[]).append(float(r['gbps']))
print("\nSUMMARY (aggregate Gbit/s over reps)")
for (a,d),v in g.items(): print("active=%s %-5s n=%d mean %.3f min %.3f max %.3f sd %.3f"%(a,d,len(v),st.mean(v),min(v),max(v),st.pstdev(v) if len(v)>1 else 0))
PY
echo "csv: $OUT"
