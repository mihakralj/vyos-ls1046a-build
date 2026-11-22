#!/usr/bin/env bash
# Benchmark a vendor DUT at MTU 9000 without owning its shared flowtable.
set -Eeuo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
. "$HERE/testrig-env.sh"
if [ "${RIG_PARITY:-0}" = 1 ]; then . "$HERE/testrig-parity-profile.sh" || exit 2; fi
rig_require VENDOR_DUT GEN1 GEN2 GEN_IF_NS VENDOR_NS \
  BR_G1_IPV4 BR_G2_IPV4 BR_G1_IPV6 BR_G2_IPV6 \
  JUMBO_G1_VLAN4 JUMBO_G2_VLAN4 JUMBO_G1_VLAN6 JUMBO_G2_VLAN6 JUMBO_BPID

MODE=${1:-run}
case "$MODE" in preflight|preflight-routed|smoke|run|run-routed) [ "$#" -le 1 ] ;; *) false ;; esac ||
  { echo "usage: $0 [preflight|preflight-routed|smoke|run|run-routed]" >&2; exit 2; }
if [ -n "${RIG_JUMBO_ROW:-}" ]; then
  case "$MODE:$RIG_JUMBO_ROW" in
    run-routed:unicast-9k|run-routed:nat-9k|run-routed:vlan-9k|run-routed:vlan-port-9k) ;;
    *) echo "ERROR: RIG_JUMBO_ROW requires run-routed and a supported routed row" >&2; exit 2 ;;
  esac
