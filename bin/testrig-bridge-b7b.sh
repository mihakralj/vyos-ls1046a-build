#!/bin/bash
# Bridge B7b gate (plans/ASK2-BRIDGE-OFFLOAD-PLAN.md §6.1): access<->trunk VLAN pop/push on a
# VLAN-filtering bridge. eth3 = access port (VID 30 PVID untagged), eth4 = trunk (VID 30 tagged).
# Rows: push (untagged in on eth3, tagged VID 30 out on eth4) and pop (tagged in on eth4, untagged
# out on eth3) for IPv4 and IPv6 with hit counters and wire captures (tag present/absent, TTL and
# hop limit unchanged), ARP and PCP!=0 fallback, the vlan_offload kill switch, PVID churn and
# untagged-flag churn (record resync).
# Needs a debugfs (kunit) image with B7b, eth3/eth4 engaged at boot, a fresh boot. Builds the
# bridge with ip(8) outside the VyOS config: it deletes eth3.10/eth4.20 and the eth3/eth4
# addresses, so REBOOT the DUT afterwards.
# Settings: DUT (ssh target, default vyos@192.168.1.155), GEN1/GEN2 (dell1/dell2),
# NS (generator netns facing the DUT, default n106; empty = root namespace), IF (generator port).
set -u
FAILS=0
SSHO="-o BatchMode=yes -o ConnectTimeout=8"
DUTH=${DUT:-vyos@192.168.1.155}
DUT="ssh -n $SSHO -i $HOME/.ssh/vyos_key $DUTH"
D1="ssh -n $SSHO ${GEN1:-admin@192.168.1.112}"; D2="ssh -n $SSHO ${GEN2:-admin@192.168.1.113}"
NS=${NS-n106}; IF=${IF:-enp1s0f1np1}; N=20; VID=30
NX=""; [ -n "$NS" ] && NX="ip netns exec $NS"
P=/sys/module/ask/parameters; PCD=/sys/kernel/debug/fman_pcd/0; BR=br-b7

dut(){ $DUT "$1"; }
gmac(){ $1 "sudo $NX cat /sys/class/net/$IF/address"; }
M1=$(gmac "$D1"); M2=$(gmac "$D2")
recs(){ dut "sudo grep -c 'keysz=16 ' $PCD/fe_ehash_stats"; }
chk(){ sleep 2; r=$(recs); [ "$r" = "$2" ] && v=PASS || v=FAIL; printf '%-34s records %2s (want %s)  %s\n' "$1" "$r" "$2" "$v"
  [ $v = FAIL ] && { FAILS=$((FAILS+1)); dut "sudo grep 'keysz=16 ' $PCD/fe_ehash_stats | cut -c1-240; sudo bridge vlan show; sudo bridge fdb show br $BR | grep -v permanent" | sed 's/^/    /'; }; return 0; }
hits(){ dut "sudo awk '/keysz=16 /{for(i=1;i<=NF;i++)if(\$i~/^pkt_count=/){split(\$i,a,\"=\");s+=a[2]}}END{print s+0}' $PCD/fe_ehash_stats"; }
k_rx(){ dut "cat /sys/class/net/$1/statistics/rx_packets"; }
peer_rx(){ $2 "sudo $NX ethtool -S $IF | awk '/port.rx_$1:/{print \$2}'"; }
# send <host> <src mac> <dst mac> <kind> [count]; kind v4|v6|arp, t<vid>[p<pcp>][v6] = tagged v4 (v6)
send(){ $1 "sudo $NX python3 -c \"from scapy.all import *
import re
e=Ether(dst='$3',src='$2')
k='$4'
t=re.fullmatch(r't(\\d+)(?:p(\\d))?(v6)?',k)
if t: e=e/Dot1Q(vlan=int(t[1]),prio=int(t[2] or 0)); k='v6' if t[3] else 'v4'
f={'v4':e/IP(src='10.99.77.1',dst='10.99.77.2',ttl=64)/UDP(sport=47001,dport=47002)/Raw(bytes(958)),
 'v6':e/IPv6(src='fd77::1',dst='fd77::2',hlim=64)/UDP(sport=47001,dport=47002)/Raw(bytes(38)),
 'arp':e/ARP(hwsrc='$2',psrc='10.99.77.1',pdst='10.99.77.2')/Raw(bytes(18))}[k]
sendp(f,iface='$IF',count=${5:-$N},inter=0.01,verbose=0)\""; }
# warm push/pop flows (dedup retries after 1 s)
warm(){ for k in v4 v6; do s=""; [ $k = v6 ] && s=v6
    send "$D1" $M1 $M2 $k 2; send "$D2" $M2 $M1 t$VID$s 2; sleep 1.2
    send "$D1" $M1 $M2 $k 2; send "$D2" $M2 $M1 t$VID$s 2; done; sleep 1; }
