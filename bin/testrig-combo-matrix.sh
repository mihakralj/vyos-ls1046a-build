#!/bin/bash
# testrig-combo-matrix.sh — port/vlan x v4/v6 x unidir/bidir HW-offload
# throughput matrix for the dell1-DUT-dell2 10G test rig.
#
# Topology (DUT is swappable — see below):
#   dell1 (admin@192.168.1.112) enp1s0f0np0/.10 (X710 p1) --SFP+--> DUT .185 eth3
#   DUT routes eth3 <-> eth4 (HW ASK2 flowtable if present, else plain SW routing)
#   dell2 (admin@192.168.1.113) enp1s0f0np0/.20 (X710 p1) --SFP+--> DUT .185 eth4
# Since 2026-10-09 the dells carry a second X710 port each (enp1s0f1np1) to DUT .106,
# inside netns n106 with the same addresses, and a direct ConnectX-3 link (enp2s0,
# 10.99.99.0/24). All addresses, routes and policy rules are persistent: see
# bin/testrig-dell-net.sh (NetworkManager profiles lab-185-*, systemd lab-n106).
#
# Addressing (see /memories/repo/testrig-dell-boxes.md):
#   port (untagged): dell1 10.99.1.112/fd99:1::112  <-> DUT eth3 10.99.1.<tail>/fd99:1::<tail>
#                     dell2 10.99.2.113/fd99:2::113  <-> DUT eth4 10.99.2.<tail>/fd99:2::<tail>
#   vlan (tagged):    dell1 enp1s0f0np0.10 (vlan 10) 10.99.10.112/fd99:10::112 <-> DUT eth3.10
#                     dell2 enp1s0f0np0.20 (vlan 20) 10.99.20.113/fd99:20::113 <-> DUT eth4.20
# <tail> is fixed at 185 (NOT derived from DUT_HOST): every known DUT's
# eth3/eth4/vif10/vif20 are pinned with a SECOND set of addresses matching
# .185's data-plane scheme (10.99.X.185 / fd99:X::185), alongside the DUT's
# own native addresses. This means dell1/dell2 NEVER need reconfiguration
# when swapping DUTs — only DUT_HOST changes (for management-plane checks),
# and only the physical DAC cables need to move. See "pin a new DUT" below.
#
# To add a new DUT to this rig, pin it to present as .185 on the data plane
# (one-time, on the DUT itself — NOT on dell1/dell2):
#   configure
#   set interfaces ethernet eth3 address '10.99.1.185/24'
#   set interfaces ethernet eth3 address 'fd99:1::185/64'
#   set interfaces ethernet eth3 vif 10 address '10.99.10.185/24'
#   set interfaces ethernet eth3 vif 10 address 'fd99:10::185/64'
#   set interfaces ethernet eth4 address '10.99.2.185/24'
#   set interfaces ethernet eth4 address 'fd99:2::185/64'
#   set interfaces ethernet eth4 vif 20 address '10.99.20.185/24'
#   set interfaces ethernet eth4 vif 20 address 'fd99:20::185/64'
#   commit; save; exit
# (If "set" fails with a bare "Set failed": the active config tree's
# per-leaf directories lack group-write for vyattacfg — fix with
# `sudo chmod -R g+w /opt/vyatta/config/active`, matching the documented
# /opt/vyatta/config/tmp permissions bug in AGENTS.md, then retry. Seen on
# .106 2026-10-02.)
#
# dell1/dell2 need policy routing because the "port" destination subnet
# (e.g. 10.99.2.0/24) is reachable from TWO different local sources on the
# generator (plain port address and vlan address) depending on which combo
# is under test — see `setup` below.
#
# HW vs SW labeling is auto-detected per DUT (`detect_mode`): a DUT with an
# nftables flowtable in "offload hardware" mode and an armed FE (fe_arm) is
# labeled HW; anything else (e.g. .106, which has no flowtable at all) is
# labeled SW — this is a legitimate baseline, not a failure.
#
# KNOWN ISSUE (2026-10-02, ASK2/.185 only, VLAN offload armed): bidirectional
# traffic on a vlan-port/vlan-vlan combo has wedged eth3 into the documented
# "RX-deaf" state (plans/ASK2-PERFORMANCE-TEST-HARNESS.md §8/§8.1 — only a
# cold power-cycle clears it, warm reboot/link-bounce do not). Root cause:
# the VLAN CC-tree does a full teardown/rebuild + FE disengage/re-engage on
# both ports continuously under multi-stream churn (dmesg: "ask: vlan_cc:
# port 0x11 rebuild N remaining keys" / "FE re-engaged ... after VLAN CC
# teardown"), observed even mid-flow, not just between test combos — bidir
# roughly doubles the simultaneous-flow churn on the same port. Unidir VLAN
# traffic is confirmed safe (clean RX counters, no "Err FD status" errors)
# but currently measures no better than the SW-fallback baseline — this is
# the actual bug to chase next, not just a missing CLI flag.
# Until that's fixed, `matrix` SKIPS vlan-port/vlan-vlan bidir cells by
# default (prints N/A) to make repeated testing safe. Override with
# ALLOW_VLAN_BIDIR=1 only when deliberately reproducing/testing the churn
# fix, and watch dmesg for "Err FD status" live while it runs.
#
# Usage:
#   testrig-combo-matrix.sh setup            # idempotent: vlan ifaces + policy routes
#   testrig-combo-matrix.sh check             # ping-verify all 6 combos + offload mode
#   testrig-combo-matrix.sh matrix [reps] [duration] [streams]   # full table (default: 3 12 8)
#   testrig-combo-matrix.sh run <combo> <unidir|bidir> [duration] [streams]  # single cell, raw iperf output
#
# Env overrides: DELL1, DELL2, DUT_HOST, DUT_KEY, DUT_TAIL, IPERF_PORT, ALLOW_VLAN_BIDIR

