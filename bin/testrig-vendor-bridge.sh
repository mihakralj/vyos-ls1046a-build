#!/usr/bin/env bash
# Measure an untagged DUT2 bridge with VLAN filtering and STP.
# BR_G{1,2}_IPV{4,6} are existing generator addresses; BR_PROBE{1,2} are
# unused IPv4 addresses on the corresponding physical segments.
set -Eeuo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
. "$HERE/testrig-env.sh"
if [ "${RIG_PARITY:-0}" = 1 ]; then . "$HERE/testrig-parity-profile.sh" || exit 2; fi
rig_require VENDOR_DUT GEN1 GEN2 GEN_IF_NS VENDOR_NS \
  BR_G1_IPV4 BR_G2_IPV4 BR_G1_IPV6 BR_G2_IPV6 BR_PROBE1 BR_PROBE2
if ! python3 - "$BR_G1_IPV4" "$BR_G2_IPV4" "$BR_G1_IPV6" "$BR_G2_IPV6" "$BR_PROBE1" "$BR_PROBE2" <<'PY'
import ipaddress, sys
try:
    addresses = [ipaddress.ip_address(x) for x in sys.argv[1:]]
except ValueError:
    raise SystemExit(1)
if ([ip.version for ip in addresses] != [4, 4, 6, 6, 4, 4] or
        len(set(addresses)) != 6 or any("%" in value for value in sys.argv[1:])):
    raise SystemExit(1)
PY
then
  echo "ERROR: bridge endpoint and ARP probe addresses must be distinct, valid and unscoped" >&2
  exit 2
fi

MODE=${1:-run}
case "$MODE" in run|smoke|preflight) [ "$#" -le 1 ] ;; *) false ;; esac ||
  { echo "usage: $0 [run|smoke|preflight]" >&2; exit 2; }
RUN=${OUTDIR:?set OUTDIR to the matrix result directory}
REPS=${REPS:-3}; DUR=${DUR:-30}; STEADY_FROM=${STEADY_FROM:-10}; STREAMS=${STREAMS:-8}
TEST_MTU=${BR_MTU:-1500}
case "$TEST_MTU" in 1500|9000) ;; *) echo "ERROR: BR_MTU must be 1500 or 9000" >&2; exit 2;; esac
for number in "$REPS" "$DUR" "$STEADY_FROM" "$STREAMS"; do
  [[ $number =~ ^[0-9]+$ ]] || { echo "ERROR: invalid benchmark parameter" >&2; exit 2; }
done
(( REPS > 0 && STREAMS > 0 && DUR > STEADY_FROM + 2 )) ||
  { echo "ERROR: invalid duration, steady window, or repetitions" >&2; exit 2; }
TOKEN=$(od -An -N16 -tx1 /dev/urandom | tr -d ' \n')
[[ $TOKEN =~ ^[0-9a-f]{32}$ ]] || { echo "ERROR: cannot generate owner token" >&2; exit 1; }
BR="brt${TOKEN:0:8}"
OWNER="ask2wan-${TOKEN:0:16}-br"
TEMP="wn${TOKEN:0:10}"
SERVER_DIR="/tmp/wgrig-srv-$TOKEN-br"
LEASE_ROOT="${XDG_STATE_HOME:-$HOME/.local/state}/testrig-dut2-bridge"
SSH=(ssh -o BatchMode=yes -o ConnectTimeout=8)
[ -z "$RIG_KEY" ] || SSH+=(-i "$RIG_KEY")
d(){ "${SSH[@]}" -n "$VENDOR_DUT" "$@"; }
d_in(){ "${SSH[@]}" "$VENDOR_DUT" "$@"; }
g(){
  local host=$1 command quoted
  shift
  command=$*
  printf -v quoted '%q' "$command"
  "${SSH[@]}" -n "$host" "sudo -n ip netns exec $VENDOR_NS sh -c $quoted"
}
g_in(){ local host=$1; shift; "${SSH[@]}" "$host" "sudo -n ip netns exec $VENDOR_NS $*"; }
say(){ printf '[%s] %s\n' "$(date +%T)" "$*"; }
fail(){ echo "ERROR: $*" >&2; return 1; }

