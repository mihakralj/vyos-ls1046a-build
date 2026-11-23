#!/usr/bin/env bash
# testrig-ask2-wan.sh — the combined-WAN rows of the ASK2 matrix (plans/ASK2-PERF-MATRIX-PLAN.md), measured
# whatever the offload state (each cell records its HW/SW verdict):
#   nat+ppp+vlan  LAN dell2 (eth4) <-> WAN dell1 over PPPoE on VLAN 7 (DUT pppoe7 on eth3.7), NAT44 out of
#             pppoe7; IPv6 routed over the same session (fd99:57::/64)
#   port-forward  destination NAT on the WAN port: dell1 connects to 10.99.1.185 / fd99:1::185:5201, forwarded
#             to dell2 (DNAT44 / DNAT66)
#   wg+nat+pp+vl  LAN NAT44 into WireGuard wgw0 (10.99.230.0/24, fd99:230::/64) whose outer UDP rides pppoe7
#             on VLAN 7 to dell1; IPv6 inner routed
# Upload = LAN -> WAN, download = WAN -> LAN, bidir = both; IPv4 and IPv6; REPS reps, the table keeps the fastest.
#
# Resource ownership: one run holds an exclusive local lease (flock + a lease directory with a journal).
# Preflight is read-only and refuses to start if any resource the run needs already exists. Each resource is
# journaled before it is created and carries a per-run marker: link altnames (ask2wan-<id>-*), the
# description ask2-wan-<id> on the DUT's VyOS nodes, proto/metric tags on routes and rules, and a private
# /var/lib/ask2-wan-<id>/ on dell1 (/run is noexec there) that holds the second concentrator's pidfile, options and pppd hook scripts
# (pppd's shared /etc/ppp hooks and the pppoe10 concentrator are never run or touched). Teardown walks the
# journal backwards and deletes only resources that still carry the marker; anything uncertain keeps the
# lease, and the next run refuses until `teardown <lease-dir>` succeeds. Runs remain quarantined, and a
# lease that journaled PPP processes requires manual recovery. The DUT config is never saved.
#
# Usage: OUTDIR=<matrix run dir> bin/testrig-ask2-wan.sh [run]   setup, cells, teardown
#        bin/testrig-ask2-wan.sh setup                           setup only; prints the lease directory
#        bin/testrig-ask2-wan.sh teardown <lease-dir>            release a kept or failed lease
#        bin/testrig-ask2-wan.sh preflight                       read-only conflict check
# Env:   REPS=3 DUR=10 STEADY_FROM=3 BIDIR_DUR=20 BIDIR_STEADY_FROM=12 KEEP=1 (run: keep the environment)
#        WAN_LEASE_ROOT (default ~/.local/state/testrig-ask2-wan)
# Output: quick-wan-uni-r<N>.csv / quick-wan-bidir-r<N>.csv in OUTDIR (bin/ask2-matrix-table.py reads them).
# Never run it next to other rig traffic.
set -uo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
. "$HERE/testrig-env.sh"
if [ "${RIG_PARITY:-0}" = 1 ]; then . "$HERE/testrig-parity-profile.sh" || exit 2; fi
MODE=${1:-run}
case $MODE in run|setup|preflight) [ "$#" -le 1 ] ;; teardown) [ "$#" -eq 2 ] ;; *) false ;; esac ||
  { echo "usage: $0 [run|setup|preflight] | teardown <lease-dir>" >&2; exit 2; }
if [[ $MODE = run || $MODE = setup ]] &&
   [ "${RIG_WAN_LIVE_APPROVED:-0}" != 1 ]; then
  echo "ABORT: WAN setup remains gated: PPP channel lifetime and process drain are unproven" >&2
  exit 2
