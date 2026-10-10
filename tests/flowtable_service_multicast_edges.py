"""The multicast cases between the common ones.

  - a routed source moving to another input port, which changes the key;
  - two sources of one group, which are two keys;
  - the two learners handing one key between them as the topology changes;
  - the adapter reloaded under standing groups;
  - `ip -s mroute` across the group leaving and re-entering hardware;
  - a forward chain dropping a routed group toward one of its oifs.

Each case reads the same three things as the rest of the suite: every
sequence at every observer, the classifier's own count on the adapter's row,
and whether the CPU carried the stream at all.
"""
from __future__ import annotations

import asyncio
from contextlib import AsyncExitStack
import json
import os
import time

import pytest

from _mcast_helpers import arm_bridge_querier
from _mcast_windows import (COUNT, bridge_settings, delivered, dut_console, host, in_hardware, in_software, kernel_mroute, learn, mcast_rows, mdb, members, moved, mroute_row, packets, same, stream, streamed, summary)
from _topology import (LAN_NIC, TARGET_LAN_IF, TARGET_WAN_IF, VLAN_ID_PPPOE_WAN, TopologyStack,
                       dut_vlan_subif, lan_vlan_subif)
from _mroute_capture import payload
from _flowtable_rig import (HEALTH_BASELINE, command, console_command)
from _mcast_e2e import (wan_source_address)
from _mroute_capacity import (_daemon, _python)

MOVE_GROUP = {4: "239.9.9.1", 6: "ff1e::9:9:1"}
TWO_SOURCE_GROUP = {4: "239.9.9.2", 6: "ff1e::9:9:2"}
SECOND_SOURCE = {4: "198.18.165.2", 6: "fd00:165::2"}
HANDOFF_GROUP = {4: "239.9.9.3", 6: "ff1e::9:9:3"}
RELOAD_GROUP = {4: "239.9.9.4", 6: "ff1e::9:9:4"}
FOLD_GROUP = {4: "239.9.9.5", 6: "ff1e::9:9:5"}
RELOAD_FILTER_GROUP = {4: "239.9.9.6", 6: "ff1e::9:9:6"}
IDLE_GROUP = {4: "239.9.9.7", 6: "ff1e::9:9:7"}
FIREWALL_GROUP = {4: "239.9.9.8", 6: "ff1e::9:9:8"}
# The second oif of the firewall case, tagged on the LAN port; claimed in
# _topology.
FIREWALL_VID = 325
FIREWALL_TABLE = "ask_ft_mr_firewall"
# The bridge's group membership interval for the ageing case, in centiseconds
# as the bridge takes it: short enough to wait out, and not so short that a
# refresh or two could land either side of it.
IDLE_INTERVAL = 1500
WAN_VID = int(os.environ.get("ASK_MROUTE_WAN_VID", str(VLAN_ID_PPPOE_WAN)))
WAN_PEER = os.environ.get("ASK_MROUTE_WAN_IF", "askwan3900")
# A multicast policy rule that matches none of these streams: its presence
# alone refuses the whole family, which sends a standing group to software
# and back without touching its MFC entry.
POLICY = {4: "-4", 6: "-6"}
# A mark nothing sets, so the rule matches no packet. Not a source prefix:
# iproute2 6.13 parses an mrule's prefix in the IPMR family and refuses it.
POLICY_MARK = "0xa51f"
POLICY_PREF = "32011"
LOCAL = f"{TARGET_LAN_IF}/0"


def routed(state, group, source, *, inbound=TARGET_WAN_IF, listeners=frozenset({LOCAL})):
    row = mroute_row(state, group, source)
    return (bool(row) and row["state"] == "installed" and row["in"] == inbound and
            members(row, "listeners") == set(listeners))


def withdrawn(state, group, source):
    """Gone, or the negative entry smcrouted may leave after traffic asks for a
    withdrawn route: no listeners, and in software."""
    row = mroute_row(state, group, source)
    return row is None or (row["state"] == "refused-listener" and row["listeners"] == "-")


