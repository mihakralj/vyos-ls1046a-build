"""IPv6 inside an IPsec tunnel with an IPv4 outer header, in hardware.

The adapter accepts IPv4 tunnel endpoints only, so an IPv6 flow reaches an SA
as IPv6-in-IPv4: a dual-stack LAN's IPv6 carried over an IPv4 site-to-site
tunnel. What such a direction does with a packet too big for the SA's bundle
is the question here. Software checks a packet against the bundle's MTU, the
outer device's less the ESP expansion, and answers an oversized one with
Packet Too Big (xfrm6_tunnel_check_size). The entry is programmed with the
port's MTU and the expansion on top, so the hardware takes the packet, and
what it does next is what these cases pin: it encrypts the inner packet whole
and fragments the outer IPv4 packet, whose DF it leaves clear because the
inner family has none to copy. The inner packet is never fragmented, which is
what bounding an IPv6 direction exists to prevent (a router must not fragment
IPv6), and it arrives exactly once. Outer fragmentation is what Linux itself
does for an IPv4 inner packet without DF.

The same tunnel is also what carries IPv6 across a WAN that has no IPv6 at
all, and that case gets its own test. There the remote IPv6 prefix is routed
to the WAN port with no gateway and nothing else: no IPv6 default route and no
IPv6 neighbour on the link. A packet-offloaded bundle has to take its child
route from the flow, since the SA's IPv4 endpoints mean nothing to IPv6
routing, and the software path has to hand the plaintext to SEC without
resolving a neighbour of the inner destination, which nothing on the link
would answer.
"""
from __future__ import annotations

from _flowtable_ipv6_sa import DPORT, SPORT, V4_WAN_DPORT, V4_WAN_SPORT

from _flowtable_ipv6_sa import FITS, REMOTE_PREFIX, REMOTE_V6, fragments_sent, sa_pair, xfrm_counters

import asyncio
import json
import os
from pathlib import Path
import socket


from _topology import LAN_IPV6, LAN_NIC, TARGET_LAN_IF, TARGET_WAN_IF, WAN_IPV6, lan_run_python
from _flowtable_ipv6 import (PayloadEcho, _drive, _drop_tables, _hardware_delta, _offload_table, _udp_exchange)
from _flowtable_rig import command
from _flowtable_service_ipsec_replay import (xfrm_mib)


async def test_oversized(ipv6_rig):
    r = ipv6_rig
    echo = PayloadEcho()
    transport, _ = await asyncio.get_running_loop().create_datagram_endpoint(
        lambda: echo, local_addr=(WAN_IPV6, DPORT), family=socket.AF_INET6)
    cleanup = []

    async def send(count=8):
        return await _udp_exchange(r, SPORT, WAN_IPV6, DPORT, count, (WAN_IPV6, DPORT),
                                   "flowtable_v6_sa")

    try:
        # A dual-stack WAN carries an IPv6 default route. The bundle does not
        # need it: the flow's own route leaves by the offload port and is the
        # child. It is here because a dual-stack WAN is this case's setting.
        await command(r.target, r.session, "ip", "-6", "route", "add", "default", "via", WAN_IPV6,
                      "dev", TARGET_WAN_IF)
        cleanup.append((r.target, ["ip", "-6", "route", "del", "default", "via", WAN_IPV6,
                                   "dev", TARGET_WAN_IF]))
        await sa_pair(r, cleanup)
        await _offload_table(r, f"ip6 saddr {LAN_IPV6} udp sport {SPORT} udp dport {DPORT}")
        admitted = await _drive(r, send, lambda s: s["entries"] == 2,
                                "both directions of the protected IPv6 flow should be in hardware")
        forward = next(f for f in admitted["flows"] if f["sa"] != "0")
        assert forward["in_sa"] == "0" and forward["family"] == "6", admitted
        assert sum(f["in_sa"] != "0" for f in admitted["flows"]) == 1, admitted
        r.record("ipv6-sa-admitted", admitted)
        results = {}
        for size in (FITS, FITS + 1, 1452):
            payload = bytes([size % 251]) * size
            before, frags = await r.state(), await fragments_sent(r)
            peer_before = xfrm_mib(Path("/proc/net/xfrm_stat").read_text())
            counts = {f["cookie"]: int(f["packets"]) for f in before["flows"]}
            script = f'''
import json
from scapy.all import Ether, IPv6, UDP, Raw, ICMPv6PacketTooBig, sendp, sniff
packet = IPv6(src={LAN_IPV6!r}, dst={WAN_IPV6!r})/UDP(sport={SPORT}, dport={DPORT})/Raw({payload!r})
answers = sniff(iface={LAN_NIC!r}, timeout=2, lfilter=lambda p: ICMPv6PacketTooBig in p,
                started_callback=lambda: sendp(Ether(dst={r.dut_lan_mac!r})/packet,
                                               iface={LAN_NIC!r}, verbose=False))
print(json.dumps({{"too_big": [a[ICMPv6PacketTooBig].mtu for a in answers]}}))
'''
            result = await lan_run_python(r.lan, script, timeout=20, label="flowtable_v6_sa_oversized")
            assert result.rc == 0, result.stdout
            await asyncio.sleep(0.5)
            after = await r.state()
            moved = {f["cookie"]: int(f["packets"]) - counts[f["cookie"]] for f in after["flows"]}
            sent = await fragments_sent(r)
            results[size] = {"lan": json.loads(result.stdout.strip().splitlines()[-1]),
                             "delivered": echo.received[payload], "hardware": moved,
                             "fragments": {k: sent[k] - frags[k] for k in sent}}
            peer_after = xfrm_mib(Path("/proc/net/xfrm_stat").read_text())
            results[size]["peer_xfrm_delta"] = {
                key: value - peer_before[key] for key, value in peer_after.items()
                if value != peer_before[key]
            }
            # Keep the failing size and peer refusal counters even when the
            # assertion below prevents the final aggregate from being written.
            r.record(f"ipv6-sa-oversized-{size}", results[size])
            observed = results[size]
            assert observed["delivered"] == 1 and observed["lan"]["too_big"] == [], (size, observed)
            assert moved[forward["cookie"]] == 1, (size, observed)
            assert set(moved) == set(counts), (size, before, after)
            # Two outer fragments exactly when the inner packet exceeds the
            # bundle; never an inner one.
            assert observed["fragments"] == {4: 0 if size == FITS else 2, 6: 0}, (size, observed)
        final = await r.state()
        assert final["errors"] == r.errors and final["entries"] == 2, final
        r.record("ipv6-sa-oversized", {"results": results, "final": final})
    finally:
        transport.close()
        await _drop_tables(r)
        for agent, argv in reversed(cleanup):
            await command(agent, r.session, *argv, check=False)