fi
rig_require ASK2_DUT GEN1 GEN2 GEN_IF PPPOE_USER PPPOE_PLUGIN
REPS=${REPS:-3}; export DUR=${DUR:-10} STEADY_FROM=${STEADY_FROM:-3}
BIDIR_DUR=${BIDIR_DUR:-20}; BIDIR_STEADY_FROM=${BIDIR_STEADY_FROM:-12}
LEASES=${WAN_LEASE_ROOT:-${XDG_STATE_HOME:-$HOME/.local/state}/testrig-ask2-wan}
SSHO=(-o BatchMode=yes -o ConnectTimeout=8)
[ -z "$RIG_KEY" ] || SSHO+=(-i "$RIG_KEY")
VID=7; WPORT=51870; IF7="$GEN_IF.$VID"
WAN_RULE6=(10957 157 157 fd99:57::1 fd99:2::/64)
WAN_WGROUTE=(far 10958 158 fd99:230::112 fd99:2::/64 wgw0 1420 158)
WAN_G2_ROUTES=(
  "10.99.57.0/24 10.99.2.185"
  "10.99.230.0/24 10.99.2.185"
  "fd99:57::/64 fd99:2::185"
  "fd99:230::/64 fd99:2::185"
)
OWN=$HERE/testrig-wan-own.sh; ROUTE=$HERE/testrig-wg-route.sh
NAT=$HERE/testrig-wan-nat.sh
host(){ case $1 in dut) echo "$ASK2_DUT";; g1) echo "$GEN1";; g2) echo "$GEN2";; esac; }
on(){ local h=$1; shift; ssh -n "${SSHO[@]}" "$(host "$h")" "$@"; }
onin(){ local h=$1; shift; ssh "${SSHO[@]}" "$(host "$h")" "$@"; }   # forwards stdin (heredoc scripts)
own(){ local h=$1; shift; local a; printf -v a ' %q' "$@"; ssh "${SSHO[@]}" "$(host "$h")" "sudo sh -s --$a" < "$OWN"; }
say(){ echo "[$(date +%T)] $*"; }

# The DUT's VyOS nodes the run owns, each tagged with its description.
VY_NODES=("interfaces ethernet eth3 vif $VID" "interfaces pppoe pppoe7" "protocols static route6 fd99:57::/64")
vyos(){ # commit set/delete lines, never save; fails on a commit error
  local out
  if ! out=$({ echo 'source /opt/vyatta/etc/functions/script-template'; echo configure; printf '%s\n' "$@"
               echo commit; echo 'exit discard'; } | ssh "${SSHO[@]}" "$ASK2_DUT" 'vbash -s' 2>&1); then
    printf '%s\n' "$out" | grep -viE 'password' >&2 || true
    echo "ERROR: DUT configuration command failed" >&2; return 1
  fi
  if grep -qiE 'commit failed|failed|error|invalid|not valid' <<<"$out"; then
    grep -vE '^(\[edit\]|$)' <<<"$out" | grep -viE 'password' >&2; return 1; fi
}
vy_desc(){ on dut "cli-shell-api returnActiveValue $1 description"; }
vy_exists(){ on dut "cli-shell-api existsActive $1"; }
dut_ppp_drained(){
  local i
  for i in $(seq 1 30); do
    on dut "test ! -e /sys/class/net/pppoe7 &&
      ! systemctl is-active --quiet ppp@pppoe7.service 2>/dev/null &&
      ! ps -C pppd -o args= | grep -q '[p]ppoe7'" && return 0
    sleep 1
  done
  echo "DUT pppoe7 link, unit, or process did not drain" >&2
  return 1
}

