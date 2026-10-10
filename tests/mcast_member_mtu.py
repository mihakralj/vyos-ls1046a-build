"""A196: a multicast group is carried only while no replica can outgrow a listener.

A listener's entry ends in ENQUEUE_PKT, which fragments any replica larger than
its MTU, and no preemptive check stands in front of it. Linux never does that
to a forwarded multicast packet: ip6mr answers an oversized IPv6 replica with
Packet Too Big, and ipmr drops an IPv4 one with DF set. So a routed group whose
listener MTU is below what its ingress can deliver stays in software, and the
test proves both halves on the wire -- nothing oversized reaches the narrow
listener, whole or in fragments, and a full-size stream into a listener as
large as the ingress is still replicated by the hardware.

Routed rather than bridged because the bench can shrink a routed listener
without touching the managed ports: the listener is a VLAN device on the LAN
port, and its MTU is the listener's. The bridged bound is the same comparison
in device MTUs and is covered by the host harness.
"""
from __future__ import annotations

import asyncio
import json
import re
import time

import pytest

from _mcast_wire import capture, frames, new_config, send
from _topology import (
    LAN_NIC, TARGET_LAN_IF, TARGET_WAN_IF, TopologyStack, VLAN_ID_MCAST_MTU,
    dut_vlan_subif, lan_vlan_subif,
)
from _flowtable_rig import (read)
from _mcast_e2e import (_exec, mroute_line, mroute_proc_row, wan_source_address)
from _mroute_capacity import (_daemon, _preflight)

pytestmark = pytest.mark.asyncio
PORT = 47396
COUNT = 64
SMALL, OVERSIZED, FULL = 1, 2, 3


async def _fragments(target, session):
    text = await read(target, session, "/proc/ucode_frag/stats")
    counts = {}
    for family in (4, 6):
        counts[family] = int(text.split(f"Number of IPv{family} fragments sent :")[1].split()[0])
    return counts


async def _state(target, session, group, state, timeout=15, *, family=None):
    """Wait for the group's row to say `state`.

    With `family`, a few small frames of the stream go out between looks. A
    group re-derived into hardware is carried on the confirmations it
    gathered in software, and an nftables commit anywhere in between -- a
    daemon's included -- takes those back: only a copy Linux forwards after
    it confirms the group again."""
    last = ""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        last = await mroute_proc_row(target, session, group)
        if f"state={state} " in last:
            return last
        if family is not None:
            config = new_config(family, wan_source_address(family), group, PORT, [])
            await asyncio.to_thread(send, frames(config, {SMALL: 256}, 4))
        await asyncio.sleep(0.2)
    changes = re.search(r"^mroute_ruleset_changes (\d+)$",
                        await read(target, session, "/proc/cdx_flowtable"), re.M)
    raise AssertionError(f"{group}: expected state={state}, got {last!r}; "
                         f"ruleset changes so far: {changes and changes[1]}")


def _packets(row):
    return int(re.search(r"packets=(\d+)", row)[1])


async def _window(target, session, lan, peer, family, group, sizes, label):
    config = new_config(family, wan_source_address(family), group, PORT, [peer])
    fragments = await _fragments(target, session)
    before = await mroute_proc_row(target, session, group)
    async with capture(lan, config) as handle:
        await asyncio.to_thread(send, frames(config, sizes, COUNT))
        await asyncio.sleep(0.5)
    after = await mroute_proc_row(target, session, group)
    result = handle["result"][peer]
    assert not result["errors"], (label, result)
    # The fragmenter never ran, for either family, whatever else happened.
    assert await _fragments(target, session) == fragments, label
    # And nothing on the wire was a fragment of this stream: not from the
    # microcode, and not from Linux, which may not fragment these either.
    assert result["fragments"] == 0, (label, result)
    assert result["duplicates"] == 0, (label, result)
    return result, before, after


