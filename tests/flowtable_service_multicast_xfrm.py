"""Routed multicast under an XFRM output policy.

Linux applies IPsec to a forwarded IPv4 multicast copy in one place: the
output route ipmr looks up for each oif, whose XFRM lookup can block the copy
or require a transform the copy then either takes or, with no state, is
dropped for. A hardware copy never takes that route. So an IPv4 group with any
oif a policy governs stays in software (refused-xfrm), and a policy added under
a group already carried takes it out of hardware -- with no nftables or MFC
change to wake anything else. ip6mr's output route takes no XFRM lookup, so
there the same policy governs nothing and the group stays carried: parity
either way.
"""
from __future__ import annotations

import asyncio

import pytest

from _mcast_windows import (COUNT, delivered, in_hardware, in_software, learn, members, mroute_row, stream, streamed, summary)
from _topology import LAN_NIC, TARGET_LAN_IF, TARGET_WAN_IF, TopologyStack, dut_vlan_subif, lan_vlan_subif
from _flowtable_rig import (command, read)
from _mcast_e2e import (wan_source_address)
from _mroute_capacity import (_daemon)

XFRM_GROUP = {4: "239.9.10.1", 6: "ff1e::9:10:1"}
# The governed oif, tagged on the LAN port; claimed in _topology.
XFRM_VID = 326
# A tunnel nobody has an SA for: a template naming it drops the copy.
TUNNEL = {4: ("198.18.168.1", "198.18.168.2"), 6: ("fd00:168::1", "fd00:168::2")}


def selector(family, group, oif):
    host = "32" if family == 4 else "128"
    return ["src", "0.0.0.0/0" if family == 4 else "::/0", "dst", f"{group}/{host}",
            "dev", oif, "dir", "out"]


def policy(family, group, oif, kind):
    if kind == "block":
        return selector(family, group, oif) + ["action", "block"]
    local, peer = TUNNEL[family]
    return selector(family, group, oif) + ["tmpl", "src", local, "dst", peer,
                                           "proto", "esp", "mode", "tunnel"]


async def xfrm_stat(r, name):
    for line in (await read(r.target, r.session, "/proc/net/xfrm_stat")).splitlines():
        key, _, value = line.partition(" ")
        if key == name:
            return int(value)
    raise AssertionError(f"{name} not in /proc/net/xfrm_stat")


@pytest.mark.parametrize("kind", ["block", "template"])
@pytest.mark.parametrize("family", [4, 6])
async def test_routed_follows_xfrm(multicast_rig, family, kind):
    """A policy on one oif of a carried group, then gone again.

    IPv4: the group leaves hardware for software the moment the policy lands,
    the governed oif receives nothing -- Linux blocks or blackholes its copy --
    and the other keeps receiving from the CPU; the policy gone, the group is
    carried to both again. IPv6: nothing changes, because Linux applies no
    XFRM to that copy either."""
    r = multicast_rig
    group, source = XFRM_GROUP[family], wan_source_address(family)
    untagged, tagged = f"{TARGET_LAN_IF}/0", f"{TARGET_LAN_IF}/{XFRM_VID}"
    governed = family == 4
    ipx = ["ip"] + (["-6"] if family == 6 else [])
    topology = TopologyStack()
    policed = False

    def row(state):
        return mroute_row(state, group, source)

    def carried(state):
        current = row(state)
        return (bool(current) and current["state"] == "installed" and
                members(current, "listeners") == {untagged, tagged})

    try:
        oif = await dut_vlan_subif(topology, r.target, r.session, parent=TARGET_LAN_IF,
                                   vid=XFRM_VID, ipv4="198.18.169.1/24", ipv6="fd00:169::1/64")
        peer = await lan_vlan_subif(topology, r.lan, parent=LAN_NIC, vid=XFRM_VID)
        observers = [(r.lan, {LAN_NIC: r.dut_lan_mac, peer: r.dut_lan_mac})]
        configs = [stream(family, group, hops=63)]

        def window(label):
            return r.window(configs, observers, ingress=TARGET_WAN_IF,
                            label=f"xfrm-{kind}-v{family}-{label}")

        async with _daemon(r.target, r.session, [TARGET_WAN_IF, TARGET_LAN_IF, oif]) as ctl:
            await ctl("add", TARGET_WAN_IF, source, group, TARGET_LAN_IF, oif)
            await learn(r, configs, carried, "carried to both")
            first = await window("carried")
            assert delivered(first, streamed(first, group), LAN_NIC)
            assert delivered(first, streamed(first, group), peer)
            in_hardware(first)

            before = await r.proc()
            blocks = await xfrm_stat(r, "XfrmOutPolBlock")
            policed = True
            await command(r.target, r.session, *ipx, "xfrm", "policy", "add",
                          *policy(family, group, oif, kind))
            if governed:
                # Nothing but the policy changed: no commit, no MFC event.
                refused = await r.settle(
                    lambda s: bool(row(s)) and row(s)["state"] == "refused-xfrm" and
                    s["mroute_installed"] == r.initial["mroute_installed"],
                    "out of hardware under the policy")
                assert refused["mroute_xfrm_changes"] > before["mroute_xfrm_changes"], summary(refused)
                assert refused["mroute_ruleset_changes"] == before["mroute_ruleset_changes"], \
                    summary(refused)
            else:
                # Every group is asked again; this one's answer does not move.
                await r.settle(lambda s: s["mroute_xfrm_changes"] > before["mroute_xfrm_changes"],
                               "the policy change seen")
                await asyncio.sleep(1)
            second = await window("policy")
            assert delivered(second, streamed(second, group), LAN_NIC)
            if governed:
                assert not delivered(second, streamed(second, group), peer)
                in_software(second)
                if kind == "block":
                    # Linux dropped every copy toward the oif for the policy.
                    assert await xfrm_stat(r, "XfrmOutPolBlock") - blocks >= COUNT
            else:
                assert delivered(second, streamed(second, group), peer)
                in_hardware(second)
                assert carried(second["after"]), summary(second["after"])

            await command(r.target, r.session, *ipx, "xfrm", "policy", "delete",
                          *selector(family, group, oif))
            policed = False
            await learn(r, configs, carried, "carried again")
            third = await window("lifted")
            assert delivered(third, streamed(third, group), LAN_NIC)
            assert delivered(third, streamed(third, group), peer)
            in_hardware(third)

            await ctl("remove", TARGET_WAN_IF, source, group)
            final = await r.settle(lambda s: row(s) is None and
                                   s["mroute_installed"] == r.initial["mroute_installed"],
                                   "the route removed")
        assert final["quarantine"] == 0, summary(final)
        assert final["mroute_install_errors"] == r.initial["mroute_install_errors"], summary(final)
    finally:
        if policed:
            await command(r.target, r.session, *ipx, "xfrm", "policy", "delete",
                          *selector(family, group, oif), check=False)
        await topology.teardown("routed xfrm")