preflight(){ # read-only: list every conflict, return 1 if any
  local c=() n rc out
  for n in "${VY_NODES[@]}"; do
    if vy_exists "$n"; then c+=("DUT config node exists: $n")
    else rc=$?; [ "$rc" = 1 ] || c+=("cannot query DUT config node $n (status $rc)"); fi
  done
  if out=$(on dut 'ip -o link show'); then
    grep -qE '^[0-9]+: (pppoe7|eth3\.7|wgw0)(@[^:]*)?:' <<<"$out" && c+=("DUT link pppoe7, eth3.7 or wgw0 exists")
  else c+=("cannot query DUT links"); fi
  if out=$(on dut 'sudo nft list tables'); then
    grep -q ask2q <<<"$out" && c+=("DUT nft table ask2q exists (a quick run is active or died)")
    if on dut 'sudo nft list ruleset' | grep -q 'comment "ask2-wan-'; then
      c+=("DUT contains a tokenized ask2-wan NAT table")
    fi
  else c+=("cannot query DUT nft tables"); fi
  if out=$(on g1 'ip -o link show && ip -d -o link show'); then
    grep -qE "^[0-9]+: ($IF7@|wgw0:)|vlan protocol 802.1Q id $VID " <<<"$out" &&
      c+=("dell1 link $IF7/wgw0 or another VLAN $VID exists")
  else c+=("cannot query dell1 links"); fi
  if out=$(on g1 'ip -6 rule show pref 10957 && ip -6 rule show pref 10958'); then
    [ -z "$out" ] || c+=("dell1 IPv6 rule pref 10957/10958 in use")
  else c+=("cannot query dell1 IPv6 rules"); fi
  if out=$(on g1 'ip -N -6 route show table all'); then
    grep -qE '(^| )table (157|158)( |$)' <<<"$out" && c+=("dell1 route table 157/158 in use")
  else c+=("cannot query dell1 IPv6 routes"); fi
  if out=$(on g1 'ip -o addr show'); then
    grep -qE ' (10\.99\.(57|230)\.|fd99:(57|230)::)' <<<"$out" &&
      c+=("dell1 already holds a 10.99.57/230 or fd99:57/230 address")
  else c+=("cannot query dell1 addresses"); fi
  if on g1 "pgrep -af '^/usr/sbin/pppoe-server .*-I $IF7( |$)'" >/dev/null; then
    c+=("dell1 pppoe-server already serves $IF7")
  else rc=$?; [ "$rc" = 1 ] || c+=("cannot query dell1 PPPoE processes (status $rc)"); fi
  if out=$(on g1 "systemctl list-units --all --no-legend 'ask2wan-*.service' 2>/dev/null"); then
    [ -z "$out" ] || c+=("dell1 has an existing owned WAN transient service")
  else c+=("cannot query dell1 WAN services"); fi
  if out=$(on g2 'for r in 10.99.57.0/24 10.99.230.0/24; do ip route show exact "$r" || exit; done; for r in fd99:57::/64 fd99:230::/64; do ip -6 route show exact "$r" || exit; done'); then
    [ -z "$out" ] || c+=("dell2 already routes a WAN prefix")
  else c+=("cannot query dell2 routes"); fi
  if [ "${#c[@]}" -gt 0 ]; then printf 'CONFLICT: %s\n' "${c[@]}" >&2; return 1; fi
}

# ---- lease ----
LEASE=""; RID=""
lease_take(){
  mkdir -p "$LEASES" || return 1
  exec 8>"$LEASES/.lock"; flock -n 8 || { echo "ABORT: another WAN run holds $LEASES/.lock" >&2; return 1; }
  local old; old=$(find "$LEASES" -mindepth 1 -maxdepth 1 -type d -name 'lease-*' | head -1)
  [ -z "$old" ] || { echo "ABORT: unreleased lease $old; run: $0 teardown $old" >&2; return 1; }
  RID=$(od -An -N8 -tx1 /dev/urandom | tr -d ' \n'); LEASE=$LEASES/lease-$RID
  mkdir "$LEASE" && printf '%s\n' "$RID" > "$LEASE/id" && : > "$LEASE/journal"
}
j(){ printf '%s\n' "$*" >> "$LEASE/journal"; }   # journal BEFORE creating, so an uncertain outcome is retried

