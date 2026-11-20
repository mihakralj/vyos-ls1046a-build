#!/usr/bin/env bash
# MTU-9000 rows of the ASK2 matrix.
# Usage: bin/testrig-ask2-9k.sh preflight|up|down|status|run
#   preflight  read-only: everything at MTU 1500, unowned, ASK armed on 0x10/0x11
#   status     read-only MTU, offload and lease inspection
#   up/down/run are disabled until FE disengagement and restoration are safe.
# Env: OUTDIR REPS DUR STEADY_FROM MEASURE_TO STREAMS (RIG_PARITY=1 applies the matched profile),
#      CELLS_UNI / CELLS_BI override the cell lists.
set -Eeuo pipefail

MODE=${1:-}
case "$MODE" in preflight|up|down|status|run) [ "$#" -eq 1 ] ;; *) false ;; esac ||
  { echo "usage: $0 preflight|up|down|status|run" >&2; exit 2; }
lifecycle_gate(){
  case "$1" in
    up|down|run)
      if [ "${RIG_JUMBO_LIVE_APPROVED:-0}" != 1 ]; then
        echo "ERROR: jumbo transition disabled; set RIG_JUMBO_LIVE_APPROVED=1 only after cold-boot FE drain preflight" >&2
        return 2
      fi ;;
  esac
}
lifecycle_gate "$MODE"

HERE=$(cd "$(dirname "$0")" && pwd)
. "$HERE/testrig-env.sh"
if [ "${RIG_PARITY:-0}" = 1 ]; then . "$HERE/testrig-parity-profile.sh" || exit 2; fi
rig_require ASK2_DUT GEN1 GEN2 GEN_IF

if [ -n "${RIG_ASK2_JUMBO_TOKEN:-}" ]; then
  [[ $RIG_ASK2_JUMBO_TOKEN =~ ^[0-9a-f]{16}$ ]] ||
    { echo "ERROR: invalid RIG_ASK2_JUMBO_TOKEN" >&2; exit 2; }
fi
if [ -n "${RIG_JUMBO_ROW:-}" ]; then
  [ "$MODE" = run ] && [ -z "${CELLS_UNI+x}" ] && [ -z "${CELLS_BI+x}" ] ||
    { echo "ERROR: RIG_JUMBO_ROW requires run mode without CELLS overrides" >&2; exit 2; }
  case "$RIG_JUMBO_ROW" in
    unicast-9k)
      CELLS_UNI="unicast-v4 unicast-v6 unicast-v4-rev unicast-v6-rev"
      CELLS_BI="unicast-v4 unicast-v6" ;;
    nat-9k)
      CELLS_UNI="nat-v4 nat-v6 nat-v4-rev nat-v6-rev"
      CELLS_BI="nat-v4 nat-v6" ;;
    vlan-9k)
      CELLS_UNI="vlan-v4 vlan-v6 vlan-v4-rev vlan-v6-rev"
      CELLS_BI="vlan-v4 vlan-v6" ;;
    vlan-port-9k)
      CELLS_UNI="vlanport-v4 vlanport-v6 portvlan-v4 portvlan-v6"
      CELLS_BI="vlanport-v4 vlanport-v6" ;;
    *) echo "ERROR: unsupported RIG_JUMBO_ROW" >&2; exit 2 ;;
  esac
fi
[[ $GEN_IF =~ ^[A-Za-z0-9][A-Za-z0-9_-]{0,11}$ ]] || { echo "ERROR: invalid GEN_IF" >&2; exit 2; }

MTU=9000
SSHO=(-o BatchMode=yes -o ConnectTimeout=8 -o ServerAliveInterval=5 -o ServerAliveCountMax=2)
[ -z "$RIG_KEY" ] || SSHO+=(-i "$RIG_KEY")
host(){ case $1 in dut) echo "$ASK2_DUT";; g1) echo "$GEN1";; g2) echo "$GEN2";; esac; }
on(){ local h=$1; shift; ssh -n "${SSHO[@]}" "$(host "$h")" "$@"; }
say(){ printf '[%s] %s\n' "$(date +%T)" "$*"; }
die(){ echo "ERROR: $*" >&2; exit 1; }

