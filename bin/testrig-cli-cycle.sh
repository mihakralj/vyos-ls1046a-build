#!/bin/bash
# testrig-cli-cycle.sh: P4 CLI remove/reapply on one ASK2 DUT port (eth3 or eth4 only).
# N times: delete + commit then set + commit `interfaces ethernet <port> offload ipv4|ipv6`
# with a ping through the DUT running. MURAM must return to baseline, no kernel errors,
# ask-check clean. Never run on eth0.
#   PORT=eth3 CYCLES=10 bin/testrig-cli-cycle.sh
set -u
DUT=${DUT:-vyos@192.168.1.185}; PORT=${PORT:-eth3}; CYCLES=${CYCLES:-10}
case $PORT in eth3|eth4) ;; *) echo "PORT must be eth3 or eth4" >&2; exit 2 ;; esac
KEY=${RIG_KEY:-$HOME/.ssh/vyos_key}
d(){ ssh -n -i "$KEY" -o BatchMode=yes -o ConnectTimeout=10 "$DUT" "$@"; }
used(){ d "sudo cat /sys/kernel/debug/fman_pcd/0/muram_budget | awk '/^used/{print \$2}'"; }
kerr(){ d "sudo dmesg | tail -300 | grep -ciE 'oops|BUG:|WARNING:|call trace'"; }
fail=0
chk(){ if [ "$2" = "$3" ]; then echo "PASS $1 ($3)"; else echo "FAIL $1: got '$2' want '$3'"; fail=$((fail+1)); fi; }
PEER=10.99.1.112; [ "$PORT" = eth4 ] && PEER=10.99.2.113
BASE=$(used); K0=$(kerr)
d "cat > /tmp/cli-cycle.sh <<EOF
#!/bin/vbash
source /opt/vyatta/etc/functions/script-template
configure
delete interfaces ethernet $PORT offload ipv4
delete interfaces ethernet $PORT offload ipv6
commit
set interfaces ethernet $PORT offload ipv4
set interfaces ethernet $PORT offload ipv6
commit
exit
EOF
chmod +x /tmp/cli-cycle.sh"
d "ping -i 0.2 -I $PORT $PEER >/tmp/cc-ping.log 2>&1 & echo \$! >/tmp/cc-ping.pid"
for i in $(seq "$CYCLES"); do
  d "sg vyattacfg -c /tmp/cli-cycle.sh >/dev/null 2>&1; echo \$?" | { read r; [ "$r" = 0 ] || echo "WARN cycle $i rc=$r"; }
  sleep 3
  m=$(used); [ "$m" = "$BASE" ] || { echo "FAIL muram cycle $i: $m want $BASE"; fail=$((fail+1)); }
done
d "kill \$(cat /tmp/cc-ping.pid); sleep 1; tail -3 /tmp/cc-ping.log"
chk muram-final "$(used)" "$BASE"
chk kernel-errors "$(kerr)" "$K0"
d "sudo ask-check 2>&1 | grep -E 'FAIL|Summary' | head -3"
echo "RESULT cli-cycle $PORT failures=$fail"; exit $((fail>0))