setup(){
  local D=/var/lib/ask2-wan-$RID O="ask2wan-$RID" DESC="ask2-wan-$RID"
  local unit="ask2wan-$RID.service" boot nat_token i
  say "wan setup, lease $LEASE"
  # dell1: private run dir, VLAN 7, second concentrator whose pppd runs only this run's hook scripts
  say "wan stage: generator owned resources"
  j g1 rundir "$D" "$O-run"; own g1 rundir-create "$D" "$O-run" || return 1
  j g1 link "$IF7" "$O-v7"; own g1 link-create "$IF7" "$O-v7" link "$GEN_IF" type vlan id "$VID" || return 1
  on g1 "sudo ip link set $IF7 up" || return 1
  j g1 rule6 "${WAN_RULE6[@]}"
  onin g1 "sudo sh -s" <<EOS || return 1
set -e
printf '#!/bin/sh\nexit 0\n' > $D/noop; chmod 755 $D/noop
cat > $D/ipv6-up <<'S'
#!/bin/sh
ip -6 addr replace fd99:57::1/64 dev "\$1" nodad
ip -6 route replace fd99:2::/64 dev "\$1" table 157 proto 157 metric 157
[ -n "\$(ip -6 rule show pref 10957)" ] || ip -6 rule add pref 10957 from fd99:57::1 to fd99:2::/64 lookup 157 protocol 157
S
chmod 755 $D/ipv6-up
{ cat /etc/ppp/pppoe-server-options
  for h in ip-pre-up ip-up ip-down ipv6-down; do echo "\$h-script $D/noop"; done
  echo "ipv6-up-script $D/ipv6-up"; } > $D/options
EOS
  on g1 "sudo test -x $D/ipv6-up -a -s $D/options" || { echo "ABORT: dell1 hook scripts missing" >&2; return 1; }
  say "wan stage: transient PPPoE concentrator"
  boot=$(on g1 'cat /proc/sys/kernel/random/boot_id') || return 1
  [[ $boot =~ ^[0-9a-f-]{36}$ ]] || return 1
  j g1 procs "$unit" "$boot"
  on g1 "sudo systemd-run --quiet --collect --unit='$unit' \
    --service-type=forking --property=PIDFile='$D/pppoe-server.pid' \
    --property=KillMode=control-group --property=TimeoutStopSec=20s \
    /usr/sbin/pppoe-server -k -g '$PPPOE_PLUGIN' -I '$IF7' \
    -L 10.99.57.1 -R 10.99.57.10 -N 2 -C 'ASK2-WAN7-$RID' \
    -X '$D/pppoe-server.pid' -O '$D/options'" || return 1
  local service_ok=0
  for i in $(seq 1 20); do
    if on g1 "systemctl is-active --quiet '$unit' &&
      systemctl show --property=MainPID --value '$unit' |
        grep -Eq '^[1-9][0-9]*$'"; then
      service_ok=1
      break
    fi
    sleep 0.1
  done
  [ "$service_ok" = 1 ] || {
    echo "ABORT: transient PPPoE service did not publish an active PID" >&2
    on g1 "systemctl show --no-pager '$unit' -p ActiveState -p SubState \
      -p MainPID" >&2 || true
    return 1
  }
  say "wan stage: DUT PPPoE configuration"
  # DUT: VLAN 7 and pppoe7 (same account as pppoe10), every node tagged with this run's description
  local PW; PW=$(on dut 'cli-shell-api returnActiveValue interfaces pppoe pppoe10 authentication password')
  [ -n "$PW" ] || { echo "ABORT: no pppoe10 password in the DUT config" >&2; return 1; }
  j dut vyos "$DESC"
  vyos "set interfaces ethernet eth3 vif $VID description '$DESC'" \
       "set interfaces pppoe pppoe7 description '$DESC'" \
       "set interfaces pppoe pppoe7 source-interface eth3.$VID" \
       "set interfaces pppoe pppoe7 authentication username '$PPPOE_USER'" \
       "set interfaces pppoe pppoe7 authentication password '$PW'" \
       "set interfaces pppoe pppoe7 mtu 1492" "set interfaces pppoe pppoe7 no-default-route" \
       "set interfaces pppoe pppoe7 no-peer-dns" "set interfaces pppoe pppoe7 ipv6 address autoconf" \
       "set protocols static route6 fd99:57::/64 interface pppoe7" \
       "set protocols static route6 fd99:57::/64 description '$DESC'" || { echo "ABORT: DUT commit failed" >&2; return 1; }
  for i in $(seq 1 30); do on dut 'ip -4 -o addr show dev pppoe7 2>/dev/null' | grep -q 'peer 10.99.57.1' && break; sleep 2; done
  if ! on dut 'ip -4 -o addr show dev pppoe7' | grep -q 'peer 10.99.57.1'; then
    echo "ABORT: pppoe7 did not come up" >&2
    on dut 'sudo journalctl -u ppp@pppoe7 --no-pager -n 12' >&2
    on g1 "pgrep -af '^/usr/sbin/pppoe-server .*$D/'; sudo journalctl -t pppd -t pppoe-server --no-pager -n 8" >&2
    return 1
  fi
  say "wan stage: generator WAN routes"
  # dell2 (LAN): the WAN-side prefixes through the DUT, tagged proto 159 metric 4159
  local dst gw route
  for route in "${WAN_G2_ROUTES[@]}"; do
    read -r dst gw <<<"$route"
    j g2 route "$dst" "$gw"; own g2 route-add "$dst" "$gw" 159 4159 </dev/null || return 1
  done
  # WireGuard over pppoe7: owned wgw0 on both ends; dell1's IPv6 return path to the LAN is a tagged policy
  say "wan stage: owned WireGuard links and routes"
  # route (table 158), not a main-table route over dell1's own fd99:2::/64
  local kd kg pd pg
  j dut link wgw0 "$O-wg"; own dut link-create wgw0 "$O-wg" type wireguard || return 1
  j g1 link wgw0 "$O-wg"; own g1 link-create wgw0 "$O-wg" type wireguard || return 1
  kd=$(on dut 'wg genkey'); pd=$(printf '%s\n' "$kd" | ssh "${SSHO[@]}" "$ASK2_DUT" 'wg pubkey')
  kg=$(on g1 'wg genkey'); pg=$(printf '%s\n' "$kg" | ssh "${SSHO[@]}" "$GEN1" 'wg pubkey')
  printf '%s\n' "$kg" | ssh "${SSHO[@]}" "$GEN1" "sudo sh -c 'wg set wgw0 private-key /dev/stdin listen-port $WPORT peer $pd allowed-ips 10.99.230.1/32,fd99:2::/64,fd99:230::1/128 &&
    ip addr add 10.99.230.112/24 dev wgw0 && ip -6 addr add fd99:230::112/64 dev wgw0 nodad && ip link set wgw0 mtu 1420 up'" || return 1
  local route_args
  j g1 wgroute "${WAN_WGROUTE[@]}"
  printf -v route_args ' %q' "${WAN_WGROUTE[@]}"
  ssh "${SSHO[@]}" "$GEN1" "sudo sh -s -- add$route_args" < "$ROUTE" || return 1
  printf '%s\n' "$kd" | ssh "${SSHO[@]}" "$ASK2_DUT" "sudo sh -c 'wg set wgw0 private-key /dev/stdin listen-port $WPORT peer $pg endpoint 10.99.57.1:$WPORT allowed-ips 10.99.230.0/24,fd99:230::/64 persistent-keepalive 10 &&
    ip addr add 10.99.230.1/24 dev wgw0 && ip -6 addr add fd99:230::1/64 dev wgw0 nodad && ip link set wgw0 mtu 1420 up'" || return 1
  if [ "${WGFP_WAN:-1}" = 1 ]; then
    local wg_token
    wg_token=$(od -An -N16 -tx1 /dev/urandom | tr -d ' \n')
    [[ $wg_token =~ ^[0-9a-f]{32}$ ]] || return 1
    j dut wgfp_lease "$wg_token"
    onin dut "sudo sh -s -- '$wg_token' 'wgo-$wg_token-wg'" <<'EOS' || return 1
set -eu
tok=$1 alias=$2
P=/sys/module/wireguard/parameters/wgfp_peer_lease
[ -w "$P" ] || { echo "ERROR: $P not available" >&2; exit 1; }
printf 'reserve %s\n' "$tok" > "$P"
ip link property add dev wgw0 altname "$alias"
printf 'bind %s %s\n' "$tok" "$alias" > "$P"
EOS
  fi
  nat_token=$(od -An -N16 -tx1 /dev/urandom | tr -d ' \n')
  [[ $nat_token =~ ^[0-9a-f]{32}$ ]] || return 1
  j dut nat "$nat_token"
  onin dut 'sudo sh -s -- add '"$nat_token" < "$NAT" || return 1
  # verify every path the cells use
  for ((i=0; i<10; i++)); do on dut 'ping -c1 -W1 10.99.230.112 >/dev/null 2>&1' && break; sleep 1; done
  local p ok=1
  # IPv4 from the DUT (the cells NAT it; un-NATed LAN pings would return asymmetrically), IPv6 end to end
  for p in "dut 10.99.57.1" "dut 10.99.230.112" "g2 -6 fd99:57::1" "g2 -6 fd99:230::112"; do
    on ${p%% *} "ping -c2 -W2 ${p#* } >/dev/null" || { echo "path check failed: ${p%% *} -> ${p#* }" >&2; ok=0; }
  done
  say "pppoe7: $(on dut 'ip -4 -o addr show dev pppoe7' | grep -o 'inet [^ ]* peer [^ ]*')  wgw0 handshake: $(on dut 'sudo wg show wgw0 latest-handshakes' | awk '{print ($2>0)?"yes":"no"}')"
  [ $ok = 1 ]
}

