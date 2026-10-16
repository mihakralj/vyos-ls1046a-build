#!/usr/bin/env bash
# PPPoE session on the OpenWrt vendor DUT for the pppoe-* cells of testrig-offload-quick.sh.
# Same shape as the ASK2 rig (the ASK2 session is gen1 root ns <-> ASK2 DUT eth3 as pppoe10):
#   up      pppoe-server inside gen1's VENDOR_NS netns (port facing the vendor DUT) and a runtime pppd
#           client on the vendor DUT eth3 named pppoe10; the /etc/ppp/ip-up.d + ipv6-up.d hooks on gen1 run
#           in pppd's netns, so they build the table-150 policy routes for that session by themselves.
#   down    stop the client, then the n106 server.
#   status  session state on both ends.
# Nothing persistent changes: no uci, no systemd unit; a reboot of the DUT or gen1 drops the session.
# Usage: bin/testrig-pppoe-vendor.sh up|down|status
# Settings (bin/testrig-env.sh): GEN1 VENDOR_DUT RIG_KEY VENDOR_NS GEN_IF_NS PPPOE_USER PPPOE_PLUGIN
set -u
. "$(dirname "$0")/testrig-env.sh"
rig_require GEN1 VENDOR_DUT
SSHO="-o BatchMode=yes -o ConnectTimeout=8 $RIG_SSHK"
V="ssh -n $SSHO $VENDOR_DUT"
D1="ssh -n $SSHO $GEN1"
NS=$VENDOR_NS; D1_IF=$GEN_IF_NS; SRV_PID=/var/run/pppoe-server-$NS.pid
IFN=pppoe10

status(){
  echo "== dell1 $NS"; $D1 "sudo ip netns exec $NS ip -br addr | grep -E 'ppp'; sudo ip netns exec $NS ip rule | grep 150; test -f $SRV_PID && sudo kill -0 \$(cat $SRV_PID) 2>/dev/null && echo server-running"
  echo "== vendor DUT"; $V "ip -br addr show $IFN 2>&1; ip -6 route | grep fd99:50; pidof pppd"
}

up(){
  if ! $D1 "test -f $SRV_PID && sudo kill -0 \$(cat $SRV_PID) 2>/dev/null"; then
    $D1 "sudo ip netns exec $NS /usr/sbin/pppoe-server -k -g $PPPOE_PLUGIN -I $D1_IF -L 10.99.50.1 -R 10.99.50.10 -N 10 -C ASK2-TESTRIG-VENDOR -X $SRV_PID -O /etc/ppp/pppoe-server-options" || { echo "pppoe-server start failed"; return 1; }
  fi
  # same credentials as the ASK2 session; read from the server's secrets file, not echoed
  local pw; pw=$($D1 "sudo awk -v u='$PPPOE_USER' '{n=\$1; gsub(/\"/, \"\", n)} n == u {print \$3}' /etc/ppp/chap-secrets" | tr -d '"')
  [ -n "$pw" ] || { echo "no $PPPOE_USER secret in gen1's /etc/ppp/chap-secrets"; return 1; }
  if ! $V "ip link show $IFN >/dev/null 2>&1"; then
    $V "pppd plugin pppoe.so nic-eth3 ifname $IFN user $PPPOE_USER password '$pw' noauth nodefaultroute noipdefault mtu 1492 mru 1492 noccp novj novjccomp nobsdcomp nodeflate +ipv6 lcp-echo-interval 10 lcp-echo-failure 3 logfile /tmp/pppoe10.log"
  fi
  for i in $(seq 1 20); do $V "ip -4 addr show $IFN 2>/dev/null | grep -q 'peer 10.99.50.1'" && break; sleep 1; done
  $V "ip -4 addr show $IFN | grep -q 'peer 10.99.50.1'" || { echo "session did not come up; see the DUT /tmp/pppoe10.log"; $V "tail -15 /tmp/pppoe10.log"; return 1; }
  $V "ip -6 route replace fd99:50::/64 dev $IFN"
  # fw4's forward chain only knows the lab zone ports; without this rule pppoe10 traffic hits handle_reject
  $V "nft -a list chain inet fw4 forward | grep -q ask2q-pppoe || { nft insert rule inet fw4 forward iifname $IFN accept comment ask2q-pppoe; nft insert rule inet fw4 forward oifname $IFN accept comment ask2q-pppoe; }"
  status
  $V "ping -c2 -W2 10.99.50.1 >/dev/null 2>&1" && echo "ping v4 ok" || echo "ping v4 FAILED"
  $D1 "sudo ip netns exec $NS ping -c2 -W2 -I 10.99.50.1 10.99.2.113 >/dev/null 2>&1" && echo "gen1->gen2 over the session v4 ok" || echo "gen1->gen2 v4 FAILED"
  $D1 "sudo ip netns exec $NS ping -6 -c2 -W2 -I fd99:50::1 fd99:2::113 >/dev/null 2>&1" && echo "gen1->gen2 over the session v6 ok" || echo "gen1->gen2 v6 FAILED"
}

down(){
  $V "for h in \$(nft -a list chain inet fw4 forward | sed -n 's/.*ask2q-pppoe.*# handle \\([0-9]*\\)/\\1/p'); do nft delete rule inet fw4 forward handle \$h; done; true"
  $V "p=\$(pidof pppd); [ -n \"\$p\" ] && kill \$p; sleep 2; ip link show $IFN 2>&1 | head -1"
  $D1 "test -f $SRV_PID && sudo kill \$(cat $SRV_PID); sleep 1; sudo ip netns exec $NS ip -br link | grep -c ppp"
}

case "${1:-}" in up) up ;; down) down ;; status) status ;; *) echo "usage: $0 up|down|status"; exit 2 ;; esac