# row <name> <host> <src> <dst> <kind> <ingress> <peer> <want hits>
row(){
  h0=$(hits); k0=$(k_rx $6); r0=$(peer_rx unicast "$7"); send "$2" $3 $4 $5; sleep 2
  dh=$(( $(hits)-h0 )); dk=$(( $(k_rx $6)-k0 )); dr=$(( $(peer_rx unicast "$7")-r0 ))
  if [ $8 -gt 0 ]; then [ $dh -eq $8 ] && [ $dr -ge $N ] && [ $dk -lt 5 ] && v=PASS || v=FAIL
  else [ $dh -eq 0 ] && [ $dk -ge $N ] && [ $dr -ge $N ] && v=PASS || v=FAIL; fi
  [ $v = FAIL ] && FAILS=$((FAILS+1))
  printf '%-22s %6d %6d %6d  %s\n' "$1" $dh $dk $dr $v
}
# wire <name> <tx host> <src> <dst> <kind> <rx host> <want: tag|untag> <want ttl/hlim line regex>
# captures 3 frames at the receiving generator (tcpdump -e shows the 802.1Q tag)
wire(){
  $6 "sudo $NX timeout 8 tcpdump -i $IF -e -nn -v -c 3 'ether src $3 and ether dst $4' 2>/dev/null" >/tmp/b7b-cap.$$ &
  cp=$!; sleep 2; send "$2" $3 $4 $5 5; wait $cp
  c=$(grep -c "$3" /tmp/b7b-cap.$$); tg=$(grep -c "vlan $VID" /tmp/b7b-cap.$$); tl=$(grep -cE "$8" /tmp/b7b-cap.$$)
  if [ "$7" = tag ]; then [ $c -ge 3 ] && [ $tg -ge 3 ] && [ $tl -ge 3 ] && v=PASS || v=FAIL
  else [ $c -ge 3 ] && [ $tg -eq 0 ] && [ $tl -ge 3 ] && v=PASS || v=FAIL; fi
  [ $v = FAIL ] && { FAILS=$((FAILS+1)); sed 's/^/    /' /tmp/b7b-cap.$$ | head -12; }
  printf '%-22s frames %d, vlan %d tagged, ttl/hlim 64 %d (want %s)  %s\n' "$1" $c $tg $tl "$7" $v
  rm -f /tmp/b7b-cap.$$
}
hdr(){ printf '%-22s %6s %6s %6s  %s\n' case hits k_rx peer verdict; }

[ "$(dut "sudo test -e $P/bridge_offload && sudo test -e $PCD/fe_ehash_stats && echo y")" = y ] || { echo "need a debugfs image with bridge_offload"; exit 1; }
echo "DUT $DUTH  gen1 $M1  gen2 $M2  ns ${NS:-root} $IF"
dut 'uname -r; grep -o "vyos-union=[^ ]*" /proc/cmdline; uptime'
dut "for p in bridge_offload vlan_offload; do echo \$p=\$(cat $P/\$p); done"
dut "sudo /usr/local/bin/pcd-snapshot capture -o /tmp/b7b-baseline.json >/dev/null; sudo grep used $PCD/muram_budget"
# the generators register VID 30 so their NIC VLAN filter passes tagged captures
for g in "$D1" "$D2"; do $g "sudo $NX ip link add link $IF name $IF.$VID type vlan id $VID 2>/dev/null; sudo $NX ip link set $IF.$VID up"; done

echo "== bridge: eth3 access VID $VID, eth4 trunk VID $VID"
dut "echo 1 | sudo tee $P/bridge_offload >/dev/null; echo 1 | sudo tee $P/vlan_offload >/dev/null
for v in eth3.10 eth4.20; do sudo ip link del \$v 2>/dev/null; done
sudo ip addr flush dev eth3; sudo ip addr flush dev eth4
sudo ip link add $BR type bridge vlan_filtering 1
sudo ip link set eth3 master $BR; sudo ip link set eth4 master $BR; sudo ip link set $BR up
sudo bridge vlan add dev eth3 vid $VID pvid untagged; sudo bridge vlan del dev eth3 vid 1
sudo bridge vlan add dev eth4 vid $VID
sleep 3; sudo bridge vlan show; sudo dmesg | tail -20 | grep -E 'role change|error|failed'"
echo "records before traffic: $(recs) (want 0)"
warm; chk "warm push+pop v4+v6" 4
dut "sudo grep 'keysz=16 ' $PCD/fe_ehash_stats | cut -c1-200" | sed 's/^/    /'