set -u

DELL1="${DELL1:-admin@192.168.1.112}"
DELL2="${DELL2:-admin@192.168.1.113}"
DUT_HOST="${DUT_HOST:-vyos@192.168.1.185}"
DUT_KEY="${DUT_KEY:-$HOME/.ssh/vyos_key}"
IPERF_PORT="${IPERF_PORT:-5001}"
# Last octet of the DUT's PINNED data-plane addresses (10.99.X.<tail>).
# Fixed at 185 regardless of DUT_HOST — every DUT in this rig is configured
# to also answer on the .185 data-plane addresses (see "pin a new DUT"
# above), so dell1/dell2 routing never needs to change. Override only if
# you deliberately set up a DUT that does NOT mirror the .185 addressing.
DUT_TAIL="${DUT_TAIL:-185}"

REPS_DEFAULT=3
DURATION_DEFAULT=12
STREAMS_DEFAULT=8
OMIT_DEFAULT=2

SSH1() { ssh -o BatchMode=yes -o ConnectTimeout=8 "$DELL1" "$@"; }
SSH2() { ssh -o BatchMode=yes -o ConnectTimeout=8 "$DELL2" "$@"; }
SSHD() { ssh -o BatchMode=yes -o ConnectTimeout=8 -i "$DUT_KEY" "$DUT_HOST" "$@"; }

log() { printf '[%s] %s\n' "$(date +%H:%M:%S)" "$*" >&2; }

