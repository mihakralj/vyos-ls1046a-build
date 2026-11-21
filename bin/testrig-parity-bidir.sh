#!/usr/bin/env bash
# Paired core battery: alternate DUTs per cell and reverse their order each round.
# Usage: REPS=5 bin/testrig-parity-bidir.sh nat-v6 vlan-v4
#        bin/testrig-parity-bidir.sh --preflight
#        bin/testrig-parity-bidir.sh --battery [--preflight] [unicast nat ...]
# Battery order: row, repetition, DUT1/VyOS then DUT2/OpenWrt, all six columns.
# Unsupported rows remain explicitly blocked; no historical samples are reused.
# BIDIR=0 also accepts one-way/reverse quick cells. This runner never changes MTUs.
# Env: REPS TAG RUN_DIR DUR STEADY_FROM MEASURE_TO STREAMS PARITY_CC PARITY_MTU.
# Result files are labelled ask2-* and vendor-*.
set -euo pipefail

HERE=$(cd "$(dirname "$0")" && pwd)
. "$HERE/testrig-env.sh"
. "$HERE/testrig-parity-profile.sh"
export RIG_PARITY=1
rig_require GEN1 GEN2 ASK2_DUT VENDOR_DUT
VENDOR_KIND=${VENDOR_KIND:-vendor}
TAG=${TAG:-$(date -u +%Y%m%dT%H%M%SZ)}
RUN_DIR=${RUN_DIR:-$RIG_OUT/paired-bidir-$TAG}
SSH_INPUT=(ssh -o BatchMode=yes -o ConnectTimeout=8)
[[ -z "$RIG_KEY" ]] || SSH_INPUT+=(-i "$RIG_KEY")
SSH=("${SSH_INPUT[@]}" -n)

fail(){ echo "ERROR: $*" >&2; exit 1; }
MODE=run
BATTERY=0
if [[ ${1:-} = --battery ]]; then BATTERY=1; shift; fi
if [[ ${1:-} = --preflight ]]; then MODE=preflight; shift; fi
[[ "$MODE" = preflight || $BATTERY = 1 || $# -gt 0 ]] || fail "name at least one quick cell"
[[ "$VENDOR_KIND" = vendor ]] || fail "matched profile currently requires the OpenWrt vendor DUT"
export BIDIR=${BIDIR:-1}
case $BIDIR in 0|1) ;; *) fail "BIDIR must be 0 or 1" ;; esac
export PARITY_MTU=${PARITY_MTU:-1500}
case $PARITY_MTU in 1500|9000) ;; *) fail "PARITY_MTU must be 1500 or 9000" ;; esac
if [[ $BATTERY = 1 ]]; then
    [[ $REPS = 3 && $PARITY_MTU = 1500 ]] ||
        fail "row battery requires best-of-three at MTU 1500; jumbo adapters remain gated"
    [[ ${RIG_PRESERVE_RPS:-1} = 1 ]] || fail "row battery must preserve matched RPS"
    export RIG_PRESERVE_RPS=1 RIG_VERIFY_PORTS=1 IPERF_PORT=${IPERF_PORT:-5278}
    [[ $IPERF_PORT =~ ^[1-9][0-9]{0,4}$ ]] && (( IPERF_PORT <= 65535 )) ||
        fail "invalid battery server port"
else
  for cell in "$@"; do
    [[ $cell =~ ^(unicast|nat|vlan)-v[46](-rev)?$ ||
       $cell =~ ^(vlanport|portvlan)-v[46]$ ||
       $cell =~ ^pppoe-(up|down)-v[46]$ ||
       $cell =~ ^(wanfull|portfwd|wgwan)-(up|down)-v[46]$ ]] ||
        fail "unsupported quick cell: $cell"
  done
fi
for value in "$GEN_IF" "$GEN_IF_NS" "$VENDOR_NS"; do
    [[ $value =~ ^[a-zA-Z0-9_.-]+$ ]] || fail "invalid interface or namespace"
done
[[ -z "$RIG_KEY" || -r "$RIG_KEY" ]] || fail "cannot read RIG_KEY $RIG_KEY"
RESUME=${RIG_RESUME:-0}
case $RESUME in 0|1) ;; *) fail "RIG_RESUME must be 0 or 1" ;; esac
if [[ -e "$RUN_DIR" && $RESUME != 1 ]]; then
    fail "result directory already exists: $RUN_DIR (set RIG_RESUME=1 after a validated stop)"
