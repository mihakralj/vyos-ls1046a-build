#!/usr/bin/env bash
# PPPoE session-lifecycle churn under traffic (plans/ASK2-PPPOE-OFFLOAD-PLAN.md, gate A8
# "Session lifecycle": 10,000 connect/reconnect/disconnect cycles, no stale records or mappings).
# ASK2 DUT only. The rig must already carry the PPPoE session used by the quick harness:
# DUT pppoe10 (source-interface eth3, address assigned per session) <-> gen1 concentrator
# 10.99.50.1 / fd99:50::1, gen2 behind DUT eth4 (10.99.2.113 / fd99:2::113), return routes in
# place (plans archive §3a).
#
# Each cycle, with low-rate TCP running in both directions (decap gen1 -> gen2, encap gen2 -> gen1)
# for each family in FAMILIES:
#   1. wait for the session, read its PPPoE session ID from /proc/net/pppoe;
#   2. hardware proof before the flap: fe_ehash_stats pkt_count grows over HW_WIN seconds;
#   3. flap the session while the TCP flows are in flight. MODE=client: systemctl stop/start
#      ppp@pppoe10 on the DUT (what `disconnect`/`connect interface` do); MODE=server: kill the
#      concentrator's pppd for this session, the DUT pppd redials by itself; MODE=mix: every
#      SERVER_EVERY-th cycle is a server-side kill, the rest client-side; MODE=kill: no flap, the
#      ask.pppoe_offload kill switch goes 1 -> 0 (records must drop to the baseline, no hardware
#      hits and kernel forwarding on pppoe10 while off) -> 1 on the same session (same SID);
#   4. hardware proof after the reconnect (new session ID, same TCP flows): records must be
#      rebuilt and hit again, otherwise the flows are stuck on stale state or fell to software;
#   5. every flow must finish with data delivered (iperf3 received bytes > 0, no iperf3 error).
# Every DRAIN_EVERY cycles and at the end: delete the test conntrack entries, then the record count
# must return to the baseline within DRAIN_S and MURAM `used` must equal the baseline.
# Every cycle: new kernel errors (journal cursor) stop the run. At the end: pcd-snapshot diff.
#
# The flows are TCP at RATE per flow (default 100M): no flood, as the plan requires. A full
# gate run is CYCLES=10000 (about 30 s per cycle, ~3.5 days); start with the default 20.
# NAT over PPPoE, PPPoE-over-VLAN and pool exhaustion are separate gates and are not covered here.
#
# Usage: bin/testrig-pppoe-churn.sh
# Env:   CYCLES MODE SERVER_EVERY FAMILIES RATE TRAFFIC_S SERVER_TRAFFIC_S HW_WIN UP_S DOWN_HOLD DRAIN_EVERY DRAIN_S
#        RUN_ON=dell2 (run on gen2; gen2 commands run locally). Hosts and key: bin/testrig-env.sh.
#        RIG_NS=$VENDOR_NS runs both generators' commands as root inside that netns (the ports facing
#        the second DUT; start its concentrator with the pppoe-server line of testrig-pppoe-vendor.sh).
# Exit: 0 PASS, 1 FAIL, 2 setup error.
set -u
. "$(dirname "$0")/testrig-env.sh"
rig_require ASK2_DUT GEN1
CYCLES=${CYCLES:-20}; MODE=${MODE:-mix}; SERVER_EVERY=${SERVER_EVERY:-5}
FAMILIES=${FAMILIES:-"4 6"}; RATE=${RATE:-100M}; TRAFFIC_S=${TRAFFIC_S:-30}; HW_WIN=${HW_WIN:-3}
UP_S=${UP_S:-60}; DOWN_HOLD=${DOWN_HOLD:-1}; DRAIN_EVERY=${DRAIN_EVERY:-10}; DRAIN_S=${DRAIN_S:-30}
# a server-side kill waits for the DUT pppd redial (holdoff up to 30 s): its flows must outlast it
SERVER_TRAFFIC_S=${SERVER_TRAFFIC_S:-$((TRAFFIC_S+UP_S))}
RUN_ON=${RUN_ON:-agent}; RIG_NS=${RIG_NS:-}
case $MODE in client|server|mix|kill) ;; *) echo "MODE must be client, server, mix or kill" >&2; exit 2 ;; esac
[ "$TRAFFIC_S" -ge $((4+2*HW_WIN+DOWN_HOLD+15)) ] || { echo "TRAFFIC_S too short for proof + flap + reconnect" >&2; exit 2; }
[ "$RUN_ON" = dell2 ] || rig_require GEN2

