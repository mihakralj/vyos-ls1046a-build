#!/usr/bin/env bash
# wg-matrix.sh — full WireGuard throughput matrix for one DUT, then the median table.
#   1 tunnel : bin/testrig-wireguard.sh        (4 TCP streams, a NEW tunnel every rep so serial-worker placement is sampled)
#   2, 4 tunnels : bin/testrig-wireguard-multi.sh (2 TCP streams per tunnel, one tunnel set per row)
#   each x up / down / bidir, IPv4 inner, MTU 1420, 30 s runs (first 10 s discarded)
#   OUTER=6 supported: every tunnel runs over IPv6 between the DUT and the selected far generator (use a separate TAG).
# Usage: bin/wg-matrix.sh 70|106|185   Env: REPS1=5 REPSN=3 WG_PORT=eth3|eth4 WG_GEN_QDISC=default|fq DUR=30 STEADY_FROM=10 OUTDIR=/mnt/builds/ask2-review/oracle
# The rig creates tunnels outside VyOS config. On .185, a temporary nft table
# untracks only this rig's outer UDP traffic; existing VyOS configuration is untouched.
# The current .70 has no fw4 forward chain; use WG_SKIP_FW4=1 only if its
# existing forwarding policy already permits the test traffic.
set -euo pipefail
cd "$(dirname "$0")/.."
. bin/testrig-env.sh
. bin/testrig-wgfp-modes.sh
WHO=${1:?usage: wg-matrix.sh 70|106|185}
REPS1=${REPS1:-5}; REPSN=${REPSN:-3}; DUR=${DUR:-30}; STEADY_FROM=${STEADY_FROM:-10}
OUTDIR=${OUTDIR:-/mnt/builds/ask2-review/oracle}; TAG=${TAG:-wgfull}
export DUR STEADY_FROM
if ! [[ $TAG =~ ^[a-zA-Z0-9._-]+$ ]] || [ "$TAG" = . ] || [ "$TAG" = .. ]; then
  echo "TAG must be a simple filename component" >&2; exit 2
fi
for value in "$REPS1" "$REPSN" "$DUR" "$STEADY_FROM"; do
  if ! [[ $value =~ ^(0|[1-9][0-9]*)$ ]]; then
    echo "Repetitions and durations must be non-negative integers" >&2; exit 2
  fi
done
if (( REPS1 < 1 || REPSN < 1 || DUR <= STEADY_FROM + 2 )); then
  echo "Repetitions must be positive and DUR must exceed STEADY_FROM by at least 3 seconds" >&2; exit 2
fi
rig_require GEN1 GEN2
case $WHO in
  70)  export DUT_KIND=vendor DUT="${DUT:-root@192.168.1.70}" ;;
  106) export DUT_KIND=vendor DUT="${DUT:-root@192.168.1.106}" ;;
  185) export DUT_KIND=ask2 DUT="${DUT:-vyos@192.168.1.185}" ;;
  *) echo "70, 106 or 185" >&2; exit 2 ;;
esac
if [ "$DUT_KIND" = ask2 ]; then WG_INNER6=${WG_INNER6:-1}; else WG_INNER6=${WG_INNER6:-0}; fi
case $WG_INNER6 in 0|1) ;; *) echo "WG_INNER6 must be 0 or 1" >&2; exit 2 ;; esac
if [ "$DUT_KIND" = vendor ]; then export RIG_NS=${RIG_NS:-$VENDOR_NS}; else export RIG_NS=${RIG_NS:-}; fi
if [ -n "$RIG_NS" ] && ! [[ $RIG_NS =~ ^[a-zA-Z0-9_.-]+$ ]]; then
  echo "Invalid RIG_NS" >&2; exit 2
fi
# WG_PORT=eth3 (default): dell1, the faster generator, terminates the tunnels.
# WG_PORT=eth4 uses dell2; 1T cells on eth3 get a "-eth3" suffix.
export WG_PORT=${WG_PORT:-eth3}
case $WG_PORT in
  eth4) WG_SUF= ;;
  eth3) WG_SUF=-eth3 ;;
  *) echo "WG_PORT must be eth3 or eth4" >&2; exit 2 ;;
esac
export WG_SUF
WG_GEN_QDISC=${WG_GEN_QDISC:-default}
case $WG_GEN_QDISC in default|fq) ;; *) echo "WG_GEN_QDISC must be default or fq" >&2; exit 2 ;; esac
export WG_GEN_QDISC
single_endpoints=$(bin/testrig-wireguard.sh topology) || { echo "Cannot read single-tunnel endpoints" >&2; exit 2; }
multi_endpoints=$(bin/testrig-wireguard-multi.sh topology) || { echo "Cannot read multi-tunnel endpoints" >&2; exit 2; }
if [ "$single_endpoints" != "$multi_endpoints" ] || [[ $single_endpoints == *$'\n'* ]]; then
  echo "WireGuard runner endpoints do not match" >&2; exit 2
