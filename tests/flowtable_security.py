"""Tunnel endpoint, inbound policy and bridge egress filter regressions."""
from __future__ import annotations

import asyncio
import json
import socket
from types import SimpleNamespace

import pytest

from _topology import TARGET_WAN_IF
from _flowtable_rig import (DPORT, SPORT, WAN_IP, command)
from _flowtable_tunnel import (DUT_WAN_IPV4, _offload_table, _udp_exchange)


async def warm_tunnel(r):
    await _offload_table(r)
    for _ in range(4):
        assert await _udp_exchange(r, 8) == {"echoed": 8, "lost": 0}
        await asyncio.sleep(1)


@pytest.mark.parametrize(("tunnel_rig", "options"),
                         [("6o4", False), ("4o6", False), ("4o6", True)],
                         indirect=["tunnel_rig"])
@pytest.mark.parametrize("wrong", ["source", "destination", "protocol"])
async def test_tunnel_outer_endpoints(tunnel_rig, options, wrong):
    """A hardware reply must match both outer endpoints and the encapsulation."""
    from scapy.all import Ether, GRE, IP, IPv6, IPv6ExtHdrDestOpt, UDP

    r = tunnel_rig
    await warm_tunnel(r)
    await r.wait(lambda state: any(f["in_tnl"] != "-" for f in state["flows"]))
    shape = r.shape
    outer, inner = (IP, IPv6) if shape.mode == "6o4" else (IPv6, IP)
    endpoints = {"src": shape.outer[1], "dst": shape.outer[0]}
    original = r.echo.datagram_received
    sent = 0
    wrong_protocol = False
    with socket.socket(socket.AF_PACKET, socket.SOCK_RAW) as wire:
        wire.bind((r.wan_if, 0))

        def reply(data, addr):
            nonlocal sent
            packet = Ether(src=r.wan_mac, dst=r.dut_wan_mac) / outer(**endpoints)
            if options:
                packet /= IPv6ExtHdrDestOpt()
            if wrong_protocol:
                packet /= GRE()
            packet /= (inner(src=shape.inner_orch, dst=r.lan_address) /
                       UDP(sport=shape.dport, dport=shape.sport) / data)
            wire.send(bytes(packet))
            sent += 1

        r.echo.datagram_received = reply
        try:
            before = next(f for f in (await r.state())["flows"] if f["in_tnl"] != "-")
            assert await _udp_exchange(r, 4) == {"echoed": 4, "lost": 0}
            good = next(f for f in (await r.state())["flows"] if f["cookie"] == before["cookie"])
            assert int(good["packets"]) - int(before["packets"]) == 4, (before, good)
            key = "src" if wrong == "source" else "dst"
            saved = endpoints[key]
            if wrong == "protocol":
                wrong_protocol = True
            else:
                endpoints[key] = "198.18.250.2" if outer is IP else "fd42:6173:ffff::2"
            result = await _udp_exchange(r, 4)
            state = await r.state()
            r.record("security-tunnel-" + shape.mode + ("-options" if options else "") + "-" + wrong,
                     {"result": result, "sent": sent, "state": state})
            assert sent == 8, "the WAN did not send every probe"
            assert result == {"echoed": 0, "lost": 4}, result
            assert any(f["in_tnl"] != "-" for f in state["flows"]), state
            unchanged = next(f for f in state["flows"] if f["cookie"] == before["cookie"])
            assert unchanged["packets"] == good["packets"], (good, unchanged)
            endpoints[key] = saved
            wrong_protocol = False
            assert await _udp_exchange(r, 4) == {"echoed": 4, "lost": 0}
        finally:
            r.echo.datagram_received = original


