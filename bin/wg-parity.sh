#!/usr/bin/env bash
# WireGuard parity battery: one tunnel, selected ports, inner families and directions.
# The runners own their scoped NOTRACK rules and verify WGFP counters per cell.
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
. "$HERE/testrig-env.sh"
if [ "${RIG_PARITY:-0}" = 1 ]; then . "$HERE/testrig-parity-profile.sh"; fi
. "$HERE/testrig-wgfp-modes.sh"

WHO=${1:?usage: wg-parity.sh vendor|ask2}
case $WHO in
  vendor|106) WHO=106; export DUT_KIND=vendor ;;
  ask2|185) WHO=185; export DUT_KIND=ask2 ;;
  *) echo "vendor or ask2" >&2; exit 2 ;;
esac
rig_require GEN1
[ "${RUN_ON:-agent}" = dell2 ] || rig_require GEN2
DUT=${DUT:-$(rig_dut "$DUT_KIND")}
export DUT
REPS=${REPS:-3}; PORTS=${PORTS:-"eth3 eth4"}
export DUR=${DUR:-30} STEADY_FROM=${STEADY_FROM:-10} RUN_ON=${RUN_ON:-agent}
OUTDIR=${OUTDIR:-$RIG_OUT}; TAG=${TAG:-wgpar}; OUTER=${OUTER:-4}
export OUTDIR OUTER
if ! [[ $TAG =~ ^[a-zA-Z0-9._-]+$ ]] || [ "$TAG" = . ] || [ "$TAG" = .. ]; then
  echo "TAG must be a simple filename component" >&2; exit 2
fi
case $OUTER in 4|6) ;; *) echo "OUTER must be 4 or 6" >&2; exit 2 ;; esac
if ! [[ $REPS =~ ^[1-9][0-9]*$ ]]; then echo "REPS must be positive" >&2; exit 2; fi
if ! [[ $DUR =~ ^[1-9][0-9]*$ && $STEADY_FROM =~ ^(0|[1-9][0-9]*)$ ]] ||
   (( DUR <= STEADY_FROM + 2 )); then
  echo "DUR must exceed STEADY_FROM by at least 3 seconds" >&2; exit 2
fi
if [ "${KEEP:-0}" = 1 ]; then echo "KEEP=1 is not supported by parity" >&2; exit 2; fi
if [ "$DUT_KIND" = vendor ] && [ "${WGFP:-0}" = 1 ]; then
  echo "WGFP is available only on the ASK2 DUT" >&2; exit 2
fi
wgfp_validate_modes || exit 2
case ${WG_NOTRACK:-0} in
  0|3) ;;
  *) echo "Parity requires peer-scoped NOTRACK; leave WG_NOTRACK unset" >&2; exit 2 ;;
esac
if [ "$DUT_KIND" = ask2 ]; then
  export WG_NOTRACK=3
fi
if [ "${WGFP:-0}" != 1 ] && [ -n "${WGFP_COUNTER_LOG:-}" ]; then
  echo "WGFP_COUNTER_LOG requires WGFP=1" >&2; exit 2
fi
if [[ $PORTS == *$'\n'* ]]; then echo "Invalid PORTS" >&2; exit 2; fi
read -r -a ports <<< "$PORTS"
[ "${#ports[@]}" -gt 0 ] || { echo "PORTS must not be empty" >&2; exit 2; }
seen=" "
for P in "${ports[@]}"; do
  case $P in eth3|eth4) ;; *) echo "PORTS must contain only eth3 or eth4" >&2; exit 2 ;; esac
  case $seen in *" $P "*) echo "Duplicate parity port: $P" >&2; exit 2 ;; esac
  seen="$seen$P "
  path="$OUTDIR/$TAG-$WHO-$P.csv"
  if [ -e "$path" ] || [ -L "$path" ]; then echo "Refusing to overwrite $path" >&2; exit 2; fi
done
read -r -a cells <<< "${CELLS:-wg-v4 wg-v6}"
[ "${#cells[@]}" -gt 0 ] || { echo "CELLS must not be empty" >&2; exit 2; }
if [[ ${CELLS:-} == *$'\n'* ]]; then echo "Invalid CELLS" >&2; exit 2; fi
seen=" "
for cell in "${cells[@]}"; do
  case $cell in wg-v4|wg-v6) ;; *) echo "Invalid parity cell: $cell" >&2; exit 2 ;; esac
  case $seen in *" $cell "*) echo "Duplicate parity cell: $cell" >&2; exit 2 ;; esac
  seen="$seen$cell "
done
DIRS=${DIRS:-"up down bidir"}
if [[ $DIRS == *$'\n'* ]]; then echo "Invalid DIRS" >&2; exit 2; fi
read -r -a directions <<< "$DIRS"
[ "${#directions[@]}" -gt 0 ] || { echo "DIRS must not be empty" >&2; exit 2; }
seen=" "
for direction in "${directions[@]}"; do
  case $direction in up|down|bidir) ;; *) echo "Invalid direction: $direction" >&2; exit 2 ;; esac
  case $seen in *" $direction "*) echo "Duplicate parity direction: $direction" >&2; exit 2 ;; esac
  seen="$seen$direction "
