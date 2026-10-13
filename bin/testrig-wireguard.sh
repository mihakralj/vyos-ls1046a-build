#!/usr/bin/env bash
# testrig-wireguard.sh — WireGuard throughput harness for the dell1 / DUT / dell2 10G rig.
#
# WireGuard is never offloaded by ASK2 or the vendor fast path (ChaCha20-Poly1305 runs in
# software, NEON on the A72), so this measures the DUT's software crypto + forwarding cost.
# The tunnel is terminated ON THE DUT; the far end is dell2:
#
#   dell1 --plain--> DUT eth3 --route--> wg0 (encrypt) --UDP/51820--> DUT eth4 --> dell2 wg0 (decrypt)
#
#   inner:  dell1 10.99.1.112 -> 10.99.200.113 (dell2 wg0)       v6: fd99:1::112 -> fd99:200::113
#   outer:  DUT 10.99.2.185 <-> dell2 10.99.2.113 (OUTER=6: fd99:2::185 <-> fd99:2::113)
#   "up"   = dell1 sends  -> DUT encrypts (eth3 -> wg0 -> eth4), dell2 decrypts
#   "down" = dell2 sends  -> DUT decrypts (eth4 -> wg0 -> eth3)   (iperf3 -R)
#   "bidir"= both at once
#
# Cells:
#   plain-v4     no tunnel, dell1 -> dell2 through the DUT (software/HW path reference)
#   wg-v4        IPv4 inner through the DUT tunnel
#   wg-v6        IPv6 inner through the DUT tunnel
#   ceiling-v4   dell1 <-> dell2 wg directly over the ConnectX-3 link (enp2s0, no DUT): the
#                generator pair's own crypto ceiling, so a DUT number is never generator-limited
#
# Everything is runtime-only: wg0 on the DUT (`ip link add`, not VyOS config), wg interfaces
# and policy routes on the dells, iperf3 servers under pidfiles. An EXIT trap removes all of it
# (KEEP=1 leaves the tunnel up for manual poking; `teardown` removes it).
#
# Usage: bin/testrig-wireguard.sh [setup|teardown|check|run] [cell ...]    (default: run, cells wg-v4 wg-v6)
#   bin/testrig-wireguard.sh run ceiling-v4 plain-v4 wg-v4 wg-v6
#   DIRS="up down bidir" STREAMS=4 DUR=30 bin/testrig-wireguard.sh run wg-v4
#   MODE=udp LEN=1280 RATE=800M bin/testrig-wireguard.sh run wg-v4
# Env: WG_NOTRACK=1 (outer UDP untracked by conntrack, experiment)
#      DUT_KIND=ask2|vendor   (vendor = .106 OpenWrt; RIG_NS=n106 is implied)
#      REPS (repeat each cell/direction, summary mean/min/max/sd), DUT, DIRS, DUR, STEADY_FROM, STREAMS, WGMTU, OUTER=4|6, MODE=tcp|udp, LEN, RATE, OUT, KEEP
# .106 needs a WireGuard kernel module built for its exact kernel; the OpenWrt feed kmod does not match
# the vendor kernel hash, so `check` reports that and `run` refuses until one is installed.
set -u
DUT_KIND=${DUT_KIND:-ask2}
if [ "$DUT_KIND" = vendor ]; then DUT=${DUT:-root@192.168.1.106}; RIG_NS=${RIG_NS:-n106}
else DUT=${DUT:-vyos@192.168.1.185}; RIG_NS=${RIG_NS:-}; fi
DIRS=${DIRS:-"up down"}; DUR=${DUR:-30}; STEADY_FROM=${STEADY_FROM:-10}; STREAMS=${STREAMS:-4}
REPS=${REPS:-1}; WGMTU=${WGMTU:-1420}; OUTER=${OUTER:-4}; MODE=${MODE:-tcp}; LEN=${LEN:-1280}; RATE=${RATE:-1G}
OUT=${OUT:-/mnt/builds/ask2-review/oracle/wg-$(date +%Y%m%d-%H%M)-${DUT_KIND}.csv}
J=${J:-/mnt/builds/ask2-review/oracle/json-wg}; mkdir -p "$J"
SSHO="-o BatchMode=yes -o ConnectTimeout=8"
NSX="sudo"; [ -n "$RIG_NS" ] && NSX="sudo ip netns exec $RIG_NS"   # always root; netns when RIG_NS is set

