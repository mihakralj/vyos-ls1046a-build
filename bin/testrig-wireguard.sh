#!/usr/bin/env bash
# testrig-wireguard.sh — WireGuard throughput harness for the dell1 / DUT / dell2 10G rig.
#
# ChaCha20-Poly1305 runs in software, using NEON on the A72. On ASK2, WGFP=1 can
# accelerate eligible forwarding and outer receive paths. The tunnel terminates
# on the DUT; the far end is dell2:
#
#   dell1 --plain--> DUT eth3 --route--> wg0 (encrypt) --UDP/51820--> DUT eth4 --> dell2 wg0 (decrypt)
#
#   inner:  dell1 10.99.1.112 -> 10.99.200.113 (dell2 wg0)       v6: fd99:1::112 -> fd99:200::113
#   outer:  DUT 10.99.2.185 <-> dell2 10.99.2.113 (OUTER=6: fd99:2::185 <-> fd99:2::113)
#   "up"   = dell1 sends  -> DUT encrypts (eth3 -> wg0 -> eth4), dell2 decrypts
#   "down" = dell2 sends  -> DUT decrypts (eth4 -> wg0 -> eth3)   (iperf3 -R)
#   "bidir"= both at once
#
# WG_PORT=eth3 mirrors the topology: the encrypted leg runs over eth3 and the plain leg over eth4:
#
#   dell2 --plain--> DUT eth4 --route--> wg0 (encrypt) --UDP/51820--> DUT eth3 --> dell1 wg0 (decrypt)
#
#   inner:  dell2 10.99.2.113 -> 10.99.200.112 (dell1 wg0)       v6: fd99:2::113 -> fd99:200::112
#   outer:  DUT 10.99.1.185 <-> dell1 10.99.1.112 (OUTER=6: fd99:1::185 <-> fd99:1::112)
#   up/down keep their meaning from the DUT's side: "up" = the DUT encrypts, "down" = the DUT decrypts.
#   Cell names get a "-eth3" suffix (wg-v4-eth3); the ceiling cell is not available in this mode.
#
# Cells:
#   plain-v4     no tunnel, dell1 -> dell2 through the DUT (software/HW path reference)
#   wg-v4        IPv4 inner through the DUT tunnel
#   wg-v6        IPv6 inner through the DUT tunnel
#   ceiling-v4   dell1 <-> dell2 wg directly over the ConnectX-3 link (enp2s0, no DUT): the
#                generator pair's own crypto ceiling, so a DUT number is never generator-limited
#
# Everything is runtime-only. Links, routes, rules and iperf3 servers are
# identified by a per-run lease; cleanup refuses resources it cannot verify.
# KEEP=1 or `setup` retains the lease for a later `teardown`.
#
# Usage: bin/testrig-wireguard.sh [setup|teardown|check|run] [cell ...]    (default: run, cells wg-v4 wg-v6)
#   bin/testrig-wireguard.sh run ceiling-v4 plain-v4 wg-v4 wg-v6
#   DIRS="up down bidir" STREAMS=4 DUR=30 bin/testrig-wireguard.sh run wg-v4
#   MODE=udp LEN=1280 RATE=800M bin/testrig-wireguard.sh run wg-v4
# Env: WG_NOTRACK=1 (outer UDP untracked by conntrack, experiment)
#      DUT_KIND=ask2|vendor   (vendor = OpenWrt NXP ASK; RIG_NS=$VENDOR_NS is implied). Hosts and key: bin/testrig-env.sh
#      WG_PORT=eth4|eth3 (the DUT port that carries the encrypted leg; default eth4)
#      REPS (repeat each cell/direction, summary mean/min/max/sd), DUT, DIRS, DUR, STEADY_FROM, STREAMS, WGMTU, OUTER=4|6, MODE=tcp|udp, LEN, RATE, OUT, KEEP
#      WG_SKIP_SERVERS=1 leaves iperf servers untouched. For run, also set
#      WG_EXTERNAL_IPERF=1 and provide the server on WG_IPERF_PORT (default 5231).
#      ASK2 software and WGFP runs both require the exclusive wgfp_peer_lease reservation.
#      WGFP_COUNTER_LOG=<path> records per-attempt WGFP counters before and after each cell.
#      WG_SKIP_FW4=1 leaves the vendor's existing forwarding policy unchanged;
#      use it only when that policy already permits the test traffic.
#      WG_CLIENT_TIMEOUT caps each iperf client in seconds (default DUR+45).
# The vendor DUT needs WireGuard tools and a kernel module matching its exact kernel ABI.
set -u
. "$(dirname "$0")/testrig-env.sh"
. "$(dirname "$0")/testrig-wgfp-counters.sh"
. "$(dirname "$0")/testrig-wg-lease.sh"
. "$(dirname "$0")/testrig-wg-resource.sh"
DUT_KIND=${DUT_KIND:-ask2}
DUT=${DUT:-$(rig_dut "$DUT_KIND")} || exit 2
if [ "$DUT_KIND" = vendor ]; then RIG_NS=${RIG_NS:-$VENDOR_NS}; else RIG_NS=${RIG_NS:-}; fi
DIRS=${DIRS:-"up down"}; DUR=${DUR:-30}; STEADY_FROM=${STEADY_FROM:-10}; STREAMS=${STREAMS:-4}
REPS=${REPS:-1}; WGMTU=${WGMTU:-1420}; OUTER=${OUTER:-4}; MODE=${MODE:-tcp}; LEN=${LEN:-1280}; RATE=${RATE:-1G}
if ! [[ $DUR =~ ^[1-9][0-9]{0,5}$ ]]; then echo "DUR must be a positive integer" >&2; exit 2; fi
WG_CLIENT_TIMEOUT=${WG_CLIENT_TIMEOUT:-$((DUR+45))}
if ! [[ $WG_CLIENT_TIMEOUT =~ ^[1-9][0-9]{0,6}$ ]] || (( WG_CLIENT_TIMEOUT <= DUR )); then
  echo "WG_CLIENT_TIMEOUT must be an integer greater than DUR" >&2; exit 2
