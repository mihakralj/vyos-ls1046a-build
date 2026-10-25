#!/usr/bin/env bash
# DUT jobs that change kernel routing state, fired one kind per 60 s run (4 times per run), from dell2.
# Tests whether IPv4/IPv6 route or address changes (which bump the route-cache generation and make the
# flowtable's dst_check() fail) tear offloaded flows down, against a noop control. Needs the DUT's
# eth2 unused and dhclient@eth1..eth4 stopped so nothing else perturbs the run.
# usage (on dell2): bash ~/testrig/testrig-trigger-routes.sh   -> ~/testrig/out/trigger-*.json, trigger-routes.log
P=$HOME/testrig/testrig-trigger-probe.py
O=$HOME/testrig/out; mkdir -p "$O"
run() { tag=$1; shift; echo "##### $tag $(date +%T)"; python3 "$P" "$tag" "$@"; }
for rep in 1 2; do
  run noop$rep      a=true b=true c=true d=true
  run route4-$rep   a='sudo ip route add blackhole 198.51.100.0/24; sudo ip route del blackhole 198.51.100.0/24' \
                    b='sudo ip route add blackhole 198.51.100.0/24; sudo ip route del blackhole 198.51.100.0/24' \
                    c='sudo ip route add blackhole 198.51.100.0/24; sudo ip route del blackhole 198.51.100.0/24' \
                    d='sudo ip route add blackhole 198.51.100.0/24; sudo ip route del blackhole 198.51.100.0/24'
  run addr4-$rep    a='sudo ip addr add 192.0.2.77/32 dev lo; sudo ip addr del 192.0.2.77/32 dev lo' \
                    b='sudo ip addr add 192.0.2.77/32 dev lo; sudo ip addr del 192.0.2.77/32 dev lo' \
                    c='sudo ip addr add 192.0.2.77/32 dev lo; sudo ip addr del 192.0.2.77/32 dev lo' \
                    d='sudo ip addr add 192.0.2.77/32 dev lo; sudo ip addr del 192.0.2.77/32 dev lo'
  run route6-$rep   a='sudo ip -6 route add blackhole 2001:db8:77::/48; sudo ip -6 route del blackhole 2001:db8:77::/48' \
                    b='sudo ip -6 route add blackhole 2001:db8:77::/48; sudo ip -6 route del blackhole 2001:db8:77::/48' \
                    c='sudo ip -6 route add blackhole 2001:db8:77::/48; sudo ip -6 route del blackhole 2001:db8:77::/48' \
                    d='sudo ip -6 route add blackhole 2001:db8:77::/48; sudo ip -6 route del blackhole 2001:db8:77::/48'
done
echo "##### done $(date +%T)"