dut(){ local c=$1; [ "$DUT_KIND" = vendor ] && c=${c//sudo /}; ssh -n $SSHO -i $HOME/.ssh/vyos_key $DUT "$c"; }
duti(){ local c=$1; [ "$DUT_KIND" = vendor ] && c=${c//sudo /}; ssh $SSHO -i $HOME/.ssh/vyos_key $DUT "$c"; }   # stdin passes through
D1="ssh -n $SSHO admin@192.168.1.112"; D2="ssh -n $SSHO admin@192.168.1.113"
D1I="ssh $SSHO admin@192.168.1.112";   D2I="ssh $SSHO admin@192.168.1.113"

WGP=51820; WGPD=51821; IPERF_P=5231
if [ "$OUTER" = 6 ]; then DUT_OUT=fd99:2::185; D2_OUT=fd99:2::113; EP="[$D2_OUT]:$WGP"; else DUT_OUT=10.99.2.185; D2_OUT=10.99.2.113; EP="$D2_OUT:$WGP"; fi
# dell2 reaches the DUT's outer address on the same /24 (or /64), so no extra routes are needed for the outer leg.

STATE=/tmp/wg-rig.state; touch $STATE 2>/dev/null

# name|inner client addr (dell1)|inner server addr (dell2)|family|wg
CELLS_ALL="
plain-v4|10.99.1.112|10.99.2.113|4|none
wg-v4|10.99.1.112|10.99.200.113|4|dut
wg-v6|fd99:1::112|fd99:200::113|6|dut
ceiling-v4|10.99.201.112|10.99.201.113|4|direct
"

# ---------- tunnel plumbing ----------
wg_dut_up(){
  local k1 k2 p1 p2
  dut 'sudo modprobe wireguard 2>/dev/null; sudo ip link del wg0 2>/dev/null; sudo ip link add wg0 type wireguard' || return 1
  k1=$(dut 'wg genkey'); p1=$(printf '%s\n' "$k1" | duti 'wg pubkey')
  k2=$($D2 'wg genkey');  p2=$(printf '%s\n' "$k2" | $D2I 'wg pubkey')
  # dut side
  printf '%s\n' "$k1" | duti 'umask 077; cat > /tmp/wgk'
  dut "sudo wg set wg0 private-key /tmp/wgk listen-port $WGP peer $p2 allowed-ips 10.99.200.113/32,fd99:200::113/128 endpoint $EP; rm -f /tmp/wgk"
  dut "sudo ip addr add 10.99.200.1/24 dev wg0; sudo ip -6 addr add fd99:200::1/64 dev wg0; sudo ip link set wg0 mtu $WGMTU up"
  # dell2 side (in RIG_NS when set: the namespace holding the port facing the DUT)
  printf '%s\n' "$k2" | $D2I 'umask 077; cat > /tmp/wgk'
  $D2 "$NSX ip link del wg0 2>/dev/null; $NSX ip link add wg0 type wireguard &&
       $NSX wg set wg0 private-key /tmp/wgk listen-port $WGP peer $p1 allowed-ips 10.99.200.1/32,10.99.1.0/24,fd99:200::1/128,fd99:1::/64 endpoint [$DUT_OUT]:$WGP &&
       rm -f /tmp/wgk &&
       $NSX ip addr add 10.99.200.113/24 dev wg0 && $NSX ip -6 addr add fd99:200::113/64 dev wg0 && $NSX ip link set wg0 mtu $WGMTU up &&
       true"
  # endpoint with brackets only valid for v6; fix for v4 outer
  if [ "$OUTER" != 6 ]; then $D2 "$NSX wg set wg0 peer $p1 endpoint $DUT_OUT:$WGP"; fi
  # dell2 replies from its tunnel address must go back through wg0, not the plain route via the DUT
  $D2 "$NSX ip rule del priority 12100 2>/dev/null; $NSX ip -6 rule del priority 12100 2>/dev/null;
       $NSX ip rule add priority 12100 from 10.99.200.113 lookup 200; $NSX ip route replace 10.99.1.0/24 dev wg0 table 200;
       $NSX ip -6 rule add priority 12100 from fd99:200::113 lookup 200; $NSX ip -6 route replace fd99:1::/64 dev wg0 table 200"
  # dell1: tunnel subnet via the DUT, route MTU pinned so no PMTUD dependency
  $D1 "$NSX ip route replace 10.99.200.0/24 via 10.99.1.185 mtu $WGMTU; $NSX ip -6 route replace fd99:200::/64 via fd99:1::185 mtu $WGMTU"
  # OpenWrt fw4: wg0 belongs to no zone, so forwarded traffic to/from it falls through to handle_reject.
  # Runtime-only accept rules (comment wgrig), removed again in teardown.
  if [ "$DUT_KIND" = vendor ]; then
    dut 'nft insert rule inet fw4 forward oifname "wg0" counter accept comment "wgrig"; nft insert rule inet fw4 forward iifname "wg0" counter accept comment "wgrig"'
  fi
  # Optional experiment: keep the OUTER tunnel UDP out of conntrack (WG_NOTRACK=1). The serial wg tx worker
  # pays for netfilter hooks on every outer packet; this measures how much of the ceiling that is.
  if [ "${WG_NOTRACK:-0}" = 1 ]; then
    dut "sudo nft add table inet wgnt; sudo nft 'add chain inet wgnt pre { type filter hook prerouting priority -300 ; }'; sudo nft 'add chain inet wgnt out { type filter hook output priority -300 ; }'; sudo nft add rule inet wgnt pre udp dport $WGP notrack; sudo nft add rule inet wgnt out udp sport $WGP notrack"
  fi
  echo dut >> $STATE
}

wg_direct_up(){   # dell1 <-> dell2 over enp2s0 (no DUT); root namespaces
  local k1 k2 p1 p2
  k1=$($D1 'wg genkey'); p1=$(printf '%s\n' "$k1" | $D1I 'wg pubkey')
  k2=$($D2 'wg genkey'); p2=$(printf '%s\n' "$k2" | $D2I 'wg pubkey')
  printf '%s\n' "$k1" | $D1I 'umask 077; cat > /tmp/wgk'
  printf '%s\n' "$k2" | $D2I 'umask 077; cat > /tmp/wgk'
  $D1 "sudo ip link del wgd 2>/dev/null; sudo ip link add wgd type wireguard && sudo wg set wgd private-key /tmp/wgk listen-port $WGPD peer $p2 allowed-ips 10.99.201.113/32 endpoint 10.99.99.113:$WGPD && rm -f /tmp/wgk && sudo ip addr add 10.99.201.112/24 dev wgd && sudo ip link set wgd mtu $WGMTU up"
  $D2 "sudo ip link del wgd 2>/dev/null; sudo ip link add wgd type wireguard && sudo wg set wgd private-key /tmp/wgk listen-port $WGPD peer $p1 allowed-ips 10.99.201.112/32 endpoint 10.99.99.112:$WGPD && rm -f /tmp/wgk && sudo ip addr add 10.99.201.113/24 dev wgd && sudo ip link set wgd mtu $WGMTU up"
  echo direct >> $STATE
}

# WireGuard queues a peer's serial tx worker on CPU (peer->internal_id % online_cpus); internal_id is a global
# counter, so the CPU rotates with every new wg0. For a controlled experiment recreate wg0 until the pegged CPU
# observed under a short probe load equals WG_SERIAL_CPU (at most 12 attempts).
probe_cpu(){
  $D1 "$NSX iperf3 -c 10.99.200.113 -B 10.99.1.112 -p $IPERF_P -P 4 -t 7 -Z >/dev/null 2>&1" &
  local p=$!; sleep 2
  dut 'grep "^cpu[0-9]" /proc/stat; sleep 3; echo --; grep "^cpu[0-9]" /proc/stat' | awk '
    /^--$/{sec=1; next}
    {t=0; for(i=2;i<=11;i++) t+=$i; idle=$5+$6; if(!sec){T0[$1]=t;I0[$1]=idle} else {T1[$1]=t;I1[$1]=idle}}
    END{mx=-1; for(k in T1){d=T1[k]-T0[k]; b=d>0?100*(d-(I1[k]-I0[k]))/d:0; if(b>mx){mx=b;mc=k}} print mc}'
  wait $p
}
place_serial_cpu(){
  local want=cpu$1 got n
  for n in $(seq 1 12); do
    got=$(probe_cpu)
    if [ "$got" = "$want" ]; then echo "serial tx worker placed on $want (attempt $n)"; return 0; fi
    echo "  attempt $n: serial stage on $got, want $want; recreating wg0" >&2
    sleep 8; teardown; wg_dut_up; servers_up; $D1 "$NSX ping -c3 -W2 -I 10.99.1.112 10.99.200.113 >/dev/null 2>&1"
  done
  echo "could not place the serial stage on $want" >&2; return 1
}

servers_up(){
  $D2 "sudo test -f /tmp/wg-iperf.pid && sudo kill \$(cat /tmp/wg-iperf.pid) 2>/dev/null; sleep 0.3; sudo rm -f /tmp/wg-iperf.pid
       $NSX iperf3 -s -p $IPERF_P -D -I /tmp/wg-iperf.pid" ; }
servers_down(){ $D2 "[ -f /tmp/wg-iperf.pid ] && sudo kill \$(cat /tmp/wg-iperf.pid) 2>/dev/null; sudo rm -f /tmp/wg-iperf.pid; true"; }

teardown(){
  servers_down 2>/dev/null
  dut 'sudo nft delete table inet wgnt 2>/dev/null; sudo ip link del wg0 2>/dev/null; rm -f /tmp/wgk; for h in $(nft -a list chain inet fw4 forward 2>/dev/null | sed -n "s/.*wgrig.*# handle \([0-9]*\)/\1/p"); do nft delete rule inet fw4 forward handle $h; done; true' 2>/dev/null
  $D2 "$NSX ip link del wg0 2>/dev/null; $NSX ip rule del priority 12100 2>/dev/null; $NSX ip -6 rule del priority 12100 2>/dev/null;
       $NSX ip route flush table 200 2>/dev/null; $NSX ip -6 route flush table 200 2>/dev/null; sudo ip link del wgd 2>/dev/null; rm -f /tmp/wgk; true" 2>/dev/null
  $D1 "$NSX ip route del 10.99.200.0/24 2>/dev/null; $NSX ip -6 route del fd99:200::/64 2>/dev/null; sudo ip link del wgd 2>/dev/null; rm -f /tmp/wgk; true" 2>/dev/null
  : > $STATE
}

preflight(){
  local out
  out=$(dut 'sudo modprobe wireguard 2>/dev/null; sudo ip link add wgchk type wireguard 2>&1; sudo ip link del wgchk 2>/dev/null; which wg >/dev/null 2>&1 || echo NOWG')
  if [ -n "$out" ]; then
    echo "DUT $DUT ($DUT_KIND): no WireGuard: $out" >&2
    [ "$DUT_KIND" = vendor ] && echo "  .106: feed kmod-wireguard targets kernel hash b33bbf..., the vendor kernel is a2a056...; apk refuses it. Build wireguard.ko against the vendor tree." >&2
    return 1
  fi
  $D1 'which wg iperf3 >/dev/null' && $D2 'which wg iperf3 >/dev/null' || { echo "dells need wireguard-tools + iperf3" >&2; return 1; }
}

# ---------- measurement ----------
parse(){ python3 - "$1" "$STEADY_FROM" "$MODE" <<'PY'
import json,sys
try: j=json.load(open(sys.argv[1]))
except Exception: print("0 0 0"); sys.exit()
f=float(sys.argv[2]); udp=sys.argv[3]=="udp"
def sel(key):
    return [i[key] for i in j.get('intervals',[]) if key in i and i[key]['start']>=f-0.01 and not i[key].get('omitted')]
a=sel('sum'); b=sel('sum_bidir_reverse')
def mean(iv): return sum(x['bits_per_second'] for x in iv)/len(iv)/1e9 if iv else 0
g=mean(a)+mean(b)
e=j.get('end',{}); c=e.get('cpu_utilization_percent',{})
if udp:
    s=e.get('sum',{}); x=s.get('lost_percent',0)
else:
    x=e.get('sum_sent',{}).get('retransmits') or sum(i['sum'].get('retransmits',0) for i in j.get('intervals',[]))
print(f"{g:.3f} {c.get('host_total',0):.0f} {x}")
PY
}

cpu_stats(){ awk '
 /^--$/{sec++; next}
 /^cpu/{ n=$1; t=0; for(i=2;i<=11;i++) t+=$i; idle=$5+$6; s=$8; if(sec==0){T0[n]=t;I0[n]=idle;S0[n]=s} else {T1[n]=t;I1[n]=idle;S1[n]=s} }
 /^N /{ if(sec==0) r0[$2]=$3; else r1[$2]=$3 }
 END{ d=T1["cpu"]-T0["cpu"]; busy=d>0?100*(d-(I1["cpu"]-I0["cpu"]))/d:0; sirq=d>0?100*(S1["cpu"]-S0["cpu"])/d:0; mx=0;
      for(k in T1){ if(k=="cpu")continue; dd=T1[k]-T0[k]; if(dd>0){ b=100*(dd-(I1[k]-I0[k]))/dd; if(b>mx){mx=b; mc=k} } }
      printf "%.1f %.1f %.1f %d %d %s\n", busy, sirq, mx, r1["eth3"]-r0["eth3"], r1["eth4"]-r0["eth4"], mc }' "$1"; }

hits_sum(){ awk '/^--$/{sec++; next} /^E /{ if(sec==0) a[$2]=$3; else b[$2]=$3 } END{ for(k in b){ d=((k in a)&&b[k]>=a[k])?b[k]-a[k]:b[k]; s+=d } print s+0 }' "$1"; }

run_cell(){
  local name=$1 a=$2 b=$3 fam=$4 kind=$5 dir flag tag win
  local NSC="$NSX"; [ "$kind" = direct ] && NSC="sudo"   # direct pair uses root namespaces
  if ! $D1 "$NSC ping -c3 -W2 -i 0.3 -I $a $b >/dev/null 2>&1"; then
    printf '%-12s %s\n' "$name" "SKIP (no path $a -> $b)"; echo "$(date +%H:%M:%S),$name,-,0,0,0,0,0,0,0,0,SKIP,-" >> "$OUT"; return
  fi
  for dir in $DIRS; do
   for rep in $(seq 1 $REPS); do
    if [ "${RECREATE:-0}" = 1 ] && [ "$kind" = dut ]; then   # new wg0 = new peer id = new serial tx worker CPU (id % ncpu)
      teardown; wg_dut_up; servers_up; $D1 "$NSX ping -c3 -W2 -I 10.99.1.112 10.99.200.113 >/dev/null 2>&1"
    fi
    case $dir in up) flag="";; down) flag="-R";; bidir) flag="--bidir";; esac
    win=$((DUR-STEADY_FROM-2)); tag="$name-$dir-r$rep"
    local udp=""; [ "$MODE" = udp ] && udp="-u -b $RATE -l $LEN"
    # server is in RIG_NS on dell2 for DUT cells; the ceiling cell has its own root-ns server
    if [ "$kind" = direct ]; then $D2 "sudo test -f /tmp/wg-iperf-d.pid && sudo kill \$(cat /tmp/wg-iperf-d.pid) 2>/dev/null; sleep 0.3; sudo rm -f /tmp/wg-iperf-d.pid; iperf3 -s -p $IPERF_P -D -I /tmp/wg-iperf-d.pid"; fi
    for attempt in 1 2 3; do
    dut_stat=/tmp/wg-dut.stat
    if [ "$DUT_KIND" = vendor ]; then
      ssh $SSHO -i $HOME/.ssh/vyos_key $DUT sh -s -- $STEADY_FROM $win > $dut_stat <<'EOS' &
sf=$1; w=$2
snap(){ grep '^cpu' /proc/stat; for i in eth3 eth4; do echo "N $i $(cat /sys/class/net/$i/statistics/rx_packets)"; done; echo --; }
sleep $sf; snap; sleep $w; snap
EOS
    else
      ssh $SSHO -i $HOME/.ssh/vyos_key $DUT bash -s -- $STEADY_FROM $win > $dut_stat <<'EOS' &
sf=$1; w=$2
snap(){ grep '^cpu' /proc/stat; for i in eth3 eth4; do echo "N $i $(cat /sys/class/net/$i/statistics/rx_packets)"; done
        sudo cat /sys/kernel/debug/fman_pcd/0/fe_ehash_stats 2>/dev/null | sed -n 's/.*record_dma=\(0x[0-9a-f]*\).*pkt_count=\([0-9]*\).*/E \1 \2/p'; echo --; }
sleep $sf; snap; sleep $w; snap
EOS
    fi
    sp=$!
    $D1 "$NSC iperf3 -c $b -B $a -p $IPERF_P -P $STREAMS -t $DUR -i 1 -Z -J $flag $udp" > "$J/$tag.json" &
    wait $!; wait $sp; sleep 1
    read gbps gen xr < <(parse "$J/$tag.json")
    read busy sirq mx rx3 rx4 mc < <(cpu_stats $dut_stat)
    hw=$(hits_sum $dut_stat)
    if awk -v g="$gbps" 'BEGIN{exit !(g>0.05)}'; then break; fi
    echo "  (rep $rep produced no traffic, attempt $attempt; restarting the iperf3 server and retrying)" >&2
    if [ "$kind" = direct ]; then $D2 "sudo test -f /tmp/wg-iperf-d.pid && sudo kill \$(cat /tmp/wg-iperf-d.pid) 2>/dev/null; sleep 0.3; sudo rm -f /tmp/wg-iperf-d.pid; iperf3 -s -p $IPERF_P -D -I /tmp/wg-iperf-d.pid"; else servers_up; fi
    sleep 5
    done
    # pps on the DUT's wire = rx packets on eth3+eth4 over the window
    pps=$(( (rx3 + rx4) / win ))
    printf '%-12s %-5s r%s %7s Gbit/s  dutbusy %5s%% sirq %5s%% maxcore %5s%%  gen %3s%%  %s %s  pps %s  hw_hits %s  cpu %s\n' \
      "$name" "$dir" "$rep" "$gbps" "$busy" "$sirq" "$mx" "$gen" "$([ "$MODE" = udp ] && echo loss% || echo retr)" "$xr" "$pps" "$hw" "$mc"
    echo "$(date +%H:%M:%S),$name,$dir,$rep,$gbps,$xr,$busy,$sirq,$mx,$gen,$pps,$hw,$mc" >> "$OUT"
    sleep 5
   done
  done
}

cmd=${1:-run}; [ $# -gt 0 ] && shift
case $cmd in
  teardown) teardown; echo "torn down"; exit 0;;
  check)    preflight && echo "preflight OK ($DUT_KIND)"; exit $?;;
  setup|run) : ;;
  *) echo "usage: $0 [setup|teardown|check|run] [cell...]" >&2; exit 2;;