undo(){ # undo one journal line; 0 = gone or never created, 1 = left in place
  local h=$1 kind=$2; shift 2
  case $kind in
    rundir) own "$h" rundir-delete "$@" ;;
    link) own "$h" link-delete "$@" ;;
    route) own "$h" route-delete "$1" "$2" 159 4159 ;;
    procs) own "$h" service-stop "$@" ;;
    nat)
      onin "$h" 'sudo sh -s -- delete '"$1" < "$NAT" ;;
    wgfp_lease)
      onin "$h" "sudo sh -s -- '$1'" <<'EOS'
set -eu
tok=$1
P=/sys/module/wireguard/parameters/wgfp_peer_lease
[ -e "$P" ] || exit 0
cur=$(cat "$P")
case "$cur" in
  unowned) ;;
  "owned=$tok dev=off")
    printf 'release %s\n' "$tok" > "$P" ;;
  "owned=$tok "*)
    printf 'stop %s\n' "$tok" > "$P"
    printf 'release %s\n' "$tok" > "$P" ;;
  *) echo "ERROR: wgfp_peer_lease owned by another actor: $cur" >&2; exit 1 ;;
esac
ip link property del dev "wgo-$tok-wg" altname "wgo-$tok-wg" 2>/dev/null || true
EOS
      ;;
    rule6) own "$h" rule6-delete "$@" ;;
    wgroute)
      local args
      printf -v args ' %q' "$@"
      ssh "${SSHO[@]}" "$(host "$h")" "sudo sh -s -- delete$args" < "$ROUTE" ;;
    vyos)
      local n d rc del=() bad=0
      for n in "${VY_NODES[@]}"; do
        if vy_exists "$n"; then
          d=$(vy_desc "$n") || { echo "cannot read DUT description for $n" >&2; return 1; }
          if [ "$d" = "$1" ]; then del+=("delete $n"); else echo "DUT $n carries description '$d', not $1; refusing" >&2; bad=1; fi
        else
          rc=$?
          [ "$rc" = 1 ] && continue
          echo "cannot query DUT config node $n (status $rc)" >&2; return 1
        fi
      done
      [ "${#del[@]}" -eq 0 ] || vyos "${del[@]}" || return 1
      for n in "${VY_NODES[@]}"; do
        if vy_exists "$n"; then
          d=$(vy_desc "$n") || { echo "cannot recheck DUT description for $n" >&2; return 1; }
          [ "$d" != "$1" ] || { echo "DUT $n still present" >&2; bad=1; }
        else
          rc=$?; [ "$rc" = 1 ] || { echo "cannot recheck DUT config node $n (status $rc)" >&2; return 1; }
        fi
      done
      dut_ppp_drained || bad=1
      return $bad ;;
    *) echo "unknown journal entry $kind" >&2; return 1 ;;
  esac
}