LEASE_ROOT="${XDG_STATE_HOME:-$HOME/.local/state}/testrig-ask2-jumbo"
LEASE="$LEASE_ROOT/lease"
rig_lock(){
  local path=$1 inherited=$2 label=$3 fd
  if [[ -e /proc/self/fd/$inherited && "$path" -ef /proc/self/fd/$inherited ]]; then
    fd=$inherited
  else
    exec {fd}>>"$path" || die "cannot open $label lock"
  fi
  flock -n "$fd" || die "$label lock is held"
}
case "$MODE" in
  up|down|run)
    mkdir -p "$RIG_OUT"
    rig_lock "$RIG_OUT/parity-bidir.lock" 8 "paired battery"
    rig_lock /tmp/testrig-dut2-battery.lock 9 "DUT2 battery"
    ;;
esac
mkdir -p "$LEASE_ROOT"
exec {lease_lock}>>"$LEASE_ROOT/.lock"
flock -n "$lease_lock" || die "another ASK2 jumbo operation holds the lock"

# Generator links: host, device, VLAN parent flag.
GLINKS=("g1 $GEN_IF" "g1 $GEN_IF.10" "g2 $GEN_IF" "g2 $GEN_IF.20")
DUT_IFS=(eth3 eth4 eth3.10 eth4.20)
DUT_NODES=("interfaces ethernet eth3 vif 10 mtu" "interfaces ethernet eth4 vif 20 mtu"
           "interfaces ethernet eth3 mtu" "interfaces ethernet eth4 mtu")

vyos(){ # commit set/delete lines, never save; fails on a commit error
  local out
  out=$({ echo 'source /opt/vyatta/etc/functions/script-template'; echo configure
          printf '%s\n' "$@"; echo commit; echo 'exit discard'; } |
        ssh "${SSHO[@]}" "$ASK2_DUT" 'vbash -s' 2>&1) || { echo "$out" >&2; return 1; }
  if grep -qiE 'commit failed|failed|error|invalid|not valid' <<<"$out"; then
    grep -vE '^(\[edit\]|$)' <<<"$out" >&2; return 1; fi
}
dut_mtus(){ on dut "cat $(printf '/sys/class/net/%s/mtu ' "${DUT_IFS[@]}")" | paste -sd' '; }
dut_config_mtus(){ # one token per node: value or "-"
  local n value status out=()
  for n in "${DUT_NODES[@]}"; do
    if value=$(on dut "cli-shell-api returnActiveValue $n" 2>/dev/null); then
      [[ $value =~ ^[0-9]+$ ]] || { echo "ERROR: invalid DUT MTU value for $n" >&2; return 1; }
    else
      status=$?
      [ "$status" = 1 ] || { echo "ERROR: cannot inspect DUT MTU $n (status $status)" >&2; return 1; }
      value=-
    fi
    out+=("$value")
  done
  echo "${out[*]}"
}
fe_state(){ on dut 'sudo cat /sys/kernel/debug/fman_pcd/0/fe_arm'; }
armed_check(){ # want=on|off
  local state armed
  state=$(fe_state) || die "cannot read fe_arm"
  armed=$(grep '^Armed ports:' <<<"$state" || true)
  grep -qx 'Faulted ports: (none)' <<<"$state" || die "FE faulted ports: $(grep '^Faulted' <<<"$state")"
  grep -qx 'FE fault/staging: no' <<<"$state" || die "FE fault/staging state is not clean"
  if [ "$1" = on ]; then
    [[ " $armed " == *" 0x10 "* && " $armed " == *" 0x11 "* ]] || return 1
  else
    [[ " $armed " != *" 0x10 "* && " $armed " != *" 0x11 "* ]] || return 1
  fi
}
offload(){ # on|off for both families on eth3/eth4, verified through fe_arm
  local v=set i f c=()
  [ "$1" = off ] && v=delete
  for i in eth3 eth4; do for f in ipv4 ipv6; do c+=("$v interfaces ethernet $i offload $f"); done; done
  vyos "${c[@]}" || die "offload $1 commit failed"
  for _ in 1 2 3 4 5 6 7 8 9 10; do armed_check "$1" && { say "ASK offload $1 on eth3/eth4 verified"; return 0; }; sleep 1; done
  die "fe_arm did not reach offload $1 for ports 0x10/0x11"
}
offload_configured(){
  local i f
  for i in eth3 eth4; do for f in ipv4 ipv6; do
    on dut "cli-shell-api existsActive interfaces ethernet $i offload $f" || return 1
  done; done
}
offload_unconfigured(){
  local i f rc
  for i in eth3 eth4; do for f in ipv4 ipv6; do
    if on dut "cli-shell-api existsActive interfaces ethernet $i offload $f"; then
      return 1
    else
      rc=$?
      [ "$rc" = 1 ] || return 1
    fi
  done; done
}