esac
WANT="$*"; [ -z "$WANT" ] && WANT="wg-v4 wg-v6"
preflight || exit 3
envrec(){
  dut 'echo "kernel: $(uname -r) $(uname -v | cut -c1-60)"; echo "lockdep: $([ -e /proc/lockdep ] && echo PRESENT || echo absent)"; \
       zcat /proc/config.gz 2>/dev/null | grep -E "^CONFIG_(PROVE_LOCKING|DEBUG_SPINLOCK|KASAN)=" ; \
       echo "freq_khz: $(cat /sys/devices/system/cpu/cpu0/cpufreq/scaling_cur_freq 2>/dev/null) gov: $(cat /sys/devices/system/cpu/cpu0/cpufreq/scaling_governor 2>/dev/null)"; \
       echo "rps_eth3: $(cat /sys/class/net/eth3/queues/rx-0/rps_cpus 2>/dev/null) uptime: $(cut -d. -f1 /proc/uptime)s"' 2>/dev/null
}
ENVTXT=$(envrec); echo "$ENVTXT"
if echo "$ENVTXT" | grep -q -E "lockdep: PRESENT|PROVE_LOCKING=y|DEBUG_SPINLOCK=y"; then
  echo "REFUSING: DUT kernel has lock debugging enabled; numbers would be invalid (plans/ask2-wireguard-plan.md 2.1)." >&2; exit 5
