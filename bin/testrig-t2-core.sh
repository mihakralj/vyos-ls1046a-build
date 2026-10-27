#!/usr/bin/env bash
# T2 core battery: paired ASK2 + vendor runs of the TCP cells that have a driver and are safe to repeat.
# Run on gen2 (detached, with a log). Usage: REPS=10 bin/testrig-t2-core.sh [bidir|oneway|all]
#   bidir  : the 8 testrig-parity-bidir.sh cells (R05 R06 N05 N06 V09-V12)
#   oneway : unicast/nat/vlan/vlanport/nat-vlan, v4+v6, both directions (R01-R04 N01-N04 V01-V08 C01 C02)
# Board order alternates every round; every result is a separate CSV under RUN_DIR. Failed cells are logged and
# the battery continues. PPPoE, destructive (reboot, flood, port loop) and TBD-driver rows are not in this script.
set -uo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
. "$HERE/testrig-env.sh"
rig_require GEN1 ASK2_DUT VENDOR_DUT
REPS=${REPS:-10}
PHASE=${1:-all}
TAG=${TAG:-t2-$(date -u +%Y%m%dT%H%M%SZ)}
RUN_DIR=${RUN_DIR:-$RIG_OUT/$TAG}
mkdir -p "$RUN_DIR"
export RUN_ON=dell2 DUR=${DUR:-30} STEADY_FROM=${STEADY_FROM:-10} STREAMS=${STREAMS:-8}

BIDIR_CELLS=(unicast-v4 unicast-v6 nat-v4 nat-v6 vlan-v4 vlan-v6 vlanport-v4 vlanport-v6)
ONEWAY_CELLS=(unicast-v4 unicast-v4-rev unicast-v6 unicast-v6-rev nat-v4 nat-v4-rev nat-v6 nat-v6-rev
              vlan-v4 vlan-v4-rev vlan-v6 vlan-v6-rev vlanport-v4 portvlan-v4 vlanport-v6 portvlan-v6
              nat-vlan-v4 nat-vlan-v6)

one() { # board cell rep bidir(0|1)
    local board=$1 cell=$2 rep=$3 bidir=$4 kind=${ASK2_KIND:-ask2} dut=$ASK2_DUT ns=
    [[ $board = vendor ]] && { kind=vendor; dut=$VENDOR_DUT; ns=$VENDOR_NS; }
    local stem=$RUN_DIR/$board-$cell$([[ $bidir = 1 ]] && echo -bidir)-r$rep
    echo "##### $(date -u +%FT%TZ) board=$board cell=$cell bidir=$bidir rep=$rep"
    DUT_KIND=$kind DUT=$dut RIG_NS=$ns BIDIR=$([[ $bidir = 1 ]] && echo 1 || echo 0) OUT=$stem.csv J=$stem-json \
        SAMPLE=$([[ $board = ask2 ]] && echo 1 || echo 0) \
        bash "$HERE/testrig-offload-quick.sh" "$cell" || echo "FAILED board=$board cell=$cell rep=$rep" >> "$RUN_DIR/failures.log"
}

run_phase() { # bidir-flag cells...
    local bidir=$1; shift
    local rep cell board boards
    for ((rep = 1; rep <= REPS; rep++)); do
        boards=(ask2 vendor); (( rep % 2 == 1 )) || boards=(vendor ask2)
        for cell in "$@"; do
            for board in "${boards[@]}"; do
                [[ -z ${ONLY_BOARD:-} || $ONLY_BOARD = "$board" ]] || continue
                one "$board" "$cell" "$rep" "$bidir"
            done
        done
    done
}

# ASK2_KIND=opnsense ASK2_DUT=root@<opns> ONLY_BOARD=ask2 runs an OPNsense DUT cabled to the ASK2 (root-namespace) ports.
# ONLY_BOARD=vendor ONLY_CELLS="a b" reruns a subset (e.g. cells that failed)
[[ -n ${ONLY_CELLS:-} ]] && { read -ra BIDIR_CELLS <<< "$ONLY_CELLS"; ONEWAY_CELLS=("${BIDIR_CELLS[@]}"); }

exec 9>"$RIG_OUT/t2-core.lock"
flock -n 9 || { echo "another T2 battery is running" >&2; exit 1; }
[[ $PHASE = bidir  || $PHASE = all ]] && run_phase 1 "${BIDIR_CELLS[@]}"
[[ $PHASE = oneway || $PHASE = all ]] && run_phase 0 "${ONEWAY_CELLS[@]}"
echo "##### complete: $RUN_DIR"
