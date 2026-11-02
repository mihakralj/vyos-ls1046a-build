#!/usr/bin/env bash
# testrig-wgfp-p2b3.sh — WireGuard fast path P2b-3 gate (plans/ASK2-WIREGUARD-FASTPATH-PLAN.md): the slow-path punt,
# roaming, AllowedIPs re-check, forced rekey, oversize/PMTU and wg0 deletion, on the automatic (P2b-2) records.
# Prereq: a live tunnel (KEEP=1 bin/testrig-wireguard.sh setup wg-v4) on an image with the P2b-3 fastpath.c.
# Usage: bin/testrig-wgfp-p2b3.sh [rekey rekey120 roam aip pmtu del netns]   (default: rekey roam aip pmtu)
#   STOCK=1 runs the same tests with the fast path off (the stock WireGuard reference for A/B).
#   dell2 is given persistent-keepalive 1 for rekey/roam, as a roaming client would have: a pure TCP receiver
#   that roams or loses its session sends nothing, and stock WireGuard then never recovers either (2026-11-02).
#   del deletes the DUT wg0 (re-run the tunnel setup afterwards); netns runs the upstream wireguard netns.sh
#   selftest on the DUT (needs the script at $NETNS_SH, default: from the scratch CI tree; peer mode off).
# Each flow test runs one TCP flow dell1 -> dell2 wg0 for DUR s, fires the event at EV s and prints per-second
# throughput plus the wgfp_peer counters before/after. Env: DUR=20 EV=6
set -u
DUR=${DUR:-20}; EV=${EV:-6}
SSHO="-o BatchMode=yes -o ConnectTimeout=8"
dut(){ ssh -n $SSHO -i $HOME/.ssh/vyos_key vyos@192.168.1.185 "$1"; }
D1="ssh -n $SSHO admin@192.168.1.112"; D2="ssh -n $SSHO admin@192.168.1.113"
WP=/sys/module/wireguard/parameters/wgfp_peer; FE=/sys/kernel/debug/fman_pcd/0/fe_flow
st(){ dut "sudo cat $WP" | sed 's/ tmpl=[0-9]//'; }
fails=0; ok(){ echo "  PASS: $*"; }; bad(){ echo "  FAIL: $*"; fails=$((fails+1)); }

STOCK=${STOCK:-0}
on(){ dut "echo off | sudo tee $WP >/dev/null"; [ "$STOCK" = 1 ] && return
      dut "echo 'wg0 eth4' | sudo tee $WP >/dev/null" || { echo "ABORT: wgfp_peer"; exit 1; }; }
ka(){ $D2 "sudo wg set wg0 peer \$(sudo wg show wg0 peers) persistent-keepalive $1"; }
srv(){ $D2 'sudo pkill -f "^iperf3 -s -p 5241"; iperf3 -s -p 5241 -D'; }
# flow <name> <event command run with eval at EV>: prints intervals, leaves the iperf json in /tmp/p2b3.json on dell1
flow(){
  local cp=$((42000 + RANDOM % 8000))
  $D1 "iperf3 -c 10.99.200.113 -B 10.99.1.112 --cport $cp -p 5241 -t $DUR -i 1 ${FLOWOPT:-} -J > /tmp/p2b3.json 2>&1 &"
  sleep "$EV"; echo "  before: $(st)"; eval "$2"; sleep $((DUR - EV + 3)); echo "  after:  $(st)"
  IV=$($D1 "python3 -c \"import json;d=json.load(open('/tmp/p2b3.json'));print(' '.join('%.2f'%(i['sum']['bits_per_second']/1e9) for i in d['intervals']))\"" 2>/dev/null)
  echo "  Gbit/s per s: $IV"
  [ -n "$IV" ] || [ "${NOIV:-0}" = 1 ] || { bad "no iperf3 result: $($D1 "grep -o '\"error\":.*' /tmp/p2b3.json")"; return 1; }
}
# mean of the intervals from second $1 to $2 (1-based, inclusive)
iv_mean(){ echo "$IV" | awk -v a="$1" -v b="$2" '{s=0;n=0;for(i=a;i<=b&&i<=NF;i++){s+=$i;n++} printf "%.2f", n?s/n:0}'; }
cnt(){ dut "sudo cat $WP" | grep -o "$1=[0-9]*" | cut -d= -f2; }

