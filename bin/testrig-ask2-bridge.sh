#!/usr/bin/env bash
# testrig-ask2-bridge.sh — hardened ASK2 bridge & bridge-9k matrix runner.
#
# Usage: bin/testrig-ask2-bridge.sh preflight|status|run
#   preflight  read-only: verify clean DUT (eth3/eth4 unmastered, MTU 1500, ASK
#              armed on 0x10/0x11, 0 bridge records), clean generators, no stale lease.
#              Also runs testrig-ask2-9k.sh preflight when MTUS includes 9000.
#   status     read-only summary of DUT bridge state, fe_arm, MTUs and lease.
#   run        acquire rig locks + boot-bound lease, bridge eth3+eth4 non-destructively
#              (preserving eth3.10/eth4.20 and routed IPs), measure up/down/bidir x v4/v6
#              for each MTU in MTUS, verify pcd-snapshot reversibility, and restore cleanly.
#              MTU 9000 transitions are fenced exclusively through testrig-ask2-9k.sh up/down
#              while the bridge is torn down (never mutating MTU while ASK2 is armed).
#
# Env:
#   OUTDIR=<matrix run dir>   required in run mode; writes bridge-<mtu>.csv + json-bridge/
#   MTUS="1500 9000"          space-separated list of 1500 and/or 9000 (default "1500 9000")
#   BRIDGE_OFFLOAD=1          1 (HW offload, default) or 0 (SW bridge baseline)
#   REPS=3 DUR=10 STEADY_FROM=3 BIDIR_DUR=20 BIDIR_STEADY_FROM=12 STREAMS=8
#   RIG_PARITY=1              applies matched profile from bin/testrig-parity-profile.sh
set -Eeuo pipefail

MODE=${1:-}
case "$MODE" in preflight|status|run) [ "$#" -eq 1 ] ;; *) false ;; esac ||
  { echo "usage: $0 preflight|status|run" >&2; exit 2; }

HERE=$(cd "$(dirname "$0")" && pwd)
. "$HERE/testrig-env.sh"
if [ "${RIG_PARITY:-0}" = 1 ]; then . "$HERE/testrig-parity-profile.sh" || exit 2; fi
rig_require ASK2_DUT GEN1 GEN2 GEN_IF

[[ $GEN_IF =~ ^[A-Za-z0-9][A-Za-z0-9_-]{0,11}$ ]] ||
  { echo "ERROR: invalid GEN_IF: $GEN_IF" >&2; exit 2; }

BRIDGE_OFFLOAD=${BRIDGE_OFFLOAD:-1}
case "$BRIDGE_OFFLOAD" in 0|1) ;; *) echo "ERROR: BRIDGE_OFFLOAD must be 0 or 1" >&2; exit 2 ;; esac

MTUS_RAW=${MTUS:-"1500 9000"}
MTU_LIST=()
WANT_9K=0
for _m in $MTUS_RAW; do
  case "$_m" in
    1500|9000) ;;
    *) echo "ERROR: MTUS entries must be 1500 or 9000 (got '$_m')" >&2; exit 2 ;;
  esac
  _dup=0
  for _e in "${MTU_LIST[@]+"${MTU_LIST[@]}"}"; do [ "$_e" = "$_m" ] && _dup=1; done
  [ "$_dup" = 0 ] && MTU_LIST+=("$_m")
  [ "$_m" = 9000 ] && WANT_9K=1
done
[ "${#MTU_LIST[@]}" -gt 0 ] || { echo "ERROR: MTUS must not be empty" >&2; exit 2; }

REPS=${REPS:-3}
DUR=${DUR:-10}
STEADY_FROM=${STEADY_FROM:-3}
BIDIR_DUR=${BIDIR_DUR:-20}
BIDIR_STEADY_FROM=${BIDIR_STEADY_FROM:-12}
STREAMS=${STREAMS:-8}
MEASURE_TO=${MEASURE_TO:-}
IPERF_ARGS=${IPERF_ARGS:-}

for _n in "$REPS" "$DUR" "$STEADY_FROM" "$BIDIR_DUR" "$BIDIR_STEADY_FROM" "$STREAMS"; do
  [[ $_n =~ ^[0-9]+$ ]] || { echo "ERROR: invalid numeric benchmark parameter: $_n" >&2; exit 2; }