if [ "${RIG_INHERIT_LOCK:-0}" = 1 ]; then
  [ "$(readlink -f /proc/$$/fd/9)" = /tmp/testrig-dut2-battery.lock ] ||
    { echo "ERROR: inherited DUT2 rig lock is missing" >&2; exit 1; }
else
  [ "$TEST_MTU" = 1500 ] || { echo "ERROR: jumbo bridge requires an owned MTU lease" >&2; exit 1; }
  exec 9>/tmp/testrig-dut2-battery.lock
fi
flock -n 9 || { echo "ERROR: another DUT2 benchmark owns the rig lock" >&2; exit 1; }
BOOT=$(d 'cat /proc/sys/kernel/random/boot_id')
[[ $BOOT =~ ^[0-9a-f-]{36}$ ]] || { echo "ERROR: cannot identify DUT boot" >&2; exit 1; }
if [ "$TEST_MTU" = 9000 ]; then
  [[ ${RIG_JUMBO_TOKEN:-} =~ ^[0-9a-f]{32}$ ]] ||
    { echo "ERROR: invalid jumbo lease token" >&2; exit 1; }
  [[ ${JUMBO_BPID:-} =~ ^[0-9]{1,2}$ && ${RIG_JUMBO_POOL_MIN:-} =~ ^[1-9][0-9]*$ ]] &&
    (( 10#$JUMBO_BPID < 64 )) ||
    { echo "ERROR: jumbo bridge requires a valid pool ID and safety floor" >&2; exit 1; }
  jumbo_lease="${XDG_STATE_HOME:-$HOME/.local/state}/testrig-dut2-jumbo/lease-$RIG_JUMBO_TOKEN/identity"
  [ -f "$jumbo_lease" ] &&
    grep -Fxq "token=$RIG_JUMBO_TOKEN" "$jumbo_lease" &&
    grep -Fxq "boot=$BOOT" "$jumbo_lease" ||
    { echo "ERROR: jumbo lease is absent or belongs to another boot" >&2; exit 1; }
fi
check_boot(){ [ "$(d 'cat /proc/sys/kernel/random/boot_id')" = "$BOOT" ] || fail "DUT rebooted"; }

empty_policy(){
  local host=$1 family=$2 rules routes
  rules=$(g "$host" "ip -$family rule show pref 10960") || return 1
  [ -z "$rules" ] || fail "generator policy priority 10960 is in use" || return 1
  routes=$(g "$host" "out=\$(ip -$family route show table 160 2>&1) ||
    case \$out in *'FIB table does not exist'*) out=;; *) echo \"\$out\" >&2; exit 1;; esac
    printf '%s' \"\$out\"") || return 1
  [ -z "$routes" ] || fail "generator route table 160 is in use"
}

preflight(){
  check_boot
  d "test ! -e /sys/class/net/$BR && test ! -e /sys/class/net/$TEMP &&
     test ! -L /sys/class/net/eth3/master && test ! -L /sys/class/net/eth4/master &&
     test \"\$(cat /sys/class/net/eth3/mtu)\" = $TEST_MTU &&
     test \"\$(cat /sys/class/net/eth4/mtu)\" = $TEST_MTU" ||
    fail "DUT bridge name, temporary link, port master, or MTU conflicts" || return 1
  d 'for f in /proc/sys/net/bridge/bridge-nf-call-iptables /proc/sys/net/bridge/bridge-nf-call-ip6tables; do
       if [ -e "$f" ] && [ "$(cat "$f")" != 0 ]; then exit 1; fi
     done' || fail "bridge netfilter is enabled" || return 1
  d 'nft list table inet ask_flowtable >/dev/null' || fail "vendor flowtable unavailable" || return 1
  local host family
  for host in "$GEN1" "$GEN2"; do
    g "$host" "test \"\$(cat /sys/class/net/$GEN_IF_NS/mtu)\" = $TEST_MTU &&
      command -v tcpdump >/dev/null && command -v iperf3 >/dev/null && command -v timeout >/dev/null" ||
      fail "generator MTU or required tools unavailable" || return 1
    for family in 4 6; do empty_policy "$host" "$family" || return 1; done
  done
  g "$GEN1" "test -z \"\$(ip -4 neigh show to $BR_PROBE1)\"" ||
    fail "generator1 topology probe address is cached" || return 1
  g "$GEN2" "test -z \"\$(ip -4 neigh show to $BR_PROBE2)\" &&
     test -z \"\$(ss -H -ltn 'sport = :5277')\" && test ! -e $SERVER_DIR" ||
    fail "generator2 topology probe or iperf port conflicts"
}