fi
read -r WG_GEN4 WG_DUT4 WG_GEN6 WG_DUT6 extra <<< "$single_endpoints"
if [ -n "$extra" ] || ! [[ $WG_GEN4 =~ ^[0-9.]+$ && $WG_DUT4 =~ ^[0-9.]+$ &&
                           $WG_GEN6 =~ ^[0-9a-fA-F:]+$ && $WG_DUT6 =~ ^[0-9a-fA-F:]+$ ]]; then
  echo "Invalid WireGuard runner endpoints" >&2; exit 2
fi
case ${OUTER:-4} in 4|6) export OUTER=${OUTER:-4} ;; *) echo "OUTER must be 4 or 6" >&2; exit 2 ;; esac
if [ "$DUT_KIND" = vendor ] && [ "${WGFP:-0}" = 1 ]; then
  echo "WGFP is available only on the ASK2 DUT" >&2; exit 2
fi
wgfp_validate_modes || exit 2
if [ "${WG_NOTRACK:-0}" != 0 ]; then
  echo "Leave WG_NOTRACK unset for the matrix; ASK2 applies its own scoped rules" >&2; exit 2
fi
if [ "${KEEP:-0}" = 1 ]; then
  echo "KEEP=1 is not supported by the matrix" >&2; exit 2
fi
mkdir -p "$OUTDIR"
for path in "$OUTDIR/$TAG-$WHO-t1.csv" "$OUTDIR/$TAG-$WHO-t2.csv" "$OUTDIR/$TAG-$WHO-t4.csv" \
            "$OUTDIR/$TAG-$WHO-t2v6.csv" "$OUTDIR/$TAG-$WHO-t4v6.csv" \
            "${J:-}" "${WG_STATE_PATH:-}" "${WG_DUT_STAT:-}" "${WGFP_COUNTER_LOG:-}"; do
  [ -n "$path" ] || continue
  if [ -e "$path" ] || [ -L "$path" ]; then
    echo "Refusing to overwrite $path" >&2; exit 2
  fi