t_rekey(){
  echo "== rekey: dell2 drops and re-adds the DUT peer at ${EV}s (fresh handshake, new keypair)"
  local pk aip
  pk=$($D2 'sudo wg show wg0 peers'); aip=$($D2 'sudo wg show wg0 allowed-ips' | cut -f2 | tr ' ' ',')
  $D2 "echo 'module wireguard +p' | sudo tee /sys/kernel/debug/dynamic_debug/control >/dev/null; sudo dmesg -C"
  on; srv
  ka 1
  flow rekey "$D2 \"sudo wg set wg0 peer $pk remove; sudo wg set wg0 peer $pk allowed-ips $aip endpoint 10.99.2.185:51820 persistent-keepalive 1\""
  local pre post im
  pre=$(iv_mean 2 $((EV-1))); post=$(iv_mean $((EV+6)) $((DUR-1)))
  im=$($D2 'sudo dmesg | grep -ciE "invalid mac|invalid nonce|unallowed src"')
  $D2 "echo 'module wireguard -p' | sudo tee /sys/kernel/debug/dynamic_debug/control >/dev/null"; ka 0
  echo "  mean before $pre, from $((EV+6))s $post Gbit/s; dell2 invalid-MAC/nonce/src lines: $im"
  awk -v a="$pre" -v b="$post" 'BEGIN{exit !(b >= 0.5*a)}' && ok "flow recovered after the new handshake" || bad "flow did not recover"
  [ "${im:-1}" = 0 ] && ok "no Invalid MAC on dell2" || bad "dell2 logged invalid packets"
}

t_roam(){
  echo "== roam: dell2 moves its listen port 51820 -> 51830 at ${EV}s (the DUT learns the new endpoint)"
  on; srv; ka 1
  flow roam "$D2 'sudo wg set wg0 listen-port 51830'"
  local ep pre post
  ep=$(dut 'sudo wg show wg0 endpoints' | cut -f2)
  pre=$(iv_mean 2 $((EV-1))); post=$(iv_mean $((EV+3)) $((DUR-1)))
  echo "  DUT endpoint now $ep; mean before $pre, from $((EV+3))s $post Gbit/s"
  [ "$ep" = 10.99.2.113:51830 ] && ok "endpoint roamed" || bad "endpoint $ep"
  awk -v a="$pre" -v b="$post" 'BEGIN{exit !(b >= 0.7*a)}' && ok "throughput held across the roam" || bad "throughput dropped"
  $D2 'sudo wg set wg0 listen-port 51820'; sleep 2; ka 0; dut 'ping -c 2 -W 1 10.99.200.113 >/dev/null' || true
}

t_rekey120(){
  echo "== rekey120: one 140 s flow across the REKEY_AFTER_TIME (120 s) rekey"
  local h0 h1 z
  on; srv; h0=$(dut 'sudo wg show wg0 latest-handshakes' | cut -f2)
  DUR=140 EV=2 flow rekey120 true
  h1=$(dut 'sudo wg show wg0 latest-handshakes' | cut -f2)
  z=$(echo "$IV" | awk '{n=0;for(i=3;i<NF;i++) if($i<0.05) n++; print n}')
  echo "  latest handshake $h0 -> $h1; seconds below 0.05 Gbit/s (excluding first 2 and last): $z"
  [ "$h1" -gt "$h0" ] && ok "rekeyed during the flow" || bad "no new handshake"
  [ "$z" = 0 ] && ok "no stall across the rekey" || bad "$z stalled seconds"
}

t_aip(){
  echo "== aip: a one-way UDP flow; the DUT peer loses 10.99.200.113 from AllowedIPs at ${EV}s, restored 6 s later"
  echo "   (TCP is useless here: the DUT drops dell2's ACKs by source AllowedIPs, so the flow stalls either way)"
  local pk w0 w1 i2
  pk=$(dut 'sudo wg show wg0 peers')
  on; srv; w0=$(cnt aip_withdraws)
  # iperf3 itself errors out (its control connection is cut with the peer), hence NOIV
  NOIV=1 FLOWOPT="-u -b 400M -l 1300" flow aip "dut 'sudo wg set wg0 peer $pk allowed-ips 10.99.200.250/32'; sleep 3; S1=\$(cnt sealed); sleep 2; S2=\$(cnt sealed); I1=\$(cnt installs); echo \"  mid:    \$(st)\"; dut 'sudo wg set wg0 peer $pk allowed-ips 10.99.200.113/32,fd99:200::113/128'"
  w1=$(cnt aip_withdraws); i2=$(cnt installs)
  echo "  aip_withdraws $w0 -> $w1; sealed in the 2 s window $((S2 - S1)); installs after restore $I1 -> $i2"
  [ "${w1:-0}" -gt "${w0:-0}" ] && ok "record withdrawn on the AllowedIPs change" || bad "no AllowedIPs withdraw"
  [ $((S2 - S1)) = 0 ] && ok "nothing sealed for the peer while not allowed" || bad "$((S2 - S1)) frames sealed while not allowed"
  [ "${i2:-0}" -gt "${I1:-0}" ] && ok "flow re-learned after the restore" || bad "flow not re-learned"
}