# generator link identity "ifindex iflink mac mtu owner-altname"
gen_state(){
  local h=$1 dev=$2
  on "$h" "d=$dev; printf '%s %s %s %s ' \$(cat /sys/class/net/\$d/ifindex /sys/class/net/\$d/iflink /sys/class/net/\$d/address /sys/class/net/\$d/mtu); ip -d -o link show dev \$d | awk '{o=\"-\"; for(i=1;i<NF;i++) if(\$i==\"altname\" && \$(i+1) ~ /^t2mtu-/) o=\$(i+1); print o}'"
}
gen_mtu(){ # op dev-entry-index before after
  local op=$1 idx=$2 before=$3 after=$4 h dev ifindex iflink mac
  read -r h dev <<<"${GLINKS[$idx]}"
  read -r ifindex iflink mac <<<"${GID[$idx]}"
  ssh "${SSHO[@]}" "$(host "$h")" \
    "sudo -n sh -s -- $op $dev $ifindex $iflink $mac ${OWNERS[$idx]} $before $after" \
    < "$HERE/testrig-vendor-mtu.sh"
}

lease_read(){
  [ ! -L "$LEASE" ] || die "lease is a symlink: $LEASE"
  [ ! -e "$LEASE" ] || [ -f "$LEASE" ] || die "lease is not a regular file: $LEASE"
  [ -f "$LEASE" ] || return 1
  # shellcheck disable=SC1090
  . "$LEASE"
  [[ ${TOKEN:-} =~ ^[0-9a-f]{16}$ && ${BOOT:-} =~ ^[0-9a-f-]{36}$ ]] || die "malformed lease $LEASE"
  OWNERS=("t2mtu-$TOKEN-g1p" "t2mtu-$TOKEN-g1v" "t2mtu-$TOKEN-g2p" "t2mtu-$TOKEN-g2v")
  GID=("$GID0" "$GID1" "$GID2" "$GID3")
}

preflight(){
  local c=() i s mtu owner cfg
  [ ! -e "$LEASE" ] && [ ! -L "$LEASE" ] || c+=("lease exists: $LEASE (retain for manual recovery)")
  pgrep -f '[b]ash (.*/)?testrig-offload-quick.sh' >/dev/null && c+=("a quick harness is running")
  [ "$(dut_mtus)" = "1500 1500 1500 1500" ] || c+=("DUT MTUs are not all 1500: $(dut_mtus)")
  if cfg=$(dut_config_mtus); then
    [ "$cfg" = "- - - -" ] || c+=("DUT has configured MTUs (vif10 vif20 eth3 eth4): $cfg")
  else
    c+=("cannot inspect DUT MTU configuration")
  fi
  offload_configured || c+=("DUT eth3/eth4 lack offload ipv4+ipv6")
  armed_check on || c+=("fe_arm does not show 0x10 and 0x11 armed")
  for i in 0 1 2 3; do
    s=$(gen_state ${GLINKS[$i]}) || { c+=("cannot read ${GLINKS[$i]}"); continue; }
    read -r _ _ _ mtu owner <<<"$s"
    [ "$mtu" = 1500 ] || c+=("${GLINKS[$i]} MTU $mtu")
    [ "$owner" = - ] || c+=("${GLINKS[$i]} already owned by $owner")
  done
  if [ "${#c[@]}" -gt 0 ]; then printf 'CONFLICT: %s\n' "${c[@]}" >&2; return 1; fi
  say "preflight: DUT and generators at MTU 1500, unowned, ASK armed on 0x10/0x11"
}

