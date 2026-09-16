#!/bin/bash
# vlan-offload-standard-test.sh — standardized throughput+CPU comparison:
# unicast / NAT / VLAN-VLAN, IPv4 and IPv6, against a single DUT.
#
# Reuses the /proc/stat CPU-delta technique and iperf3 --json parsing
# pattern from bin/verify-ask1-vs-ask2.sh, generalized for a
# directly-wired generator (no intermediate "heidi" host) and extended
# with IPv6 and a VLAN-VLAN lane.
#
# Topology assumed (matches tonight's live setup):
#   GEN  = the traffic-generating box, reached via SSH ProxyJump through
#          the DUT (its only network path once wired into DUT eth3 --
#          see decomp/vlan-cc-throughput-bottleneck.md for why).
#   DUT  = the router under test (.185 = ASK2 today; repoint DUT_HOST at
#          .106 for the ASK1/vendor comparison run -- same script, same
#          methodology, just a different target).
#   HELGA (or whatever's on the DUT's other port) = the traffic sink.
#
# Six lanes, matching the ASK2/ASK1 comparison benchmark table format:
#   unicast-v4, unicast-v6, nat-v4, nat-v6, vlan-vlan-v4, vlan-vlan-v6
#
# NOTE: on the current DUT config, "unicast" and "nat" resolve to the
# SAME wire path (an existing masquerade rule NATs the unicast subnet
# unconditionally) -- both rows are still reported since NAT has
# repeatedly measured as ~free, and a DUT with a separate no-NAT subnet
# can just point NAT_V4/NAT_V6 at a different pair below.
#
# Usage:
#   vlan-offload-standard-test.sh check
#   vlan-offload-standard-test.sh run [lane] [-P N] [-T SEC]
#   vlan-offload-standard-test.sh all [-P N] [-T SEC]
#
# Env overrides (defaults match tonight's .135 -> .185 -> HELGA wiring):
#   DUT_HOST, DUT_KEY          SSH target/key for the DUT (CPU sampling)
#   GEN_USER, GEN_PASS         generator login (password auth via sshpass)
#   UNICAST_V4/V6 GEN, DST     generator bind addr + dest addr (untagged)
#   NAT_V4/V6 GEN, DST         generator bind addr + dest addr (NAT lane)
#   VLAN_V4/V6 GEN, DST        generator bind addr + dest addr (VID10<->VID20)

set -u

DUT_HOST="${DUT_HOST:-vyos@192.168.1.185}"
DUT_KEY="${DUT_KEY:-$HOME/.ssh/vyos_key}"
GEN_USER="${GEN_USER:-user}"
GEN_PASS="${GEN_PASS:-password}"
GEN_ADDR="${GEN_ADDR:-10.99.1.135}"   # reached via ProxyJump through DUT_HOST
PORT="${PORT:-5201}"

SSH_DUT() { ssh -o StrictHostKeyChecking=no -o ConnectTimeout=8 -i "$DUT_KEY" "$DUT_HOST" "$@"; }
SSH_GEN() {
  sshpass -p "$GEN_PASS" ssh -o StrictHostKeyChecking=no -o ConnectTimeout=10 \
    -o PreferredAuthentications=password -o PubkeyAuthentication=no \
    -o ProxyCommand="ssh -i $DUT_KEY -W %h:%p -o StrictHostKeyChecking=no $DUT_HOST" \
    "$GEN_USER@$GEN_ADDR" "$@"
}

# lane: name | gen_bind_addr | dst_addr
LANE_UNICAST_V4="${UNICAST_V4_GEN:-10.99.1.135}|${UNICAST_V4_DST:-10.99.2.16}"
LANE_UNICAST_V6="${UNICAST_V6_GEN:-fd99:1::135}|${UNICAST_V6_DST:-fd99:2::16}"
LANE_NAT_V4="${NAT_V4_GEN:-10.99.1.135}|${NAT_V4_DST:-10.99.2.16}"
LANE_NAT_V6="${NAT_V6_GEN:-fd99:1::135}|${NAT_V6_DST:-fd99:2::16}"
LANE_VLAN_V4="${VLAN_V4_GEN:-10.99.10.135}|${VLAN_V4_DST:-10.99.20.16}"
LANE_VLAN_V6="${VLAN_V6_GEN:-fd99:10::135}|${VLAN_V6_DST:-fd99:20::16}"

OUTDIR="${OUTDIR:-/tmp/vlan-offload-test}"
mkdir -p "$OUTDIR"

log()  { echo "[$(date +%H:%M:%S)] $*"; }

