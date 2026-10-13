#!/bin/bash
# Bridge B3a gate (plans/ASK2-BRIDGE-OFFLOAD-PLAN.md §13.7): a two-port bridge eth3+eth4 on .185
# forwards static-FDB unicast in hardware; ARP, broadcast and unknown unicast stay in software;
# deleting an entry or leaving the bridge removes the records and restores the routed role.
# B3b rows: STP block, isolation, VLAN filtering, MAC move and carrier loss must remove the
# affected records and bring them back. Then a 15 s iperf3 dell1->dell2 across the bridge
# with bridge_offload 1 vs 0 (THROUGHPUT=0 skips it).
# Needs an ask.ko with the bridge_offload param, eth3/eth4 engaged at boot, a cold boot.
# Builds the bridge with ip(8), outside the VyOS config: it deletes eth3.10/eth4.20 and the
# eth3/eth4 addresses, so REBOOT the DUT afterwards to get the routed config back.
set -u
SSHO="-o BatchMode=yes -o ConnectTimeout=8"
DUT="ssh -n $SSHO -i $HOME/.ssh/vyos_key vyos@192.168.1.185"
D1="ssh -n $SSHO admin@192.168.1.112"; D2="ssh -n $SSHO admin@192.168.1.113"
IF=enp1s0f0np0; M1=3c:fd:fe:2a:f0:20; M2=3c:fd:fe:2a:ec:80; UNK=02:00:00:aa:bb:02; N=20
P=/sys/module/ask/parameters/bridge_offload; PCD=/sys/kernel/debug/fman_pcd/0; BR=br-b3a

dut(){ $DUT "$1"; }
recs(){ dut "sudo grep -c 'keysz=8 ' $PCD/fe_ehash_stats"; }
# chk <name> <want records>: settle, then compare the record count
chk(){ sleep 2; r=$(recs); [ "$r" = "$2" ] && v=PASS || v=FAIL; printf '%-26s records %2s (want %s)  %s\n' "$1" "$r" "$2" "$v"; }
cpu(){ dut "head -1 /proc/stat"; }
hits(){ dut "sudo awk '/keysz=8 /{for(i=1;i<=NF;i++)if(\$i~/^pkt_count=/){split(\$i,a,\"=\");s+=a[2]}}END{print s+0}' $PCD/fe_ehash_stats"; }
k_rx(){ dut "cat /sys/class/net/$1/statistics/rx_packets"; }
peer_rx(){ $2 "sudo ethtool -S $IF | awk '/port.rx_$1:/{print \$2}'"; }
# send <host> <src mac> <dst mac> <kind>
send(){ $1 "sudo python3 -c \"from scapy.all import *
e=Ether(dst='$3',src='$2')
f={'v4':e/IP(src='10.99.77.1',dst='10.99.77.2')/UDP(sport=47001,dport=47002)/Raw(bytes(958)),
 'v6':e/IPv6(src='fd77::1',dst='fd77::2')/UDP(sport=47001,dport=47002)/Raw(bytes(38)),
 'arp':e/ARP(hwsrc='$2',psrc='10.99.77.1',pdst='10.99.77.2')/Raw(bytes(18))}['$4']
sendp(f,iface='$IF',count=$N,inter=0.01,verbose=0)\""; }
# row <name> <host> <src> <dst> <kind> <ingress> <peer> <peer counter> <want hits>
row(){
  h0=$(hits); k0=$(k_rx $6); r0=$(peer_rx $8 "$7"); send "$2" $3 $4 $5; sleep 2
  dh=$(( $(hits)-h0 )); dk=$(( $(k_rx $6)-k0 )); dr=$(( $(peer_rx $8 "$7")-r0 ))
  if [ $9 -gt 0 ]; then [ $dh -eq $9 ] && [ $dr -ge $N ] && [ $dk -lt 5 ] && v=PASS || v=FAIL
  else [ $dh -eq 0 ] && [ $dk -ge $N ] && v=PASS || v=FAIL; fi
  printf '%-14s %6d %6d %6d  %s\n' "$1" $dh $dk $dr $v
}

[ "$(dut "test -e $P && echo y")" = y ] || { echo "no $P: install the B3a image first"; exit 1; }
dut 'uname -r; grep -o "vyos-union=[^ ]*" /proc/cmdline; uptime'
dut "sudo /usr/local/bin/pcd-snapshot capture -o /tmp/b3a-baseline.json >/dev/null; sudo grep used $PCD/muram_budget"

echo "== bridge eth3+eth4"
dut "echo 1 | sudo tee $P >/dev/null
for v in eth3.10 eth4.20; do sudo ip link del \$v 2>/dev/null; done
sudo ip addr flush dev eth3; sudo ip addr flush dev eth4
sudo ip link add $BR type bridge vlan_filtering 0
sudo ip link set eth3 master $BR; sudo ip link set eth4 master $BR; sudo ip link set $BR up
sleep 3
sudo bridge fdb add $M2 dev eth4 master static; sudo bridge fdb add $M1 dev eth3 master static
sleep 2; sudo dmesg | tail -40 | grep -E 'role change|bridge|L2_DA|profile 1|error|failed'"
echo "records: $(recs) (want 4: 2 MACs x IPv4/IPv6 x 1 other port)"

