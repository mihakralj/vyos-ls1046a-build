"""Routed multicast under a ruleset that tells a group's streams apart by port.

A routed group is carried under its source and group alone -- the classifier
key stops at L3 -- and only once Linux has been seen forwarding a copy of it
to every oif. A copy on one UDP port says nothing of a stream on another, so a
rule that treats the ports differently keeps the group in software
(refused-ports) where Linux judges every packet: the port it drops receives
nothing, the one it forwards receives everything. A rule that reads ports but
cannot change the stream's fate -- fw4's ISAKMP accept ahead of the group's
own -- keeps nothing out of hardware. The rule gone, the group is carried with
every port again.

The same holds for what is not nftables. An iptables-legacy rule that splits
the ports keeps the group in software as refused-xtables: the kernel walks the
x_tables tables a copy crosses, and its own count of table changes has every
group asked again once the rule goes. A tc filter running in software where
the stream arrives or where a copy leaves keeps it there as refused-tc,
whatever the filter reads; nothing reports a filter's removal, so the group is
asked again at the next refresh. This image's own legacy ruleset, a legacy
rule that reads ports but cannot change the stream's fate, a chain that only
returns, and a hardware-only police on the uplink keep nothing out of
hardware.
"""
from __future__ import annotations

import pytest

from _mcast_windows import (COUNT, OTHER_PORT, PORT, delivered, dut_console, in_hardware, in_software, learn, members, moved, mroute_row, stream, streamed, summary)
from _topology import LAN_NIC, TARGET_LAN_IF, TARGET_WAN_IF, TopologyStack, dut_vlan_subif, lan_vlan_subif
from _flowtable_rig import (command, console_command, read)
from _mcast_e2e import (wan_source_address)
from _mroute_capacity import (_daemon)

PORTS_GROUP = {4: "239.9.11.1", 6: "ff1e::9:11:1"}
PORTS_TABLE = "ask_ft_mr_ports"
# The second stream's port: the one the splitting rules single out.
OTHER = OTHER_PORT
# The second oif, tagged on the LAN port, which the oif case drops the other
# port toward; claimed in _topology.
PORTS_VID = 327
# What each case's rule does: which (port, oif) pairs Linux forwards.
KINDS = ["forward-drop", "prerouting-allow", "oif-drop", "control"]


def ruleset(family, group, kind, oif):
    match = "ip daddr" if family == 4 else "ip6 daddr"
    if kind == "forward-drop":
        chain = f'''chain forward {{ type filter hook forward priority filter; policy accept;
  {match} {group} udp dport {OTHER} drop
 }}'''
    elif kind == "prerouting-allow":
        chain = f'''chain prerouting {{ type filter hook prerouting priority filter; policy accept;
  {match} {group} udp dport {PORT} accept
  {match} {group} drop
 }}'''
    elif kind == "oif-drop":
        chain = f'''chain forward {{ type filter hook forward priority filter; policy accept;
  oifname "{oif}" {match} {group} udp dport {OTHER} drop
 }}'''
    else:
        # fw4's shape: established traffic first, a port rule for another
        # stream, the group's own accept, and every other multicast stream
        # dropped. Every path this group can take accepts, whatever its ports.
        chain = f'''chain forward {{ type filter hook forward priority filter; policy accept;
  ct state established,related accept
  meta l4proto udp udp dport 500 accept
  {match} {group} accept
  meta pkttype multicast drop
 }}'''
    return f"table inet {PORTS_TABLE} {{\n {chain}\n}}"


# The iptables-legacy and tc cases: what the group's row says while the rule
# stands, and whether Linux then forwards the second port to the untagged oif
# and to the tagged one. The first port is forwarded everywhere throughout.
XT_KINDS = {
    "legacy-forward-drop": ("refused-xtables", False, False),
    "legacy-oif-drop": ("refused-xtables", True, False),
    "tc-ingress-drop": ("refused-tc", False, False),
    "tc-egress-drop": ("refused-tc", True, False),
    "legacy-control": ("installed", True, True),
    "tc-hw-police": ("installed", True, True),
}
# The user chain the legacy control jumps to.
LEGACY_CHAIN = "ask_mr_ports"
# The control's hardware-only meter, well above anything a window sends.
POLICE = ["rate", "2000mbit", "burst", "4m", "conform-exceed", "drop"]