fi
# RUN_ON=gen2 (alias dell2): run on gen2 (see testrig-offload-quick.sh): gen2 commands run locally, gen1 and
# the DUT go over ssh.
RUN_ON=${RUN_ON:-agent}
rig_require GEN1; [ "$RUN_ON" = dell2 ] || rig_require GEN2
OUTDIR=${OUTDIR:-$RIG_OUT}
OUT=${OUT:-$OUTDIR/wg-$(date +%Y%m%d-%H%M)-${DUT_KIND}.csv}
J=${J:-$OUTDIR/json-wg}; mkdir -p "$J"
SSHO="-o BatchMode=yes -o ConnectTimeout=8 $RIG_SSHK"
NSX="sudo"; [ -n "$RIG_NS" ] && NSX="sudo ip netns exec $RIG_NS"   # always root; netns when RIG_NS is set

dut(){ local c=$1; [ "$DUT_KIND" = vendor ] && c=${c//sudo /}; ssh -n $SSHO $DUT "$c"; }
duti(){ local c=$1; [ "$DUT_KIND" = vendor ] && c=${c//sudo /}; ssh $SSHO $DUT "$c"; }   # stdin passes through
WG_PORT=${WG_PORT:-eth4}
# D1 = the dell on the PLAIN leg (iperf3 client), D2 = the dell that holds the far wg0 end (iperf3 server).
# eth4 (default): D1 = dell1, D2 = dell2. eth3: the roles swap. The ceiling cell is bound to the literal dell1/dell2 pair.
case $WG_PORT in
  eth4) DA=gen1; DB=gen2; PORTSUF=""
        P4=10.99.1.112; P6=fd99:1::112; PGW4=10.99.1.185; PGW6=fd99:1::185; PSUB4=10.99.1.0/24; PSUB6=fd99:1::/64
        TI4=10.99.200.113; TI6=fd99:200::113; O_DUT4=10.99.2.185; O_DUT6=fd99:2::185; O_T4=10.99.2.113; O_T6=fd99:2::113
        PLAIN_SRV4=10.99.2.113 ;;
  eth3) DA=gen2; DB=gen1; PORTSUF="-eth3"
        P4=10.99.2.113; P6=fd99:2::113; PGW4=10.99.2.185; PGW6=fd99:2::185; PSUB4=10.99.2.0/24; PSUB6=fd99:2::/64
        TI4=10.99.200.112; TI6=fd99:200::112; O_DUT4=10.99.1.185; O_DUT6=fd99:1::185; O_T4=10.99.1.112; O_T6=fd99:1::112
        PLAIN_SRV4=10.99.1.112 ;;
  *) echo "WG_PORT must be eth3 or eth4" >&2; exit 2 ;;
esac
rig(){   # rig gen1|gen2 <-n or empty>: the command prefix that runs a command on that generator
  [ "$RUN_ON" = dell2 ] && [ "$1" = gen2 ] && { echo "bash -c"; return; }
  if [ "$1" = gen1 ]; then echo "ssh ${2:-} $SSHO $GEN1"; else echo "ssh ${2:-} $SSHO $GEN2"; fi
}
D1=$(rig $DA -n); D2=$(rig $DB -n)
D1I=$(rig $DA); D2I=$(rig $DB)
DD1=$(rig gen1 -n); DD2=$(rig gen2 -n)   # literal gen1/gen2 (ceiling cell)
DD1I=$(rig gen1);   DD2I=$(rig gen2)

WGP=51820; WGPD=51821; IPERF_P=${WG_IPERF_PORT:-5231}
if ! [[ $IPERF_P =~ ^[1-9][0-9]{0,4}$ ]] || (( IPERF_P > 65535 )); then
  echo "WG_IPERF_PORT must be a port number from 1 to 65535" >&2; exit 2
fi
if [ "$IPERF_P" != 5231 ] && [ "${WG_SKIP_SERVERS:-0}" != 1 ]; then
  echo "WG_IPERF_PORT requires WG_SKIP_SERVERS=1 and an externally managed server" >&2; exit 2
fi
if [ "$OUTER" = 6 ]; then
  DUT_OUT=$O_DUT6; D2_OUT=$O_T6; EP="[$D2_OUT]:$WGP"; FAR_EP="[$DUT_OUT]:$WGP"
else
  DUT_OUT=$O_DUT4; D2_OUT=$O_T4; EP="$D2_OUT:$WGP"; FAR_EP="$DUT_OUT:$WGP"
fi
# the far dell reaches the DUT's outer address on the same /24 (or /64), so no extra routes are needed for the outer leg.

# shellcheck disable=SC2034 # consumed by the sourced lease helper
WG_LEASE_KIND="single-$RUN_ON"
# shellcheck disable=SC2034 # consumed by the sourced lease helper
WG_LEASE_DIR="${WG_STATE_PATH:-/tmp/wg-rig}.single-lease"
# shellcheck disable=SC2034 # consumed by the sourced lease helper
WG_LEASE_COUNT=1
# shellcheck disable=SC2034 # consumed by the sourced lease helper
IPERF_BASE=$IPERF_P
WG_LINK_HELPER="$(dirname "$0")/testrig-wg-link.sh"
WG_ROUTE_HELPER="$(dirname "$0")/testrig-wg-route.sh"
WG_NFT_HELPER="$(dirname "$0")/testrig-wg-nft.sh"
# shellcheck disable=SC2034 # consumed by the sourced resource helper
WG_SERVER_HELPER="$(dirname "$0")/testrig-wg-server.py"
# shellcheck disable=SC2034 # consumed by the sourced resource helper
WG_STOP_HELPER="$(dirname "$0")/testrig-wgfp-stop.py"

# name|inner client addr (plain-leg dell)|inner server addr (far wg0 end)|family|wg
CELLS_ALL="
plain-v4|$P4|$PLAIN_SRV4|4|none
wg-v4|$P4|$TI4|4|dut
wg-v6|$P6|$TI6|6|dut
ceiling-v4|10.99.201.112|10.99.201.113|4|direct
"

# ---------- tunnel plumbing ----------
wg_link(){
  local side=$1 action=$2
  case $side in
    dut)     duti "sudo sh -s -- $action wg0 wgo-$WG_OWNER_TOKEN-dut" < "$WG_LINK_HELPER";;
    far)     $D2I "$NSX sh -s -- $action wg0 wgo-$WG_OWNER_TOKEN-far" < "$WG_LINK_HELPER";;
    direct1) $DD1I "sudo sh -s -- $action wgd wgo-$WG_OWNER_TOKEN-direct1" < "$WG_LINK_HELPER";;
    direct2) $DD2I "sudo sh -s -- $action wgd wgo-$WG_OWNER_TOKEN-direct2" < "$WG_LINK_HELPER";;
    *) return 1;;
  esac
}

