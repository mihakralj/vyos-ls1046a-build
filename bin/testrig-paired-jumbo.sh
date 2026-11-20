#!/usr/bin/env bash
# Alternate VyOS and OpenWrt for each routed jumbo row and repetition.
set -Eeuo pipefail

MODE=run
if [[ ${1:-} = --preflight ]]; then MODE=preflight; shift; fi
if [[ $MODE = run && ${RIG_JUMBO_LIVE_APPROVED:-0} != 1 ]]; then
  echo "ERROR: paired jumbo run disabled until FE disengagement and drain are proven safe" >&2
  exit 2
fi

HERE=$(cd "$(dirname "$0")" && pwd)
. "$HERE/testrig-env.sh"
. "$HERE/testrig-parity-profile.sh"
export RIG_PARITY=1 PARITY_MTU=9000
rig_require GEN1 GEN2 ASK2_DUT VENDOR_DUT PARITY_REFERENCE_DIR \
  PARITY_ASK2_COLD_BOOT_ID PARITY_VENDOR_COLD_BOOT_ID \
  BR_G1_IPV4 BR_G2_IPV4 BR_G1_IPV6 BR_G2_IPV6 \
  JUMBO_G1_VLAN4 JUMBO_G2_VLAN4 JUMBO_G1_VLAN6 JUMBO_G2_VLAN6 JUMBO_BPID

fail(){ echo "ERROR: $*" >&2; exit 1; }
same_endpoint(){ [[ -z $1 || $1 = "$2" ]] || fail "different generator endpoint: $3"; }
same_endpoint "${QUICK_G1_IPV4:-}" "$BR_G1_IPV4" QUICK_G1_IPV4
same_endpoint "${QUICK_G2_IPV4:-}" "$BR_G2_IPV4" QUICK_G2_IPV4
same_endpoint "${QUICK_G1_IPV6:-}" "$BR_G1_IPV6" QUICK_G1_IPV6
same_endpoint "${QUICK_G2_IPV6:-}" "$BR_G2_IPV6" QUICK_G2_IPV6
same_endpoint "${QUICK_G1_VLAN4:-}" "$JUMBO_G1_VLAN4" QUICK_G1_VLAN4
same_endpoint "${QUICK_G2_VLAN4:-}" "$JUMBO_G2_VLAN4" QUICK_G2_VLAN4
same_endpoint "${QUICK_G1_VLAN6:-}" "$JUMBO_G1_VLAN6" QUICK_G1_VLAN6
same_endpoint "${QUICK_G2_VLAN6:-}" "$JUMBO_G2_VLAN6" QUICK_G2_VLAN6
for address in "$BR_G1_IPV4" "$BR_G2_IPV4" "$BR_G1_IPV6" "$BR_G2_IPV6" \
  "$JUMBO_G1_VLAN4" "$JUMBO_G2_VLAN4" "$JUMBO_G1_VLAN6" "$JUMBO_G2_VLAN6"; do
  [[ $address =~ ^[[:xdigit:].:]+$ ]] || fail "invalid generator endpoint"
done
export QUICK_G1_IPV4=$BR_G1_IPV4 QUICK_G2_IPV4=$BR_G2_IPV4
export QUICK_G1_IPV6=$BR_G1_IPV6 QUICK_G2_IPV6=$BR_G2_IPV6
export QUICK_G1_VLAN4=$JUMBO_G1_VLAN4 QUICK_G2_VLAN4=$JUMBO_G2_VLAN4
export QUICK_G1_VLAN6=$JUMBO_G1_VLAN6 QUICK_G2_VLAN6=$JUMBO_G2_VLAN6
[[ $REPS = 3 ]] || fail "paired jumbo requires three repetitions"
[[ ${RIG_PRESERVE_RPS:-1} = 1 ]] || fail "paired jumbo must preserve generator RPS"
[[ ${IPERF_PORT:-5278} = 5278 ]] || fail "paired jumbo requires TCP port 5278"
[[ -z ${RIG_JUMBO_ROW:-}${RIG_JUMBO_SNAPSHOT_DIR:-}${RIG_JUMBO_PAIR_DIR:-}${RIG_JUMBO_PAIR_REP:-} ]] ||
  fail "jumbo group variables are owned by this coordinator"