@pytest.mark.parametrize("family", [4, 6])
async def test_ingress_move(multicast_rig, family):
    """The source of a routed (S,G) moves from behind the WAN port to behind the LAN port.

    Adding a route for a new inbound interface makes ipmr find the (S,G)
    whatever its parent and rewrite the parent in place, which the learner sees
    as a replace. The port is part of the classifier key, so the hardware group
    must be deleted and added again on the new one: exactly one row, counting
    from nothing, and a stream still arriving the old way matching nothing."""
    r = multicast_rig
    group, source = MOVE_GROUP[family], wan_source_address(family)
    wan = f"{TARGET_WAN_IF}/{WAN_VID}"
    peer = json.loads(await _python(None, f"""
import subprocess
print(subprocess.check_output(['ip', '-j', '-d', 'link', 'show', 'dev', {WAN_PEER!r}], text=True))
"""))[0]
    assert "UP" in peer["flags"] and peer["linkinfo"]["info_data"]["id"] == WAN_VID, peer
    topology = TopologyStack()

    def row(state):
        return mroute_row(state, group, source)

    try:
        oif = await dut_vlan_subif(topology, r.target, r.session, parent=TARGET_WAN_IF, vid=WAN_VID,
                                   ipv4="198.18.164.253/30", ipv6=f"fd00:{WAN_VID:x}::1/64")
        async with _daemon(r.target, r.session, [TARGET_WAN_IF, TARGET_LAN_IF, oif]) as ctl:
            await ctl("add", TARGET_WAN_IF, source, group, TARGET_LAN_IF)
            # Carried once Linux has been seen forwarding it: a few frames first.
            first = await learn(r, [stream(family, group, hops=63)],
                                lambda s: routed(s, group, source), "installed on the WAN port")
            inbound = await r.window([stream(family, group, hops=63)], [(r.lan, {LAN_NIC: r.dut_lan_mac})],
                                     ingress=TARGET_WAN_IF, label=f"move-v{family}-wan")
            assert delivered(inbound, streamed(inbound, group), LAN_NIC)
            assert moved(inbound, row) == COUNT, summary(inbound["after"])
            in_hardware(inbound)

            await ctl("add", TARGET_LAN_IF, source, group, oif)
            # A new oif, confirmed from the stream as it now arrives.
            after = await learn(r, [stream(family, group, hops=63)],
                                lambda s: routed(s, group, source, inbound=TARGET_LAN_IF,
                                                 listeners={wan}),
                                "rekeyed on the LAN port", sender="lan")
            assert [g for g in after["mroute"] if same(g["group"], group)] == [row(after)], summary(after)
            assert after["mroute_installed"] == first["mroute_installed"], summary(after)
            # A new key is a new classifier entry: counted from nothing but
            # the tail of the burst that confirmed it, not the old entry's.
            assert packets(row(after)) < 8, summary(after)
            line, _, _ = await kernel_mroute(r, family, source, group, offloaded=True)
            assert line and f"Iif: {TARGET_LAN_IF}" in line and "offload" in line, line

            outbound = await r.window([stream(family, group, hops=63)], [(None, {WAN_PEER: r.dut_wan_mac})],
                                      ingress=TARGET_LAN_IF, sender="lan", label=f"move-v{family}-lan")
            assert delivered(outbound, streamed(outbound, group), WAN_PEER)
            assert moved(outbound, row) == COUNT, summary(outbound["after"])
            in_hardware(outbound)

            # The old way in is not a key any more: nothing replicates it.
            stale = await r.window([stream(family, group, hops=63)], [(r.lan, {LAN_NIC: r.dut_lan_mac})],
                                   ingress=TARGET_WAN_IF, label=f"move-v{family}-stale")
            assert not delivered(stale, streamed(stale, group), LAN_NIC)
            assert moved(stale, row) == 0, summary(stale["after"])
            in_software(stale)

            # smcrouted still holds its first route as configuration. Removing
            # the moved one takes the kernel's single entry, and the group.
            await ctl("remove", TARGET_LAN_IF, source, group)
            await r.settle(lambda s: row(s) is None and s["mroute_installed"] == r.initial["mroute_installed"],
                           "the moved route removed")
    finally:
        await topology.teardown("ingress move")


@pytest.mark.parametrize("family", [4, 6])
async def test_two_sources(multicast_rig, family):
    """Two sources of one group are two keys: counted apart, removed apart.

    They share the group's Ethernet multicast address, which each entry
    subscribes the ingress port's filter to; the survivor has to keep
    matching, on the same entry, after the other is deleted."""
    r = multicast_rig
    group = TWO_SOURCE_GROUP[family]
    first, second = wan_source_address(family), SECOND_SOURCE[family]
    observers = [(r.lan, {LAN_NIC: r.dut_lan_mac})]

    def row(source):
        return lambda state: mroute_row(state, group, source)

    def window(sources, label):
        return r.window([stream(family, group, hops=63, source=s) for s in sources], observers,
                        ingress=TARGET_WAN_IF, label=f"sources-v{family}-{label}")

    async with _daemon(r.target, r.session, [TARGET_WAN_IF, TARGET_LAN_IF]) as ctl:
        for source in (first, second):
            await ctl("add", TARGET_WAN_IF, source, group, TARGET_LAN_IF)
        both = await learn(r, [stream(family, group, hops=63, source=s) for s in (first, second)],
                           lambda s: routed(s, group, first) and routed(s, group, second),
                           "both sources installed")
        assert both["mroute_installed"] == r.initial["mroute_installed"] + 2, summary(both)

        together = await window([first, second], "together")
        for source in (first, second):
            assert delivered(together, streamed(together, group, source), LAN_NIC), source
            assert moved(together, row(source)) == COUNT, (source, summary(together["after"]))
        in_hardware(together, streams=2)
        for only, other in ((first, second), (second, first)):
            alone = await window([only], "alone")
            assert delivered(alone, streamed(alone, group, only), LAN_NIC)
            assert moved(alone, row(only)) == COUNT, summary(alone["after"])
            assert moved(alone, row(other)) == 0, summary(alone["after"])
            in_hardware(alone)

        await ctl("remove", TARGET_WAN_IF, first, group)
        survived = await r.settle(lambda s: mroute_row(s, group, first) is None and
                                  s["mroute_installed"] == r.initial["mroute_installed"] + 1,
                                  "the first source removed")
        assert routed(survived, group, second), summary(survived)
        # Both windows it was sent in, on the same entry, over what the
        # learning stream left on it once the route was in hardware.
        learned = packets(row(second)(together["before"]))
        assert packets(row(second)(survived)) == learned + 2 * COUNT, summary(survived)
        after = await window([first, second], "survivor")
        assert not delivered(after, streamed(after, group, first), LAN_NIC)
        assert delivered(after, streamed(after, group, second), LAN_NIC)
        assert moved(after, row(second)) == COUNT, summary(after["after"])
        in_software(after)
        assert after["stream_cpu"] < COUNT * 1.1, (after["stream_cpu"], after["cpu"], after["idle"])
        assert withdrawn(after["after"], group, first), summary(after["after"])

        await ctl("remove", TARGET_WAN_IF, second, group)
        final = await r.settle(lambda s: mroute_row(s, group, second) is None and
                               s["mroute_installed"] == r.initial["mroute_installed"],
                               "the second source removed")
    assert final["quarantine"] == 0, summary(final)
    assert final["mroute_install_errors"] == r.initial["mroute_install_errors"], summary(final)


