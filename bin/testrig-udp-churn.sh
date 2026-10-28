#!/bin/bash
# testrig-udp-churn.sh: R4 conntrack teardown-fuse probe (plans/ASK2-VENDOR-ASK-REVIEW.md R4, vendor patch 142).
#   N UDP tuples (fixed source ports) are opened, offloaded, deleted with `conntrack -D` and reopened on the
#   same 5-tuple CYCLES times, back to back. A final HOLD phase keeps every tuple live at ~1 pkt/s; any
#   conntrack DESTROY seen on the DUT while its tuple is still sending is a flow reaped under a live,
#   hardware-forwarded session (the fuse). Expect 0.
#   bin/testrig-udp-churn.sh   (FAM=4|6, N=500, CYCLES=5, HOLD=150; hosts and key from bin/testrig-env.sh)
set -u
. "$(dirname "$0")/testrig-env.sh"
DUT=${DUT:-$(rig_dut ask2)} || exit 2
rig_require GEN1 GEN2
FAM=${FAM:-4}; N=${N:-500}; CYCLES=${CYCLES:-5}; HOLD=${HOLD:-150}; BURST=${BURST:-3}; PORT=${PORT:-5393}; SP0=${SP0:-30000}
SSHO="-o BatchMode=yes -o ConnectTimeout=8"
d(){ ssh -n $SSHO $RIG_SSHK "$DUT" "$@"; }
g1(){ ssh -n $SSHO $RIG_SSHK "$GEN1" "$@"; }
g2(){ ssh -n $SSHO $RIG_SSHK "$GEN2" "$@"; }
if [ "$FAM" = 6 ]; then SRC=fd99:1::112; DST=fd99:2::113; AF=AF_INET6; else SRC=10.99.1.112; DST=10.99.2.113; AF=AF_INET; fi
used(){ d "sudo cat /sys/kernel/debug/fman_pcd/0/muram_budget | awk '/^used/{print \$2}'"; }
ehash(){ d "sudo cat /sys/kernel/debug/fman_pcd/0/fe_ehash_stats" | grep -c keysz; }
kerr(){ d "sudo dmesg | tail -300 | grep -ciE 'oops|BUG:|WARNING:|call trace'"; }
fail=0
chk(){ if [ "$2" = "$3" ]; then echo "PASS $1 ($3)"; else echo "FAIL $1: got '$2' want '$3'"; fail=$((fail+1)); fi; }
BASE=$(used)

SRV="import socket
s=socket.socket(socket.$AF,socket.SOCK_DGRAM); s.bind(('$DST',$PORT)); s.settimeout(1)
import time; end=time.time()+$((CYCLES*60+HOLD+120)); n=0
while time.time()<end:
    try: s.recvfrom(2048); n+=1
    except socket.timeout: pass
print(n)"
# One socket per tuple; MODE=burst sends BURST seconds of traffic so the flows offload, MODE=hold sends 1 pkt/s per tuple.
CLI="import socket,time,sys
mode=sys.argv[1]
socks=[]
for i in range($N):
    s=socket.socket(socket.$AF,socket.SOCK_DGRAM); s.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1); s.bind(('$SRC',$SP0+i)); socks.append(s)
if mode=='burst':
    for _ in range($BURST*20):
        for s in socks: s.sendto(b'u'*64,('$DST',$PORT))
        time.sleep(0.05)
else:
    print('hold_start',time.time(),flush=True)
    end=time.time()+$HOLD
    while time.time()<end:
        t0=time.time()
        for s in socks: s.sendto(b'u'*64,('$DST',$PORT))
        time.sleep(max(0,1-(time.time()-t0)))
    print('hold_end',time.time(),flush=True)"

d "sudo conntrack -D -p udp --dport $PORT >/dev/null 2>&1; sudo rm -f /tmp/udpchurn-ev.txt; (sudo timeout $((CYCLES*60+HOLD+150)) conntrack -E -e DESTROY -p udp --dport $PORT -o timestamp >/tmp/udpchurn-ev.txt 2>&1 &)"
g2 "pkill -f udpchurn-srv.py; echo \"$SRV\" >/tmp/udpchurn-srv.py; (python3 /tmp/udpchurn-srv.py >/tmp/udpchurn-srv.log 2>&1 &)"
sleep 2
echo "== cfg: fam=$FAM n=$N cycles=$CYCLES hold=${HOLD}s | udp_offload params: $(d "grep -H . /sys/module/ask/parameters/* 2>/dev/null | tr '\n' ' ' | head -c 300")"
g1 "echo \"$CLI\" >/tmp/udpchurn-cli.py"
for c in $(seq 1 "$CYCLES"); do
  g1 "ulimit -n 65536; python3 /tmp/udpchurn-cli.py burst"
  sleep 1
  [ "$c" = 1 ] && echo "== cycle 1: ehash records $(ehash)"
  d "sudo conntrack -D -p udp --dport $PORT >/dev/null 2>&1"
done
g1 "ulimit -n 65536; python3 /tmp/udpchurn-cli.py hold" | tee /tmp/udpchurn-cli.txt
HS=$(awk '$1=="hold_start"{print $2}' /tmp/udpchurn-cli.txt); HE=$(awk '$1=="hold_end"{print $2}' /tmp/udpchurn-cli.txt)
echo "== offloaded ehash records at end of hold: $(ehash)"
sleep 2
d "sudo cat /tmp/udpchurn-ev.txt" >/tmp/udpchurn-ev.local
# conntrack -o timestamp prints "[<epoch>.<usec>]" per event; count DESTROYs inside the HOLD window
LIVE=$(awk -v s="${HS:-0}" -v e="${HE:-0}" 'match($0,/\[[0-9]+\.[0-9]+\]/){t=substr($0,RSTART+1,RLENGTH-2)+0; if(t>=s+2 && t<=e) n++} END{print n+0}' /tmp/udpchurn-ev.local)
echo "DESTROY events total=$(grep -c DESTROY /tmp/udpchurn-ev.local) inside live HOLD window=$LIVE"
chk live-flows-reaped "$LIVE" 0
d "sudo conntrack -D -p udp --dport $PORT >/dev/null 2>&1; true"; sleep 5
chk ehash-after-delete "$(ehash)" 0
g2 "pkill -f udpchurn-srv.py" >/dev/null 2>&1
chk muram-after "$(used)" "$BASE"
chk kernel-errors "$(kerr)" 0
echo "RESULT udp-churn-v$FAM failures=$fail"
exit $fail