wg_probe_link(){
  duti "sudo sh -s -- $1 wgc${WG_OWNER_TOKEN:0:10} wgo-$WG_OWNER_TOKEN-probe" < "$WG_LINK_HELPER"
}

wg_route(){
  local route=$1 action=$2 index priority table realm side source destination target
  case $route in
    far4)   index=0; side=far;   source=$TI4; destination=$PSUB4; target=wg0;;
    plain4) index=1; side=plain; source=$P4;  destination=10.99.200.0/24; target=$PGW4;;
    far6)   index=2; side=far;   source=$TI6; destination=$PSUB6; target=wg0;;
    plain6) index=3; side=plain; source=$P6;  destination=fd99:200::/64; target=$PGW6;;
    *) return 1;;
  esac
  priority=$((1000 + 16#${WG_OWNER_TOKEN:0:6} % 30000 + index))
  table=$((1000000 + 16#${WG_OWNER_TOKEN:6:6} * 16 + index))
  realm=$(( (16#${WG_OWNER_TOKEN:12:8} & 0x7fffffff) + 1 ))
  if [ "$side" = far ]; then
    $D2I "$NSX sh -s -- $action $side $priority $table $source $destination $target $WG_LEASE_MTU $realm" < "$WG_ROUTE_HELPER"
  else
    $D1I "$NSX sh -s -- $action $side $priority $table $source $destination $target $WG_LEASE_MTU $realm" < "$WG_ROUTE_HELPER"
  fi
}

wg_server_run(){
  case $1 in
    tunnel) $D2I "$NSX $2";;
    direct) $DD2I "sudo $2";;
    *) return 1;;
  esac
}

wg_bound_links_owned(){
  dut "name=\$(sudo ip -o link show wgo-$WG_OWNER_TOKEN-dut | sed -n 's/^[0-9]*: \\([^:@ ]*\\).*/\\1/p'); [ \"\$name\" = wg0 ]" ||
    { echo "ERROR: WGFP peer wg0 no longer belongs to this lease" >&2; return 1; }
}

wg_dut_up(){
  local k1 k2 p1 p2 route
  k1=$(dut 'wg genkey') || return 1
  p1=$(printf '%s\n' "$k1" | duti 'wg pubkey') || return 1
  k2=$($D2 'wg genkey') || return 1
  p2=$(printf '%s\n' "$k2" | $D2I 'wg pubkey') || return 1
  wg_resource_add link-dut wg_link dut create || return 1
  printf '%s\n' "$k1" | duti "sudo wg set wg0 private-key /dev/stdin listen-port $WGP peer $p2 allowed-ips $TI4/32,$TI6/128 endpoint $EP" || return 1
  dut "sudo ip addr add 10.99.200.1/24 dev wg0 && sudo ip -6 addr add fd99:200::1/64 dev wg0 && sudo ip link set wg0 mtu $WGMTU up" || return 1
  # WG_THREADED=0|1 overrides wg0's threaded NAPI (6.18 defaults to 1, the vendor 6.12 kernel to 0); the value read back is printed
  if [ -n "${WG_THREADED:-}" ]; then
    case $WG_THREADED in 0|1) ;; *) echo "WG_THREADED must be 0 or 1" >&2; return 1;; esac
    dut "echo $WG_THREADED | sudo tee /sys/class/net/wg0/threaded >/dev/null && echo wg0 threaded=\$(cat /sys/class/net/wg0/threaded)" >&2 || return 1
  fi
  # WG_NAPI_CPU=N pins the threaded-NAPI kthread (napi/wg0-*) of wg0 to CPU N (ASK2 only; the thread otherwise floats)
  if [ -n "${WG_NAPI_CPU:-}" ]; then
    [[ $WG_NAPI_CPU =~ ^[0-9]+$ ]] || { echo "WG_NAPI_CPU must be a CPU number" >&2; return 1; }
    dut "for p in \$(pgrep -f 'napi/wg0'); do sudo taskset -cp $WG_NAPI_CPU \$p >/dev/null || exit 1; done; echo napi/wg0 pinned to cpu$WG_NAPI_CPU: \$(pgrep -f 'napi/wg0' | wc -l) 'thread(s)'" >&2 || return 1
  fi
  wg_resource_add link-far wg_link far create || return 1
  printf '%s\n' "$k2" | $D2I "$NSX wg set wg0 private-key /dev/stdin listen-port $WGP peer $p1 allowed-ips 10.99.200.1/32,$PSUB4,fd99:200::1/128,$PSUB6 endpoint $FAR_EP" || return 1
  $D2 "$NSX ip addr add $TI4/24 dev wg0 && $NSX ip -6 addr add $TI6/64 dev wg0 && $NSX ip link set wg0 mtu $WGMTU up" || return 1
  for route in far4 plain4 far6 plain6; do
    wg_resource_add "route-$route" wg_route "$route" add || return 1
  done
}

wg_tunnel_start(){
  local marker="$WG_LEASE_DIR/tunnel"
  if [ -e "$marker" ] || [ -L "$marker" ]; then
    echo "ERROR: tunnel already recorded in $WG_LEASE_DIR" >&2
    return 1
  fi
  printf 'created\n' > "$marker" || return 1
  wg_dut_up
}

wg_bind(){
  wg_activate_binding wg0 "wgo-$WG_OWNER_TOKEN-dut"
}

wg_fw_add(){
  dut "nft insert rule inet fw4 forward oifname 'wg0' counter accept comment 'wgrig-$WG_OWNER_TOKEN' && nft insert rule inet fw4 forward iifname 'wg0' counter accept comment 'wgrig-$WG_OWNER_TOKEN'"
}

wg_fw_remove(){
  dut "rules=\$(nft -a list chain inet fw4 forward) || exit 1
    handles=\$(printf '%s\\n' \"\$rules\" | awk -v tag='wgrig-$WG_OWNER_TOKEN' '
      index(\$0, \"comment \\\"\" tag \"\\\"\") { for (i=1; i<NF; i++) if (\$i == \"handle\" && \$(i+1) ~ /^[0-9]+$/) print \$(i+1) }
    ') || exit 1
    for h in \$handles; do nft delete rule inet fw4 forward handle \"\$h\" || exit 1; done"
}

wg_notrack_add(){
  duti "sudo sh -s -- add $WG_OWNER_TOKEN $WG_NOTRACK $WGP" < "$WG_NFT_HELPER"
}

wg_notrack_remove(){
  duti "sudo sh -s -- delete $WG_OWNER_TOKEN" < "$WG_NFT_HELPER"
}

wg_network_up(){
  if [ "$DUT_KIND" = vendor ]; then
    if [ "${WG_SKIP_FW4:-0}" = 1 ]; then
      echo "Using the vendor's existing forwarding policy; no fw4 rules inserted" >&2
    else
      wg_resource_add fw4 wg_fw_add ||
        { echo "Failed to insert vendor fw4 rules" >&2; return 1; }
    fi
  fi
  [ "${WG_NOTRACK:-0}" = 0 ] || wg_resource_add notrack wg_notrack_add
}

wg_direct_up(){   # dell1 <-> dell2 over enp2s0 (no DUT); root namespaces
  local k1 k2 p1 p2
  k1=$($DD1 'wg genkey') || return 1
  p1=$(printf '%s\n' "$k1" | $DD1I 'wg pubkey') || return 1
  k2=$($DD2 'wg genkey') || return 1
  p2=$(printf '%s\n' "$k2" | $DD2I 'wg pubkey') || return 1
  wg_resource_add link-direct1 wg_link direct1 create || return 1
  printf '%s\n' "$k1" | $DD1I "sudo wg set wgd private-key /dev/stdin listen-port $WGPD peer $p2 allowed-ips 10.99.201.113/32 endpoint 10.99.99.113:$WGPD" || return 1
  $DD1 "sudo ip addr add 10.99.201.112/24 dev wgd && sudo ip link set wgd mtu $WGMTU up" || return 1
  wg_resource_add link-direct2 wg_link direct2 create || return 1
  printf '%s\n' "$k2" | $DD2I "sudo wg set wgd private-key /dev/stdin listen-port $WGPD peer $p1 allowed-ips 10.99.201.112/32 endpoint 10.99.99.112:$WGPD" || return 1
  $DD2 "sudo ip addr add 10.99.201.113/24 dev wgd && sudo ip link set wgd mtu $WGMTU up"
}

# WireGuard queues a peer's serial tx worker on CPU (peer->internal_id % online_cpus); internal_id is a global
# counter, so the CPU rotates with every new wg0. For a controlled experiment recreate wg0 until the pegged CPU
# observed under a short probe load equals WG_SERIAL_CPU (at most 12 attempts).
probe_cpu(){
  $D1 "$NSX iperf3 -c $TI4 -B $P4 -p $IPERF_P -P 4 -t 7 -Z >/dev/null 2>&1" &
  local p=$!; sleep 2
  dut 'grep "^cpu[0-9]" /proc/stat; sleep 3; echo --; grep "^cpu[0-9]" /proc/stat' | awk '
    /^--$/{sec=1; next}
    {t=0; for(i=2;i<=11;i++) t+=$i; idle=$5+$6; if(!sec){T0[$1]=t;I0[$1]=idle} else {T1[$1]=t;I1[$1]=idle}}
    END{mx=-1; for(k in T1){d=T1[k]-T0[k]; b=d>0?100*(d-(I1[k]-I0[k]))/d:0; if(b>mx){mx=b;mc=k}} print mc}'
  wait $p
}
place_serial_cpu(){
  local want=cpu$1 got n
  for n in $(seq 1 12); do
    got=$(probe_cpu)
    if [ "$got" = "$want" ]; then echo "serial tx worker placed on $want (attempt $n)"; return 0; fi
    echo "  attempt $n: serial stage on $got, want $want; recreating wg0" >&2
    sleep 8; wg_tunnel_recreate || return 1
    $D1 "$NSX ping -c3 -W2 -I $P4 $TI4 >/dev/null 2>&1" || return 1
  done
  echo "could not place the serial stage on $want" >&2; return 1
}

server_select(){
  local role=$1 other
  [ "$WG_LEASE_SKIP_SERVERS" != 1 ] || return 0
  case $role in tunnel) other=direct;; direct) other=tunnel;; *) return 1;; esac
  wg_resource_remove "server-$other" wg_server_stop "$other" "$WG_LEASE_IPERF_BASE" || return 1
  if [ -e "$WG_LEASE_DIR/server-$role" ] || [ -L "$WG_LEASE_DIR/server-$role" ]; then
    if [ -L "$WG_LEASE_DIR/server-$role" ] || [ ! -f "$WG_LEASE_DIR/server-$role" ] ||
       [ "$(cat "$WG_LEASE_DIR/server-$role")" != created ]; then
      echo "ERROR: server $role has uncertain ownership" >&2
      return 1
    fi
    return 0
  fi
  wg_resource_add "server-$role" wg_server_start "$role" "$WG_LEASE_IPERF_BASE"
}

