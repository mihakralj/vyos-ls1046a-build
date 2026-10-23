#!/bin/bash
# testrig-a14.sh: A14 safety probes on the ASK2 DUT (gen1 eth3 -> DUT -> eth4 gen2, root namespaces).
#   ttl   FAM=4|6  : TTL/hop-limit 0,1,2 probes on a live offloaded TCP tuple. Expect ICMP time-exceeded
#                    for 0/1 at gen1, TTL/hlim 2 forwarded as 1, nothing arriving at gen2 with hl 0/255.
#   neigh FAM=4|6  : replace the eth4 neighbour of gen2 with a bogus MAC under a paced flow. Expect
#                    frames to stop reaching gen2 within ~1 s (no stale hardware rewrite).
#   f266  FAM=4|6  : 100 sequential 20 KB TCP flows (REUSE=k cycles k fixed source ports to force 5-tuple reuse);
#                    expect all complete, 0 HW_OFFLOAD conntrack entries and 0 ehash records after close.
#   bin/testrig-a14.sh ttl|neigh|f266   (FAM=4 default; hosts and key from bin/testrig-env.sh)
set -u
. "$(dirname "$0")/testrig-env.sh"
DUT=${DUT:-$(rig_dut ask2)} || exit 2
rig_require GEN1 GEN2
MODE=${1:-ttl}; FAM=${FAM:-4}; PORT=${PORT:-5391}; CP=${CP:-$((40000+RANDOM%2000))}
NIC1=${NIC1:-enp1s0f0np0}; NIC2=${NIC2:-enp1s0f0np0}
SSHO="-o BatchMode=yes -o ConnectTimeout=8"
d(){ ssh -n $SSHO $RIG_SSHK "$DUT" "$@"; }
g1(){ ssh -n $SSHO $RIG_SSHK "$GEN1" "$@"; }
g2(){ ssh -n $SSHO $RIG_SSHK "$GEN2" "$@"; }
if [ "$FAM" = 6 ]; then SRC=fd99:1::112; DST=fd99:2::113; NH=fd99:1::185; OUT_ND=$DST
else SRC=10.99.1.112; DST=10.99.2.113; NH=10.99.1.185; OUT_ND=$DST; fi
DMAC=$(d "cat /sys/class/net/eth3/address")
used(){ d "sudo cat /sys/kernel/debug/fman_pcd/0/muram_budget | awk '/^used/{print \$2}'"; }
pkts(){ d "sudo cat /sys/kernel/debug/fman_pcd/0/fe_ehash_stats" | sed -n 's/.*pkt_count=\([0-9]*\).*/\1/p' | awk '{s+=$1}END{print s+0}'; }
kerr(){ d "sudo dmesg | tail -300 | grep -ciE 'oops|BUG:|WARNING:|call trace'"; }
fail=0
chk(){ if [ "$2" = "$3" ]; then echo "PASS $1 ($3)"; else echo "FAIL $1: got '$2' want '$3'"; fail=$((fail+1)); fi; }
BASE=$(used)

PROBE='import sys
from scapy.all import Ether, IP, IPv6, TCP, sendp
fam, nic, dmac, src, dst, sport, dport = sys.argv[1:8]
for h in [int(x) for x in sys.argv[8].split(",")]:
    ip = IPv6(src=src, dst=dst, hlim=h) if fam == "6" else IP(src=src, dst=dst, ttl=h)
    sendp(Ether(dst=dmac)/ip/TCP(sport=int(sport), dport=int(dport), flags="A", seq=12345, ack=1), iface=nic, count=5, inter=0.1, verbose=0)'

start_flow(){ # $1 = rate, $2 = seconds
  g2 "sudo pkill iperf3; iperf3 -s -p $PORT -D -1" >/dev/null 2>&1
  g1 "sudo pkill iperf3; rm -f /tmp/a14-iperf.log; nohup iperf3 -$FAM -c $DST -B $SRC --cport $CP -p $PORT -t $2 -b $1 >/tmp/a14-iperf.log 2>&1 &"
  sleep 5
}

if [ "$MODE" = ttl ]; then
  if [ "$FAM" = 6 ]; then F2="ip6 and tcp port $PORT and (ip6[7] < 3 or ip6[7] > 250)"; F1="icmp6 and ip6[40]==3"
  else F2="tcp port $PORT and ip[8] < 3"; F1="icmp and icmp[0]==11"; fi
  g2 "sudo rm -f /tmp/a14-rx.txt; (sudo timeout 30 tcpdump -i $NIC2 -n -v -c 80 '$F2' >/tmp/a14-rx.txt 2>&1 &)"
  g1 "sudo rm -f /tmp/a14-icmp.txt; (sudo timeout 30 tcpdump -i $NIC1 -n -v -c 80 '$F1' >/tmp/a14-icmp.txt 2>&1 &)"
  sleep 1
  start_flow 5M 22
  echo "== offloaded entries: $(d "sudo cat /sys/kernel/debug/fman_pcd/0/fe_ehash_stats" | grep -c keysz)"
  for H in 0 1 2; do
    g1 "echo '$PROBE' >/tmp/a14probe.py; sudo python3 /tmp/a14probe.py $FAM $NIC1 $DMAC $SRC $DST $CP $PORT $H"; sleep 1
  done
  sleep 18
  ICMP=$(g1 "grep -c -E 'time exceeded|time-exceeded' /tmp/a14-icmp.txt")
  echo "== gen2 packets seen with low/wrapped hl:"
  g2 "grep -E 'hlim|ttl' /tmp/a14-rx.txt | sed -E 's/.*(hlim [0-9]+|ttl [0-9]+).*/\1/' | sort | uniq -c"
  echo "ICMP time-exceeded at gen1: $ICMP (want 10: hl 0 and 1 x5 each)"
  chk icmp-count "$ICMP" 10
  if [ "$FAM" = 6 ]; then WRAP=$(g2 "grep -c -E 'hlim (0|255)' /tmp/a14-rx.txt"); else WRAP=$(g2 "grep -c -E 'ttl 0' /tmp/a14-rx.txt"); fi
  chk no-wrapped-hl-at-gen2 "$WRAP" 0
  g1 "tail -3 /tmp/a14-iperf.log"