done
SSH=(ssh -o BatchMode=yes -o ConnectTimeout=8)
if [ -n "$RIG_KEY" ]; then SSH+=(-i "$RIG_KEY"); fi
TABLE="wgmatrix${BASHPID}${RANDOM}"
TABLE_TOKEN="${TABLE}x${RANDOM}"
TABLE_MAY_EXIST=0
wgfp_mode_read() {
  "${SSH[@]}" -n "$DUT" "sudo -n cat /sys/module/wireguard/parameters/$1"
}
run_checked() {
  local checkpoint counter_start counter_digest cell peers
  wgfp_check_modes || return 1
  if [ "${WGFP:-0}" = 1 ]; then
    checkpoint=$(python3 - "$WGFP_COUNTER_LOG" <<'PY'
import sys
sys.path.insert(0, "bin")
from testrig_wg_validation import wgfp_counter_snapshot

try:
    print(*wgfp_counter_snapshot(sys.argv[1]))
except (OSError, ValueError) as exc:
    raise SystemExit(f"ERROR: cannot snapshot WGFP counter history: {exc}")
PY
    ) || return 1
    read -r counter_start counter_digest <<< "$checkpoint"
  fi
  "$@" || return $?
  wgfp_check_modes || return 1
  if [ "${WGFP:-0}" = 1 ]; then
    if [ "$1" = bin/testrig-wireguard.sh ]; then
      cell="wg-v4$WG_SUF"; peers=1
    else
      cell="$TUNNELS"; peers=$TUNNELS
      [ "${INNER:-4}" != 6 ] || cell="${cell}v6"
    fi
    python3 - "$WGFP_COUNTER_LOG" "$cell" "$REPS" "$peers" "$DIRS" "$WGFP_RX" \
      "$counter_start" "$counter_digest" <<'PY'
import sys
sys.path.insert(0, "bin")
from testrig_wg_validation import validate_wgfp_counters

_, log, cell, reps, peers, directions, rx, start, digest = sys.argv
expected = {(cell, direction, rep): int(peers) for direction in directions.split()
            for rep in range(1, int(reps) + 1)}
try:
    validate_wgfp_counters(log, expected, rx == "1", int(start), digest)
except (OSError, UnicodeError, KeyError, TypeError, ValueError) as exc:
    raise SystemExit(f"ERROR: matrix validation failed: {exc}")
PY
  fi
}
cleanup() {
  local rc=$? listing handle
  trap - EXIT INT TERM
  if [ "$TABLE_MAY_EXIST" = 1 ]; then
    if listing=$("${SSH[@]}" -n "$DUT" "sudo -n nft -a list table inet $TABLE"); then
      if handle=$(printf '%s\n' "$listing" | awk -v table="$TABLE" -v token="$TABLE_TOKEN" '
        NR == 1 && $1 == "table" && $2 == "inet" && $3 == table {
          for (i = 1; i < NF; i++) if ($i == "handle" && $(i + 1) ~ /^[0-9]+$/) handle = $(i + 1)
        }
        $1 == "comment" && $2 == "\"" token "\"" && NF == 2 { owned = 1 }
        END { if (handle && owned) print handle; else exit 1 }'); then
        if "${SSH[@]}" -n "$DUT" "sudo -n nft delete table inet handle $handle"; then
          rm -- "$run_dir/nft-pending" || rc=1
        else
          rc=1
        fi
      else
        echo "ERROR: nft table ownership changed; refusing to remove $TABLE" >&2
        rc=1
      fi
    else
      echo "ERROR: cannot inspect possibly created nft table $TABLE" >&2
      rc=1
    fi
  fi
  exit "$rc"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

# Fixed interface names and listening ports cannot be shared. The runners
# verify ownership of their uniquely tagged routes, rules and server state.
"${SSH[@]}" "$DUT" sh -s -- "$DUT_KIND" "${WGFP:-0}" "${WG_SKIP_FW4:-0}" <<'REMOTE'
set -eu
kind=$1
admin() { if [ "$kind" = vendor ]; then "$@"; else sudo -n "$@"; fi; }
for cmd in ip awk; do command -v "$cmd" >/dev/null || { echo "Missing $cmd" >&2; exit 1; }; done
admin nft --version >/dev/null || { echo "Cannot run nft on DUT" >&2; exit 1; }
if ip link show wg0 >/dev/null 2>&1; then echo "wg0 already exists on DUT" >&2; exit 1; fi
for k in $(seq 0 15); do
  if ip link show "wgm$k" >/dev/null 2>&1; then echo "wgm$k already exists on DUT" >&2; exit 1; fi
done
for subnet in 200 $(seq 210 225); do
  routes=$(admin ip -4 route show exact "10.99.$subnet.0/24") || exit 1
  [ -z "$routes" ] || { echo "Test route 10.99.$subnet.0/24 already exists on DUT" >&2; exit 1; }
done
routes=$(admin ip -6 route show exact fd99:200::/64) || exit 1
[ -z "$routes" ] || { echo "IPv6 test route already exists on DUT" >&2; exit 1; }
if [ "$kind" = vendor ] && [ "$3" != 1 ]; then
  nft -a list chain inet fw4 forward >/dev/null || { echo "Cannot inspect vendor forward rules" >&2; exit 1; }
fi
if [ "$2" = 1 ]; then
  peer=$(admin cat /sys/module/wireguard/parameters/wgfp_peer) || exit 1
  [ "$peer" = off ] || { echo "WGFP is already bound" >&2; exit 1; }
fi
[ -r /proc/net/udp ] || { echo "Cannot inspect DUT UDP sockets" >&2; exit 1; }
for port in 51820 51830 51831 51832 51833; do
  hex=$(printf '%04X' "$port")
  for file in /proc/net/udp /proc/net/udp6; do
    [ -r "$file" ] || continue
    if awk -v port="$hex" 'NR>1 { split($2,a,":"); if (toupper(a[2])==port) found=1 } END { exit !found }' "$file"; then
      echo "UDP port $port already in use on DUT" >&2; exit 1
    fi
  done
done
REMOTE

# Both tunnel runners use the configured generator targets.
for host in "$GEN1" "$GEN2"; do
  "${SSH[@]}" "$host" sh -s -- "${RIG_NS:--}" "${WG_SKIP_SERVERS:-0}" <<'REMOTE'
set -eu
ns=$1
net() { if [ "$ns" = - ]; then "$@"; else sudo -n ip netns exec "$ns" "$@"; fi; }
if [ "$ns" != - ]; then sudo -n ip netns exec "$ns" true || exit 1; fi
for cmd in ip ss timeout; do command -v "$cmd" >/dev/null || { echo "Missing $cmd" >&2; exit 1; }; done
for n in wg0 $(seq -f wgm%g 0 15); do
  if ip link show "$n" >/dev/null 2>&1 ||
     { [ "$ns" != - ] && net ip link show "$n" >/dev/null 2>&1; }; then
    echo "$n already exists on generator" >&2; exit 1
  fi
done
for subnet in 200 $(seq 210 225); do
  routes=$(net ip -4 route show exact "10.99.$subnet.0/24") || exit 1
  [ -z "$routes" ] || { echo "Test route 10.99.$subnet.0/24 already exists" >&2; exit 1; }
done
routes=$(net ip -6 route show exact fd99:200::/64) || exit 1
[ -z "$routes" ] || { echo "IPv6 test route already exists" >&2; exit 1; }
for port in 51820 51830 51831 51832 51833; do
  sockets=$(net ss -H -lun "sport = :$port") || exit 1
  [ -z "$sockets" ] || { echo "UDP port $port already in use on generator" >&2; exit 1; }
done
if [ "$2" != 1 ]; then
  for port in 5231 5240 5241 5242 5243; do
    sockets=$(net ss -H -ltn "sport = :$port") || exit 1
    [ -z "$sockets" ] || { echo "TCP port $port already in use on generator" >&2; exit 1; }
  done
fi
REMOTE
done

wgfp_check_modes
run_dir=$(mktemp -d "$OUTDIR/$TAG-$WHO-XXXXXXXX")
printf '%s\n' "$WG_GEN_QDISC" > "$run_dir/generator-qdisc.txt"
printf '%s\n' "$WG_PORT" > "$run_dir/encrypted-leg.txt"
export J=${J:-"$run_dir/json"} WG_STATE_PATH=${WG_STATE_PATH:-"$run_dir/state"}
export WG_DUT_STAT=${WG_DUT_STAT:-"$run_dir/dut.stat"}
if [ "${WGFP:-0}" = 1 ]; then export WGFP_COUNTER_LOG=${WGFP_COUNTER_LOG:-"$run_dir/wgfp-counters.tsv"}; fi
mkdir -p "$J"
echo "Per-run artifacts: $run_dir"
echo "Encrypted WireGuard leg: $WG_PORT"
echo "Generator tunnel qdisc: $WG_GEN_QDISC (no explicit iperf rate cap added)"
if [ "$DUT_KIND" = ask2 ]; then
  printf '%s\n%s\n' "$TABLE" "$TABLE_TOKEN" > "$run_dir/nft-pending"
  TABLE_MAY_EXIST=1
  "${SSH[@]}" -n "$DUT" "sudo -n nft 'create table inet $TABLE { comment \"$TABLE_TOKEN\"; }'"
  "${SSH[@]}" -n "$DUT" "sudo -n nft 'add chain inet $TABLE pre { type filter hook prerouting priority -300 ; }' && \
    sudo -n nft 'add chain inet $TABLE out { type filter hook output priority -300 ; }' && \
    sudo -n nft add rule inet $TABLE pre ip saddr $WG_GEN4 ip daddr $WG_DUT4 udp sport 51820-51833 udp dport 51820-51833 notrack && \
    sudo -n nft add rule inet $TABLE out ip saddr $WG_DUT4 ip daddr $WG_GEN4 udp sport 51820-51833 udp dport 51820-51833 notrack && \
    sudo -n nft add rule inet $TABLE pre ip6 saddr $WG_GEN6 ip6 daddr $WG_DUT6 udp sport 51820-51833 udp dport 51820-51833 notrack && \
    sudo -n nft add rule inet $TABLE out ip6 saddr $WG_DUT6 ip6 daddr $WG_GEN6 udp sport 51820-51833 udp dport 51820-51833 notrack"
fi
echo "##### $WHO: 1 tunnel, 4 streams, $REPS1 reps with a fresh tunnel each"
INNER=4 REPS=$REPS1 RECREATE=1 DIRS="up down bidir" STREAMS=4 OUT=$run_dir/t1.csv run_checked bin/testrig-wireguard.sh run wg-v4
for A in 2 4; do
  echo "##### $WHO: $A tunnels, 2 streams each, $REPSN reps"
  INNER=4 REPS=$REPSN DIRS="up down bidir" TUNNELS=$A ACTIVE=$A OUT=$run_dir/t$A.csv run_checked bin/testrig-wireguard-multi.sh run
done
# WG_INNER6=1 (default on the ASK2 DUT): the same 2/4-tunnel cells with IPv6 inside the tunnels (outer stays IPv4)
if [ "$WG_INNER6" = 1 ]; then
  for A in 2 4; do
    echo "##### $WHO: $A tunnels, IPv6 inner, 2 streams each, $REPSN reps"
    INNER=6 REPS=$REPSN DIRS="up down bidir" TUNNELS=$A ACTIVE=$A OUT=$run_dir/t${A}v6.csv run_checked bin/testrig-wireguard-multi.sh run
  done
fi
python3 - "$run_dir" "$REPS1" "$REPSN" "${WGFP:-0}" "${WGFP_COUNTER_LOG:-}" \
  "$J" "$DUR" "$STEADY_FROM" "$WGFP_RX" "$WG_INNER6" <<'PY'
import collections
import csv
import math
import os
import statistics
import sys
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, "bin")
from testrig_wg_validation import iperf_rate, validate_wgfp_counters

(out, reps1, repsn, wgfp, counter_file, json_dir, duration, steady_from,
 wgfp_rx, inner6) = sys.argv[1:]
duration, steady_from = int(duration), int(steady_from)
directions = ("up", "down", "bidir")
summary = {}
expected = [(4, 1, int(reps1)), (4, 2, int(repsn)), (4, 4, int(repsn))]
if inner6 == "1":
    expected += [(6, 2, int(repsn)), (6, 4, int(repsn))]

try:
    counter_expected = {}
    for family, tunnels, reps in expected:
        suffix = "v6" if family == 6 else ""
        path = Path(out) / f"t{tunnels}{suffix}.csv"
        with path.open(newline="", encoding="utf-8") as file:
            rows = list(csv.DictReader(file))
        values = collections.defaultdict(list)
        for row in rows:
            if tunnels == 1:
                if row["cell"] != f"wg-v4{os.environ.get('WG_SUF', '')}":
                    raise ValueError(f"{path}: unexpected cell identity")
            elif row["tunnels"] != str(tunnels) or row["active"] != str(tunnels):
                raise ValueError(f"{path}: unexpected tunnel identity")
            direction = row["dir"]
            rate, rep = float(row["gbps"]), int(row["rep"])
            if direction not in directions or not math.isfinite(rate) or rate < 0:
                raise ValueError(f"{path}: invalid measurement {direction} r{rep}: {rate}")
            prefix = "wgm6" if family == 6 else "wgm"
            names = ([f"wg-v4{os.environ.get('WG_SUF', '')}-{direction}-r{rep}.json"] if tunnels == 1 else
                     [f"{prefix}-{direction}-n{tunnels}-k{k}-r{rep}.json" for k in range(tunnels)])
            measured = sum(iperf_rate(Path(json_dir) / name, direction, duration, steady_from) for name in names)
            precision = max(0, -Decimal(row["gbps"]).as_tuple().exponent)
            tolerance = max(1e-5, 0.500001 * 10 ** -precision)
            if abs(measured - rate) > tolerance:
                raise ValueError(f"{path}: {direction} r{rep} differs from its iperf JSON")
            values[direction].append((rep, rate))
        for direction in directions:
            samples = values[direction]
            if sorted(rep for rep, _ in samples) != list(range(1, reps + 1)):
                raise ValueError(f"{path}: missing or duplicate {direction} repetitions")
            summary[(family, tunnels, direction)] = [rate for _, rate in samples]
            cell = f"wg-v4{os.environ.get('WG_SUF', '')}" if tunnels == 1 else f"{tunnels}{suffix}"
            for rep in range(1, reps + 1):
                counter_expected[(cell, direction, rep)] = tunnels

    if wgfp == "1":
        validate_wgfp_counters(counter_file, counter_expected, wgfp_rx == "1")
except (OSError, UnicodeError, KeyError, TypeError, ValueError) as exc:
    raise SystemExit(f"ERROR: matrix validation failed: {exc}")

print("\nMedian Gbit/s:")
for tunnels in (1, 2, 4):
    print(f"{tunnels}T " + " ".join(
        f"{d}={statistics.median(summary[(4, tunnels, d)]):.3f}" for d in directions))
if wgfp == "1":
    print(f"Per-cell WGFP counters: {counter_file}")
if inner6 == "1":
    print("\nIPv6 inner, fastest / median Gbit/s:")
    for tunnels in (2, 4):
        cells = []
        for direction in directions:
            values = summary[(6, tunnels, direction)]
            cells.append(f"{direction}={max(values):.3f}/{statistics.median(values):.3f}")
        print(f"{tunnels}T v6 " + " ".join(cells))
PY
results=(t1 t2 t4)
if [ "$WG_INNER6" = 1 ]; then results+=(t2v6 t4v6); fi
for result in "${results[@]}"; do
  published="$OUTDIR/$TAG-$WHO-$result.csv"
  if ! ln -T -- "$run_dir/$result.csv" "$published"; then
    echo "ERROR: cannot publish $published without overwriting an existing file" >&2
    exit 1
  fi
done
echo "##### $WHO done"