done
(( REPS > 0 && STREAMS > 0 && DUR > STEADY_FROM && BIDIR_DUR > BIDIR_STEADY_FROM )) ||
  { echo "ERROR: invalid duration, steady window, streams or repetitions" >&2; exit 2; }
if [ -n "$MEASURE_TO" ]; then
  [[ $MEASURE_TO =~ ^[0-9]+$ ]] && (( MEASURE_TO > STEADY_FROM )) ||
    { echo "ERROR: invalid MEASURE_TO: $MEASURE_TO" >&2; exit 2; }
fi

if [ "$MODE" = run ]; then
  RUN=${OUTDIR:-}
  [ -n "$RUN" ] || { echo "ERROR: set OUTDIR to the matrix run directory" >&2; exit 2; }
fi

SSHO=(-o BatchMode=yes -o ConnectTimeout=8 -o ServerAliveInterval=5 -o ServerAliveCountMax=2)
[ -z "$RIG_KEY" ] || SSHO+=(-i "$RIG_KEY")
d(){ ssh -n "${SSHO[@]}" "$ASK2_DUT" "$@"; }
g1(){ ssh -n "${SSHO[@]}" "$GEN1" "$@"; }
g2(){ ssh -n "${SSHO[@]}" "$GEN2" "$@"; }
say(){ printf '[%s] %s\n' "$(date +%T)" "$*"; }
die(){ echo "ERROR: $*" >&2; exit 1; }

A1=10.99.77.112
A2=10.99.77.113
B1=fd99:77::112
B2=fd99:77::113
IPERF_PORT=5277
PCD=/sys/kernel/debug/fman_pcd/0
SNAP_FILE=/tmp/ask2-bridge-baseline.json

LEASE_ROOT="${XDG_STATE_HOME:-$HOME/.local/state}/testrig-ask2-bridge"
LEASE="$LEASE_ROOT/lease"
JUMBO_LEASE="${XDG_STATE_HOME:-$HOME/.local/state}/testrig-ask2-jumbo/lease"

rig_lock(){
  local path=$1 target_fd=$2 label=$3
  if [[ -e /proc/self/fd/$target_fd && "$path" -ef /proc/self/fd/$target_fd ]]; then
    flock -n "$target_fd" || die "$label lock is held"
    return 0
  fi
  case "$target_fd" in
    8) exec 8>>"$path" || die "cannot open $label lock"; flock -n 8 || die "$label lock is held" ;;
    9) exec 9>>"$path" || die "cannot open $label lock"; flock -n 9 || die "$label lock is held" ;;
    *) die "unsupported lock fd $target_fd" ;;
  esac
}

if [ "$MODE" = run ]; then
  mkdir -p "$RIG_OUT"
  rig_lock "$RIG_OUT/parity-bidir.lock" 8 "paired battery"
  rig_lock /tmp/testrig-dut2-battery.lock 9 "DUT2 battery"
  mkdir -p "$LEASE_ROOT"
  exec {lease_lock}>>"$LEASE_ROOT/.lock"
  flock -n "$lease_lock" || die "another ASK2 bridge operation holds the lock"
fi

fe_state(){ d "sudo cat $PCD/fe_arm"; }
armed_check(){
  local state armed
  state=$(fe_state) || return 1
  armed=$(grep '^Armed ports:' <<<"$state" || true)
  grep -qx 'Faulted ports: (none)' <<<"$state" || return 1
  grep -qx 'FE fault/staging: no' <<<"$state" || return 1
  [[ " $armed " == *" 0x10 "* && " $armed " == *" 0x11 "* ]]
}
dut_mtus(){ d "cat /sys/class/net/eth3/mtu /sys/class/net/eth4/mtu" | paste -sd' '; }
dut_masters(){
  d 'for i in eth3 eth4; do if [ -L "/sys/class/net/$i/master" ]; then basename "$(readlink -f "/sys/class/net/$i/master")"; else echo -; fi; done' | paste -sd' '
}
bridge_recs(){ d "sudo grep -c 'keysz=16 ' $PCD/fe_ehash_stats 2>/dev/null || true"; }
bridge_hits(){ d "sudo awk '/keysz=16 /{for(i=1;i<=NF;i++)if(\$i~/^pkt_count=/){split(\$i,a,\"=\");s+=a[2]}}END{print s+0}' $PCD/fe_ehash_stats 2>/dev/null"; }
dut_cpu(){ d "grep '^cpu ' /proc/stat"; }
busy_pct(){
  printf '%s\n%s\n' "$1" "$2" | awk '{for(i=2;i<=11;i++)a[NR,i]=$i}
    END{for(i=2;i<=11;i++){x=a[2,i]-a[1,i];T+=x;if(i==5||i==6)id+=x}
        printf "%.1f", (T>0 ? 100*(T-id)/T : 0)}'
}
kernel_err_count(){
  d "sudo journalctl -k -b | grep -ciE 'possible circular|inconsistent lock|WARNING:|BUG:|Oops:|DMA bus error|SYNC timed out' || true"
}

