#!/usr/bin/env bash
# testrig-pktgen.sh — ASK2 Phase 0.4 UDP line-rate harness (A2 64 B/IMIX, UDP vlan<->vlan, flow scale).
# Generator: kernel pktgen on dell1's X710 (THREADS TX threads/queues, pps rate control, IMIX).
# Counting: X710 MAC counters, port.tx_unicast on dell1 and port.rx_unicast on dell2, so neither
# host's socket path limits the result (the 2026-10-04 Python receiver and ConnectX-3 5.9 Mpps TX
# ceiling did). No DPDK: both X710 functions share IOMMU group 2, so vfio would take the .185 path down too.
#
# Each trial first primes the flow (dell1 sends, dell2 answers the address it saw, dell1 sends
# again) so conntrack is ESTABLISHED and the DUT offloads it, also through NAT. The DUT-side HW
# share is reported: ask2 = fe_ehash_stats pkt_count growth / sent; vendor = 1 - frames handed to
# the kernel (ethtool -S eth3 'rx packets [TOTAL]') / sent.
#
# Usage (control host; rig per bin/testrig-dell-net.sh):
#   DUT_KIND=ask2|vendor bin/testrig-pktgen.sh trial PATH SIZE PPS SECS  one run, PPS 0 = as fast as possible
#   DUT_KIND=ask2|vendor bin/testrig-pktgen.sh a2 PATH SIZE              max rate at <= LOSS % loss, then median of 3
#   DUT_KIND=ask2|vendor bin/testrig-pktgen.sh flows PATH N PPS          N single-packet flows (distinct UDP sports)
# PATH: port-v4 port-v6 vlan-v4 vlan-v6.  SIZE: wire bytes incl. FCS (pktgen floor: 64 v4, 82 v6) or imix
# (7:4:1 of 64/570/1518).  Env: LOSS (0.001 %), TRIAL (10 s), STEPS (7), THREADS (4), BURST (32, only for
# PPS 0: pktgen's ratep with burst > 1 overshoots and sends line-rate micro-bursts), OUT (csv).
#   BIDIR=1   dell2 transmits the exact reverse tuples at the same time (PPS, line rate and counters are then
#             totals over both directions). Each trial first warms every flow at 50 kpps per direction, so
#             conntrack sees both directions and the DUT offloads them. port-v6 is refused (NAT66 on eth4).
#   FLOWS=N   N flows (UDP source ports 10000..10000+N-1, dport 9000) instead of one (9000 -> 9000).
#             One-directional runs with FLOWS > 1 are never primed, so they stay on the kernel path.
#   NOPRIME=1 skip priming of the single flow: it stays on the kernel path (the miss path when ASK is engaged).
#             With BIDIR=1, dell2 sends its own flows (sports as dell1, dport 9001) instead of the reverse
#             tuples and nothing is warmed, so both directions stay on the kernel path.
#   CONFIRM=N confirm runs after an a2 search (3).
#   DRY=1     print the pktgen configuration instead of running it (the counters are still read).
#   MAXPPS=N  a2 searches 0..N only and skips the unpaced line-rate probe (kernel-path runs).
# a2-result CSV rows: req_pps = max rate, offered_pps = median delivered, tx = mean delivered, rx = max
# deviation of the 3 confirm runs from the mean, in %.
# Generator ceilings (dell1, iommu=pt, 64 B): unpaced 14.0-14.1 Mpps; paced (burst 1, 4 threads)
# accurate to ~2.5 % up to 12 Mpps. Calibrate with DST_MAC=02:00:00:00:00:99 (the DUT discards it).
set -u
DUT_KIND=${DUT_KIND:-ask2}; LOSS=${LOSS:-0.001}; TRIAL=${TRIAL:-10}; STEPS=${STEPS:-7}
THREADS=${THREADS:-4}; BURST=${BURST:-32}; BIDIR=${BIDIR:-0}; FLOWS=${FLOWS:-1}; NOPRIME=${NOPRIME:-0}; DRY=${DRY:-0}
OUT=${OUT:-/mnt/builds/ask2-review/oracle/pktgen-$DUT_KIND-$(date +%Y%m%d).csv}
SSHO="-o BatchMode=yes -o ConnectTimeout=8"
D1="ssh -n $SSHO admin@192.168.1.112"; D2="ssh -n $SSHO admin@192.168.1.113"; D1S="ssh $SSHO admin@192.168.1.112"
D2S="ssh $SSHO admin@192.168.1.113"
if [ "$DUT_KIND" = vendor ]; then
  DUT=root@192.168.1.106; IF=enp1s0f1np1; NS="sudo ip netns exec n106"