async def management(r, bridge):
    """The DUT's management address and default gateway, wherever they are now."""
    for dev in (bridge, TARGET_WAN_IF):
        info = json.loads((await command(r.target, r.session, "ip", "-j", "-4", "addr", "show",
                                         "dev", dev, check=False))["stdout"] or "[]")
        address = next((f"{a['local']}/{a['prefixlen']}" for i in info for a in i["addr_info"]
                        if a["family"] == "inet"), None)
        if address:
            break
    routes = json.loads((await command(r.target, r.session, "ip", "-j", "route", "show", "default"))["stdout"])
    gateway = next((route["gateway"] for route in routes if route.get("dev") in (bridge, TARGET_WAN_IF)), None)
    assert address, "no management address on the WAN port or the bridge"
    return address, gateway


async def healthy_agent(r):
    for _ in range(40):
        try:
            if (await r.target.health(r.session)).get("ok"):
                return
        except Exception:
            pass
        await asyncio.sleep(0.5)
    pytest.fail("the agent did not answer after the WAN port moved")


async def dissolve(r, console, bridge, address, gateway):
    """Take the WAN-LAN bridge apart, leaving the WAN port its address.

    Over the console, because the management address is on the bridge until
    the moment it is not."""
    steps = [["ip", "link", "del", bridge], ["ip", "addr", "replace", address, "dev", TARGET_WAN_IF],
             ["ip", "link", "set", TARGET_WAN_IF, "up"], ["ip", "link", "set", TARGET_LAN_IF, "up"]]
    if gateway:
        steps.append(["ip", "route", "replace", "default", "via", gateway, "dev", TARGET_WAN_IF])
    for argv in steps:
        await console_command(console, *argv, timeout=30)
    await healthy_agent(r)


async def rebuild(r, console, bridge, address, gateway, mac):
    """Put the bridge back as its fixture built it, address and all."""
    steps = [["ip", "link", "add", "name", bridge, "type", "bridge", "mcast_snooping", "1", "mcast_querier", "1",
              "mcast_igmp_version", "3", "mcast_mld_version", "2"],
             ["ip", "link", "set", bridge, "address", mac], ["ip", "link", "set", bridge, "up"],
             ["ip", "addr", "del", address, "dev", TARGET_WAN_IF],
             ["ip", "link", "set", TARGET_WAN_IF, "master", bridge],
             ["ip", "link", "set", TARGET_LAN_IF, "master", bridge],
             ["ip", "link", "set", TARGET_WAN_IF, "up"], ["ip", "link", "set", TARGET_LAN_IF, "up"],
             ["ip", "addr", "add", address, "dev", bridge]]
    if gateway:
        steps.append(["ip", "route", "replace", "default", "via", gateway, "dev", bridge])
    for argv in steps:
        await console_command(console, *argv, timeout=30)
    await healthy_agent(r)
    await asyncio.sleep(3)  # the querier's startup queries
    # A new bridge floods everything until its querier counts, and the
    # bridged learner carries nothing the bridge floods.
    await arm_bridge_querier(lambda *argv: command(r.target, r.session, *argv), bridge)