teardown(){ # LEASE must be set; deletes in reverse journal order, keeps the lease on any doubt
  local journal kind expected rid route valid fail=0
  local -a entry
  say "wan teardown, lease $LEASE"
  rid=${LEASE##*/lease-}
  [[ $rid =~ ^[0-9a-f]{16}$ ]] || { echo "ERROR: invalid WAN lease identity; lease retained" >&2; return 1; }
  journal=$(tac "$LEASE/journal") || { echo "ERROR: cannot read WAN journal; lease retained" >&2; return 1; }
  while read -r -a entry; do
    [ "${#entry[@]}" -ne 0 ] || continue
    kind=${entry[1]:-}
    case "${entry[0]}:$kind" in
      g1:rundir|g1:link|dut:link|g2:route) expected=4 ;;
      g1:procs) expected=4 ;;
      dut:nat|dut:wgfp_lease) expected=3 ;;
      g1:rule6) expected=7 ;;
      g1:wgroute) expected=10 ;;
      dut:vyos) expected=3 ;;
      *) echo "ERROR: invalid WAN journal entry; lease retained" >&2; return 1 ;;
    esac
    [ "${#entry[@]}" = "$expected" ] || { echo "ERROR: invalid WAN journal entry; lease retained" >&2; return 1; }
    valid=0
    case "${entry[0]}:$kind" in
      g1:rundir)
        [[ ${entry[2]} = "/var/lib/ask2-wan-$rid" && ${entry[3]} = "ask2wan-$rid-run" ]] && valid=1 ;;
      g1:procs)
        [[ ${entry[2]} = "ask2wan-$rid.service" && ${entry[3]} =~ ^[0-9a-f-]{36}$ ]] && valid=1 ;;
      g1:link)
        [[ ${entry[2]} = "$IF7" && ${entry[3]} = "ask2wan-$rid-v7" ||
           ${entry[2]} = wgw0 && ${entry[3]} = "ask2wan-$rid-wg" ]] && valid=1 ;;
      dut:link)
        [[ ${entry[2]} = wgw0 && ${entry[3]} = "ask2wan-$rid-wg" ]] && valid=1 ;;
      dut:nat|dut:wgfp_lease)
        [[ ${entry[2]} =~ ^[0-9a-f]{32}$ ]] && valid=1 ;;
      g2:route)
        for route in "${WAN_G2_ROUTES[@]}"; do
          [[ "${entry[2]} ${entry[3]}" = "$route" ]] && valid=1
        done ;;
      g1:rule6) [[ ${entry[*]:2} = "${WAN_RULE6[*]}" ]] && valid=1 ;;
      g1:wgroute) [[ ${entry[*]:2} = "${WAN_WGROUTE[*]}" ]] && valid=1 ;;
      dut:vyos) [[ ${entry[2]} = "ask2-wan-$rid" ]] && valid=1 ;;
    esac
    [ "$valid" = 1 ] || { echo "ERROR: invalid WAN journal entry; lease retained" >&2; return 1; }
  done <<<"$journal"
  while read -r -a entry; do
    [ "${#entry[@]}" -ne 0 ] || continue
    if ! undo "${entry[@]}" </dev/null; then
      printf 'LEFT IN PLACE:' >&2; printf ' %s' "${entry[@]}" >&2; printf '\n' >&2
      fail=1; break
    fi
  done <<<"$journal"
  if [ $fail = 0 ]; then rm -rf -- "$LEASE"; say "teardown complete, lease released"
  else say "TEARDOWN INCOMPLETE: lease kept at $LEASE; fix, then: $0 teardown $LEASE"; return 1; fi
  say "DUT pppoe10 $(on dut 'ip -4 -o addr show dev pppoe10' | grep -o 'peer [^ ]*')"
}