else
  DUT=vyos@192.168.1.185; IF=enp1s0f0np0; NS="sudo"
fi
dut(){ local c=$1; [ "$DUT_KIND" = vendor ] && c=${c//sudo /}; ssh -n $SSHO -i $HOME/.ssh/vyos_key $DUT "$c"; }

path_set(){ # -> FAM SRC DST VLAN
  case $1 in
    port-v4) FAM=4; SRC=10.99.1.112;  DST=10.99.2.113;  VLAN=0 ;;
    port-v6) FAM=6; SRC=fd99:1::112;  DST=fd99:2::113;  VLAN=0 ;;
    vlan-v4) FAM=4; SRC=10.99.10.112; DST=10.99.20.113; VLAN=10 ;;
    vlan-v6) FAM=6; SRC=fd99:10::112; DST=fd99:20::113; VLAN=10 ;;
    *) echo "unknown path $1"; exit 2 ;;
  esac
  GW=10.99.1.185; [ $FAM = 6 ] && GW=fd99:1::185
  [ $VLAN != 0 ] && { GW=10.99.10.185; [ $FAM = 6 ] && GW=fd99:10::185; }
  $D1 "$NS ping -c1 -W1 $GW >/dev/null"
  MAC=$($D1 "$NS ip neigh show $GW" | awk '{for(i=1;i<NF;i++) if($i=="lladdr") print $(i+1)}' | head -1)
  MAC=${DST_MAC:-$MAC}   # DST_MAC=<unused unicast MAC>: DUT discards at its MAC filter (generator-only calibration)
  [ -n "$MAC" ] || { echo "no neighbour for $GW"; exit 1; }
  [ "$BIDIR" = 1 ] || return 0
  [ "$1" = port-v6 ] && { echo "BIDIR port-v6: the reverse tuple is NAT66-masqueraded on eth4; use vlan-v6"; exit 2; }
  SRC2=$DST; DST2=$SRC; VLAN2=0; GW2=10.99.2.185
  [ $VLAN != 0 ] && { VLAN2=20; GW2=10.99.20.185; }
  [ $FAM = 6 ] && GW2=fd99:20::185
  $D2 "$NS ping -c1 -W1 $GW2 >/dev/null"
  MAC2=$($D2 "$NS ip neigh show $GW2" | awk '{for(i=1;i<NF;i++) if($i=="lladdr") print $(i+1)}' | head -1)
  MAC2=${DST_MAC:-$MAC2}
  [ -n "$MAC2" ] || { echo "no neighbour for $GW2 on dell2"; exit 1; }
}
lrate(){ echo $(( $(linerate "$1") * (BIDIR == 1 ? 2 : 1) )); }   # both directions count in BIDIR
# line-rate pps for a wire size (20 B preamble + IFG per frame); imix = 7:4:1 of 64/570/1518
linerate(){ awk -v s="$1" 'BEGIN{if(s=="imix") s=(7*64+4*570+1518)/12; printf "%d", 1e10/((s+20)*8)}'; }

prime(){ # $1 = UDP port used by both ends
  local p=$1 py
  py="import socket,sys;f=socket.AF_INET6 if ':' in sys.argv[1] else socket.AF_INET;s=socket.socket(f,socket.SOCK_DGRAM);s.bind((sys.argv[1],int(sys.argv[2])));s.settimeout(4)
try:
  d,a=s.recvfrom(64);s.sendto(b'r',a)
except Exception: pass"
  $D2 "$NS timeout 5 python3 -c \"$py\" $DST $p" &
  local l=$!; sleep 0.7
  $D1 "$NS python3 -c \"import socket,sys,time;f=socket.AF_INET6 if ':' in sys.argv[1] else socket.AF_INET;s=socket.socket(f,socket.SOCK_DGRAM);s.bind((sys.argv[1],int(sys.argv[3])));[ (s.sendto(b'p',(sys.argv[2],int(sys.argv[3]))),time.sleep(0.5)) for _ in range(3)]\" $SRC $DST $p"
  wait $l; sleep 1
}

