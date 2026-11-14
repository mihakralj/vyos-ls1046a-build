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
# Everything is runtime: the DUT side is a VyOS commit that is never saved and is deleted again on exit,
# the generator side (dell1 VLAN 7 + a second pppoe-server with its own up-scripts, dell2 routes, wgw0 on
# both ends) is removed on exit. The existing pppoe10 session and dell1's shared /etc/ppp hooks are untouched.
# Usage: OUTDIR=<matrix run dir> bin/testrig-ask2-wan.sh   Env: REPS=3 DUR=10 STEADY_FROM=3 BIDIR_DUR=20
#        BIDIR_STEADY_FROM=12 STREAMS=8 KEEP=1 (leave the environment up)
# Output: quick-wan-uni-r<N>.csv / quick-wan-bidir-r<N>.csv in OUTDIR (bin/ask2-matrix-table.py reads them).
# Never run it next to other rig traffic.
set -u
HERE=$(cd "$(dirname "$0")" && pwd)
. "$HERE/testrig-env.sh"
rig_require ASK2_DUT GEN1 GEN2
REPS=${REPS:-3}; export DUR=${DUR:-10} STEADY_FROM=${STEADY_FROM:-3}
BIDIR_DUR=${BIDIR_DUR:-20}; BIDIR_STEADY_FROM=${BIDIR_STEADY_FROM:-12}
RUN=${OUTDIR:?set OUTDIR to the matrix run directory}; mkdir -p "$RUN"
SSHO=(-o BatchMode=yes -o ConnectTimeout=8 $RIG_SSHK)
d(){ ssh -n "${SSHO[@]}" "$ASK2_DUT" "$@"; }
di(){ ssh "${SSHO[@]}" "$ASK2_DUT" "$@"; }
g1(){ ssh -n "${SSHO[@]}" "$GEN1" "$@"; }
g1i(){ ssh "${SSHO[@]}" "$GEN1" "$@"; }
g2(){ ssh -n "${SSHO[@]}" "$GEN2" "$@"; }
VID=7; WPORT=51870; IF7="$GEN_IF.$VID"

vyos(){ # vyos <set|delete lines...>: one configure session, commit, never save
  { echo 'source /opt/vyatta/etc/functions/script-template'; echo configure; printf '%s\n' "$@"; echo commit; echo 'exit discard'; } |
    di 'vbash -s' 2>&1 | grep -vE '^(\[edit\]|$)|discardChanges|Warning: configuration changes have not been saved' || true
}

up(){
  echo "##### wan setup $(date +%T)"
  # dell1: VLAN 7 and a second PPPoE concentrator whose session runs its own up-scripts
  g1i 'sudo sh -s' <<EOF || return 1
set -e
ip link show $IF7 >/dev/null 2>&1 || ip link add link $GEN_IF name $IF7 type vlan id $VID
ip link set $IF7 up
cat > /etc/ppp/ask2-wan7-ipv6-up <<'S'
#!/bin/sh
ip -6 addr replace fd99:57::1/64 dev "\$1" nodad
ip -6 route replace fd99:2::/64 dev "\$1" table 157
ip -6 rule show | grep -q 'from fd99:57::1 to fd99:2::/64 lookup 157' || ip -6 rule add pref 10957 from fd99:57::1 to fd99:2::/64 lookup 157
S
printf '#!/bin/sh\nexit 0\n' > /etc/ppp/ask2-wan7-ip-up
chmod 755 /etc/ppp/ask2-wan7-ipv6-up /etc/ppp/ask2-wan7-ip-up
{ cat /etc/ppp/pppoe-server-options; echo 'ip-up-script /etc/ppp/ask2-wan7-ip-up'; echo 'ipv6-up-script /etc/ppp/ask2-wan7-ipv6-up'; } > /etc/ppp/ask2-wan7-options
[ -s /var/run/pppoe-server-wan7.pid ] && kill -0 \$(cat /var/run/pppoe-server-wan7.pid) 2>/dev/null ||
  /usr/sbin/pppoe-server -k -g ${PPPOE_PLUGIN} -I $IF7 -L 10.99.57.1 -R 10.99.57.10 -N 2 -C ASK2-WAN7 -X /var/run/pppoe-server-wan7.pid -O /etc/ppp/ask2-wan7-options
EOF
  # DUT: VLAN 7 and pppoe7 on it, configured like pppoe10 (same account)
  local PW; PW=$(d "sed -n '/pppoe pppoe10 {/,/^    }/s/.*password \"\\(.*\\)\"/\\1/p' /config/config.boot")
  [ -n "$PW" ] || { echo "ABORT: no pppoe10 password in the DUT config"; return 1; }
  vyos "set interfaces ethernet eth3 vif $VID description ask2-wan7" \
       "set interfaces pppoe pppoe7 source-interface eth3.$VID" \
       "set interfaces pppoe pppoe7 authentication username '$PPPOE_USER'" \
       "set interfaces pppoe pppoe7 authentication password '$PW'" \
       "set interfaces pppoe pppoe7 mtu 1492" "set interfaces pppoe pppoe7 no-default-route" \
       "set interfaces pppoe pppoe7 no-peer-dns" "set interfaces pppoe pppoe7 ipv6 address autoconf" \
       "set protocols static route6 fd99:57::/64 interface pppoe7"
  local i; for i in $(seq 1 30); do d 'ip -4 -o addr show dev pppoe7 2>/dev/null' | grep -q 'peer 10.99.57.1' && break; sleep 2; done
  d 'ip -4 -o addr show dev pppoe7' | grep -q 'peer 10.99.57.1' || { echo "ABORT: pppoe7 did not come up"; return 1; }
  # dell2 (LAN): reach the WAN-side prefixes through the DUT
  g2 'sudo ip route replace 10.99.57.0/24 via 10.99.2.185; sudo ip route replace 10.99.230.0/24 via 10.99.2.185;
      sudo ip -6 route replace fd99:57::/64 via fd99:2::185; sudo ip -6 route replace fd99:230::/64 via fd99:2::185' || return 1
  # WireGuard over pppoe7: the DUT dials dell1's concentrator address, dell1 learns the endpoint
  local kd kg pd pg
  kd=$(d 'wg genkey'); pd=$(printf '%s\n' "$kd" | di 'wg pubkey')
  kg=$(g1 'wg genkey'); pg=$(printf '%s\n' "$kg" | g1i 'wg pubkey')
  printf '%s\n' "$kg" | g1i "sudo sh -c 'ip link add wgw0 type wireguard 2>/dev/null; wg set wgw0 private-key /dev/stdin listen-port $WPORT peer $pd allowed-ips 10.99.230.1/32,fd99:2::/64,fd99:230::1/128 &&
    ip addr replace 10.99.230.112/24 dev wgw0 && ip -6 addr replace fd99:230::112/64 dev wgw0 nodad && ip link set wgw0 mtu 1420 up &&
    ip -6 route replace fd99:2::/64 dev wgw0'" || return 1
  printf '%s\n' "$kd" | di "sudo sh -c 'ip link add wgw0 type wireguard 2>/dev/null; wg set wgw0 private-key /dev/stdin listen-port $WPORT peer $pg endpoint 10.99.57.1:$WPORT allowed-ips 10.99.230.0/24,fd99:230::/64 persistent-keepalive 10 &&
    ip addr replace 10.99.230.1/24 dev wgw0 && ip -6 addr replace fd99:230::1/64 dev wgw0 nodad && ip link set wgw0 mtu 1420 up'" || return 1
  for i in $(seq 1 10); do d 'ping -c1 -W1 10.99.230.112 >/dev/null 2>&1' && break; sleep 1; done
  echo "pppoe7: $(d 'ip -4 -o addr show dev pppoe7' | grep -o 'inet [^ ]* peer [^ ]*')  wgw0 handshake: $(d 'sudo wg show wgw0 latest-handshakes' | awk '{print ($2>0)?"yes":"no"}')"
}

