"""A158: the flowtable listener ceiling and replication across physical ports.

Run on an idle DUT. No global daemon kills.
Captures identify every sequence independently on every receiving VLAN.
"""
from __future__ import annotations

from _mroute_capacity import PORT

from _mroute_capacity import _absent, _daemon, _preflight, _python, _state, _window

import json
import os

import pytest

from _mcast_cpu import stream_cpu_counters
from _topology import LAN_NIC, TARGET_LAN_IF, TARGET_WAN_IF, TopologyStack, VLAN_IDS_MROUTE_LIMIT, VLAN_ID_PPPOE_WAN, dut_vlan_subif, lan_vlan_subif
from _mcast_e2e import _exec, dut_mac, wan_source_address

pytestmark = pytest.mark.asyncio


@pytest.mark.parametrize("family", [4, 6])
async def test_routed_listener_ceiling(aiohttp_session, target_agent, lan,
                                       splat_window, family):
    """Eight exact copies; nine all in software; remove ninth and recover eight."""
    await _preflight(target_agent, aiohttp_session)
    topology = TopologyStack()
    group = "239.8.158.1" if family == 4 else "ff1e::8:158:1"
    dut, peers = [], []
    try:
        for i, vid in enumerate(VLAN_IDS_MROUTE_LIMIT):
            dut.append(await dut_vlan_subif(
                topology, target_agent, aiohttp_session, parent=TARGET_LAN_IF, vid=vid,
                ipv4=f"198.18.158.{4 * i + 1}/30",
                ipv6=f"fd00:158:{vid:x}::1/64"))
            peers.append(await lan_vlan_subif(topology, lan, parent=LAN_NIC, vid=vid))
        mac = await dut_mac(target_agent, aiohttp_session, TARGET_LAN_IF)
        async with stream_cpu_counters(target_agent, aiohttp_session, (PORT,)), \
                _daemon(target_agent, aiohttp_session, [TARGET_WAN_IF, *dut]) as ctl:
            await ctl("add", TARGET_WAN_IF, wan_source_address(family), group, *dut[:8])
            for size in (8, 9, 8):
                if size == 9:
                    await ctl("add", TARGET_WAN_IF, wan_source_address(family), group, *dut)
                state = "installed" if size == 8 else "refused-listener"
                listeners = [f"{TARGET_LAN_IF}/{v}" for v in VLAN_IDS_MROUTE_LIMIT[:size]]
                await _state(target_agent, aiohttp_session, group, state,
                             listeners if size == 8 else (),
                             family=family if size == 8 else None)
                await _window(target_agent, aiohttp_session, family=family, group=group,
                              observers=[(lan, dict.fromkeys(peers, mac))],
                              expected=peers[:size], hardware=size == 8,
                              label=f"limit-{size}-{'recovered' if len(dut) == 8 else 'initial'}")
                if size == 9:
                    # smcroute ADD only grows a route. Removing the ninth VIF
                    # exercises the actual netdevice/FIB withdrawal path.
                    await _exec(target_agent, aiohttp_session, "ip", "link", "del", dut.pop())
            await ctl("remove", TARGET_WAN_IF, wan_source_address(family), group)
            await _absent(target_agent, aiohttp_session, group)
    finally:
        await topology.teardown("A158 listener ceiling")


@pytest.mark.parametrize("family", [4, 6])
async def test_routed_replication_across_physical_ports(aiohttp_session, target_agent,
                                                       lan, splat_window, family):
    """One copy leaves LAN, another leaves WAN tagged; ingress is WAN untagged."""
    await _preflight(target_agent, aiohttp_session)
    assert TARGET_LAN_IF != TARGET_WAN_IF, "two distinct physical ports required"
    topology = TopologyStack()
    group = "239.8.158.2" if family == 4 else "ff1e::8:158:2"
    vid = VLAN_IDS_MROUTE_LIMIT[0]
    wan_vid = int(os.environ.get("ASK_MROUTE_WAN_VID", str(VLAN_ID_PPPOE_WAN)))
    wan_peer = os.environ.get("ASK_MROUTE_WAN_IF", "askwan3900")
    peer = json.loads(await _python(None, f"""
import subprocess
print(subprocess.check_output(['ip', '-j', '-d', 'link', 'show', 'dev', {wan_peer!r}], text=True))
"""))[0]
    assert "UP" in peer["flags"], peer
    assert peer["linkinfo"]["info_kind"] == "vlan", peer
    assert peer["linkinfo"]["info_data"]["id"] == wan_vid, peer
    try:
        lan_oif = await dut_vlan_subif(
            topology, target_agent, aiohttp_session, parent=TARGET_LAN_IF, vid=vid,
            ipv4="198.18.158.1/30", ipv6=f"fd00:158:{vid:x}::1/64")
        lan_peer = await lan_vlan_subif(topology, lan, parent=LAN_NIC, vid=vid)
        wan_oif = await dut_vlan_subif(
            topology, target_agent, aiohttp_session, parent=TARGET_WAN_IF, vid=wan_vid,
            ipv4="198.18.158.253/30", ipv6=f"fd00:158:{wan_vid:x}::1/64")
        # AF_PACKET sees the replica even though its source is this host's
        # own sender address, without changing its addresses or routes.
        lan_mac = await dut_mac(target_agent, aiohttp_session, TARGET_LAN_IF)
        wan_mac = await dut_mac(target_agent, aiohttp_session, TARGET_WAN_IF)
        async with stream_cpu_counters(target_agent, aiohttp_session, (PORT,)), \
                _daemon(target_agent, aiohttp_session, [TARGET_WAN_IF, lan_oif, wan_oif]) as ctl:
            await ctl("add", TARGET_WAN_IF, wan_source_address(family), group, lan_oif, wan_oif)
            await _state(target_agent, aiohttp_session, group, "installed",
                         [f"{TARGET_LAN_IF}/{vid}", f"{TARGET_WAN_IF}/{wan_vid}"],
                         family=family)
            await _window(target_agent, aiohttp_session, family=family, group=group,
                          observers=[(lan, {lan_peer: lan_mac}), (None, {wan_peer: wan_mac})],
                          expected=[lan_peer, wan_peer], hardware=True, label="physical-ports")
            await ctl("remove", TARGET_WAN_IF, wan_source_address(family), group)
            await _absent(target_agent, aiohttp_session, group)
    finally:
        await topology.teardown("A158 physical ports")
