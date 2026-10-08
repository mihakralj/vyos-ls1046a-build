#!/usr/bin/env bash
# Quick per-offload throughput check: one DUR-second run per cell (default 30 s),
# iperf3 -Z unidir, steady value = mean of the receiver intervals from STEADY_FROM s.
# Cells: unicast, NAT, VLAN-VLAN, PPPoE (down = decap, up = encap), IPv4 and IPv6.
# Each cell also records an offload proof so a software-forwarded run cannot pass:
# the per-record pkt_count growth in fe_ehash_stats over the steady window, compared
# with the data frames iperf3 reports for the same window.
#
# Rig: dell1 (enp1s0, enp1s0.10) <-> DUT eth3, dell2 (enp2s0, enp2s0.20) <-> DUT eth4.
# NAT cells use runtime nft tables (table ask2q) masquerading out of eth4; the VyOS
# NAT66 rule is removed for the run and restored on exit. Nothing persistent changes.
#
# Usage: bin/testrig-offload-quick.sh [cell ...]       (default: all cells)
# Env:   DUT DUR STEADY_FROM STREAMS OUT
set -u
DUT=${DUT:-vyos@192.168.1.185}
DUR=${DUR:-30}; STEADY_FROM=${STEADY_FROM:-10}; STREAMS=${STREAMS:-8}
OUT=${OUT:-/mnt/builds/ask2-review/oracle/quick-$(date +%Y%m%d-%H%M).csv}
J=${J:-/mnt/builds/ask2-review/oracle/json-quick}; mkdir -p "$J"
SSHO="-o BatchMode=yes -o ConnectTimeout=8"
S="ssh -n $SSHO -i $HOME/.ssh/vyos_key $DUT"
SI="ssh $SSHO -i $HOME/.ssh/vyos_key $DUT"
D1="ssh -n $SSHO admin@192.168.1.112"; D2="ssh -n $SSHO admin@192.168.1.113"

# name|nat|client|bind(source) address|server address
CELLS_ALL="
unicast-v4|none|d1|10.99.1.112|10.99.2.113
unicast-v6|none|d1|fd99:1::112|fd99:2::113
nat-v4|4|d1|10.99.1.112|10.99.2.113
nat-v6|6|d1|fd99:1::112|fd99:2::113
vlan-v4|none|d1|10.99.10.112|10.99.20.113
vlan-v6|none|d1|fd99:10::112|fd99:20::113
pppoe-down-v4|none|d1|10.99.50.1|10.99.2.113
pppoe-up-v4|none|d2|10.99.2.113|10.99.50.1
pppoe-down-v6|none|d1|fd99:50::1|fd99:2::113
pppoe-up-v6|none|d2|fd99:2::113|fd99:50::1
"
WANT="$*"

NAT66_RULE='oifname "eth4" ip6 saddr fd99:1::/64 counter masquerade comment "SRC-NAT66-100"'
vyos_nat66_present=0
$S 'sudo nft list chain ip6 vyos_nat POSTROUTING 2>/dev/null' | grep -q SRC-NAT66-100 && vyos_nat66_present=1

nat_clear(){ $S 'sudo nft delete table ip ask2q 2>/dev/null; sudo nft delete table ip6 ask2q 2>/dev/null; true'; }
nat_set(){
  nat_clear
  case $1 in
    4) $S 'sudo nft add table ip ask2q && sudo nft "add chain ip ask2q post { type nat hook postrouting priority srcnat ; }" && sudo nft add rule ip ask2q post oifname eth4 ip saddr 10.99.1.0/24 masquerade' ;;
    6) $S 'sudo nft add table ip6 ask2q && sudo nft "add chain ip6 ask2q post { type nat hook postrouting priority srcnat ; }" && sudo nft add rule ip6 ask2q post oifname eth4 ip6 saddr fd99:1::/64 masquerade' ;;
  esac
}
cleanup(){
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
try: j=json.load(open(sys.argv[1]))
except Exception: print("0 0 0 0"); sys.exit()
f=float(sys.argv[2])
iv=[i['sum'] for i in j.get('intervals',[]) if i['sum']['start']>=f-0.01 and not i['sum'].get('omitted')]
g=sum(x['bits_per_second'] for x in iv)/len(iv)/1e9 if iv else 0
span=(iv[-1]['end']-iv[0]['start']) if iv else 0
bytes_=sum(x['bytes'] for x in iv)
e=j.get('end',{}); c=e.get('cpu_utilization_percent',{})
r=e.get('sum_sent',{}).get('retransmits')
if r is None: r=sum(i['sum'].get('retransmits',0) for i in j.get('intervals',[]))
print(f"{g:.3f} {c.get('host_total',0):.0f} {r} {bytes_}")
PY
}