export RIG_PRESERVE_RPS=1 RIG_VERIFY_PORTS=1 IPERF_PORT=5278
for value in "$GEN_IF" "$GEN_IF_NS" "$VENDOR_NS"; do
  [[ $value =~ ^[A-Za-z0-9_.-]+$ ]] || fail "invalid interface or namespace"
done
[[ -z $RIG_KEY || -r $RIG_KEY ]] || fail "cannot read RIG_KEY"
[[ $PARITY_REFERENCE_DIR = /* && -d $PARITY_REFERENCE_DIR && ! -L $PARITY_REFERENCE_DIR ]] ||
  fail "PARITY_REFERENCE_DIR must be an existing absolute directory"
[[ -f $PARITY_REFERENCE_DIR/COMPLETE || -f $PARITY_REFERENCE_DIR/PARTIAL ]] ||
  fail "the reference battery has not finished successfully"
TAG=${TAG:-$(date -u +%Y%m%dT%H%M%SZ)}
RUN_DIR=${RUN_DIR:-$RIG_OUT/paired-jumbo-$TAG}
[[ $RUN_DIR = /* && ! -e $RUN_DIR && ! -L $RUN_DIR ]] ||
  fail "RUN_DIR must be a new absolute directory"
SSH_INPUT=(ssh -o BatchMode=yes -o ConnectTimeout=8)
[[ -z $RIG_KEY ]] || SSH_INPUT+=(-i "$RIG_KEY")
SSH=("${SSH_INPUT[@]}" -n)

mkdir -p "$RIG_OUT"
exec 8>>"$RIG_OUT/parity-bidir.lock"
flock -n 8 || fail "another paired battery is running"
exec 9>>/tmp/testrig-dut2-battery.lock
flock -n 9 || fail "a DUT2 matrix adapter owns the rig"
if pgrep -f '[b]ash (.*/)?testrig-offload-quick.sh' >/dev/null; then
  fail "another quick harness is running"
fi
LEASE="${XDG_STATE_HOME:-$HOME/.local/state}/testrig-ask2-jumbo/lease"
[[ ! -e $LEASE && ! -L $LEASE ]] || fail "a VyOS jumbo lease already exists: $LEASE"

umask 077
mkdir "$RUN_DIR"
python3 "$HERE/testrig_parity.py" profile "$RUN_DIR/protocol.json"
python3 "$HERE/testrig_jumbo_parity.py" reference "$PARITY_REFERENCE_DIR/protocol.json"
sha256sum --check "$PARITY_REFERENCE_DIR/harness.sha256" > "$RUN_DIR/reference-hashes.txt" ||
  fail "reference harness changed"
sha256sum "$HERE"/testrig{-paired-jumbo.sh,-ask2-9k.sh,-vendor-jumbo.sh,-jumbo-snapshot.sh,-vendor-mtu.sh,-vendor-jumbo-nft.sh,-gen-tune.sh,-offload-quick.sh,-parity-profile.sh,-env.sh,_parity.py,_jumbo_parity.py,_iperf.py,_wg_validation.py,-wg-server.py,-wgfp-stop.py} \
  > "$RUN_DIR/harness.sha256"
python3 "$HERE/testrig_jumbo_parity.py" schedule "$RUN_DIR" "$@" > "$RUN_DIR/schedule.tsv"
sha256sum "$RUN_DIR"/{protocol.json,battery.json,schedule.tsv} >> "$RUN_DIR/harness.sha256"

mkdir "$RUN_DIR/baseline"
"$HERE/testrig-jumbo-snapshot.sh" "$RUN_DIR/baseline" before
python3 "$HERE/testrig_parity.py" compare \
  "$RUN_DIR/baseline/gen1-before.json" "$RUN_DIR/baseline/gen2-before.json" --mtu 1500 \
  --baseline "$PARITY_REFERENCE_DIR/gen1-before.json" "$PARITY_REFERENCE_DIR/gen2-before.json"

config=$("${SSH[@]}" "$ASK2_DUT" '/bin/vbash -ic "show configuration commands"')
if grep -Eq "^set interfaces ethernet eth[34] address '?dhcp(v6)?'?$" <<<"$config"; then
  fail "VyOS traffic ports still have DHCP configured"
fi
"${SSH[@]}" "$ASK2_DUT" '
for e in 3 4; do
  state=$(systemctl is-active dhclient@eth$e)
  [ "$state" = inactive ] || { echo "eth$e dhclient state: $state" >&2; exit 1; }
done'
[[ $("${SSH[@]}" "$ASK2_DUT" 'sudo -n cat /sys/module/wireguard/parameters/wgfp_peer') = off ]] ||
  fail "WGFP is bound; do not start a routed battery"
boots=()
for board in ask2 vendor; do
  dut=$ASK2_DUT
  cold=$PARITY_ASK2_COLD_BOOT_ID
  if [[ $board = vendor ]]; then dut=$VENDOR_DUT; cold=$PARITY_VENDOR_COLD_BOOT_ID; fi
  boot=$("${SSH[@]}" "$dut" 'cat /proc/sys/kernel/random/boot_id')
  [[ $boot =~ ^[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}$ &&
     $boot = "$cold" && $boot = "$(<"$PARITY_REFERENCE_DIR/boot-$board.txt")" ]] ||
    fail "$board boot differs from the confirmed cold boot or reference battery"
  boots+=("$boot")
  printf '%s\n' "$boot" > "$RUN_DIR/boot-$board.txt"
  "${SSH[@]}" "$dut" 'cat /etc/os-release' > "$RUN_DIR/platform-$board.txt"
  cmp -s "$PARITY_REFERENCE_DIR/platform-$board.txt" "$RUN_DIR/platform-$board.txt" ||
    fail "$board operating system differs from the reference battery"
  if [[ $board = ask2 ]]; then
    grep -q '^ID=vyos$' "$RUN_DIR/platform-$board.txt" || fail "DUT1 must be VyOS"
  else
    grep -Eq '^ID="?openwrt"?$|^ID_LIKE=.*openwrt' "$RUN_DIR/platform-$board.txt" ||
      fail "DUT2 must be OpenWrt"
  fi
  "${SSH[@]}" "$dut" '
uname -r
for e in eth3 eth4; do
  ip -br addr show dev "$e"
  for attr in rps_cpus rps_flow_cnt; do
    printf "%s %s: " "$e" "$attr"
    cat /sys/class/net/$e/queues/rx-*/$attr | sort -u | tr "\n" " "
    echo
  done
done
printf "rps_sock_flow_entries: "
cat /proc/sys/net/core/rps_sock_flow_entries' > "$RUN_DIR/controls-$board.txt"
  cmp -s "$PARITY_REFERENCE_DIR/controls-$board.txt" "$RUN_DIR/controls-$board.txt" ||
    fail "$board controls differ from the reference battery"
done

check_boots(){
  [[ $("${SSH[@]}" "$ASK2_DUT" 'cat /proc/sys/kernel/random/boot_id') = "${boots[0]}" &&
     $("${SSH[@]}" "$VENDOR_DUT" 'cat /proc/sys/kernel/random/boot_id') = "${boots[1]}" ]] ||
    { echo "ERROR: a DUT rebooted; paired jumbo is incomplete" >&2; return 1; }
}
check_hashes(){
  sha256sum --check --quiet "$RUN_DIR/harness.sha256" &&
    sha256sum --check --quiet "$PARITY_REFERENCE_DIR/harness.sha256" ||
    { echo "ERROR: a benchmark harness changed" >&2; return 1; }
}
check_nat(){
  local tables
  tables=$("${SSH[@]}" "$ASK2_DUT" 'sudo -n nft list tables') ||
    { echo "ERROR: cannot inspect VyOS NAT ownership" >&2; return 1; }
  if grep -Eq '^table (ip|ip6) ask2q$' <<<"$tables"; then
    echo "ERROR: foreign or uncleared ask2q table; preserve the lease" >&2
    return 1
  fi
}
check_sockets(){
  local host sockets address4 vlan4 address6 vlan6 index
  for index in 1 2; do
    if [[ $index = 1 ]]; then
      host=$GEN1
      address4=$BR_G1_IPV4 vlan4=$JUMBO_G1_VLAN4
      address6=$BR_G1_IPV6 vlan6=$JUMBO_G1_VLAN6
    else
      host=$GEN2
      address4=$BR_G2_IPV4 vlan4=$JUMBO_G2_VLAN4
      address6=$BR_G2_IPV6 vlan6=$JUMBO_G2_VLAN6
    fi
    sockets=$("${SSH_INPUT[@]}" "$host" \
      "sudo -n sh -s -- $VENDOR_NS $address4 $vlan4 $address6 $vlan6" <<'REMOTE'
set -eu
ns=$1 addr4=$2 vlan4=$3 addr6=$4 vlan6=$5
for port in 5277 5278; do
  ss -H -ltn "sport = :$port"
  ss -H -tn "( sport = :$port or dport = :$port )"
  ip netns exec "$ns" ss -H -ltn "sport = :$port"
  ip netns exec "$ns" ss -H -tn "( sport = :$port or dport = :$port )"
done
for address in "$addr4" "$vlan4"; do
  ss -H -ltn4 "src $address"
  ss -H -tn4 "src $address"
  ss -H -uan4 "src $address"
  ip netns exec "$ns" ss -H -ltn4 "src $address"
  ip netns exec "$ns" ss -H -tn4 "src $address"
  ip netns exec "$ns" ss -H -uan4 "src $address"
done
for address in "$addr6" "$vlan6"; do
  ss -H -ltn6 "src [$address]"
  ss -H -tn6 "src [$address]"
  ss -H -uan6 "src [$address]"
  ip netns exec "$ns" ss -H -ltn6 "src [$address]"
  ip netns exec "$ns" ss -H -tn6 "src [$address]"
  ip netns exec "$ns" ss -H -uan6 "src [$address]"
done
REMOTE
    ) || { echo "ERROR: cannot inspect generator sockets on $host" >&2; return 1; }
    [[ -z $sockets ]] ||
      { echo "ERROR: test-path sockets remain on $host" >&2; return 1; }
  done
}
check_boots
check_hashes
check_nat
check_sockets
bash "$HERE/testrig-ask2-9k.sh" preflight
RIG_INHERIT_LOCK=1 REPS=1 WG_REPS=1 OUTDIR="$RUN_DIR/vendor-preflight" \
  bash "$HERE/testrig-vendor-jumbo.sh" preflight-routed
check_boots
check_hashes
check_nat
check_sockets
if [[ $MODE = preflight ]]; then
  echo "Paired jumbo preflight passed without changing MTUs: $RUN_DIR"
  exit 0
fi

server_hosts=("$GEN1" "$GEN2")
server_states=(none none)
token=$(od -An -N16 -tx1 /dev/urandom | tr -d ' \n')
lease_token=$(od -An -N8 -tx1 /dev/urandom | tr -d ' \n')
[[ $token =~ ^[0-9a-f]{32}$ && $lease_token =~ ^[0-9a-f]{16}$ ]] ||
  fail "cannot generate ownership tokens"
server_directory="/tmp/wgrig-srv-$token-jumbo"
printf 'server=%s\nlease=%s\n' "$server_directory" "$lease_token" > "$RUN_DIR/owners.txt"
ask_state=none
finished=0

server_call(){
  local index=$1; shift
  "${SSH_INPUT[@]}" "${server_hosts[$index]}" "sudo -n python3 - $*"
}
start_server(){
  local index=$1 status
  server_states[$index]=pending
  if server_call "$index" "start $server_directory$index $IPERF_PORT" < "$HERE/testrig-wg-server.py"; then
    server_states[$index]=owned
  else
    status=$?
    if [[ $status = 10 || $status = 11 ]]; then server_states[$index]=none; fi
    return "$status"
  fi
}
stop_servers(){
  local index directory ident pid started port clean=1
  for index in 0 1; do
    case ${server_states[$index]} in
      none) continue ;;
      pending)
        echo "ERROR: uncertain server creation: $server_directory$index" >&2
        clean=0; continue ;;
    esac
    directory=$server_directory$index
    if ident=$(server_call "$index" "identity $directory $IPERF_PORT" < "$HERE/testrig-wg-server.py") &&
       [[ $ident =~ ^[0-9]+:[0-9]+:[0-9]+$ ]]; then
      IFS=: read -r pid started port <<< "$ident"
      if [[ $port = "$IPERF_PORT" ]] &&
         server_call "$index" "$pid $started server $port" < "$HERE/testrig-wgfp-stop.py" &&
         server_call "$index" "remove $directory $port" < "$HERE/testrig-wg-server.py"; then
        server_states[$index]=none
      else clean=0; fi
    else clean=0; fi
  done
  [[ $clean = 1 ]]
}
restore_ask(){
  [[ $ask_state != none ]] || return 0
  if [[ ! -e $LEASE && ! -L $LEASE ]]; then
    [[ $ask_state = starting ]] ||
      { echo "ERROR: owned VyOS jumbo lease disappeared" >&2; return 1; }
    ask_state=none
    return 0
  fi
  check_boots && check_nat && check_sockets || return 1
  RIG_ASK2_JUMBO_TOKEN=$lease_token bash "$HERE/testrig-ask2-9k.sh" down || return 1
  [[ ! -e $LEASE && ! -L $LEASE ]] ||
    { echo "ERROR: VyOS jumbo lease remains after cleanup" >&2; return 1; }
  ask_state=none
}
cleanup(){
  local status=$? clean=1
  trap - EXIT INT TERM
  stop_servers || clean=0
  if [[ $clean = 1 ]]; then restore_ask || clean=0; fi
  if [[ $clean != 1 ]]; then
    echo "ERROR: cleanup is uncertain; preserve $RUN_DIR/owners.txt and inspect the lease" >&2
    status=1
  fi
  if [[ $status = 0 && $finished = 1 ]]; then
    printf 'protocol=matched-iperf-v1\n' > "$RUN_DIR/COMPLETE"
  fi
  exit "$status"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

run_ask(){
  local row=$1 rep=$2 column cell bidir stem snapshot
  snapshot=$RUN_DIR/ask2-$row-r$rep-snapshot
  mkdir "$snapshot"
  check_sockets
  ask_state=starting
  RIG_ASK2_JUMBO_TOKEN=$lease_token bash "$HERE/testrig-ask2-9k.sh" up
  ask_state=owned
  start_server 0
  start_server 1
  "$HERE/testrig-jumbo-snapshot.sh" "$snapshot" before
  python3 "$HERE/testrig_jumbo_parity.py" check-snapshot "$RUN_DIR" "$row" "$rep" ask2 before
  python3 "$HERE/testrig_jumbo_parity.py" cells "$row" > "$RUN_DIR/ask2-$row-r$rep-cells.tsv"
  while IFS=$'\t' read -r column cell bidir; do
    check_boots
    check_nat
    stem=$RUN_DIR/ask2-$row-$column-r$rep
    echo "##### $(date -u +%FT%TZ) row=$row rep=$rep board=ask2 column=$column"
    DUT_KIND=ask2 DUT=$ASK2_DUT RIG_NS='' BIDIR=$bidir OUT=$stem.csv J=$stem-json \
      bash "$HERE/testrig-offload-quick.sh" "$cell"
  done < "$RUN_DIR/ask2-$row-r$rep-cells.tsv"
  stop_servers
  "$HERE/testrig-jumbo-snapshot.sh" "$snapshot" after
  python3 "$HERE/testrig_jumbo_parity.py" check-snapshot "$RUN_DIR" "$row" "$rep" ask2 after
  restore_ask
  printf 'boot=%s\n' "${boots[0]}" > "$RUN_DIR/ask2-$row-r$rep.complete"
}
run_vendor(){
  local row=$1 rep=$2 snapshot output
  local -a candidates
  snapshot=$RUN_DIR/vendor-$row-r$rep-snapshot
  output=$RUN_DIR/vendor-$row-r$rep
  mkdir "$snapshot" "$output"
  check_sockets
  RIG_INHERIT_LOCK=1 RIG_JUMBO_ROW=$row RIG_JUMBO_SNAPSHOT_DIR=$snapshot \
    RIG_JUMBO_PAIR_DIR=$RUN_DIR RIG_JUMBO_PAIR_REP=$rep \
    REPS=1 WG_REPS=1 OUTDIR=$output bash "$HERE/testrig-vendor-jumbo.sh" run-routed
  check_boots
  shopt -s nullglob
  candidates=("$output"/dut2-jumbo-*)
  shopt -u nullglob
  [[ ${#candidates[@]} = 1 ]] || fail "expected exactly one OpenWrt jumbo run"
  python3 "$HERE/testrig_jumbo_parity.py" record "$RUN_DIR" "${candidates[0]}" "$row" "$rep"
}

while IFS=$'\t' read -r row rep board; do
  check_boots
  check_hashes
  case $board in
    ask2) run_ask "$row" "$rep" ;;
    vendor) run_vendor "$row" "$rep" ;;
    *) fail "invalid scheduled board: $board" ;;
  esac
done < "$RUN_DIR/schedule.tsv"
check_boots
check_hashes
python3 "$HERE/testrig_jumbo_parity.py" complete "$RUN_DIR"
finished=1
cat "$RUN_DIR/matrix.txt"