fi
if [ -n "${RIG_JUMBO_SNAPSHOT_DIR:-}" ]; then
  [[ $MODE = run-routed && -n ${RIG_JUMBO_ROW:-} && ${RIG_PARITY:-0} = 1 &&
     $RIG_JUMBO_SNAPSHOT_DIR = /* && -d $RIG_JUMBO_SNAPSHOT_DIR && ! -L $RIG_JUMBO_SNAPSHOT_DIR ]] ||
    { echo "ERROR: jumbo snapshots require a paired routed row and an existing absolute directory" >&2; exit 2; }
fi
if [ -n "${RIG_JUMBO_PAIR_DIR:-}${RIG_JUMBO_PAIR_REP:-}" ]; then
  [[ ${RIG_JUMBO_PAIR_DIR:-} = /* && -d $RIG_JUMBO_PAIR_DIR && ! -L $RIG_JUMBO_PAIR_DIR &&
     ${RIG_JUMBO_PAIR_REP:-} =~ ^[123]$ &&
     ${RIG_JUMBO_SNAPSHOT_DIR:-} = "$RIG_JUMBO_PAIR_DIR/vendor-${RIG_JUMBO_ROW:-}-r$RIG_JUMBO_PAIR_REP-snapshot" ]] ||
    { echo "ERROR: invalid paired jumbo snapshot group" >&2; exit 2; }
fi
case "$MODE" in
  run-routed|preflight-routed) ;;
  *) rig_require BR_PROBE1 BR_PROBE2 ;;
esac
OUTDIR=${OUTDIR:?set OUTDIR to the matrix result directory}
[[ $OUTDIR = /* ]] || { echo "ERROR: OUTDIR must be absolute" >&2; exit 2; }
[[ $GEN_IF_NS =~ ^[A-Za-z0-9][A-Za-z0-9_.-]{0,11}$ &&
   $VENDOR_NS =~ ^[A-Za-z0-9][A-Za-z0-9_.-]*$ ]] ||
  { echo "ERROR: invalid generator interface or namespace" >&2; exit 2; }
[[ $JUMBO_BPID =~ ^[0-9]{1,2}$ ]] && (( 10#$JUMBO_BPID < 64 )) ||
  { echo "ERROR: JUMBO_BPID must be a buffer pool ID from 0 to 63" >&2; exit 2; }
probe_args=()
case "$MODE" in
  run-routed|preflight-routed) ;;
  *) probe_args=("$BR_PROBE1" "$BR_PROBE2") ;;
esac
if ! python3 - "$BR_G1_IPV4" "$BR_G2_IPV4" "$BR_G1_IPV6" "$BR_G2_IPV6" \
  "$JUMBO_G1_VLAN4" "$JUMBO_G2_VLAN4" "$JUMBO_G1_VLAN6" "$JUMBO_G2_VLAN6" \
  "${probe_args[@]}" <<'PY'
import ipaddress, sys
try:
    addresses = [ipaddress.ip_address(value) for value in sys.argv[1:]]
except ValueError:
    raise SystemExit(1)
versions = [4, 4, 6, 6, 4, 4, 6, 6]
if len(addresses) == 10:
    versions += [4, 4]
if ([ip.version for ip in addresses] != versions or
        len(set(addresses)) != len(addresses) or
        any("%" in text for text in sys.argv[1:]) or
        any(str(ip) != text.lower() for ip, text in zip(addresses, sys.argv[1:]))):
    raise SystemExit(1)
PY
then
  echo "ERROR: benchmark endpoints must be distinct, valid, unscoped, canonical IP addresses" >&2
  exit 2
fi

REPS=${REPS:-3}; DUR=${DUR:-30}; STEADY_FROM=${STEADY_FROM:-10}; STREAMS=${STREAMS:-8}
for number in "$REPS" "$DUR" "$STEADY_FROM" "$STREAMS"; do
  [[ $number =~ ^[0-9]+$ ]] || { echo "ERROR: invalid benchmark parameter" >&2; exit 2; }
done
(( REPS > 0 && STREAMS > 0 && DUR > STEADY_FROM + 2 )) ||
  { echo "ERROR: invalid duration, steady window, or repetitions" >&2; exit 2; }
if [ "$MODE" = smoke ]; then REPS=1; DUR=8; STEADY_FROM=2; fi

TOKEN=$(od -An -N16 -tx1 /dev/urandom | tr -d ' \n')
[[ $TOKEN =~ ^[0-9a-f]{32}$ ]] || { echo "ERROR: cannot generate owner token" >&2; exit 1; }
PORT=5278
SSH=(ssh -o BatchMode=yes -o ConnectTimeout=8 -o ServerAliveInterval=5 -o ServerAliveCountMax=2)
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
remote(){
  local target=$1
  shift
  case "$target" in dut) d "$@" ;; g1) g "$GEN1" "$@" ;; g2) g "$GEN2" "$@" ;; esac
}
say(){ printf '[%s] %s\n' "$(date +%T)" "$*"; }
fail(){ echo "ERROR: $*" >&2; return 1; }

case "${RIG_INHERIT_LOCK:-0}" in
  1)
    [[ -e /proc/self/fd/9 && /tmp/testrig-dut2-battery.lock -ef /proc/self/fd/9 ]] ||
      { echo "ERROR: inherited DUT2 battery lock is missing or incorrect" >&2; exit 1; }
    ;;
  0) exec 9>>/tmp/testrig-dut2-battery.lock ;;
  *) echo "ERROR: invalid RIG_INHERIT_LOCK" >&2; exit 2 ;;
esac
flock -n 9 || { echo "ERROR: another DUT2 benchmark owns the rig lock" >&2; exit 1; }
LEASE_ROOT="${XDG_STATE_HOME:-$HOME/.local/state}/testrig-dut2-jumbo"
BRIDGE_ROOT="${XDG_STATE_HOME:-$HOME/.local/state}/testrig-dut2-bridge"
WAN_ROOT="${XDG_STATE_HOME:-$HOME/.local/state}/testrig-dut2-wan"
mkdir -p "$LEASE_ROOT"
exec 8>>"$LEASE_ROOT/.lock"
flock -n 8 || { echo "ERROR: another jumbo run owns the lease lock" >&2; exit 1; }
no_leases(){
  local root found
  for root in "$LEASE_ROOT" "$BRIDGE_ROOT" "$WAN_ROOT"; do
    [ -d "$root" ] || continue
    found=$(find "$root" -mindepth 1 -maxdepth 1 -type d -name 'lease-*' -print -quit) || return 1
    [ -z "$found" ] || { fail "unreleased benchmark lease $found"; return 1; }
  done
}
no_bridge_lease(){
  local found
  [ -d "$BRIDGE_ROOT" ] || return 0
  found=$(find "$BRIDGE_ROOT" -mindepth 1 -maxdepth 1 -type d -name 'lease-*' -print -quit) || return 1
  [ -z "$found" ] || fail "bridge lease remains: $found"
}

BOOT=$(d 'cat /proc/sys/kernel/random/boot_id')
[[ $BOOT =~ ^[0-9a-f-]{36}$ ]] || { echo "ERROR: cannot identify DUT boot" >&2; exit 1; }
check_boot(){ [ "$(d 'cat /proc/sys/kernel/random/boot_id')" = "$BOOT" ] || fail "DUT rebooted"; }
pool_count(){
  local dev bpid
  for dev in eth3 eth4; do
    bpid=$(d "cat /sys/class/net/$dev/bpids") ||
      { fail "cannot read $dev BPID"; return 1; }
    [[ $bpid =~ ^[0-9]{1,2}$ ]] && (( 10#$bpid == 10#$JUMBO_BPID )) ||
      { fail "$dev BPID does not match the selected pool"; return 1; }
  done
  d "set -- /sys/bus/platform/devices/*.bman/pool_count/$JUMBO_BPID
     [ \"\$#\" -eq 1 ] && [ -r \"\$1\" ] && cat \"\$1\""
}
check_health(){
  local count
  check_boot || return 1
  d "awk -F: '
    \$1==\"fatal\" || \$1==\"quarantine\" || \$1==\"owed\" || \$1==\"resume_failures\" {
      gsub(/[[:space:]]/, \"\", \$2); if (\$2 != \"0\") bad=1; n++
    }
    END {exit (bad || n != 4)}
    ' /proc/ask_flowtable/status && nft list table inet ask_flowtable >/dev/null" ||
    { fail "vendor flowtable health is uncertain"; return 1; }
  count=$(pool_count) || { fail "cannot read BMan pool"; return 1; }
  [[ $count =~ ^[0-9]+$ ]] && (( count >= POOL_MIN )) ||
    { fail "BMan pool below safety floor ($count < $POOL_MIN)"; return 1; }
}

# Parent links precede their VLANs on setup; teardown uses the reverse dependency order.
targets=(dut dut dut dut g1 g1 g2 g2)
devs=(eth3 eth4 eth3.10 eth4.20 "$GEN_IF_NS" "$GEN_IF_NS.10" "$GEN_IF_NS" "$GEN_IF_NS.20")
suffixes=(d3 d4 d3v d4v g1 g1v g2 g2v)
indexes=(); links=(); macs=(); owners=()
link_info(){
  local i=$1 dev=${devs[$1]}
  remote "${targets[$i]}" "printf '%s %s %s %s\\n' \
    \"\$(cat /sys/class/net/$dev/ifindex)\" \"\$(cat /sys/class/net/$dev/iflink)\" \
    \"\$(cat /sys/class/net/$dev/address)\" \"\$(cat /sys/class/net/$dev/mtu)\""
}
link_op(){
  local op=$1 i=$2 before=$3 after=$4
  case ${targets[$i]} in
    dut) d_in sh -s -- "$op" "${devs[$i]}" "${indexes[$i]}" "${links[$i]}" \
           "${macs[$i]}" "${owners[$i]}" "$before" "$after" < "$HERE/testrig-vendor-mtu.sh" ;;
    g1) g_in "$GEN1" sh -s -- "$op" "${devs[$i]}" "${indexes[$i]}" "${links[$i]}" \
           "${macs[$i]}" "${owners[$i]}" "$before" "$after" < "$HERE/testrig-vendor-mtu.sh" ;;
    g2) g_in "$GEN2" sh -s -- "$op" "${devs[$i]}" "${indexes[$i]}" "${links[$i]}" \
           "${macs[$i]}" "${owners[$i]}" "$before" "$after" < "$HERE/testrig-vendor-mtu.sh" ;;
  esac
}
no_foreign_children(){
  local i=$1 child listing
  case "$i" in 0) child=${devs[2]} ;; 1) child=${devs[3]} ;;
    4) child=${devs[5]} ;; 6) child=${devs[7]} ;; *) return 1 ;; esac
  listing=$(remote "${targets[$i]}" "ip -o link show") || return 1
  if ! printf '%s\n' "$listing" | awk -v parent="${devs[$i]}" -v child="$child" '
      {split($2, a, "@"); sub(/:$/, "", a[2]); if (a[2]==parent && a[1]!=child) bad=1}
      END {exit bad}'; then
    fail "unexpected dependent link on ${targets[$i]}:${devs[$i]}"
    return 1
  fi
}
check_addresses(){
  local host=$1 dev=$2 family=$3 address=$4 output
  output=$(g "$host" "ip -o -$family addr show dev $dev") || return 1
  printf '%s\n' "$output" | awk -v address="$address" '
    $3=="inet" || $3=="inet6" {split($4, a, "/"); if (a[1]==address) found=1}
    END {exit !found}' || fail "generator endpoint $address is not on $dev"
}
no_test_sockets(){
  local host
  for host in "$GEN1" "$GEN2"; do
    g "$host" "test -z \"\$(ss -H -ltn 'sport = :$PORT')\" &&
      test -z \"\$(ss -H -tn '( sport = :$PORT or dport = :$PORT )')\" &&
      test -z \"\$(ss -H -ltn 'sport = :5277')\" &&
      test -z \"\$(ss -H -tn '( sport = :5277 or dport = :5277 )')\"" ||
      { fail "benchmark sockets remain on a generator"; return 1; }
  done
}
snapshot(){
  local target=$1 devices command
  case "$target" in
    dut) devices="eth3 eth4 eth3.10 eth4.20" ;;
    g1) devices="$GEN_IF_NS $GEN_IF_NS.10" ;;
    g2) devices="$GEN_IF_NS $GEN_IF_NS.20" ;;
  esac
  command="for i in $devices; do
      ip -d -o link show dev \"\$i\" || exit 1
      ip -o -4 addr show dev \"\$i\" || exit 1
      ip -o -6 addr show dev \"\$i\" || exit 1
    done
    for family in 4 6; do
      ip -o -\$family route show table all || exit 1
      ip -o -\$family rule show || exit 1
    done"
  if [ "$target" != dut ]; then
    command="$command
      for f in /sys/class/net/$GEN_IF_NS/queues/rx-*/rps_cpus; do
        printf 'RPS %s ' \"\$f\"; cat \"\$f\" || exit 1
      done"
  fi
  remote "$target" "$command" | LC_ALL=C sort
}
snapshot_flowtable(){
  d 'nft list table inet ask_flowtable' | sed -E 's/counter packets [0-9]+ bytes [0-9]+/counter/g'
}
baseline_ping(){
  g "$GEN1" "ping -c 1 -W 2 -I $BR_G1_IPV4 $BR_G2_IPV4 >/dev/null" &&
    g "$GEN2" "ping -c 1 -W 2 -I $BR_G2_IPV4 $BR_G1_IPV4 >/dev/null" &&
    g "$GEN1" "ping -6 -c 1 -W 2 -I $BR_G1_IPV6 $BR_G2_IPV6 >/dev/null" &&
    g "$GEN2" "ping -6 -c 1 -W 2 -I $BR_G2_IPV6 $BR_G1_IPV6 >/dev/null" &&
    g "$GEN1" "ping -c 1 -W 2 -I $JUMBO_G1_VLAN4 $JUMBO_G2_VLAN4 >/dev/null" &&
    g "$GEN2" "ping -c 1 -W 2 -I $JUMBO_G2_VLAN4 $JUMBO_G1_VLAN4 >/dev/null" &&
    g "$GEN1" "ping -6 -c 1 -W 2 -I $JUMBO_G1_VLAN6 $JUMBO_G2_VLAN6 >/dev/null" &&
    g "$GEN2" "ping -6 -c 1 -W 2 -I $JUMBO_G2_VLAN6 $JUMBO_G1_VLAN6 >/dev/null" &&
    g "$GEN1" "ping -c 1 -W 2 -I $JUMBO_G1_VLAN4 $BR_G2_IPV4 >/dev/null" &&
    g "$GEN2" "ping -c 1 -W 2 -I $BR_G2_IPV4 $JUMBO_G1_VLAN4 >/dev/null" &&
    g "$GEN1" "ping -6 -c 1 -W 2 -I $JUMBO_G1_VLAN6 $BR_G2_IPV6 >/dev/null" &&
    g "$GEN2" "ping -6 -c 1 -W 2 -I $BR_G2_IPV6 $JUMBO_G1_VLAN6 >/dev/null" ||
    { fail "baseline routed or VLAN path failed"; return 1; }
}

no_leases
POOL_BEFORE=$(pool_count)
[[ $POOL_BEFORE =~ ^[0-9]+$ ]] && (( POOL_BEFORE >= 1000 )) ||
  { echo "ERROR: BMan pool baseline is unavailable or too small" >&2; exit 1; }
POOL_MIN=$((POOL_BEFORE / 2))
check_health
bin_tune="$HERE/testrig-gen-tune.sh"
"$bin_tune" check
d 'test ! -L /sys/class/net/eth3/master && test ! -L /sys/class/net/eth4/master' ||
  { echo "ERROR: a DUT test port already has a master" >&2; exit 1; }
no_test_sockets
for host in "$GEN1" "$GEN2"; do
  active=$(g "$host" "ss -H -tn state established") || exit 1
  [ -z "$active" ] || { echo "ERROR: an existing TCP session is active in the test namespace" >&2; exit 1; }
  rps=$(g "$host" "for f in /sys/class/net/$GEN_IF_NS/queues/rx-*/rps_cpus; do cat \"\$f\" || exit 1; done") || exit 1
  [ -n "$rps" ] && ! printf '%s\n' "$rps" | grep -qvE '^[0,]+$' ||
    { echo "ERROR: generator RPS is not uniformly zero" >&2; exit 1; }
done
for i in "${!devs[@]}"; do
  info=$(link_info "$i") || exit 1
  read -r index link mac mtu extra <<< "$info"
  [[ $index =~ ^[1-9][0-9]*$ && $link =~ ^[1-9][0-9]*$ &&
     $mac =~ ^([0-9a-f]{2}:){5}[0-9a-f]{2}$ && $mtu = 1500 && -z ${extra:-} ]] ||
    { echo "ERROR: invalid baseline for ${targets[$i]}:${devs[$i]}: $info" >&2; exit 1; }
  remote "${targets[$i]}" "ip -o link show dev ${devs[$i]} | grep -Fq 'UP,LOWER_UP'" ||
    { echo "ERROR: ${targets[$i]}:${devs[$i]} is not up" >&2; exit 1; }
  indexes[$i]=$index; links[$i]=$link; macs[$i]=$mac
  owners[$i]="t2mtu-${TOKEN:0:16}-${suffixes[$i]}"
done
[ "${links[2]}" = "${indexes[0]}" ] && [ "${links[3]}" = "${indexes[1]}" ] &&
  [ "${links[5]}" = "${indexes[4]}" ] && [ "${links[7]}" = "${indexes[6]}" ] ||
  { echo "ERROR: VLAN parent identities do not match" >&2; exit 1; }
for i in 0 1 4 6; do no_foreign_children "$i"; done
check_addresses "$GEN1" "$GEN_IF_NS" 4 "$BR_G1_IPV4"
check_addresses "$GEN2" "$GEN_IF_NS" 4 "$BR_G2_IPV4"
check_addresses "$GEN1" "$GEN_IF_NS" 6 "$BR_G1_IPV6"
check_addresses "$GEN2" "$GEN_IF_NS" 6 "$BR_G2_IPV6"
check_addresses "$GEN1" "$GEN_IF_NS.10" 4 "$JUMBO_G1_VLAN4"
check_addresses "$GEN2" "$GEN_IF_NS.20" 4 "$JUMBO_G2_VLAN4"
check_addresses "$GEN1" "$GEN_IF_NS.10" 6 "$JUMBO_G1_VLAN6"
check_addresses "$GEN2" "$GEN_IF_NS.20" 6 "$JUMBO_G2_VLAN6"
baseline_ping
say "DUT boot $BOOT; pool $JUMBO_BPID has $POOL_BEFORE buffers (floor $POOL_MIN)"
case "$MODE" in
  preflight|preflight-routed) say "Read-only jumbo preflight passed"; exit 0 ;;