SSHO="-o BatchMode=yes -o ConnectTimeout=8"
DUT="ssh -n $SSHO $RIG_SSHK $ASK2_DUT"
D1="ssh -n $SSHO $RIG_SSHK $GEN1"; D2="ssh -n $SSHO $RIG_SSHK ${GEN2:-}"
[ "$RUN_ON" = dell2 ] && D2="bash -c"
if [ -n "$RIG_NS" ]; then
  # the command goes in on stdin, so its quoting is the same as without a netns
  nsd1(){ ssh $SSHO $RIG_SSHK $GEN1 "sudo ip netns exec $RIG_NS bash -s" <<< "$1"; }
  nsd2(){ if [ "$RUN_ON" = dell2 ]; then sudo ip netns exec "$RIG_NS" bash -s <<< "$1"
          else ssh $SSHO $RIG_SSHK "$GEN2" "sudo ip netns exec $RIG_NS bash -s" <<< "$1"; fi; }
  D1=nsd1; D2=nsd2
fi
PCD=/sys/kernel/debug/fman_pcd/0; IFN=pppoe10; UNIT=ppp@pppoe10.service
KSW=/sys/module/ask/parameters/pppoe_offload
ERR='F-256|SYNC timed out|DMA bus error|WARNING:|BUG:|Oops|circular locking|inconsistent lock|suspicious RCU|re-engage failed|RX-deaf|Err FD'
TAG=pppoe-churn-$$; FAILS=0

dut(){ $DUT "$1"; }
recs(){ dut "sudo grep -c 'keysz=' $PCD/fe_ehash_stats"; }
muram(){ dut "sudo awk '/^used/{print \$2}' $PCD/muram_budget"; }
sid(){ dut "awk 'NR==2{print \$1}' /proc/net/pppoe"; }
session_up(){ dut "ip -4 addr show $IFN 2>/dev/null | grep -q 'peer 10.99.50.1'" ; }
wait_up(){ local i; for i in $(seq 1 "$UP_S"); do session_up && return 0; sleep 1; done; return 1; }
wait_recs(){ dut "for i in \$(seq 1 $((DRAIN_S*4))); do r=\$(sudo grep -c 'keysz=' $PCD/fe_ehash_stats); [ \"\$r\" -le $1 ] && break; sleep 0.25; done; echo \$r"; }
ct_flush(){ dut "for a in 10.99.50.1 fd99:50::1; do sudo conntrack -D -s \$a >/dev/null 2>&1; sudo conntrack -D -d \$a >/dev/null 2>&1; done; true"; }

# flow table: name|client host|bind|server address|port; servers listen on the other generator
flows(){ local f; for f in $FAMILIES; do
  if [ "$f" = 4 ]; then echo "decap-v4|d1|10.99.50.1|10.99.2.113|5311"; echo "encap-v4|d2|10.99.2.113|10.99.50.1|5312"
  else echo "decap-v6|d1|fd99:50::1|fd99:2::113|5313"; echo "encap-v6|d2|fd99:2::113|fd99:50::1|5314"; fi; done; }
gen(){ [ "$1" = d1 ] && echo "$D1" || echo "$D2"; }
other(){ [ "$1" = d1 ] && echo d2 || echo d1; }

servers(){ local n h b a p; while IFS='|' read -r n h b a p; do
  $(gen "$(other "$h")") "pkill -f '^iperf3 -s -p $p -D' ; iperf3 -s -p $p -D >/dev/null 2>&1"; done < <(flows); }
