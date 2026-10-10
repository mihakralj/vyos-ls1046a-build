"""Shared support for flowtable module."""

from __future__ import annotations

from _flowtable_connections import SPORT
from _flowtable_rig import DPORT, TABLE, WAN_IP
from _topology import TARGET_LAN_IF, TARGET_WAN_IF


async def table(r):
    await r.nft(f'''table inet {TABLE} {{
 flowtable fast {{ hook ingress priority 0; devices = {{ {TARGET_LAN_IF}, {TARGET_WAN_IF} }}; flags offload; }}
 chain forward {{ type filter hook forward priority 0; policy accept;
 ip saddr {r.lan_ip} ip daddr {WAN_IP} udp sport {SPORT} udp dport {DPORT} flow add @fast
 ip saddr {r.lan_ip} ip daddr {WAN_IP} tcp sport {SPORT} tcp dport {DPORT} flow add @fast
 }}
}}''')
    await r.wait(lambda s: s["bindings"] == 2)