hdr
row push-v4      "$D1" $M1 $M2 v4         eth3 "$D2" $N
row push-v6      "$D1" $M1 $M2 v6         eth3 "$D2" $N
row pop-v4       "$D2" $M2 $M1 t$VID      eth4 "$D1" $N
row pop-v6       "$D2" $M2 $M1 t${VID}v6  eth4 "$D1" $N
row arp-access   "$D1" $M1 $M2 arp        eth3 "$D2" 0
row pop-pcp5     "$D2" $M2 $M1 t${VID}p5  eth4 "$D1" 0
wire wire-push-v4 "$D1" $M1 $M2 v4        "$D2" tag   'ttl 64'
wire wire-push-v6 "$D1" $M1 $M2 v6        "$D2" tag   'hlim 64'
wire wire-pop-v4  "$D2" $M2 $M1 t$VID     "$D1" untag 'ttl 64'
wire wire-pop-v6  "$D2" $M2 $M1 t${VID}v6 "$D1" untag 'hlim 64'
wire wire-arp     "$D1" $M1 $M2 arp       "$D2" tag   'ARP|arp'

echo "== kill switch: vlan_offload 0"
dut "echo 0 | sudo tee $P/vlan_offload >/dev/null"; sleep 2; warm
echo "records with vlan_offload 0: $(recs) (pop/push refused)"
hdr
row push-v4-killed "$D1" $M1 $M2 v4    eth3 "$D2" 0
row pop-v4-killed  "$D2" $M2 $M1 t$VID eth4 "$D1" 0
dut "echo 1 | sudo tee $P/vlan_offload >/dev/null"; sleep 2; warm
echo "records with vlan_offload 1 again: $(recs)"
hdr
row push-v4-rearmed "$D1" $M1 $M2 v4    eth3 "$D2" $N
row pop-v4-rearmed  "$D2" $M2 $M1 t$VID eth4 "$D1" $N

echo "== untagged-flag churn: eth4 VID $VID untagged (pop/push -> same)"
warm; chk "rewarm before churn" 4
dut "sudo bridge vlan add dev eth4 vid $VID untagged"; chk "eth4 VID $VID untagged" 0
warm; dut "sudo grep 'keysz=16 ' $PCD/fe_ehash_stats | cut -c1-200" | sed 's/^/    /'
hdr
row same-untagged-v4 "$D1" $M1 $M2 v4 eth3 "$D2" $N
wire wire-same-untag "$D1" $M1 $M2 v4 "$D2" untag 'ttl 64'
dut "sudo bridge vlan add dev eth4 vid $VID"; chk "eth4 VID $VID tagged again" 0
warm; chk "rewarm push+pop" 4

echo "== PVID churn: eth3 PVID $VID -> 40"
dut "sudo bridge vlan add dev eth3 vid 40 pvid untagged"; chk "eth3 PVID 40" 0
hdr
row push-v4-pvid40 "$D1" $M1 $M2 v4 eth3 "$D2" 0
dut "sudo bridge vlan add dev eth3 vid $VID pvid untagged; sudo bridge vlan del dev eth3 vid 40"
warm; chk "eth3 PVID $VID again + warm" 4
row push-v4-back   "$D1" $M1 $M2 v4 eth3 "$D2" $N

echo "== leave bridge"
for g in "$D1" "$D2"; do $g "sudo $NX ip link del $IF.$VID"; done
dut "sudo ip link set eth3 nomaster; sudo ip link set eth4 nomaster; sudo ip link del $BR
sleep 2; sudo dmesg | tail -20 | grep -E 'role change|error|failed'; sudo grep used $PCD/muram_budget"
dut "sudo /usr/local/bin/pcd-snapshot diff /tmp/b7b-baseline.json | tail -5; exit \${PIPESTATUS[0]}" || { echo "pcd-snapshot diff: FAIL"; FAILS=$((FAILS+1)); }
kerr=$(dut "sudo journalctl -k -b | grep -ciE 'possible circular|inconsistent lock|WARNING:|BUG:|DMA bus error|SYNC timed out'")
echo "kernel errors this boot: $kerr (want 0)"; [ "${kerr:-1}" = 0 ] || FAILS=$((FAILS+1))
left=$(recs); echo "records: $left (want 0)"; [ "$left" = 0 ] || FAILS=$((FAILS+1))
echo "Reboot the DUT now: eth3.10/eth4.20 and the eth3/eth4 addresses are gone until then."
echo "== result: $FAILS failure(s)"; [ "$FAILS" -eq 0 ] && echo PASS || echo FAIL
exit $(( FAILS > 0 ))