server_restart(){
  local role=$1
  wg_resource_remove "server-$role" wg_server_stop "$role" "$WG_LEASE_IPERF_BASE" || return 1
  server_select "$role"
}

wg_dut_down(){
  local route result=0
  wg_prepare_binding_cleanup || return 1
  for route in far4 plain4 far6 plain6; do
    wg_resource_remove "route-$route" wg_route "$route" delete || result=1
  done
  [ "$result" = 0 ] || return 1
  wg_resource_remove link-dut wg_link dut delete || result=1
  wg_resource_remove link-far wg_link far delete || result=1
  return "$result"
}

wg_tunnel_recreate(){
  wg_resource_remove tunnel wg_dut_down || return 1
  wg_tunnel_start || return 1
  wg_bind
}

teardown(){
  local result=0
  wg_resource_remove server-tunnel wg_server_stop tunnel "$WG_LEASE_IPERF_BASE" || result=1
  wg_resource_remove server-direct wg_server_stop direct "$WG_LEASE_IPERF_BASE" || result=1
  [ "$result" = 0 ] || return 1
  wg_prepare_binding_cleanup || return 1
  wg_resource_remove tunnel wg_dut_down || return 1
  wg_resource_remove notrack wg_notrack_remove || result=1
  wg_resource_remove fw4 wg_fw_remove || result=1
  [ "$result" = 0 ] || return 1
  wg_resource_remove link-direct1 wg_link direct1 delete || result=1
  wg_resource_remove link-direct2 wg_link direct2 delete || result=1
  [ "$result" = 0 ] || return 1
  wg_release_binding
}

