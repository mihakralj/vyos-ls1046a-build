#!/usr/bin/env bash
# Quick per-offload throughput check: one DUR-second run per cell (default 30 s),
# iperf3 -Z unidir, steady value = mean of the receiver intervals from STEADY_FROM s.
# Cells: unicast, NAT, VLAN-VLAN, PPPoE (down = decap, up = encap), IPv4 and IPv6.
# The `-rev` cells run the same path dell2 -> dell1 (in eth4, out eth3) to check both ports separately.
# Each cell also records an offload proof so a software-forwarded run cannot pass:
# the per-record pkt_count growth in fe_ehash_stats over the steady window, compared
# with the data frames iperf3 reports for the same window.
#
# Rig: dell1 (enp1s0f0np0, .10) <-> DUT .185 eth3, dell2 (enp1s0f0np0, .20) <-> DUT .185 eth4
# (X710 port 1; persistent config: bin/testrig-dell-net.sh).
# NAT cells use runtime nft tables (table ask2q) masquerading out of eth4; the VyOS
# NAT66 rule is removed for the run and restored on exit. Nothing persistent changes.
#
# Combo cells (opt-in, not in the default run; name them or pass `combo`):
#   nat-pppoe-up-v4  NAT44 masquerade on pppoe10, LAN -> PPPoE (encap + NAT, decap replies)
#   nat-vlan-v4      NAT44 masquerade on eth4.20, VLAN 10 -> VLAN 20
#   nat-vlan-v6      NAT66 masquerade on eth4.20, VLAN 10 -> VLAN 20
#
# PPPoE cells enable RPS on dell1's DUT-facing NIC for the run (see rps_set); other cells leave it off.
# Usage: bin/testrig-offload-quick.sh [cell ...]       (default: the 10 base cells)
# Env:   DUT DUR STEADY_FROM STREAMS OUT BIDIR J SAMPLE RUN_ON OUTDIR (SAMPLE=1: ASK2 per-second conntrack+CPU sampler)
set -u
DUT=${DUT:-vyos@192.168.1.185}
DUR=${DUR:-30}; STEADY_FROM=${STEADY_FROM:-10}; STREAMS=${STREAMS:-8}
# BIDIR=1: one client with iperf3 --bidir (both directions' streams connect before data flows);
# Gbit/s and retransmits are the sum of both directions.
BIDIR=${BIDIR:-0}; BIDIR_ARG=""; [ "$BIDIR" = 1 ] && BIDIR_ARG="--bidir"
# RUN_ON=dell2: the script is stored and run on dell2 (ssh to dell1 and the DUT starts from the rig,
# not from the agent host): dell2 commands run locally, dell1 and the DUT go over ssh with dell2's
# own ~/.ssh/vyos_key, and results default to ~/testrig/out. Default (agent) is the old behaviour.
RUN_ON=${RUN_ON:-agent}
if [ "$RUN_ON" = dell2 ]; then OUTDIR=${OUTDIR:-$HOME/testrig/out}; else OUTDIR=${OUTDIR:-/mnt/builds/ask2-review/oracle}; fi
OUT=${OUT:-$OUTDIR/quick-$(date +%Y%m%d-%H%M).csv}
J=${J:-$OUTDIR/json-quick}; mkdir -p "$J"
SSHO="-o BatchMode=yes -o ConnectTimeout=8"
# DUT_KIND=ask2 (.185 VyOS: sudo, HW proof = fe_ehash_stats record hits) or vendor (.106 OpenWrt
# vendor ASK: root, no sudo/bash, HW proof = frames that never reach the kernel: ethtool -S
# 'rx packets [TOTAL]', as baseline3.sh PROOF=kernelrx; the netdev rx_packets counts fast-path frames too).
# RIG_NS=n106 runs the dell side inside the n106 netns (the X710 port facing .106).
DUT_KIND=${DUT_KIND:-ask2}; RIG_NS=${RIG_NS:-}
NSX=""; [ -n "$RIG_NS" ] && NSX="sudo ip netns exec $RIG_NS"
dut(){ local c=$1; [ "$DUT_KIND" = vendor ] && c=${c//sudo /}; ssh -n $SSHO -i $HOME/.ssh/vyos_key $DUT "$c"; }
S=dut
SI="ssh $SSHO -i $HOME/.ssh/vyos_key $DUT"
D1="ssh -n $SSHO admin@192.168.1.112"; D2="ssh -n $SSHO admin@192.168.1.113"
[ "$RUN_ON" = dell2 ] && { D1="ssh -n $SSHO -i $HOME/.ssh/vyos_key admin@192.168.1.112"; D2="bash -c"; }

# name|nat|client|bind(source) address|server address
CELLS_ALL="
unicast-v4|none|d1|10.99.1.112|10.99.2.113
unicast-v6|none|d1|fd99:1::112|fd99:2::113
nat-v4|4|d1|10.99.1.112|10.99.2.113
nat-v6|6|d1|fd99:1::112|fd99:2::113
vlan-v4|none|d1|10.99.10.112|10.99.20.113
vlan-v6|none|d1|fd99:10::112|fd99:20::113
unicast-v4-rev|none|d2|10.99.2.113|10.99.1.112
unicast-v6-rev|none|d2|fd99:2::113|fd99:1::112
nat-v4-rev|4r|d2|10.99.2.113|10.99.1.112
nat-v6-rev|6r|d2|fd99:2::113|fd99:1::112
vlan-v4-rev|none|d2|10.99.20.113|10.99.10.112
vlan-v6-rev|none|d2|fd99:20::113|fd99:10::112
vlanport-v4|none|d1|10.99.10.112|10.99.2.113
portvlan-v4|none|d2|10.99.2.113|10.99.10.112
vlanport-v6|none|d1|fd99:10::112|fd99:2::113
portvlan-v6|none|d2|fd99:2::113|fd99:10::112
pppoe-down-v4|none|d1|10.99.50.1|10.99.2.113
pppoe-up-v4|none|d2|10.99.2.113|10.99.50.1
pppoe-down-v6|none|d1|fd99:50::1|fd99:2::113
pppoe-up-v6|none|d2|fd99:2::113|fd99:50::1
"
# opt-in combo cells: only run when named (or via `combo`)
CELLS_COMBO="
nat-pppoe-up-v4|4p|d2|10.99.2.113|10.99.50.1
nat-vlan-v4|4v|d1|10.99.10.112|10.99.20.113
nat-vlan-v6|6v|d1|fd99:10::112|fd99:20::113
"
WANT="$*"
case " $WANT " in *" combo "*) WANT="$WANT nat-pppoe-up-v4 nat-vlan-v4 nat-vlan-v6" ;; esac
CELLS_RUN="$CELLS_ALL"
[ -n "$WANT" ] && CELLS_RUN="$CELLS_ALL $CELLS_COMBO"

NAT66_RULE='oifname "eth4" ip6 saddr fd99:1::/64 counter masquerade comment "SRC-NAT66-100"'
vyos_nat66_present=0
$S 'sudo nft list chain ip6 vyos_nat POSTROUTING 2>/dev/null' | grep -q SRC-NAT66-100 && vyos_nat66_present=1

nat_clear(){ $S 'sudo nft delete table ip ask2q 2>/dev/null; sudo nft delete table ip6 ask2q 2>/dev/null; true'; }
nat_set(){
  nat_clear
  case $1 in
    4) $S 'sudo nft add table ip ask2q && sudo nft "add chain ip ask2q post { type nat hook postrouting priority srcnat ; }" && sudo nft add rule ip ask2q post oifname eth4 ip saddr 10.99.1.0/24 masquerade' ;;
    4p) $S 'sudo nft add table ip ask2q && sudo nft "add chain ip ask2q post { type nat hook postrouting priority srcnat ; }" && sudo nft add rule ip ask2q post oifname pppoe10 ip saddr 10.99.2.0/24 masquerade' ;;
    4v) $S 'sudo nft add table ip ask2q && sudo nft "add chain ip ask2q post { type nat hook postrouting priority srcnat ; }" && sudo nft add rule ip ask2q post oifname eth4.20 ip saddr 10.99.10.0/24 masquerade' ;;
    4r) $S 'sudo nft add table ip ask2q && sudo nft "add chain ip ask2q post { type nat hook postrouting priority srcnat ; }" && sudo nft add rule ip ask2q post oifname eth3 ip saddr 10.99.2.0/24 masquerade' ;;
    6r) $S 'sudo nft add table ip6 ask2q && sudo nft "add chain ip6 ask2q post { type nat hook postrouting priority srcnat ; }" && sudo nft add rule ip6 ask2q post oifname eth3 ip6 saddr fd99:2::/64 masquerade' ;;
    6v) $S 'sudo nft add table ip6 ask2q && sudo nft "add chain ip6 ask2q post { type nat hook postrouting priority srcnat ; }" && sudo nft add rule ip6 ask2q post oifname eth4.20 ip6 saddr fd99:10::/64 masquerade' ;;
    6) $S 'sudo nft add table ip6 ask2q && sudo nft "add chain ip6 ask2q post { type nat hook postrouting priority srcnat ; }" && sudo nft add rule ip6 ask2q post oifname eth4 ip6 saddr fd99:1::/64 masquerade' ;;
  esac
}
# dell1's mlx4 NIC cannot RSS-hash PPPoE frames, so every returning ACK lands on RX queue 0 / CPU0
# and the whole TCP send path runs in that one softirq (about 5.9 Gbit/s ceiling, DUT idle).
# RPS hashes on the inner flow instead. Runtime-only; applied for PPPoE cells, restored on exit.
D1_IF=${D1_IF:-enp1s0f0np0}
[ -n "$RIG_NS" ] && D1_IF=${D1_IF_NS:-enp1s0f1np1}   # the dell1 port facing .106 lives in the netns; sysfs shows it only from inside
rps_set(){
  local m=0
  [ "$1" = on ] && m='$(printf %x $(( (1<<$(nproc)) - 1 )))'
  if [ -n "$RIG_NS" ]; then
    $D1 "sudo ip netns exec $RIG_NS sh -c 'm=$m; for q in /sys/class/net/$D1_IF/queues/rx-*/rps_cpus; do echo \$m > \$q; done'"
  else
    $D1 "m=$m; for q in /sys/class/net/$D1_IF/queues/rx-*/rps_cpus; do echo \$m | sudo -n tee \$q >/dev/null; done"
  fi
}
cleanup(){
  rps_set off 2>/dev/null
  nat_clear
  if [ $vyos_nat66_present = 1 ]; then
    $S "sudo nft list chain ip6 vyos_nat POSTROUTING | grep -q SRC-NAT66-100 || sudo nft add rule ip6 vyos_nat POSTROUTING $NAT66_RULE"
  fi
}
trap cleanup EXIT
if [ $vyos_nat66_present = 1 ]; then
  $S 'h=$(sudo nft -a list chain ip6 vyos_nat POSTROUTING | sed -n "s/.*SRC-NAT66-100.*# handle \([0-9]*\)/\1/p"); [ -n "$h" ] && sudo nft delete rule ip6 vyos_nat POSTROUTING handle $h'