fi

if [ "$MODE" = neigh ]; then
  FAKE=02:00:00:00:00:01
  REAL=$(g2 "cat /sys/class/net/$NIC2/address")
  g2 "sudo rm -f /tmp/a14.pcap; (sudo timeout 30 tcpdump -i $NIC2 -s 64 -tt -e -n -w /tmp/a14.pcap 'tcp port $PORT' >/dev/null 2>&1 &)"
  sleep 1
  start_flow 20M 20
  echo "== pre: offloaded entries $(d "sudo cat /sys/kernel/debug/fman_pcd/0/fe_ehash_stats" | grep -c keysz)"
  T=$(date +%s.%N)
  d "sudo ip neigh replace $OUT_ND lladdr $FAKE dev eth4 nud permanent"
  sleep 3
  d "sudo ip neigh replace $OUT_ND lladdr $REAL dev eth4 nud permanent; sudo ip neigh change $OUT_ND dev eth4 lladdr $REAL nud reachable" >/dev/null 2>&1
  sleep 14
  g2 "sudo tcpdump -r /tmp/a14.pcap -e -n -tt 2>/dev/null" | awk -v t=$T -v real=$REAL -v fake=$FAKE '
    { ts=$1; dst=$4; sub(/,$/,"",dst);
      ph=(ts<t)?"pre":(ts<t+1?"w1":(ts<t+3?"post":"late"));
      if (dst==real) c[ph" real"]++; else if (dst==fake) c[ph" fake"]++; else c[ph" other"]++ }
    END { for (k in c) print k, c[k] }' | sort | tee /tmp/a14-neigh.txt
  PRE=$(awk '$1=="pre" && $2=="real"{print $3}' /tmp/a14-neigh.txt); chk flow-was-running "$([ "${PRE:-0}" -gt 100 ] && echo yes || echo no)" yes
  P3=$(awk '$1=="post" && $2=="real"{print $3}' /tmp/a14-neigh.txt); chk no-stale-forwarding-1s-to-3s-after-replace "${P3:-0}" 0
  g1 "tail -3 /tmp/a14-iperf.log"
fi

if [ "$MODE" = f266 ]; then
  N=${N:-100}; PORT=${F266_PORT:-5392}
  SRV="import socket
s=socket.socket(socket.AF_INET$([ $FAM = 6 ] && echo 6),socket.SOCK_STREAM); s.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1)
s.bind(('$DST',$PORT)); s.listen(64); d=b'x'*20000
for i in range($N):
    c,_=s.accept(); c.recv(16); c.sendall(d); c.close()"
  CLI="import socket
ok=0
for i in range($N):
    s=socket.socket(socket.AF_INET$([ $FAM = 6 ] && echo 6),socket.SOCK_STREAM); s.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1); s.settimeout(5); s.bind(('$SRC',$([ -n "${REUSE:-}" ] && echo "50000+i%$REUSE" || echo 0))); s.connect(('$DST',$PORT)); s.sendall(b'g'); n=0
    while True:
        b=s.recv(65536)
        if not b: break
        n+=len(b)
    s.close(); ok+=(n==20000)
print(ok)"
  d "sudo conntrack -D -p tcp --dport $PORT >/dev/null 2>&1; true"
  g2 "[ -f /tmp/f266srv.pid ] && sudo kill \$(cat /tmp/f266srv.pid) 2>/dev/null; echo \"$SRV\" >/tmp/f266srv.py; (timeout 120 python3 /tmp/f266srv.py >/tmp/f266-srv.log 2>&1 & echo \$! >/tmp/f266srv.pid)"
  sleep 1
  echo "== tcp_ctl_punt=$(d cat /sys/module/ask/parameters/tcp_ctl_punt) ehash_before=$(d "sudo cat /sys/kernel/debug/fman_pcd/0/fe_ehash_stats" | grep -c keysz)"
  g1 "echo \"$CLI\" >/tmp/f266cli.py; python3 /tmp/f266cli.py" | tee /tmp/f266-ok.txt
  chk flows-completed "$(tail -1 /tmp/f266-ok.txt)" "$N"
  sleep 8
  d "sudo conntrack -L -p tcp --dport $PORT 2>/dev/null" >/tmp/f266-ct.txt
  echo "ct_total=$(wc -l </tmp/f266-ct.txt) hw_offload=$(grep -c HW_OFFLOAD /tmp/f266-ct.txt)"
  awk '{print $4}' /tmp/f266-ct.txt | sort | uniq -c
  chk ehash-after "$(d "sudo cat /sys/kernel/debug/fman_pcd/0/fe_ehash_stats" | grep -c keysz)" 0
  chk hw-offload-after "$(grep -c HW_OFFLOAD /tmp/f266-ct.txt)" 0
fi

g1 "sudo pkill iperf3" >/dev/null 2>&1; g2 "sudo pkill iperf3" >/dev/null 2>&1
chk muram-after "$(used)" "$BASE"
chk kernel-errors "$(kerr)" 0
echo "RESULT a14-$MODE-v$FAM failures=$fail"
exit $fail