# combo name -> "src|dst|v6flag"
combo_row() {
  case "$1" in
    "port-port-v4") echo "10.99.1.112|10.99.2.113|" ;;
    "port-port-v6") echo "fd99:1::112|fd99:2::113|-V" ;;
    "vlan-port-v4") echo "10.99.10.112|10.99.2.113|" ;;
    "vlan-port-v6") echo "fd99:10::112|fd99:2::113|-V" ;;
    "vlan-vlan-v4") echo "10.99.10.112|10.99.20.113|" ;;
    "vlan-vlan-v6") echo "fd99:10::112|fd99:20::113|-V" ;;
    *) echo "" ;;
  esac
}
ALL_COMBOS="port-port-v4 port-port-v6 vlan-port-v4 vlan-port-v6 vlan-vlan-v4 vlan-vlan-v6"
combo_label() {
  case "$1" in
    port-port-v4) echo "port→port v4" ;;
    port-port-v6) echo "port→port v6" ;;
    vlan-port-v4) echo "vlan→port v4" ;;
    vlan-port-v6) echo "vlan→port v6" ;;
    vlan-vlan-v4) echo "vlan→vlan v4" ;;
    vlan-vlan-v6) echo "vlan→vlan v6" ;;
  esac
}

# ------------------------------------------------------------ setup
cmd_setup() {
  [ -n "$DUT_TAIL" ] || { log "could not derive DUT_TAIL from DUT_HOST=$DUT_HOST — set DUT_TAIL explicitly"; return 1; }
  log "DUT=$DUT_HOST tail=$DUT_TAIL"
  local t="$DUT_TAIL"

  # Addresses, routes and policy rules are persistent on the dells (bin/testrig-dell-net.sh);
  # setup only checks that they are active instead of re-adding runtime routes.
  log "dell1+dell2: persistent rig profiles (bin/testrig-dell-net.sh)"
  for h in SSH1 SSH2; do
    $h "nmcli -t -f NAME con show --active | grep -qx lab-185-p1 && nmcli -t -f NAME con show --active | grep -qx lab-185-vlan" \
      || { log "  $h: lab-185-p1/lab-185-vlan not active; run: sudo bash testrig-dell-net.sh dell1|dell2"; return 1; }
  done
  [ "$t" = 185 ] || log "  note: the persistent routes use the .185 data-plane gateways (tail 185)"

  log "dell1+dell2: flush stale neighbor cache for DUT gateway addresses (post cable-swap safety)"
  SSH1 "for d in enp1s0f0np0 enp1s0f0np0.10; do sudo ip neigh flush dev \$d; sudo ip -6 neigh flush dev \$d; done 2>/dev/null; true"
  SSH2 "for d in enp1s0f0np0 enp1s0f0np0.20; do sudo ip neigh flush dev \$d; sudo ip -6 neigh flush dev \$d; done 2>/dev/null; true"

  log "dell2: ensure iperf2 server running (-V, dual-stack)"
  if ! SSH2 "ss -ltn | grep -q ':$IPERF_PORT '"; then
    SSH2 "nohup iperf -s -V -p $IPERF_PORT > /tmp/iperf2_srv.log 2>&1 & sleep 1; echo LAUNCHED" || true
  fi
  SSH2 "ss -ltn | grep -q ':$IPERF_PORT '" && log "  server OK" || { log "  server FAILED to start"; return 1; }
  log "setup: OK"
}

# ------------------------------------------------------------ mode detection
# Prints "HW" if the DUT has an armed hardware flowtable, else "SW".
detect_mode() {
  local has_offload_flag has_fe_armed
  has_offload_flag=$(SSHD "sudo nft list ruleset 2>/dev/null | grep -A3 'flowtable' | grep -c 'flags offload'" 2>/dev/null)
  has_fe_armed=$(SSHD "sudo cat /sys/kernel/debug/fman_pcd/0/fe_arm 2>/dev/null | grep -c 'Armed ports: 0x'" 2>/dev/null)
  if [ "${has_offload_flag:-0}" -gt 0 ] 2>/dev/null && [ "${has_fe_armed:-0}" -gt 0 ] 2>/dev/null; then
    echo "HW"
  else
    echo "SW"
  fi
}