fi

parse(){ python3 - "$1" "$STEADY_FROM" <<'PY'
import json,sys
with open(sys.argv[1]) as source:
    j=json.load(source)
if j.get('error'):
    sys.exit(f"iperf3 failed: {j['error']}")
f=float(sys.argv[2])
ok=[i for i in j.get('intervals',[]) if i['sum']['start']>=f-0.01 and not i['sum'].get('omitted')]
# --bidir: the reverse direction is reported separately per interval
bps=[i['sum']['bits_per_second']+i.get('sum_bidir_reverse',{}).get('bits_per_second',0) for i in ok]
g=sum(bps)/len(bps)/1e9 if bps else 0
bytes_=sum(i['sum']['bytes']+i.get('sum_bidir_reverse',{}).get('bytes',0) for i in ok)
e=j.get('end',{}); c=e.get('cpu_utilization_percent',{})
r=e.get('sum_sent',{}).get('retransmits')
if r is None: r=sum(i['sum'].get('retransmits',0) for i in j.get('intervals',[]))
r+=e.get('sum_sent_bidir_reverse',{}).get('retransmits',0) or 0
print(f"{g:.3f} {c.get('host_total',0):.0f} {r} {bytes_}")
PY
}

echo "ts,cell,gbps,dut_busy,dut_softirq,gen_cpu,retrans,hw_pkts,data_pkts_est,hw_ratio,verdict" > "$OUT"
printf '%-15s %7s %6s %6s %6s %8s %12s %6s  %s\n' cell Gbit/s busy% sirq% gen% retrans hw_pkts ratio verdict

