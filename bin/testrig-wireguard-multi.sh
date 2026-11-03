#!/usr/bin/env bash
# testrig-wireguard-multi.sh — N parallel WireGuard tunnels through the DUT (stage 2b of plans/archive/ask2-wireguard-plan.md).
#
# Each tunnel has a distinct outer UDP 5-tuple and an ordered per-peer receive stage.
# Crypto workers can run across CPUs, and WGFP can spread distinct inner TX flows
# across sinks. Multiple tunnels also let RSS distribute the outer flows.
#
#   dell1 -> DUT eth3 -> DUT wgm<K> (encrypt) -> UDP 51830+K -> DUT eth4 -> dell2 wgm<K> (decrypt)      K = 0..N-1
#   inner 10.99.(210+K).0/24: DUT .1, dell2 .113; one iperf3 pair per tunnel (dell2 ports 5240+K), all started together
#
# Usage: bin/testrig-wireguard-multi.sh [run|setup|teardown]
# Env:   TUNNELS=4 ACTIVE=<n>  (n of the tunnels carry traffic; default all)   DIRS="up down"  DUR=30 STEADY_FROM=10
#        STREAMS=2 (per tunnel)  REPS=3  DUT_KIND=ask2|vendor  OUT=<csv>
#        WGFP=1 binds the DPAA WireGuard fast path to all wgm<K> after setup (multi-peer gate)
#        WG_SKIP_SERVERS=1 leaves servers untouched; for run also set WG_EXTERNAL_IPERF=1.
#        WG_IPERF_BASE selects the externally managed server ports (default 5240).
#        WG_SKIP_FW4=1 leaves the vendor's existing forwarding policy unchanged;
#        use it only when that policy already permits the test traffic.
# Everything is runtime-only and removed by an EXIT trap (KEEP=1 leaves it up).
set -u
DUT_KIND=${DUT_KIND:-ask2}
if [ "$DUT_KIND" = vendor ]; then DUT=${DUT:-root@192.168.1.106}; RIG_NS=${RIG_NS:-n106}
else DUT=${DUT:-vyos@192.168.1.185}; RIG_NS=${RIG_NS:-}; fi
TUNNELS=${TUNNELS:-4}; ACTIVE=${ACTIVE:-$TUNNELS}; DIRS=${DIRS:-"up down"}; DUR=${DUR:-30}; STEADY_FROM=${STEADY_FROM:-10}
STREAMS=${STREAMS:-2}; REPS=${REPS:-3}; WGMTU=${WGMTU:-1420}
IPERF_BASE=${WG_IPERF_BASE:-5240}
if ! [[ $IPERF_BASE =~ ^[1-9][0-9]{0,4}$ ]] || (( IPERF_BASE > 65520 )); then
  echo "WG_IPERF_BASE must be a port number from 1 to 65520" >&2; exit 2
fi
if [ "$IPERF_BASE" != 5240 ] && [ "${WG_SKIP_SERVERS:-0}" != 1 ]; then
  echo "WG_IPERF_BASE requires WG_SKIP_SERVERS=1 and externally managed servers" >&2; exit 2
fi
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
sport(){ echo $((IPERF_BASE+$1)); } # iperf3 server port of tunnel K

teardown(){
  if [ "${WG_SKIP_SERVERS:-0}" != 1 ]; then
    $D2 "for k in \$(seq 0 15); do f=/tmp/wgm-iperf-\$k.pid; [ -f \$f ] && sudo kill \$(cat \$f) 2>/dev/null; sudo rm -f \$f; done; true" 2>/dev/null
  fi
  dut 'for k in $(seq 0 15); do sudo ip link del wgm$k 2>/dev/null; done; rm -f /tmp/wgmk; true' 2>/dev/null
  if [ "${WG_SKIP_FW4:-0}" != 1 ]; then
    dut 'for h in $(nft -a list chain inet fw4 forward 2>/dev/null | sed -n "s/.*wgmrig.*# handle \([0-9]*\)/\1/p"); do nft delete rule inet fw4 forward handle $h; done; true' 2>/dev/null
  fi
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
    if [ "${WG_SKIP_SERVERS:-0}" != 1 ]; then
      $D2 "$NSX iperf3 -s -p $(sport $k) -D -I /tmp/wgm-iperf-$k.pid"
    fi
  done
  if [ "$DUT_KIND" = vendor ]; then
    if [ "${WG_SKIP_FW4:-0}" = 1 ]; then
      echo "Using the vendor's existing forwarding policy; no fw4 rules inserted" >&2
    else
      dut 'nft insert rule inet fw4 forward oifname "wgm*" counter accept comment "wgmrig" && nft insert rule inet fw4 forward iifname "wgm*" counter accept comment "wgmrig"' ||
        { echo "Failed to insert vendor fw4 rules" >&2; return 1; }
    fi
  fi
  for k in $(seq 0 $((TUNNELS-1))); do $D1 "$NSX ping -c2 -W2 -I 10.99.1.112 10.99.$(sub $k).113 >/dev/null 2>&1"; done
  dut 'sudo wg show all latest-handshakes' | awk '{print "handshake "$1" "$3}'
}

