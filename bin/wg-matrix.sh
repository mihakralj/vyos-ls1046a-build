#!/usr/bin/env bash
# wg-matrix.sh — full WireGuard throughput matrix for one DUT, then the median table.
#   1 tunnel : bin/testrig-wireguard.sh        (4 TCP streams, a NEW tunnel every rep so serial-worker placement is sampled)
#   2, 4 tunnels : bin/testrig-wireguard-multi.sh (2 TCP streams per tunnel, one tunnel set per row)
#   each x up / down / bidir, IPv4 inner, MTU 1420, 30 s runs (first 10 s discarded)
# Usage: bin/wg-matrix.sh 70|106|185   Env: REPS1=5 REPSN=3 DUR=30 STEADY_FROM=10 OUTDIR=/mnt/builds/ask2-review/oracle
# The rig creates tunnels outside VyOS config. On .185, a temporary nft table
# untracks only this rig's outer UDP traffic; existing VyOS configuration is untouched.
# The current .70 has no fw4 forward chain; use WG_SKIP_FW4=1 only if its
# existing forwarding policy already permits the test traffic.
set -euo pipefail
cd "$(dirname "$0")/.."
. bin/testrig-env.sh
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
if [ "$DUT_KIND" = vendor ]; then export RIG_NS=${RIG_NS:-$VENDOR_NS}; else export RIG_NS=${RIG_NS:-}; fi
if [ -n "$RIG_NS" ] && ! [[ $RIG_NS =~ ^[a-zA-Z0-9_.-]+$ ]]; then
  echo "Invalid RIG_NS" >&2; exit 2
fi
if [ "${WG_PORT:-eth4}" != eth4 ] || [ "${OUTER:-4}" != 4 ]; then
  echo "The matrix requires WG_PORT=eth4 and OUTER=4 for both runners" >&2; exit 2
fi
if [ "$DUT_KIND" = vendor ] && [ "${WGFP:-0}" = 1 ]; then
  echo "WGFP is available only on the ASK2 DUT" >&2; exit 2
fi
# WGFP_RX=1: P3 receive offload on for the run (it defaults off); down/bidir must then show
# direct-RX frames. The DUT's previous setting is restored on exit.
WGFP_RX=${WGFP_RX:-0}
case $WGFP_RX in 0|1) ;; *) echo "WGFP_RX must be 0 or 1" >&2; exit 2 ;; esac
if [ "$WGFP_RX" = 1 ] && [ "${WGFP:-0}" != 1 ]; then
  echo "WGFP_RX=1 needs WGFP=1" >&2; exit 2
fi
# WGFP_RX_FAST=0|1|2: P6 in-place decrypt (wireguard.wgfp_rx_fast) for the run; unset leaves the
# DUT's setting alone. Needs WGFP_RX=1. Restored on exit.
WGFP_RX_FAST=${WGFP_RX_FAST:-}
case $WGFP_RX_FAST in ''|0|1|2) ;; *) echo "WGFP_RX_FAST must be 0, 1 or 2" >&2; exit 2 ;; esac
if [ -n "$WGFP_RX_FAST" ] && [ "$WGFP_RX" != 1 ]; then
  echo "WGFP_RX_FAST needs WGFP_RX=1" >&2; exit 2