fi
[ "${KEEP:-0}" = 1 ] || trap teardown EXIT
teardown
needdut=0; needdirect=0
for c in $WANT; do case $c in wg-*) needdut=1;; ceiling-*) needdirect=1;; esac; done
[ $needdut = 1 ] && { wg_dut_up || { echo "DUT tunnel setup failed" >&2; exit 4; }; }
[ $needdirect = 1 ] && wg_direct_up
servers_up
[ -n "${WG_SERIAL_CPU:-}" ] && [ $needdut = 1 ] && { place_serial_cpu "$WG_SERIAL_CPU" || exit 6; }
[ "$cmd" = setup ] && { echo "tunnel up; run cells with: $0 run <cell...>"; trap - EXIT; exit 0; }
if [ $needdut = 1 ]; then   # prime the handshake and prove the path before measuring
  $D1 "$NSX ping -c3 -W2 -I 10.99.1.112 10.99.200.113 >/dev/null 2>&1"
  dut 'sudo wg show wg0 latest-handshakes' | awk '{print "handshake epoch: "$2}'
fi
echo "ts,cell,dir,rep,gbps,retr_or_loss,dut_busy,dut_sirq,dut_maxcore,gen_cpu,dut_rx_pps,hw_hits,maxcpu" > "$OUT"
echo "DUT=$DUT kind=$DUT_KIND mode=$MODE streams=$STREAMS dur=$DUR mtu=$WGMTU outer=v$OUTER"
for line in $CELLS_ALL; do
  IFS='|' read -r name a b fam kind <<<"$line"
  [[ " $WANT " == *" $name "* ]] || continue
  run_cell "$name" "$a" "$b" "$fam" "$kind"
done
python3 - "$OUT" <<'PY'
import csv,sys,statistics as st,collections
rows=[r for r in csv.DictReader(open(sys.argv[1])) if r['dir']!='-' and float(r['gbps'])>0.05]   # zero-traffic reps are failures, never averaged
g=collections.OrderedDict()
for r in rows: g.setdefault((r['cell'],r['dir']),[]).append(r)
print("\nSUMMARY (steady-window Gbit/s over reps)")
print("%-12s %-6s %3s %8s %8s %8s %7s %9s %9s"%("cell","dir","n","mean","min","max","sd","dut_busy","maxcore"))
for (c,d),v in g.items():
    x=[float(r['gbps']) for r in v]; b=[float(r['dut_busy']) for r in v]; m=[float(r['dut_maxcore']) for r in v]
    print("%-12s %-6s %3d %8.3f %8.3f %8.3f %7.3f %8.1f%% %8.1f%%"%(c,d,len(x),st.mean(x),min(x),max(x),st.pstdev(x) if len(x)>1 else 0,st.mean(b),st.mean(m)))
PY
echo "csv: $OUT"