wg_probe_cleanup(){
  wg_prepare_binding_cleanup || return 1
  wg_resource_remove link-probe wg_probe_link delete || return 1
  wg_release_binding || return 1
  wg_lease_end
}

preflight(){
  local out WG_LEASE_DIR="${WG_LEASE_DIR}.probe"
  if ! out=$(dut "sudo modprobe wireguard 2>/dev/null || true
    command -v wg >/dev/null 2>&1 || { echo NOWG; exit 1; }" 2>&1); then
    echo "DUT $DUT ($DUT_KIND): no WireGuard: $out" >&2
    [ "$DUT_KIND" = vendor ] && echo "  Install WireGuard tools and a kmod matching the running kernel; do not force an ABI mismatch." >&2
    return 1
  fi
  wgfp_require_unbound || return 1
  local WGFP=0
  wg_lease_begin || return 1
  if ! wg_reserve_binding; then
    if [ -e "$WG_LEASE_DIR/binding" ] || [ -L "$WG_LEASE_DIR/binding" ]; then
      wg_probe_cleanup || echo "ERROR: probe lease retained at $WG_LEASE_DIR" >&2
    else
      wg_lease_end || echo "ERROR: probe lease retained at $WG_LEASE_DIR" >&2
    fi
    return 1
  fi
  if ! wg_resource_add link-probe wg_probe_link create; then
    echo "DUT $DUT ($DUT_KIND): WireGuard link probe failed" >&2
    wg_probe_cleanup || echo "ERROR: probe lease retained at $WG_LEASE_DIR" >&2
    return 1
  fi
  if ! wg_probe_cleanup; then
    echo "ERROR: probe cleanup failed; retry with: $0 probe-teardown" >&2
    return 1
  fi
  $D1 'which wg iperf3 timeout >/dev/null' && $D2 'which wg iperf3 >/dev/null' || { echo "dells need wireguard-tools + iperf3, and the client needs timeout" >&2; return 1; }
}

# ---------- measurement ----------
parse(){ python3 - "$1" "$STEADY_FROM" "$MODE" "$2" <<'PY'
import json,math,sys
try: j=json.load(open(sys.argv[1]))
except (OSError,ValueError): print("0 0 0"); sys.exit()
if j.get('error'): print("0 0 0"); sys.exit()
f=float(sys.argv[2]); udp=sys.argv[3]=="udp"
def sel(key):
    return [i[key] for i in j.get('intervals',[]) if key in i and i[key]['start']>=f-0.01 and not i[key].get('omitted')]
a=sel('sum'); b=sel('sum_bidir_reverse')
if sys.argv[4]=="bidir" and len(a)!=len(b):
    print(f"ERROR: missing reverse iperf intervals in {sys.argv[1]}",file=sys.stderr)
    sys.exit(1)
def mean(iv): return sum(x['bits_per_second'] for x in iv)/len(iv)/1e9 if iv else 0
g=mean(a)+mean(b)
if not math.isfinite(g) or g < 0: print("0 0 0"); sys.exit()
e=j.get('end',{}); c=e.get('cpu_utilization_percent',{})
if udp:
    s=e.get('sum',{}); x=s.get('lost_percent',0)
else:
    if sys.argv[4]=="bidir":
        for key in ('sum_sent','sum_sent_bidir_reverse'):
            value=e.get(key,{}).get('retransmits')
            if not isinstance(value,int) or value<0:
                print(f"ERROR: missing or invalid {key} retransmits in {sys.argv[1]}",file=sys.stderr)
                sys.exit(1)
    x=e.get('sum_sent',{}).get('retransmits') or sum(i['sum'].get('retransmits',0) for i in j.get('intervals',[]))
    x+=e.get('sum_sent_bidir_reverse',{}).get('retransmits',0) or 0
print(f"{g:.9f} {c.get('host_total',0):.0f} {x}")
PY
}

cpu_stats(){ awk '
 /^--$/{sec++; next}
 /^cpu/{ n=$1; t=0; for(i=2;i<=11;i++) t+=$i; idle=$5+$6; s=$8; if(sec==0){T0[n]=t;I0[n]=idle;S0[n]=s} else {T1[n]=t;I1[n]=idle;S1[n]=s} }
 /^N /{ if(sec==0) r0[$2]=$3; else r1[$2]=$3 }
 END{ d=T1["cpu"]-T0["cpu"]; busy=d>0?100*(d-(I1["cpu"]-I0["cpu"]))/d:0; sirq=d>0?100*(S1["cpu"]-S0["cpu"])/d:0; mx=0;
      for(k in T1){ if(k=="cpu")continue; dd=T1[k]-T0[k]; if(dd>0){ b=100*(dd-(I1[k]-I0[k]))/dd; if(b>mx){mx=b; mc=k} } }
      printf "%.1f %.1f %.1f %d %d %s\n", busy, sirq, mx, r1["eth3"]-r0["eth3"], r1["eth4"]-r0["eth4"], mc }' "$1"; }

# PROF=1: per-CPU time, per-CPU softirq counts and per-thread CPU ticks (with the CPU each thread last ran on) at the
# start and end of the steady window, written to $J/<cell>.prof; summarise with bin/wg-prof-summary.py.
dut_prof(){ ssh $SSHO $DUT sh -s -- "$1" "$2" <<'EOS'
sf=$1; w=$2
snap(){ echo "== $(date +%s)"; grep '^cpu' /proc/stat; echo "-- softirqs"; cat /proc/softirqs; echo "-- interrupts"; cat /proc/interrupts
        echo "-- threads"; for f in /proc/[0-9]*/task/[0-9]*/stat; do cat $f 2>/dev/null; echo; done; echo "-- end"; }
sleep $sf; snap; sleep $w; snap
EOS
}

hits_sum(){ awk '/^--$/{sec++; next} /^E /{ if(sec==0) a[$2]=$3; else b[$2]=$3 } END{ for(k in b){ d=((k in a)&&b[k]>=a[k])?b[k]-a[k]:b[k]; s+=d } print s+0 }' "$1"; }

run_cell(){
  local name=$1 a=$2 b=$3 fam=$4 kind=$5 dir flag tag win
  name=$name$PORTSUF   # the encrypted-leg port is part of the cell identity (CSV, JSON file names)
  local NSC="$NSX"; [ "$kind" = direct ] && NSC="sudo"   # direct pair uses root namespaces
  if ! $D1 "$NSC ping -c3 -W2 -i 0.3 -I $a $b >/dev/null 2>&1"; then
    printf '%-12s %s\n' "$name" "SKIP (no path $a -> $b)"; echo "$(date +%H:%M:%S),$name,-,0,0,0,0,0,0,0,0,SKIP,-" >> "$OUT"
    if [ "${WG_SKIP_SERVERS:-0}" = 1 ]; then return 1; else return 0; fi
  fi
  for dir in $DIRS; do
   for rep in $(seq 1 $REPS); do
    if [ "${RECREATE:-0}" = 1 ] && [ "$kind" = dut ]; then   # new wg0 = new peer id = new serial tx worker CPU (id % ncpu)
      wg_tunnel_recreate || { echo "DUT tunnel recreation failed" >&2; return 1; }
      $D1 "$NSX ping -c3 -W2 -I $P4 $TI4 >/dev/null 2>&1" || return 1
    fi
    case $dir in up) flag="";; down) flag="-R";; bidir) flag="--bidir";; esac
    win=$((DUR-STEADY_FROM-2)); tag="$name-$dir-r$rep"
    local udp=""; [ "$MODE" = udp ] && udp="-u -b $RATE -l $LEN"
    # The direct cell uses a root-namespace server; DUT cells use the rig namespace.
    if [ "$kind" = direct ]; then server_select direct || return 1; else server_select tunnel || return 1; fi
    for attempt in 1 2 3; do
    wgfp_counter_sample "$name" "$dir" "$rep" before "$attempt" || return 1
    dut_stat=${WG_DUT_STAT:-/tmp/wg-dut.stat}
    if [ "$DUT_KIND" = vendor ]; then
      ssh $SSHO $DUT sh -s -- $STEADY_FROM $win > "$dut_stat" <<'EOS' &