@pytest.mark.parametrize("family", [4, 6])
async def test_learner_handoff(multicast_rig, mcast_bridge, family):
    """One (S,G), claimed in turn by each learner as the other lets it go.

    A bridged group needs both ports in the bridge, since one is its ingress;
    a routed group refuses an ingress that is a bridge port. So on this rig
    the two can only meet while the WAN port changes sides, and that is when
    the key has to change hands: the one that loses its topology gives the key
    back, and the other -- which may have been told `refused-contested` a
    moment earlier -- must end up carrying it rather than stay refused."""
    r = multicast_rig
    bridge = mcast_bridge
    group, source = HANDOFF_GROUP[family], wan_source_address(family)
    address, gateway = await management(r, bridge)
    bridged = [(r.lan, {LAN_NIC: None})]
    routed_observers = [(r.lan, {LAN_NIC: r.dut_lan_mac})]

    def bridged_row(state):
        rows = mcast_rows(state, group)
        assert len(rows) <= 1, rows
        return rows[0] if rows else None

    def bridged_carries(state):
        row = bridged_row(state)
        return (bool(row) and row["state"] == "installed" and row["in"] == TARGET_WAN_IF and
                same(row["src"], source) and members(row, "ports") == {LOCAL})

    def routed_row(state):
        return mroute_row(state, group, source)

    def refused_ingress(state):
        row = routed_row(state)
        return bool(row) and row["state"] == "refused-ingress" and row["listeners"] == "-"

    console = await dut_console("mcast-handoff")
    try:
        async with _daemon(r.target, r.session, [TARGET_WAN_IF, TARGET_LAN_IF]) as ctl:
            await mdb(r, bridge, group, add=True)
            await learn(r, [stream(family, group, hops=64)], bridged_carries, "the bridged group")
            await ctl("add", TARGET_WAN_IF, source, group, TARGET_LAN_IF)
            held = await r.settle(lambda s: refused_ingress(s) and bridged_carries(s),
                                  "the bridged learner holding the key")
            assert held["mroute_installed"] == r.initial["mroute_installed"], summary(held)
            first = await r.window([stream(family, group, hops=64)], bridged, ingress=TARGET_WAN_IF,
                                   label=f"handoff-v{family}-bridged")
            assert delivered(first, streamed(first, group), LAN_NIC)
            assert moved(first, bridged_row) == COUNT, summary(first["after"])
            in_hardware(first)

            # The bridge goes, its group with it, and the key is handed back.
            await dissolve(r, console, bridge, address, gateway)
            # The port plain again: ipmr sees the stream on it, and the
            # routed group is carried once it has been seen forwarding it.
            taken = await learn(r, [stream(family, group, hops=63)],
                                lambda s: routed(s, group, source) and not mcast_rows(s, group) and
                                s["mcast_installed"] == r.initial["mcast_installed"],
                                "the routed learner taking the key")
            assert taken["mroute_installed"] == r.initial["mroute_installed"] + 1, summary(taken)
            second = await r.window([stream(family, group, hops=63)], routed_observers,
                                    ingress=TARGET_WAN_IF, label=f"handoff-v{family}-routed")
            assert delivered(second, streamed(second, group), LAN_NIC)
            assert moved(second, routed_row) == COUNT, summary(second["after"])
            in_hardware(second)

            # And back: the WAN port becomes a bridge port, the routed group
            # can no longer be keyed on it, and the membership takes over.
            await rebuild(r, console, bridge, address, gateway, r.dut_wan_mac)
            await mdb(r, bridge, group, add=True)
            await learn(r, [stream(family, group, hops=64)], bridged_carries, "the bridged group again")
            back = await r.settle(lambda s: refused_ingress(s) and bridged_carries(s),
                                  "the bridged learner holding the key again")
            assert back["mroute_installed"] == r.initial["mroute_installed"], summary(back)
            third = await r.window([stream(family, group, hops=64)], bridged, ingress=TARGET_WAN_IF,
                                   label=f"handoff-v{family}-bridged-again")
            assert delivered(third, streamed(third, group), LAN_NIC)
            assert moved(third, bridged_row) == COUNT, summary(third["after"])
            in_hardware(third)

            await ctl("remove", TARGET_WAN_IF, source, group)
            await mdb(r, bridge, group, add=False)
            final = await r.settle(lambda s: routed_row(s) is None and not mcast_rows(s, group),
                                   "both learners' records gone")
    finally:
        console.close()
    assert final["quarantine"] == 0, summary(final)
    for counter in ("mcast_install_errors", "mroute_install_errors"):
        assert final[counter] == r.initial[counter], (counter, summary(final))


async def reload_adapter(r, label, standing, during):
    """rmmod and modprobe the adapter with groups standing; CDX stays.

    `during` runs while the adapter is out, and the whole path must keep
    forwarding in software: what the adapter owned was only the acceleration."""
    console = await dut_console(label)
    unloaded = False
    try:
        await console_command(console, "rmmod", "ask_flowtable", timeout=30)
        unloaded = True
        for path in ("/sys/module/ask_flowtable", "/proc/cdx_flowtable"):
            assert (await console_command(console, "test", "-e", path, check=False))["rc"] == 1, path
        await during()
        await console_command(console, "modprobe", "ask_flowtable", timeout=30)
        unloaded = False
        loaded = await r.proc()
        assert loaded["fatal"] == loaded["quarantine"] == 0, summary(loaded)
        # The reload restarted the error count; health is measured from it.
        HEALTH_BASELINE["errors"] = loaded["errors"]
    finally:
        try:
            if unloaded:
                await console_command(console, "modprobe", "ask_flowtable", timeout=30, check=False)
        finally:
            console.close()
    return await standing()


