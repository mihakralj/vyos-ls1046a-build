#!/bin/bash
# Bridge B6 soak (plans/ASK2-BRIDGE-OFFLOAD-PLAN.md §13.30): learned-MAC churn under line-rate
# bridged traffic. Bridge eth3+eth4 on .185 as in testrig-bridge-b6.sh; iperf3 dell1 -> dell2
# across the bridge for the whole run keeps 2 base records (one per direction). Each cycle sends
# one IPv4 frame from 32 new source MACs per dell (64 learned MACs, 64 records, each taken over as
# extern_learn) and deletes those FDB entries; every JOIN_EVERY-th cycle eth3 leaves and rejoins
# the bridge (disengage/engage, table freed and rebuilt; the base flows come back from traffic).
# Per cycle: record count, MURAM, new kernel errors (journal cursor), hardware hits of the
# background flow. STOPs on a stuck record count, a new kernel error, or a background flow that
# stops hitting. Builds the bridge with ip(8): REBOOT the DUT afterwards.
set -u
CYCLES=${CYCLES:-100}; JOIN_EVERY=${JOIN_EVERY:-10}
SSHO="-o BatchMode=yes -o ConnectTimeout=8"
DUT="ssh -n $SSHO -i $HOME/.ssh/vyos_key vyos@192.168.1.185"
D1="ssh -n $SSHO admin@192.168.1.112"; D2="ssh -n $SSHO admin@192.168.1.113"
IF=enp1s0f0np0; M1=3c:fd:fe:2a:f0:20; M2=3c:fd:fe:2a:ec:80
P=/sys/module/ask/parameters/bridge_offload; PCD=/sys/kernel/debug/fman_pcd/0; BR=br-b6
ERR='F-256|SYNC timed out|DMA bus error|WARNING:|BUG:|Oops|circular locking|inconsistent lock|suspicious RCU|re-engage failed'

dut(){ $DUT "$1"; }
recs(){ dut "sudo grep -c 'keysz=16 ' $PCD/fe_ehash_stats"; }
hits(){ dut "sudo awk '/keysz=16 /{for(i=1;i<=NF;i++)if(\$i~/^pkt_count=/){split(\$i,a,\"=\");s+=a[2]}}END{print s+0}' $PCD/fe_ehash_stats"; }
# wait_recs <n>: poll up to 15 s for the record count; prints it
wait_recs(){ dut "for i in \$(seq 1 60); do r=\$(sudo grep -c 'keysz=16 ' $PCD/fe_ehash_stats); [ \"\$r\" = $1 ] && break; sleep 0.25; done; echo \$r"; }
# churn_send: one IPv4 frame from each of 32 new source MACs per dell (eth3 side 02:00:00:cc:03:xx)
churn_send(){ for d in 3 4; do [ $d = 3 ] && h="$D1" dst=$M2 || h="$D2" dst=$M1
  $h "sudo python3 -c \"from scapy.all import *
sendp([Ether(dst='$dst',src='02:00:00:cc:0$d:%02x'%i)/IP(src='10.99.78.%d'%i,dst='10.99.78.250')/UDP(sport=47001,dport=47002)/Raw(bytes(200)) for i in range(32)],iface='$IF',verbose=0)\""; done; }
churn_del(){ dut "for p in 3 4; do for i in \$(seq 0 31); do printf 'fdb del 02:00:00:cc:%02x:%02x dev eth%d master\n' \$p \$i \$p; done; done > /tmp/soak.batch; sudo bridge -force -batch /tmp/soak.batch 2>/dev/null"; }

[ "$(dut "test -e $P && echo y")" = y ] || { echo "no $P: install a B6 image first"; exit 1; }
dut 'uname -r; grep -o "vyos-union=[^ ]*" /proc/cmdline; uptime'
dut "sudo /usr/local/bin/pcd-snapshot capture -o /tmp/soak-baseline.json >/dev/null"
base_muram=$(dut "sudo awk '/used/{print \$2}' $PCD/muram_budget"); echo "baseline MURAM $base_muram"