# ------------------------------------------------------------ check
cmd_check() {
  log "DUT reachability + offload state ($DUT_HOST, tail=$DUT_TAIL)"
  SSHD 'uname -r' >/dev/null || { log "DUT unreachable"; return 1; }
  SSHD 'sudo cat /sys/kernel/debug/fman_pcd/0/fe_arm 2>&1 || echo "(no fman_pcd debugfs — not an ASK2 board)"' | sed 's/^/  /'
  SSHD "sudo nft list ruleset 2>/dev/null | grep -A2 'flowtable' || echo '(no flowtable configured)'" | sed 's/^/  /'
  log "detected mode: $(detect_mode)"

  log "ping-verifying all 6 combo address-pairs from dell1"
  local name lane src dst v6 ok=0 fail=0
  for name in $ALL_COMBOS; do
    lane=$(combo_row "$name"); src="${lane%%|*}"; local rest="${lane#*|}"; dst="${rest%%|*}"; v6="${rest##*|}"
    local pingbin="ping"
    if SSH1 "$pingbin -c2 -W2 -I $src $dst >/dev/null 2>&1"; then
      log "  $(combo_label "$name"): OK ($src -> $dst)"
      ok=$((ok+1))
    else
      log "  $(combo_label "$name"): FAIL ($src -> $dst)"
      fail=$((fail+1))
    fi
  done
  log "check: $ok/$((ok+fail)) combos reachable"
  [ "$fail" -eq 0 ]
}

# ------------------------------------------------------------ single run
# parse the last [SUM] line's bandwidth into Gbps
parse_sum_gbps() {
  awk '
    /\[SUM\]/ { val=$6; unit=$7 }
    END {
      if (unit == "Gbits/sec")      printf "%.3f", val;
      else if (unit == "Mbits/sec") printf "%.3f", val/1000.0;
      else if (unit == "Kbits/sec") printf "%.3f", val/1000000.0;
      else print "";
    }'
}

run_once() { # <combo> <unidir|bidir> <duration> <streams>
  local name="$1" mode="$2" dur="$3" streams="$4"
  local lane src dst v6 rest fdflag=""
  lane=$(combo_row "$name"); [ -n "$lane" ] || { echo ""; return 1; }
  src="${lane%%|*}"; rest="${lane#*|}"; dst="${rest%%|*}"; v6="${rest##*|}"
  [ "$mode" = "bidir" ] && fdflag="--full-duplex"
  SSH1 "iperf -c $dst -B $src $v6 -p $IPERF_PORT -P $streams -t $dur $fdflag 2>&1" | parse_sum_gbps
}

cmd_run() {
  local name="${1:?combo required}" mode="${2:?unidir|bidir required}"
  local dur="${3:-$DURATION_DEFAULT}" streams="${4:-$STREAMS_DEFAULT}"
  SSH1 "iperf -c $(combo_row "$name" | awk -F'|' '{print $2}') -B $(combo_row "$name" | awk -F'|' '{print $1}') $(combo_row "$name" | awk -F'|' '{print $3}') -p $IPERF_PORT -P $streams -t $dur $([ "$mode" = bidir ] && echo --full-duplex)"
}