case $MODE in
  preflight) preflight && echo "preflight OK"; exit ;;
  teardown)
    LEASE=$(cd "$2" && pwd) || exit 2
    [[ $LEASE == "$LEASES"/lease-* && -f $LEASE/journal ]] || { echo "not a lease directory: $2" >&2; exit 2; }
    exec 8>"$LEASES/.lock"; flock -n 8 || { echo "ABORT: another WAN run holds the lock" >&2; exit 1; }
    teardown; exit ;;
esac

if pgrep -f '[t]estrig-offload-quick.sh|[w]g-matrix.sh|[w]g-parity.sh|[t]estrig-wireguard|[t]estrig-ask2-(9k|bridge|policer|matrix)|[s]afe9k' >/dev/null; then
  echo "ABORT: another rig job is running" >&2; exit 2; fi
lease_take || exit 2
preflight || { rm -rf -- "$LEASE"; echo "ABORT: preflight found existing resources; nothing was changed" >&2; exit 2; }
if [ "$MODE" = setup ]; then
  setup || { echo "setup failed; tearing down" >&2; teardown; exit 1; }
  echo "lease: $LEASE"; exit 0
fi
RUN=${OUTDIR:?set OUTDIR to the matrix run directory}; mkdir -p "$RUN"
[ "${KEEP:-0}" = 1 ] || trap 'teardown || exit 1' EXIT
trap 'exit 130' INT; trap 'exit 143' TERM
setup || { echo "ABORT: wan setup failed" >&2; exit 1; }
RIG_EXTERNAL_NAT_TOKEN=$(awk '$1=="dut" && $2=="nat" {print $3}' "$LEASE/journal")
[[ $RIG_EXTERNAL_NAT_TOKEN =~ ^[0-9a-f]{32}$ ]] ||
  { echo "ABORT: owned WAN NAT journal is missing" >&2; exit 1; }