fi
if [[ $RESUME = 1 ]]; then
    [[ -s "$RUN_DIR/protocol.json" && -s "$RUN_DIR/harness.sha256" &&
       -s "$RUN_DIR/battery.json" && -s "$RUN_DIR/schedule.tsv" ]] ||
        fail "resume directory is incomplete"
fi
mkdir -p "$RIG_OUT"
exec 8>>"$RIG_OUT/parity-bidir.lock"
flock -n 8 || fail "another paired battery is running"
exec 9>>/tmp/testrig-dut2-battery.lock
flock -n 9 || fail "a DUT2 matrix adapter owns the rig"
if pgrep -f '[b]ash (.*/)?testrig-offload-quick.sh' >/dev/null; then
    fail "another quick harness is running; do not overlap rig traffic"
fi
mkdir -p "$RUN_DIR"
if [[ $RESUME != 1 ]]; then
    python3 "$HERE/testrig_parity.py" profile "$RUN_DIR/protocol.json"
    sha256sum "$HERE"/testrig{-parity-bidir.sh,-offload-quick.sh,-parity-profile.sh,_parity.py,_iperf.py,_wg_validation.py} \
        > "$RUN_DIR/harness.sha256"
fi
if [[ $BATTERY = 1 ]]; then
    if [[ $RESUME != 1 ]]; then
        sha256sum "$HERE"/testrig-{wg-server,wgfp-stop}.py >> "$RUN_DIR/harness.sha256"
        python3 "$HERE/testrig_parity.py" schedule "$RUN_DIR" "$@" > "$RUN_DIR/schedule.tsv"
    fi
    python3 "$HERE/testrig_parity.py" battery-report "$RUN_DIR"
    [[ -s "$RUN_DIR/schedule.tsv" || $MODE = preflight ]] ||
        fail "all requested rows are blocked; no traffic will be sent"
fi
snapshot_generators(){
    local phase=$1 index=0 host vlan
    local -a baseline=()
    "$HERE/testrig-gen-tune.sh" check > "$RUN_DIR/generator-tuning-$phase.txt" ||
        fail "generator tuning differs; inspect $RUN_DIR/generator-tuning-$phase.txt"
    for host in "$GEN1" "$GEN2"; do
        index=$((index+1)); vlan=$((index*10))
        "${SSH_INPUT[@]}" "$host" \
            "sudo -n python3 - snapshot $GEN_IF $GEN_IF_NS $VENDOR_NS $vlan" \
            < "$HERE/testrig_parity.py" > "$RUN_DIR/gen$index-$phase.json"
    done
    if [[ $BATTERY = 1 && $phase != before ]]; then
        baseline=(--baseline "$RUN_DIR/gen1-before.json" "$RUN_DIR/gen2-before.json")
    fi
    python3 "$HERE/testrig_parity.py" compare \
        "$RUN_DIR/gen1-$phase.json" "$RUN_DIR/gen2-$phase.json" --mtu "$PARITY_MTU" "${baseline[@]}"
}
if [[ $RESUME != 1 ]]; then snapshot_generators before; fi

config=$("${SSH[@]}" "$ASK2_DUT" '/bin/vbash -ic "show configuration commands"')
if grep -E "^set interfaces ethernet eth[34] address '?dhcp(v6)?'?$" <<<"$config"; then
    fail "ASK2 traffic ports still have DHCP configured"
fi
"${SSH[@]}" "$ASK2_DUT" '
for e in 3 4; do
    state=$(systemctl is-active dhclient@eth$e)
    [ "$state" = inactive ] || { echo "eth$e dhclient state: $state" >&2; exit 1; }