sf=$1; w=$2
snap(){ grep '^cpu' /proc/stat; for i in eth3 eth4; do echo "N $i $(cat /sys/class/net/$i/statistics/rx_packets)"; done; echo --; }
sleep $sf; snap; sleep $w; snap
EOS
    else
      ssh $SSHO $DUT bash -s -- $STEADY_FROM $win > "$dut_stat" <<'EOS' &
sf=$1; w=$2
snap(){ grep '^cpu' /proc/stat; for i in eth3 eth4; do echo "N $i $(cat /sys/class/net/$i/statistics/rx_packets)"; done
        sudo cat /sys/kernel/debug/fman_pcd/0/fe_ehash_stats 2>/dev/null | sed -n 's/.*record_dma=\(0x[0-9a-f]*\).*pkt_count=\([0-9]*\).*/E \1 \2/p'; echo --; }
sleep $sf; snap; sleep $w; snap
EOS
    fi
    sp=$!
    pp=""; if [ "${PROF:-0}" = 1 ]; then dut_prof $STEADY_FROM $win > "$J/$tag.prof" & pp=$!; fi
    $D1 "$NSC timeout --signal=TERM --kill-after=5s ${WG_CLIENT_TIMEOUT}s iperf3 -c $b -B $a -p $IPERF_P -P $STREAMS -t $DUR -i 1 -Z -J $flag $udp" > "$J/$tag.json" &
    client_status=0; wait $! || client_status=$?
    stat_status=0; wait $sp || stat_status=$?
    wgfp_counter_sample "$name" "$dir" "$rep" after "$attempt" || return 1
    [ -n "$pp" ] && wait $pp; sleep 1
    if [ "$stat_status" -ne 0 ]; then
      echo "ERROR: DUT statistics sampling failed" >&2; return 1
    fi
    parsed=$(parse "$J/$tag.json" "$dir") || { echo "ERROR: unable to parse iperf results" >&2; return 1; }
    read gbps gen xr <<< "$parsed"
    read busy sirq mx rx3 rx4 mc < <(cpu_stats "$dut_stat")
    hw=$(hits_sum "$dut_stat")
    if [ "$client_status" -eq 0 ] && awk -v g="$gbps" 'BEGIN{exit !(g>0)}'; then break; fi
    if [ "$attempt" -eq 3 ]; then
      echo "ERROR: iperf produced no traffic or failed after $attempt attempts" >&2; return 1
    fi
    if [ "${WG_SKIP_SERVERS:-0}" = 1 ]; then
      echo "  (rep $rep produced no traffic, attempt $attempt; retrying with the external iperf server)" >&2
    else
      echo "  (rep $rep produced no traffic, attempt $attempt; restarting the iperf3 server and retrying)" >&2
      if [ "$kind" = direct ]; then server_restart direct || return 1; else server_restart tunnel || return 1; fi
    fi
    sleep 5
    done
    # Netdev RX counters can omit WGFP sink traffic; this is not wire PPS.
    pps=$(( (rx3 + rx4) / win ))
    printf '%-12s %-5s r%s %7s Gbit/s  dutbusy %5s%% sirq %5s%% maxcore %5s%%  gen %3s%%  %s %s  netdev_rx_pps %s  hw_hits %s  cpu %s\n' \
      "$name" "$dir" "$rep" "$gbps" "$busy" "$sirq" "$mx" "$gen" "$([ "$MODE" = udp ] && echo loss% || echo retr)" "$xr" "$pps" "$hw" "$mc"
    echo "$(date +%H:%M:%S),$name,$dir,$rep,$gbps,$xr,$busy,$sirq,$mx,$gen,$pps,$hw,$mc" >> "$OUT"
    sleep 5
   done
  done
}