df_probe(){
  local s4=$((MTU-28)) s6=$((MTU-48)) bad=0 p
  local g1p4=${QUICK_G1_IPV4:-10.99.1.112} g2p4=${QUICK_G2_IPV4:-10.99.2.113}
  local g1v4=${QUICK_G1_VLAN4:-10.99.10.112} g2v4=${QUICK_G2_VLAN4:-10.99.20.113}
  local g1p6=${QUICK_G1_IPV6:-fd99:1::112} g2p6=${QUICK_G2_IPV6:-fd99:2::113}
  local g1v6=${QUICK_G1_VLAN6:-fd99:10::112} g2v6=${QUICK_G2_VLAN6:-fd99:20::113}
  for p in "$g1p4" "$g2p4" "$g1v4" "$g2v4" "$g1p6" "$g2p6" "$g1v6" "$g2v6"; do
    [[ $p =~ ^[[:xdigit:].:]+$ ]] || { echo "ERROR: invalid DF probe endpoint" >&2; return 1; }
  done
  for p in "$g1p4 $g2p4" "$g1v4 $g2v4" "$g1v4 $g2p4"; do
    set -- $p
    on g1 "ping -M do -c 2 -W 2 -s $s4 -I $1 $2 >/dev/null" || { echo "DF ping $s4 B $1 -> $2 failed" >&2; bad=1; }
    on g2 "ping -M do -c 2 -W 2 -s $s4 -I $2 $1 >/dev/null" || { echo "DF ping $s4 B $2 -> $1 failed" >&2; bad=1; }
  done
  for p in "$g1p6 $g2p6" "$g1v6 $g2v6" "$g1v6 $g2p6"; do
    set -- $p
    on g1 "ping -6 -M do -c 2 -W 2 -s $s6 -I $1 $2 >/dev/null" || { echo "DF ping $s6 B $1 -> $2 failed" >&2; bad=1; }
    on g2 "ping -6 -M do -c 2 -W 2 -s $s6 -I $2 $1 >/dev/null" || { echo "DF ping $s6 B $2 -> $1 failed" >&2; bad=1; }
  done
  [ "$bad" = 0 ]
}

up(){
  local i s a b m cfg tmp
  preflight || die "preflight failed; nothing changed"
  TOKEN=${RIG_ASK2_JUMBO_TOKEN:-$(od -An -N8 -tx1 /dev/urandom | tr -d ' \n')}
  BOOT=$(on dut 'cat /proc/sys/kernel/random/boot_id')
  GID=()
  for i in 0 1 2 3; do s=$(gen_state ${GLINKS[$i]}); read -r a b m _ <<<"$s"; GID+=("$a $b $m"); done
  tmp=$(mktemp "$LEASE_ROOT/.lease.XXXXXXXX") || die "cannot create lease"
  { printf 'TOKEN=%s\nBOOT=%s\n' "$TOKEN" "$BOOT"
    for i in 0 1 2 3; do printf 'GID%d=%q\n' "$i" "${GID[$i]}"; done; } > "$tmp"
  if ! ln -- "$tmp" "$LEASE"; then
    rm -- "$tmp"
    die "cannot take lease: $LEASE exists"
  fi
  rm -- "$tmp"
  lease_read
  say "lease $TOKEN taken (DUT boot $BOOT)"
  for i in 0 1 2 3; do gen_mtu claim "$i" 1500 "$MTU" || die "claim ${GLINKS[$i]} failed"; done
  [ "$(on dut 'cat /proc/sys/kernel/random/boot_id')" = "$BOOT" ] ||
    die "DUT rebooted during generator claims; lease kept"
  cfg=$(dut_config_mtus) || die "cannot inspect DUT MTU configuration; lease kept"
  [ "$cfg" = "- - - -" ] && [ "$(dut_mtus)" = "1500 1500 1500 1500" ] ||
    die "DUT MTU changed during generator claims; lease kept"
  offload_configured || die "DUT offload configuration changed during generator claims; lease kept"
  armed_check on || die "DUT offload is not armed; lease kept"
  for i in 0 1 2 3; do gen_mtu check "$i" 1500 "$MTU" || die "${GLINKS[$i]} claim changed; lease kept"; done
  offload off
  vyos "set interfaces ethernet eth3 mtu $MTU" "set interfaces ethernet eth4 mtu $MTU" \
       "set interfaces ethernet eth3 vif 10 mtu $MTU" "set interfaces ethernet eth4 vif 20 mtu $MTU" ||
    die "DUT MTU $MTU commit failed"
  for i in 0 2 1 3; do gen_mtu set "$i" 1500 "$MTU" || die "${GLINKS[$i]} MTU $MTU failed"; done
  [ "$(dut_mtus)" = "$MTU $MTU $MTU $MTU" ] || die "DUT MTU readback: $(dut_mtus)"
  for i in 0 1 2 3; do gen_mtu check "$i" "$MTU" 1500 || die "${GLINKS[$i]} readback failed"; done
  say "MTU $MTU read back on DUT eth3/eth4/eth3.10/eth4.20 and four generator links"
  offload on
  sleep 2
  df_probe || die "a path does not carry MTU $MTU frames"
  say "DF probes at MTU $MTU passed: routed, VLAN and VLAN-port, IPv4 and IPv6, both directions"
}

