"""A packet-offloaded SA's plaintext leaves by the SA's own port or not at all.

xfrm_output() hands a packet for such an SA on still in plaintext, and only
the port the SA was installed on gives it to SEC. The bundle's child route is
looked up afresh for every forwarded packet, so when the route to the peer
moves to another device -- a failover, a more specific route, a routing rule
-- the child names that device, and a frame sent there would leave in the
clear: the upstream wrong-device drop in validate_xmit_xfrm() keys on
xfrm_offload(), which answers NULL for these frames because the state is
carried with len and no olen.

The flow here is forwarded LAN -> WAN host, IPv4 in IPv4, with no flowtable, so
every packet takes the software path. Four phases on one SA pair:

  - the route to the peer leaves by the WAN port: every datagram is echoed and
    the port hands exactly that many frames to SEC (`tx toenc`);
  - the route is moved to a dummy device: every datagram is refused and
    counted as XfrmOutBundleCheckError, the dummy transmits nothing, and SEC
    sees nothing either;
  - the route is moved back: the same SA carries traffic again, so nothing
    about the refusal outlived its cause;
  - with the route still on the WAN port, a tc filter on that port's egress
    redirects the plaintext to the dummy after xfrm_output() let it through:
    the dummy's own transmit path refuses every frame, with the same counter,
    and neither the dummy nor SEC sees one.

The leak oracle is the dummy's own transmit counter. A postrouting counter on
the dummy would not do: the bundle's POSTROUTING runs before xfrm_output() and
names the child's device as its output, so it counts the refused packets too.
"""
from __future__ import annotations

from _ipsec_offload_egress_device import DETOUR_GATEWAY, DPORT, V6_DPORT, V6_SPORT

from _ipsec_offload_egress_device import COUNT, DETOUR, INNER, PEER, add_detour, counters, install_tunnel, moved, payload, send, send6, wan_outer

import asyncio
import json
import os
import socket

from ask_orch.client import Agent
from ask_orch.uart import Console
from _topology import LAN_IPV6, TARGET_WAN_IF
from _flowtable_ipv6 import (PayloadEcho, _udp_exchange)
from _flowtable_ipv6_sa import (REMOTE_V6)
from _flowtable_rig import (artifact_dir, WAN_IP, Echo, command, console_command)


async def test_offloaded_sa_plaintext_stays_on_its_port(rig):
    r = rig
    wan = Agent("wan", f"http://{os.environ.get('ASK_WAN_IP', '127.0.0.1')}:9110")
    outer = await wan_outer(r)
    echo = Echo()
    transport = None
    cleanup = []

    async def step(agent, argv, undo):
        await command(agent, r.session, *argv)
        cleanup.append((agent, undo))

    try:
        route = json.loads(r.lan.run(f"ip -j route get {INNER}", timeout=10).stdout.strip())[0]
        assert route.get("gateway") == r.lan_gateway, ("the LAN VM must reach INNER through the DUT",
                                                        route)
        await install_tunnel(r, wan, outer, [r.lan_ip], step)
        transport, _ = await asyncio.get_running_loop().create_datagram_endpoint(
            lambda: echo, local_addr=(INNER, DPORT))
        await add_detour(r, step)

        phases = {}
        before = await counters(r)
        echoed = await send(r, 0, COUNT, wait=True)
        phases["wan"] = {"echoed": echoed, **moved(before, await counters(r))}
        assert phases["wan"] == {"echoed": COUNT, "refused": 0, "detour_tx": 0, "toenc": COUNT}, phases
        assert all(echo.received[payload(n)] == 1 for n in range(COUNT)), echo.received

        await command(r.target, r.session, "ip", "route", "replace", PEER + "/32", "via",
                      DETOUR_GATEWAY, "dev", DETOUR)
        before = await counters(r)
        await send(r, COUNT, COUNT, wait=False)
        await asyncio.sleep(0.5)
        phases["detour"] = {"delivered": sum(echo.received[payload(n)]
                                             for n in range(COUNT, 2 * COUNT)),
                            **moved(before, await counters(r))}
        assert phases["detour"] == {"delivered": 0, "refused": COUNT, "detour_tx": 0, "toenc": 0}, \
            phases

        await command(r.target, r.session, "ip", "route", "replace", PEER + "/32", "via", WAN_IP,
                      "dev", TARGET_WAN_IF)
        before = await counters(r)
        echoed = await send(r, 2 * COUNT, COUNT, wait=True)
        phases["restored"] = {"echoed": echoed, **moved(before, await counters(r))}
        assert phases["restored"] == {"echoed": COUNT, "refused": 0, "detour_tx": 0,
                                      "toenc": COUNT}, phases

        # `tc` is not in the agent's argv allowlist, so the filter is built on
        # the console.
        await command(r.target, r.session, "modprobe", "act_mirred")
        with Console.target(log_path=str(artifact_dir() / "ipsec-egress-device-uart.log")) as console:
            await asyncio.to_thread(console.login, "root", None)
            clsact = False
            try:
                await console_command(console, "tc", "qdisc", "add", "dev", TARGET_WAN_IF, "clsact")
                clsact = True
                await console_command(console, "tc", "filter", "add", "dev", TARGET_WAN_IF, "egress",
                                      "protocol", "ip", "flower", "dst_ip", INNER, "action",
                                      "mirred", "egress", "redirect", "dev", DETOUR)
                before = await counters(r)
                await send(r, 3 * COUNT, COUNT, wait=False)
                await asyncio.sleep(0.5)
                phases["redirected"] = {"delivered": sum(echo.received[payload(n)]
                                                         for n in range(3 * COUNT, 4 * COUNT)),
                                        **moved(before, await counters(r))}
            finally:
                if clsact:
                    await console_command(console, "tc", "qdisc", "del", "dev", TARGET_WAN_IF,
                                          "clsact", check=False)
        assert phases["redirected"] == {"delivered": 0, "refused": COUNT, "detour_tx": 0,
                                        "toenc": 0}, phases
        r.record("ipsec-egress-device", phases)
    finally:
        if transport:
            transport.close()
        for agent, argv in reversed(cleanup):
            await command(agent, r.session, *argv, check=False)