@pytest.mark.parametrize("family", [4, 6])
async def test_reload_routed(multicast_rig, family):
    """The MFC outlives the adapter; registering again replays it."""
    r = multicast_rig
    group, source = RELOAD_GROUP[family], wan_source_address(family)
    observers = [(r.lan, {LAN_NIC: r.dut_lan_mac})]

    def row(state):
        return mroute_row(state, group, source)

    async with _daemon(r.target, r.session, [TARGET_WAN_IF, TARGET_LAN_IF]) as ctl:
        await ctl("add", TARGET_WAN_IF, source, group, TARGET_LAN_IF)
        await learn(r, [stream(family, group, hops=63)], lambda s: routed(s, group, source),
                    "installed before the reload")
        before = await r.window([stream(family, group, hops=63)], observers, ingress=TARGET_WAN_IF,
                                label=f"reload-routed-v{family}-before")
        assert delivered(before, streamed(before, group), LAN_NIC)
        in_hardware(before)

        async def unloaded():
            out = await r.window([stream(family, group, hops=63)], observers, ingress=TARGET_WAN_IF,
                                 label=f"reload-routed-v{family}-unloaded", adapter=False)
            assert delivered(out, streamed(out, group), LAN_NIC)
            in_software(out)
            line, _, _ = await kernel_mroute(r, family, source, group, offloaded=False, fold=False)
            assert line and "offload" not in line, line

        async def standing():
            # Confirmations went with the module: seen afresh.
            return await learn(r, [stream(family, group, hops=63)],
                               lambda s: routed(s, group, source), "relearned after the reload")

        relearned = await reload_adapter(r, f"mcast-reload-routed-v{family}", standing, unloaded)
        assert len([g for g in relearned["mroute"] if same(g["group"], group)]) == 1, summary(relearned)
        assert relearned["mroute_installed"] == r.initial["mroute_installed"] + 1, summary(relearned)
        after = await r.window([stream(family, group, hops=63)], observers, ingress=TARGET_WAN_IF,
                               label=f"reload-routed-v{family}-after")
        assert delivered(after, streamed(after, group), LAN_NIC)
        assert moved(after, row) == COUNT, summary(after["after"])
        in_hardware(after)
        line, _, _ = await kernel_mroute(r, family, source, group, offloaded=True)
        assert line and "offload" in line, line
        await ctl("remove", TARGET_WAN_IF, source, group)
        final = await r.settle(lambda s: row(s) is None and s["mroute_installed"] == r.initial["mroute_installed"],
                               "removed after the reload")
    assert final["quarantine"] == 0, summary(final)


@pytest.mark.parametrize("family", [4, 6])
async def test_counter_fold(multicast_rig, family):
    """`ip -s mroute` counts every packet of an entry once, whoever forwarded it.

    ipmr counts what the CPU forwards into the MFC entry itself; the adapter
    adds what the classifier matched. A group goes to software and back while
    its entry stands -- here a policy rule that matches nothing refuses the
    whole family -- and across all of it the entry's count must be the
    software count plus the hardware count, never less than it was. A
    daemon's SIOCGETSGCNT reads the same numbers and prunes on them."""
    r = multicast_rig
    group, source = FOLD_GROUP[family], wan_source_address(family)
    flag = POLICY[family]
    size = (20 if family == 4 else 40) + 8 + len(payload("00" * 16, 0))
    observers = [(r.lan, {LAN_NIC: r.dut_lan_mac})]
    samples = []

    def row(state):
        return mroute_row(state, group, source)

    def refused(state):
        current = row(state)
        return bool(current) and current["state"] == "refused-policy"

    async def policy(op, check=True):
        return await command(r.target, r.session, "ip", flag, "mrule", op, "fwmark", POLICY_MARK,
                             "lookup", "253", "pref", POLICY_PREF, check=check)

    async def counted(expected, label, offloaded):
        line, count, size_total = await kernel_mroute(r, family, source, group, offloaded=offloaded)
        samples.append({"label": label, "packets": count, "bytes": size_total, "line": line})
        r.record(f"fold-v{family}", samples)
        assert line and ("offload" in line) == offloaded, samples
        assert all(a["packets"] <= b["packets"] and a["bytes"] <= b["bytes"]
                   for a, b in zip(samples, samples[1:])), ("went backwards", samples)
        assert (count, size_total) == (expected, expected * size), (label, samples)

    async def window(label, hardware):
        result = await r.window([stream(family, group, hops=63)], observers, ingress=TARGET_WAN_IF,
                                label=f"fold-v{family}-{label}")
        assert delivered(result, streamed(result, group), LAN_NIC)
        (in_hardware if hardware else in_software)(result)
        assert moved(result, row) == (COUNT if hardware else 0), summary(result["after"])

    rules = json.loads((await command(r.target, r.session, "ip", "-j", flag, "mrule", "show"))["stdout"] or "[]")
    assert not any(rule.get("priority") == int(POLICY_PREF) for rule in rules), rules
    ruled = False
    try:
        async with _daemon(r.target, r.session, [TARGET_WAN_IF, TARGET_LAN_IF]) as ctl:
            await policy("add")
            ruled = True
            await ctl("add", TARGET_WAN_IF, source, group, TARGET_LAN_IF)
            # The software window is what confirms the group for when the
            # rule goes, with no traffic of its own: it has to fall after the
            # ruleset has settled -- a commit the learner first noticed with
            # this group, such as the boot service's table going, is timed
            # from here.
            await r.settle(lambda s: refused(s) and s["mroute_ruleset_settled"] == 1,
                           "refused while the policy rule stands")
            await window("software", hardware=False)
            await counted(COUNT, "software", offloaded=False)

            await policy("del")
            ruled = False
            await r.settle(lambda s: routed(s, group, source), "installed once the rule is gone")
            await counted(COUNT, "installed", offloaded=True)
            await window("hardware", hardware=True)
            await counted(2 * COUNT, "hardware", offloaded=True)

            await policy("add")
            ruled = True
            await r.settle(refused, "back to software")
            await counted(2 * COUNT, "withdrawn", offloaded=False)
            await window("software-again", hardware=False)
            await counted(3 * COUNT, "software-again", offloaded=False)

            await policy("del")
            ruled = False
            await r.settle(lambda s: routed(s, group, source), "installed again")
            await counted(3 * COUNT, "reinstalled", offloaded=True)
            await window("hardware-again", hardware=True)
            await counted(4 * COUNT, "hardware-again", offloaded=True)

            await ctl("remove", TARGET_WAN_IF, source, group)
            await r.settle(lambda s: row(s) is None, "removed")
    finally:
        if ruled:
            await policy("del", check=False)


