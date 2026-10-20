#!/bin/bash
# Bridge B6/B7a gate (plans/ASK2-BRIDGE-OFFLOAD-PLAN.md §13.30, §13.32): learned-MAC and
# VLAN-aware bridge offload on .185. Records are per flow (ingress port, DA, SA, TCI, EtherType)
# and come from forwarded traffic, so every
# check warms the flows first. Rows: hits and misses, the FDB "offload"/"extern_learn" flags,
# invalidation (STP, isolation, VLAN filtering, a dynamic MAC move, family mask), ageing, a static
# entry over a learned one, then iperf3 with bridge_offload 1 vs 0 (THROUGHPUT=0 skips it).
# B7a rows (VLAN=0 skips them): tagged frames on a non-filtering bridge (and a PCP 5 record, a vif
# that takes the VLAN away from the bridge), then a VLAN-filtering bridge with access, trunk,
# filtered and access->trunk (before the destination is learned in that VLAN) pairs.
# Needs an ask.ko with B6, eth3/eth4 engaged at boot, a cold boot. Builds the bridge with ip(8)
# outside the VyOS config: it deletes eth3.10/eth4.20 and the eth3/eth4 addresses, so REBOOT the
# DUT afterwards. Static FDB entries mark their MAC as user-added until reboot (never taken over),
# so run this on a fresh boot.
# Settings: DUT (SSH target), GEN1/GEN2, NS (empty = root namespace), IF (generator port).
set -u
FAILS=0
SSHO="-o BatchMode=yes -o ConnectTimeout=8"
DUTH=${DUT:-vyos@192.168.1.185}
DUT="ssh -n $SSHO -i $HOME/.ssh/vyos_key $DUTH"
D1="ssh -n $SSHO ${GEN1:-admin@192.168.1.112}"; D2="ssh -n $SSHO ${GEN2:-admin@192.168.1.113}"
NS=${NS:-}; IF=${IF:-enp1s0f0np0}; UNK=02:00:00:aa:bb:02; N=20
NX=""; [ -n "$NS" ] && NX="ip netns exec $NS"
P=/sys/module/ask/parameters/bridge_offload; PCD=/sys/kernel/debug/fman_pcd/0; BR=br-b6
FAM="sudo /usr/local/bin/vyos-offload-ask --port 0x10 family"

dut(){ $DUT "$1"; }
M1=$($D1 "sudo $NX cat /sys/class/net/$IF/address")
M2=$($D2 "sudo $NX cat /sys/class/net/$IF/address")
recs(){ dut "sudo grep -c 'keysz=16 ' $PCD/fe_ehash_stats"; }
# chk <name> <want records>: settle, then compare the record count
chk(){ sleep 2; r=$(recs); [ "$r" = "$2" ] && v=PASS || v=FAIL; printf '%-28s records %2s (want %s)  %s\n' "$1" "$r" "$2" "$v"
  [ $v = FAIL ] && { FAILS=$((FAILS+1)); dut "sudo grep 'keysz=16 ' $PCD/fe_ehash_stats | cut -c1-240; sudo bridge vlan show; sudo bridge fdb show br $BR | grep -v permanent" | sed 's/^/    /'; }; return 0; }