async def test_offloaded_cross_family_sa_stays_on_its_port(ipv6_rig):
    """The first three phases for IPv6 inside the IPv4 tunnel. The bundle's
    route is then the flow's own, which does not move; what moves is the IPv4
    route to the peer, which the kernel asks per packet in the SA's own
    family and refuses the packet for once it leaves by another device."""
    r = ipv6_rig
    outer = await wan_outer(r)
    echo = PayloadEcho()
    transport = None
    cleanup = []

    async def step(agent, argv, undo):
        await command(agent, r.session, *argv)
        cleanup.append((agent, undo))

    async def exchange():
        return await _udp_exchange(r, V6_SPORT, REMOTE_V6, V6_DPORT, COUNT, (REMOTE_V6, V6_DPORT),
                                   "ipsec_egress_device_v6")

    try:
        await install_tunnel(r, r.wan, outer, [LAN_IPV6], step, inner=REMOTE_V6)
        transport, _ = await asyncio.get_running_loop().create_datagram_endpoint(
            lambda: echo, local_addr=(REMOTE_V6, V6_DPORT), family=socket.AF_INET6)
        await add_detour(r, step)

        phases = {}
        before = await counters(r)
        report = await exchange()
        phases["wan"] = {**report, **moved(before, await counters(r))}
        assert phases["wan"] == {"echoed": COUNT, "lost": 0, "refused": 0, "detour_tx": 0,
                                 "toenc": COUNT}, phases

        await command(r.target, r.session, "ip", "route", "replace", PEER + "/32", "via",
                      DETOUR_GATEWAY, "dev", DETOUR)
        before, delivered = await counters(r), echo.packets
        await send6(r, COUNT)
        await asyncio.sleep(0.5)
        phases["detour"] = {"delivered": echo.packets - delivered, **moved(before, await counters(r))}
        assert phases["detour"] == {"delivered": 0, "refused": COUNT, "detour_tx": 0, "toenc": 0}, \
            phases

        await command(r.target, r.session, "ip", "route", "replace", PEER + "/32", "via", WAN_IP,
                      "dev", TARGET_WAN_IF)
        before = await counters(r)
        report = await exchange()
        phases["restored"] = {**report, **moved(before, await counters(r))}
        assert phases["restored"] == {"echoed": COUNT, "lost": 0, "refused": 0, "detour_tx": 0,
                                      "toenc": COUNT}, phases
        r.record("ipsec-egress-device-v6", phases)
    finally:
        if transport:
            transport.close()
        for agent, argv in reversed(cleanup):
            await command(agent, r.session, *argv, check=False)
