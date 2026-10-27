#!/bin/sh
# Strip a pre-configured OpenWrt (e.g. OpenWrt+ASK reference image) down to a
# network-neutral state: no DHCP server, DNS, firewall/NAT or bridge; eth0 is a
# DHCP client so the agent can reach the board; dropbear SSH is enabled with
# the agent's public key installed. Run ON the OpenWrt DUT from the
# serial console (the network restart drops SSH). Originals saved as *.bak.
# Usage: sh owrt-strip-network.sh [iface]   (default eth0 = left RJ45)
set -eu

IFACE="${1:-eth0}"

for svc in dnsmasq odhcpd firewall; do
	if [ -x "/etc/init.d/$svc" ]; then
		"/etc/init.d/$svc" stop || true
		"/etc/init.d/$svc" disable || true
	fi
done

for f in dhcp firewall network; do
	[ -f "/etc/config/$f" ] && [ ! -f "/etc/config/$f.bak" ] && cp "/etc/config/$f" "/etc/config/$f.bak"
done
: > /etc/config/dhcp
: > /etc/config/firewall

cat > /etc/config/network <<EOF
config interface 'loopback'
	option device 'lo'
	option proto 'static'
	option ipaddr '127.0.0.1'
	option netmask '255.0.0.0'

config interface 'mgmt'
	option device '$IFACE'
	option proto 'dhcp'
EOF

# SSH: dropbear enabled + agent public key (matches ~/.ssh/vyos_key)
SSH_PUBKEY='ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIBTFXhL3cM7V4TqClTrl2AEI80Jcv6Fuyimm++VyOS++ vanityssh'
mkdir -p /etc/dropbear
touch /etc/dropbear/authorized_keys
grep -qxF "$SSH_PUBKEY" /etc/dropbear/authorized_keys || echo "$SSH_PUBKEY" >> /etc/dropbear/authorized_keys
chmod 700 /etc/dropbear
chmod 600 /etc/dropbear/authorized_keys
/etc/init.d/dropbear enable
/etc/init.d/dropbear restart || /etc/init.d/dropbear start

/etc/init.d/network restart

# dnsmasq is off, so resolv.conf must use the DHCP-provided servers instead of 127.0.0.1
ln -sf /tmp/resolv.conf.d/resolv.conf.auto /tmp/resolv.conf
sleep 5
ip -br addr show "$IFACE"
nslookup openwrt.org || echo "DNS check failed (needs uplink + DHCP DNS)"
echo "Leftover bridge/NAT refs (review manually):"
grep -rl 'br-lan\|masq' /etc 2>/dev/null | grep -v '\.bak$' || true