def legacy_rules(group, kind, oif):
    """The arguments that put a legacy kind's rules in place, and those that
    take them out again, each list in the order it runs."""
    if kind != "legacy-control":
        drop = ["-d", group, "-p", "udp", "--dport", str(OTHER)]
        if kind == "legacy-oif-drop":
            drop += ["-o", oif]
        return ([["-I", "FORWARD", "1", *drop, "-j", "DROP"]],
                [["-D", "FORWARD", *drop, "-j", "DROP"]])
    # A rule that reads ports but accepts, ahead of a jump to a chain that
    # only returns: the walk follows both, and neither can split the group.
    isakmp = ["-p", "udp", "--dport", "500", "-j", "ACCEPT"]
    return ([["-N", LEGACY_CHAIN], ["-A", LEGACY_CHAIN, "-d", group, "-j", "RETURN"],
             ["-I", "FORWARD", "1", "-j", LEGACY_CHAIN], ["-I", "FORWARD", "1", *isakmp]],
            [["-D", "FORWARD", *isakmp], ["-D", "FORWARD", "-j", LEGACY_CHAIN],
             ["-F", LEGACY_CHAIN], ["-X", LEGACY_CHAIN]])


def tc_filter(family, kind, oif):
    """The device, the clsact direction and the filter a tc kind adds there.

    The drops skip hardware, so they run in software alone; the police skips
    software, so it runs in the port's meter alone."""
    if kind == "tc-hw-police":
        return TARGET_WAN_IF, "ingress", ["matchall", "skip_sw", "action", "police", *POLICE]
    drop = ["protocol", "ip" if family == 4 else "ipv6", "flower", "skip_hw",
            "ip_proto", "udp", "dst_port", str(OTHER), "action", "drop"]
    if kind == "tc-ingress-drop":
        return TARGET_WAN_IF, "ingress", drop
    return oif, "egress", drop


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("family", [4, 6])
async def test_routed_follows_ports(multicast_rig, family, kind):
    """Two streams of one (S,G) on two ports, to two oifs, under a rule that may split them.

    Splitting rules: the group is confirmed by the port Linux forwards and
    then refused, so the whole group stays in software and every (port, oif)
    Linux drops receives nothing; the rule gone, both ports are carried to
    both oifs in hardware. The control: carried from the start, both ports
    delivered to both oifs by the classifier."""
    r = multicast_rig
    group, source = PORTS_GROUP[family], wan_source_address(family)
    untagged, tagged = f"{TARGET_LAN_IF}/0", f"{TARGET_LAN_IF}/{PORTS_VID}"
    splits = kind != "control"
    topology = TopologyStack()
    tabled = False

    def row(state):
        return mroute_row(state, group, source)

    def carried(state):
        current = row(state)
        return (bool(current) and current["state"] == "installed" and
                members(current, "listeners") == {untagged, tagged})

    def refused(state):
        current = row(state)
        return bool(current) and current["state"] == "refused-ports"

    def forwarded(port, iface):
        """Whether Linux forwards `port` to the oif `iface` observes."""
        if port == PORT or kind == "control":
            return True
        return kind == "oif-drop" and iface == LAN_NIC

    configs = [stream(family, group, hops=63), stream(family, group, hops=63, port=OTHER)]

    try:
        oif = await dut_vlan_subif(topology, r.target, r.session, parent=TARGET_LAN_IF,
                                   vid=PORTS_VID, ipv4="198.18.170.1/24", ipv6="fd00:170::1/64")
        peer = await lan_vlan_subif(topology, r.lan, parent=LAN_NIC, vid=PORTS_VID)
        observers = [(r.lan, {LAN_NIC: r.dut_lan_mac, peer: r.dut_lan_mac})]

        async def window(label, ruled):
            result = await r.window(configs, observers, ingress=TARGET_WAN_IF,
                                    label=f"ports-{kind}-v{family}-{label}")
            for port in (PORT, OTHER):
                config = streamed(result, group, port=port)
                for iface in (LAN_NIC, peer):
                    expected = forwarded(port, iface) if ruled else True
                    assert delivered(result, config, iface) == expected, (port, iface, label)
            return result

        async with _daemon(r.target, r.session, [TARGET_WAN_IF, TARGET_LAN_IF, oif]) as ctl:
            # Owned before it exists: a cancellation after the commit still
            # takes it down.
            tabled = True
            await command(r.target, r.session, "nft", ruleset(family, group, kind, oif))
            await ctl("add", TARGET_WAN_IF, source, group, TARGET_LAN_IF, oif)
            await learn(r, configs[:1], refused if splits else carried,
                        "refused for its ports" if splits else "carried whatever its ports")
            first = await window("ruled", ruled=True)
            if splits:
                in_software(first, streams=2)
                assert refused(first["after"]), summary(first["after"])
                assert first["after"]["mroute_installed"] == r.initial["mroute_installed"], \
                    summary(first["after"])
            else:
                in_hardware(first, streams=2)
                assert moved(first, row) == 2 * COUNT, summary(first["after"])

            before = await r.proc()
            await command(r.target, r.session, "nft", "delete", "table", "inet", PORTS_TABLE)
            tabled = False
            # A commit, which takes every confirmation back once the learner
            # has seen it: a group carried under the rule would otherwise
            # read as carried again before it was taken out.
            await r.settle(lambda s: s["mroute_ruleset_changes"] > before["mroute_ruleset_changes"],
                           "the rule's removal seen")
            await learn(r, configs[:1], carried, "carried once the rule is gone")
            second = await window("unruled", ruled=False)
            in_hardware(second, streams=2)
            assert moved(second, row) == 2 * COUNT, summary(second["after"])

            await ctl("remove", TARGET_WAN_IF, source, group)
            final = await r.settle(lambda s: row(s) is None and
                                   s["mroute_installed"] == r.initial["mroute_installed"],
                                   "the route removed")
        assert final["quarantine"] == 0, summary(final)
        assert final["mroute_install_errors"] == r.initial["mroute_install_errors"], summary(final)
        assert final["mroute_port_probe_errors"] == r.initial["mroute_port_probe_errors"], \
            summary(final)
    finally:
        if tabled:
            await command(r.target, r.session, "nft", "delete", "table", "inet", PORTS_TABLE,
                          check=False)
        await topology.teardown("routed ports")