for line in $CELLS_RUN; do
  IFS='|' read -r name nat cli a b <<<"$line"
  if [ -n "$WANT" ] && ! [[ " $WANT " == *" $name "* ]]; then continue; fi
  [ $cli = d1 ] && G="$D1" || G="$D2"
  if ! $G "$NSX ping -c2 -W2 -i 0.3 -I $a $b >/dev/null 2>&1"; then
    printf '%-15s %s\n' "$name" "SKIP (no path $a -> $b)"; echo "$(date +%H:%M:%S),$name,0,0,0,0,0,0,0,0,SKIP" >> "$OUT"; continue
  fi
  if ! nat_set "$nat" >/dev/null; then
    echo "ERROR: NAT setup failed for $name" >&2
    exit 1
  fi
  case $name in *pppoe*) rps_set on ;; *) rps_set off ;; esac
  tag="$name"; [ "$BIDIR" = 1 ] && tag="$name-bidir"
  if [ "$DUT_KIND" = vendor ]; then
  $SI sh -s -- $STEADY_FROM $((DUR-STEADY_FROM-2)) > /tmp/quick.stat <<'EOS' &
sf=$1; w=$2
rx(){ for i in eth3 eth4; do ethtool -S $i | awk -v i=$i '/rx packets \[TOTAL\]/{print "K", i, $NF}'; done; }   # SDK: frames handed to the kernel
sleep $sf; head -1 /proc/stat; rx; sleep $w; head -1 /proc/stat; rx
EOS
  else
  $SI bash -s -- $STEADY_FROM $((DUR-STEADY_FROM-2)) > /tmp/quick.stat <<'EOS' &