hits(){ dut "sudo awk '/keysz=16 /{for(i=1;i<=NF;i++)if(\$i~/^pkt_count=/){split(\$i,a,\"=\");s+=a[2]}}END{print s+0}' $PCD/fe_ehash_stats"; }
k_rx(){ dut "cat /sys/class/net/$1/statistics/rx_packets"; }
peer_rx(){ $2 "sudo $NX ethtool -S $IF | awk '/port.rx_$1:/{print \$2}'"; }
cpu(){ dut "head -1 /proc/stat"; }
fdbflags(){ dut "sudo bridge fdb show br $BR | grep -i '^$1 ' | grep -v permanent | head -1"; }
# send <host> <src mac> <dst mac> <kind> [count]; kind t<vid>[p<pcp>] = tagged IPv4
send(){ $1 "sudo $NX python3 -c \"from scapy.all import *
import re
e=Ether(dst='$3',src='$2')
t=re.fullmatch(r't(\\d+)(?:p(\\d))?','$4')
if t: e=e/Dot1Q(vlan=int(t[1]),prio=int(t[2] or 0))
f={'v4':e/IP(src='10.99.77.1',dst='10.99.77.2')/UDP(sport=47001,dport=47002)/Raw(bytes(958)),
 'v6':e/IPv6(src='fd77::1',dst='fd77::2')/UDP(sport=47001,dport=47002)/Raw(bytes(38)),
 'arp':e/ARP(hwsrc='$2',psrc='10.99.77.1',pdst='10.99.77.2')/Raw(bytes(18))}['v4' if t else '$4']
sendp(f,iface='$IF',count=${5:-$N},inter=0.01,verbose=0)\""; }
# warm [kinds]: learn both MACs, then let the hook see each direction (dedup retries after 1 s)
warm(){ for k in ${1:-v4 v6}; do
    send "$D1" $M1 $M2 $k 2; send "$D2" $M2 $M1 $k 2; sleep 1.2; send "$D1" $M1 $M2 $k 2; done; sleep 1; }
# row <name> <host> <src> <dst> <kind> <ingress> <peer> <peer counter> <want hits>
row(){
  h0=$(hits); k0=$(k_rx $6); r0=$(peer_rx $8 "$7"); send "$2" $3 $4 $5; sleep 2
  dh=$(( $(hits)-h0 )); dk=$(( $(k_rx $6)-k0 )); dr=$(( $(peer_rx $8 "$7")-r0 ))
  if [ $9 -gt 0 ]; then [ $dh -eq $9 ] && [ $dr -ge $N ] && [ $dk -lt 5 ] && v=PASS || v=FAIL
  else [ $dh -eq 0 ] && [ $dk -ge $N ] && v=PASS || v=FAIL; fi
  [ $v = FAIL ] && FAILS=$((FAILS+1))
  printf '%-14s %6d %6d %6d  %s\n' "$1" $dh $dk $dr $v
}

[ "$(dut "test -e $P && echo y")" = y ] || { echo "no $P: install a B6 image first"; exit 1; }
dut 'uname -r; grep -o "vyos-union=[^ ]*" /proc/cmdline; uptime'
dut "sudo /usr/local/bin/pcd-snapshot capture -o /tmp/b6-baseline.json >/dev/null; sudo grep used $PCD/muram_budget"

echo "== bridge eth3+eth4"
dut "echo 1 | sudo tee $P >/dev/null
for v in eth3.10 eth4.20; do sudo ip link del \$v 2>/dev/null; done
sudo ip addr flush dev eth3; sudo ip addr flush dev eth4
sudo ip link add $BR type bridge vlan_filtering 0
sudo ip link set eth3 master $BR; sudo ip link set eth4 master $BR; sudo ip link set $BR up
sleep 3; sudo dmesg | tail -20 | grep -E 'role change|error|failed'"
echo "records before traffic: $(recs) (want 0)"
warm; chk "warm v4+v6 both ways" 4
echo "M1: $(fdbflags $M1)"; echo "M2: $(fdbflags $M2)"
f=$(fdbflags $M1); case "$f" in *extern_learn*offload*|*offload*extern_learn*) v=PASS;; *) v=FAIL; FAILS=$((FAILS+1));; esac
echo "M1 taken over (extern_learn + offload): $v"

printf '%-14s %6s %6s %6s  %s\n' case hits k_rx peer verdict
row v4-hit     "$D1" $M1 $M2  v4  eth3 "$D2" unicast   $N
row v6-hit     "$D1" $M1 $M2  v6  eth3 "$D2" unicast   $N
row v4-reverse "$D2" $M2 $M1  v4  eth4 "$D1" unicast   $N
row arp-to-M2  "$D1" $M1 $M2  arp eth3 "$D2" unicast   0
row unknown-DA "$D1" $M1 $UNK v4  eth3 "$D2" unicast   0
row broadcast  "$D1" $M1 ff:ff:ff:ff:ff:ff arp eth3 "$D2" broadcast 0