wg_single_cleanup(){
  local rc=$?
  trap - EXIT INT TERM
  if teardown; then wg_lease_end || rc=1; else rc=1; fi
  exit "$rc"
}

cmd=${1:-run}; [ $# -gt 0 ] && shift
case $cmd in
  teardown) wg_lease_load && teardown && wg_lease_end || exit 4; echo "torn down"; exit 0;;
  probe-teardown)
    WG_LEASE_DIR="${WG_LEASE_DIR}.probe"
    wg_lease_load && wg_probe_cleanup || exit 4
    echo "probe torn down"; exit 0;;
  check)    preflight && echo "preflight OK ($DUT_KIND)"; exit $?;;
  setup|run) : ;;
  *) echo "usage: $0 [setup|teardown|probe-teardown|check|run] [cell...]" >&2; exit 2;;
esac
[ "${WG_SKIP_SERVERS:-0}" != 1 ] || [ "$cmd" != run ] || [ "${WG_EXTERNAL_IPERF:-0}" = 1 ] ||
  { echo "WG_SKIP_SERVERS=1 with run requires WG_EXTERNAL_IPERF=1" >&2; exit 2; }
[ "${WG_SKIP_SERVERS:-0}" != 1 ] || [ -z "${WG_SERIAL_CPU:-}" ] ||
  { echo "WG_SERIAL_CPU requires the iperf server" >&2; exit 2; }