status(){
  echo "DUT boot:       $(d 'cat /proc/sys/kernel/random/boot_id')"
  echo "DUT MTUs:       $(dut_mtus) (eth3 eth4)"
  echo "DUT masters:    $(dut_masters) (eth3 eth4)"
  echo "bridge_offload: $(d 'cat /sys/module/ask/parameters/bridge_offload 2>/dev/null || echo missing')"
  echo "bridge records: $(bridge_recs)"
  echo "GEN1 MTU:       $(g1 "cat /sys/class/net/$GEN_IF/mtu")"
  echo "GEN2 MTU:       $(g2 "cat /sys/class/net/$GEN_IF/mtu")"
  echo "Bridge lease:   $([ -e "$LEASE" ] || [ -L "$LEASE" ] && echo "PRESENT ($LEASE)" || echo none)"
  echo "Jumbo lease:    $([ -e "$JUMBO_LEASE" ] || [ -L "$JUMBO_LEASE" ] && echo "PRESENT ($JUMBO_LEASE)" || echo none)"
  fe_state
}

preflight(){
  local c=() m1 m2 recs g1a g2a srv
  [ ! -e "$LEASE" ] && [ ! -L "$LEASE" ] || c+=("bridge lease exists: $LEASE (retain for manual recovery)")
  [ ! -e "$JUMBO_LEASE" ] && [ ! -L "$JUMBO_LEASE" ] || c+=("jumbo lease exists: $JUMBO_LEASE")
  pgrep -f '[b]ash (.*/)?testrig-offload-quick.sh' >/dev/null && c+=("testrig-offload-quick.sh is running")
  d "test -e /sys/module/ask/parameters/bridge_offload" || c+=("DUT lacks /sys/module/ask/parameters/bridge_offload")
  [ "$(dut_mtus)" = "1500 1500" ] || c+=("DUT eth3/eth4 MTUs are not 1500: $(dut_mtus)")
  [ "$(dut_masters)" = "- -" ] || c+=("DUT eth3/eth4 already have a master: $(dut_masters)")
  armed_check || c+=("DUT fe_arm is not clean and armed on 0x10/0x11")
  recs=$(bridge_recs)
  [ "${recs:-0}" = "0" ] || c+=("DUT has $recs existing bridge FE records")
  m1=$(g1 "cat /sys/class/net/$GEN_IF/mtu") || m1=err
  m2=$(g2 "cat /sys/class/net/$GEN_IF/mtu") || m2=err
  [ "$m1" = "1500" ] || c+=("GEN1 $GEN_IF MTU is $m1 (want 1500)")
  [ "$m2" = "1500" ] || c+=("GEN2 $GEN_IF MTU is $m2 (want 1500)")
  g1a=$(g1 "ip -o addr show dev $GEN_IF") || g1a=""
  g2a=$(g2 "ip -o addr show dev $GEN_IF") || g2a=""
  grep -qE "(${A1}|${B1})" <<<"$g1a" && c+=("GEN1 $GEN_IF already carries test bridge address")
  grep -qE "(${A2}|${B2})" <<<"$g2a" && c+=("GEN2 $GEN_IF already carries test bridge address")
  srv=$(g2 "ss -H -ltn 'sport = :$IPERF_PORT'" 2>/dev/null || true)
  [ -z "$srv" ] || c+=("GEN2 port $IPERF_PORT is already listening")
  if [ "$WANT_9K" = 1 ]; then
    "$HERE/testrig-ask2-9k.sh" preflight >/dev/null || c+=("testrig-ask2-9k.sh preflight failed")
  fi
  if [ "${#c[@]}" -gt 0 ]; then
    printf 'CONFLICT: %s\n' "${c[@]}" >&2
    return 1
  fi
  say "preflight: DUT eth3/eth4 unmastered at MTU 1500, ASK armed on 0x10/0x11, generators clean"
}

