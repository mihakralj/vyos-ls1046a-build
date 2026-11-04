#!/usr/bin/env bash
# testrig-wgfp-soak.sh — WireGuard fast path robustness gates (plans/ASK2-WIREGUARD-FASTPATH-PLAN.md, A3).
# Usage: bin/testrig-wgfp-soak.sh overload|soak
#   overload  P2a lab seal unpaced (sinks saturated: ~850k pkt/s offered, ~500k sealed) while dell1 pings dell2
#             through the same ingress port (eth3). Without sink tail drop the backlog drains eth3's buffer pool and
#             the unrelated ping loses (2026-11-02 baseline: 34% loss). Gate: ping loss <= 1%, eth3 pool recovers.
#             Leaves lab records in the ehash tables: reboot afterwards.
#   soak      4 automatic TCP flows for DUR (default 600 s) through a live tunnel (KEEP=1 bin/testrig-wireguard.sh
#             setup wg-v4). PORT_BASE selects the first of four server ports (default 5241).
#             Samples the BMan hardware pool counts, sink frm_cnt and TX failures every 30 s; checks
#             pcd-snapshot before binding vs after unbinding. The pool gate compares hardware drift with
#             software-accounted buffer consumption; sink queues and fe_flow must be empty afterwards.
#             Refuses kernels without the validated frm_cnt_encoding=host stats marker.
set -u
MODE=${1:-}; DUR=${DUR:-600}; PORT_BASE=${PORT_BASE:-5241}
SSHO="-o BatchMode=yes -o ConnectTimeout=8"
dut(){ ssh -n $SSHO -i $HOME/.ssh/vyos_key vyos@192.168.1.185 "$1"; }
D1="ssh -n $SSHO admin@192.168.1.112"; D2="ssh -n $SSHO admin@192.168.1.113"
WP=/sys/module/wireguard/parameters/wgfp_peer; WL=/sys/module/wireguard/parameters/wgfp_peer_lease
LEASE_TOKEN=${WG_LEASE_TOKEN:-$(cat "${WG_STATE_PATH:-/tmp/wg-rig}.single-lease/token" 2>/dev/null)}
DBG=/sys/kernel/debug/dpaa_wgfp
bp(){
  case "$1" in eth3|eth4) ;; *) echo "invalid pool interface: $1" >&2; return 2;; esac
  ssh $SSHO -i "$HOME/.ssh/vyos_key" vyos@192.168.1.185 sudo -n python3 - "$1" \
    < "$(dirname "$0")/bman-pool-count.py"
}
bp_accounted(){
  local stats count
  case "$1" in eth3|eth4) ;; *) echo "invalid pool interface: $1" >&2; return 2;; esac
  stats=$(dut "sudo /usr/sbin/ethtool -S $1") || return 1
  count=$(printf '%s\n' "$stats" | sed -n 's/^[[:space:]]*bpool \[TOTAL\]:[[:space:]]*\([0-9][0-9]*\)[[:space:]]*$/\1/p')
  [[ $count =~ ^[0-9]+$ ]] || { echo "missing $1 software pool accounting" >&2; return 1; }
  printf '%s\n' "$count"
}
pool_balanced(){
  local drift
  [[ $1 =~ ^[0-9]+:[0-9]+$ && $2 =~ ^[0-9]+$ &&
     $3 =~ ^[0-9]+:[0-9]+$ && $4 =~ ^[0-9]+$ ]] || return 1
  [[ ${1%%:*} = "${3%%:*}" ]] || return 1
  (( ${1#*:} > 0 && ${3#*:} > 0 )) || return 1
  drift=$(( (${4} - ${2}) - (${3#*:} - ${1#*:}) ))
  (( drift >= -64 && drift <= 64 ))
}
sink_count_verified(){
  printf '%s\n' "$1" | awk 'NR == 1 {
                             for (i=1; i<=NF; i++)
                               if ($i ~ /^frm_cnt_encoding=/) {
                                 found++
                                 if ($i == "frm_cnt_encoding=host") host++
                               }
                           }
                           END { exit !(found == 1 && host == 1) }'
}
header_counter(){
  printf '%s\n' "$1" | awk -v key="$2" '
    NR == 1 {
      for (i=1; i<=NF; i++)
        if ($i ~ ("^" key "=[0-9]+$")) { split($i, a, "="); value=a[2]; found++ }
    }
    END { if (found != 1) exit 1; print value }
  '
}
fails=0; ok(){ echo "  PASS: $*"; }; bad(){ echo "  FAIL: $*"; fails=$((fails+1)); }

case $MODE in
overload)
  b0=$(bp eth3) || { echo "ABORT: cannot read eth3 BMan pool" >&2; exit 1; }
  a0=$(bp_accounted eth3) || { echo "ABORT: cannot read eth3 pool accounting" >&2; exit 1; }
  PPS_LIST=0 SECS=20 TBLS=3 timeout 240 "$(dirname "$0")/testrig-wgfp-p2a.sh" > /tmp/wgfp-overload-p2a.log 2>&1 &
  p2a_pid=$!
  for _ in $(seq 1 60); do grep -q 'inserted 16 records' /tmp/wgfp-overload-p2a.log 2>/dev/null && break; sleep 1; done
  sleep 7
  b1=$(bp eth3) || { bad "cannot read eth3 BMan pool"; b1=unavailable; }
  out=$($D1 'ping -c 100 -i 0.1 -W 1 10.99.2.113 | grep -o "[0-9.]*% packet loss"')
  b2=$(bp eth3) || { bad "cannot read eth3 BMan pool"; b2=unavailable; }
  wait "$p2a_pid" || bad "overload generator failed"
  sleep 25; b3=$(bp eth3) || { bad "cannot read eth3 BMan pool"; b3=unavailable; }
  a3=$(bp_accounted eth3) || { bad "cannot read eth3 pool accounting"; a3=unavailable; }
  dut "sudo cat $DBG/stats | head -5"
  echo "  side-flow ping: $out; eth3 BMan BPID:free start $b0, saturated $b1 / $b2, after $b3; accounted $a0 -> $a3"
  loss=${out%%%*}
  awk -v l="${loss:-100}" 'BEGIN{exit !(l <= 1)}' && ok "unrelated traffic on the ingress port unaffected" || bad "side-flow loss ${loss}%"
  pool_balanced "$b0" "$a0" "$b3" "$a3" && ok "eth3 pool accounting balanced" || bad "eth3 BMan/accounting $b0/$a0 -> $b3/$a3"
  echo "  NOTE: reboot .185 (lab records stay in the ehash tables)"
  ;;
soak)
  [[ $DUR =~ ^[1-9][0-9]*$ ]] || { echo "ABORT: DUR must be a positive number of seconds" >&2; exit 1; }
  [[ $PORT_BASE =~ ^[1-9][0-9]{0,4}$ ]] && [ "$PORT_BASE" -le 65532 ] ||
    { echo "ABORT: PORT_BASE must be between 1 and 65532" >&2; exit 1; }
  ports=()
  for k in 0 1 2 3; do ports+=("$((PORT_BASE + k))"); done
  run_id="wgfp-soak-$(date +%s)-$$"
  snap="/tmp/$run_id-pcd.json"
  server_pids=(); client_pids=(); server_files=(); client_files=()
  bound=0; snap_owned=0
  stop_owned(){
    local side=$1 pid started port host
    IFS=: read -r pid started port <<< "$2"
    [[ $pid =~ ^[0-9]+$ && $started =~ ^[0-9]+$ && $port =~ ^[0-9]+$ && $pid -gt 1 ]] || return 1
    if [ "$side" = server ]; then host=admin@192.168.1.113; else host=admin@192.168.1.112; fi
    ssh $SSHO "$host" python3 - "$pid" "$started" "$side" "$port" \
      < "$(dirname "$0")/testrig-wgfp-stop.py"
  }
  owned_binding(){
    [[ $1 == on*" dev=wg0" ]]
  }
  unbind_owned(){
    local current
    current=$(dut "sudo cat $WP") || { echo "ERROR: cannot inspect fast-path binding" >&2; return 1; }
    if [ "$current" = off ]; then
      bound=0
    elif owned_binding "$current"; then
      dut "echo 'stop $LEASE_TOKEN' | sudo tee $WL >/dev/null" || return 1
      bound=0
    else
      echo "ERROR: fast-path binding changed; refusing to unbind it" >&2
      return 1
    fi
  }
  read_iperf_result(){
    ssh $SSHO admin@192.168.1.112 python3 - "$1" "$DUR" <<'PY'
import json
import math
import sys

try:
    with open(sys.argv[1]) as f:
        result = json.load(f)
    duration = float(sys.argv[2])
    tolerance = min(1.0, duration * 0.02)
    if result.get("error"):
        raise ValueError(result["error"])
    for summary in (result["end"]["sum_sent"], result["end"]["sum_received"]):
        seconds = float(summary["seconds"])
        rate = float(summary["bits_per_second"])
        if not math.isfinite(seconds) or seconds < duration - tolerance or \
           not math.isfinite(rate) or rate <= 0:
            raise ValueError("incomplete or empty transfer")

    intervals = result["intervals"]
    if not intervals:
        raise ValueError("no interval data")
    previous_end = 0.0
    for index, interval in enumerate(intervals):
        sample = interval["sum"]
        start, end, seconds, rate = (float(sample[key]) for key in
                                      ("start", "end", "seconds", "bits_per_second"))
        if not all(math.isfinite(value) for value in (start, end, seconds, rate)) or \
           sample.get("omitted", False) or abs(start - previous_end) > 0.05 or \
           seconds <= 0 or seconds > 1.5 or end <= start or \
           abs((end - start) - seconds) > 0.05 or \
           rate < 0 or (rate == 0 and
                        (seconds >= 0.5 or index != len(intervals) - 1)):
            raise ValueError("invalid or stalled interval")
        previous_end = end
    if previous_end < duration - tolerance:
        raise ValueError("interval data ends early")
    print(result["end"]["sum_sent"]["bits_per_second"])
except (OSError, KeyError, TypeError, ValueError) as err:
    print(f"invalid iperf result: {err}", file=sys.stderr)
    sys.exit(1)
PY
  }
  cleanup(){
    local rc=$? token p k
    trap - EXIT INT TERM
    for token in "${client_pids[@]}"; do stop_owned client "$token" || rc=1; done
    if [ "$bound" = 1 ]; then
      unbind_owned || { echo "ERROR: could not unbind owned fast path" >&2; rc=1; }
    fi
    for token in "${server_pids[@]}"; do stop_owned server "$token" || rc=1; done
    for p in "${server_files[@]}"; do
      $D2 "rm -f /tmp/$run_id-s$p.pid" || rc=1
    done
    for k in "${client_files[@]}"; do
      $D1 "rm -f /tmp/$run_id-c$k.json" || rc=1
    done
    if [ "$snap_owned" = 1 ]; then dut "sudo rm -f $snap" || rc=1; fi
    exit "$rc"
  }
  trap cleanup EXIT
  trap 'exit 130' INT
  trap 'exit 143' TERM
  for host in admin@192.168.1.112 admin@192.168.1.113; do
    ssh $SSHO "$host" "python3 -c 'import os,signal; fd=os.pidfd_open(os.getpid()); os.close(fd); assert hasattr(signal,\"pidfd_send_signal\")'" ||
      { echo "ABORT: generator lacks Python pidfd support" >&2; exit 1; }
  done
  [[ $LEASE_TOKEN =~ ^[0-9a-f]{32}$ ]] || { echo "ABORT: no wgfp lease token (run KEEP=1 bin/testrig-wireguard.sh setup wg-v4)" >&2; exit 1; }
  current=$(dut "sudo cat $WP") || { echo "ABORT: cannot read wgfp_peer" >&2; exit 1; }
  [ "$current" = off ] || { echo "ABORT: fast path already bound; refusing to replace it" >&2; exit 1; }
  stats=$(dut "sudo cat $DBG/stats") || { echo "ABORT: cannot read sink counters" >&2; exit 1; }
  sink_count_verified "$stats" ||
    { echo "ABORT: sink frm_cnt byte order is unverified; the kernel must advertise frm_cnt_encoding=host" >&2; exit 1; }
  log_start=$(dut 'date +%s') || { echo "ABORT: cannot read DUT time" >&2; exit 1; }
  [[ $log_start =~ ^[0-9]+$ ]] || { echo "ABORT: invalid DUT time" >&2; exit 1; }
  dut "test ! -e $snap" || { echo "ABORT: snapshot path already exists" >&2; exit 1; }
  snap_owned=1
  dut "sudo pcd-snapshot capture -o $snap >/dev/null" || { echo "ABORT: pcd-snapshot capture" >&2; exit 1; }
  e3=$(bp eth3) || { echo "ABORT: cannot read eth3 BMan pool" >&2; exit 1; }
  e4=$(bp eth4) || { echo "ABORT: cannot read eth4 BMan pool" >&2; exit 1; }
  [[ $e3 =~ ^[0-9]+:[0-9]+$ && $e4 =~ ^[0-9]+:[0-9]+$ ]] &&
    (( ${e3#*:} > 0 && ${e4#*:} > 0 )) ||
    { echo "ABORT: empty or invalid BMan pool" >&2; exit 1; }
  a3=$(bp_accounted eth3) || { echo "ABORT: cannot read eth3 pool accounting" >&2; exit 1; }
  a4=$(bp_accounted eth4) || { echo "ABORT: cannot read eth4 pool accounting" >&2; exit 1; }
  $D2 "for p in ${ports[*]}; do listeners=\$(ss -H -ltn \"sport = :\$p\") || exit 1; [ -z \"\$listeners\" ] || { echo \"port \$p already in use\" >&2; exit 1; }; done" ||
    { echo "ABORT: iperf server ports are unavailable" >&2; exit 1; }
  current=$(dut "sudo cat $WP") || { echo "ABORT: cannot recheck fast-path binding" >&2; exit 1; }
  [ "$current" = off ] || { echo "ABORT: fast path became bound before setup" >&2; exit 1; }
  bound=1
  dut "echo 'bind $LEASE_TOKEN wgo-$LEASE_TOKEN-dut' | sudo tee $WL >/dev/null" || { echo "ABORT: wgfp_peer_lease bind"; exit 1; }
  stats=$(dut "sudo cat $DBG/stats") || { echo "ABORT: cannot read sink counters" >&2; exit 1; }
  x0=$(header_counter "$stats" xmit_fail) || { echo "ABORT: missing or invalid xmit_fail counter" >&2; exit 1; }
  ern0=$(header_counter "$stats" xmit_ern) || { echo "ABORT: missing or invalid xmit_ern counter" >&2; exit 1; }
  peer0=$(dut "sudo cat $WP") || { echo "ABORT: cannot read fast-path counters" >&2; exit 1; }
  owned_binding "$peer0" || { echo "ABORT: fast-path binding changed after setup" >&2; exit 1; }
  sealed0=$(printf '%s\n' "$peer0" | sed -n 's/.*sealed=\([0-9]*\).*/\1/p')
  [[ $sealed0 =~ ^[0-9]+$ ]] || { echo "ABORT: missing sealed counter" >&2; exit 1; }
  for p in "${ports[@]}"; do
    pid=$($D2 "test ! -e /tmp/$run_id-s$p.pid && iperf3 -s -1 -p $p -D -I /tmp/$run_id-s$p.pid && cat /tmp/$run_id-s$p.pid") ||
      { echo "ABORT: could not start iperf server $p" >&2; exit 1; }
    server_files+=("$p")
    [[ $pid =~ ^[0-9]+$ && $pid -gt 1 ]] || { echo "ABORT: invalid server PID" >&2; exit 1; }
    started=$($D2 "awk '{print \$22}' /proc/$pid/stat") || { echo "ABORT: server $p exited" >&2; exit 1; }
    [[ $started =~ ^[0-9]+$ ]] || { echo "ABORT: invalid server start time" >&2; exit 1; }
    server_pids+=("$pid:$started:$p")
  done
  $D2 "for p in ${ports[*]}; do listeners=\$(ss -H -ltn \"sport = :\$p\") || exit 1; [ -n \"\$listeners\" ] || { echo \"server \$p did not listen\" >&2; exit 1; }; done" ||
    { echo "ABORT: an iperf server did not start" >&2; exit 1; }
  for k in 0 1 2 3; do
    $D1 "test ! -e /tmp/$run_id-c$k.json" || { echo "ABORT: client result path already exists" >&2; exit 1; }
    client_files+=("$k")
    pid=$($D1 "iperf3 -c 10.99.200.113 -B 10.99.1.112 --cport $((44000 + RANDOM % 4000 + k)) -p $((PORT_BASE+k)) -t $DUR -J > /tmp/$run_id-c$k.json 2>&1 < /dev/null & echo \$!") ||
      { echo "ABORT: could not start iperf client $k" >&2; exit 1; }
    [[ $pid =~ ^[0-9]+$ && $pid -gt 1 ]] || { echo "ABORT: invalid client PID" >&2; exit 1; }
    started=$($D1 "awk '{print \$22}' /proc/$pid/stat") || { echo "ABORT: client $k exited" >&2; exit 1; }
    [[ $started =~ ^[0-9]+$ ]] || { echo "ABORT: invalid client start time" >&2; exit 1; }
    client_pids+=("$pid:$started:$((PORT_BASE+k))")
  done
  start=$SECONDS; deadline=$((DUR < 30 ? DUR : 30)); last_sealed=$sealed0
  while :; do
    elapsed=$((SECONDS - start))
    if [ "$elapsed" -lt "$deadline" ]; then sleep "$((deadline - elapsed))"; fi
    e3sample=$(bp eth3) || { echo "ABORT: cannot read eth3 BMan pool" >&2; exit 1; }
    e4sample=$(bp eth4) || { echo "ABORT: cannot read eth4 BMan pool" >&2; exit 1; }
    [[ $e3sample =~ ^[0-9]+:[0-9]+$ && $e4sample =~ ^[0-9]+:[0-9]+$ ]] &&
      (( ${e3sample#*:} > 0 && ${e4sample#*:} > 0 )) ||
      { echo "ABORT: empty or invalid BMan pool" >&2; exit 1; }
    stats=$(dut "sudo cat $DBG/stats") || { bad "cannot read sink counters"; stats=unavailable; }
    peer=$(dut "sudo cat $WP") || { bad "cannot read fast-path counters"; peer=unavailable; }
    owned_binding "$peer" || { echo "ABORT: fast-path binding changed during soak" >&2; exit 1; }
    t=$((SECONDS - start))
    current_sealed=$(printf '%s\n' "$peer" | sed -n 's/.*sealed=\([0-9]*\).*/\1/p')
    if ! [[ $current_sealed =~ ^[0-9]+$ ]]; then
      bad "missing sealed counter at ${t}s"
    else
      if [ "$current_sealed" -lt "$last_sealed" ]; then
        bad "sealed counter regressed at ${t}s"
      elif [ "$t" -ge 20 ] && [ "$t" -lt "$((DUR - 5))" ] && [ "$current_sealed" -eq "$last_sealed" ]; then
        bad "fast path stopped sealing at ${t}s"
      fi
      last_sealed=$current_sealed
    fi
    echo "  t=${t}s BMan BPID:free eth3=$e3sample eth4=$e4sample $(printf '%s\n' "$stats" | grep -oE 'xmit_fail=[0-9]+|xmit_ern=[0-9]+' | tr '\n' ' ')frm_cnt=$(printf '%s\n' "$stats" | grep -o 'frm_cnt=[0-9]*' | cut -d= -f2 | tr '\n' ' ')$(printf '%s\n' "$peer" | grep -oE ' sealed=[0-9]+| punted=[0-9]+' | tr -d '\n')"
    if [ "$t" -ge "$DUR" ] || [ "$deadline" -eq "$DUR" ]; then break; fi
    deadline=$((deadline + 30 < DUR ? deadline + 30 : DUR))
  done
  sleep 8
  tot=0; for k in 0 1 2 3; do
    if ! b=$(read_iperf_result "/tmp/$run_id-c$k.json"); then
      bad "invalid iperf result for client $k"; b=0
    elif ! [[ $b =~ ^[0-9]+([.][0-9]+)?([eE][+-]?[0-9]+)?$ ]] ||
         ! awk -v b="$b" 'BEGIN{exit !(b > 0)}'; then
      bad "invalid iperf result for client $k: $b"; b=0
    fi
    tot=$(awk -v a="$tot" -v b="$b" 'BEGIN{print a+b}')
  done
  peer1=$(dut "sudo cat $WP") || { bad "cannot read fast-path counters"; peer1=; }
  owned_binding "$peer1" || { echo "ABORT: fast-path binding changed before teardown" >&2; exit 1; }
  sealed1=$(printf '%s\n' "$peer1" | sed -n 's/.*sealed=\([0-9]*\).*/\1/p')
  if ! unbind_owned; then bad "could not unbind owned fast path"; exit 1; fi
  sleep 2
  if flow_table=$(dut "sudo cat /sys/kernel/debug/fman_pcd/0/fe_flow"); then
    left=$(printf '%s\n' "$flow_table" | grep -c 'flow\[')
  else
    bad "cannot read flow table"; left=unavailable
  fi
  sleep 5
  final_stats=$(dut "sudo cat $DBG/stats") || { bad "cannot read final sink counters"; final_stats=; }
  xf=$(header_counter "$final_stats" xmit_fail) || xf=unavailable
  ernf=$(header_counter "$final_stats" xmit_ern) || ernf=unavailable
  e3b=$(bp eth3) || { bad "cannot read eth3 BMan pool"; e3b=unavailable; }
  e4b=$(bp eth4) || { bad "cannot read eth4 BMan pool"; e4b=unavailable; }
  a3b=$(bp_accounted eth3) || { bad "cannot read eth3 pool accounting"; a3b=unavailable; }
  a4b=$(bp_accounted eth4) || { bad "cannot read eth4 pool accounting"; a4b=unavailable; }
  if logs=$(dut "sudo journalctl -k --since '@$log_start' -p warning -o cat --no-pager"); then
    errs=$(printf '%s\n' "$logs" | grep -iE 'bug|oops|stall|lockup|refcount|(^|[^a-z])ern([^a-z]|$)|underflow' | grep -v 'callbacks suppressed' | head -3)
  else
    bad "cannot read kernel journal"
    errs="journal unavailable"
  fi
  diffout=$(dut "sudo pcd-snapshot diff $snap 2>&1; echo rc=\$?")
  echo "  mean up over ${DUR}s: $(awk -v t=$tot 'BEGIN{printf "%.3f", t/1e9}') Gbit/s; BMan BPID:free eth3 $e3 -> $e3b (accounted $a3 -> $a3b), eth4 $e4 -> $e4b (accounted $a4 -> $a4b); xmit_fail $xf; xmit_ern $ernf; fe_flow left $left"
  pool_balanced "$e3" "$a3" "$e3b" "$a3b" && pool_balanced "$e4" "$a4" "$e4b" "$a4b" &&
    ok "BMan pool drift matches software-accounted consumption" ||
    bad "BMan/accounting drift eth3 $e3/$a3->$e3b/$a3b eth4 $e4/$a4->$e4b/$a4b"
  [[ $xf =~ ^[0-9]+$ ]] && [ "$xf" = "$x0" ] && ok "no transmit failures" || bad "xmit_fail $x0 -> $xf"
  [[ $ernf =~ ^[0-9]+$ ]] && [ "$ernf" = "$ern0" ] && ok "no asynchronous enqueue rejections" || bad "xmit_ern $ern0 -> $ernf"
  [[ $sealed1 =~ ^[0-9]+$ ]] && [ "$sealed1" -gt "$sealed0" ] && ok "fast path sealed packets" || bad "fast path sealed no packets"
  [ "$left" = 0 ] && ok "every record withdrawn" || bad "$left records left"
  if sink_count_verified "$final_stats" && printf '%s\n' "$final_stats" | awk '
    $1 ~ /^sink[0-9]+$/ {
      sinks++; found=0; bytes=0
      for (i=1; i<=NF; i++) {
        if ($i ~ /^frm_cnt=[0-9]+$/) { split($i, a, "="); found=1; if (a[2] != 0) bad=1 }
        if ($i ~ /^byte_cnt=[0-9]+$/) { split($i, a, "="); bytes=1; if (a[2] != 0) bad=1 }
      }
      if (!found || !bytes) bad=1
    }
    END { exit !(sinks > 0 && !bad) }
  '; then ok "sink queues empty"; else bad "sink queues not empty or unavailable"; fi
  [ -z "$errs" ] && ok "no kernel errors" || { bad "kernel messages"; echo "$errs"; }
  echo "$diffout" | grep -q 'rc=0' && ok "pcd-snapshot diff clean" || { bad "pcd-snapshot diff"; echo "$diffout" | tail -8; }
  ;;
*) echo "usage: $0 overload|soak" >&2; exit 2 ;;
esac
echo "gate: $fails failure(s)"; exit $((fails > 0))