done'
boots=()
for board in ask2 vendor; do
    dut=$ASK2_DUT
    [[ "$board" = ask2 ]] || dut=$VENDOR_DUT
    boot=$("${SSH[@]}" "$dut" 'cat /proc/sys/kernel/random/boot_id')
    [[ $boot =~ ^[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}$ ]] ||
        fail "cannot identify $board boot"
    boots+=("$boot")
    if [[ $RESUME = 1 ]]; then
        [[ $(cat "$RUN_DIR/boot-$board.txt") = "$boot" ]] ||
            fail "$board rebooted since the stopped campaign; resume refused"
    else
        printf '%s\n' "$boot" > "$RUN_DIR/boot-$board.txt"
    fi
    if [[ $BATTERY = 1 ]]; then
        "${SSH[@]}" "$dut" 'cat /etc/os-release' > "$RUN_DIR/platform-$board.txt"
        if [[ $board = ask2 ]]; then
            grep -q '^ID=vyos$' "$RUN_DIR/platform-$board.txt" || fail "DUT1 must be VyOS"
            [[ $("${SSH[@]}" "$dut" 'sudo -n cat /sys/module/wireguard/parameters/wgfp_peer') = off ]] ||
                fail "WGFP is bound; do not start a routed battery"
        else
            grep -Eq '^ID="?openwrt"?$|^ID_LIKE=.*openwrt' "$RUN_DIR/platform-$board.txt" ||
                fail "DUT2 must be OpenWrt"
        fi
    fi
    "${SSH[@]}" "$dut" "