if [ "$MODE" = status ]; then
  status
  exit 0
fi
if [ "$MODE" = preflight ]; then
  preflight
  exit $?
fi

# ── run mode ───────────────────────────────────────────────────────────
preflight || die "preflight failed; nothing changed"

TOKEN=$(od -An -N8 -tx1 /dev/urandom | tr -d ' \n')
[[ $TOKEN =~ ^[0-9a-f]{16}$ ]] || die "cannot generate lease token"
BOOT=$(d 'cat /proc/sys/kernel/random/boot_id')
[[ $BOOT =~ ^[0-9a-f-]{36}$ ]] || die "cannot read DUT boot_id"
BR="br-mx-${TOKEN:0:8}"

HAVE_LEASE=0
CREATED_BR=0
ADDED_G1=0
ADDED_G2=0
STARTED_SRV=0
SET_OFFLOAD=0
ORIG_OFFLOAD=""
JUMBO_UP=0
HAVE_PCD_SNAP=0
BASE_KERR=0

write_lease(){
  local tmp
  tmp=$(mktemp "$LEASE_ROOT/.lease.XXXXXXXX") || die "cannot create lease"
  printf 'TOKEN=%s\nBOOT=%s\nBR=%s\nJUMBO_UP=%s\n' "$TOKEN" "$BOOT" "$BR" "$JUMBO_UP" > "$tmp"
  if [ "$HAVE_LEASE" = 0 ]; then
    if ! ln -- "$tmp" "$LEASE"; then
      rm -f -- "$tmp"
      die "cannot take lease: $LEASE exists"
    fi
    rm -f -- "$tmp"
    HAVE_LEASE=1
  else
    mv -f -- "$tmp" "$LEASE"
  fi
}