preflight
if [ "$MODE" = preflight ]; then say "Read-only bridge preflight passed"; exit 0; fi
mkdir -p "$RUN" "$LEASE_ROOT"
exec 8>"$LEASE_ROOT/.lock"
flock -n 8 || { echo "ERROR: another bridge run owns the lease lock" >&2; exit 1; }
stale=$(find "$LEASE_ROOT" -mindepth 1 -maxdepth 1 -type d -name 'lease-*' -print -quit)
[ -z "$stale" ] || { echo "ERROR: unreleased bridge lease $stale" >&2; exit 1; }
LEASE="$LEASE_ROOT/lease-$TOKEN"
mkdir "$LEASE"
printf 'token=%s\nboot=%s\nbridge=%s\n' "$TOKEN" "$BOOT" "$BR" > "$LEASE/identity"
: > "$LEASE/journal"
record(){ printf '%s\n' "$*" >> "$LEASE/journal"; }
bridge_state=none
port3_state=none
port4_state=none
g1v4=none; g1v6=none; g2v4=none; g2v6=none
server_state=none
clean=1

snapshot_dut(){
  d 'for i in eth3 eth4; do ip -o link show dev "$i"; ip -o -4 addr show dev "$i"; ip -o -6 addr show dev "$i"; done
     ip -d -o link show type vlan' | sort
}
snapshot_gen(){
  g "$1" "ip -o -4 addr show dev $GEN_IF_NS; ip -o -6 addr show dev $GEN_IF_NS" | sort
}
snapshot_flowtable(){
  d 'nft list table inet ask_flowtable' | sed -E 's/counter packets [0-9]+ bytes [0-9]+/counter/g'
}
route(){
  local host=$1 action=$2 source=$3 dest=$4
  g_in "$host" sh -s -- "$action" far 10960 160 "$source" "$dest" "$GEN_IF_NS" "$TEST_MTU" 160 < "$HERE/testrig-wg-route.sh"
}

