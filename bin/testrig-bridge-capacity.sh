#!/bin/bash
# Bridge B2 row 7 (plans/ASK2-BRIDGE-OFFLOAD-PLAN.md §13.3): L2_DA table capacity and lookup at
# occupancy. Needs the debugfs (kunit=true) image on .185. Engages eth3 as L2_DA by hand, inserts
# N (default 8192) (DA 02:00:dd:xx:xx:xx, 0x0800) -> eth4 records, sends 20 IPv4 frames from dell1
# to the first, middle and last DA (each must hit 20 and reach dell2), deletes everything and
# restores ROUTED. Reports insert/delete time, MURAM and pcd-snapshot.
set -u
FAILS=0
N=${N:-8192}
SSHO="-o BatchMode=yes -o ConnectTimeout=8"
DUT="ssh -n $SSHO -i $HOME/.ssh/vyos_key vyos@192.168.1.185"
D1="ssh -n $SSHO admin@192.168.1.112"; D2="ssh -n $SSHO admin@192.168.1.113"
IF=enp1s0f0np0; SA=3c:fd:fe:2a:f0:20
OFF=/sys/kernel/debug/ask/offload; PCD=/sys/kernel/debug/fman_pcd/0

dut(){ $DUT "$1"; }
mac(){ printf '02:00:dd:%02x:%02x:%02x' $(( $1>>16 & 255 )) $(( $1>>8 & 255 )) $(( $1 & 255 )); }
recs16(){ dut "sudo grep -c 'keysz=16 ' $PCD/fe_ehash_stats"; }
hits1(){ dut "sudo awk '/keysz=16 /{for(i=1;i<=NF;i++)if(\$i~/^pkt_count=/){split(\$i,a,\"=\");s+=a[2]}}END{print s+0}' $PCD/fe_ehash_stats"; }
d2_rx(){ $D2 "sudo ethtool -S $IF | awk '/port.rx_unicast:/{print \$2}'"; }
send(){ $D1 "sudo python3 -c \"from scapy.all import *
sendp(Ether(dst='$1',src='$SA')/IP(src='10.99.1.112',dst='10.99.2.113')/UDP(sport=47001,dport=47002)/Raw(bytes(958)),iface='$IF',count=20,inter=0.01,verbose=0)\""; }
# op <add|del> <first> <count>: one remote loop of debugfs writes; prints failures and seconds
op(){ dut "sudo bash -c 's=\$(date +%s%N); f=0; for i in \$(seq $2 \$(( $2+$3-1 ))); do
  m=\$(printf 02:00:dd:%02x:%02x:%02x \$((i>>16&255)) \$((i>>8&255)) \$((i&255)))
  if [ $1 = add ]; then echo \"l2add eth3 \$m $SA 0 0x0800 eth4\"; else echo \"l2del eth3 \$m $SA 0 0x0800\"; fi > $OFF 2>/dev/null || f=\$((f+1)); done
  echo failed=\$f ms=\$(( (\$(date +%s%N)-s)/1000000 ))'"; }

[ "$(dut "sudo test -e $OFF && echo y")" = y ] || { echo "no $OFF: install the kunit/debug ISO first"; exit 1; }
dut 'uname -r; grep -o "vyos-union=[^ ]*" /proc/cmdline; uptime'
dut "sudo /usr/local/bin/pcd-snapshot capture -o /tmp/cap-baseline.json >/dev/null; sudo grep used $PCD/muram_budget"
dut "echo 'disengage 0x10' | sudo tee $OFF >/dev/null; sudo ip link set eth3 promisc on; echo 'l2engage eth3' | sudo tee $OFF >/dev/null"

mem(){ dut "awk '/MemAvailable/{print \$2}' /proc/meminfo"; }
m0=$(mem)
echo "== insert $N records"; op add 0 "$N"
m1=$(mem)
echo "records: $(dut "sudo grep -c 'keysz=16 ' $PCD/fe_ehash_stats")  $(dut "sudo grep used $PCD/muram_budget")  MemAvailable -$(( (m0-m1)/1024 )) MiB ($(( (m0-m1)*1024/N )) B/record)"
printf '%-22s %6s %6s  %s\n' DA hits dell2 verdict
for i in 0 $((N/2)) $((N-1)); do
  m=$(mac $i); h0=$(hits1); r0=$(d2_rx); send "$m"; sleep 2
  dh=$(( $(hits1)-h0 )); dr=$(( $(d2_rx)-r0 ))
  [ $dh -eq 20 ] && [ $dr -ge 20 ] && v=PASS || { v=FAIL; FAILS=$((FAILS+1)); }
  printf '%-22s %6d %6d  %s\n' "$m" $dh $dr $v
done

echo "== delete $N records"; op del 0 "$N"
left=$(recs16); echo "records: $left  MemAvailable back to $(( ($(mem)-m0)/1024 )) MiB vs start"
[ "$left" = 0 ] || { echo "records left after delete: FAIL"; FAILS=$((FAILS+1)); }
dut "echo 'disengage 0x10' | sudo tee $OFF >/dev/null; sudo ip link set eth3 promisc off; echo 'engage 0x10' | sudo tee $OFF >/dev/null; sudo grep used $PCD/muram_budget"
dut "sudo /usr/local/bin/pcd-snapshot diff /tmp/cap-baseline.json | tail -2; exit \${PIPESTATUS[0]}" || { echo "pcd-snapshot diff: FAIL"; FAILS=$((FAILS+1)); }
kerr=$(dut "sudo journalctl -k --since '-15 min' | grep -ciE 'possible circular|inconsistent lock|WARNING:|BUG:|DMA bus error|SYNC timed out'")
echo "kernel errors: $kerr"; [ "${kerr:-1}" = 0 ] || FAILS=$((FAILS+1))
echo "== result: $FAILS failure(s)"; [ "$FAILS" -eq 0 ] && echo PASS || echo FAIL
exit $(( FAILS > 0 ))