esac

umask 077
mkdir -p "$OUTDIR"
RUN="$OUTDIR/dut2-jumbo-$TOKEN"
mkdir "$RUN"
printf 'mode=%s\nboot=%s\npool=%s\npool_before=%s\npool_min=%s\n' \
  "$MODE" "$BOOT" "$JUMBO_BPID" "$POOL_BEFORE" "$POOL_MIN" > "$RUN/metadata"
for target in dut g1 g2; do snapshot "$target" > "$RUN/$target-before"; done
snapshot_flowtable > "$RUN/flowtable-before"
LEASE="$LEASE_ROOT/lease-$TOKEN"
mkdir "$LEASE"
printf 'token=%s\nboot=%s\nrun=%s\n' "$TOKEN" "$BOOT" "$RUN" > "$LEASE/identity"
: > "$LEASE/journal"
record(){
  printf '%s\n' "$*" >> "$LEASE/journal"
  python3 - "$LEASE" <<'PY'
import os, sys
for name in ("identity", "journal", ""):
    fd = os.open(os.path.join(sys.argv[1], name), os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)
PY
}
record "baseline snapshots $RUN"
claims_done=0; claim_count=0; nat_state=none
server_state=(none none)
server_dirs=("/tmp/wgrig-srv-$TOKEN-j1" "/tmp/wgrig-srv-$TOKEN-j2")
server_hosts=("$GEN1" "$GEN2")