servers_stop(){ local n h b a p; while IFS='|' read -r n h b a p; do
  $(gen "$(other "$h")") "pkill -f '^iperf3 -s -p $p -D'; true"; done < <(flows); }
clients_start(){ local n h b a p; while IFS='|' read -r n h b a p; do
  $(gen "$h") "rm -f /tmp/$TAG-$n.json; setsid nohup iperf3 -c $a -B $b -p $p -b $RATE -t $1 -J > /tmp/$TAG-$n.json 2>&1 < /dev/null &"
  done < <(flows); }
# clients_check: wait for every client, print "name bytes" (bytes 0 = no data or an iperf3 error)
clients_check(){ local n h b a p; while IFS='|' read -r n h b a p; do
  $(gen "$h") "for i in \$(seq 1 $(($1+30))); do pgrep -f '^iperf3 -c $a -B $b -p $p ' >/dev/null || break; sleep 1; done
pkill -f '^iperf3 -c $a -B $b -p $p '
python3 -c 'import json,sys
try:
    j=json.load(open(sys.argv[1]))
    r=int(j.get(\"end\",{}).get(\"sum_received\",{}).get(\"bytes\",0))
    if not j.get(\"error\") and r: print(sys.argv[2], r); sys.exit()
    # the end-of-test control exchange can stall after the data phase completed: trust the
    # per-second sender intervals when they ran to the end of the test with non-zero bytes in the last 4 s
    iv=j.get(\"intervals\",[])
    ok=iv and iv[-1][\"sum\"][\"end\"]>=float(sys.argv[3])-2 and sum(i[\"sum\"][\"bytes\"] for i in iv[-4:])>0
    print(sys.argv[2], int(sum(i[\"sum\"][\"bytes\"] for i in iv)) if ok else 0)
except Exception:
    print(sys.argv[2], 0)' /tmp/$TAG-$n.json $n $1"; done < <(flows); }
# hw_delta: pkt_count growth over HW_WIN s, per record (records deleted or re-created inside the
# window must not cancel growth elsewhere)
hw_delta(){ dut "s(){ sudo sed -n 's/.*record_dma=\\(0x[0-9a-f]*\\).*pkt_count=\\([0-9]*\\).*/\\1 \\2/p' $PCD/fe_ehash_stats; }
{ s; echo --; sleep $HW_WIN; s; } | awk '/^--/{k=1; next} !k{a[\$1]=\$2; next} {d=((\$1 in a) && \$2>=a[\$1]) ? \$2-a[\$1] : \$2; t+=d} END{print t+0}'"; }

# swpkts: packets the kernel forwarded through the session (pppoe10 rx + tx)
swpkts(){ dut "echo \$(( \$(cat /sys/class/net/$IFN/statistics/rx_packets) + \$(cat /sys/class/net/$IFN/statistics/tx_packets) ))"; }

flap(){ # $1 = client|server|kill
  if [ "$1" = kill ]; then
    # KOFF = "records hw_hits sw_packets" while the switch is off
    dut "echo 0 | sudo tee $KSW >/dev/null" || { echo "cannot clear $KSW"; return 1; }
    local r h w0 w1; r=$(wait_recs "$base_recs"); w0=$(swpkts); h=$(hw_delta); w1=$(swpkts)
    KOFF="$r $h $((w1-w0))"
    dut "echo 1 | sudo tee $KSW >/dev/null" || { echo "cannot set $KSW"; return 1; }
  elif [ "$1" = client ]; then
    dut "sudo systemctl stop $UNIT"; sleep "$DOWN_HOLD"
    session_up && { echo "session still up after stop"; return 1; }
    dut "sudo systemctl start $UNIT"
  else
    # the concentrator hands out a new peer address per session: find its ppp unit by the DUT's
    local la; la=$(dut "ip -o -4 addr show $IFN | awk '{print \$4; exit}'")
    [ -n "$la" ] || { echo "no local address on $IFN"; return 1; }
    if [ -n "$RIG_NS" ]; then
      # /run/pppN.pid is shared with the root netns concentrator: pick the netns pppd by address
      $D1 "k=''; for p in \$(ip netns pids $RIG_NS); do [ \"\$(cat /proc/\$p/comm 2>/dev/null)\" = pppd ] || continue
tr '\\0' ' ' < /proc/\$p/cmdline | grep -q ':$la ' && k=\"\$k \$p\"; done; [ -n \"\$k\" ] && kill \$k" \
        || { echo "no concentrator pppd in $RIG_NS for peer $la"; return 1; }
    else
      $D1 "i=\$(ip -o -4 addr show | awk -v a='peer $la/' 'index(\$0, a){print \$2; exit}'); [ -n \"\$i\" ] && sudo kill \$(cat /run/\$i.pid 2>/dev/null || cat /var/run/\$i.pid)" \
        || { echo "no concentrator pppd for peer $la"; return 1; }
    fi
    sleep "$DOWN_HOLD"
  fi
  return 0
}

cleanup(){ servers_stop >/dev/null 2>&1; dut "echo 1 | sudo tee $KSW >/dev/null"; session_up || dut "sudo systemctl start $UNIT"; }
trap cleanup EXIT

# preflight
dut 'uname -r; grep -o "vyos-union=[^ ]*" /proc/cmdline; uptime' || exit 2
[ "$(dut "cat /sys/module/ask/parameters/pppoe_offload")" = Y ] || { echo "ask.pppoe_offload is not Y"; exit 2; }
session_up || { echo "$IFN is not up with peer 10.99.50.1"; exit 2; }
dut "sudo sh -c 'command -v conntrack' >/dev/null" || { echo "conntrack(8) missing on the DUT"; exit 2; }
for f in $FAMILIES; do
  [ "$f" = 4 ] && p="ping -c2 -W2 -I 10.99.50.1 10.99.2.113" || p="ping -6 -c2 -W2 -I fd99:50::1 fd99:2::113"
  $D1 "$p >/dev/null" || { echo "gen1 -> gen2 over the session (IPv$f) fails: check routes"; exit 2; }
  [ "$f" = 4 ] && p="ping -c2 -W2 -I 10.99.2.113 10.99.50.1" || p="ping -6 -c2 -W2 -I fd99:2::113 fd99:50::1"
  $D2 "$p >/dev/null" || { echo "gen2 -> gen1 over the session (IPv$f) fails: check routes"; exit 2; }
done
ct_flush; base_recs=$(wait_recs 0); base_muram=$(muram)
dut "sudo /usr/local/bin/pcd-snapshot capture -o /tmp/$TAG-baseline.json >/dev/null" || { echo "pcd-snapshot capture failed"; exit 2; }
cur=$(dut "sudo journalctl -k -n 0 --show-cursor | sed -n 's/^-- cursor: //p'")
echo "baseline: records $base_recs, MURAM used $base_muram, session $(sid), mode $MODE, families $FAMILIES, rate $RATE/flow"
servers

printf '%5s %6s %-6s %-9s %-9s %8s %8s %5s %5s %7s %4s %s\n' cycle t_s how sid_pre sid_post hw_pre hw_post r_up r_dn muram err note
t0=$(date +%s); stop=""; reused=0; changed=0
for c in $(seq 1 "$CYCLES"); do
  note=""
  how=client; { [ "$MODE" = server ] || { [ "$MODE" = mix ] && [ $((c % SERVER_EVERY)) = 0 ]; }; } && how=server
  [ "$MODE" = kill ] && how=kill; KOFF=""
  wait_up || { stop="session not up before cycle"; break; }
  s0=$(sid)
  dur=$TRAFFIC_S; [ "$how" = server ] && dur=$SERVER_TRAFFIC_S
  # the off phase waits up to DRAIN_S for the flush: the flows must outlast it
  [ "$how" = kill ] && dur=$((TRAFFIC_S+DRAIN_S))
  clients_start "$dur"; sleep 4
  hp=$(hw_delta); r_up=$(recs)
  flap "$how" || { stop="flap failed ($how)"; }
  r_dn=$(recs)
  if [ -z "$stop" ]; then
    wait_up || stop="session did not come back within ${UP_S}s ($how)"
  fi
  s1=$(sid); hq=0
  [ -z "$stop" ] && { sleep 3; hq=$(hw_delta); }
  short=$(clients_check "$dur" | awk '$2==0{printf "%s ",$1}')
  [ "$s0" = "$s1" ] && reused=$((reused+1)) || changed=$((changed+1))
  if [ $((c % DRAIN_EVERY)) = 0 ]; then
    ct_flush; dr=$(wait_recs "$base_recs"); m=$(muram); note="drain recs=$dr"
    [ "$dr" -le "$base_recs" ] || stop="records stuck at $dr after drain (baseline $base_recs)"
    [ "$m" = "$base_muram" ] || stop="MURAM used $m after drain (baseline $base_muram)"
  else m=$(muram); fi
  out=$(dut "sudo journalctl -k --after-cursor='$cur' --show-cursor")
  ncur=$(printf '%s\n' "$out" | sed -n 's/^-- cursor: //p'); [ -n "$ncur" ] && cur=$ncur
  err=$(printf '%s\n' "$out" | grep -cE "$ERR")
  printf '%5d %6d %-6s %-9s %-9s %8s %8s %5s %5s %7s %4d %s\n' "$c" $(( $(date +%s)-t0 )) "$how" "$s0" "$s1" "$hp" "$hq" "$r_up" "$r_dn" "$m" "$err" "$note"
  [ "$hp" -gt 0 ] || stop=${stop:-"no hardware hits before the flap"}
  [ "$hq" -gt 0 ] || stop=${stop:-"no hardware hits after the reconnect (stale state or software path)"}
  [ -z "$short" ] || stop=${stop:-"flows without delivered data: $short"}
  [ "$err" -eq 0 ] || { stop=${stop:-"kernel errors"}; printf '%s\n' "$out" | grep -E "$ERR" | head -5; }
  if [ "$how" = kill ]; then
    read -r ko_r ko_h ko_w <<< "${KOFF:-x x x}"
    echo "      off: records $ko_r (baseline $base_recs), hw hits $ko_h, kernel pkts $ko_w"
    [ "$ko_r" = "$base_recs" ] || stop=${stop:-"records not flushed by pppoe_offload=0 ($ko_r)"}
    [ "$ko_h" = 0 ] || stop=${stop:-"hardware hits while pppoe_offload=0 ($ko_h)"}
    [ "${ko_w:-0}" -gt 0 ] 2>/dev/null || stop=${stop:-"no kernel forwarding while pppoe_offload=0"}
    [ "$s0" = "$s1" ] || stop=${stop:-"session ID changed across the kill switch"}
  fi
  [ -n "$stop" ] && { echo "STOP at cycle $c: $stop"; FAILS=$((FAILS+1)); break; }
done
echo "session IDs: changed on $changed cycle(s), same ID on $reused cycle(s)"

echo "== final drain"
servers_stop; session_up || { dut "sudo systemctl start $UNIT"; wait_up; }
ct_flush; left=$(wait_recs "$base_recs"); m=$(muram)
echo "records $left (baseline $base_recs), MURAM used $m (baseline $base_muram)"
[ "$left" -le "$base_recs" ] || { echo "records: FAIL"; FAILS=$((FAILS+1)); }
[ "$m" = "$base_muram" ] || { echo "MURAM: FAIL"; FAILS=$((FAILS+1)); }
dut "sudo /usr/local/bin/pcd-snapshot diff /tmp/$TAG-baseline.json | tail -3; exit \${PIPESTATUS[0]}" || { echo "pcd-snapshot diff: FAIL"; FAILS=$((FAILS+1)); }
echo "== result: $FAILS failure(s)"; [ "$FAILS" -eq 0 ] && echo PASS || echo FAIL
exit $(( FAILS > 0 ))