down(){
  local i cfg c=() cfg_values=() boot h dev s index link mac mtu owner extra
  local want_index want_link want_mac all_gen_1500=1
  local owned=() gen_mtus=()
  lease_read || { say "no lease; nothing to restore"; return 0; }
  [ -z "${RIG_ASK2_JUMBO_TOKEN:-}" ] || [ "$TOKEN" = "$RIG_ASK2_JUMBO_TOKEN" ] ||
    die "DUT jumbo lease belongs to another run"
  boot=$(on dut 'cat /proc/sys/kernel/random/boot_id') || die "DUT unreachable; lease kept"
  [ "$boot" = "$BOOT" ] || die "DUT rebooted since lease acquisition; ownership is uncertain; lease kept"
  pgrep -f '[b]ash (.*/)?testrig-offload-quick.sh' >/dev/null && die "a quick harness is running; lease kept"
  cfg=$(dut_config_mtus) || die "cannot inspect DUT MTU configuration; lease kept"
  read -r -a cfg_values <<< "$cfg"
  [ "${#cfg_values[@]}" = 4 ] || die "incomplete DUT MTU configuration; lease kept"
  for i in 0 1 2 3; do
    case ${cfg_values[$i]} in
      -) ;;
      "$MTU") c+=("delete ${DUT_NODES[$i]}") ;;
      *) die "DUT ${DUT_NODES[$i]} holds a foreign value; lease kept" ;;
    esac
  done
  if offload_configured; then
    armed_check on || die "configured DUT offload is not armed; lease kept"
  elif offload_unconfigured; then
    armed_check off || die "unconfigured DUT offload is not disarmed; lease kept"
  else
    die "DUT offload configuration is partial or foreign; lease kept"
  fi
  for i in 0 1 2 3; do
    read -r h dev <<<"${GLINKS[$i]}"
    s=$(gen_state "$h" "$dev") || die "cannot inspect $h $dev; lease kept"
    read -r index link mac mtu owner extra <<<"$s"
    read -r want_index want_link want_mac <<<"${GID[$i]}"
    [ "$index $link $mac" = "$want_index $want_link $want_mac" ] && [ -z "$extra" ] ||
      die "$h $dev identity changed; lease kept"
    case "$mtu:$owner" in
      "1500:-") owned[$i]=0 ;;
      "1500:${OWNERS[$i]}") owned[$i]=1 ;;
      "$MTU:${OWNERS[$i]}") owned[$i]=1; all_gen_1500=0 ;;
      *) die "$h $dev MTU or ownership changed; lease kept" ;;
    esac
    gen_mtus[$i]=$mtu
  done
  for i in 0 1 2 3; do
    if [ "${owned[$i]}" = 1 ]; then
      if [ "${gen_mtus[$i]}" = 1500 ]; then
        gen_mtu check "$i" 1500 "$MTU" || die "${GLINKS[$i]} ownership changed; lease kept"
      else
        gen_mtu check "$i" "$MTU" 1500 || die "${GLINKS[$i]} ownership changed; lease kept"
      fi
    fi
  done
  if [ "$cfg" = "- - - -" ]; then
    [ "$all_gen_1500" = 1 ] && [ "$(dut_mtus)" = "1500 1500 1500 1500" ] ||
      die "baseline MTUs changed without owned DUT configuration; lease kept"
    for i in 0 1 2 3; do gen_mtu release "$i" "$MTU" 1500 || die "${GLINKS[$i]} release failed; lease kept"; done
    rm -f "$LEASE"
    say "unchanged MTUs verified; generator claims released"
    return
  fi
  for i in 0 1 2 3; do
    [ "${owned[$i]}" = 1 ] || die "${GLINKS[$i]} claim is missing; lease kept"
  done
  if offload_configured || armed_check on; then offload off; fi
  for i in 1 3 0 2; do gen_mtu set "$i" "$MTU" 1500 || die "${GLINKS[$i]} restore failed; lease kept"; done
  [ "$(on dut 'cat /proc/sys/kernel/random/boot_id')" = "$BOOT" ] ||
    die "DUT rebooted during restoration; lease kept"
  [ "$(dut_config_mtus)" = "$cfg" ] || die "DUT MTU configuration changed during restoration; lease kept"
  [ "${#c[@]}" = 0 ] || vyos "${c[@]}" || die "DUT MTU restore commit failed; lease kept"
  [ "$(dut_mtus)" = "1500 1500 1500 1500" ] || die "DUT MTU readback after restore: $(dut_mtus); lease kept"
  offload on
  armed_check on || die "DUT offload re-arm did not settle; generator ownership retained"
  for i in 0 1 2 3; do gen_mtu release "$i" "$MTU" 1500 || die "${GLINKS[$i]} release failed; lease kept"; done
  rm -f "$LEASE"
  say "MTU 1500 restored and verified on DUT and generators; ASK re-armed; lease released"
}