dut "echo 1 | sudo tee $P >/dev/null
for v in eth3.10 eth4.20; do sudo ip link del \$v 2>/dev/null; done
sudo ip addr flush dev eth3; sudo ip addr flush dev eth4
sudo ip link add $BR type bridge vlan_filtering 0
sudo ip link set eth3 master $BR; sudo ip link set eth4 master $BR; sudo ip link set $BR up; sleep 3"
cur=$(dut "sudo journalctl -k -n 0 --show-cursor | sed -n 's/^-- cursor: //p'")

$D1 "sudo ip addr add 10.99.77.112/24 dev $IF"; $D2 "sudo ip addr add 10.99.77.113/24 dev $IF"
$D1 "setsid nohup iperf3 -c 10.99.77.113 -B 10.99.77.112 -P 4 -t 7200 -i 10 > /tmp/bridge-soak-bg.log 2>&1 < /dev/null &"
sleep 8
echo "records $(wait_recs 2) (want 2: the background flow, one per direction)"

stop=""; t0=$(date +%s)
printf '%5s %6s %5s %5s %7s %4s %10s %s\n' cycle t_s peak after muram err bg_hits note
for c in $(seq 1 "$CYCLES"); do
  note=""
  h0=$(hits)
  churn_send; peak=$(wait_recs 66)
  churn_del; after=$(wait_recs 2)
  if [ $((c % JOIN_EVERY)) = 0 ]; then
    dut "sudo ip link set eth3 nomaster; sleep 1; sudo ip link set eth3 master $BR; sleep 3"
    after=$(wait_recs 2); note="join/leave"; sleep 8; h0=$(hits); sleep 3
  fi
  dh=$(( $(hits)-h0 ))
  muram=$(dut "sudo awk '/used/{print \$2}' $PCD/muram_budget")
  out=$(dut "sudo journalctl -k --after-cursor='$cur' --show-cursor")
  cur=$(printf '%s\n' "$out" | sed -n 's/^-- cursor: //p'); [ -z "$cur" ] && cur=$(dut "sudo journalctl -k -n 0 --show-cursor | sed -n 's/^-- cursor: //p'")
  err=$(printf '%s\n' "$out" | grep -cE "$ERR")
  printf '%5d %6d %5s %5s %7s %4d %10d %s\n' $c $(( $(date +%s)-t0 )) "$peak" "$after" "$muram" $err $dh "$note"
  [ "$peak" != 66 ] && stop="peak records $peak"
  [ "$after" != 2 ] && stop="records stuck at $after"
  [ "$err" -gt 0 ] && { stop="kernel errors"; printf '%s\n' "$out" | grep -E "$ERR" | head -5; }
  [ "$dh" -le 0 ] && stop="background flow stopped hitting"
  [ -n "$stop" ] && { echo "STOP at cycle $c: $stop"; break; }
done

$D1 "pkill -f '[i]perf3 -c 10.99.77.113'"; sleep 2
echo "== background iperf3 (10 s intervals, Gbit/s)"
$D1 "grep -E '^\[SUM\]' /tmp/bridge-soak-bg.log | awk '{for(i=1;i<=NF;i++)if(\$i ~ /bits\/sec/){v=\$(i-1);u=\$i;if(u~/^Mbits/)v/=1000;printf \"%.2f \",v}} END{print \"\"}'"
$D1 "sudo ip addr del 10.99.77.112/24 dev $IF"; $D2 "sudo ip addr del 10.99.77.113/24 dev $IF"

echo "== teardown"
dut "sudo ip link set eth3 nomaster; sudo ip link set eth4 nomaster; sudo ip link del $BR
echo 0 | sudo tee $P >/dev/null; sleep 2
sudo grep used $PCD/muram_budget; sudo /usr/local/bin/pcd-snapshot diff /tmp/soak-baseline.json | tail -3"
echo "records $(recs) (want 0); baseline MURAM $base_muram"
echo "Reboot the DUT now to restore eth3.10/eth4.20 and the eth3/eth4 addresses."