@pytest.mark.parametrize("family", [4, 6])
async def test_reload_bridged(multicast_rig, mcast_bridge, family):
    """The MDB outlives the adapter; its standing memberships must come back.

    Registering on the switchdev chain replays nothing, and a membership that
    stands is never announced again -- a refreshing report finds its port
    group and only restarts a timer. So whatever brings the groups back has
    to be the adapter's own doing at load."""
    r = multicast_rig
    bridge = mcast_bridge
    group, source = RELOAD_GROUP[family], wan_source_address(family)
    observers = [(r.lan, {LAN_NIC: None})]

    def row(state):
        rows = mcast_rows(state, group)
        assert len(rows) <= 1, rows
        return rows[0] if rows else None

    def carried(state):
        current = row(state)
        return (bool(current) and current["state"] == "installed" and same(current["src"], source)
                and members(current, "ports") == {LOCAL})

    await mdb(r, bridge, group, add=True)
    await learn(r, [stream(family, group, hops=64)], carried, "installed before the reload")
    before = await r.window([stream(family, group, hops=64)], observers, ingress=TARGET_WAN_IF,
                            label=f"reload-bridged-v{family}-before")
    assert delivered(before, streamed(before, group), LAN_NIC)
    in_hardware(before)

    async def unloaded():
        out = await r.window([stream(family, group, hops=64)], observers, ingress=TARGET_WAN_IF,
                             label=f"reload-bridged-v{family}-unloaded", adapter=False)
        assert delivered(out, streamed(out, group), LAN_NIC)
        in_software(out)

    async def standing():
        # Traffic is offered throughout, so a learner waiting for a source
        # gets one; what it cannot get from traffic is the membership.
        return await learn(r, [stream(family, group, hops=64)], carried,
                           "the membership standing across the reload")

    relearned = await reload_adapter(r, f"mcast-reload-bridged-v{family}", standing, unloaded)
    assert len([g for g in relearned["mcast"] if same(g["group"], group)]) == 1, summary(relearned)
    after = await r.window([stream(family, group, hops=64)], observers, ingress=TARGET_WAN_IF,
                           label=f"reload-bridged-v{family}-after")
    assert delivered(after, streamed(after, group), LAN_NIC)
    assert moved(after, row) == COUNT, summary(after["after"])
    in_hardware(after)
    await mdb(r, bridge, group, add=False)
    final = await r.settle(lambda s: row(s) is None and s["mcast_installed"] == r.initial["mcast_installed"],
                           "removed after the reload")
    assert final["quarantine"] == 0, summary(final)