ctr(){ # host (d1|d2) dir (tx|rx) -> MAC unicast counter of the DUT-facing port
  local h=$D1; [ $1 = d2 ] && h=$D2
  $h "$NS ethtool -S $IF" | awk "/port.${2}_unicast:/{print \$2}"
}
proof(){
  if [ "$DUT_KIND" = vendor ]; then dut "ethtool -S eth3; ethtool -S eth4" | awk '/rx packets \[TOTAL\]/{k+=$NF} END{print "K", k}'
  else dut 'sudo cat /sys/kernel/debug/fman_pcd/0/fe_ehash_stats' | sed -n 's/.*record_dma=\(0x[0-9a-f]*\).*pkt_count=\([0-9]*\).*/E \1 \2/p'; fi
}

pgsh(){ # host-cmd: run stdin as root on that dell, or print it (DRY=1)
  if [ "$DRY" = 1 ]; then echo "--- pktgen on ${1##* }" >&2; cat >&2; echo 1000000; else $1 "$NS bash -s"; fi
}
pg_run(){ # dir(1 = dell1->dell2, 2 = dell2->dell1) size pps count udp_src_min udp_src_max udp_dst threads -> us
  local dir=$1; shift
  local size=$1 pps=$2 cnt=$3 smin=$4 smax=$5 dp=$6 nt=$7
  local h=$D1S s=$SRC d=$DST m=$MAC v=$VLAN s0=$smin s1=$smax d0=$dp d1=$dp
  [ $dir = 2 ] && { h=$D2S; s=$SRC2; d=$DST2; m=$MAC2; v=$VLAN2; s0=$dp; s1=$dp; d0=$smin; d1=$smax; }
  [ $dir = 2 ] && [ "$NOPRIME" = 1 ] && { s0=$smin; s1=$smax; d0=9001; d1=9001; }   # never a reply
  pgsh "$h" <<EOF
set -e
modprobe pktgen
pg(){ echo "\$2" > /proc/net/pktgen/\$1; }
pg pgctrl reset
for t in \$(seq 0 7); do pg kpktgend_\$t rem_device_all; done
for t in \$(seq 0 $((nt-1))); do
  d=$IF@\$t; pg kpktgend_\$t "add_device \$d"
  pg \$d "count $((cnt/nt))"; pg \$d "delay 0"
  if [ $size = imix ]; then pg \$d "imix_weights 60,7 566,4 1514,1"; pg \$d "burst 1"; pg \$d "clone_skb 0"
  elif [ $smin != $smax ]; then pg \$d "pkt_size $((${size/imix/64}-4))"; pg \$d "burst 1"; pg \$d "clone_skb 0"
  else pg \$d "pkt_size $((${size/imix/64}-4))"; pg \$d "burst $([ $pps -gt 0 ] && echo 1 || echo $BURST)"; pg \$d "clone_skb 1000000"; fi
  [ $pps -gt 0 ] && pg \$d "ratep $((pps/nt))"
  pg \$d "dst_mac $m"; pg \$d "queue_map_min \$t"; pg \$d "queue_map_max \$t"
  if [ $FAM = 6 ]; then pg \$d "dst6 $d"; pg \$d "src6 $s"
  else pg \$d "dst_min $d"; pg \$d "dst_max $d"; pg \$d "src_min $s"; pg \$d "src_max $s"; fi
  pg \$d "udp_src_min $s0"; pg \$d "udp_src_max $s1"; pg \$d "udp_dst_min $d0"; pg \$d "udp_dst_max $d1"
  [ $v -gt 0 ] && pg \$d "vlan_id $v"
  [ $FAM = 6 ] && pg \$d "flag UDPCSUM"
  pg \$d "flag NO_TIMESTAMP"
done
t0=\$(date +%s%N); pg pgctrl start; echo \$(( (\$(date +%s%N)-t0)/1000 ))
pg pgctrl reset
EOF
}

