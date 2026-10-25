#!/bin/bash
# testrig-port-loop.sh: P4 per-port loop on one ASK2 DUT port (eth3 or eth4 only).
# Idempotent engage, MTU steps, link down/up, N-cycle disengage/engage churn with a
# ping running, then ask-check, MURAM and pcd-snapshot against the engaged baseline.
#   DUT=vyos@192.168.1.185 PORT=eth3 CYCLES=100 PEER=10.99.1.112 bash bin/testrig-port-loop.sh
# Never run on eth0 (SSH lifeline) and never while the A8 soak owns the DUT.
set -u
DUT=${DUT:-vyos@192.168.1.185}; PORT=${PORT:-eth3}; CYCLES=${CYCLES:-100}
case $PORT in
  eth3) HW=0x10; PEER=${PEER:-10.99.1.112} ;;
  eth4) HW=0x11; PEER=${PEER:-10.99.2.113} ;;
  *) echo "PORT must be eth3 or eth4" >&2; exit 2 ;;
esac
KEY=${RIG_KEY:-$HOME/.ssh/vyos_key}
d(){ ssh -i "$KEY" -o ConnectTimeout=10 "$DUT" "$@"; }
fail=0
chk(){ if [ "$2" = "$3" ]; then echo "PASS $1 ($3)"; else echo "FAIL $1: got '$2' want '$3'"; fail=$((fail+1)); fi; }
used(){ d "sudo cat /sys/kernel/debug/fman_pcd/0/muram_budget | awk '/^used/{print \$2}'"; }
snapdiff(){ d "sudo pcd-snapshot diff /tmp/pl-base.json 2>&1 | tail -2"; }
tool(){ d "sudo /usr/local/bin/vyos-offload-ask --port $HW $*" 2>&1; }
kerr(){ d "sudo dmesg | tail -200 | grep -ciE 'oops|BUG:|WARNING:|call trace'"; }

echo "== $PORT ($HW) baseline"
d "sudo ask-check 2>&1 | grep -E 'FAIL|Summary' | head -3"
d "sudo pcd-snapshot capture -o /tmp/pl-base.json >/dev/null 2>&1"
BASE=$(used); echo "MURAM used baseline $BASE"
K0=$(kerr)

echo "== idempotent engage x3"
for i in 1 2 3; do tool engage 3 >/dev/null; done
chk muram-after-idempotent "$(used)" "$BASE"

echo "== MTU steps (down to 1500 last)"
for m in 1280 2500 3600 1500; do
  d "sudo ip link set $PORT mtu $m" ; sleep 2
  chk "mtu-$m-applied" "$(d "cat /sys/class/net/$PORT/mtu")" "$m"
  chk "mtu-$m-muram" "$(used)" "$BASE"
done
sleep 3; d "ping -c 3 -W 2 -I $PORT $PEER >/dev/null 2>&1; echo \$?" | { read r; chk ping-after-mtu "$r" 0; }

echo "== link down/up x3"
for i in 1 2 3; do d "sudo ip link set $PORT down; sleep 2; sudo ip link set $PORT up"; sleep 6; done
d "ping -c 3 -W 2 -I $PORT $PEER >/dev/null 2>&1; echo \$?" | { read r; chk ping-after-flap "$r" 0; }
chk muram-after-flap "$(used)" "$BASE"

echo "== $CYCLES-cycle disengage/engage churn"
d "ping -i 0.2 -I $PORT $PEER >/dev/null 2>&1 & echo \$! >/tmp/pl-ping.pid"
for i in $(seq 1 "$CYCLES"); do
  tool disengage >/dev/null || { echo "FAIL disengage cycle $i"; fail=$((fail+1)); break; }
  tool engage 3 >/dev/null || { echo "FAIL engage cycle $i"; fail=$((fail+1)); break; }
  [ $((i % 25)) = 0 ] && echo "cycle $i used=$(used)"
done
d 'kill $(cat /tmp/pl-ping.pid) 2>/dev/null; rm -f /tmp/pl-ping.pid'
sleep 3
chk muram-after-churn "$(used)" "$BASE"
echo "pcd-snapshot diff:"; snapdiff
chk kernel-errors "$(kerr)" "$K0"
d "sudo ask-check 2>&1 | grep -E 'FAIL|Summary' | head -3"
d "sudo rm -f /tmp/pl-base.json"
echo "RESULT $PORT failures=$fail"; exit $((fail>0))