echo "ts,cell,gbps,dut_busy,dut_softirq,gen_cpu,retrans,hw_pkts,data_pkts_est,hw_ratio,verdict" > "$OUT"
printf '%-15s %7s %6s %6s %6s %8s %12s %6s  %s\n' cell Gbit/s busy% sirq% gen% retrans hw_pkts ratio verdict

for line in $CELLS_ALL; do
  IFS='|' read -r name nat cli a b <<<"$line"
  if [ -n "$WANT" ] && ! [[ " $WANT " == *" $name "* ]]; then continue; fi
  [ $cli = d1 ] && G="$D1" || G="$D2"
  if ! $G "ping -c2 -W2 -i 0.3 -I $a $b >/dev/null 2>&1"; then
    printf '%-15s %s\n' "$name" "SKIP (no path $a -> $b)"; echo "$(date +%H:%M:%S),$name,0,0,0,0,0,0,0,0,SKIP" >> "$OUT"; continue
  fi
  nat_set $nat >/dev/null 2>&1
  tag="$name"
  $SI bash -s -- $STEADY_FROM $((DUR-STEADY_FROM-2)) > /tmp/quick.stat <<'EOS' &
sf=$1; w=$2
hits(){ sudo cat /sys/kernel/debug/fman_pcd/0/fe_ehash_stats | sed -n 's/.*record_dma=\(0x[0-9a-f]*\).*pkt_count=\([0-9]*\).*/E \1 \2/p'; }
sleep $sf; head -1 /proc/stat; hits; sleep $w; head -1 /proc/stat; hits
EOS
  sp=$!
  $G "iperf3 -c $b -B $a -p 5201 -P $STREAMS -t $DUR -i 1 -Z -J" > "$J/$tag.json" &
  p1=$!; wait $p1 $sp; sleep 1
  read gbps gen rt bytes < <(parse "$J/$tag.json")
  win=$((DUR-STEADY_FROM-2))
  read busy sirq < <(awk '!/^cpu /{next} {k++} k==1{for(i=2;i<=11;i++)a[i]=$i} k==2{for(i=2;i<=11;i++){d=$i-a[i];T+=d; if(i==5||i==6)idle+=d; if(i==8)s=d}; if(T>0) printf "%.1f %.1f\n",100*(T-idle)/T,100*s/T; else print "0 0"}' /tmp/quick.stat)
  # per-record delta: records aged out inside the window must not cancel growth elsewhere
  hw=$(awk '/^cpu /{sec++; next} /^E /{if(sec==1) a[$2]=$3; else b[$2]=$3} END{for(k in b){d=((k in a) && b[k]>=a[k]) ? b[k]-a[k] : b[k]; s+=d}; print s+0}' /tmp/quick.stat)
  est=$(awk -v g="$gbps" -v w="$win" 'BEGIN{printf "%d", g*1e9/8/1448*w}')
  ratio=$(awk -v h="$hw" -v e="$est" 'BEGIN{if(e>0) printf "%.2f", h/e; else print "0"}')
  verdict=$(awk -v r="$ratio" -v g="$gbps" 'BEGIN{if(g<0.1) print "FAIL(no traffic)"; else if(r>=0.9) print "HW"; else if(r>=0.1) print "PARTIAL"; else print "SW"}')
  printf '%-15s %7s %6s %6s %6s %8s %12s %6s  %s\n' "$name" "$gbps" "$busy" "$sirq" "$gen" "$rt" "$hw" "$ratio" "$verdict"
  echo "$(date +%H:%M:%S),$name,$gbps,$busy,$sirq,$gen,$rt,$hw,$est,$ratio,$verdict" >> "$OUT"
  sleep 8
done
echo "csv: $OUT"