clear_nat(){
  [ "$nat_state" != none ] || return 0
  d_in sh -s -- delete "$TOKEN" < "$HERE/testrig-vendor-jumbo-nft.sh" || return 1
  nat_state=none
}
start_nat(){
  local mode=$1
  clear_nat
  record "nft create $mode"
  nat_state=pending
  if [ "$mode" = empty ]; then
    d_in sh -s -- create "$TOKEN" empty < "$HERE/testrig-vendor-jumbo-nft.sh"
  else
    d_in sh -s -- create "$TOKEN" nat "$BR_G1_IPV4" "$BR_G2_IPV4" \
      "$BR_G1_IPV6" "$BR_G2_IPV6" < "$HERE/testrig-vendor-jumbo-nft.sh"
  fi
  nat_state=owned
}
start_server(){
  local i=$1 status
  record "server ${server_hosts[$i]} ${server_dirs[$i]} $PORT"
  server_state[$i]=pending
  if g_in "${server_hosts[$i]}" python3 - start "${server_dirs[$i]}" "$PORT" < "$HERE/testrig-wg-server.py"; then
    server_state[$i]=owned
  else
    status=$?
    if [ "$status" = 10 ] || [ "$status" = 11 ]; then server_state[$i]=none; fi
    return "$status"
  fi
}
stop_server(){
  local i=$1 ident pid started
  [ "${server_state[$i]}" != none ] || return 0
  if ident=$(g_in "${server_hosts[$i]}" python3 - identity "${server_dirs[$i]}" "$PORT" < "$HERE/testrig-wg-server.py") &&
     [[ $ident =~ ^[0-9]+:[0-9]+:5278$ ]]; then
    IFS=: read -r pid started _ <<< "$ident"
    g_in "${server_hosts[$i]}" python3 - "$pid" "$started" server "$PORT" < "$HERE/testrig-wgfp-stop.py" &&
      g_in "${server_hosts[$i]}" python3 - remove "${server_dirs[$i]}" "$PORT" < "$HERE/testrig-wg-server.py" ||
      return 1
  elif ! g "${server_hosts[$i]}" "test ! -e ${server_dirs[$i]}"; then
    fail "server identity is uncertain on generator $i"
    return 1
  fi
  server_state[$i]=none
}
safe_to_restore(){
  check_health && no_bridge_lease && no_test_sockets &&
    d 'test ! -L /sys/class/net/eth3/master && test ! -L /sys/class/net/eth4/master' &&
    snapshot_flowtable > "$RUN/flowtable-pre-restore" &&
    cmp -s "$RUN/flowtable-before" "$RUN/flowtable-pre-restore" ||
    { fail "traffic, bridge, or DUT health is uncertain; MTUs retained"; return 1; }
}
restore_mtu(){
  local i
  if [ "$claims_done" = 0 ]; then
    for ((i=claim_count-1; i>=0; i--)); do link_op release "$i" 9000 1500 || return 1; done
    return 0
  fi
  safe_to_restore || return 1
  for i in 5 7 4 6 2 3 0 1; do
    case "$i" in 4|6|0|1) no_foreign_children "$i" || return 1 ;; esac
    link_op set "$i" 9000 1500 || return 1
    case "$i" in 0|1) check_health || return 1 ;; esac
  done
  for ((i=claim_count-1; i>=0; i--)); do link_op release "$i" 9000 1500 || return 1; done
}
cleanup(){
  local rc=$? clean=1 target
  trap - EXIT INT TERM
  set +e
  stop_server 0 || { echo "ERROR: generator 1 server cleanup uncertain" >&2; clean=0; }
  stop_server 1 || { echo "ERROR: generator 2 server cleanup uncertain" >&2; clean=0; }
  clear_nat || { echo "ERROR: NAT cleanup uncertain" >&2; clean=0; }
  if [ "$clean" = 1 ]; then restore_mtu || clean=0; fi
  if [ "$clean" = 1 ]; then
    check_health || clean=0
    no_test_sockets || clean=0
    baseline_ping || clean=0
    for target in dut g1 g2; do
      snapshot "$target" > "$RUN/$target-after" &&
        cmp -s "$RUN/$target-before" "$RUN/$target-after" ||
        { echo "ERROR: $target link, address, route, or RPS baseline changed" >&2; clean=0; }
    done
    snapshot_flowtable > "$RUN/flowtable-after" &&
      cmp -s "$RUN/flowtable-before" "$RUN/flowtable-after" ||
      { echo "ERROR: shared flowtable configuration changed" >&2; clean=0; }
  fi
  if [ "$clean" = 1 ] &&
     [ "$(cat "$LEASE/identity")" = "$(printf 'token=%s\nboot=%s\nrun=%s' "$TOKEN" "$BOOT" "$RUN")" ]; then
    rm -f -- "$LEASE/identity" "$LEASE/journal"
    rmdir "$LEASE" || clean=0
  else
    clean=0
  fi
  if [ "$clean" = 1 ]; then
    say "Generator and DUT MTUs, routes, links and shared flowtable restored"
    if [ "$rc" = 0 ]; then
      printf 'mode=%s\nboot=%s\n' "$MODE" "$BOOT" > "$RUN/COMPLETE"
      say "Accepted jumbo artifacts: $RUN"
    fi
  else
    echo "ERROR: retained jumbo ownership record $LEASE; inspect before recovery" >&2
    rc=1
  fi
  exit "$rc"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
say "Jumbo lease $LEASE; candidate results $RUN"
for i in "${!devs[@]}"; do
  record "claim ${targets[$i]} ${devs[$i]} ${indexes[$i]} ${links[$i]} ${macs[$i]} ${owners[$i]}"
  claim_count=$((claim_count + 1))
  link_op claim "$i" 1500 9000
done
claims_done=1
for i in "${!devs[@]}"; do
  record "set ${targets[$i]} ${devs[$i]} 1500 9000"
  link_op set "$i" 1500 9000
done
check_links(){
  local i
  for i in "${!devs[@]}"; do link_op check "$i" 9000 1500 || return 1; done
}
check_links
check_health

ping_pair(){
  local family=$1 source=$2 dest=$3 size
  if [ "$family" = 4 ]; then size=8972; else size=8952; fi
  g "$GEN1" "ping -$family -M do -s $size -c 2 -W 2 -I $source $dest"
  check_health
  g "$GEN2" "ping -$family -M do -s $size -c 2 -W 2 -I $dest $source"
  check_health
}
ping_pair 4 "$BR_G1_IPV4" "$BR_G2_IPV4"
ping_pair 6 "$BR_G1_IPV6" "$BR_G2_IPV6"
ping_pair 4 "$JUMBO_G1_VLAN4" "$JUMBO_G2_VLAN4"
ping_pair 6 "$JUMBO_G1_VLAN6" "$JUMBO_G2_VLAN6"
ping_pair 4 "$JUMBO_G1_VLAN4" "$BR_G2_IPV4"
ping_pair 6 "$JUMBO_G1_VLAN6" "$BR_G2_IPV6"
say "Routed, VLAN and VLAN-port IPv4/IPv6 full-size DF probes passed"

start_server 0
start_server 1
if [ -n "${RIG_JUMBO_SNAPSHOT_DIR:-}" ]; then
  "$HERE/testrig-jumbo-snapshot.sh" "$RIG_JUMBO_SNAPSHOT_DIR" before
  if [ -n "${RIG_JUMBO_PAIR_DIR:-}" ]; then
    python3 "$HERE/testrig_jumbo_parity.py" check-snapshot \
      "$RIG_JUMBO_PAIR_DIR" "$RIG_JUMBO_ROW" "$RIG_JUMBO_PAIR_REP" vendor before
  fi
fi
export QUICK_G1_IPV4=$BR_G1_IPV4 QUICK_G2_IPV4=$BR_G2_IPV4
export QUICK_G1_IPV6=$BR_G1_IPV6 QUICK_G2_IPV6=$BR_G2_IPV6
export QUICK_G1_VLAN4=$JUMBO_G1_VLAN4 QUICK_G2_VLAN4=$JUMBO_G2_VLAN4
export QUICK_G1_VLAN6=$JUMBO_G1_VLAN6 QUICK_G2_VLAN6=$JUMBO_G2_VLAN6

validate_result(){
  PYTHONPATH="$HERE${PYTHONPATH:+:$PYTHONPATH}" python3 - "$1" "$2" "$3" "$STREAMS" "$PORT" "$DUR" "$STEADY_FROM" \
    "$BR_G1_IPV4" "$BR_G2_IPV4" "$BR_G1_IPV6" "$BR_G2_IPV6" \
    "$JUMBO_G1_VLAN4" "$JUMBO_G2_VLAN4" "$JUMBO_G1_VLAN6" "$JUMBO_G2_VLAN6" "${MEASURE_TO:-$((DUR-1))}" <<'PY'
import csv, ipaddress, json, math, sys
from testrig_iperf import measure_tcp
csv_path, json_path, tag = sys.argv[1:4]
streams, port, duration, steady_from = map(int, sys.argv[4:8])
p1_4, p2_4, p1_6, p2_6, v1_4, v2_4, v1_6, v2_6 = sys.argv[8:16]
with open(csv_path, newline="") as source:
    rows = list(csv.DictReader(source))
if len(rows) != 1 or rows[0]["cell"] != tag:
    raise SystemExit("unexpected or incomplete jumbo CSV")
rate = float(rows[0]["gbps"])
if not math.isfinite(rate) or rate < 0 or rows[0]["verdict"] not in ("HW", "PARTIAL", "SW", "FAIL(no traffic)"):
    raise SystemExit("invalid jumbo throughput")
with open(json_path) as source:
    result = json.load(source)
if result.get("error"):
    raise SystemExit(result["error"])
start = result.get("start", {})
if type(start.get("tcp_mss_default")) is not int or start["tcp_mss_default"] < 8800:
    raise SystemExit("jumbo result lacks a jumbo TCP MSS")
bidir = tag.endswith("-bidir")
test = start.get("test_start", {})
if (test.get("protocol") != "TCP" or test.get("num_streams") != streams or
        test.get("duration") != duration or test.get("bidir", 0) != int(bidir)):
    raise SystemExit("jumbo iperf parameters do not match the requested trial")
if "-v4" in tag:
    p1, p2, v1, v2 = p1_4, p2_4, v1_4, v2_4
elif "-v6" in tag:
    p1, p2, v1, v2 = p1_6, p2_6, v1_6, v2_6
else:
    raise SystemExit("invalid jumbo address family")
if tag.startswith("vlanport-"):
    source, dest = v1, p2
elif tag.startswith("portvlan-"):
    source, dest = p2, v1
elif tag.startswith("vlan-"):
    source, dest = v1, v2
elif tag.startswith(("unicast-", "nat-")):
    source, dest = p1, p2
else:
    raise SystemExit("invalid jumbo cell")
if tag.endswith("-rev"):
    source, dest = dest, source
connections = start.get("connected", [])
if (len(connections) != streams * (2 if bidir else 1) or
        any(ipaddress.ip_address(c["local_host"]) != ipaddress.ip_address(source) or
            ipaddress.ip_address(c["remote_host"]) != ipaddress.ip_address(dest) or
            c["remote_port"] != port for c in connections)):
    raise SystemExit("jumbo iperf connections do not match test endpoints")
measured = measure_tcp(result, duration, steady_from, float(sys.argv[16]),
                       direction="bidir" if bidir else "up", streams=streams)["gbps"]
if not math.isfinite(measured) or abs(measured - rate) > 0.001:
    raise SystemExit("jumbo CSV does not match iperf data")
PY
}
run_quick(){
  local cell=$1 bidir=$2 rep=$3 tag=$1 csv json
  [ "$bidir" = 0 ] || tag="$tag-bidir"
  csv="$RUN/quick9k-$tag-r$rep.csv"
  json="$RUN/json/$tag-r$rep"
  check_health
  say "Jumbo $tag repetition $rep"
  DUT_KIND=vendor RIG_NS="$VENDOR_NS" RIG_EXTERNAL_NAT_TOKEN="$TOKEN" \
    RIG_PRESERVE_RPS=1 RIG_VERIFY_PORTS=1 IPERF_PORT="$PORT" \
    DUR="$DUR" STEADY_FROM="$STEADY_FROM" MEASURE_TO="${MEASURE_TO:-$((DUR-1))}" STREAMS="$STREAMS" BIDIR="$bidir" \
    OUT="$csv" J="$json" "$HERE/testrig-offload-quick.sh" "$cell"
  validate_result "$csv" "$json/$tag.json" "$tag"
  check_health
}
uni=(unicast-v4 unicast-v6 unicast-v4-rev unicast-v6-rev
     vlan-v4 vlan-v6 vlan-v4-rev vlan-v6-rev
     vlanport-v4 vlanport-v6 portvlan-v4 portvlan-v6)
bi=(unicast-v4 unicast-v6 vlan-v4 vlan-v6 vlanport-v4 vlanport-v6)
nat_uni=(nat-v4 nat-v6 nat-v4-rev nat-v6-rev)
nat_bi=(nat-v4 nat-v6)
case ${RIG_JUMBO_ROW:-} in
  unicast-9k)
    uni=(unicast-v4 unicast-v6 unicast-v4-rev unicast-v6-rev)
    bi=(unicast-v4 unicast-v6); nat_uni=(); nat_bi=() ;;
  nat-9k)
    uni=(); bi=() ;;
  vlan-9k)
    uni=(vlan-v4 vlan-v6 vlan-v4-rev vlan-v6-rev)
    bi=(vlan-v4 vlan-v6); nat_uni=(); nat_bi=() ;;
  vlan-port-9k)
    uni=(vlanport-v4 vlanport-v6 portvlan-v4 portvlan-v6)
    bi=(vlanport-v4 vlanport-v6); nat_uni=(); nat_bi=() ;;