# one measured run -> globals TX RX LOSSP OPPS HWS
measure(){ # size pps count smin smax dp threads
  local t0 r0 p0 t1 r1 p1 t2 r2 us us2 f
  t0=$(ctr d1 tx); r0=$(ctr d2 rx); p0=$(proof)
  if [ "$BIDIR" = 1 ]; then   # half the rate and count each way, both dells at once
    t2=$(ctr d2 tx); r2=$(ctr d1 rx); f=$(mktemp)
    pg_run 2 "$1" $(( $2 / 2 )) $(( $3 / 2 )) "$4" "$5" "$6" "$7" > "$f" &
    us=$(pg_run 1 "$1" $(( $2 / 2 )) $(( $3 / 2 )) "$4" "$5" "$6" "$7"); wait $!
    us2=$(cat "$f"); rm -f "$f"; [ "${us2:-0}" -gt "${us:-0}" ] && us=$us2
  else
    us=$(pg_run 1 "$@")   # pktgen wall time; offered pps = MAC TX count / that time
  fi
  sleep 1
  [ "$DRY" = 1 ] || dut true || { echo "DUT unreachable after the trial: stopping"; exit 3; }
  t1=$(ctr d1 tx); r1=$(ctr d2 rx); p1=$(proof)
  TX=$((t1-t0)); RX=$((r1-r0))
  [ "$BIDIR" = 1 ] && { TX=$((TX + $(ctr d2 tx) - t2)); RX=$((RX + $(ctr d1 rx) - r2)); }
  OPPS=$(( us > 0 ? TX*1000000/us : 0 ))
  LOSSP=$(awk -v t=$TX -v r=$RX 'BEGIN{if(t>0) printf "%.5f", 100*(t-r)/t; else print "100"}')
  if [ "$DUT_KIND" = vendor ]; then
    HWS=$(printf '%s\n%s\n' "$p0" "$p1" | awk -v t=$TX 'NR==1{a=$2} NR==2{k=$2-a} END{if(t>0) printf "%.3f", 1-k/t; else print 0}')
  else
    HWS=$( { echo "$p0"; echo SEP; echo "$p1"; } | awk -v t=$TX '/^SEP/{s=1;next} /^E /{if(!s) a[$2]=$3; else b[$2]=$3} END{for(k in b){d=((k in a)&&b[k]>=a[k])?b[k]-a[k]:b[k]; h+=d}; if(t>0) printf "%.3f", h/t; else print 0}')
  fi
}
log(){ # mode path size req
  printf '%-6s %-8s %-5s req=%-9s offered=%-9s tx=%-11s rx=%-11s loss=%-9s hw=%s\n' "$1" "$2" "$3" "$4" "$OPPS" "$TX" "$RX" "$LOSSP%" "$HWS"
  [ -f "$OUT" ] || echo "ts,dut,mode,path,size,req_pps,offered_pps,tx,rx,loss_pct,hw_share" > "$OUT"
  echo "$(date +%F_%T),$DUT_KIND,$1,$2,$3,$4,$OPPS,$TX,$RX,$LOSSP,$HWS" >> "$OUT"
}
warm(){ # size sport_min: keep every flow alive at 50 kpps each way until the DUT has offloaded all of them.
  # 2026-10-10: inserts run at ~255 flows/s (~32 s for 8192) while idle records age out after 30 s, and a
  # line-rate flood racing the inserts wedged the FE (FMFP_EXTC INV0 stuck); so traffic continues until
  # conntrack shows every flow [HW_OFFLOAD], and only then does the measured trial start.
  local f b1 b2 n=0 last=-1 still=0 t want=$((FLOWS - FLOWS / 200))   # >= 99.5 %: a rare flow misses
  f=$(mktemp)
  pg_run 2 "$1" 50000 $((50000 * 180)) "$2" $(($2 + FLOWS - 1)) 9000 "$THREADS" > "$f" & b2=$!
  pg_run 1 "$1" 50000 $((50000 * 180)) "$2" $(($2 + FLOWS - 1)) 9000 "$THREADS" > /dev/null & b1=$!
  if [ "$DUT_KIND" = vendor ] || [ "$DRY" = 1 ]; then sleep 5; n=$FLOWS
  else
    for t in $(seq 1 180); do
      sleep 1
      n=$(dut "sudo conntrack -L -p udp 2>/dev/null | grep HW_OFFLOAD | grep -c '=9000 ' || true"); n=${n:-0}
      [ "$n" -ge "$want" ] && break
      if [ "$n" = "$last" ]; then still=$((still + 1)); [ $still -ge 20 ] && break; else still=0; fi
      last=$n
    done
    echo "warm: $n of $FLOWS flows in hardware after ${t} s" >&2
  fi
  $D1 "$NS sh -c 'echo stop > /proc/net/pktgen/pgctrl'"; $D2 "$NS sh -c 'echo stop > /proc/net/pktgen/pgctrl'"
  wait $b1 $b2; rm -f "$f"
  [ "$n" -ge "$want" ] || { echo "warm: only $n of $FLOWS flows offloaded; refusing to flood"; exit 4; }
}
trial(){ # path size pps secs
  local pps=$3 n sp=9000
  [ "${4:-0}" -ge 1 ] || { echo "trial: SECS must be >= 1 (pktgen count 0 means send forever)"; exit 2; }
  n=$(( ${pps:-0} > 0 ? pps*$4 : $(lrate $2)*$4 ))
  [ "$FLOWS" -gt 1 ] && sp=10000
  if [ "$NOPRIME" = 1 ]; then :
  elif [ "$BIDIR" = 1 ]; then warm "$2" $sp
  elif [ "$FLOWS" = 1 ]; then prime 9000; fi
  measure "$2" "$pps" "$n" $sp $((sp + FLOWS - 1)) 9000 "$THREADS"
  log trial "$1" "$2" "$pps"
}
ok(){ awk -v l="$LOSSP" -v m="$LOSS" 'BEGIN{exit !(l+0<=m+0)}'; }

