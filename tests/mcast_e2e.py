"""End-to-end multicast offload: a real consumer joins, a real source sends.

Programming a group by hand and checking the hardware replicated it proves the
encoder. It cannot prove the thing this file is actually about, which is that
nobody has to program anything: the LAN VM sends an IGMP or MLD report, the
bridge's own snooping learns the group, and the offload follows from that.

Topology. vision (the orchestrator, this host) is the source, on the DUT's WAN
port. The LAN VM is the consumer, behind the DUT and reachable only over
the libvirt UART. The DUT bridges the two.

**The assertion that matters is not that the LAN VM receives the stream.** The Linux
bridge floods multicast perfectly well in software, so arrival proves
forwarding and says nothing about offload. Each case therefore carries three
oracles, and the third is the one that discriminates:

  1. `bridge mdb show` reports `offload` against the port group. Read this as
     "the adapter took responsibility for the group", not "the hardware is
     carrying it": the switchdev handler runs under RTNL and cannot take the
     transaction an install needs, so it decides and a work item installs.
     See docs/flowtable/multicast.md, "The handler cannot install".
  2. The group is present in the hardware table, read from /proc. THIS is
     "actually installed", and it is where a disagreement with (1) surfaces.
  3. **The DUT's CPU does not see the stream.** A hardware-replicated frame is
     matched and transmitted by the FMAN and never reaches the host, so a
     counter on the ingress port's netdev hook, keyed on the stream's own UDP
     port, counts ~0 while the LAN VM counts thousands.
     Software flooding cannot produce that, and neither can a group that is
     merely present in a table but not matching.

The three deliberately mean different things — accepted, installed, and
matching — so a case that passes all three has been checked at three
independent points rather than three times at one.

IGMPv2 and IGMPv3 are both covered and the split is not incidental. The
classifier key is an exact (S,G), so a v3 INCLUDE report — which carries a
source — is installable from the membership alone, while a v2 report or a v3
EXCLUDE{} gives a (*,G) the membership cannot key, and the group only appears
once traffic has taught the adapter its source and ingress port. Those are two
different halves of the learner, and a suite that used whatever `ip maddr`
happened to emit would exercise one of them.
"""

from __future__ import annotations

from _mcast_e2e import (GROUPS_V4, GROUPS_V6, MCAST_PORT, MROUTE_BRIDGE_MAC, STREAM_PPS, STREAM_S, _exec, dut_mac, installed_by_traffic, lan_join_and_count, mroute_proc_row, proc_field, run_bridged_case, run_routed_case, send_stream_from_vision, wan_source_address)

import asyncio
import re

import pytest


from _mcast_cpu import cpu_frames
from _mcast_helpers import (kill_parallel_tcpdumps, read_pcap_count, spawn_parallel_tcpdumps)
from _topology import LAN_NIC, TARGET_LAN_IF, TARGET_WAN_IF, VLAN_ID_MROUTE, TopologyStack, dut_vlan_subif, lan_vlan_subif
# The serial console and its command wrapper, for the one step that cannot go
# over the agent: see mcast_bridge.

pytestmark = pytest.mark.asyncio


@pytest.mark.parametrize("case,source_kind,igmp_version", [
    ("v3_include", "explicit", 3),
    ("v2", None, 2),
])
async def test_bridged_ipv4(aiohttp_session, target_agent, lan, mcast_bridge,
                            stream_cpu, case, source_kind, igmp_version):
    """IPv4, both report versions.

    v3 INCLUDE gives the MDB a source, so the membership alone composes a key.
    v2 gives a (*,G), and the group can only appear once traffic has supplied
    the source and the ingress port — the two halves of the learner.
    """
    source = wan_source_address(4) if source_kind == "explicit" else None
    await run_bridged_case(
        aiohttp_session, target_agent, lan,
        group=GROUPS_V4[case], family=4, source=source,
        igmp_version=igmp_version, label=f"mcast_e2e_v4_{case}",
    )


@pytest.mark.parametrize("case,source_kind", [
    ("mldv2_include", "explicit"),
    ("mldv1", None),
])
async def test_bridged_ipv6(aiohttp_session, target_agent, lan, mcast_bridge,
                            stream_cpu, case, source_kind):
    """IPv6, both report versions, against the mc6 side of the encoder."""
    source = wan_source_address(6) if source_kind == "explicit" else None
    await run_bridged_case(
        aiohttp_session, target_agent, lan,
        group=GROUPS_V6[case], family=6, source=source,
        igmp_version=None, label=f"mcast_e2e_v6_{case}",
    )