async def test_ipv4_only_wan(ipv6_rig):
    """IPv6-in-IPv4 across a WAN with no IPv6: the remote prefix is routed to
    the WAN port without a gateway, the DUT has no IPv6 default route, and the
    far end's address is on no link, so no IPv6 neighbour exists for it.

    Every bundle must still build (XfrmOutBundleGenError does not move), the
    first packets must cross in software for the flow to be established at
    all, and once admitted the classifier carries both directions exactly."""
    r = ipv6_rig
    echo = PayloadEcho()
    transport = None
    cleanup = []

    async def send(count=8):
        return await _udp_exchange(r, V4_WAN_SPORT, REMOTE_V6, V4_WAN_DPORT, count,
                                   (REMOTE_V6, V4_WAN_DPORT), "flowtable_v6_sa_ipv4_wan")

    try:
        defaults = (await command(r.target, r.session, "ip", "-6", "route", "show",
                                  "default"))["stdout"].strip()
        assert not defaults, ("this case needs a DUT without an IPv6 default route", defaults)
        await command(r.wan, r.session, "ip", "-6", "addr", "add", REMOTE_V6 + "/128", "dev", "lo",
                      "nodad")
        cleanup.append((r.wan, ["ip", "-6", "addr", "del", REMOTE_V6 + "/128", "dev", "lo"]))
        transport, _ = await asyncio.get_running_loop().create_datagram_endpoint(
            lambda: echo, local_addr=(REMOTE_V6, V4_WAN_DPORT), family=socket.AF_INET6)
        await command(r.target, r.session, "ip", "-6", "route", "add", REMOTE_PREFIX, "dev",
                      TARGET_WAN_IF)
        cleanup.append((r.target, ["ip", "-6", "route", "del", REMOTE_PREFIX, "dev",
                                   TARGET_WAN_IF]))
        await sa_pair(r, cleanup, remote=REMOTE_V6)
        await _offload_table(r, f"ip6 saddr {LAN_IPV6} udp sport {V4_WAN_SPORT} "
                                f"udp dport {V4_WAN_DPORT}")
        mib = await xfrm_counters(r)
        # Admission needs an established connection, so it also proves the
        # first packets crossed the software path in both directions.
        admitted = await _drive(r, send, lambda s: s["entries"] == 2,
                                "the protected IPv6 flow should be in hardware on an IPv4-only WAN")
        rows = {f["in"]: f for f in admitted["flows"]}
        assert set(rows) == {TARGET_LAN_IF, TARGET_WAN_IF}, admitted
        assert rows[TARGET_LAN_IF]["sa"] != "0" and rows[TARGET_WAN_IF]["in_sa"] != "0", admitted
        assert all(f["family"] == "6" for f in rows.values()), admitted
        # The encrypted direction's next hop is the one IPv4 routing gives
        # the tunnel's endpoint, resolved in that family: the IPv6 route
        # under the bundle has no neighbour for it.
        peer = os.environ["ASK_WAN_IPERF_IP"]
        route = json.loads((await command(r.target, r.session, "ip", "-j", "route", "get",
                                          peer))["stdout"])[0]
        assert rows[TARGET_LAN_IF]["nexthop"] == route.get("gateway", peer), (route, admitted)
        report = await send(64)
        assert report == {"echoed": 64, "lost": 0}, report
        after = await r.state()
        moved = {f["in"]: f for f in after["flows"]}
        delta = _hardware_delta(rows, moved)
        assert delta == {TARGET_LAN_IF: 64, TARGET_WAN_IF: 64}, (delta, admitted, after)
        assert (admitted["installs"], admitted["deletes"]) == (after["installs"], after["deletes"]), \
            (admitted, after)
        assert after["errors"] == r.errors, after
        for direction in rows:
            assert moved[direction]["cookie"] == rows[direction]["cookie"], (rows, moved)
        now = await xfrm_counters(r)
        refused = {name: now[name] - mib[name]
                   for name in ("XfrmOutBundleGenError", "XfrmOutBundleCheckError",
                                "XfrmOutNoStates", "XfrmOutError")}
        assert not any(refused.values()), refused
        r.record("ipv6-sa-ipv4-only-wan", {"admitted": admitted, "after": after,
                                           "hardware_delta": delta, "xfrm": refused})
    finally:
        if transport:
            transport.close()
        await _drop_tables(r)
        for agent, argv in reversed(cleanup):
            await command(agent, r.session, *argv, check=False)