esac
if [ "$MODE" = smoke ]; then
  uni=(unicast-v4 unicast-v6 vlan-v4 vlan-v6 vlanport-v4 vlanport-v6)
  bi=()
  nat_uni=(nat-v4 nat-v6)
  nat_bi=()
fi
for ((rep=1; rep<=REPS; rep++)); do
  check_links
  if (( ${#uni[@]} || ${#bi[@]} )); then
    start_nat empty
    for cell in "${uni[@]}"; do run_quick "$cell" 0 "$rep"; done
    for cell in "${bi[@]}"; do run_quick "$cell" 1 "$rep"; done
  fi
  if (( ${#nat_uni[@]} || ${#nat_bi[@]} )); then
    start_nat nat
    for cell in "${nat_uni[@]}"; do run_quick "$cell" 0 "$rep"; done
    for cell in "${nat_bi[@]}"; do run_quick "$cell" 1 "$rep"; done
  fi
  clear_nat
done
stop_server 0
stop_server 1
no_test_sockets
check_links
check_health
if [ -n "${RIG_JUMBO_SNAPSHOT_DIR:-}" ]; then
  "$HERE/testrig-jumbo-snapshot.sh" "$RIG_JUMBO_SNAPSHOT_DIR" after
  if [ -n "${RIG_JUMBO_PAIR_DIR:-}" ]; then
    python3 "$HERE/testrig_jumbo_parity.py" check-snapshot \
      "$RIG_JUMBO_PAIR_DIR" "$RIG_JUMBO_ROW" "$RIG_JUMBO_PAIR_REP" vendor after
  fi
fi
if [ "$MODE" != run-routed ]; then
  record "bridge $MODE"
  if [ "$MODE" = smoke ]; then bridge_mode=smoke; else bridge_mode=run; fi
  OUTDIR="$RUN" RIG_INHERIT_LOCK=1 RIG_JUMBO_TOKEN="$TOKEN" \
    RIG_JUMBO_POOL_MIN="$POOL_MIN" BR_MTU=9000 REPS="$REPS" \
    DUR="$DUR" STEADY_FROM="$STEADY_FROM" STREAMS="$STREAMS" \
    "$HERE/testrig-vendor-bridge.sh" "$bridge_mode"
fi
check_health