@pytest.mark.rfc("1112")
@pytest.mark.rfc("4291")
@pytest.mark.rfc("3307")
@pytest.mark.rfc("1812", section="5.3.1")
@pytest.mark.parametrize("family", [4, 6])
async def test_routed_to_a_port(aiohttp_session, target_agent, lan, smcrouted,
                                stream_cpu, family):
    """The plain shape, both families: one (S,G), one oif, and that oif is the
    LAN port itself. A TTL-1 stream for the same group is not routed.
    """
    group = GROUPS_V6["routed"] if family == 6 else GROUPS_V4["routed"]
    await run_routed_case(
        aiohttp_session, target_agent, lan, group=group, family=family,
        oif=TARGET_LAN_IF, lan_iface=LAN_NIC,
        label=f"mroute_port_v{family}", smcrouted=smcrouted, expire=True,
    )


async def test_routed_to_a_vlan_subinterface(aiohttp_session, target_agent,
                                             lan, smcrouted, stream_cpu):
    """The oif is a VLAN device over the LAN port.

    The listener is the port beneath it and the tag is pushed by the entry's
    own INSERT_VLAN_HDR, which is the whole reason a listener carries a tag
    stack rather than an interface name: a VLAN device has no onif of its own
    and would describe none.
    """
    stack = TopologyStack()
    group = GROUPS_V4["routed_vlan"]
    try:
        dut_if = await dut_vlan_subif(
            stack, target_agent, aiohttp_session, parent=TARGET_LAN_IF,
            vid=VLAN_ID_MROUTE, ipv4=f"192.168.{VLAN_ID_MROUTE}.1/24",
        )
        lan_if = await lan_vlan_subif(
            stack, lan, parent=LAN_NIC, vid=VLAN_ID_MROUTE,
            ipv4=f"192.168.{VLAN_ID_MROUTE}.2/24",
        )
        await run_routed_case(
            aiohttp_session, target_agent, lan, group=group, family=4,
            oif=dut_if, lan_iface=lan_if,
            label="mroute_vlan", smcrouted=smcrouted,
        )
    finally:
        await stack.teardown("test_routed_to_a_vlan_subinterface")


