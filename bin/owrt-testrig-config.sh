#!/bin/sh
# Put an OpenWrt NXP-ASK DUT (Mono Gateway LS1046A) into the bin/testrig-* data-path layout.
# Run on the DUT as root (POSIX sh), or from a workstation:
#   ssh root@192.168.1.70 'sh -s' < bin/owrt-testrig-config.sh
# Prerequisite: bin/owrt-strip-network.sh already ran (eth0 = DHCP mgmt, nothing else).
#
# Result: eth3 10.99.1.185/24 fd99:1::185/64, eth3.10 10.99.10.185/24 fd99:10::185/64,
#         eth4 10.99.2.185/24 fd99:2::185/64, eth4.20 10.99.20.185/24 fd99:20::185/64
#         (the same data-plane addresses as the VyOS DUT, so a cable move swaps DUTs),
#         forwarding on, fw4 stopped and disabled. ASK offload needs no extra step: the ask-flowtable
#         daemon owns its own nft table. Do NOT run fw4 here: with an empty /etc/config/firewall it
#         loads default-drop and blocks eth0 (ping 'Operation not permitted', SSH dead), 2026-10-26.
# Run it detached with a log, not as a long-lived SSH session:
#   scp bin/owrt-testrig-config.sh root@192.168.1.70:/tmp/ && ssh root@192.168.1.70 \
#       'sh /tmp/owrt-testrig-config.sh >/tmp/testrig-config.log 2>&1 </dev/null &'
# eth0 (mgmt) is never touched.
# Remove the layout again with: sh bin/owrt-testrig-config.sh --undo
set -eu

mode=${1:-apply}

undo() {
	for s in t3 t3v10 t4 t4v20; do uci -q delete network.$s || true; done
	for s in dev_eth3_10 dev_eth4_20; do uci -q delete network.$s || true; done
	uci commit network
}

if [ "$mode" = "--undo" ]; then
	undo
	/etc/init.d/network reload
	echo "testrig layout removed"
	exit 0
fi

[ "$(id -u)" = 0 ] || { echo "run as root" >&2; exit 1; }
[ "$(uci -q get network.mgmt.device)" = eth0 ] || { echo "network.mgmt (eth0) missing: run owrt-strip-network.sh first" >&2; exit 1; }

undo

addif() { # name device v4/len v6/len
	uci set network.$1=interface
	uci set network.$1.device=$2
	uci set network.$1.proto=static
	uci set network.$1.ipaddr=$3
	uci set network.$1.ip6addr=$4
}
addvlan() { # section parent vid
	uci set network.$1=device
	uci set network.$1.type=8021q
	uci set network.$1.ifname=$2
	uci set network.$1.name=$2.$3
	uci set network.$1.vid=$3
}

addvlan dev_eth3_10 eth3 10
addvlan dev_eth4_20 eth4 20
addif t3 eth3 10.99.1.185/24 fd99:1::185/64
addif t3v10 eth3.10 10.99.10.185/24 fd99:10::185/64
addif t4 eth4 10.99.2.185/24 fd99:2::185/64
addif t4v20 eth4.20 10.99.20.185/24 fd99:20::185/64
uci commit network

sysctl -w net.ipv4.ip_forward=1 net.ipv6.conf.all.forwarding=1 >/dev/null
/etc/init.d/network reload
/etc/init.d/firewall stop 2>/dev/null || true
/etc/init.d/firewall disable 2>/dev/null || true
sleep 5

if ip -4 addr show dev eth0 | grep -q 'inet 192\.168\.'; then
	echo "mgmt still up"
else
	echo "WARNING: eth0 lost its address; check the console" >&2
	exit 1
fi

echo "--- addresses"
ip -br addr show | grep -E 'eth[34]'
echo "--- flowtable"
nft list ruleset | grep -E 'flowtable|flow add|offload' | head
echo "--- ASK"
sed -n 1,4p /proc/ask_flowtable/status 2>/dev/null || echo "ask_flowtable not loaded"