@pytest.mark.parametrize("family", [4, 6])
async def test_reload_bridged_blocked_source(multicast_rig, mcast_bridge,
                                                                        family):
    """A blocked source stands across the reload, and stays blocked after it.

    An IGMPv3 or MLDv2 host on the LAN port listens to any source but one.
    The bridge holds that source's (S,G) port group blocked. The replay at
    load has to say so: replayed as an ordinary member, the blocked source
    would be carried to the very port that refused it, with the frame no
    longer reaching the bridge to correct it. The allowed source comes back
    in hardware; the blocked one never reaches the port, before, during or
    after."""
    r = multicast_rig
    bridge = mcast_bridge
    group, blocked, allowed = RELOAD_FILTER_GROUP[family], wan_source_address(family), SECOND_SOURCE[family]
    port = f"{TARGET_LAN_IF}/0"
    observers = [(r.lan, {LAN_NIC: None})]
    streams = [stream(family, group, hops=64, source=s) for s in (blocked, allowed)]

    def row(state, source):
        rows = [g for g in mcast_rows(state, group) if same(g["src"], source)]
        assert len(rows) <= 1, rows
        return rows[0] if rows else None

    def settled(state):
        kept, refused = row(state, allowed), row(state, blocked)
        return (bool(kept) and kept["state"] == "installed" and members(kept, "ports") == {port}
                and bool(refused) and refused["state"] == "discarding"
                and refused["ports"] == "-")

    async def window(label, adapter=True):
        result = await r.window([stream(family, group, hops=64, source=s) for s in (blocked, allowed)],
                                observers, ingress=TARGET_WAN_IF, adapter=adapter,
                                label=f"reload-blocked-v{family}-{label}")
        assert delivered(result, streamed(result, group, allowed), LAN_NIC)
        assert not delivered(result, streamed(result, group, blocked), LAN_NIC)
        return result

    async with AsyncExitStack() as stack:
        await stack.enter_async_context(bridge_settings(
            r, bridge, igmp_version=3, mld_version=2, last_member_count=2,
            last_member_interval=100))
        member = await stack.enter_async_context(host(
            r.lan, family=family, group=group, iface=LAN_NIC, mode="asm",
            version=3 if family == 4 else 2, source=blocked))
        await member.do("block")
        await asyncio.sleep(3.5)   # the unanswered group-and-source queries
        # The bridge itself has to hold the source blocked on the port, or
        # the host never sent the BLOCK and there is nothing to carry across.
        listed = json.loads((await command(r.target, r.session, "bridge", "-j", "-d", "mdb", "show",
                                           "dev", bridge))["stdout"] or "[]")
        entries = [e for block in listed for e in block.get("mdb", [])]
        assert any(e.get("port") == TARGET_LAN_IF and same(e["grp"], group) and e.get("src")
                   and same(e["src"], blocked) and "blocked" in e.get("flags", [])
                   for e in entries), ("the bridge holds no blocked source", entries)
        await learn(r, streams, settled, "the allowed source carried, the blocked one not")
        before = await window("before")
        assert moved(before, lambda s: row(s, allowed)) == COUNT, summary(before["after"])
        # The blocked source is dropped where it is matched, not on the CPU.
        assert moved(before, lambda s: row(s, blocked)) == COUNT, summary(before["after"])
        in_hardware(before, streams=2)

        async def unloaded():
            out = await window("unloaded", adapter=False)
            in_software(out, streams=2)

        async def standing():
            return await learn(r, streams, settled, "the blocked source standing across the reload")

        relearned = await reload_adapter(r, f"mcast-reload-blocked-v{family}", standing, unloaded)
        # The allowed source carried and the blocked one discarding.
        assert relearned["mcast_installed"] == r.initial["mcast_installed"] + 2, summary(relearned)
        after = await window("after")
        assert moved(after, lambda s: row(s, allowed)) == COUNT, summary(after["after"])
        assert moved(after, lambda s: row(s, blocked)) == COUNT, summary(after["after"])
        in_hardware(after, streams=2)
    final = await r.settle(lambda s: not mcast_rows(s, group) and
                           s["mcast_installed"] == r.initial["mcast_installed"],
                           "removed after the reload", timeout=20)
    assert final["quarantine"] == 0, summary(final)


@pytest.mark.parametrize("family", [4, 6])
async def test_bridged_idle_flow_ages_out(multicast_rig, mcast_bridge, family):
    """A bridged stream that stops leaves hardware on the bridge's own clock.

    Its membership stands -- a static one here, which the bridge never ages
    -- but its entry counts nothing, and after the bridge's group membership
    interval, lowered for the case and put back, the flow goes: the entry and
    its place among the group's flows are for a source that is sending. The
    source resuming is a flow again from its next frames, in hardware."""
    r = multicast_rig
    bridge = mcast_bridge
    group, source = IDLE_GROUP[family], wan_source_address(family)
    observers = [(r.lan, {LAN_NIC: None})]

    def row(state):
        rows = [g for g in mcast_rows(state, group) if same(g["src"], source)]
        assert len(rows) <= 1, rows
        return rows[0] if rows else None

    def carried(state):
        current = row(state)
        return (bool(current) and current["state"] == "installed"
                and members(current, "ports") == {LOCAL})

    async def live(label):
        result = await r.window([stream(family, group, hops=64)], observers, ingress=TARGET_WAN_IF,
                                label=f"idle-v{family}-{label}")
        assert delivered(result, streamed(result, group), LAN_NIC)
        assert moved(result, row) == COUNT, summary(result["after"])
        in_hardware(result)

    async with AsyncExitStack() as stack:
        await stack.enter_async_context(bridge_settings(r, bridge, membership_interval=IDLE_INTERVAL))
        await mdb(r, bridge, group, add=True)
        stack.push_async_callback(mdb, r, bridge, group, add=False)
        await learn(r, [stream(family, group, hops=64)], carried, "installed from traffic")
        await live("before")

        stopped = time.monotonic()
        aged = await r.settle(lambda s: row(s) is None and
                              s["mcast_installed"] == r.initial["mcast_installed"],
                              "the idle flow out of hardware", timeout=IDLE_INTERVAL / 100 + 25)
        # Not before the interval: the refresh reads the count five seconds
        # apart, so the entry was last seen counting at most that long after
        # the window ended.
        assert time.monotonic() - stopped >= IDLE_INTERVAL / 100 - 5, summary(aged)
        # The membership stands, waiting for a source again.
        assert [g["state"] for g in mcast_rows(aged, group)] == ["pending-source"], summary(aged)

        await learn(r, [stream(family, group, hops=64)], carried, "learned again when it resumes")
        await live("resumed")
    final = await r.settle(lambda s: not mcast_rows(s, group) and
                           s["mcast_installed"] == r.initial["mcast_installed"],
                           "removed", timeout=15)
    assert final["quarantine"] == 0, summary(final)
    assert final["mcast_install_errors"] == r.initial["mcast_install_errors"], summary(final)