printf '%-14s %6s %6s %6s  %s\n' case hits k_rx peer verdict
row v4-hit     "$D1" $M1 $M2  v4  eth3 "$D2" unicast   $N
row v6-hit     "$D1" $M1 $M2  v6  eth3 "$D2" unicast   $N
row v4-reverse "$D2" $M2 $M1  v4  eth4 "$D1" unicast   $N
row arp-to-M2  "$D1" $M1 $M2  arp eth3 "$D2" unicast   0
row unknown-DA "$D1" $M1 $UNK v4  eth3 "$D2" unicast   0
row broadcast  "$D1" $M1 ff:ff:ff:ff:ff:ff arp eth3 "$D2" broadcast 0

echo "== B3b invalidation"
# state 0: with STP off, br_port_state_selection() forces any other state back to forwarding
dut "sudo bridge link set dev eth4 state 0"; chk "eth4 STP disabled" 0
row v4-blocked "$D1" $M1 $M2 v4 eth3 "$D2" unicast 0
dut "sudo bridge link set dev eth4 state 3"; chk "eth4 forwarding again" 4
dut "sudo bridge link set dev eth3 isolated on"; chk "eth3 isolated" 0
dut "sudo bridge link set dev eth3 isolated off"; chk "eth3 not isolated" 4
dut "sudo ip link set $BR type bridge vlan_filtering 1"; chk "vlan_filtering 1" 0
dut "sudo ip link set $BR type bridge vlan_filtering 0"; chk "vlan_filtering 0" 4
dut "sudo bridge fdb replace $M2 dev eth3 master static"; chk "move M2 to eth3" 4
row v4-after-move "$D1" $M1 $M2 v4 eth3 "$D2" unicast 0
dut "sudo bridge fdb replace $M2 dev eth4 master static"; chk "move M2 back to eth4" 4
row v4-moved-back "$D1" $M1 $M2 v4 eth3 "$D2" unicast $N
# the X710 keeps the PHY up on ifdown unless link-down-on-close is set
$D2 "sudo ethtool --set-priv-flags $IF link-down-on-close on; sudo ip link set $IF down"; sleep 2
echo "eth4 carrier: $(dut 'cat /sys/class/net/eth4/carrier')"; chk "dell2 link down (eth4)" 0
$D2 "sudo ip link set $IF up; sudo ethtool --set-priv-flags $IF link-down-on-close off"
for i in $(seq 1 30); do [ "$(dut 'cat /sys/class/net/eth4/carrier')" = 1 ] && break; sleep 1; done
chk "dell2 link up (eth4)" 4

if [ "${THROUGHPUT:-1}" = 1 ]; then
  echo "== throughput dell1 -> dell2 across $BR (iperf3 -P 4, 15 s)"
  $D1 "sudo ip addr add 10.99.77.112/24 dev $IF"; $D2 "sudo ip addr add 10.99.77.113/24 dev $IF"
  for off in 1 0; do
    dut "echo $off | sudo tee $P >/dev/null"; sleep 3
    h0=$(hits); c0=$(cpu)
    bps=$($D1 "iperf3 -c 10.99.77.113 -B 10.99.77.112 -P 4 -t 15 -O 3 -Z -J" | python3 -c 'import json,sys;print(round(json.load(sys.stdin)["end"]["sum_received"]["bits_per_second"]/1e9,2))')
    c1=$(cpu); dh=$(( $(hits)-h0 ))
    busy=$(printf '%s\n%s\n' "$c0" "$c1" | awk '{for(i=2;i<=11;i++)a[NR,i]=$i} END{for(i=2;i<=11;i++){d=a[2,i]-a[1,i];T+=d;if(i==5||i==6)id+=d}; printf "%.1f",100*(T-id)/T}')
    printf 'bridge_offload=%s  %6s Gbit/s  DUT busy %5s%%  hw hits %d\n' $off "$bps" "$busy" $dh
  done
  $D1 "sudo ip addr del 10.99.77.112/24 dev $IF"; $D2 "sudo ip addr del 10.99.77.113/24 dev $IF"
  dut "echo 1 | sudo tee $P >/dev/null"; chk "bridge_offload back on" 4
fi

echo "== delete $M2"; dut "sudo bridge fdb del $M2 dev eth4 master static"; sleep 2
echo "records: $(recs) (want 2)"
row v4-after-del "$D1" $M1 $M2 v4 eth3 "$D2" unicast 0

echo "== leave bridge"
dut "sudo ip link set eth3 nomaster; sudo ip link set eth4 nomaster; sudo ip link del $BR
echo 0 | sudo tee $P >/dev/null; sleep 2
sudo dmesg | tail -20 | grep -E 'role change|error|failed'; sudo grep used $PCD/muram_budget
sudo /usr/local/bin/pcd-snapshot diff /tmp/b3a-baseline.json | tail -5"
echo "records: $(recs) (want 0)"
echo "Reboot the DUT now: eth3.10/eth4.20 and the eth3/eth4 addresses are gone until then."
