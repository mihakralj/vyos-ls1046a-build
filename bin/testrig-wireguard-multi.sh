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
#        ASK2 software and WGFP runs both require the exclusive wgfp_peer_lease reservation.
#        WG_SKIP_SERVERS=1 leaves servers untouched; for run also set WG_EXTERNAL_IPERF=1.
#        WG_IPERF_BASE selects the externally managed server ports (default 5240).
#        WGFP_COUNTER_LOG=<path> records WGFP counters before and after each cell.
#        WG_SKIP_FW4=1 leaves the vendor's existing forwarding policy unchanged;
#        use it only when that policy already permits the test traffic.
#        WG_CLIENT_TIMEOUT caps each iperf client in seconds (default DUR+45).
#        INNER=6 carries IPv6 inside the tunnels (fd99:(210+K)::/64, DUT ::1, dell2 ::113; dell1 fd99:1::112)
#        instead of IPv4; the outer transport stays IPv4. JSON files then start with wgm6- and counter cells
#        are labelled <N>v6.
# Everything is runtime-only and removed by an EXIT trap (KEEP=1 leaves it up).
set -u
. "$(dirname "$0")/testrig-env.sh"
. "$(dirname "$0")/testrig-wgfp-counters.sh"
. "$(dirname "$0")/testrig-wg-lease.sh"
. "$(dirname "$0")/testrig-wg-resource.sh"
DUT_KIND=${DUT_KIND:-ask2}
DUT=${DUT:-$(rig_dut "$DUT_KIND")} || exit 2
if [ "$DUT_KIND" = vendor ]; then RIG_NS=${RIG_NS:-$VENDOR_NS}; else RIG_NS=${RIG_NS:-}; fi
rig_require GEN1 GEN2
TUNNELS=${TUNNELS:-4}; ACTIVE=${ACTIVE:-$TUNNELS}; DIRS=${DIRS:-"up down"}; DUR=${DUR:-30}; STEADY_FROM=${STEADY_FROM:-10}
STREAMS=${STREAMS:-2}; REPS=${REPS:-3}; WGMTU=${WGMTU:-1420}; INNER=${INNER:-4}
case $INNER in 4) JPRE=wgm; CTAG="";; 6) JPRE=wgm6; CTAG=v6;; *) echo "INNER must be 4 or 6" >&2; exit 2;; esac
if ! [[ $TUNNELS =~ ^([1-9]|1[0-6])$ && $ACTIVE =~ ^([1-9]|1[0-6])$ ]] ||
   (( ACTIVE > TUNNELS )); then
  echo "TUNNELS and ACTIVE must be 1..16, with ACTIVE <= TUNNELS" >&2; exit 2
fi
if ! [[ $DUR =~ ^[1-9][0-9]{0,5}$ ]]; then echo "DUR must be a positive integer" >&2; exit 2; fi
WG_CLIENT_TIMEOUT=${WG_CLIENT_TIMEOUT:-$((DUR+45))}
if ! [[ $WG_CLIENT_TIMEOUT =~ ^[1-9][0-9]{0,6}$ ]] || (( WG_CLIENT_TIMEOUT <= DUR )); then
  echo "WG_CLIENT_TIMEOUT must be an integer greater than DUR" >&2; exit 2
fi
IPERF_BASE=${WG_IPERF_BASE:-5240}
if ! [[ $IPERF_BASE =~ ^[1-9][0-9]{0,4}$ ]] || (( IPERF_BASE > 65520 )); then
  echo "WG_IPERF_BASE must be a port number from 1 to 65520" >&2; exit 2
fi
if [ "$IPERF_BASE" != 5240 ] && [ "${WG_SKIP_SERVERS:-0}" != 1 ]; then
  echo "WG_IPERF_BASE requires WG_SKIP_SERVERS=1 and externally managed servers" >&2; exit 2
