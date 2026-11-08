#!/bin/bash
# Suricata G7 stage-3 prototype, run ON the DUT as root (runtime only; reboot or `down` restores). Copy to /tmp first.
# Inline NFQ on forwarded TCP/UDP of the rig subnets; Suricata's bypass-mark (0x100000) is copied
# to the conntrack mark; the IPv4 flowtable rule (vyos_filter handle 10) only learns marked flows.
set -u
M=0x100000; D=/tmp/ips
case ${1:-} in
up)
  mkdir -p $D/log
  cat > $D/test.rules <<'R'
alert tcp any any -> any 5299 (msg:"ASK2-G7 test alert on port 5299"; flow:to_server; flowbits:set,ask2.alerted; sid:9900001; rev:1;)
alert tcp any any -> any [5201,5299] (msg:"ASK2-G7 bypass bulk after first data"; flow:established,to_server; stream_size:client,>,1048576; flowbits:isnotset,ask2.alerted; bypass; noalert; sid:9900002; rev:1;)
R
  nft -f - <<N
table inet ask2_ips {
  chain q { type filter hook forward priority -20; policy accept;
    ct mark and $M == $M counter accept
    ip saddr 10.99.0.0/16 meta l4proto { tcp, udp } counter queue num 0 bypass
  }
  chain m { type filter hook forward priority -19; policy accept;
    meta mark and $M == $M ct mark set ct mark or $M counter
  }
}
N
  nft replace rule ip vyos_filter VYOS_FORWARD_filter handle 10 ct mark and $M == $M counter flow add @VYOS_FLOWTABLE_FT comment '"ipv4-FWD-filter-10"'
  suricata -D --pidfile $D/s.pid -l $D/log -q 0 -S $D/test.rules -c /etc/suricata/suricata.yaml \
    --set nfq.mode=accept --set nfq.fail-open=yes --set nfq.bypass-mark=$M --set nfq.bypass-mask=$M \
    --set stream.bypass=true --set stream.reassembly.depth=1mb --set outputs.1.eve-log.enabled=yes 2>&1 | tail -3
  sleep 8; pgrep -a suricata | head -2 ;;
down)
  [ -f $D/s.pid ] && kill $(cat $D/s.pid) 2>/dev/null; sleep 2; rm -f $D/s.pid
  nft delete table inet ask2_ips 2>/dev/null
  nft replace rule ip vyos_filter VYOS_FORWARD_filter handle 10 counter flow add @VYOS_FLOWTABLE_FT comment '"ipv4-FWD-filter-10"'
  nft -a list chain ip vyos_filter VYOS_FORWARD_filter | grep 'handle 10' ;;
show)
  nft list table inet ask2_ips 2>/dev/null | grep counter
  nft -a list chain ip vyos_filter VYOS_FORWARD_filter | grep 'handle 10'
  /usr/sbin/conntrack -L 2>/dev/null | grep -E 'dport=52' ;;
*) echo "usage: $0 up|down|show"; exit 2 ;;
esac