export RIG_EXTERNAL_NAT_TOKEN RIG_EXTERNAL_NAT_OWNER="ask2-wan-$RIG_EXTERNAL_NAT_TOKEN"

UNI="wanfull-up-v4 wanfull-up-v6 wanfull-down-v4 wanfull-down-v6 portfwd-up-v4 portfwd-up-v6 portfwd-down-v4 portfwd-down-v6
wgwan-up-v4 wgwan-up-v6 wgwan-down-v4 wgwan-down-v6"
BI="wanfull-up-v4 wanfull-up-v6 portfwd-down-v4 portfwd-down-v6 wgwan-up-v4 wgwan-up-v6"
for r in $(seq 1 "$REPS"); do
  say "wan unidir rep $r"
  DUT_KIND=ask2 OUT=$RUN/quick-wan-uni-r$r.csv J=$RUN/json-wan-r$r "$HERE/testrig-offload-quick.sh" $UNI
  say "wan bidir rep $r"
  DUT_KIND=ask2 BIDIR=1 DUR=$BIDIR_DUR STEADY_FROM=$BIDIR_STEADY_FROM OUT=$RUN/quick-wan-bidir-r$r.csv J=$RUN/json-wan-r$r \
    "$HERE/testrig-offload-quick.sh" $BI
done
say "wan done"
[ "${KEEP:-0}" != 1 ] || echo "KEEP=1: environment left up; release with: $0 teardown $LEASE"
