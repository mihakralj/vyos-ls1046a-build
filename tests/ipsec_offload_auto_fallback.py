"""A child SA strongSwan installs with `hw_offload = auto` is either offloaded
whole or works in software.

strongSwan 6.0.3's kernel-netlink backend treats every SA and policy of a
child SA on its own. It installs the inbound SA, then the outbound one, then
the policies, each bound to the device that holds the local address. An SA
the kernel refuses with packet offload it retries as crypto offload, which the
adapter refuses as well, and xfrm then installs it in software; a policy the
kernel refuses with packet offload it retries without. So when the adapter
refused only a tunnel's outbound SA -- a peer the route to which leaves by
another device, such as a LAN client tunnelling to the WAN address -- the
child SA came up with that SA in software, its inbound SA in hardware, and
both policies packet-offloaded. A packet-offloaded outbound policy selects
packet-offloaded states only (xfrm_state_find()), so every packet the tunnel
sent waited on an acquire, and the inbound half, arriving on the LAN, was
dropped as never reaching SEC.

The adapter now refuses the inbound SA of such a tunnel as well -- the route
to its peer leaves by another device, which is what refuses the outbound
half -- and an outbound policy naming an SA it does not hold. The sequence
below is strongSwan's, driven with `ip xfrm`: each SA tried with packet
offload and retried as crypto offload, each policy tried with packet offload
and retried without. The peer is routed through a dummy device, so nothing
sent to it arrives anywhere, and nothing arrives from it: this pins what is
installed and that the tunnel's own traffic finds its SA, not a foreign
device's receive path.

A peer routed by the offload port takes the same sequence and ends fully
offloaded, which is the ordinary case and must not change.
"""

from __future__ import annotations

import asyncio

import pytest

from ask_orch.uart import Console
from _topology import TARGET_WAN_IF
from _flowtable_rig import (artifact_dir, console_command, read)
from _flowtable_service_ipsec_replay import (xfrm_mib)
from _ipsec_inbound_flow_offload import (crypto)

# Addresses from the RFC 2544 benchmarking block (198.18.0.0/15), distinct from
# every other IPsec file's.
LOCAL = "198.18.233.1"
PEER = "198.18.233.2"
INNER = "198.18.234.2"
DETOUR = "askauto0"
REQIDS = {"out": "49331", "in": "49332"}
SPIS = {"out": "0xa2330001", "in": "0xa2330002"}
PEER_MAC = "02:00:00:00:23:32"


async def _run(agent, session, *argv, expect_rc=0):
    result = await agent.exec_cmd(session, list(argv))
    if expect_rc is not None:
        assert result["rc"] == expect_rc, (argv, result)
    return result


def _state(direction):
    src, dst = (LOCAL, PEER) if direction == "out" else (PEER, LOCAL)
    return ["src", src, "dst", dst, "proto", "esp", "spi", SPIS[direction]]


def _selector(direction):
    src, dst = (LOCAL, INNER) if direction == "out" else (INNER, LOCAL)
    return ["src", f"{src}/32", "dst", f"{dst}/32"]


def _template(direction, spi):
    src, dst = (LOCAL, PEER) if direction == "out" else (PEER, LOCAL)
    # strongSwan names the SA's SPI in an outbound policy's template, and in
    # no other.
    return ["tmpl", "src", src, "dst", dst, "proto", "esp", *(["spi", spi] if spi else []),
            "mode", "tunnel", "reqid", REQIDS[direction], "level", "required"]


async def _add_state(agent, session, direction):
    """What strongSwan's add_sa() does under `auto`: packet offload, then
    crypto offload after a refusal. Returns the refusal, if any."""
    base = ["ip", "xfrm", "state", "add", *_state(direction), *crypto(REQIDS[direction])]
    first = await _run(agent, session, *base, "offload", "packet", "dev", TARGET_WAN_IF,
                       "dir", direction, expect_rc=None)
    if first["rc"] == 0:
        return None
    await _run(agent, session, *base, "offload", "dev", TARGET_WAN_IF, "dir", direction)
    return first["stderr"] + first["stdout"]


async def _add_policy(agent, session, direction, spi=None):
    """What strongSwan's add_policy_internal() does under `auto`: packet
    offload, then none after a refusal. Returns the refusal, if any."""
    base = ["ip", "xfrm", "policy", "add", *_selector(direction), "dir", direction,
            *_template(direction, spi)]
    first = await _run(agent, session, *base, "offload", "packet", "dev", TARGET_WAN_IF,
                       expect_rc=None)
    if first["rc"] == 0:
        return None
    await _run(agent, session, *base)
    return first["stderr"] + first["stdout"]


async def _state_offloaded(agent, session, direction):
    shown = await _run(agent, session, "ip", "-d", "xfrm", "state", "get", *_state(direction))
    return "crypto offload parameters" in shown["stdout"]


async def _policy_offloaded(agent, session, direction):
    shown = await _run(agent, session, "ip", "xfrm", "policy", "get", *_selector(direction),
                       "dir", direction)
    return "offload" in shown["stdout"]


async def _state_packets(agent, session, direction):
    shown = await _run(agent, session, "ip", "-s", "xfrm", "state", "get", *_state(direction))
    lines = shown["stdout"].splitlines()
    current = next(i for i, line in enumerate(lines) if "lifetime current" in line)
    # "  <bytes>(bytes), <packets>(packets)" on the line after the header.
    return int(lines[current + 1].split(",")[1].split("(")[0])


async def _clear(agent, session):
    for direction in ("out", "in", "fwd"):
        await _run(agent, session, "ip", "xfrm", "policy", "delete",
                   *_selector("in" if direction == "fwd" else direction), "dir", direction,
                   expect_rc=None)
    for direction in ("out", "in"):
        await _run(agent, session, "ip", "xfrm", "state", "delete", *_state(direction),
                   expect_rc=None)