done
[ "${MODE:-tcp}" = tcp ] || { echo "Parity requires MODE=tcp" >&2; exit 2; }
for path in "${J:-}" "${WG_STATE_PATH:-}" "${WG_DUT_STAT:-}"; do
  [ -n "$path" ] || continue
  if [ -e "$path" ] || [ -L "$path" ]; then echo "Refusing to overwrite $path" >&2; exit 2; fi
done
SSH=(ssh -o BatchMode=yes -o ConnectTimeout=8)
if [ -n "$RIG_KEY" ]; then SSH+=(-i "$RIG_KEY"); fi
wgfp_mode_read() {
  "${SSH[@]}" -n "$DUT" "sudo -n cat /sys/module/wireguard/parameters/$1"
}
wgfp_check_modes
mkdir -p "$OUTDIR"
run_dir=$(mktemp -d "$OUTDIR/$TAG-$WHO-run-XXXXXXXX")
export J=${J:-"$run_dir/json"} WG_STATE_PATH=${WG_STATE_PATH:-"$run_dir/state"}
export WG_DUT_STAT=${WG_DUT_STAT:-"$run_dir/dut.stat"}
echo "Per-run artifacts: $run_dir"
if [ "${WGFP:-0}" = 1 ]; then
  WGFP_COUNTER_LOG=${WGFP_COUNTER_LOG:-"$run_dir/wgfp-counters.tsv"}
  export WGFP_COUNTER_LOG
  echo "WGFP counter log: $WGFP_COUNTER_LOG"
fi

for P in "${ports[@]}"; do
  echo "##### $WHO: tunnel on $P, 1 tunnel, ${STREAMS:-4} streams, $REPS reps with a fresh tunnel each, $(date +%T)"
  wgfp_check_modes
  out="$run_dir/$P.csv"
  published="$OUTDIR/$TAG-$WHO-$P.csv"
  counter_start=0; counter_digest=""
  if [ "${WGFP:-0}" = 1 ]; then
    checkpoint=$(python3 - "$HERE" "$WGFP_COUNTER_LOG" <<'PY'
import sys
sys.path.insert(0, sys.argv[1])
from testrig_wg_validation import wgfp_counter_snapshot

try:
    print(*wgfp_counter_snapshot(sys.argv[2]))
except (OSError, ValueError) as exc:
    raise SystemExit(f"ERROR: cannot snapshot WGFP counter history: {exc}")
PY
    ) || exit 1
    read -r counter_start counter_digest <<< "$checkpoint"
  fi
  if WG_PORT=$P REPS=$REPS RECREATE=1 DIRS="$DIRS" STREAMS=${STREAMS:-4} \
    OUT="$out" "$HERE/testrig-wireguard.sh" run "${cells[@]}"; then
    :
  else
    rc=$?
    echo "ERROR: WireGuard parity runner failed on $P" >&2
    exit "$rc"
  fi
  wgfp_check_modes
  python3 - "$HERE" "$out" "$J" "$REPS" "$DUR" "$STEADY_FROM" "$P" "$DIRS" \
    "${WGFP:-0}" "$WGFP_RX" "${WGFP_COUNTER_LOG:-}" "$counter_start" "$counter_digest" "${cells[@]}" <<'PY'
import csv
import math
import sys
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, sys.argv[1])
from testrig_wg_validation import iperf_rate, validate_wgfp_counters

(_, _, out, json_dir, repetitions, duration, steady_from, port, directions,
 wgfp, wgfp_rx, counter_file, counter_start, counter_digest, *cells) = sys.argv
repetitions, duration, steady_from = int(repetitions), int(duration), int(steady_from)
suffix = "-eth3" if port == "eth3" else ""
expected = {(cell + suffix, direction, rep) for cell in cells for direction in directions.split()
            for rep in range(1, repetitions + 1)}
seen = set()
try:
    with open(out, newline="", encoding="utf-8") as file:
        for row in csv.DictReader(file):
            cell, direction, rep = row["cell"], row["dir"], int(row["rep"])
            key = (cell, direction, rep)
            rate = float(row["gbps"])
            if key not in expected or key in seen or not math.isfinite(rate) or rate < 0:
                raise ValueError(f"{out}: invalid or duplicate measurement {key}")
            path = Path(json_dir) / f"{cell}-{direction}-r{rep}.json"
            measured = iperf_rate(path, direction, duration, steady_from)
            precision = max(0, -Decimal(row["gbps"]).as_tuple().exponent)
            if abs(measured - rate) > max(1e-5, 0.500001 * 10 ** -precision):
                raise ValueError(f"{out}: {key} differs from its iperf JSON")
            seen.add(key)
    if seen != expected:
        raise ValueError(f"{out}: missing requested measurements: {sorted(expected - seen)}")
    if wgfp == "1":
        validate_wgfp_counters(counter_file, {key: 1 for key in expected}, wgfp_rx == "1",
                               int(counter_start), counter_digest)
except (OSError, UnicodeError, KeyError, TypeError, ValueError) as exc:
    raise SystemExit(f"ERROR: parity validation failed: {exc}")
PY
  if ! ln -T -- "$out" "$published"; then
    echo "ERROR: could not publish parity results without overwriting $published" >&2
    exit 1
  fi
  echo "Validated results: $published"
done
echo "##### $WHO done $(date +%T)"