# ------------------------------------------------------------ matrix + table
center() {
  local s="$1" w="$2" len=${#1} total left right
  total=$(( w - len )); [ "$total" -lt 0 ] && total=0
  left=$(( total / 2 )); right=$(( total - left ))
  printf '%*s%s%*s' "$left" '' "$s" "$right" ''
}

print_table() {
  # rows: "label|unidir_str|bidir_str" ...
  local -n _rows=$1
  local mode_label="${2:-HW}"
  local w1=19 w2=12 w3=12
  local hline tline bline
  hline="├$(printf '─%.0s' $(seq 1 $w1))┼$(printf '─%.0s' $(seq 1 $w2))┼$(printf '─%.0s' $(seq 1 $w3))┤"
  tline="┌$(printf '─%.0s' $(seq 1 $w1))┬$(printf '─%.0s' $(seq 1 $w2))┬$(printf '─%.0s' $(seq 1 $w3))┐"
  bline="└$(printf '─%.0s' $(seq 1 $w1))┴$(printf '─%.0s' $(seq 1 $w2))┴$(printf '─%.0s' $(seq 1 $w3))┘"
  echo "$tline"
  printf '│%s│%s│%s│\n' "$(center "Combo" $w1)" "$(center "Throughput" $w2)" "$(center "Throughput" $w3)"
  printf '│%s│%s│%s│\n' "$(center "" $w1)" "$(center "$mode_label unidir" $w2)" "$(center "$mode_label bidir" $w3)"
  local row label u b
  for row in "${_rows[@]}"; do
    echo "$hline"
    label="${row%%|*}"; local rest="${row#*|}"; u="${rest%%|*}"; b="${rest##*|}"
    printf '│%s│%s│%s│\n' "$(center "$label" $w1)" "$(center "$u" $w2)" "$(center "$b" $w3)"
  done
  echo "$bline"
}

cmd_matrix() {
  local reps="${1:-$REPS_DEFAULT}" dur="${2:-$DURATION_DEFAULT}" streams="${3:-$STREAMS_DEFAULT}"
  cmd_setup || { log "setup failed, aborting"; return 1; }
  local mode_label; mode_label=$(detect_mode)
  log "matrix: DUT=$DUT_HOST mode=$mode_label reps=$reps duration=${dur}s streams=$streams"

  local rows=()
  local name mode i val sum n avg skipped_bidir=0
  for name in $ALL_COMBOS; do
    local results_uni=() results_bi=()
    for mode in unidir bidir; do
      if [ "$mode" = bidir ] && [[ "$name" == vlan-* ]] && [ "${ALLOW_VLAN_BIDIR:-0}" != "1" ]; then
        log "  $(combo_label "$name") bidir: SKIPPED (known CC-churn lockup risk — set ALLOW_VLAN_BIDIR=1 to override)"
        skipped_bidir=1
        continue
      fi
      for i in $(seq 1 "$reps"); do
        val=$(run_once "$name" "$mode" "$dur" "$streams")
        log "  $(combo_label "$name") $mode rep $i/$reps: ${val:-FAIL} Gbps"
        if [ "$mode" = unidir ]; then results_uni+=("${val:-0}"); else results_bi+=("${val:-0}"); fi
      done
    done
    avg_uni=$(printf '%s\n' "${results_uni[@]}" | awk '{s+=$1; n++} END{if(n>0) printf "%.2f", s/n; else print "0.00"}')
    if [ "${#results_bi[@]}" -eq 0 ]; then
      avg_bi="N/A"
    else
      avg_bi="$(printf '%s\n' "${results_bi[@]}" | awk '{s+=$1; n++} END{if(n>0) printf "%.2f", s/n; else print "0.00"}') Gbps"
    fi
    rows+=("$(combo_label "$name")|${avg_uni} Gbps|${avg_bi}")
  done

  echo ""
  print_table rows "$mode_label"
  local note="NB: n=$reps AVGs shown | DUT=$DUT_HOST ($mode_label)"
  [ "$skipped_bidir" -eq 1 ] && note="$note | vlan bidir SKIPPED (ALLOW_VLAN_BIDIR=1 to override, see header comment)"
  echo "$note"
}

# ------------------------------------------------------------ dispatch
sub="${1:-matrix}"; shift || true
case "$sub" in
  setup)  cmd_setup ;;
  check)  cmd_check ;;
  run)    cmd_run "$@" ;;
  matrix) cmd_matrix "$@" ;;
  *) echo "usage: $0 {setup|check|run <combo> <unidir|bidir> [dur] [streams]|matrix [reps] [dur] [streams]}" >&2
     echo "combos: $ALL_COMBOS" >&2
     exit 1 ;;
esac