cmd=${1:-}; shift || true
case $cmd in
  trial) path_set "$1"; trial "$1" "$2" "$3" "${4:-$TRIAL}" ;;
  a2)
    path_set "$1"; lr=$(lrate "$2"); lo=0; hi=$lr; best=0
    [ "${MAXPPS:-0}" -gt 0 ] && [ "$MAXPPS" -lt $lr ] && hi=$MAXPPS   # cap the search, no line-rate probe
    if [ $hi = $lr ]; then   # unpaced burst mode: paced pktgen tops out near 12 Mpps (4 threads, 64 B)
      trial "$1" "$2" 0 "$TRIAL"; ok && best=$lr
    fi
    if [ $best = 0 ]; then
      for _ in $(seq 1 $STEPS); do
        mid=$(( (lo+hi)/2 )); trial "$1" "$2" "$mid" "$TRIAL"
        if ok; then lo=$mid; best=$mid; else hi=$mid; fi
      done
    fi
    [ $best -gt 0 ] || { echo "a2 $1 $2: no rate met ${LOSS}% (lowest tried $hi pps)"; exit 1; }
    res=""
    cr=$best; [ $best = $lr ] && cr=0   # line rate passed: confirm unpaced too
    for _ in $(seq 1 ${CONFIRM:-3}); do trial "$1" "$2" "$cr" "$TRIAL"; res="$res $(awk -v t=$TX -v r=$RX -v o=$OPPS 'BEGIN{printf "%d", (t>0)? o*r/t : 0}'):$LOSSP:$HWS"; done
    med=$(echo $res | tr ' ' '\n' | sort -t: -k1,1n | sed -n "$(( (${CONFIRM:-3} + 1) / 2 ))p")
    IFS=: read -r mpps mloss mhw <<<"$med"
    read -r mean dev < <(echo $res | tr ' ' '\n' | cut -d: -f1 | awk '{v[NR]=$1; s+=$1} END{m=s/NR; for(i in v){d=v[i]-m; if(d<0)d=-d; if(d>x)x=d}; printf "%d %.1f\n", m, (m>0 ? 100*x/m : 0)}')
    gb=$(awk -v p=$mean -v s="$2" 'BEGIN{if(s=="imix") s=(7*64+4*570+1518)/12; printf "%.2f", p*(s+20)*8/1e9}')
    printf 'A2 %-6s %-8s %-5s%s line=%s pps  max_rate=%s pps (%.1f%% of line)  median_of_3: delivered=%s pps loss=%s%% hw=%s  mean=%s pps (%s Gbit/s L1) +-%s%%\n' \
      "$DUT_KIND" "$1" "$2" "$([ "$BIDIR" = 1 ] && echo ' bidir')" "$lr" "$best" "$(awk -v b=$best -v l=$lr 'BEGIN{print 100*b/l}')" "$mpps" "$mloss" "$mhw" "$mean" "$gb" "$dev"
    echo "$(date +%F_%T),$DUT_KIND,a2-result$([ "$BIDIR" = 1 ] && echo -bidir),$1,$2,$best,$mpps,$mean,$dev,$mloss,$mhw" >> "$OUT" ;;
  flows)
    path_set "$1"; n=$2; pps=$3
    for rep in 1 2 3; do   # a fresh UDP dport per rep: earlier reps' conntrack entries must not be reused
      measure 64 "$pps" "$n" 10000 $((10000+n-1)) $((9000+rep)) 1
      log flows "$1" "$n" "$pps"
    done ;;
  *) sed -n '2,/^set -u/p' "$0" | sed '$d'; exit 2 ;;
esac