fi
if [ "$DUT_KIND" = ask2 ] && [ "${WG_NOTRACK:-0}" != 0 ]; then
  echo "The ASK2 matrix already applies scoped NOTRACK; leave WG_NOTRACK unset" >&2; exit 2
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
RX_ORIG=
RXP=/sys/module/wireguard/parameters/wgfp_rx
FAST_ORIG=
FASTP=/sys/module/wireguard/parameters/wgfp_rx_fast
cleanup() {
  local rc=$? listing handle
  trap - EXIT INT TERM
  if [ -n "$FAST_ORIG" ]; then
    "${SSH[@]}" -n "$DUT" "echo $FAST_ORIG | sudo -n tee $FASTP >/dev/null" ||
      { echo "ERROR: cannot restore $FASTP=$FAST_ORIG" >&2; rc=1; }
  fi
  if [ -n "$RX_ORIG" ]; then
    "${SSH[@]}" -n "$DUT" "echo $RX_ORIG | sudo -n tee $RXP >/dev/null" ||
      { echo "ERROR: cannot restore $RXP=$RX_ORIG" >&2; rc=1; }
  fi
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

run_dir=$(mktemp -d "$OUTDIR/$TAG-$WHO-XXXXXXXX")
export J=${J:-"$run_dir/json"} WG_STATE_PATH=${WG_STATE_PATH:-"$run_dir/state"}
export WG_DUT_STAT=${WG_DUT_STAT:-"$run_dir/dut.stat"}
if [ "${WGFP:-0}" = 1 ]; then export WGFP_COUNTER_LOG=${WGFP_COUNTER_LOG:-"$run_dir/wgfp-counters.tsv"}; fi
mkdir -p "$J"
echo "Per-run artifacts: $run_dir"
if [ "$DUT_KIND" = ask2 ]; then
  printf '%s\n%s\n' "$TABLE" "$TABLE_TOKEN" > "$run_dir/nft-pending"
  TABLE_MAY_EXIST=1
  "${SSH[@]}" -n "$DUT" "sudo -n nft 'create table inet $TABLE { comment \"$TABLE_TOKEN\"; }'"
  "${SSH[@]}" -n "$DUT" "sudo -n nft 'add chain inet $TABLE pre { type filter hook prerouting priority -300 ; }' && \
    sudo -n nft 'add chain inet $TABLE out { type filter hook output priority -300 ; }' && \
    sudo -n nft add rule inet $TABLE pre ip saddr 10.99.2.113 ip daddr 10.99.2.185 udp sport 51820-51833 udp dport 51820-51833 notrack && \
    sudo -n nft add rule inet $TABLE out ip saddr 10.99.2.185 ip daddr 10.99.2.113 udp sport 51820-51833 udp dport 51820-51833 notrack"
fi
if [ "$DUT_KIND" = ask2 ] && [ "${WGFP:-0}" = 1 ]; then
  RX_ORIG=$("${SSH[@]}" -n "$DUT" "sudo -n cat $RXP") || exit 1
  "${SSH[@]}" -n "$DUT" "echo $WGFP_RX | sudo -n tee $RXP >/dev/null" || exit 1
  echo "wgfp_rx: $RX_ORIG -> $WGFP_RX"
  if [ -n "$WGFP_RX_FAST" ]; then
    FAST_ORIG=$("${SSH[@]}" -n "$DUT" "sudo -n cat $FASTP") ||
      { echo "ERROR: this image has no $FASTP (P6)" >&2; exit 1; }
    "${SSH[@]}" -n "$DUT" "echo $WGFP_RX_FAST | sudo -n tee $FASTP >/dev/null" || exit 1
    echo "wgfp_rx_fast: $FAST_ORIG -> $WGFP_RX_FAST"
  fi
fi
echo "##### $WHO: 1 tunnel, 4 streams, $REPS1 reps with a fresh tunnel each"
REPS=$REPS1 RECREATE=1 DIRS="up down bidir" STREAMS=4 OUT=$OUTDIR/$TAG-$WHO-t1.csv bin/testrig-wireguard.sh run wg-v4
for A in 2 4; do
  echo "##### $WHO: $A tunnels, 2 streams each, $REPSN reps"
  REPS=$REPSN DIRS="up down bidir" TUNNELS=$A ACTIVE=$A OUT=$OUTDIR/$TAG-$WHO-t$A.csv bin/testrig-wireguard-multi.sh run
done
# WG_INNER6=1 (default on the ASK2 DUT): the same 2/4-tunnel cells with IPv6 inside the tunnels (outer stays IPv4)
[ "$DUT_KIND" = ask2 ] && WG_INNER6=${WG_INNER6:-1} || WG_INNER6=${WG_INNER6:-0}
if [ "$WG_INNER6" = 1 ]; then
  for A in 2 4; do
    echo "##### $WHO: $A tunnels, IPv6 inner, 2 streams each, $REPSN reps"
    INNER=6 REPS=$REPSN DIRS="up down bidir" TUNNELS=$A ACTIVE=$A OUT=$OUTDIR/$TAG-$WHO-t${A}v6.csv bin/testrig-wireguard-multi.sh run
  done
fi
python3 - "$OUTDIR" "$TAG" "$WHO" "$REPS1" "$REPSN" "${WGFP:-0}" "${WGFP_COUNTER_LOG:-}" "$J" "$DUR" "$STEADY_FROM" "$WGFP_RX" <<'PY'
import collections
import csv
import json
import math
import statistics
import sys
from decimal import Decimal
from pathlib import Path

out, tag, who, reps1, repsn, wgfp, counter_file, json_dir, duration, steady_from, wgfp_rx = sys.argv[1:]
duration, steady_from = int(duration), int(steady_from)
directions = ("up", "down", "bidir")
summary = {}
expected = {1: int(reps1), 2: int(repsn), 4: int(repsn)}

def iperf_rate(path, direction):
    data = json.loads(path.read_text())
    if data.get("error"):
        raise ValueError(f"{path}: {data['error']}")
    end = data["end"]
    fields = ["sum_sent", "sum_received"]
    if direction == "bidir":
        fields += ["sum_sent_bidir_reverse", "sum_received_bidir_reverse"]
    tolerance = max(1.0, duration * 0.05)
    for field in fields:
        seconds = float(end[field]["seconds"])
        rate = float(end[field]["bits_per_second"])
        if (not math.isfinite(seconds) or not duration - tolerance <= seconds <= duration + tolerance
                or not math.isfinite(rate) or rate <= 0):
            raise ValueError(f"{path}: incomplete or invalid {field}")
    previous = 0.0
    steady = []
    for interval in data["intervals"]:
        sample = interval["sum"]
        start, stop, seconds, rate = (float(sample[key]) for key in
                                      ("start", "end", "seconds", "bits_per_second"))
        if (not all(math.isfinite(x) for x in (start, stop, seconds, rate))
                or abs(start - previous) > 0.05 or not 0 < seconds <= 1.55
                or abs(stop - start - seconds) > 0.05 or rate < 0
                or sample.get("omitted", False)):
            raise ValueError(f"{path}: invalid or incomplete interval")
        if direction == "bidir":
            reverse = interval["sum_bidir_reverse"]
            reverse_rate = float(reverse["bits_per_second"])
            reverse_start, reverse_stop = float(reverse["start"]), float(reverse["end"])
            if (not all(math.isfinite(x) for x in (reverse_rate, reverse_start, reverse_stop))
                    or reverse_rate < 0 or abs(reverse_start - start) > 0.05
                    or abs(reverse_stop - stop) > 0.05
                    or reverse.get("omitted", False)):
                raise ValueError(f"{path}: invalid reverse interval")
            rate += reverse_rate
        if start >= steady_from - 0.01:
            steady.append(rate)
        previous = stop
    if previous < duration - tolerance or not steady:
        raise ValueError(f"{path}: incomplete steady window")
    return sum(steady) / len(steady) / 1e9

try:
    for tunnels, reps in expected.items():
        path = Path(out) / f"{tag}-{who}-t{tunnels}.csv"
        rows = list(csv.DictReader(path.open()))
        values = collections.defaultdict(list)
        for row in rows:
            direction = row["dir"]
            rate, rep = float(row["gbps"]), int(row["rep"])
            if direction not in directions or not math.isfinite(rate) or rate <= 0:
                raise ValueError(f"{path}: invalid measurement {direction} r{rep}: {rate}")
            names = ([f"wg-v4-{direction}-r{rep}.json"] if tunnels == 1 else
                     [f"wgm-{direction}-n{tunnels}-k{k}-r{rep}.json" for k in range(tunnels)])
            measured = sum(iperf_rate(Path(json_dir) / name, direction) for name in names)
            precision = max(0, -Decimal(row["gbps"]).as_tuple().exponent)
            tolerance = max(1e-5, 0.500001 * 10 ** -precision)
            if abs(measured - rate) > tolerance:
                raise ValueError(f"{path}: {direction} r{rep} differs from its iperf JSON")
            values[direction].append((rep, rate))
        for direction in directions:
            samples = values[direction]
            if sorted(rep for rep, _ in samples) != list(range(1, reps + 1)):
                raise ValueError(f"{path}: missing or duplicate {direction} repetitions")
            summary[(tunnels, direction)] = statistics.median(rate for _, rate in samples)

    if wgfp == "1":
        samples = collections.defaultdict(dict)
        def parse_counters(text, required):
            counters = {}
            for field in text.split():
                key, sep, value = field.partition("=")
                if key not in required:
                    continue
                if key in counters or not sep or not value.isascii() or not value.isdecimal():
                    raise ValueError(f"invalid or duplicate WGFP counter {key}")
                counters[key] = int(value)
            if required - counters.keys():
                raise ValueError(f"missing WGFP counters: {sorted(required - counters.keys())}")
            return counters

        def parse_sinks(text, expected):
            sinks = {}
            for line in (text.split("|") if text else []):
                fields = line.split()
                if not fields or not fields[0].startswith("sink") or not fields[0][4:].isascii() \
                        or not fields[0][4:].isdecimal() or fields[0] in sinks:
                    raise ValueError("invalid or duplicate WGFP sink")
                fqids = [field[5:] for field in fields[1:] if field.startswith("fqid=")]
                if (len(fqids) != 1 or not fqids[0].startswith("0x") or len(fqids[0]) <= 2
                        or any(c not in "0123456789abcdefABCDEF" for c in fqids[0][2:])):
                    raise ValueError(f"{fields[0]}: invalid or duplicate FQID")
                counters = parse_counters(" ".join(fields[1:]), {"frames", "errs", "sg"})
                sinks[fields[0]] = (int(fqids[0], 16), counters)
            if len(sinks) != expected or len({v[0] for v in sinks.values()}) != expected:
                raise ValueError("incomplete or duplicate WGFP sink FQIDs")
            return sinks

        for line in Path(counter_file).read_text().splitlines():
            cell, direction, rep, phase, attempt, state, qman, sink_data = line.split("\t")
            counters = parse_counters(state, {"sealed", "rx", "peers", "install_fail"})
            # P6 (wgfp_rx_fast) consumes sink frames before the P3 copy: they are received too
            optional = dict(f.partition("=")[::2] for f in state.split() if "=" in f)
            counters["rx"] += sum(int(optional[k]) for k in ("p6_fwd", "p6_stack")
                                  if optional.get(k, "").isdecimal())
            counters.update(parse_counters(qman, {"sinks", "xmit_ok", "xmit_fail", "xmit_ern"}))
            counters["sink_data"] = parse_sinks(sink_data, counters["sinks"])
            key = (cell, direction, int(rep), int(attempt))
            if phase not in ("before", "after") or phase in samples[key]:
                raise ValueError(f"{cell} {direction} r{rep}: invalid or duplicate counter phase")
            samples[key][phase] = counters
        for tunnels, reps in expected.items():
            cell = "wg-v4" if tunnels == 1 else str(tunnels)
            for direction in directions:
                for rep in range(1, reps + 1):
                    attempts = [key for key, pair in samples.items()
                                if key[:3] == (cell, direction, rep) and set(pair) == {"before", "after"}]
                    if not attempts:
                        raise ValueError(f"{cell} {direction} r{rep}: missing WGFP counter pair")
                    pair = samples[max(attempts)]
                    before, after = pair["before"], pair["after"]
                    # P3 receive offload defaults off: unless the run asked for it (WGFP_RX=1),
                    # the direct-RX counter must stay still; with it, down/bidir must move it.
                    if direction != "down" and after["sealed"] <= before["sealed"]:
                        raise ValueError(f"{cell} {direction} r{rep}: fast-path counters did not advance")
                    if wgfp_rx != "1" and after["rx"] != before["rx"]:
                        raise ValueError(f"{cell} {direction} r{rep}: P3 receive counter advanced")
                    if wgfp_rx == "1" and direction != "up" and after["rx"] <= before["rx"]:
                        raise ValueError(f"{cell} {direction} r{rep}: P3 receive counter did not advance")
                    if after["install_fail"] != before["install_fail"] or after["peers"] < tunnels:
                        raise ValueError(f"{cell} {direction} r{rep}: installation or peer count failed")
                    if direction != "down" and after["xmit_ok"] <= before["xmit_ok"]:
                        raise ValueError(f"{cell} {direction} r{rep}: QMan submissions did not advance")
                    if any(after[name] != before[name] for name in ("xmit_fail", "xmit_ern")):
                        raise ValueError(f"{cell} {direction} r{rep}: QMan failures or rejections changed")
                    old_sinks, new_sinks = before["sink_data"], after["sink_data"]
                    # per-port sinks are created with a port's first record, so sinks may
                    # appear during a run; one that vanishes or changes FQID is still an error
                    if (not old_sinks.keys() <= new_sinks.keys()
                            or any(old_sinks[name][0] != new_sinks[name][0] for name in old_sinks)):
                        raise ValueError(f"{cell} {direction} r{rep}: sink FQ identities changed")
                    deltas = []
                    zero = {"frames": 0, "errs": 0, "sg": 0}
                    for name in new_sinks:
                        old, new = old_sinks.get(name, (0, zero))[1], new_sinks[name][1]
                        if new["frames"] < old["frames"]:
                            raise ValueError(f"{cell} {direction} r{rep}: sink frame counter decreased")
                        if any(new[field] != old[field] for field in ("errs", "sg")):
                            raise ValueError(f"{cell} {direction} r{rep}: sink errors or SG drops changed")
                        deltas.append(new["frames"] - old["frames"])
                    if not any(deltas):
                        raise ValueError(f"{cell} {direction} r{rep}: sink frame counters did not advance")
except (OSError, KeyError, TypeError, ValueError) as exc:
    raise SystemExit(f"ERROR: matrix validation failed: {exc}")

print("\nMedian Gbit/s:")
for tunnels in expected:
    print(f"{tunnels}T " + " ".join(f"{d}={summary[(tunnels, d)]:.3f}" for d in directions))
if wgfp == "1":
    print(f"Per-cell WGFP counters: {counter_file}")
PY
if [ "$WG_INNER6" = 1 ]; then
  python3 - "$OUTDIR/$TAG-$WHO" <<'PY6'
import csv, statistics, sys
print("\nIPv6 inner, fastest / median Gbit/s:")
for a in ("2", "4"):
    rows = list(csv.DictReader(open(f"{sys.argv[1]}-t{a}v6.csv")))
    cells = []
    for d in ("up", "down", "bidir"):
        v = [float(r["gbps"]) for r in rows if r["dir"] == d and float(r["gbps"]) > 0]
        cells.append(f"{d}={max(v):.3f}/{statistics.median(v):.3f}" if v else f"{d}=-")
    print(f"{a}T v6 " + " ".join(cells))
PY6
fi
echo "##### $WHO done"