status(){
  say "DUT MTUs (eth3 eth4 eth3.10 eth4.20): $(dut_mtus); configured: $(dut_config_mtus)"
  fe_state | grep -E '^(Armed|Faulted|FE fault)'
  for i in 0 1 2 3; do echo "${GLINKS[$i]}: $(gen_state ${GLINKS[$i]})"; done
  if [ -f "$LEASE" ]; then echo "lease:"; cat "$LEASE"; else echo "lease: none"; fi
}

case "$MODE" in
  preflight) preflight; exit ;;
  status) status; exit ;;
  down) down; exit ;;
  up) up; exit ;;
esac

RUN=${OUTDIR:?set OUTDIR to the matrix run directory}
REPS=${REPS:-3}; DUR=${DUR:-32}; STEADY_FROM=${STEADY_FROM:-10}; STREAMS=${STREAMS:-8}
export DUR STEADY_FROM STREAMS MEASURE_TO=${MEASURE_TO:-$((DUR-2))}
mkdir -p "$RUN"
up_done=0
finish(){ local rc=$?; trap - EXIT; [ "$up_done" = 1 ] && { down || rc=1; }; exit "$rc"; }
trap finish EXIT
trap 'exit 130' INT TERM
up_done=1
up
read -r -a UNI <<<"${CELLS_UNI-unicast-v4 unicast-v6 unicast-v4-rev unicast-v6-rev nat-v4 nat-v6 nat-v4-rev nat-v6-rev vlan-v4 vlan-v6 vlan-v4-rev vlan-v6-rev vlanport-v4 vlanport-v6 portvlan-v4 portvlan-v6}"
read -r -a BI <<<"${CELLS_BI-unicast-v4 unicast-v6 nat-v4 nat-v6 vlan-v4 vlan-v6 vlanport-v4 vlanport-v6}"
for r in $(seq 1 "$REPS"); do
  if [ "${#UNI[@]}" -gt 0 ]; then
    say "9k unidir rep $r"
    DUT_KIND=ask2 RIG_PRESERVE_RPS=1 BIDIR=0 OUT="$RUN/quick9k-uni-r$r.csv" J="$RUN/json9k-r$r" \
      "$HERE/testrig-offload-quick.sh" "${UNI[@]}"
  fi
  if [ "${#BI[@]}" -gt 0 ]; then
    say "9k bidir rep $r"
    DUT_KIND=ask2 RIG_PRESERVE_RPS=1 BIDIR=1 OUT="$RUN/quick9k-bidir-r$r.csv" J="$RUN/json9k-r$r" \
      "$HERE/testrig-offload-quick.sh" "${BI[@]}"
  fi
  armed_check on || die "ASK lost 0x10/0x11 arming during rep $r"
done
say "9k done"