sf=$1; w=$2
hits(){ sudo cat /sys/kernel/debug/fman_pcd/0/fe_ehash_stats | sed -n 's/.*record_dma=\(0x[0-9a-f]*\).*pkt_count=\([0-9]*\).*/E \1 \2/p'; }
sleep $sf; head -1 /proc/stat; hits; sleep $w; head -1 /proc/stat; hits
EOS
  fi
  sp=$!
  # SAMPLE=1 (ASK2 only): per-second DUT sampler -> $J/$tag.dut.txt, lines
  # "S <epoch> <dport-5201 conntrack entries> <HW_OFFLOAD> <software [OFFLOAD]> <cpu fields user..softirq>".
  # Entries with neither flag are not in the flowtable at all (kernel path).
  smp=""
  if [ "${SAMPLE:-0}" = 1 ] && [ "$DUT_KIND" != vendor ]; then
  $SI bash -s -- $DUR > "$J/$tag.dut.txt" <<'EOS' &
for s in $(seq 0 $1); do
  c=$(sudo cat /proc/net/nf_conntrack | awk '/dport=5201/{n++; if($0~/HW_OFFLOAD/)h++; else if($0~/OFFLOAD/)o++} END{printf "%d %d %d",n+0,h+0,o+0}')
  echo "S $(date +%s.%N) $c $(head -1 /proc/stat | cut -d' ' -f3-9)"
  sleep 1
done
EOS
  smp=$!
  fi
  $G "$NSX iperf3 -c $b -B $a -p 5201 -P $STREAMS -t $DUR -i 1 -Z $BIDIR_ARG -J" > "$J/$tag.json" &
  p1=$!; wait $p1 $sp $smp; sleep 1
  if ! read gbps gen rt bytes < <(parse "$J/$tag.json"); then
    echo "ERROR: invalid iperf3 result for $tag" >&2
    exit 1
  fi
  win=$((DUR-STEADY_FROM-2))
  read busy sirq < <(awk '!/^cpu /{next} {k++} k==1{for(i=2;i<=11;i++)a[i]=$i} k==2{for(i=2;i<=11;i++){d=$i-a[i];T+=d; if(i==5||i==6)idle+=d; if(i==8)s=d}; if(T>0) printf "%.1f %.1f\n",100*(T-idle)/T,100*s/T; else print "0 0"}' /tmp/quick.stat)
  # per-record delta: records aged out inside the window must not cancel growth elsewhere
  hw=$(awk '/^cpu /{sec++; next} /^E /{if(sec==1) a[$2]=$3; else b[$2]=$3} END{for(k in b){d=((k in a) && b[k]>=a[k]) ? b[k]-a[k] : b[k]; s+=d}; print s+0}' /tmp/quick.stat)
  est=$(awk -v g="$gbps" -v w="$win" 'BEGIN{printf "%d", g*1e9/8/1448*w}')
  if [ "$DUT_KIND" = vendor ]; then   # frames the kernel never saw were forwarded by the fast path
    krx=$(awk '/^cpu /{sec++; next} /^K /{if(sec==1) a[$2]=$3; else b[$2]=$3} END{for(k in b) s+=b[k]-a[k]; print s+0}' /tmp/quick.stat)
    hw=$(( est > krx ? est - krx : 0 ))
  fi
  ratio=$(awk -v h="$hw" -v e="$est" 'BEGIN{if(e>0) printf "%.2f", h/e; else print "0"}')
  verdict=$(awk -v r="$ratio" -v g="$gbps" 'BEGIN{if(g<0.1) print "FAIL(no traffic)"; else if(r>=0.9) print "HW"; else if(r>=0.1) print "PARTIAL"; else print "SW"}')
  printf '%-15s %7s %6s %6s %6s %8s %12s %6s  %s\n' "$tag" "$gbps" "$busy" "$sirq" "$gen" "$rt" "$hw" "$ratio" "$verdict"
  echo "$(date +%H:%M:%S),$tag,$gbps,$busy,$sirq,$gen,$rt,$hw,$est,$ratio,$verdict" >> "$OUT"
  sleep 8
done
echo "csv: $OUT"