teardown_bridge(){
  [ "$CREATED_BR" = 1 ] || return 0
  local rc=0
  d "for p in eth3 eth4; do
       if [ -L \"/sys/class/net/\$p/master\" ] && [ \"\$(basename \"\$(readlink -f \"/sys/class/net/\$p/master\")\")\" = \"$BR\" ]; then
         sudo ip link set \"\$p\" nomaster
       fi
     done
     if ip link show dev \"$BR\" >/dev/null 2>&1; then
       sudo ip link del \"$BR\"
     fi" || rc=1
  sleep 2
  if [ "$rc" -eq 0 ] && [ "$(dut_masters)" = "- -" ]; then
    CREATED_BR=0
    return 0
  fi
  return 1
}

verify_bridge_quiesced(){
  local recs
  [ "$(dut_masters)" = "- -" ] || { echo "ERROR: eth3/eth4 still mastered: $(dut_masters)" >&2; return 1; }
  recs=$(bridge_recs)
  [ "${recs:-1}" = "0" ] || { echo "ERROR: $recs leftover bridge FE records after teardown" >&2; return 1; }
  armed_check || { echo "ERROR: fe_arm is not clean and armed on 0x10/0x11 after bridge teardown" >&2; return 1; }
}

cleanup(){
  local orig_rc=$? clean_rc=0 boot_now kerr_now
  set +e
  if [ "$STARTED_SRV" = 1 ]; then
    g2 "pkill -f '^iperf3 -s -p $IPERF_PORT'" 2>/dev/null || true
    STARTED_SRV=0
  fi
  if [ "$ADDED_G1" = 1 ]; then
    g1 "sudo ip addr del $A1/24 dev $GEN_IF 2>/dev/null || true; sudo ip -6 addr del $B1/64 dev $GEN_IF 2>/dev/null || true" || clean_rc=1
    ADDED_G1=0
  fi
  if [ "$ADDED_G2" = 1 ]; then
    g2 "sudo ip addr del $A2/24 dev $GEN_IF 2>/dev/null || true; sudo ip -6 addr del $B2/64 dev $GEN_IF 2>/dev/null || true" || clean_rc=1
    ADDED_G2=0
  fi
  if [ "$CREATED_BR" = 1 ]; then
    teardown_bridge || clean_rc=1
  fi
  if [ "$SET_OFFLOAD" = 1 ] && [ -n "$ORIG_OFFLOAD" ]; then
    d "echo $ORIG_OFFLOAD | sudo tee /sys/module/ask/parameters/bridge_offload >/dev/null" || clean_rc=1
    SET_OFFLOAD=0
  fi
  if [ "$JUMBO_UP" = 1 ]; then
    if "$HERE/testrig-ask2-9k.sh" down; then
      JUMBO_UP=0
      write_lease
    else
      clean_rc=1
    fi
  fi
  if [ "$HAVE_LEASE" = 1 ]; then
    boot_now=$(d 'cat /proc/sys/kernel/random/boot_id' 2>/dev/null || echo "")
    [ "$boot_now" = "$BOOT" ] || { echo "ERROR: DUT rebooted during bridge run" >&2; clean_rc=1; }
    [ "$(dut_mtus 2>/dev/null || echo err)" = "1500 1500" ] || { echo "ERROR: DUT MTUs not restored to 1500" >&2; clean_rc=1; }
    verify_bridge_quiesced || clean_rc=1
    if [ "$HAVE_PCD_SNAP" = 1 ]; then
      if ! d "sudo /usr/local/bin/pcd-snapshot diff $SNAP_FILE" >/dev/null; then
        echo "ERROR: pcd-snapshot diff failed after bridge teardown" >&2
        clean_rc=1
      fi
    fi
    kerr_now=$(kernel_err_count 2>/dev/null || echo 999)
    if [ "${kerr_now:-999}" -gt "${BASE_KERR:-0}" ]; then
      echo "ERROR: kernel errors increased ($BASE_KERR -> $kerr_now)" >&2
      clean_rc=1
    fi
    if [ "$clean_rc" -eq 0 ]; then
      rm -f -- "$LEASE"
      HAVE_LEASE=0
      say "teardown verified clean (pcd-snapshot reversible, 0 leftover records); lease released"
    else
      echo "ERROR: teardown verification failed; retaining lease $LEASE for manual recovery" >&2
    fi
  fi
  if [ "$orig_rc" -ne 0 ]; then exit "$orig_rc"; fi
  exit "$clean_rc"
}
trap cleanup EXIT

write_lease
say "lease $TOKEN taken (DUT boot $BOOT, bridge $BR)"

mkdir -p "$RUN/json-bridge"
BASE_KERR=$(kernel_err_count)
ORIG_OFFLOAD=$(d 'cat /sys/module/ask/parameters/bridge_offload')
case "$ORIG_OFFLOAD" in Y|y|1) ORIG_OFFLOAD=1 ;; N|n|0) ORIG_OFFLOAD=0 ;; *) ORIG_OFFLOAD=1 ;; esac

if d "test -x /usr/local/bin/pcd-snapshot"; then
  d "sudo /usr/local/bin/pcd-snapshot capture -o $SNAP_FILE >/dev/null"
  HAVE_PCD_SNAP=1
fi

SET_OFFLOAD=1
d "echo $BRIDGE_OFFLOAD | sudo tee /sys/module/ask/parameters/bridge_offload >/dev/null"

ADDED_G1=1
g1 "set -e; sudo ip addr add $A1/24 dev $GEN_IF; sudo ip -6 addr add $B1/64 dev $GEN_IF nodad"
ADDED_G2=1
g2 "set -e; sudo ip addr add $A2/24 dev $GEN_IF; sudo ip -6 addr add $B2/64 dev $GEN_IF nodad"
STARTED_SRV=1
g2 "iperf3 -s -p $IPERF_PORT -D"

df_probe_bridge(){
  local m=$1 s4 s6 ok i
  s4=$((m - 28))
  s6=$((m - 48))
  g1 "ping -M do -c 3 -W 2 -s $s4 -I $A1 $A2 >/dev/null" || die "MTU $m IPv4 DF ping g1->g2 failed"
  g2 "ping -M do -c 3 -W 2 -s $s4 -I $A2 $A1 >/dev/null" || die "MTU $m IPv4 DF ping g2->g1 failed"
  ok=0
  for i in 1 2 3 4 5 6 7 8 9 10; do
    if g1 "ping -6 -c 1 -W 1 -s 56 -I $B1 $B2 >/dev/null"; then ok=1; break; fi
    sleep 1
  done
  [ "$ok" = 1 ] || die "MTU $m IPv6 neighbor prime g1->g2 failed"
  g1 "ping -6 -M do -c 3 -W 2 -s $s6 -I $B1 $B2 >/dev/null" || die "MTU $m IPv6 DF ping g1->g2 failed"
  g2 "ping -6 -M do -c 3 -W 2 -s $s6 -I $B2 $B1 >/dev/null" || die "MTU $m IPv6 DF ping g2->g1 failed"
}

