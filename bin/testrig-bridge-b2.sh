#!/bin/bash
# Bridge B1-close / B2 short-frame check (plans/ASK2-BRIDGE-OFFLOAD-PLAN.md §13.20).
# Needs a CONFIG_NET_ASK_DEBUG_FS (kunit=true) image on .185, freshly COLD-booted.
# Arms eth3 (0x10) with the L2_DA profile and one (DA, 0x0800) record -> eth4, then sends
# 20 frames per case from dell1. Non-IPv4 cases must MISS (pkt_count +0, kernel rx +20);
# the IPv4 case must HIT (pkt_count +20, dell2 port.rx_unicast +20). Restores ROUTED at the end.
# CASES="..." overrides the case list; tag_* cases (802.1Q VID 10) are reported as info only.
set -u
SSHO="-o BatchMode=yes -o ConnectTimeout=8"
DUT="ssh -n $SSHO -i $HOME/.ssh/vyos_key vyos@192.168.1.185"
D1="ssh -n $SSHO admin@192.168.1.112"; D2="ssh -n $SSHO admin@192.168.1.113"
IF=enp1s0f0np0; DA=02:00:00:aa:bb:01; SA=3c:fd:fe:2a:f0:20; N=20
OFF=/sys/kernel/debug/ask/offload; PCD=/sys/kernel/debug/fman_pcd/0

dut(){ $DUT "$1"; }
ctl(){ dut "echo '$1' | sudo tee $OFF >/dev/null" || echo "!! '$1' failed"; }
hits(){ dut "sudo awk '/keysz=8 /{for(i=1;i<=NF;i++)if(\$i~/^pkt_count=/){split(\$i,a,\"=\");s+=a[2]}}END{print s+0}' $PCD/fe_ehash_stats"; }
k_rx(){ dut "cat /sys/class/net/eth3/statistics/rx_packets"; }
d2_rx(){ $D2 "sudo ethtool -S $IF | awk '/port.rx_unicast:/{print \$2}'"; }
send(){ $D1 "sudo python3 -c \"from scapy.all import *
e=Ether(dst='$DA',src='$SA')
f={'arp60':e/ARP(hwsrc='$SA',psrc='10.99.1.112',pdst='10.99.1.185')/Raw(bytes(18)),
 'e60':Ether(dst='$DA',src='$SA',type=0x88b5)/Raw(bytes(46)),
 'e128':Ether(dst='$DA',src='$SA',type=0x88b5)/Raw(bytes(114)),
 'v4udp1000':e/IP(src='10.99.1.112',dst='10.99.2.113')/UDP(sport=47001,dport=47002)/Raw(bytes(958)),
 'tag_v4_1000':e/Dot1Q(vlan=10)/IP(src='10.99.1.112',dst='10.99.2.113')/UDP(sport=47001,dport=47002)/Raw(bytes(954)),
 'tag_v4_64':e/Dot1Q(vlan=10)/IP(src='10.99.1.112',dst='10.99.2.113')/UDP(sport=47001,dport=47002)/Raw(bytes(18)),
 'tag_arp64':e/Dot1Q(vlan=10)/ARP(hwsrc='$SA',psrc='10.99.1.112',pdst='10.99.1.185')/Raw(bytes(18))}['$1']
sendp(f,iface='$IF',count=$N,inter=0.01,verbose=0)\""; }

[ "$(dut "sudo test -e $OFF && echo y")" = y ] || { echo "no $OFF: install the kunit/debug ISO first"; exit 1; }
dut 'uname -r; grep -o "vyos-union=[^ ]*" /proc/cmdline; uptime'

echo "== 1. built-in KUnit (boot)"; dut 'sudo dmesg | grep -cE "^\[.*\] +not ok" ; sudo dmesg | grep -E "# (Totals|fman)" | tail -5'
echo "== 2. ask_bridge KUnit"; dut 'sudo modprobe ask_kunit; sudo cat /sys/kernel/debug/kunit/ask_bridge/results 2>&1 | grep -E "ok|not ok" | tail -8'

echo "== 3. arm"; dut 'sudo /usr/local/bin/pcd-snapshot capture -o /tmp/b2-baseline.json >/dev/null && echo baseline captured'
ctl "disengage 0x10"; dut 'sudo ip link set eth3 promisc on'
ctl "l2engage eth3"; ctl "l2add eth3 $DA 0x0800 eth4"
dut 'sudo dmesg | tail -15 | grep -iE "F-148|F-255|l2|engage|error|warn"'
printf '%-10s %6s %6s %6s  %s\n' case hits k_rx dell2 verdict
for c in ${CASES:-arp60 e60 e128 v4udp1000}; do
  h0=$(hits); k0=$(k_rx); r0=$(d2_rx); send $c; sleep 2
  dh=$(( $(hits)-h0 )); dk=$(( $(k_rx)-k0 )); dr=$(( $(d2_rx)-r0 ))
  if [ ${c#tag} != $c ]; then v=info
  elif [ $c = v4udp1000 ]; then [ $dh -eq $N ] && [ $dr -ge $N ] && v=PASS || v=FAIL
  else [ $dh -eq 0 ] && [ $dk -ge $N ] && v=PASS || v=FAIL; fi
  printf '%-10s %6d %6d %6d  %s\n' $c $dh $dk $dr $v
done

echo "== 4. restore"; ctl "l2del eth3 $DA 0x0800"; ctl "disengage 0x10"
dut 'sudo ip link set eth3 promisc off'; ctl "engage 0x10"
dut "sudo grep used $PCD/muram_budget; sudo /usr/local/bin/pcd-snapshot diff /tmp/b2-baseline.json | tail -5"