@pytest.mark.parametrize("family", [4, 6])
async def test_routed_firewall(multicast_rig, family):
    """A forward chain dropping the group toward one oif, as fw4 does from WAN to LAN.

    Linux never forwards the stream there, so no copy of it is seen leaving
    by that oif, and the group waits in software whole: the dropped oif
    receives nothing and the other receives the stream from the CPU. The rule
    gone, the next copy confirms the oif and the group is carried to both.
    The rule back is an nftables commit, which takes every routed group back
    to software; this one cannot be confirmed toward the oif again, and that
    oif stops receiving."""
    r = multicast_rig
    group, source = FIREWALL_GROUP[family], wan_source_address(family)
    untagged, tagged = f"{TARGET_LAN_IF}/0", f"{TARGET_LAN_IF}/{FIREWALL_VID}"
    match = "ip daddr" if family == 4 else "ip6 daddr"
    topology = TopologyStack()
    dropping = False

    def row(state):
        return mroute_row(state, group, source)

    def carried(state):
        current = row(state)
        return (bool(current) and current["state"] == "installed" and
                members(current, "listeners") == {untagged, tagged})

    try:
        oif = await dut_vlan_subif(topology, r.target, r.session, parent=TARGET_LAN_IF,
                                   vid=FIREWALL_VID, ipv4="198.18.167.1/24",
                                   ipv6="fd00:167::1/64")
        peer = await lan_vlan_subif(topology, r.lan, parent=LAN_NIC, vid=FIREWALL_VID)
        observers = [(r.lan, {LAN_NIC: r.dut_lan_mac, peer: r.dut_lan_mac})]

        def held(state):
            current = row(state)
            return (bool(current) and current["state"] == "pending-confirm" and
                    current["unconfirmed"] == oif)

        async def drop():
            await command(r.target, r.session, "nft", f'''table inet {FIREWALL_TABLE} {{
 chain forward {{ type filter hook forward priority filter; policy accept;
  oifname "{oif}" {match} {group} drop
 }}
}}''')

        def window(label):
            return r.window([stream(family, group, hops=63)], observers, ingress=TARGET_WAN_IF,
                            label=f"firewall-v{family}-{label}")

        async with _daemon(r.target, r.session, [TARGET_WAN_IF, TARGET_LAN_IF, oif]) as ctl:
            # Owned before it exists: a cancellation after the commit still
            # takes it down.
            dropping = True
            await drop()
            await ctl("add", TARGET_WAN_IF, source, group, TARGET_LAN_IF, oif)
            await learn(r, [stream(family, group, hops=63)], held,
                        "confirmed toward the allowed oif only")
            first = await window("dropped")
            assert delivered(first, streamed(first, group), LAN_NIC)
            assert not delivered(first, streamed(first, group), peer)
            in_software(first)
            assert held(first["after"]), summary(first["after"])
            assert first["after"]["mroute_installed"] == r.initial["mroute_installed"], \
                summary(first["after"])

            await command(r.target, r.session, "nft", "delete", "table", "inet", FIREWALL_TABLE)
            dropping = False
            allowed = await learn(r, [stream(family, group, hops=63)], carried,
                                  "carried to both once allowed")
            second = await window("allowed")
            assert delivered(second, streamed(second, group), LAN_NIC)
            assert delivered(second, streamed(second, group), peer)
            in_hardware(second)
            assert moved(second, row) == COUNT, summary(second["after"])

            dropping = True
            await drop()
            # Withdrawn at once; confirmed again only once the ruleset has
            # stood still, which the window below then does toward the port.
            await r.settle(lambda s: s["mroute_ruleset_changes"] > allowed["mroute_ruleset_changes"]
                           and not carried(s) and s["mroute_ruleset_settled"] == 1 and
                           s["mroute_installed"] == r.initial["mroute_installed"],
                           "withdrawn by the commit")
            third = await window("dropped-again")
            assert delivered(third, streamed(third, group), LAN_NIC)
            assert not delivered(third, streamed(third, group), peer)
            in_software(third)
            assert held(third["after"]), summary(third["after"])

            await ctl("remove", TARGET_WAN_IF, source, group)
            final = await r.settle(lambda s: row(s) is None and
                                   s["mroute_installed"] == r.initial["mroute_installed"],
                                   "the route removed")
        assert final["quarantine"] == 0, summary(final)
        assert final["mroute_install_errors"] == r.initial["mroute_install_errors"], summary(final)
    finally:
        if dropping:
            await command(r.target, r.session, "nft", "delete", "table", "inet", FIREWALL_TABLE,
                          check=False)
        await topology.teardown("routed firewall")