@pytest.mark.parametrize("kind", list(XT_KINDS))
@pytest.mark.parametrize("family", [4, 6])
async def test_routed_follows_legacy_and_tc(multicast_rig, family, kind):
    """Two streams of one (S,G) on two ports, to two oifs, under an
    iptables-legacy rule or a tc filter that may split them.

    A legacy rule that splits the ports is found by the kernel's walk of the
    x_tables tables once the group is confirmed, and keeps it in software
    (refused-xtables); a tc filter running in software on the input or an
    oif keeps it there whatever was confirmed (refused-tc). Either way every
    (port, oif) Linux drops receives nothing. The rule gone, both ports are
    carried to both oifs in hardware. The controls -- the image's own legacy
    ruleset with a port rule and a returning chain added to it, and a police
    that runs in the port's meter alone -- are carried from the start."""
    r = multicast_rig
    group, source = PORTS_GROUP[family], wan_source_address(family)
    untagged, tagged = f"{TARGET_LAN_IF}/0", f"{TARGET_LAN_IF}/{PORTS_VID}"
    ruled_state, other_untagged, other_tagged = XT_KINDS[kind]
    splits = ruled_state != "installed"
    legacy = kind.startswith("legacy-")
    iptables = "iptables" if family == 4 else "ip6tables"
    topology = TopologyStack()
    console = None
    placed = False
    oif = None

    def row(state):
        return mroute_row(state, group, source)

    def carried(state):
        current = row(state)
        return (bool(current) and current["state"] == "installed" and
                members(current, "listeners") == {untagged, tagged})

    def refused(state):
        current = row(state)
        return bool(current) and current["state"] == ruled_state

    def forwarded(port, iface):
        """Whether Linux forwards `port` to the oif `iface` observes."""
        if port == PORT:
            return True
        return other_untagged if iface == LAN_NIC else other_tagged

    async def tc(*argv, check=True):
        """`tc` runs on the console: argv[0] is the whole of the agent's gate,
        and tc can start any program (`tc exec bpf import ... run`), so the
        agent does not list it."""
        return await console_command(console, "tc", *argv, check=check, timeout=30)

    async def place():
        nonlocal placed
        # Owned before it exists: a failure halfway still takes it down.
        placed = True
        if legacy:
            for argv in legacy_rules(group, kind, oif)[0]:
                await command(r.target, r.session, iptables, *argv)
            return
        dev, direction, spec = tc_filter(family, kind, oif)
        # A killed run's qdisc, if any, goes with whatever it held.
        await tc("qdisc", "del", "dev", dev, "clsact", check=False)
        await tc("qdisc", "add", "dev", dev, "clsact")
        await tc("filter", "add", "dev", dev, direction, *spec)
        if kind == "tc-hw-police":
            # In hardware or nowhere: the kernel agreeing that the port took
            # it, and that software never runs it.
            shown = await tc("filter", "show", "dev", dev, direction)
            assert "in_hw" in shown["stdout"] and "skip_sw" in shown["stdout"], shown["stdout"]

    async def lift(check=True):
        nonlocal placed
        if legacy:
            for argv in legacy_rules(group, kind, oif)[1]:
                await command(r.target, r.session, iptables, *argv, check=check)
        else:
            dev = tc_filter(family, kind, oif)[0]
            await tc("qdisc", "del", "dev", dev, "clsact", check=check)
        placed = False

    configs = [stream(family, group, hops=63), stream(family, group, hops=63, port=OTHER)]

    try:
        if not legacy:
            console = await dut_console(f"mroute-ports-{kind}-v{family}")
        oif = await dut_vlan_subif(topology, r.target, r.session, parent=TARGET_LAN_IF,
                                   vid=PORTS_VID, ipv4="198.18.170.1/24", ipv6="fd00:170::1/64")
        peer = await lan_vlan_subif(topology, r.lan, parent=LAN_NIC, vid=PORTS_VID)
        observers = [(r.lan, {LAN_NIC: r.dut_lan_mac, peer: r.dut_lan_mac})]

        async def window(label, ruled):
            result = await r.window(configs, observers, ingress=TARGET_WAN_IF,
                                    label=f"ports-{kind}-v{family}-{label}")
            for port in (PORT, OTHER):
                config = streamed(result, group, port=port)
                for iface in (LAN_NIC, peer):
                    expected = forwarded(port, iface) if ruled else True
                    assert delivered(result, config, iface) == expected, (port, iface, label)
            return result

        if legacy:
            # The walk reads x_tables; through iptables-nft the same rule
            # would be nftables', and refused-ports.
            version = await command(r.target, r.session, iptables, "-V")
            assert "legacy" in version["stdout"], version
        if kind == "legacy-control":
            # The tables the image itself instantiates, which every routed
            # case on it is judged under; it builds no legacy IPv6 NAT.
            names = (await read(r.target, r.session,
                                "/proc/net/ip_tables_names" if family == 4 else
                                "/proc/net/ip6_tables_names")).split()
            wanted = {"filter", "nat"} if family == 4 else {"filter"}
            assert wanted <= set(names), names

        async with _daemon(r.target, r.session, [TARGET_WAN_IF, TARGET_LAN_IF, oif]) as ctl:
            await place()
            await ctl("add", TARGET_WAN_IF, source, group, TARGET_LAN_IF, oif)
            await learn(r, configs[:1], refused if splits else carried,
                        f"{ruled_state} under the rule" if splits else "carried under the rule")
            first = await window("ruled", ruled=True)
            if splits:
                # An ingress drop takes the second port before the CPU
                # counter's hook, which tc precedes; every other frame of
                # both ports reaches it.
                in_software(first, streams=1 if kind == "tc-ingress-drop" else 2)
                assert refused(first["after"]), summary(first["after"])
                assert first["after"]["mroute_installed"] == r.initial["mroute_installed"], \
                    summary(first["after"])
            else:
                in_hardware(first, streams=2)
                assert moved(first, row) == 2 * COUNT, summary(first["after"])

            before = await r.proc()
            await lift()
            if legacy:
                # No generation of nftables' moves: the kernel's count of
                # x_tables changes does, and the learner asks every group
                # again once it has seen it. A tc filter's removal is seen by
                # nothing, and the next refresh asks again.
                await r.settle(lambda s: s["mroute_xtables_changes"] >
                               before["mroute_xtables_changes"], "the rule's removal seen")
            await learn(r, configs[:1], carried, "carried once the rule is gone")
            second = await window("unruled", ruled=False)
            in_hardware(second, streams=2)
            assert moved(second, row) == 2 * COUNT, summary(second["after"])

            await ctl("remove", TARGET_WAN_IF, source, group)
            final = await r.settle(lambda s: row(s) is None and
                                   s["mroute_installed"] == r.initial["mroute_installed"],
                                   "the route removed")
        assert final["quarantine"] == 0, summary(final)
        assert final["mroute_install_errors"] == r.initial["mroute_install_errors"], summary(final)
        assert final["mroute_port_probe_errors"] == r.initial["mroute_port_probe_errors"], \
            summary(final)
    finally:
        try:
            if placed:
                await lift(check=False)
        finally:
            if console is not None:
                console.close()
            await topology.teardown("routed ports")