down(){
  echo "##### wan teardown $(date +%T)"
  d "sudo ip link del wgw0 2>/dev/null; true"
  g1 "sudo ip link del wgw0 2>/dev/null; true"
  g2 'sudo ip route del 10.99.57.0/24 2>/dev/null; sudo ip route del 10.99.230.0/24 2>/dev/null;
      sudo ip -6 route del fd99:57::/64 2>/dev/null; sudo ip -6 route del fd99:230::/64 2>/dev/null; true'
  vyos "delete protocols static route6 fd99:57::/64" "delete interfaces pppoe pppoe7" "delete interfaces ethernet eth3 vif $VID"
  g1 "sudo sh -c '[ -s /var/run/pppoe-server-wan7.pid ] && kill \$(cat /var/run/pppoe-server-wan7.pid) 2>/dev/null;
      pkill -f \"[p]ppd .*ask2-wan7-options\"; sleep 1; ip link del $IF7 2>/dev/null;
      while ip -6 rule show | grep -q \"lookup 157\"; do ip -6 rule del pref 10957; done; ip -6 route flush table 157 2>/dev/null;
      rm -f /etc/ppp/ask2-wan7-options /etc/ppp/ask2-wan7-ip-up /etc/ppp/ask2-wan7-ipv6-up /var/run/pppoe-server-wan7.pid; true'"
  echo "DUT left: $(d 'ip -br link | grep -cE "pppoe7|wgw0|eth3\.7"') interfaces (want 0); pppoe10 $(d 'ip -4 -o addr show dev pppoe10' | grep -o 'peer [^ ]*')"
}
[ "${KEEP:-0}" = 1 ] || trap down EXIT
up || { echo "ABORT: wan setup failed"; exit 1; }

UNI="wanfull-up-v4 wanfull-up-v6 wanfull-down-v4 wanfull-down-v6 portfwd-up-v4 portfwd-up-v6 portfwd-down-v4 portfwd-down-v6
wgwan-up-v4 wgwan-up-v6 wgwan-down-v4 wgwan-down-v6"
BI="wanfull-up-v4 wanfull-up-v6 portfwd-down-v4 portfwd-down-v6 wgwan-up-v4 wgwan-up-v6"
for r in $(seq 1 "$REPS"); do
  echo "##### wan unidir rep $r $(date +%T)"
  DUT_KIND=ask2 OUT=$RUN/quick-wan-uni-r$r.csv J=$RUN/json-wan-r$r "$HERE/testrig-offload-quick.sh" $UNI
  echo "##### wan bidir rep $r $(date +%T)"
  DUT_KIND=ask2 BIDIR=1 DUR=$BIDIR_DUR STEADY_FROM=$BIDIR_STEADY_FROM OUT=$RUN/quick-wan-bidir-r$r.csv J=$RUN/json-wan-r$r \
    "$HERE/testrig-offload-quick.sh" $BI
done
echo "##### wan done $(date +%T)"