t_pmtu(){
  echo "== pmtu: a learned UDP flow sends DF packets larger than the wg0 MTU (1420): expect ICMP frag-needed"
  local d0 p0 i0 out
  on; d0=$(cnt decline_withdraws); p0=$(cnt punted); i0=$(cnt installs)
  $D2 "sudo pkill -f ^python3.*udpecho9100; nohup python3 -c \"
import socket,time # udpecho9100
s=socket.socket(socket.AF_INET,socket.SOCK_DGRAM); s.bind(('10.99.200.113',9100)); s.settimeout(30)
try:
  while True:
    d,a=s.recvfrom(4096); s.sendto(d[:64],a)
except Exception: pass\" >/dev/null 2>&1 &"
  sleep 1
  # dell1 pins the tunnel route MTU at 1420; lift it so the DF packet leaves at 1488
  $D1 'sudo ip route replace 10.99.200.0/24 via 10.99.1.185 mtu 1500; sudo ip route flush cache'
  out=$($D1 "python3 -c \"
import socket,time
s=socket.socket(socket.AF_INET,socket.SOCK_DGRAM); s.bind(('10.99.1.112',9101)); s.connect(('10.99.200.113',9100))
s.setsockopt(socket.IPPROTO_IP,10,2)   # IP_MTU_DISCOVER = IP_PMTUDISC_DO
s.settimeout(0.05); got=0
for i in range(400):
  s.send(b'x'*800); time.sleep(0.002)
  try: s.recv(100); got+=1
  except Exception: pass
err=''
for i in range(3):
  try: s.send(b'y'*1460)
  except OSError as e: err=e.strerror
  time.sleep(0.3)
print('echoes',got,'ip_mtu',s.getsockopt(socket.IPPROTO_IP,14),'err',err or '-')\"")
  $D1 'sudo ip route replace 10.99.200.0/24 via 10.99.1.185 mtu 1420'
  echo "  dell1: $out"; echo "  DUT:   $(st)"
  $D1 'sudo ip route flush cache'
  [ "$(cnt installs)" -gt "$i0" ] && ok "UDP flow learned and installed" || bad "UDP flow never installed"
  echo "$out" | grep -q 'ip_mtu 1420' && ok "PMTU learned from the punted packet's ICMP" || bad "no frag-needed (PMTU not 1420)"
  [ "$(cnt decline_withdraws)" -gt "$d0" ] && ok "oversize withdrew the record" || bad "no decline withdraw (flow learned?)"
  [ "$(cnt punted)" -gt "$p0" ] && ok "oversize frame punted" || bad "nothing punted"
}

t_del(){
  echo "== del: ip link del wg0 on the DUT while peer mode is bound"
  on; local t0 t1
  t0=$(date +%s); dut 'sudo timeout 30 ip link del wg0'; t1=$(date +%s); sleep 1
  echo "  delete took $((t1 - t0)) s; wgfp_peer: $(dut "sudo cat $WP")"
  dut 'sudo dmesg | grep -E "fastpath peer mode off|unregister_netdevice: waiting" | tail -3'
  [ $((t1 - t0)) -lt 10 ] && ok "wg0 deleted promptly" || bad "wg0 delete hung"
  [ "$(dut "sudo cat $WP")" = off ] && ok "peer mode stopped" || bad "peer mode still on"
  echo "  NOTE: re-run KEEP=1 bin/testrig-wireguard.sh setup wg-v4 for further tests"
}

t_netns(){
  echo "== netns: upstream tools/testing/selftests/wireguard/netns.sh on the DUT (peer mode off)"
  local s=${NETNS_SH:-/tmp/claude-1000/-mnt-builds-vyos-ls1046a-build/eacdbd34-26f9-4ee1-b5c2-f36d77998f9b/scratchpad/ci/tree/linux-6.18.60/tools/testing/selftests/wireguard/netns.sh}
  dut "echo off | sudo tee $WP >/dev/null"
  scp -q $SSHO -i $HOME/.ssh/vyos_key "$s" vyos@192.168.1.185:/tmp/netns.sh || { bad "copy netns.sh"; return; }
  dut 'sudo timeout 600 bash /tmp/netns.sh > /tmp/netns.log 2>&1; echo rc=$?; tail -5 /tmp/netns.log' | tee /dev/stderr | grep -q 'rc=0' \
    && ok "netns.sh passed" || bad "netns.sh failed (log /tmp/netns.log on the DUT)"
}

T="$*"; [ -z "$T" ] && T="rekey roam aip pmtu"
echo "DUT $(dut 'uname -v | cut -c1-40'); tests: $T"
for t in $T; do "t_$t"; done
dut "echo off | sudo tee $WP >/dev/null; sudo cat $FE 2>/dev/null | grep -c 'flow\[' | sed 's/^/fe_flow records after off: /'" 2>/dev/null
dut 'sudo dmesg --level=err,warn | grep -iE "bug|oops|warn|stall|lockup" | grep -v "callbacks suppressed" | tail -3'
echo "P2b-3 gate: $fails failure(s)"
exit $((fails > 0))