cleanup(){
  local rc=$? ident pid started ports_ok=1 host family
  trap - EXIT INT TERM
  if [ "$server_state" != none ]; then
    if ident=$(g_in "$GEN2" python3 - identity "$SERVER_DIR" 5277 < "$HERE/testrig-wg-server.py") &&
       [[ $ident =~ ^[0-9]+:[0-9]+:5277$ ]]; then
      IFS=: read -r pid started _ <<< "$ident"
      g_in "$GEN2" python3 - "$pid" "$started" server 5277 < "$HERE/testrig-wgfp-stop.py" &&
        g_in "$GEN2" python3 - remove "$SERVER_DIR" 5277 < "$HERE/testrig-wg-server.py" ||
        { echo "ERROR: owned bridge iperf cleanup uncertain" >&2; clean=0; }
    elif ! g "$GEN2" "test ! -e $SERVER_DIR"; then
      echo "ERROR: bridge iperf identity uncertain" >&2; clean=0
    fi
  fi
  [ "$g2v6" = none ] || route "$GEN2" delete "$BR_G2_IPV6" "$BR_G1_IPV6/128" || clean=0
  [ "$g2v4" = none ] || route "$GEN2" delete "$BR_G2_IPV4" "$BR_G1_IPV4/32" || clean=0
  [ "$g1v6" = none ] || route "$GEN1" delete "$BR_G1_IPV6" "$BR_G2_IPV6/128" || clean=0
  [ "$g1v4" = none ] || route "$GEN1" delete "$BR_G1_IPV4" "$BR_G2_IPV4/32" || clean=0
  if [ "$port4_state" != none ]; then
    d_in sh -s -- leave eth4 "$BR" "$OWNER" < "$HERE/testrig-vendor-bridge-port.sh" ||
      { echo "ERROR: eth4 bridge cleanup uncertain" >&2; ports_ok=0; clean=0; }
  fi
  if [ "$port3_state" != none ]; then
    d_in sh -s -- leave eth3 "$BR" "$OWNER" < "$HERE/testrig-vendor-bridge-port.sh" ||
      { echo "ERROR: eth3 bridge cleanup uncertain" >&2; ports_ok=0; clean=0; }
  fi
  if [ "$bridge_state" != none ] && [ "$ports_ok" = 1 ]; then
    if d "test ! -e /sys/class/net/$BR || test -z \"\$(ls -A /sys/class/net/$BR/brif)\""; then
      d_in sh -s -- link-delete "$BR" "$OWNER" < "$HERE/testrig-wan-own.sh" ||
        { echo "ERROR: bridge link cleanup uncertain" >&2; clean=0; }
    else
      echo "ERROR: bridge still has foreign or unremoved members" >&2; clean=0
    fi
    d "test ! -e /sys/class/net/$TEMP" ||
      { echo "ERROR: unmarked temporary bridge remains" >&2; clean=0; }
  fi
  check_boot || clean=0
  snapshot_dut > "$LEASE/dut-after" && cmp -s "$LEASE/dut-before" "$LEASE/dut-after" ||
    { echo "ERROR: DUT link or address baseline changed" >&2; clean=0; }
  snapshot_gen "$GEN1" > "$LEASE/g1-after" && cmp -s "$LEASE/g1-before" "$LEASE/g1-after" ||
    { echo "ERROR: generator1 address baseline changed" >&2; clean=0; }
  snapshot_gen "$GEN2" > "$LEASE/g2-after" && cmp -s "$LEASE/g2-before" "$LEASE/g2-after" ||
    { echo "ERROR: generator2 address baseline changed" >&2; clean=0; }
  snapshot_flowtable > "$LEASE/flowtable-after" && cmp -s "$LEASE/flowtable-before" "$LEASE/flowtable-after" ||
    { echo "ERROR: vendor flowtable configuration changed" >&2; clean=0; }
  for host in "$GEN1" "$GEN2"; do
    for family in 4 6; do empty_policy "$host" "$family" || clean=0; done
  done
  if [ "$clean" = 1 ]; then
    g "$GEN1" "ping -c1 -W2 -I $BR_G1_IPV4 $BR_G2_IPV4 >/dev/null" &&
      g "$GEN1" "ping -c1 -W2 -I $BR_G1_IPV6 $BR_G2_IPV6 >/dev/null" &&
      g "$GEN2" "ping -c1 -W2 -I $BR_G2_IPV4 $BR_G1_IPV4 >/dev/null" &&
      g "$GEN2" "ping -c1 -W2 -I $BR_G2_IPV6 $BR_G1_IPV6 >/dev/null" ||
      { echo "ERROR: restored routed IPv4/IPv6 paths failed" >&2; clean=0; }
  fi
  if [ "$clean" = 1 ] &&
     [ "$(cat "$LEASE/identity")" = "$(printf 'token=%s\nboot=%s\nbridge=%s' "$TOKEN" "$BOOT" "$BR")" ]; then
    rm -f -- "$LEASE/identity" "$LEASE/journal" "$LEASE/dut-before" "$LEASE/dut-after" \
      "$LEASE/g1-before" "$LEASE/g1-after" "$LEASE/g2-before" "$LEASE/g2-after" \
      "$LEASE/flowtable-before" "$LEASE/flowtable-after" "$LEASE/g1-arp" "$LEASE/g2-arp" \
      "$LEASE/g1-tcpdump.err" "$LEASE/g2-tcpdump.err" "$LEASE/smoke.csv"
    rmdir "$LEASE" || { echo "ERROR: bridge lease not empty" >&2; clean=0; }
  else
    clean=0
  fi
  if [ "$clean" = 1 ]; then
    say "Bridge resources removed; addresses, VLANs, flowtable and route policies restored"
  else
    echo "ERROR: retained bridge ownership record $LEASE; inspect before recovery" >&2
    rc=1
  fi
  exit "$rc"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
snapshot_dut > "$LEASE/dut-before"
snapshot_gen "$GEN1" > "$LEASE/g1-before"
snapshot_gen "$GEN2" > "$LEASE/g2-before"
snapshot_flowtable > "$LEASE/flowtable-before"

probe_topology(){
  local mac1 mac2 p1 p2 s1=0 s2=0 captures_alive=1 probe_failed=0
  mac1=$(g "$GEN1" "cat /sys/class/net/$GEN_IF_NS/address")
  mac2=$(g "$GEN2" "cat /sys/class/net/$GEN_IF_NS/address")
  [[ $mac1 =~ ^([0-9a-f]{2}:){5}[0-9a-f]{2}$ && $mac2 =~ ^([0-9a-f]{2}:){5}[0-9a-f]{2}$ && $mac1 != "$mac2" ]] ||
    fail "invalid or duplicate generator MACs" || return 1
  MAC1=$mac1
  MAC2=$mac2
  g "$GEN1" "LC_ALL=C timeout 12 tcpdump -n -l -e -i $GEN_IF_NS 'arp and (ether src $mac1 or ether src $mac2)'" \
    > "$LEASE/g1-arp" 2> "$LEASE/g1-tcpdump.err" &
  p1=$!
  g "$GEN2" "LC_ALL=C timeout 12 tcpdump -n -l -e -i $GEN_IF_NS 'arp and (ether src $mac1 or ether src $mac2)'" \
    > "$LEASE/g2-arp" 2> "$LEASE/g2-tcpdump.err" &
  p2=$!
  sleep 2
  g "$GEN1" "ping -n -c1 -W1 -I $GEN_IF_NS $BR_PROBE1 >/dev/null 2>&1; s=\$?; [ \$s = 0 ] || [ \$s = 1 ]" || probe_failed=1
  g "$GEN2" "ping -n -c1 -W1 -I $GEN_IF_NS $BR_PROBE2 >/dev/null 2>&1; s=\$?; [ \$s = 0 ] || [ \$s = 1 ]" || probe_failed=1
  kill -0 "$p1" 2>/dev/null && kill -0 "$p2" 2>/dev/null || captures_alive=0
  wait "$p1" || s1=$?
  wait "$p2" || s2=$?
  [ "$probe_failed" = 0 ] || fail "ARP topology probe failed" || return 1
  [ "$captures_alive" = 1 ] && [ "$s1" = 124 ] && [ "$s2" = 124 ] ||
    fail "ARP capture ended early or failed" || return 1
  grep -Fxq '0 packets dropped by kernel' "$LEASE/g1-tcpdump.err" &&
    grep -Fxq '0 packets dropped by kernel' "$LEASE/g2-tcpdump.err" ||
    fail "ARP capture dropped packets or lacks drop statistics" || return 1
  grep -Fiq "$mac1" "$LEASE/g1-arp" && grep -Fiq "$mac2" "$LEASE/g2-arp" ||
    fail "ARP probes were not observed on both local segments" || return 1
  if grep -Fiq "$mac2" "$LEASE/g1-arp" || grep -Fiq "$mac1" "$LEASE/g2-arp"; then
    fail "generator segments already share an untagged L2 domain"; return 1
  fi
  say "ARP probes stayed on their own segments (complete captures, zero kernel drops); bridge uses VLAN filtering and STP"
}

verify_bridge(){
  d "ip -d -o link show dev $BR | grep -Fq 'altname $OWNER' &&
     test \"\$(cat /sys/class/net/$BR/bridge/vlan_filtering)\" = 1 &&
     test \"\$(cat /sys/class/net/$BR/bridge/stp_state)\" = 1 &&
     test \"\$(cat /sys/class/net/$BR/mtu)\" = $TEST_MTU &&
     test \"\$(cat /sys/class/net/eth3/mtu)\" = $TEST_MTU &&
     test \"\$(cat /sys/class/net/eth4/mtu)\" = $TEST_MTU &&
     test \"\$(cat /sys/class/net/eth3/brport/state)\" = 3 &&
     test \"\$(cat /sys/class/net/eth4/brport/state)\" = 3 &&
     ip -o link show dev eth3 | grep -Fq 'master $BR ' &&
     ip -o link show dev eth4 | grep -Fq 'master $BR ' &&
     for f in /proc/sys/net/bridge/bridge-nf-call-iptables /proc/sys/net/bridge/bridge-nf-call-ip6tables; do
       if [ -e \"\$f\" ] && [ \"\$(cat \"\$f\")\" != 0 ]; then exit 1; fi
     done" ||
    { fail "bridge ownership, isolation, or forwarding state changed"; return 1; }
  check_boot || return 1
  if [ "$TEST_MTU" = 9000 ]; then
    d "awk -F: '
      \$1==\"fatal\" || \$1==\"quarantine\" || \$1==\"owed\" || \$1==\"resume_failures\" {
        gsub(/[[:space:]]/, \"\", \$2); if (\$2 != \"0\") bad=1; n++
      }
      END {exit (bad || n != 4)}
      ' /proc/ask_flowtable/status || exit 1
      set -- /sys/bus/platform/devices/*.bman/pool_count/$JUMBO_BPID
      [ \"\$#\" -eq 1 ] && [ -r \"\$1\" ] &&
        count=\$(cat \"\$1\") && [ \"\$count\" -ge $RIG_JUMBO_POOL_MIN ]" ||
      { fail "jumbo bridge flowtable or buffer pool health changed"; return 1; }
  fi
}

probe_topology
record "bridge $BR $OWNER"
bridge_state=pending
d_in sh -s -- link-create "$BR" "$OWNER" type bridge stp_state 1 vlan_filtering 1 < "$HERE/testrig-wan-own.sh"
bridge_state=owned
d "ip link set dev $BR up"
record "member eth3"
port3_state=pending
d_in sh -s -- join eth3 "$BR" "$OWNER" < "$HERE/testrig-vendor-bridge-port.sh"
port3_state=owned
record "member eth4"
port4_state=pending
d_in sh -s -- join eth4 "$BR" "$OWNER" < "$HERE/testrig-vendor-bridge-port.sh"
port4_state=owned
[ "$TEST_MTU" = 1500 ] || d "ip link set dev $BR mtu $TEST_MTU"
sleep 35
verify_bridge

record "generator1-policy-v4"
g1v4=pending; route "$GEN1" add "$BR_G1_IPV4" "$BR_G2_IPV4/32"; g1v4=owned
record "generator1-policy-v6"
g1v6=pending; route "$GEN1" add "$BR_G1_IPV6" "$BR_G2_IPV6/128"; g1v6=owned
record "generator2-policy-v4"
g2v4=pending; route "$GEN2" add "$BR_G2_IPV4" "$BR_G1_IPV4/32"; g2v4=owned
record "generator2-policy-v6"
g2v6=pending; route "$GEN2" add "$BR_G2_IPV6" "$BR_G1_IPV6/128"; g2v6=owned
g "$GEN1" "ping -c 2 -W 2 -I $BR_G1_IPV4 $BR_G2_IPV4"
g "$GEN1" "ping -c 2 -W 2 -I $BR_G1_IPV6 $BR_G2_IPV6"
g "$GEN2" "ping -c 2 -W 2 -I $BR_G2_IPV4 $BR_G1_IPV4"
g "$GEN2" "ping -c 2 -W 2 -I $BR_G2_IPV6 $BR_G1_IPV6"
if [ "$TEST_MTU" = 9000 ]; then
  g "$GEN1" "ping -4 -M do -s 8972 -c 2 -W 2 -I $BR_G1_IPV4 $BR_G2_IPV4"
  g "$GEN2" "ping -4 -M do -s 8972 -c 2 -W 2 -I $BR_G2_IPV4 $BR_G1_IPV4"
  g "$GEN1" "ping -6 -M do -s 8952 -c 2 -W 2 -I $BR_G1_IPV6 $BR_G2_IPV6"
  g "$GEN2" "ping -6 -M do -s 8952 -c 2 -W 2 -I $BR_G2_IPV6 $BR_G1_IPV6"
fi
verify_bridge

record "iperf $SERVER_DIR 5277"
server_state=pending
g_in "$GEN2" python3 - start "$SERVER_DIR" 5277 < "$HERE/testrig-wg-server.py"
g_in "$GEN2" python3 - identity "$SERVER_DIR" 5277 < "$HERE/testrig-wg-server.py"
server_state=owned
if [ "$MODE" = smoke ]; then
  DUR=8; STEADY_FROM=2; REPS=1
  OUT="$LEASE/smoke.csv"
else
  OUT="$RUN/bridge-$TEST_MTU-$TOKEN.csv"
fi
JSON="$RUN/json-bridge-$TOKEN"
mkdir "$JSON"
printf 'ts,mtu,fam,dir,rep,gbps,dut_busy,l2_hw_packets\n' > "$OUT"
for family in 4 6; do
  if [ "$family" = 4 ]; then source=$BR_G1_IPV4; dest=$BR_G2_IPV4
  else source=$BR_G1_IPV6; dest=$BR_G2_IPV6; fi
  for direction in up down bidir; do
    flag=''
    case "$direction" in down) flag=-R;; bidir) flag=--bidir;; esac
    for (( rep=1; rep<=REPS; rep++ )); do
      verify_bridge
      sample="$JSON/v$family-$direction-r$rep.dut"
      json="$JSON/v$family-$direction-r$rep.json"
      d "l2(){ echo L2_BEGIN; if [ -r /proc/ask_flowtable/l2 ]; then cat /proc/ask_flowtable/l2; else echo L2_UNAVAILABLE; fi; echo L2_END; }
         sleep $STEADY_FROM; head -1 /proc/stat; for i in eth3 eth4; do echo RX_\$i \$(cat /sys/class/net/\$i/statistics/rx_packets); done; l2
         sleep $((DUR-STEADY_FROM-2)); head -1 /proc/stat; for i in eth3 eth4; do echo RX_\$i \$(cat /sys/class/net/\$i/statistics/rx_packets); done; l2" > "$sample" &
      sampler=$!
      client_status=0
      g "$GEN1" "timeout --signal=TERM --kill-after=5s $((DUR+45))s iperf3 -c $dest -B $source -p 5277 -P $STREAMS -t $DUR -i 1 -Z ${IPERF_ARGS:-} $flag -J" > "$json" || client_status=$?
      wait "$sampler" || fail "DUT sampler failed"
      [ "$client_status" = 0 ] || { fail "iperf3 failed with status $client_status"; exit 1; }
      read -r gbps busy hw_packets < <(PYTHONPATH="$HERE${PYTHONPATH:+:$PYTHONPATH}" python3 - "$json" "$sample" "$STEADY_FROM" "$BR" "$MAC1" "$MAC2" "$family" "$TEST_MTU" \
        "$DUR" "$STREAMS" "$direction" "$source" "$dest" 5277 "${MEASURE_TO:-$((DUR-1))}" <<'PY'
import ipaddress, json, sys
from testrig_iperf import measure_tcp, report_json
with open(sys.argv[1]) as source:
    data = json.load(source)
if data.get("error"):
    raise SystemExit(data["error"])
if sys.argv[8] == "9000":
    start = data.get("start", {})
    mss = start.get("tcp_mss_default")
    if type(mss) is not int or mss < 8800:
        raise SystemExit("jumbo bridge result lacks a jumbo TCP MSS")
    duration, streams = map(int, sys.argv[9:11])
    direction = sys.argv[11]
    bidir = direction == "bidir"
    test = start.get("test_start", {})
    if (direction not in ("up", "down", "bidir") or test.get("protocol") != "TCP" or
            test.get("duration") != duration or test.get("num_streams") != streams or
            test.get("reverse", 0) != int(direction == "down") or
            test.get("bidir", 0) != int(bidir)):
        raise SystemExit("jumbo bridge iperf parameters do not match the requested trial")
    try:
        expected_source, expected_dest = map(ipaddress.ip_address, sys.argv[12:14])
        connections = start.get("connected", [])
        valid_connections = all(
            ipaddress.ip_address(c.get("local_host")) == expected_source and
            ipaddress.ip_address(c.get("remote_host")) == expected_dest and
            c.get("remote_port") == int(sys.argv[14])
            for c in connections
        )
    except ValueError:
        raise SystemExit("invalid jumbo bridge endpoint")
    if len(connections) != streams * (2 if bidir else 1) or not valid_connections:
        raise SystemExit("jumbo bridge connections do not match test endpoints")
report = measure_tcp(data, int(sys.argv[9]), float(sys.argv[3]), float(sys.argv[15]),
                     direction=sys.argv[11], streams=int(sys.argv[10]))
report_json(sys.argv[1], report)
rate = report["gbps"]
cpus, rx, l2, current = [], {}, [], None
with open(sys.argv[2]) as source:
    for line in source:
        fields = line.split()
        if not fields:
            continue
        if fields[0] == "L2_BEGIN":
            current = {}
            l2.append(current)
        elif fields[0] == "L2_UNAVAILABLE":
            l2[-1] = None
        elif fields[0] == "l2flow" and current is not None:
            values = dict(x.split("=", 1) for x in fields[1:] if "=" in x)
            if (values.get("br") == sys.argv[4] and
                    {values.get("src"), values.get("dst")} == {sys.argv[5], sys.argv[6]} and
                    values.get("type") == ("0x0800" if sys.argv[7] == "4" else "0x86dd")):
                key = (values["src"], values["dst"], values["port_in"], values["port_out"])
                current[key] = int(values["packets"])
        elif fields[0] == "L2_END":
            current = None
        elif fields[0] == "cpu":
            cpus.append([int(x) for x in fields[1:11]])
        elif fields[0].startswith("RX_"):
            rx.setdefault(fields[0], []).append(int(fields[1]))
if len(cpus) != 2 or any(len(rx.get(key, ())) != 2 or rx[key][1] <= rx[key][0]
                         for key in ("RX_eth3", "RX_eth4")):
    raise SystemExit("DUT port counters did not confirm traffic through both ports")
delta = [b-a for a, b in zip(*cpus)]
total = sum(delta)
if total <= 0:
    raise SystemExit("invalid DUT CPU sample")
busy = 100 * (total - delta[3] - delta[4]) / total
hw = -1
if len(l2) == 2 and all(snapshot is not None for snapshot in l2):
    hw = sum(max(0, value - l2[0].get(key, 0)) for key, value in l2[1].items())
print(f"{rate:.3f} {busy:.1f} {hw}")
PY
      ) || { fail "invalid bridge result"; exit 1; }
      verify_bridge
      printf 'bridge v%s %-5s r%s %7s Gbit/s  DUT busy %5s%%  L2 packets %s\n' "$family" "$direction" "$rep" "$gbps" "$busy" "$hw_packets"
      printf '%s,%s,%s,%s,%s,%s,%s,%s\n' "$(date +%H:%M:%S)" "$TEST_MTU" "$family" "$direction" "$rep" "$gbps" "$busy" "$hw_packets" >> "$OUT"
    done
  done
done
say "Bridge results: $OUT"
