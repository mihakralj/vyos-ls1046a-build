#!/usr/bin/env bash
# Paired bidirectional battery: run on gen2; alternate the two DUTs per cell and reverse their order each round.
# Usage: REPS=5 bin/testrig-parity-bidir.sh nat-v6 vlan-v4
# Env: REPS TAG RUN_DIR VENDOR_KIND (vendor|opnsense) DUR STEADY_FROM STREAMS; hosts per bin/testrig-env.sh.
# Result files are labelled ask2-* and vendor-*.
set -euo pipefail

HERE=$(cd "$(dirname "$0")" && pwd)
. "$HERE/testrig-env.sh"
rig_require GEN1 ASK2_DUT VENDOR_DUT
VENDOR_KIND=${VENDOR_KIND:-vendor}
REPS=${REPS:-3}
TAG=${TAG:-$(date -u +%Y%m%dT%H%M%SZ)}
RUN_DIR=${RUN_DIR:-$RIG_OUT/paired-bidir-$TAG}
SSH=(ssh -n -o BatchMode=yes -o ConnectTimeout=8 $RIG_SSHK)

fail(){ echo "ERROR: $*" >&2; exit 1; }
[[ "$REPS" =~ ^[1-9][0-9]*$ ]] || fail "REPS must be a positive integer"
(( $# > 0 )) || fail "name at least one cell"
ip link show "$GEN_IF" >/dev/null 2>&1 || fail "run this script on gen2 (no $GEN_IF here)"
[[ -z "$RIG_KEY" || -r "$RIG_KEY" ]] || fail "cannot read RIG_KEY $RIG_KEY"
[[ ! -e "$RUN_DIR" ]] || fail "result directory already exists: $RUN_DIR"
mkdir -p "$RIG_OUT"
exec 9>"$RIG_OUT/parity-bidir.lock"
flock -n 9 || fail "another paired battery is running"
if pgrep -f '[b]ash (.*/)?testrig-offload-quick.sh' >/dev/null; then
    fail "another quick harness is running; do not overlap rig traffic"
fi

config=$("${SSH[@]}" "$ASK2_DUT" '/bin/vbash -ic "show configuration commands"')
if grep -E "^set interfaces ethernet eth[1-4] address '?dhcp(v6)?'?$" <<<"$config"; then
    fail "ASK2 traffic/unused ports still have DHCP configured"
fi
"${SSH[@]}" "$ASK2_DUT" '
for e in 1 2 3 4; do
    state=$(systemctl is-active dhclient@eth$e)
    [ "$state" = inactive ] || { echo "eth$e dhclient state: $state" >&2; exit 1; }
done'
mkdir -p "$RUN_DIR"
for board in ask2 vendor; do
    dut=$ASK2_DUT
    [[ "$board" = ask2 ]] || dut=$VENDOR_DUT
    if [[ "$board" = vendor && "$VENDOR_KIND" = opnsense ]]; then   # FreeBSD: no sysfs RPS knobs
        "${SSH[@]}" "$dut" 'uname -a; ifconfig dtsec3; ifconfig dtsec4' > "$RUN_DIR/controls-$board.txt"
        continue
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
done
export RUN_ON=dell2 BIDIR=1
export DUR=${DUR:-30} STEADY_FROM=${STEADY_FROM:-10} STREAMS=${STREAMS:-8}

for ((rep=1; rep<=REPS; rep++)); do
    boards=(ask2 vendor)
    (( rep % 2 == 1 )) || boards=(vendor ask2)
    for cell in "$@"; do
        for board in "${boards[@]}"; do
            kind=ask2; dut=$ASK2_DUT; ns=
            if [[ "$board" = vendor ]]; then
                kind=$VENDOR_KIND; dut=$VENDOR_DUT; ns=$VENDOR_NS
            fi
            stem=$RUN_DIR/$board-$cell-r$rep
            echo "##### $(date -u +%FT%TZ) board=$board cell=$cell rep=$rep"
            DUT_KIND=$kind DUT=$dut RIG_NS=$ns OUT=$stem.csv J=$stem-json \
                SAMPLE=$([[ "$board" = ask2 ]] && echo 1 || echo 0) \
                bash "$HERE/testrig-offload-quick.sh" "$cell"
            python3 - "$stem.csv" "$cell-bidir" <<'PY'
import csv
import sys

with open(sys.argv[1]) as f:
    rows = list(csv.DictReader(f))
if len(rows) != 1 or rows[0]["cell"] != sys.argv[2] or rows[0]["verdict"] != "HW":
    raise SystemExit(f"ERROR: missing or non-hardware result in {sys.argv[1]}: {rows}")
PY
        done
    done
done
echo "##### complete: $RUN_DIR"