async def test_routed_to_two_listeners_on_one_port(aiohttp_session,
                                                   target_agent, lan,
                                                   smcrouted, stream_cpu):
    """Two oifs on the one LAN port: untagged, and tagged on a sub-interface.

    This is the only multi-listener replication this rig can do, and it is the
    measurement ISSUES.md A158 has been waiting for. The board has five ports
    and two with carrier, one of which is every group's ingress, so a second
    listener has to be a second tag stack on the one port that is left. A
    listener is identified by its whole framing rather than by its device, so
    the backend takes both and builds one entry per copy in the chain.

    The two copies are counted separately rather than together, which is what
    discriminates replication from a single copy seen twice: the socket joined
    on the parent NIC receives the untagged one, and only that one, because
    the tagged copy is demuxed to the sub-interface where nothing has joined;
    the capture on the sub-interface sees the tagged one.
    """
    stack = TopologyStack()
    group = GROUPS_V4["routed_pair"]
    source = wan_source_address(4)
    sent = int(STREAM_S * STREAM_PPS)
    capfile = "/tmp/ask_mroute_pair.pcap"
    try:
        dut_if = await dut_vlan_subif(
            stack, target_agent, aiohttp_session, parent=TARGET_LAN_IF,
            vid=VLAN_ID_MROUTE, ipv4=f"192.168.{VLAN_ID_MROUTE}.1/24",
        )
        lan_if = await lan_vlan_subif(
            stack, lan, parent=LAN_NIC, vid=VLAN_ID_MROUTE,
            ipv4=f"192.168.{VLAN_ID_MROUTE}.2/24",
        )
        await smcrouted([TARGET_WAN_IF, TARGET_LAN_IF, dut_if])
        await _exec(target_agent, aiohttp_session, "smcroutectl", "add",
                    TARGET_WAN_IF, source, group, TARGET_LAN_IF, dut_if)
        row = await installed_by_traffic(target_agent, aiohttp_session, group, 4)
        assert "state=installed" in row, (
            f"{group}: two oifs on one port were not installed. /proc row: "
            f"{row or '(absent)'}")
        # Both copies, named separately, on the one port.
        listeners = re.search(r"listeners=(\S+)", row)
        assert listeners, row
        assert listeners.group(1).count(TARGET_LAN_IF) == 2, (
            f"{group}: expected two listeners on {TARGET_LAN_IF}, one per tag "
            f"stack; got {listeners.group(1)!r}. A backend that identified a "
            f"listener by its device would have collapsed them")

        # One stream per observer, for the reason run_routed_case gives: the
        # socket consumer and the capture both live on the LAN VM's single
        # console and cannot be in flight together. The two copies are still
        # counted separately, which is what discriminates replication from one
        # copy seen twice -- they are just counted one stream apart.
        joiner = asyncio.create_task(lan_join_and_count(
            lan, group=group, source=None, family=4,
            seconds=STREAM_S + 4.0, igmp_version=None,
            label="mroute_pair", iface=LAN_NIC,
        ))
        await asyncio.sleep(2.0)
        counted = await cpu_frames(target_agent, aiohttp_session, TARGET_WAN_IF)
        await asyncio.to_thread(
            send_stream_from_vision, group, 4, STREAM_S, STREAM_PPS,
        )
        untagged = await joiner
        cpu_seen = await cpu_frames(target_agent, aiohttp_session, TARGET_WAN_IF) - counted

        spawn_parallel_tcpdumps(lan, [lan_if], [capfile],
                                f"udp port {MCAST_PORT}")
        await asyncio.to_thread(
            send_stream_from_vision, group, 4, STREAM_S, STREAM_PPS,
        )
        kill_parallel_tcpdumps(lan, [lan_if])
        await asyncio.sleep(0.2)
        tagged = read_pcap_count(lan, capfile)
        lan.run(f"rm -f {capfile}", timeout=5)

        assert untagged > sent * 0.95, (
            f"{group}: the untagged copy arrived {untagged} of {sent} times")
        assert tagged > sent * 0.95, (
            f"{group}: the tagged copy arrived {tagged} of {sent} times on "
            f"{lan_if}. One copy of two means the chain carries one entry "
            f"where it should carry two")
        assert cpu_seen < sent * 0.05, (
            f"{group}: {cpu_seen} of {sent} frames reached the DUT's CPU, so "
            f"ipmr replicated this in software")

        # The chain swap, triggered the way one really happens: the VLAN device
        # carrying the second oif goes away, ipmr withdraws its VIF, and the
        # group is re-derived onto the listener that is left. A second
        # `smcroutectl add` for the same (S,G) cannot stand in for it -- it
        # does not shrink an oif list, it leaves the route as it was, which is
        # what an earlier revision of this case asserted against and what the
        # rig reported back. Swapped, not withdrawn and put back: the two end on
        # the same state and listeners, and only the group's own count of
        # entries added tells them apart. The global refusal count cannot:
        # smcrouted also adds listener-less entries for whatever the WAN VIF
        # hears -- SSDP from the segment -- and each of those is a refusal.
        before = await mroute_proc_row(target_agent, aiohttp_session, group)
        assert "state=installed" in before, before
        await _exec(target_agent, aiohttp_session, "ip", "link", "del", dut_if)
        await asyncio.sleep(3.0)
        row = await mroute_proc_row(target_agent, aiohttp_session, group)
        assert "state=installed" in row, (
            f"{group}: the group did not survive losing an oif: {row!r}")
        listeners = re.search(r"listeners=(\S+)", row)
        assert listeners and listeners.group(1).count(TARGET_LAN_IF) == 1, (
            f"{group}: the replaced set still names two listeners: {row!r}")
        assert proc_field(row, "adds") == proc_field(before, "adds"), (
            f"{group}: the group left hardware to lose an oif rather than "
            f"having its chain swapped: {before!r} -> {row!r}")

        await _exec(target_agent, aiohttp_session, "smcroutectl", "remove",
                    TARGET_WAN_IF, source, group)
        await asyncio.sleep(2.0)
        assert not await mroute_proc_row(target_agent, aiohttp_session, group)
    finally:
        await stack.teardown("test_routed_to_two_listeners_on_one_port")


async def test_routed_to_a_bridge(aiohttp_session, target_agent, lan,
                                  smcrouted, mroute_lan_bridge, stream_cpu):
    """The oif is a bridge over the LAN port, with snooping off.

    br_dev_xmit() hands such a frame to br_flood(), so the listener set is
    every port carrying BR_MCAST_FLOOD -- here the one. ipmr sends the copy
    with the bridge's address, and so must the hardware, though it leaves by
    the port. A one-port bridge takes its port's address, which would make
    that oracle a coincidence, so the bridge is given one of its own first.
    """
    await _exec(target_agent, aiohttp_session, "ip", "link", "set",
                mroute_lan_bridge, "address", MROUTE_BRIDGE_MAC)
    assert await dut_mac(target_agent, aiohttp_session, mroute_lan_bridge) != \
        await dut_mac(target_agent, aiohttp_session, TARGET_LAN_IF), (
        "the bridge still shares its port's address, so the source-MAC oracle "
        "cannot tell the bridge's copy from the port's")
    await run_routed_case(
        aiohttp_session, target_agent, lan, group=GROUPS_V4["routed_bridge"],
        family=4, oif=mroute_lan_bridge, lan_iface=LAN_NIC,
        label="mroute_bridge", smcrouted=smcrouted,
    )