@pytest.mark.parametrize("tunnel_rig", ["4o6"], indirect=True)
@pytest.mark.parametrize("policy", ["block", "esp", "default", "device", "unrelated", "allow"])
async def test_tunnel_inbound_xfrm_policy(tunnel_rig, policy):
    """An inbound-only outer policy must stop plaintext on an established flow."""
    r = tunnel_rig
    await warm_tunnel(r)
    local, remote = r.shape.outer
    selector = ["src", remote + "/128", "dst", local + "/128", "proto", "4", "dir", "in"]
    await r.wait(lambda state: any(f["in_tnl"] != "-" for f in state["flows"]))
    if policy == "device":
        selector[6:6] = ["dev", "lo"]
    elif policy == "unrelated":
        selector[1] = "fd42:6173:ffff::2/128"
    requirement = (["tmpl", "src", remote, "dst", local, "proto", "esp",
                    "mode", "transport", "level", "required"] if policy == "esp" else
                   ["action", "allow" if policy == "allow" else "block"])
    if policy == "default":
        await command(r.target, r.session, "ip", "xfrm", "policy", "setdefault", "in", "block")
    else:
        await command(r.target, r.session, "ip", "-6", "xfrm", "policy", "add", *selector, *requirement)
    try:
        # Policy generation retirement is asynchronous. Keep sending long
        # enough to exercise readmission under the new policy as well.
        await asyncio.sleep(3)
        results = [await _udp_exchange(r, 4) for _ in range(2)]
        state = await r.state()
        r.record("security-xfrm-" + policy, {"results": results, "state": state})
        allowed = policy in {"unrelated", "allow"}
        assert results == [{"echoed": 4, "lost": 0} if allowed else {"echoed": 0, "lost": 4}] * 2, results
        if allowed:
            await r.wait(lambda state: any(f["in_tnl"] != "-" for f in state["flows"]))
        else:
            assert not any(f["in_tnl"] != "-" for f in state["flows"]), state
    finally:
        if policy == "default":
            await command(r.target, r.session, "ip", "xfrm", "policy", "setdefault", "in", "accept")
        else:
            await command(r.target, r.session, "ip", "-6", "xfrm", "policy", "delete", *selector)
    for _ in range(4):
        assert await _udp_exchange(r, 8) == {"echoed": 8, "lost": 0}
        await asyncio.sleep(1)
    await r.wait(lambda state: any(f["in_tnl"] != "-" for f in state["flows"]))


@pytest.mark.parametrize("hook", ["output", "postrouting"])
@pytest.mark.parametrize("installed", [False, True], ids=["before-admission", "after-admission"])
async def test_unicast_bridge_egress_filter(bridge_software, hook, installed):
    """Bridge drops hold both at admission and after a flow enters hardware."""
    r = bridge_software
    r.lan_address = r.lan_ip
    r.shape = SimpleNamespace(family=4, sport=SPORT, dport=DPORT, inner_orch=WAN_IP)
    await r.table()
    if installed:
        for _ in range(4):
            assert await _udp_exchange(r, 8) == {"echoed": 8, "lost": 0}
            await asyncio.sleep(1)
        await r.wait(lambda s: s["entries"] == 2)
    table = "ask_security_bridge"
    await command(r.target, r.session, "nft", f'''table bridge {table} {{
 chain blocked {{ type filter hook {hook} priority 0; policy accept;
 ether daddr {r.lan_mac} ip saddr {WAN_IP} udp sport {DPORT} udp dport {SPORT} counter drop
 }}
}}''')
    try:
        await asyncio.sleep(3)
        results = [await _udp_exchange(r, 4) for _ in range(2)]
        state = await r.state()
        listing = json.loads((await command(r.target, r.session, "nft", "-j", "list",
                                            "table", "bridge", table))["stdout"])
        dropped = sum(expr["counter"]["packets"] for item in listing["nftables"] if "rule" in item
                      for expr in item["rule"]["expr"] if "counter" in expr)
        r.record(f"security-bridge-{hook}-{installed}",
                 {"results": results, "dropped": dropped, "state": state})
        assert results == [{"echoed": 0, "lost": 4}] * 2, results
        assert dropped >= 8, listing
        assert not any(f["in"] == TARGET_WAN_IF for f in state["flows"]), state
    finally:
        await command(r.target, r.session, "nft", "delete", "table", "bridge", table)
    for _ in range(4):
        assert await _udp_exchange(r, 8) == {"echoed": 8, "lost": 0}
        await asyncio.sleep(1)
    await r.wait(lambda s: s["entries"] == 2)