fi
OUT=${OUT:-/mnt/builds/ask2-review/oracle/wgm-$(date +%Y%m%d-%H%M)-${DUT_KIND}.csv}
J=${J:-/mnt/builds/ask2-review/oracle/json-wg}; mkdir -p "$J"
SSHO="-o BatchMode=yes -o ConnectTimeout=8 $RIG_SSHK"
NSX="sudo"; [ -n "$RIG_NS" ] && NSX="sudo ip netns exec $RIG_NS"
dut(){ local c=$1; [ "$DUT_KIND" = vendor ] && c=${c//sudo /}; ssh -n $SSHO $DUT "$c"; }
duti(){ local c=$1; [ "$DUT_KIND" = vendor ] && c=${c//sudo /}; ssh $SSHO $DUT "$c"; }
D1="ssh -n $SSHO $GEN1"; D2="ssh -n $SSHO $GEN2"
D1I="ssh $SSHO $GEN1"; D2I="ssh $SSHO $GEN2"
DUT_OUT=10.99.2.185; D2_OUT=10.99.2.113
# shellcheck disable=SC2034 # consumed by the sourced lease helper
WG_LEASE_KIND=multi
# shellcheck disable=SC2034 # consumed by the sourced lease helper
WG_LEASE_DIR="${WG_STATE_PATH:-/tmp/wgm-rig}.multi-lease"
WG_LEASE_COUNT=$TUNNELS
WG_LINK_HELPER="$(dirname "$0")/testrig-wg-link.sh"
WG_ROUTE_HELPER="$(dirname "$0")/testrig-wg-route.sh"
# shellcheck disable=SC2034 # consumed by the sourced resource helper
WG_SERVER_HELPER="$(dirname "$0")/testrig-wg-server.py"
# shellcheck disable=SC2034 # consumed by the sourced resource helper
WG_STOP_HELPER="$(dirname "$0")/testrig-wgfp-stop.py"
sub(){ echo $((210+$1)); }          # inner third octet of tunnel K
wport(){ echo $((51830+$1)); }      # outer UDP port of tunnel K
sport(){ echo $((IPERF_BASE+$1)); } # iperf3 server port of tunnel K
far6(){ echo "fd99:$(sub "$1")::113"; }   # dell2 inner IPv6 address of tunnel K (INNER=6)

wg_dut_link(){
  duti "sudo sh -s -- $1 wgm$2 wgo-$WG_OWNER_TOKEN-d$2" < "$WG_LINK_HELPER"
}
wg_far_link(){
  $D2I "$NSX sh -s -- $1 wgm$2 wgo-$WG_OWNER_TOKEN-f$2" < "$WG_LINK_HELPER"
}
wg_route(){
  local side=$1 action=$2 k=$3 priority table realm subnet
  subnet=$(sub "$k")
  priority=$((1000 + 16#${WG_OWNER_TOKEN:0:6} % 30000 + k))
  table=$((1000000 + 16#${WG_OWNER_TOKEN:6:6} * 16 + k))
  realm=$(( (16#${WG_OWNER_TOKEN:12:8} & 0x7fffffff) + 1 ))
  if [ "$side" = far ]; then
    $D2I "$NSX sh -s -- $action far $priority $table 10.99.$subnet.113 10.99.1.0/24 wgm$k $WG_LEASE_MTU $realm" < "$WG_ROUTE_HELPER"
  else
    $D1I "$NSX sh -s -- $action plain $priority $table 10.99.1.112 10.99.$subnet.0/24 10.99.1.185 $WG_LEASE_MTU $realm" < "$WG_ROUTE_HELPER"
  fi
}
# IPv6 inner routes reuse the tunnel's priority/table (IPv4 and IPv6 rules and tables are separate)
wg_route6(){
  local side=$1 action=$2 k=$3 priority table realm subnet
  subnet=$(sub "$k")
  priority=$((1000 + 16#${WG_OWNER_TOKEN:0:6} % 30000 + k))
  table=$((1000000 + 16#${WG_OWNER_TOKEN:6:6} * 16 + k))
  realm=$(( (16#${WG_OWNER_TOKEN:12:8} & 0x7fffffff) + 1 ))
  if [ "$side" = far ]; then
    $D2I "$NSX sh -s -- $action far $priority $table fd99:$subnet::113 fd99:1::/64 wgm$k $WG_LEASE_MTU $realm" < "$WG_ROUTE_HELPER"
  else
    $D1I "$NSX sh -s -- $action plain $priority $table fd99:1::112 fd99:$subnet::/64 fd99:1::185 $WG_LEASE_MTU $realm" < "$WG_ROUTE_HELPER"
  fi
}
wg_server_run(){
  $D2I "$NSX $2"
}
wg_bound_links_owned(){
  local k
  for k in $(seq 0 $((WG_LEASE_COUNT-1))); do
    dut "name=\$(sudo ip -o link show wgo-$WG_OWNER_TOKEN-d$k | sed -n 's/^[0-9]*: \\([^:@ ]*\\).*/\\1/p'); [ \"\$name\" = wgm$k ]" ||
      { echo "ERROR: WGFP peer wgm$k no longer belongs to this lease" >&2; return 1; }
  done
}

teardown(){
  local k result=0
  if [ "$WG_LEASE_SKIP_SERVERS" != 1 ]; then
    for k in $(seq 0 $((WG_LEASE_COUNT-1))); do
      wg_resource_remove "server-$k" wg_server_stop "s$k" "$((WG_LEASE_IPERF_BASE+k))" || result=1
    done
  fi
  [ "$result" = 0 ] || return 1
  wg_prepare_binding_cleanup || return 1
  for k in $(seq 0 $((WG_LEASE_COUNT-1))); do
    wg_resource_remove "route-far6-$k" wg_route6 far delete "$k" || result=1
    wg_resource_remove "route-plain6-$k" wg_route6 plain delete "$k" || result=1
    wg_resource_remove "route-far-$k" wg_route far delete "$k" || result=1
    wg_resource_remove "route-plain-$k" wg_route plain delete "$k" || result=1
  done
  [ "$result" = 0 ] || return 1
  for k in $(seq 0 $((WG_LEASE_COUNT-1))); do
    wg_resource_remove "link-dut-$k" wg_dut_link delete "$k" || result=1
    wg_resource_remove "link-far-$k" wg_far_link delete "$k" || result=1
  done
  [ "$result" = 0 ] || return 1
  if [ "$DUT_KIND" = vendor ] && [ "$WG_LEASE_SKIP_FW4" != 1 ]; then
    dut "rules=\$(nft -a list chain inet fw4 forward) || exit 1
      printf '%s\\n' \"\$rules\" | awk -v tag='wgmrig-$WG_OWNER_TOKEN' '
        index(\$0, \"comment \\\"\" tag \"\\\"\") { for (i=1; i<NF; i++) if (\$i == \"handle\" && \$(i+1) ~ /^[0-9]+$/) print \$(i+1) }
      ' | while read -r h; do nft delete rule inet fw4 forward handle \"\$h\" || exit 1; done" || result=1
  fi
  [ "$result" = 0 ] || return 1
  wg_release_binding
}

setup(){
  [ "$DUT_KIND" = ask2 ] || dut 'sudo modprobe wireguard 2>/dev/null; true'
  local k s p k1 k2 p1 p2 expected="" aliases="" a6d="" a6f=""
  for k in $(seq 0 $((TUNNELS-1))); do
    s=$(sub $k); p=$(wport $k)
    if [ "$INNER" = 6 ]; then a6d=",fd99:$s::113/128"; a6f=",fd99:$s::1/128,fd99:1::/64"; fi
    k1=$(dut 'wg genkey') || return 1
    p1=$(printf '%s\n' "$k1" | duti 'wg pubkey') || return 1
    k2=$($D2 'wg genkey') || return 1
    p2=$(printf '%s\n' "$k2" | $D2I 'wg pubkey') || return 1
    wg_resource_add "link-dut-$k" wg_dut_link create "$k" || return 1
    printf '%s\n' "$k1" | duti "sudo wg set wgm$k private-key /dev/stdin listen-port $p peer $p2 allowed-ips 10.99.$s.113/32$a6d endpoint $D2_OUT:$p" || return 1
    dut "sudo ip addr add 10.99.$s.1/24 dev wgm$k && sudo ip link set wgm$k mtu $WGMTU up" || return 1
    [ "$INNER" != 6 ] || dut "sudo ip -6 addr add fd99:$s::1/64 dev wgm$k nodad" || return 1
    wg_resource_add "link-far-$k" wg_far_link create "$k" || return 1
    printf '%s\n' "$k2" | $D2I "$NSX wg set wgm$k private-key /dev/stdin listen-port $p peer $p1 allowed-ips 10.99.$s.1/32,10.99.1.0/24$a6f endpoint $DUT_OUT:$p" || return 1
    $D2 "$NSX ip addr add 10.99.$s.113/24 dev wgm$k && $NSX ip link set wgm$k mtu $WGMTU up" || return 1
    [ "$INNER" != 6 ] || $D2 "$NSX ip -6 addr add fd99:$s::113/64 dev wgm$k nodad" || return 1
    wg_resource_add "route-far-$k" wg_route far add "$k" || return 1
    wg_resource_add "route-plain-$k" wg_route plain add "$k" || return 1
    if [ "$INNER" = 6 ]; then
      wg_resource_add "route-far6-$k" wg_route6 far add "$k" || return 1
      wg_resource_add "route-plain6-$k" wg_route6 plain add "$k" || return 1
    fi
    if [ "${WG_SKIP_SERVERS:-0}" != 1 ]; then
      wg_resource_add "server-$k" wg_server_start "s$k" "$((WG_LEASE_IPERF_BASE+k))" || return 1
    fi
  done
  if [ "$DUT_KIND" = vendor ]; then
    if [ "${WG_SKIP_FW4:-0}" = 1 ]; then
      echo "Using the vendor's existing forwarding policy; no fw4 rules inserted" >&2
    else
      for k in $(seq 0 $((TUNNELS-1))); do
        dut "nft insert rule inet fw4 forward oifname 'wgm$k' counter accept comment 'wgmrig-$WG_OWNER_TOKEN' && nft insert rule inet fw4 forward iifname 'wgm$k' counter accept comment 'wgmrig-$WG_OWNER_TOKEN'" ||
          { echo "Failed to insert vendor fw4 rules for wgm$k" >&2; return 1; }
      done
    fi
  fi
  for k in $(seq 0 $((TUNNELS-1))); do $D1 "$NSX ping -c2 -W2 -I 10.99.1.112 10.99.$(sub $k).113 >/dev/null 2>&1"; done
  if [ "$INNER" = 6 ]; then
    for k in $(seq 0 $((TUNNELS-1))); do
      $D1 "$NSX ping -6 -c2 -W2 -I fd99:1::112 $(far6 $k) >/dev/null 2>&1" ||
        { echo "ERROR: no IPv6 path fd99:1::112 -> $(far6 $k) through wgm$k" >&2; return 1; }
    done
  fi
  dut 'sudo wg show all latest-handshakes' | awk '{print "handshake "$1" "$3}'
  if [ "${WGFP:-0}" = 1 ]; then
    for k in $(seq 0 $((TUNNELS-1))); do
      expected="${expected:+$expected }wgm$k"
      aliases="${aliases:+$aliases }wgo-$WG_OWNER_TOKEN-d$k"
    done
    wg_activate_binding "$expected" "$aliases" || return 1
    echo "wgfp: $(dut "sudo cat /sys/module/wireguard/parameters/wgfp_peer")"
  fi
}

steady(){ python3 - "$STEADY_FROM" "$@" <<'PY'
import json,math,sys
f=float(sys.argv[1]); direction=sys.argv[2]; tot=0; per=[]; retr=0
for p in sys.argv[3:]:
    try: j=json.load(open(p))
    except (OSError,ValueError) as exc:
        print(f"ERROR: invalid iperf output {p}: {exc}",file=sys.stderr); sys.exit(1)
    intervals=[i for i in j.get('intervals',[]) if i['sum']['start']>=f-0.01 and not i['sum'].get('omitted')]
    if direction=='bidir' and any('sum_bidir_reverse' not in i for i in intervals):
        print(f"ERROR: missing reverse iperf intervals in {p}",file=sys.stderr); sys.exit(1)
    a=[i['sum']['bits_per_second']+i.get('sum_bidir_reverse',{}).get('bits_per_second',0) for i in intervals]
    g=sum(a)/len(a)/1e9 if a else 0
    if (j.get('error') or not math.isfinite(g) or g<=0 or
            any(not math.isfinite(value) or value<0 for value in a)):
        print(f"ERROR: iperf produced no traffic or failed for {p}: {j.get('error', '')}",file=sys.stderr); sys.exit(1)
    if direction=='bidir':
        for key in ('sum_sent','sum_sent_bidir_reverse'):
            value=j.get('end',{}).get(key,{}).get('retransmits')
            if not isinstance(value,int) or value<0:
                print(f"ERROR: missing or invalid {key} retransmits in {p}",file=sys.stderr); sys.exit(1)
    per.append(g); tot+=g
    retr+=j.get('end',{}).get('sum_sent',{}).get('retransmits',0) or 0
    retr+=j.get('end',{}).get('sum_sent_bidir_reverse',{}).get('retransmits',0) or 0
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

wg_multi_cleanup(){
  local rc=$?
  trap - EXIT INT TERM
  if teardown; then wg_lease_end || rc=1; else rc=1; fi
  exit "$rc"
}

cmd=${1:-run}
if [ "$cmd" != teardown ] && [ "${WGFP:-0}" = 1 ] && (( TUNNELS > 8 )); then
  echo "WGFP supports at most 8 WireGuard devices" >&2
  exit 2
fi
case $cmd in
  teardown) wg_lease_load && teardown && wg_lease_end || exit 4; echo "torn down"; exit 0;;
  setup)
    [ "$DUT_KIND" != ask2 ] || dut 'sudo modprobe wireguard 2>/dev/null; true'
    wgfp_require_unbound || exit 4
    wg_lease_begin || exit 4
    trap wg_multi_cleanup EXIT
    trap 'exit 130' INT
    trap 'exit 143' TERM
    wg_reserve_binding || exit 4
    setup || exit 4
    trap - EXIT INT TERM
    exit 0;;
  run) : ;;
  *) echo "usage: $0 [run|setup|teardown]" >&2; exit 2;;
esac
[ "${WG_SKIP_SERVERS:-0}" != 1 ] || [ "${WG_EXTERNAL_IPERF:-0}" = 1 ] ||
  { echo "WG_SKIP_SERVERS=1 with run requires WG_EXTERNAL_IPERF=1" >&2; exit 2; }
[ -z "${WGFP_COUNTER_LOG:-}" ] || [ "${WGFP:-0}" = 1 ] ||
  { echo "WGFP_COUNTER_LOG requires WGFP=1" >&2; exit 2; }

ENVTXT=$(dut 'echo "kernel: $(uname -r)"; echo "lockdep: $([ -e /proc/lockdep ] && echo PRESENT || echo absent)"; echo "gov: $(cat /sys/devices/system/cpu/cpu0/cpufreq/scaling_governor 2>/dev/null) $(cat /sys/devices/system/cpu/cpu0/cpufreq/scaling_cur_freq 2>/dev/null)"' 2>/dev/null); echo "$ENVTXT"
if echo "$ENVTXT" | grep -q "lockdep: PRESENT"; then echo "REFUSING: lock debugging kernel" >&2; exit 5; fi
[ "$DUT_KIND" != ask2 ] || dut 'sudo modprobe wireguard 2>/dev/null; true'
wgfp_require_unbound || exit 4
wg_lease_begin || exit 4
trap wg_multi_cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
wg_reserve_binding || exit 4
setup || { echo "setup failed" >&2; exit 4; }
WP=/sys/module/wireguard/parameters/wgfp_peer
echo "ts,tunnels,active,dir,rep,gbps,retrans,dut_busy,hot_cpus,per_tunnel_gbps" > "$OUT"
echo "DUT=$DUT kind=$DUT_KIND tunnels=$TUNNELS active=$ACTIVE streams/tunnel=$STREAMS dur=$DUR"
win=$((DUR-STEADY_FROM-2))
for dir in $DIRS; do
  case $dir in up) flag="";; down) flag="-R";; bidir) flag="--bidir";; esac
  for rep in $(seq 1 $REPS); do
    wgfp_counter_sample "$TUNNELS$CTAG" "$dir" "$rep" before || exit 1
    dut_stat=${WG_DUT_STAT:-/tmp/wgm-dut.stat}
    ssh $SSHO $DUT sh -s -- $STEADY_FROM $win > "$dut_stat" <<'EOS' &
sf=$1; w=$2
snap(){ grep '^cpu' /proc/stat; echo --; }
sleep $sf; snap; sleep $w; snap
EOS
    sp=$!; files=""; pids=""
    for k in $(seq 0 $((ACTIVE-1))); do
      f="$J/$JPRE-$dir-n$ACTIVE-k$k-r$rep.json"; files="$files $f"
      if [ "$INNER" = 6 ]; then dst=$(far6 $k); src=fd99:1::112; else dst=10.99.$(sub $k).113; src=10.99.1.112; fi
      $D1 "$NSX timeout --signal=TERM --kill-after=5s ${WG_CLIENT_TIMEOUT}s iperf3 -c $dst -B $src -p $(sport $k) -P $STREAMS -t $DUR -i 1 -Z -J $flag" > "$f" &
      pids="$pids $!"
    done
    client_status=0
    for pid in $pids; do wait "$pid" || client_status=1; done
    stat_status=0; wait $sp || stat_status=$?
    wgfp_counter_sample "$TUNNELS$CTAG" "$dir" "$rep" after || exit 1
    sleep 1
    if [ "$client_status" -ne 0 ]; then
      echo "ERROR: iperf client failed; no traffic or an incomplete run" >&2; exit 1
    fi
    if [ "$stat_status" -ne 0 ]; then
      echo "ERROR: DUT statistics sampling failed" >&2; exit 1
    fi
    rates=$(steady "$dir" $files) || { echo "ERROR: unable to parse iperf results" >&2; exit 1; }
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
[ "${WGFP:-0}" = 1 ] && echo "wgfp: $(dut "sudo cat $WP")"
echo "csv: $OUT"
[ "${KEEP:-0}" != 1 ] || trap - EXIT INT TERM