[ -z "${WGFP_COUNTER_LOG:-}" ] || [ "${WGFP:-0}" = 1 ] ||
  { echo "WGFP_COUNTER_LOG requires WGFP=1" >&2; exit 2; }
case ${WG_NOTRACK:-0} in 0|1|2) ;; *) echo "WG_NOTRACK must be 0, 1 or 2" >&2; exit 2;; esac
if [ -n "${WG_SERIAL_CPU:-}" ] && ! [[ $WG_SERIAL_CPU =~ ^[0-9]+$ ]]; then
  echo "WG_SERIAL_CPU must be a CPU number" >&2; exit 2
fi
WANT="$*"; [ -z "$WANT" ] && WANT="wg-v4 wg-v6"
if [ "${WG_SKIP_SERVERS:-0}" = 1 ] && [[ " $WANT " == *" ceiling-v4 "* ]]; then
  echo "WG_SKIP_SERVERS=1 does not support the ceiling-v4 cell" >&2; exit 2
fi
needdut=0; needdirect=0
for c in $WANT; do case $c in wg-*) needdut=1;; ceiling-*) needdirect=1;; esac; done
if [ $needdirect = 1 ] && [ "$WG_PORT" != eth4 ]; then echo "the ceiling cell is bound to the dell1/dell2 pair; run it without WG_PORT" >&2; exit 2; fi
preflight || exit 3
envrec(){
  dut 'echo "kernel: $(uname -r) $(uname -v | cut -c1-60)"; echo "lockdep: $([ -e /proc/lockdep ] && echo PRESENT || echo absent)"; \
       zcat /proc/config.gz 2>/dev/null | grep -E "^CONFIG_(PROVE_LOCKING|DEBUG_SPINLOCK|KASAN)=" ; \
       echo "freq_khz: $(cat /sys/devices/system/cpu/cpu0/cpufreq/scaling_cur_freq 2>/dev/null) gov: $(cat /sys/devices/system/cpu/cpu0/cpufreq/scaling_governor 2>/dev/null)"; \
       echo "rps_eth3: $(cat /sys/class/net/eth3/queues/rx-0/rps_cpus 2>/dev/null) uptime: $(cut -d. -f1 /proc/uptime)s"' 2>/dev/null
}
ENVTXT=$(envrec); echo "$ENVTXT"
if echo "$ENVTXT" | grep -q -E "lockdep: PRESENT|PROVE_LOCKING=y|DEBUG_SPINLOCK=y"; then
  echo "REFUSING: DUT kernel has lock debugging enabled; numbers would be invalid (plans/archive/ask2-wireguard-plan.md 2.1)." >&2; exit 5
fi
wgfp_require_unbound || exit 4
wg_lease_begin || exit 4
trap wg_single_cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
wg_reserve_binding || exit 4
if [ $needdut = 1 ]; then
  wg_tunnel_start && wg_network_up && wg_bind ||
    { echo "DUT tunnel setup failed" >&2; exit 4; }
fi
if [ $needdirect = 1 ]; then wg_direct_up || { echo "Direct tunnel setup failed" >&2; exit 4; }; fi
server_select tunnel || { echo "Server setup failed" >&2; exit 4; }
[ -n "${WG_SERIAL_CPU:-}" ] && [ $needdut = 1 ] && { place_serial_cpu "$WG_SERIAL_CPU" || exit 6; }
[ "$cmd" = setup ] && { echo "tunnel up; remove it with: $0 teardown"; trap - EXIT INT TERM; exit 0; }
if [ $needdut = 1 ]; then   # prime the handshake and prove the path before measuring
  $D1 "$NSX ping -c3 -W2 -I $P4 $TI4 >/dev/null 2>&1"
  dut 'sudo wg show wg0 latest-handshakes' | awk '{print "handshake epoch: "$2}'
fi
echo "ts,cell,dir,rep,gbps,retr_or_loss,dut_busy,dut_sirq,dut_maxcore,gen_cpu,dut_rx_pps,hw_hits,maxcpu" > "$OUT"
echo "DUT=$DUT kind=$DUT_KIND mode=$MODE streams=$STREAMS dur=$DUR mtu=$WGMTU outer=v$OUTER"
for line in $CELLS_ALL; do
  IFS='|' read -r name a b fam kind <<<"$line"
  [[ " $WANT " == *" $name "* ]] || continue
  run_cell "$name" "$a" "$b" "$fam" "$kind" || exit 4
done
python3 - "$OUT" <<'PY'
import csv,sys,statistics as st,collections
rows=[r for r in csv.DictReader(open(sys.argv[1])) if r['dir']!='-' and float(r['gbps'])>0]   # zero-traffic reps are failures, never averaged
g=collections.OrderedDict()
for r in rows: g.setdefault((r['cell'],r['dir']),[]).append(r)
print("\nSUMMARY (steady-window Gbit/s over reps)")
print("%-12s %-6s %3s %8s %8s %8s %7s %9s %9s"%("cell","dir","n","mean","min","max","sd","dut_busy","maxcore"))
for (c,d),v in g.items():
    x=[float(r['gbps']) for r in v]; b=[float(r['dut_busy']) for r in v]; m=[float(r['dut_maxcore']) for r in v]
    print("%-12s %-6s %3d %8.3f %8.3f %8.3f %7.3f %8.1f%% %8.1f%%"%(c,d,len(x),st.mean(x),min(x),max(x),st.pstdev(x) if len(x)>1 else 0,st.mean(b),st.mean(m)))
PY
echo "csv: $OUT"
[ "${KEEP:-0}" != 1 ] || trap - EXIT INT TERM