echo "== invalidation"
# state 0: with STP off, br_port_state_selection() forces any other state back to forwarding
dut "sudo bridge link set dev eth4 state 0"; chk "eth4 STP disabled" 0
dut "sudo bridge link set dev eth4 state 3"; warm; chk "eth4 forwarding + warm" 4
dut "sudo bridge link set dev eth3 isolated on"; chk "eth3 isolated" 0
dut "sudo bridge link set dev eth3 isolated off"; warm; chk "eth3 not isolated + warm" 4
dut "sudo ip link set $BR type bridge vlan_filtering 1"; chk "vlan_filtering 1" 0
dut "sudo ip link set $BR type bridge vlan_filtering 0"; warm; chk "vlan_filtering 0 + warm" 4
# a dynamic move: M2 shows up on eth3 (spoofed ARP from dell1), the bridge takes the entry back
send "$D1" $M2 $M1 arp 1; chk "M2 moved to eth3" 0
echo "M2 now: $(fdbflags $M2)"
row v4-after-move "$D1" $M1 $M2 v4 eth3 "$D2" unicast 0
warm; chk "M2 back on eth4 + warm" 4
row v4-moved-back "$D1" $M1 $M2 v4 eth3 "$D2" unicast $N

# §13.27: records follow the ingress port's family mask (the CLI's offload ipv4 path)
dut "$FAM 1 >/dev/null"; chk "eth3 offload ipv4 only" 3
row v4-eth3-v4only "$D1" $M1 $M2 v4 eth3 "$D2" unicast $N
row v6-eth3-v4only "$D1" $M1 $M2 v6 eth3 "$D2" unicast 0
row v6-reverse     "$D2" $M2 $M1 v6 eth4 "$D1" unicast $N
dut "$FAM 3 >/dev/null"; warm v6; chk "eth3 offload ipv4 ipv6 + warm" 4

echo "== ageing (ageing_time 10 s, no traffic)"
dut "sudo ip link set $BR type bridge ageing_time 1000"; sleep 25; chk "idle 25 s" 0
dut "sudo ip link set $BR type bridge ageing_time 30000"; warm; chk "traffic again" 4

if [ "${VLAN:-1}" = 1 ]; then
  echo "== B7a: VLAN tags on a non-filtering bridge"
  warm t10; chk "tagged VID 10 + warm" 6
  row t10-hit      "$D1" $M1 $M2 t10   eth3 "$D2" unicast $N
  row t10-reverse  "$D2" $M2 $M1 t10   eth4 "$D1" unicast $N
  send "$D1" $M1 $M2 t10p5 2; sleep 1.2; send "$D1" $M1 $M2 t10p5 2; chk "PCP 5 is its own record" 7
  row t10p5-hit    "$D1" $M1 $M2 t10p5 eth3 "$D2" unicast $N
  # tagged VID 10 frames on eth3 now go to the vif, never to the bridge
  dut "sudo ip link add link eth3 name eth3.10 type vlan id 10"; chk "vif eth3.10 added" 5
  row t10-to-vif   "$D1" $M1 $M2 t10   eth3 "$D2" unicast 0
  dut "sudo ip link del eth3.10"; warm t10; chk "vif removed + warm" 6

  echo "== B7a: VLAN-filtering bridge (default PVID 1 untagged on both ports)"
  dut "sudo ip link set $BR type bridge vlan_filtering 1"; chk "vlan_filtering 1" 0
  warm; chk "access VID 1 + warm" 4
  row v4-access    "$D1" $M1 $M2 v4    eth3 "$D2" unicast $N
  dut "sudo bridge vlan add dev eth3 vid 20; sudo bridge vlan add dev eth4 vid 20"
  warm t20; chk "trunk VID 20 + warm" 6
  row t20-trunk    "$D1" $M1 $M2 t20   eth3 "$D2" unicast $N
  row t10-filtered "$D1" $M1 $M2 t10   eth3 "$D2" unicast 0
  # VID 30 has no learned destination yet; pop/push admission is tested by the B7b harness.
  # The two eth3->eth4 untagged records die; that was the last record of SA M1 (eth3, VID 1), so
  # its taken-over FDB entry is handed back by deleting it (ask_bridge_sa_release), which takes
  # the eth4->eth3 untagged records (DA M1, VID 1) and then M2's entry with it. M1's untagged
  # frames are VLAN 30 now, so a VID 1 binding for it is stale anyway. Only the VID 20 pair stays.
  dut "sudo bridge vlan add dev eth3 vid 30 pvid untagged; sudo bridge vlan add dev eth4 vid 30"
  chk "eth3 PVID 30" 2
  row v4-access-to-trunk "$D1" $M1 $M2 v4 eth3 "$D2" unicast 0
  chk "unlearned VID 30 stays software" 2
  row v4-rev-after-pvid "$D2" $M2 $M1 v4  eth4 "$D1" unicast 0
  row t20-after-pvid    "$D1" $M1 $M2 t20 eth3 "$D2" unicast $N
  dut "sudo ip link set $BR type bridge vlan_filtering 0"; warm; chk "vlan_filtering 0 + warm" 4