async def _child_sa(agent, session):
    """The whole child SA, in strongSwan's order: inbound SA, outbound SA,
    then the inbound, forwarding and outbound policies."""
    refused = {"in_state": await _add_state(agent, session, "in"),
               "out_state": await _add_state(agent, session, "out"),
               "in_policy": await _add_policy(agent, session, "in")}
    # strongSwan never offloads a forwarding policy.
    await _run(agent, session, "ip", "xfrm", "policy", "add", *_selector("in"), "dir", "fwd",
               *_template("in", None))
    refused["out_policy"] = await _add_policy(agent, session, "out", SPIS["out"])
    return refused


@pytest.mark.usefixtures("splat_window")
async def test_auto_peer_behind_another_device_works_in_software(aiohttp_session, target_agent):
    agent, session = target_agent, aiohttp_session
    setup = [
        ["ip", "address", "replace", f"{LOCAL}/32", "dev", TARGET_WAN_IF],
        ["ip", "link", "add", DETOUR, "type", "dummy"],
        ["ip", "link", "set", DETOUR, "up"],
        ["ip", "route", "replace", f"{PEER}/32", "dev", DETOUR],
        ["ip", "route", "replace", f"{INNER}/32", "dev", DETOUR],
    ]
    teardown = [
        ["ip", "link", "del", DETOUR],
        ["ip", "address", "del", f"{LOCAL}/32", "dev", TARGET_WAN_IF],
    ]
    await _clear(agent, session)
    for argv in setup:
        await _run(agent, session, *argv)
    console = None
    try:
        refused = await _child_sa(agent, session)
        # Both SAs refused as packet offload, for the route to the peer, and
        # installed in software by the crypto-offload retry.
        for key in ("in_state", "out_state"):
            assert refused[key] and "does not leave by the offload device" in refused[key], refused
        assert not await _state_offloaded(agent, session, "in")
        assert not await _state_offloaded(agent, session, "out")
        # The inbound policy is checked against whatever decrypted a packet,
        # so it stays offloaded; the outbound one names an SA the adapter
        # does not hold, so it goes to software, where that SA is.
        assert refused["in_policy"] is None, refused
        assert refused["out_policy"] and "not offloaded to its device" in refused["out_policy"], refused
        assert await _policy_offloaded(agent, session, "in")
        assert not await _policy_offloaded(agent, session, "out")

        # And the tunnel's own traffic finds its SA rather than an acquire:
        # one packet from the local endpoint to the inner address is
        # encrypted by the software SA and leaves by the dummy.
        before_mib = xfrm_mib(await read(agent, session, "/proc/net/xfrm_stat"))
        before_sa = await _state_packets(agent, session, "out")
        before_tx = int(await read(agent, session, f"/sys/class/net/{DETOUR}/statistics/tx_packets"))
        # `ping` is not in the agent's argv allowlist, so it runs on the
        # console. Nothing answers it; only the one packet leaving matters,
        # and a printk breaking the console's framing does not change that.
        console = Console.target(log_path=str(artifact_dir() / "ipsec-auto-uart.log"))
        await asyncio.to_thread(console.login, "root", None)
        ping = await console_command(console, "ping", "-c", "1", "-W", "1", "-I", LOCAL, INNER,
                                     check=False, resync=True)
        after_mib = xfrm_mib(await read(agent, session, "/proc/net/xfrm_stat"))
        # Async SEC encryption takes the packet and sends it later. A
        # noqueue device used to report that as -ENOMEM, and ping resent it.
        assert "sendmsg" not in ping["stdout"], ping
        assert await _state_packets(agent, session, "out") == before_sa + 1
        assert int(await read(agent, session,
                              f"/sys/class/net/{DETOUR}/statistics/tx_packets")) >= before_tx + 1
        assert after_mib.get("XfrmOutNoStates", 0) == before_mib.get("XfrmOutNoStates", 0), \
            (before_mib, after_mib)
    finally:
        if console is not None:
            console.close()
        await _clear(agent, session)
        for argv in teardown:
            await _run(agent, session, *argv, expect_rc=None)


@pytest.mark.usefixtures("splat_window")
async def test_auto_peer_on_the_port_is_offloaded_whole(aiohttp_session, target_agent):
    agent, session = target_agent, aiohttp_session
    setup = [
        ["ip", "address", "replace", f"{LOCAL}/32", "dev", TARGET_WAN_IF],
        ["ip", "route", "replace", f"{PEER}/32", "dev", TARGET_WAN_IF],
        ["ip", "neigh", "replace", PEER, "lladdr", PEER_MAC, "dev", TARGET_WAN_IF,
         "nud", "permanent"],
    ]
    teardown = [
        ["ip", "neigh", "del", PEER, "dev", TARGET_WAN_IF],
        ["ip", "route", "del", f"{PEER}/32", "dev", TARGET_WAN_IF],
        ["ip", "address", "del", f"{LOCAL}/32", "dev", TARGET_WAN_IF],
    ]
    await _clear(agent, session)
    for argv in setup:
        await _run(agent, session, *argv)
    try:
        refused = await _child_sa(agent, session)
        assert refused == {"in_state": None, "out_state": None, "in_policy": None,
                           "out_policy": None}, refused
        for direction in ("in", "out"):
            assert await _state_offloaded(agent, session, direction)
            assert await _policy_offloaded(agent, session, direction)
    finally:
        await _clear(agent, session)
        for argv in teardown:
            await _run(agent, session, *argv, expect_rc=None)