steady(){ python3 - "$STEADY_FROM" "${WG_SKIP_SERVERS:-0}" "$@" <<'PY'
import json,math,sys
f=float(sys.argv[1]); strict=sys.argv[2]=="1"; tot=0; per=[]; retr=0
for p in sys.argv[3:]:
    try: j=json.load(open(p))
    except (OSError,ValueError) as exc:
        if strict:
            print(f"ERROR: invalid external iperf output {p}: {exc}",file=sys.stderr); sys.exit(1)
        per.append(0.0); continue
    a=[i['sum']['bits_per_second']+i.get('sum_bidir_reverse',{}).get('bits_per_second',0) for i in j.get('intervals',[]) if i['sum']['start']>=f-0.01 and not i['sum'].get('omitted')]
    g=sum(a)/len(a)/1e9 if a else 0
    if strict and (j.get('error') or not math.isfinite(g) or g<=0 or
                   any(not math.isfinite(value) or value<0 for value in a)):
        print(f"ERROR: external iperf produced no traffic or failed for {p}: {j.get('error', '')}",file=sys.stderr); sys.exit(1)
    per.append(g); tot+=g
    retr+=j.get('end',{}).get('sum_sent',{}).get('retransmits',0) or 0
print("%.9f %d %s"%(tot,retr,",".join("%.6f"%x for x in per)))
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
case $cmd in teardown) teardown; echo "torn down"; exit 0;; setup) setup || { teardown; exit 4; }; exit 0;; run) : ;; *) echo "usage: $0 [run|setup|teardown]" >&2; exit 2;; esac
[ "${WG_SKIP_SERVERS:-0}" != 1 ] || [ "${WG_EXTERNAL_IPERF:-0}" = 1 ] ||
  { echo "WG_SKIP_SERVERS=1 with run requires WG_EXTERNAL_IPERF=1" >&2; exit 2; }

ENVTXT=$(dut 'echo "kernel: $(uname -r)"; echo "lockdep: $([ -e /proc/lockdep ] && echo PRESENT || echo absent)"; echo "gov: $(cat /sys/devices/system/cpu/cpu0/cpufreq/scaling_governor 2>/dev/null) $(cat /sys/devices/system/cpu/cpu0/cpufreq/scaling_cur_freq 2>/dev/null)"' 2>/dev/null); echo "$ENVTXT"
if echo "$ENVTXT" | grep -q "lockdep: PRESENT"; then echo "REFUSING: lock debugging kernel" >&2; exit 5; fi
[ "${KEEP:-0}" = 1 ] || trap teardown EXIT
setup || { echo "setup failed" >&2; teardown; exit 4; }
# WGFP=1: bind the DPAA fast path to every wgm<K> (multi-peer P2c gate); read-out printed at the end
WP=/sys/module/wireguard/parameters/wgfp_peer
if [ "${WGFP:-0}" = 1 ]; then
  dut "echo '$(for k in $(seq 0 $((TUNNELS-1))); do printf 'wgm%s ' $k; done)' | sudo tee $WP >/dev/null" || { echo "wgfp bind failed" >&2; exit 4; }
  echo "wgfp: $(dut "sudo cat $WP")"
fi
echo "ts,tunnels,active,dir,rep,gbps,retrans,dut_busy,hot_cpus,per_tunnel_gbps" > "$OUT"
echo "DUT=$DUT kind=$DUT_KIND tunnels=$TUNNELS active=$ACTIVE streams/tunnel=$STREAMS dur=$DUR"
win=$((DUR-STEADY_FROM-2))
for dir in $DIRS; do
  case $dir in up) flag="";; down) flag="-R";; bidir) flag="--bidir";; esac
  for rep in $(seq 1 $REPS); do
    dut_stat=${WG_DUT_STAT:-/tmp/wgm-dut.stat}
    ssh $SSHO -i $HOME/.ssh/vyos_key $DUT sh -s -- $STEADY_FROM $win > "$dut_stat" <<'EOS' &
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
    client_status=0
    for pid in $pids; do wait "$pid" || client_status=1; done
    stat_status=0; wait $sp || stat_status=$?; sleep 1
    if [ "${WG_SKIP_SERVERS:-0}" = 1 ] && [ "$client_status" -ne 0 ]; then
      echo "ERROR: external iperf client failed; no traffic or an incomplete run" >&2; exit 1
    fi
    if [ "${WG_SKIP_SERVERS:-0}" = 1 ] && [ "$stat_status" -ne 0 ]; then
      echo "ERROR: DUT statistics sampling failed" >&2; exit 1
    fi
    rates=$(steady $files) || { echo "ERROR: unable to parse iperf results" >&2; exit 1; }
    read gbps retr per <<< "$rates"
    read busy hot < <(cpu_summary "$dut_stat")
    printf 'tunnels %s active %s %-4s r%s %7s Gbit/s  retr %-8s dutbusy %5s%%  hot %s  per-tunnel %s\n' "$TUNNELS" "$ACTIVE" "$dir" "$rep" "$gbps" "$retr" "$busy" "$hot" "$per"
    echo "$(date +%H:%M:%S),$TUNNELS,$ACTIVE,$dir,$rep,$gbps,$retr,$busy,$hot,$per" >> "$OUT"
    sleep 5
  done
done
python3 - "$OUT" <<'PY'
import csv,sys,statistics as st,collections
g=collections.OrderedDict()
for r in csv.DictReader(open(sys.argv[1])):
    if float(r['gbps'])>0: g.setdefault((r['active'],r['dir']),[]).append(float(r['gbps']))
print("\nSUMMARY (aggregate Gbit/s over reps)")
for (a,d),v in g.items(): print("active=%s %-5s n=%d mean %.3f min %.3f max %.3f sd %.3f"%(a,d,len(v),st.mean(v),min(v),max(v),st.pstdev(v) if len(v)>1 else 0))
PY
[ "${WGFP:-0}" = 1 ] && echo "wgfp: $(dut "sudo cat $WP")" && dut "echo off | sudo tee $WP >/dev/null"
echo "csv: $OUT"