fi

if [ "${THROUGHPUT:-1}" = 1 ]; then
  echo "== throughput dell1 -> dell2 across $BR (iperf3 -P 4, 15 s)"
  $D1 "sudo $NX ip addr add 10.99.77.112/24 dev $IF"; $D2 "sudo $NX ip addr add 10.99.77.113/24 dev $IF"
  for off in 1 0; do
    dut "echo $off | sudo tee $P >/dev/null"; sleep 3
    h0=$(hits); c0=$(cpu)
    bps=$($D1 "sudo $NX iperf3 -c 10.99.77.113 -B 10.99.77.112 -P 4 -t 15 -O 3 -Z -J" | python3 -c 'import json,sys;print(round(json.load(sys.stdin)["end"]["sum_received"]["bits_per_second"]/1e9,2))')
    c1=$(cpu); dh=$(( $(hits)-h0 ))
    busy=$(printf '%s\n%s\n' "$c0" "$c1" | awk '{for(i=2;i<=11;i++)a[NR,i]=$i} END{for(i=2;i<=11;i++){d=a[2,i]-a[1,i];T+=d;if(i==5||i==6)id+=d}; printf "%.1f",100*(T-id)/T}')
    printf 'bridge_offload=%s  %6s Gbit/s  DUT busy %5s%%  hw hits %d\n' $off "$bps" "$busy" $dh
  done
  $D1 "sudo $NX ip addr del 10.99.77.112/24 dev $IF"; $D2 "sudo $NX ip addr del 10.99.77.113/24 dev $IF"
  dut "echo 1 | sudo tee $P >/dev/null"; warm; chk "bridge_offload back on + warm" 4
fi

echo "== static entry over a learned one"
dut "sudo bridge fdb replace $M2 dev eth4 master static"; chk "M2 static on eth4" 4
row v4-static-M2 "$D1" $M1 $M2 v4 eth3 "$D2" unicast $N
dut "sudo bridge fdb del $M2 dev eth4 master static"; chk "M2 static deleted" 0
row v4-after-del "$D1" $M1 $M2 v4 eth3 "$D2" unicast 0

echo "== leave bridge"
dut "sudo ip link set eth3 nomaster; sudo ip link set eth4 nomaster; sudo ip link del $BR
sleep 2; sudo dmesg | tail -20 | grep -E 'role change|error|failed'; sudo grep used $PCD/muram_budget"
dut "sudo /usr/local/bin/pcd-snapshot diff /tmp/b6-baseline.json | tail -5; exit \${PIPESTATUS[0]}" || { echo "pcd-snapshot diff: FAIL"; FAILS=$((FAILS+1)); }
kerr=$(dut "sudo journalctl -k -b | grep -ciE 'possible circular|inconsistent lock|WARNING:|BUG:|DMA bus error|SYNC timed out'")
echo "kernel errors this boot: $kerr (want 0)"; [ "${kerr:-1}" = 0 ] || FAILS=$((FAILS+1))
left=$(recs); echo "records: $left (want 0)"; [ "$left" = 0 ] || FAILS=$((FAILS+1))
echo "Reboot the DUT now: eth3.10/eth4.20 and the eth3/eth4 addresses are gone until then."
echo "== result: $FAILS failure(s)"; [ "$FAILS" -eq 0 ] && echo PASS || echo FAIL
exit $(( FAILS > 0 ))