@pytest.mark.parametrize("family", [4, 6])
async def test_bound(aiohttp_session, target_agent, lan,
                                      splat_window, family):
    await _preflight(target_agent, aiohttp_session)
    # Every size below is relative to a 1500-byte ingress, in both units.
    link = json.loads((await _exec(target_agent, aiohttp_session, "ip", "-j", "link",
                                   "show", "dev", TARGET_WAN_IF))["stdout"])[0]
    assert link["mtu"] == 1500, link
    mtu6 = (await read(target_agent, aiohttp_session,
                       f"/proc/sys/net/ipv6/conf/{TARGET_WAN_IF}/mtu")).strip()
    assert mtu6 == "1500", mtu6
    topology = TopologyStack()
    group = "239.8.196.1" if family == 4 else "ff1e::8:196:1"
    source = wan_source_address(family)
    vid = VLAN_ID_MCAST_MTU
    try:
        oif = await dut_vlan_subif(
            topology, target_agent, aiohttp_session, parent=TARGET_LAN_IF, vid=vid,
            ipv4="198.18.196.1/30", ipv6=f"fd00:196:{vid:x}::1/64")
        # The peer keeps the port's 1500: a whole oversized frame the hardware
        # wrongly sent would reach it and be counted rather than dropped.
        peer = await lan_vlan_subif(topology, lan, parent=LAN_NIC, vid=vid)
        await _exec(target_agent, aiohttp_session, "ip", "link", "set", oif, "mtu", "1400")
        async with _daemon(target_agent, aiohttp_session, [TARGET_WAN_IF, oif]) as ctl:
            await ctl("add", TARGET_WAN_IF, source, group, oif)

            # A 1400-byte listener behind a 1500-byte ingress: software.
            row = await _state(target_agent, aiohttp_session, group, "refused-mtu")
            assert "listeners=- " in row, row
            result, _, _ = await _window(
                target_agent, aiohttp_session, lan, peer, family, group,
                {SMALL: 256, OVERSIZED: 1448}, "narrow")
            # Linux forwards what fits and neither fragments nor forwards what
            # does not: IPv6 gets Packet Too Big, IPv4 with DF is dropped.
            assert result["seen"].get(str(SMALL)) == list(range(COUNT)), result
            assert str(OVERSIZED) not in result["seen"], result
            route, _ = await mroute_line(target_agent, aiohttp_session, family, source, group)
            assert "offload" not in route, route

            # The listener grows to the ingress's size: the MTU change alone
            # re-derives the group into hardware, on the confirmations the
            # window above gathered -- or, after a commit since, on a few
            # more frames.
            await _exec(target_agent, aiohttp_session, "ip", "link", "set", oif, "mtu", "1500")
            await _state(target_agent, aiohttp_session, group, "installed", family=family)
            result, before, after = await _window(
                target_agent, aiohttp_session, lan, peer, family, group,
                {FULL: 1500}, "full-size")
            assert result["seen"].get(str(FULL)) == list(range(COUNT)), result
            assert result["lengths"] == [1500], result
            assert result["hops"] == [63], result
            assert _packets(after) - _packets(before) >= COUNT * 0.95, (before, after)
            route, _ = await mroute_line(target_agent, aiohttp_session, family, source, group)
            assert "offload" in route, route

            # And shrinking it again takes an installed group back out.
            await _exec(target_agent, aiohttp_session, "ip", "link", "set", oif, "mtu", "1400")
            await _state(target_agent, aiohttp_session, group, "refused-mtu")

            if family == 6:
                # The IPv6 MTU alone, which no device event reports: the
                # learner's periodic refresh has to find it.
                await _exec(target_agent, aiohttp_session, "ip", "link", "set", oif, "mtu", "1500")
                await _state(target_agent, aiohttp_session, group, "installed", family=family)
                # Slashes, because the device name has a dot in it: a dotted
                # key names net/ipv6/conf/eth3/324/mtu, which does not exist,
                # and not every sysctl says so in its exit code -- so the
                # value is read back as well.
                key = f"net/ipv6/conf/{oif}/mtu"
                await _exec(target_agent, aiohttp_session, "sysctl", "-w", f"{key}=1280")
                assert (await read(target_agent, aiohttp_session,
                                   f"/proc/sys/{key}")).strip() == "1280", key
                await _state(target_agent, aiohttp_session, group, "refused-mtu", timeout=15)
                result, _, _ = await _window(
                    target_agent, aiohttp_session, lan, peer, family, group,
                    {SMALL: 256, OVERSIZED: 1448}, "narrow-ipv6-mtu")
                assert result["seen"].get(str(SMALL)) == list(range(COUNT)), result
                assert str(OVERSIZED) not in result["seen"], result

            await ctl("remove", TARGET_WAN_IF, source, group)
    finally:
        await topology.teardown("A196 member MTU")