for e in eth3 eth4; do
    mtu=\$(cat /sys/class/net/\$e/mtu)
    [ \"\$mtu\" = '$PARITY_MTU' ] ||
        { echo \"\$e MTU \$mtu differs from expected $PARITY_MTU\" >&2; exit 1; }
done"
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
check_boots(){
    local index=0 dut
    for dut in "$ASK2_DUT" "$VENDOR_DUT"; do
        [[ $("${SSH[@]}" "$dut" 'cat /proc/sys/kernel/random/boot_id') = "${boots[$index]}" ]] ||
            fail "a DUT rebooted; paired campaign is incomplete"
        index=$((index+1))
    done
}
if [[ "$MODE" = preflight ]]; then
    echo "Preflight passed; no traffic or MTU changes. Artifacts: $RUN_DIR"
    exit 0
fi
if [[ $PARITY_MTU = 9000 ]]; then
    [[ ${PARITY_ASK2_COLD_BOOT_ID:-} = "${boots[0]}" &&
       ${PARITY_VENDOR_COLD_BOOT_ID:-} = "${boots[1]}" ]] ||
        fail "jumbo traffic requires independently confirmed cold-boot IDs for both DUTs"
fi
export RUN_ON=agent

if [[ $BATTERY = 1 ]]; then
    server_hosts=("$GEN1" "$GEN1" "$GEN2" "$GEN2")
    server_namespaces=("" "$VENDOR_NS" "" "$VENDOR_NS")
    server_states=(none none none none)
    token=$(od -An -N16 -tx1 /dev/urandom | tr -d ' \n')
    [[ $token =~ ^[0-9a-f]{32}$ ]] || fail "cannot generate server owner token"
    server_directory="/tmp/wgrig-srv-$token-pair"
    printf '%s\n' "$server_directory" > "$RUN_DIR/server-owner.txt"
    server_call(){
        local index=$1; shift
        local ns=""
        [[ -z ${server_namespaces[$index]} ]] || ns="ip netns exec ${server_namespaces[$index]} "
        "${SSH_INPUT[@]}" "${server_hosts[$index]}" "sudo -n ${ns}python3 - $*"
    }
    cleanup_servers(){
        local status=$? index ident pid started port directory clean=1
        trap - EXIT INT TERM
        for index in 0 1 2 3; do
            directory=$server_directory$index
            case ${server_states[$index]} in
                none) continue ;;
                pending)
                    echo "ERROR: uncertain server creation on ${server_hosts[$index]} namespace=${server_namespaces[$index]}: $directory" >&2
                    clean=0; continue ;;
            esac
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
        if [[ $clean != 1 ]]; then
            echo "ERROR: owned server cleanup is uncertain; preserve $RUN_DIR/server-owner.txt" >&2
            status=1
        fi
        if [[ $status = 0 ]]; then
            if grep -q '"status": "blocked"' "$RUN_DIR/battery.json"; then
                printf 'Safely runnable rows finished; see battery.json for blocked rows.\n' > "$RUN_DIR/PARTIAL"
            else
                printf 'protocol=matched-iperf-v1\n' > "$RUN_DIR/COMPLETE"
            fi
        fi
        exit "$status"
    }
    trap cleanup_servers EXIT
    trap 'exit 130' INT
    trap 'exit 143' TERM
    for index in 0 1 2 3; do
        server_states[$index]=pending
        if server_call "$index" "start $server_directory$index $IPERF_PORT" < "$HERE/testrig-wg-server.py"; then
            server_states[$index]=owned
        else
            status=$?
            if [[ $status = 10 || $status = 11 ]]; then server_states[$index]=none; fi
            exit "$status"
        fi
    done
    group=""
    while IFS=$'\t' read -r row rep board column cell bidir; do
        pair="$RUN_DIR/pair-$row-$column-r$rep.json"
        if [[ $RESUME = 1 && -s "$pair" ]]; then
            echo "##### RESUME verified pair already exists: row=$row rep=$rep column=$column"
            continue
        fi
        next_group=$row-r$rep-$board
        if [[ $group != "$next_group" ]]; then
            group=$next_group
            check_boots
            if [[ $RESUME != 1 ]]; then
                sha256sum --check "$RUN_DIR/harness.sha256" > "$RUN_DIR/hashes-$group.txt"
            else
                printf 'resume: validated protocol, boot IDs and existing pair hashes\n' \
                    > "$RUN_DIR/hashes-$group.txt"
            fi
            snapshot_generators "$group"
        fi
        kind=ask2; dut=$ASK2_DUT; ns=; sudo_prefix="sudo -n "
        if [[ $board = vendor ]]; then kind=vendor; dut=$VENDOR_DUT; ns=$VENDOR_NS; sudo_prefix=; fi
        "${SSH[@]}" "$dut" "${sudo_prefix}nft list tables" > "$RUN_DIR/nft-$group-$column.txt"
        if grep -Eq '^table (ip|ip6) ask2q$' "$RUN_DIR/nft-$group-$column.txt"; then
            fail "foreign ask2q table exists; refusing to replace shared NAT state"
        fi
        stem=$RUN_DIR/$board-$row-$column-r$rep
        echo "##### $(date -u +%FT%TZ) row=$row rep=$rep board=$board column=$column"
        DUT_KIND=$kind DUT=$dut RIG_NS=$ns BIDIR=$bidir OUT=$stem.csv J=$stem-json \
            bash "$HERE/testrig-offload-quick.sh" "$cell"
        check_boots
        if [[ $board = vendor ]]; then
            tag=$cell
            [[ $bidir = 0 ]] || tag=$cell-bidir
            BIDIR=$bidir python3 "$HERE/testrig_parity.py" pair \
                "$RUN_DIR/ask2-$row-$column-r$rep.csv" "$RUN_DIR/ask2-$row-$column-r$rep-json/$tag.json" \
                "$stem.csv" "$stem-json/$tag.json" \
                "$RUN_DIR/pair-$row-$column-r$rep.json" --cell "$cell"
            python3 "$HERE/testrig_parity.py" battery-report "$RUN_DIR"
        fi
    done < "$RUN_DIR/schedule.tsv"
    snapshot_generators after
    check_boots
    cat "$RUN_DIR/matrix.txt"
    exit 0
fi

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
                bash "$HERE/testrig-offload-quick.sh" "$cell"
        done
        tag=$cell
        [[ $BIDIR = 0 ]] || tag=$cell-bidir
        python3 "$HERE/testrig_parity.py" pair \
            "$RUN_DIR/ask2-$cell-r$rep.csv" "$RUN_DIR/ask2-$cell-r$rep-json/$tag.json" \
            "$RUN_DIR/vendor-$cell-r$rep.csv" "$RUN_DIR/vendor-$cell-r$rep-json/$tag.json" \
            "$RUN_DIR/pair-$cell-r$rep.json" --cell "$cell"
    done
    snapshot_generators "r$rep"
done
index=0
for dut in "$ASK2_DUT" "$VENDOR_DUT"; do
    [[ $("${SSH[@]}" "$dut" 'cat /proc/sys/kernel/random/boot_id') = "${boots[$index]}" ]] ||
        fail "a DUT rebooted; paired campaign is incomplete"
    index=$((index+1))
done
printf 'protocol=matched-iperf-v1\n' > "$RUN_DIR/COMPLETE"
echo "##### complete: $RUN_DIR"