lane_of() {
  case "$1" in
    unicast-v4) echo "$LANE_UNICAST_V4" ;;
    unicast-v6) echo "$LANE_UNICAST_V6" ;;
    nat-v4)     echo "$LANE_NAT_V4" ;;
    nat-v6)     echo "$LANE_NAT_V6" ;;
    vlan-vlan-v4) echo "$LANE_VLAN_V4" ;;
    vlan-vlan-v6) echo "$LANE_VLAN_V6" ;;
    *) echo "" ;;
  esac
}

cmd_check() {
  log "checking DUT ($DUT_HOST)"
  SSH_DUT 'uname -r' || { echo "DUT unreachable"; exit 1; }
  log "checking generator (via ProxyJump through DUT)"
  SSH_GEN 'echo OK; which iperf3' || { echo "generator unreachable"; exit 1; }
  log "check: OK"
}

# /proc/stat CPU-delta sampling on the DUT (no mpstat dependency)
cpu_snapshot() {
  SSH_DUT "grep -E '^cpu[0-9]' /proc/stat | awk '{print \$1, \$5, \$2+\$3+\$4+\$5+\$6+\$7+\$8+\$9+\$10}'"
}

cpu_delta() { # <before-file> <after-file>
  local b="$1" a="$2" n b_idle b_tot a_idle a_tot
  while read -r n b_idle b_tot; do
    read -r _ a_idle a_tot < <(grep "^$n " "$a")
    awk -v n="$n" -v bi="$b_idle" -v bt="$b_tot" -v ai="$a_idle" -v at="$a_tot" \
      'BEGIN{db=bt-bi; da=at-bt; if(da>0) printf "%s %5.1f%%\n", n, 100*(1-(ai-bi)/da)}'
  done < "$b"
}

run_lane() { # <lane-name> <streams> <duration>
  local name="$1" P="$2" T="$3"
  local lane gen dst v6flag=""
  lane=$(lane_of "$name")
  [ -n "$lane" ] || { echo "unknown lane: $name" >&2; return 1; }
  gen="${lane%%|*}"; dst="${lane##*|}"
  case "$dst" in *:*) v6flag="-6" ;; esac

  log "=== $name === gen=$gen dst=$dst P=$P T=${T}s bidir"

  cpu_snapshot > "$OUTDIR/cpu-$name-before.txt"
  SSH_GEN "iperf3 -c $dst -B $gen $v6flag -p $PORT -P $P --bidir -t $T --json" \
    > "$OUTDIR/iperf-$name.json" 2>/dev/null
  local rc=$?
  cpu_snapshot > "$OUTDIR/cpu-$name-after.txt"

  if [ $rc -ne 0 ] || [ ! -s "$OUTDIR/iperf-$name.json" ]; then
    log "  iperf3 failed for lane $name (rc=$rc)"
    return 1
  fi

  python3 - "$OUTDIR/iperf-$name.json" "$name" <<'EOF'
import json, sys
path, name = sys.argv[1], sys.argv[2]
try:
    d = json.load(open(path))
except Exception as e:
    print(f"  {name}: unreadable json ({e})")
    sys.exit(0)
end = d.get("end", {})
sent = end.get("sum_sent", {}).get("bits_per_second", 0) / 1e9
recv = end.get("sum_received", {}).get("bits_per_second", 0) / 1e9
print(f"  throughput: tx={sent:.2f}G rx={recv:.2f}G aggregate={sent+recv:.2f}G")
EOF
  log "  DUT per-core busy% during run:"
  cpu_delta "$OUTDIR/cpu-$name-before.txt" "$OUTDIR/cpu-$name-after.txt" | sed 's/^/    /'
}

cmd_all() {
  local P="${1:-8}" T="${2:-12}"
  local name
  for name in unicast-v4 unicast-v6 nat-v4 nat-v6 vlan-vlan-v4 vlan-vlan-v6; do
    run_lane "$name" "$P" "$T"
    echo
  done
}

sub="${1:-check}"; shift || true
case "$sub" in
  check) cmd_check ;;
  run) run_lane "${1:?lane name required}" "${2:-8}" "${3:-12}" ;;
  all) cmd_all "${1:-8}" "${2:-12}" ;;
  *) echo "usage: $0 {check|run <lane> [P] [T]|all [P] [T]}" >&2
     echo "lanes: unicast-v4 unicast-v6 nat-v4 nat-v6 vlan-vlan-v4 vlan-vlan-v6" >&2
     exit 1 ;;
esac