for m in "${MTU_LIST[@]}"; do
  if [ "$m" = 9000 ]; then
    say "transitioning to MTU 9000 via testrig-ask2-9k.sh up"
    "$HERE/testrig-ask2-9k.sh" up
    JUMBO_UP=1
    write_lease
    [ "$(dut_mtus)" = "9000 9000" ] || die "DUT MTUs not 9000 after testrig-ask2-9k.sh up: $(dut_mtus)"
    armed_check || die "fe_arm not armed after testrig-ask2-9k.sh up"
  fi

  say "creating non-destructive bridge $BR (eth3+eth4, MTU $m, bridge_offload=$BRIDGE_OFFLOAD)"
  CREATED_BR=1
  d "set -e
     sudo ip link add $BR type bridge vlan_filtering 0
     sudo ip link set $BR mtu $m
     sudo ip link set eth3 master $BR
     sudo ip link set eth4 master $BR
     sudo ip link set $BR up"
  sleep 3
  armed_check || die "fe_arm faulted or disarmed after joining $BR"
  df_probe_bridge "$m"

  out="$RUN/bridge-$m.csv"
  echo "ts,mtu,fam,dir,rep,gbps,dut_busy" > "$out"

  for fam in 4 6; do
    if [ "$fam" = 4 ]; then src=$A1; dst=$A2; else src=$B1; dst=$B2; fi
    for dir in up down bidir; do
      case $dir in
        up)    f=""; t=$DUR; sf=$STEADY_FROM ;;
        down)  f="-R"; t=$DUR; sf=$STEADY_FROM ;;
        bidir) f="--bidir"; t=$BIDIR_DUR; sf=$BIDIR_STEADY_FROM ;;
      esac
      for r in $(seq 1 "$REPS"); do
        j="$RUN/json-bridge/bridge-$m-v$fam-$dir-r$r.json"
        h0=$(bridge_hits)
        c0=$(dut_cpu)
        g1 "timeout $((t + 30)) iperf3 -c $dst -B $src -p $IPERF_PORT -P $STREAMS -t $t -i 1 -Z -J $IPERF_ARGS $f" > "$j"
        c1=$(dut_cpu)
        h1=$(bridge_hits)
        dh=$(( ${h1:-0} - ${h0:-0} ))
        gb=$(python3 - "$j" "$sf" "${MEASURE_TO:-}" <<'PY'
import json, sys
j = json.load(open(sys.argv[1]))
sf = float(sys.argv[2])
mt = float(sys.argv[3]) if sys.argv[3] else None
iv = [
    i for i in j.get("intervals", [])
    if i["sum"]["start"] >= sf - 0.01 and (mt is None or i["sum"]["end"] <= mt + 0.05)
]
v = [
    i["sum"]["bits_per_second"] + i.get("sum_bidir_reverse", {}).get("bits_per_second", 0)
    for i in iv
]
print("%.3f" % (sum(v) / len(v) / 1e9 if v else 0))
PY
)
        b=$(busy_pct "$c0" "$c1")
        printf 'bridge mtu %-4s v%s %-5s r%s %7s Gbit/s  DUT busy %5s%%  hw_hits %d\n' \
          "$m" "$fam" "$dir" "$r" "$gb" "$b" "$dh"
        echo "$(date +%H:%M:%S),$m,$fam,$dir,$r,$gb,$b" >> "$out"
      done
    done
  done

  say "tearing down bridge $BR after MTU $m phase"
  teardown_bridge || die "failed to tear down $BR after MTU $m"
  verify_bridge_quiesced || die "bridge FE state not clean after MTU $m teardown"

  if [ "$m" = 9000 ]; then
    say "restoring MTU 1500 via testrig-ask2-9k.sh down"
    "$HERE/testrig-ask2-9k.sh" down
    JUMBO_UP=0
    write_lease
  fi
done
