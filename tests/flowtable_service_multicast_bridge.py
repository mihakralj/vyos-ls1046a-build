"""Bridge multicast recovery with an isolated, tagged LAN listener."""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
import json
import os
from pathlib import Path
import re
import time
from types import SimpleNamespace

import pytest
import pytest_asyncio

from ask_orch.counters import kernel_rx_packets
from ask_orch.uart import Console
from _mcast_helpers import arm_bridge_querier
from _mcast_cpu import cpu_frames, stream_cpu_counters
from _mcast_wire import capture, frames, new_config, send
from _topology import LAN_NIC, TARGET_LAN_IF, TARGET_WAN_IF, lan_run_python
from _mcast_wire_capture import multicast_mac
from _flowtable_rig import (artifact_dir, WAN_IP, command, console_command, read)
from _flowtable_identity import (set_mac)
from _flowtable_service import (managed_service, wait_service)
from _flowtable_service_multicast import (acceleration_stopped, recover)
from _mcast_e2e import (dut_mac, mroute_line)
from _mroute_capacity import (_daemon)

BRIDGE = 'br-ftmcast'
LAN_VID, WAN_VID, IPTV_VID = 287, 288, 289
IPTV_TAGGED = os.environ.get('ASK_MCAST_RECOVERY_TAGGED') == '1'
WAN_PVID = WAN_VID if IPTV_TAGGED else IPTV_VID
LAN_L3, WAN_L3, LISTENER = BRIDGE + '.287', f'{BRIDGE}.{WAN_PVID}', 'askftmc'
REPORT_TABLE = 'ask_ft_mc_reports'


@pytest_asyncio.fixture(params=[4, 6])
async def multicast_bridge_service(rig, request):
    r = rig
    r.multicast_family, r.multicast_kind = request.param, 'mcast'
    r.multicast_groups = ['239.9.5.1', '239.9.5.2'] if request.param == 4 else ['ff1e::9:5:1', 'ff1e::9:5:2']
    r.multicast_source = '198.18.103.2' if request.param == 4 else 'fd42:6173:8:2::2'
    r.multicast_vlan, r.multicast_window, r.multicast_listener = IPTV_VID, 0, LISTENER
    r.multicast_wire_vlan = IPTV_VID if IPTV_TAGGED else None
    r.multicast_bridge = BRIDGE
    members = Path('/sys/class/net', r.wan_if, 'brif')
    physical = [p.name for p in members.iterdir()
                if Path('/sys/class/net', p.name, 'device').exists()] if members.exists() else [r.wan_if]
    r.multicast_send_if = os.environ.get('ASK_WAN_WIRE_IF')
    if not r.multicast_send_if:
        assert len(physical) == 1, ('set ASK_WAN_WIRE_IF to the DUT-facing physical port', physical)
        r.multicast_send_if = physical[0]
    links = json.loads((await command(r.target, r.session, 'ip', '-j', 'link', 'show'))['stdout'])
    assert not {BRIDGE, LAN_L3, WAN_L3} & {i['ifname'] for i in links}, links
    assert all('master' not in i for i in links if i['ifname'] in (TARGET_LAN_IF, TARGET_WAN_IF)), links
    original = {}
    for dev in (TARGET_LAN_IF, TARGET_WAN_IF):
        interfaces = json.loads((await command(r.target, r.session, 'ip', '-j', '-4', 'addr', 'show', 'dev', dev))['stdout'])
        original[dev] = [f"{a['local']}/{a['prefixlen']}" for a in interfaces[0]['addr_info'] if a['family'] == 'inet']
        assert original[dev]
    defaults = json.loads((await command(r.target, r.session, 'ip', '-j', 'route', 'show', 'default'))['stdout'])
    default = next((rt for rt in defaults if rt.get('dev') == TARGET_WAN_IF), None)
    before = await r.state()
    lan_created = bridge_created = False
    try:
        # The upstream bench switch does not trunk IPTV by default. Its
        # untagged stream and the WAN subnet share the WAN PVID, as in the ISP
        # profile. The LAN listener is tagged; ordinary LAN traffic retains
        # a separate PVID. A trunk can opt into tagged WAN injection.
        # UART carries the address move.
        with Console.target(log_path=str(artifact_dir() / 'multicast-bridge-setup-uart.log')) as con:
            await asyncio.to_thread(con.login, 'root', None)
            # IGMPv3/MLDv2 queries, though this case's listener reports v2:
            # the LAN VM's other interfaces hear them too, and one v2 query
            # keeps an interface in v2 mode for minutes, past this file.
            await console_command(con, 'ip', 'link', 'add', 'name', BRIDGE, 'type', 'bridge',
                                  'vlan_filtering', '1', 'vlan_default_pvid', '0',
                                  'mcast_snooping', '1', 'mcast_querier', '1',
                                  'mcast_igmp_version', '3', 'mcast_mld_version', '2')
            bridge_created = True
            await console_command(con, 'ip', 'link', 'set', BRIDGE, 'address', r.dut_wan_mac, 'up')
            # Routed controls need the logical and physical egress MACs to
            # agree. Announce this fixture's common MAC, then restore it below.
            await set_mac(con, TARGET_LAN_IF, r.lan_gateway, r.dut_wan_mac)
            for dev, logical, vid in [(TARGET_LAN_IF, LAN_L3, LAN_VID),
                                      (TARGET_WAN_IF, WAN_L3, WAN_PVID)]:
                for address in original[dev]:
                    await console_command(con, 'ip', 'addr', 'del', address, 'dev', dev)
                await console_command(con, 'ip', 'link', 'set', dev, 'master', BRIDGE)
                await console_command(con, 'bridge', 'vlan', 'add', 'dev', dev, 'vid', str(vid), 'pvid', 'untagged')
                if vid != IPTV_VID:
                    await console_command(con, 'bridge', 'vlan', 'add', 'dev', dev, 'vid', str(IPTV_VID))
                await console_command(con, 'bridge', 'link', 'set', 'dev', dev, 'mcast_flood', 'off', 'mcast_router', '0')
                await console_command(con, 'bridge', 'vlan', 'add', 'dev', BRIDGE, 'vid', str(vid), 'self')
                await console_command(con, 'ip', 'link', 'add', 'link', BRIDGE, 'name', logical, 'type', 'vlan', 'id', str(vid))
                # Inherit the bridge MAC: a distinct upper MAC makes the
                # bridge promiscuous and requests a CPU copy of every stream.
                await console_command(con, 'ip', 'link', 'set', logical, 'up')
                for address in original[dev]:
                    await console_command(con, 'ip', 'addr', 'add', address, 'dev', logical)
            for address, logical, mac in [(r.lan_ip, LAN_L3, r.lan_mac), (WAN_IP, WAN_L3, r.wan_mac)]:
                await console_command(con, 'ip', 'route', 'replace', address + '/32', 'dev', logical)
                await console_command(con, 'ip', 'neigh', 'replace', address, 'lladdr', mac, 'nud', 'permanent', 'dev', logical)
            if default:
                await console_command(con, 'ip', 'route', 'replace', 'default', 'via', default['gateway'], 'dev', WAN_L3)
        # Static MDB configuration owns these recovery transitions. Suppress
        # the listener's automatic reports so a querier cannot undo a
        # deliberate withdrawal while its receiving socket stays open.
        rules = f'''table inet {REPORT_TABLE} {{
 chain output {{ type filter hook output priority -300; policy accept;
  oifname "{LISTENER}" ip protocol igmp drop
  oifname "{LISTENER}" meta l4proto ipv6-icmp icmpv6 type {{ 130, 131, 132, 143 }} drop
 }}
}}'''
        setup = f'''
from pathlib import Path
import json, subprocess
def run(*argv): subprocess.run(argv,check=True,capture_output=True,text=True)
assert not Path('/sys/class/net/' + {LISTENER!r}).exists()
tables=json.loads(subprocess.check_output(['nft','-j','list','tables'],text=True))
assert not any(t.get('table',{{}}).get('name') == {REPORT_TABLE!r} for t in tables['nftables'])
addresses=json.loads(subprocess.check_output(['ip','-j','addr'],text=True))
assert not any(a['local'].startswith(('198.18.103.','fd42:6173:8:2:')) for i in addresses for a in i['addr_info'])
run('ip','link','add','link',{LAN_NIC!r},'name',{LISTENER!r},'type','vlan','id',{str(IPTV_VID)!r})
filtered=False
try:
 subprocess.run(['nft','-f','-'],input={rules!r},text=True,check=True,capture_output=True)
 filtered=True
 run('ip','link','set',{LISTENER!r},'up')
 # A numbered IPTV segment also satisfies the listener's source validation.
 # With no IPv4 address, even loose rp_filter rejects this ingress device.
 run('ip','addr','add','198.18.103.3/24','dev',{LISTENER!r})
 run('ip','-6','addr','add','fd42:6173:8:2::3/64','dev',{LISTENER!r},'nodad')
 Path('/proc/sys/net/ipv4/conf/' + {LISTENER!r} + '/force_igmp_version').write_text('2')
 Path('/proc/sys/net/ipv6/conf/' + {LISTENER!r} + '/force_mld_version').write_text('1')
except BaseException:
 run('ip','link','del',{LISTENER!r})
 if filtered: run('nft','delete','table','inet',{REPORT_TABLE!r})
 raise
'''
        result = await lan_run_python(r.lan, setup, label='multicast_bridge_listener', timeout=15)
        assert result.rc == 0, result.stdout
        lan_created = True
        await asyncio.sleep(3)
        await arm_bridge_querier(lambda *argv: command(r.target, r.session, *argv), BRIDGE)
        # Before the service learns anything: a commit once a routed group
        # exists would take its confirmations back.
        async with stream_cpu_counters(r.target, r.session, (FRAMING_PORT,)), managed_service(r):
            yield r
    finally:
        failures = []
        if bridge_created:
            with Console.target(log_path=str(artifact_dir() / 'multicast-bridge-cleanup-uart.log')) as con:
                await asyncio.to_thread(con.login, 'root', None)
                async def undo(*argv):
                    try:
                        await console_command(con, *argv)
                    except Exception as error:
                        failures.append(repr(error))
                for dev in (TARGET_LAN_IF, TARGET_WAN_IF):
                    await undo('ip', 'link', 'set', dev, 'nomaster')
                await undo('ip', 'link', 'del', BRIDGE)
                for dev in (TARGET_LAN_IF, TARGET_WAN_IF):
                    for address in original[dev]:
                        await undo('ip', 'addr', 'replace', address, 'dev', dev)
                try:
                    await set_mac(con, TARGET_LAN_IF, r.lan_gateway, r.dut_lan_mac)
                except Exception as error:
                    failures.append(repr(error))
                for address, dev, mac in [(r.lan_ip, TARGET_LAN_IF, r.lan_mac), (WAN_IP, TARGET_WAN_IF, r.wan_mac)]:
                    await undo('ip', 'route', 'replace', address + '/32', 'dev', dev)
                    await undo('ip', 'neigh', 'replace', address, 'lladdr', mac, 'nud', 'permanent', 'dev', dev)
                if default:
                    await undo('ip', 'route', 'replace', 'default', 'via', default['gateway'], 'dev', TARGET_WAN_IF)
        if lan_created:
            result = await lan_run_python(r.lan, f"import subprocess\nsubprocess.run(['ip','link','del',{LISTENER!r}],check=True)\nsubprocess.run(['nft','delete','table','inet',{REPORT_TABLE!r}],check=True)\n",
                                          label='multicast_bridge_listener_cleanup', timeout=15)
            if result.rc:
                failures.append(result.stdout)
        final = await r.wait(lambda s: s['mcast_groups'] == before['mcast_groups']
                             and s['mcast_installed'] == before['mcast_installed'], timeout=8)
        r.record('multicast-bridge-drained', final)
        assert not failures, failures


@pytest.mark.parametrize('fault', ['withdrawal', 'install-failslab'])
async def test_recovery(multicast_bridge_service, fault):
    await recover(multicast_bridge_service, fault)


# Complement the routed cases: each stop method covers both families and learners.
@pytest.mark.parametrize('multicast_bridge_service,how', [
    (4, 'service-stop'), (4, 'disabled-config'),
    (6, 'stop'), (6, 'disabled'),
], indirect=['multicast_bridge_service'])
async def test_stops_with_acceleration(multicast_bridge_service, how):
    await acceleration_stopped(multicast_bridge_service, how)


# ---- what a bridged replica looks like on the wire --------------------------
#
# A bridge forwards a frame with the Ethernet addresses and hop count it arrived
# with. The listeners' old literal header wrote the egress port's address over
# the sender's, and the IP-level oracles above cannot see that: they read the
# datagram, not the frame. These read the frame.

FRAMING_PORT = 47395
FRAMING_COUNT = 128
# How the WAN wire carries IPTV: untagged on the port's PVID by default, tagged
# where the bench trunks it (ASK_MCAST_RECOVERY_TAGGED=1).
WAN_WIRE_VID = IPTV_VID if IPTV_TAGGED else 0
# A LAN-side sender on the IPTV segment, the listener's own address.
LAN_SOURCE = {4: '198.18.103.3', 6: 'fd42:6173:8:2::3'}


def _group(r):
    return '239.9.5.3' if r.multicast_family == 4 else 'ff1e::9:5:3'


async def _bridged_row(r, group, ready):
    def found(state):
        rows = [g for g in state['mcast'] if g['group'] == group]
        return rows and ready(rows[0])
    state = await r.wait(found, timeout=15)
    return next(g for g in state['mcast'] if g['group'] == group)


async def _mdb(r, port, group, add=True):
    return await command(r.target, r.session, 'bridge', 'mdb', 'replace' if add else 'del',
                         'dev', BRIDGE, 'port', port, 'grp', group, 'vid', str(IPTV_VID),
                         *(['permanent'] if add else []))


def _packets(state, group):
    return int(next(g for g in state['mcast'] if g['group'] == group)['packets'])


async def _window(r, group, source, capture_on, ifaces, inject, ingress, label):
    """Inject a fresh run and read it off the listener's wire, with the
    classifier's own count and the ingress CPU's beside it.

    `cpu` is how many of the run's own frames reached the CPU on the ingress
    port (cpu_frames()). `idle` is kept for the record only: the port's
    software receive count over as long again with nothing injected, the
    segment's own traffic. The injection's length is not known until it
    ends, since a LAN-side one runs over the console, so the idle interval is
    taken after it rather than before."""
    config = new_config(r.multicast_family, source, group, FRAMING_PORT, ifaces)
    before = await r.state()
    loop = asyncio.get_running_loop()
    async with capture(capture_on, config) as handle:
        counted = await cpu_frames(r.target, r.session, ingress)
        started = loop.time()
        await inject(config, FRAMING_COUNT)
        await asyncio.sleep(0.5)
        elapsed = loop.time() - started
        cpu = await cpu_frames(r.target, r.session, ingress) - counted
    after = await r.state()
    rx = await kernel_rx_packets(r.target, r.session, ingress)
    await asyncio.sleep(elapsed)
    idle = await kernel_rx_packets(r.target, r.session, ingress) - rx
    r.record(label, {'result': handle['result'], 'before': before, 'after': after,
                     'cpu_rx': cpu, 'idle_rx': idle, 'seconds': elapsed})
    # One binding and one ruleset per window. A table replacement rebinds
    # (rearms) and commits twice, and every commit takes each routed group's
    # confirmations back, so a stream both bridged and routed goes to Linux
    # part-way through and reads as a mixed window rather than as what it
    # was. The ruleset count moves only while a routed group is watched.
    moved = {key: (before[key], after[key]) for key in ('rearms', 'mroute_ruleset_changes')
             if before[key] != after[key]}
    assert not moved, (f'{label}: the flowtable was rebound or the ruleset changed '
                       f'inside the window', moved)
    return handle['result'], before, after, cpu, idle


async def _from_wan(r, config, count, source_mac):
    """Frames from the orchestrator onto the WAN wire, as the IPTV head end's
    router would send them: tagged where the bench trunks the IPTV VLAN."""
    await asyncio.to_thread(send, frames(config, {1: 512}, count, source_mac=source_mac,
                                         vlan=IPTV_VID if IPTV_TAGGED else None),
                            r.multicast_send_if)


async def _from_lan(r, config, count):
    """Frames from the LAN VM's own IPTV VLAN device. The kernel there puts the
    tag on, so the DUT's LAN port receives exactly the tagged frames a sender
    behind a trunk would send."""
    overhead = (20 if config['family'] == 4 else 40) + 8
    layer = (f"IP(src={config['source']!r}, dst={config['group']!r}, ttl=64, flags='DF')"
             if config['family'] == 4 else
             f"IPv6(src={config['source']!r}, dst={config['group']!r}, hlim=64)")
    script = f'''
import struct, time
from scapy.all import Ether, IP, IPv6, UDP, Raw, conf
token = bytes.fromhex({config['token']!r})
sock = conf.L2socket(iface={LISTENER!r})
try:
    for sequence in range({count}):
        data = b'ASKMCW1' + token + struct.pack('!BI', 1, sequence)
        data += b'.' * ({512 - overhead} - len(data))
        sock.send(Ether(dst={multicast_mac(config['group']).hex(':')!r}) / {layer}
                  / UDP(sport={FRAMING_PORT}, dport={FRAMING_PORT}) / Raw(data))
        time.sleep(0.005)
finally:
    sock.close()
'''
    result = await lan_run_python(r.lan, script, label='multicast_bridge_lan_source',
                                  timeout=30)
    assert result.rc == 0, result.stdout


def _assert_bridged_copy(result, source_mac, group, vlan=0):
    """`vlan` is the tag the copy arrives with where it was captured: none on
    a VLAN device, which took it off, and the wire's own on a port."""
    assert not result['errors'], result
    assert result['seen'].get('1') == list(range(FRAMING_COUNT)), result
    assert result['duplicates'] == 0 and result['fragments'] == 0, result
    # A bridge changes neither the sender's address nor the hop count.
    assert result['sources'] == [source_mac.lower()], result
    assert result['destinations'] == [multicast_mac(group).hex(':')], result
    assert result['hops'] == [64], result
    assert result['vlans'] == [vlan], result


async def test_keeps_the_sender(multicast_bridge_service):
    """IPTV in on the WAN port, out to the set-top box tagged. The replica
    carries the sender's MAC, the group's MAC and the sender's hop count, and
    the classifier -- not the CPU -- made it. A second sender of the same
    (S,G) misses the key, which names the first one's address, and once the
    first has gone idle the learner moves the key to the second."""
    r = multicast_bridge_service
    group = _group(r)
    source = r.multicast_source
    await _mdb(r, TARGET_LAN_IF, group)
    try:
        # The first frames teach the learner the stream; then it is hardware.
        await _from_wan(r, new_config(r.multicast_family, source, group, FRAMING_PORT, []),
                        16, r.wan_mac)
        row = await _bridged_row(r, group, lambda g: g['state'] == 'installed')
        assert row['smac'] == r.wan_mac.lower(), row
        assert row['dmac'] == multicast_mac(group).hex(':'), row
        assert row['in'] == TARGET_WAN_IF and row['in_vid'] == str(WAN_WIRE_VID), row
        assert row['ports'] == f'{TARGET_LAN_IF}/{IPTV_VID}', row
        result, before, after, cpu, idle = await _window(
            r, group, source, r.lan, [LISTENER],
            lambda c, n: _from_wan(r, c, n, r.wan_mac), TARGET_WAN_IF,
            'multicast-bridge-sender')
        _assert_bridged_copy(result[LISTENER], r.wan_mac, group)
        assert _packets(after, group) - _packets(before, group) >= FRAMING_COUNT * 0.95, \
            (before, after)
        assert cpu < FRAMING_COUNT * 0.1, (cpu, idle)

        other = '02:a5:19:10:00:02'
        deadline = asyncio.get_running_loop().time() + 30
        while True:
            await _from_wan(r, new_config(r.multicast_family, source, group,
                                          FRAMING_PORT, []), 16, other)
            row = await _bridged_row(r, group, lambda g: True)
            if row['smac'] == other and row['state'] == 'installed':
                break
            assert asyncio.get_running_loop().time() < deadline, row
            await asyncio.sleep(1)
        result, before, after, cpu, idle = await _window(
            r, group, source, r.lan, [LISTENER],
            lambda c, n: _from_wan(r, c, n, other), TARGET_WAN_IF,
            'multicast-bridge-new-sender')
        _assert_bridged_copy(result[LISTENER], other, group)
        assert _packets(after, group) - _packets(before, group) >= FRAMING_COUNT * 0.95, \
            (before, after)
        assert cpu < FRAMING_COUNT * 0.1, (cpu, idle)
    finally:
        await _mdb(r, TARGET_LAN_IF, group, add=False)
        await r.wait(lambda s: not any(g['group'] == group for g in s['mcast']), timeout=20)


async def test_tagged_ingress(multicast_bridge_service):
    """IPTV in tagged on the LAN port, out of the WAN port as the WAN wire
    carries it. Before a group could say what it arrives with, its root
    expected an untagged frame and every tagged one fell back to the CPU; now
    the root strips the tag it was told about, and the copy leaves with the
    LAN sender's own address and hop count."""
    r = multicast_bridge_service
    group = _group(r)
    source = LAN_SOURCE[r.multicast_family]
    capture_if = r.multicast_send_if
    await _mdb(r, TARGET_WAN_IF, group)
    try:
        await _from_lan(r, new_config(r.multicast_family, source, group, FRAMING_PORT, []), 16)
        row = await _bridged_row(r, group, lambda g: g['state'] == 'installed')
        assert row['smac'] == r.lan_mac.lower(), row
        assert row['in'] == TARGET_LAN_IF and row['in_vid'] == str(IPTV_VID), row
        assert row['ports'] == f'{TARGET_WAN_IF}/{WAN_WIRE_VID}', row
        # The orchestrator's own wire port is the listener: it receives the
        # copy exactly as the WAN segment carries it.
        result, before, after, cpu, idle = await _window(
            r, group, source, None, [capture_if],
            lambda c, n: _from_lan(r, c, n), TARGET_LAN_IF,
            'multicast-bridge-tagged-ingress')
        _assert_bridged_copy(result[capture_if], r.lan_mac, group, WAN_WIRE_VID)
        assert _packets(after, group) - _packets(before, group) >= FRAMING_COUNT * 0.95, \
            (before, after)
        assert cpu < FRAMING_COUNT * 0.1, (cpu, idle)
    finally:
        await _mdb(r, TARGET_WAN_IF, group, add=False)
        await r.wait(lambda s: not any(g['group'] == group for g in s['mcast']), timeout=20)


FILTER_TABLE = 'ask_ft_mc_filter'


async def test_yields_to_a_bridge_filter(multicast_bridge_service):
    """An installed flow replicates at the classifier, before any bridge hook
    runs. An nftables bridge chain that drops the group at forward has to be
    obeyed: while the chain exists the flow comes out of hardware and says
    why, the stream reaches the CPU, and the chain drops it -- the set-top box
    receives nothing. When the chain goes, the flow is carried again."""
    r = multicast_bridge_service
    group = '239.9.5.6' if r.multicast_family == 4 else 'ff1e::9:5:6'
    source = r.multicast_source
    match = ['ip', 'daddr'] if r.multicast_family == 4 else ['ip6', 'daddr']
    filtered = False
    await _mdb(r, TARGET_LAN_IF, group)
    try:
        await _from_wan(r, new_config(r.multicast_family, source, group, FRAMING_PORT, []),
                        16, r.wan_mac)
        await _bridged_row(r, group, lambda g: g['state'] == 'installed')

        await command(r.target, r.session, 'nft', 'add', 'table', 'bridge', FILTER_TABLE)
        filtered = True
        await command(r.target, r.session, 'nft', 'add', 'chain', 'bridge', FILTER_TABLE,
                      'forward', '{ type filter hook forward priority 0; policy accept; }')
        await command(r.target, r.session, 'nft', 'add', 'rule', 'bridge', FILTER_TABLE,
                      'forward', *match, group, 'drop')
        # Nothing announces a hook: the worker finds it within a refresh.
        await _bridged_row(r, group, lambda g: g['state'] == 'refused-filter')
        # The hook refuses every bridged flow, not only this group's.
        await r.wait(lambda s: s['mcast_installed'] == 0, timeout=10)
        result, _, _, cpu, idle = await _window(
            r, group, source, r.lan, [LISTENER],
            lambda c, n: _from_wan(r, c, n, r.wan_mac), TARGET_WAN_IF,
            'multicast-bridge-filtered')
        assert not result[LISTENER]['errors'], result
        assert not result[LISTENER]['seen'], result
        assert cpu >= FRAMING_COUNT * 0.95, (cpu, idle)

        await command(r.target, r.session, 'nft', 'delete', 'table', 'bridge', FILTER_TABLE)
        filtered = False
        await _bridged_row(r, group, lambda g: g['state'] == 'installed')
        result, before, after, cpu, idle = await _window(
            r, group, source, r.lan, [LISTENER],
            lambda c, n: _from_wan(r, c, n, r.wan_mac), TARGET_WAN_IF,
            'multicast-bridge-unfiltered')
        _assert_bridged_copy(result[LISTENER], r.wan_mac, group)
        assert _packets(after, group) - _packets(before, group) >= FRAMING_COUNT * 0.95, \
            (before, after)
        assert cpu < FRAMING_COUNT * 0.1, (cpu, idle)
    finally:
        if filtered:
            await command(r.target, r.session, 'nft', 'delete', 'table', 'bridge',
                          FILTER_TABLE, check=False)
        await _mdb(r, TARGET_LAN_IF, group, add=False)
        await r.wait(lambda s: not any(g['group'] == group for g in s['mcast']), timeout=20)


# The UDP port the filters below drop; FRAMING_PORT is the one they keep.
TC_DROPPED = FRAMING_PORT + 1


def _tc_drop(family, tagged):
    """A filter that drops TC_DROPPED in software alone. A tag is metadata by
    the time tc sees the frame -- the receive path takes it off the wire, the
    bridge keeps a copy's there -- and tc then takes the tag's protocol for
    the frame's."""
    l3 = 'ip' if family == 4 else 'ipv6'
    match = (['protocol', '802.1q', 'flower', 'skip_hw', 'vlan_id', str(IPTV_VID),
              'vlan_ethtype', l3] if tagged else ['protocol', l3, 'flower', 'skip_hw'])
    return [*match, 'ip_proto', 'udp', 'dst_port', str(TC_DROPPED), 'action', 'drop']


async def _split_window(r, group, source, label, refused):
    """Both UDP ports of the group from the WAN wire, each read off the
    set-top box's wire. `refused` is the state the flow holds while a filter
    drops TC_DROPPED where it runs in software alone: the kept port still
    arrives, through the CPU, and nothing of the dropped one. None is a flow
    carried again: both arrive whole, and the classifier made them."""
    family = r.multicast_family
    vlan = IPTV_VID if IPTV_TAGGED else None

    def row(state):
        return next((g for g in state['mcast'] if g['group'] == group), None)

    kept = new_config(family, source, group, FRAMING_PORT, [LISTENER])
    other = new_config(family, source, group, TC_DROPPED, [LISTENER])
    before = await r.state()
    async with capture(r.lan, kept) as kept_handle, capture(r.lan, other) as other_handle:
        both = zip(frames(kept, {1: 512}, FRAMING_COUNT, source_mac=r.wan_mac, vlan=vlan),
                   frames(other, {1: 512}, FRAMING_COUNT, source_mac=r.wan_mac, vlan=vlan))
        await asyncio.to_thread(send, [frame for pair in both for frame in pair],
                                r.multicast_send_if)
        await asyncio.sleep(0.5)
    after = await r.state()
    r.record(label, {'kept': kept_handle['result'], 'dropped': other_handle['result'],
                     'before': row(before), 'after': row(after)})
    _assert_bridged_copy(kept_handle['result'][LISTENER], r.wan_mac, group)
    moved = _packets(after, group) - _packets(before, group)
    if refused:
        dropped = other_handle['result'][LISTENER]
        assert not dropped['errors'] and not dropped['seen'], dropped
        assert row(after)['state'] == refused and not moved, (row(before), row(after))
    else:
        _assert_bridged_copy(other_handle['result'][LISTENER], r.wan_mac, group)
        assert moved >= 2 * FRAMING_COUNT * 0.95, (row(before), row(after))


async def test_yields_to_tc(multicast_bridge_service):
    """IPTV bridged from the WAN port to the set-top box, on two UDP ports of
    one (S,G). A tc filter dropping one of them -- where the stream arrives,
    then where the copy leaves -- runs on every frame the bridge forwards in
    software and on none an entry replicates, and the entry's key cannot tell
    the two ports apart. So while it stands the flow is out of hardware and
    says refused-tc, the set-top box receives the kept port and nothing of the
    dropped one, and once it goes the flow is carried again, both ports."""
    r = multicast_bridge_service
    family = r.multicast_family
    group = '239.9.5.7' if family == 4 else 'ff1e::9:5:7'
    source = r.multicast_source
    filtered = set()

    async def window(label, dropping):
        await _split_window(r, group, source, label, 'refused-tc' if dropping else None)

    await _mdb(r, TARGET_LAN_IF, group)
    with Console.target(log_path=str(artifact_dir() / 'multicast-bridge-tc-uart.log')) as con:
        await asyncio.to_thread(con.login, 'root', None)

        async def tc(*argv, check=True):
            # tc is not in the agent's allowlist; the console carries it.
            return await console_command(con, 'tc', *argv, check=check, timeout=30)

        async def place(dev, direction, tagged):
            filtered.add(dev)
            # A killed run's qdisc, if any, goes with whatever it held.
            await tc('qdisc', 'del', 'dev', dev, 'clsact', check=False)
            await tc('qdisc', 'add', 'dev', dev, 'clsact')
            await tc('filter', 'add', 'dev', dev, direction, *_tc_drop(family, tagged))
            # Nothing announces a filter: the refresh finds it.
            await _bridged_row(r, group, lambda g: g['state'] == 'refused-tc')

        async def lift(dev):
            await tc('qdisc', 'del', 'dev', dev, 'clsact')
            filtered.discard(dev)
            await _bridged_row(r, group, lambda g: g['state'] == 'installed')

        try:
            await _from_wan(r, new_config(family, source, group, FRAMING_PORT, []), 16,
                            r.wan_mac)
            await _bridged_row(r, group, lambda g: g['state'] == 'installed')

            # Where the stream arrives: the WAN wire's own framing.
            await place(TARGET_WAN_IF, 'ingress', IPTV_TAGGED)
            await window('multicast-bridge-tc-ingress', dropping=True)
            await lift(TARGET_WAN_IF)
            await window('multicast-bridge-tc-ingress-lifted', dropping=False)

            # Where the copy leaves: tagged, to the set-top box's VLAN.
            await place(TARGET_LAN_IF, 'egress', True)
            await window('multicast-bridge-tc-egress', dropping=True)
            await lift(TARGET_LAN_IF)
            await window('multicast-bridge-tc-egress-lifted', dropping=False)
        finally:
            for dev in sorted(filtered):
                await tc('qdisc', 'del', 'dev', dev, 'clsact', check=False)
            await _mdb(r, TARGET_LAN_IF, group, add=False)
            await r.wait(lambda s: not any(g['group'] == group for g in s['mcast']),
                         timeout=20)


NETDEV_TABLE = 'ask_ft_mc_netdev'


async def test_yields_to_a_netdev_chain(
        multicast_bridge_service):
    """IPTV bridged from the WAN port to the set-top box, on two UDP ports of
    one (S,G). An nftables netdev chain dropping one of them -- at the WAN
    port's ingress hook, then at the set-top box port's egress hook -- sees
    every frame the bridge forwards in software and none an entry replicates,
    and no bridge hook is involved. So while it stands the flow is out of
    hardware and says refused-filter, the set-top box receives the kept port
    and nothing of the dropped one, and once the table goes the flow is
    carried again, both ports. The image's own flowtable sits on the same
    ingress hooks and keeps nothing out."""
    r = multicast_bridge_service
    family = r.multicast_family
    group = '239.9.5.8' if family == 4 else 'ff1e::9:5:8'
    source = r.multicast_source
    placed = False

    async def nft(*argv, check=True):
        return await command(r.target, r.session, 'nft', *argv, check=check)

    async def place(hook, dev):
        nonlocal placed
        placed = True
        # A killed run's table, if any, goes first.
        await nft('delete', 'table', 'netdev', NETDEV_TABLE, check=False)
        await nft('add', 'table', 'netdev', NETDEV_TABLE)
        await nft('add', 'chain', 'netdev', NETDEV_TABLE, hook,
                  f'{{ type filter hook {hook} device "{dev}" priority 0; policy accept; }}')
        await nft('add', 'rule', 'netdev', NETDEV_TABLE, hook,
                  'udp', 'dport', str(TC_DROPPED), 'drop')
        # Nothing announces a chain: the refresh finds it.
        await _bridged_row(r, group, lambda g: g['state'] == 'refused-filter')

    async def lift():
        nonlocal placed
        await nft('delete', 'table', 'netdev', NETDEV_TABLE)
        placed = False
        await _bridged_row(r, group, lambda g: g['state'] == 'installed')

    await _mdb(r, TARGET_LAN_IF, group)
    try:
        await _from_wan(r, new_config(family, source, group, FRAMING_PORT, []), 16, r.wan_mac)
        await _bridged_row(r, group, lambda g: g['state'] == 'installed')
        # The service's flowtable hooks both ports' ingress, and the flow is
        # carried beside it.
        assert (await r.state())['bindings'] > 0

        await place('ingress', TARGET_WAN_IF)
        await _split_window(r, group, source, 'multicast-bridge-netdev-ingress',
                            'refused-filter')
        await lift()
        await _split_window(r, group, source, 'multicast-bridge-netdev-ingress-lifted', None)

        await place('egress', TARGET_LAN_IF)
        await _split_window(r, group, source, 'multicast-bridge-netdev-egress',
                            'refused-filter')
        await lift()
        await _split_window(r, group, source, 'multicast-bridge-netdev-egress-lifted', None)
    finally:
        if placed:
            await nft('delete', 'table', 'netdev', NETDEV_TABLE, check=False)
        await _mdb(r, TARGET_LAN_IF, group, add=False)
        await r.wait(lambda s: not any(g['group'] == group for g in s['mcast']), timeout=20)


# Streams nobody joined, more of them interleaving than a few dedup slots would
# hold, and how many frames of each.
UNWANTED, UNWANTED_ROUNDS = 32, 40
# What else the segment sends the hook in a window: a handful of streams.
BACKGROUND = 16


async def test_unwanted_streams_wake_it_once(
        multicast_bridge_service):
    """Thirty-two streams nobody joined, interleaved at about 1 kpps, while a
    membership of another group keeps the learner's hook registered. Each
    reaches the hook on every frame, and the worker can do nothing with any
    of them. Each is recorded once, and wakes the worker once: slots holding
    only the last few facts would record every one of them on every frame,
    each a pass of the worker taking the transaction. A stream somebody
    joined, arriving among them, is still learned and carried."""
    r = multicast_bridge_service
    family = r.multicast_family
    wanted = '239.9.6.1' if family == 4 else 'ff1e::9:6:1'
    unwanted = [f'239.9.7.{i + 1}' if family == 4 else f'ff1e::9:7:{i + 1:x}'
                for i in range(UNWANTED)]
    source = r.multicast_source
    vlan = IPTV_VID if IPTV_TAGGED else None

    def interleaved(configs, rounds):
        batches = [frames(c, {1: 128}, rounds, source_mac=r.wan_mac, vlan=vlan)
                   for c in configs]
        return [frame for each in zip(*batches) for frame in each]

    await _mdb(r, TARGET_LAN_IF, wanted)
    try:
        await r.wait(lambda s: s['mcast_hooked'] == 1, timeout=10)
        configs = [new_config(family, source, g, FRAMING_PORT, []) for g in unwanted]
        before = await r.state()
        await asyncio.to_thread(send, interleaved(configs, UNWANTED_ROUNDS),
                                r.multicast_send_if, 1000)
        await asyncio.sleep(1)
        after = await r.state()
        delta = {key: after[key] - before[key] for key in after
                 if key.startswith('mcast_') and isinstance(after[key], int)
                 and isinstance(before.get(key), int)}
        r.record('multicast-bridge-unwanted', {'delta': delta, 'before': before, 'after': after})
        # None of our unwanted streams may become a flow. Background traffic
        # can create unrelated entries, including hardware discard entries.
        # The segment's own multicast -- discovery from the office hosts on
        # the WAN side -- reaches the hook too, a few streams' worth; every
        # frame of the 1280 would be news without the slots.
        assert not [g for g in after['mcast'] if g['group'] in unwanted], after['mcast']
        assert delta['mcast_observed'] <= UNWANTED + BACKGROUND, delta
        assert delta['mcast_passes'] <= UNWANTED + BACKGROUND, delta

        # The wanted stream among them: learned and carried, and the others
        # are not news again -- across both runs each stream is recorded at
        # most once. Sent until it is in: its set of slots may be full of
        # facts recorded under an interval ago, which it waits out.
        configs.append(new_config(family, source, wanted, FRAMING_PORT, []))
        deadline = asyncio.get_running_loop().time() + 30
        while True:
            await asyncio.to_thread(send, interleaved(configs, 8), r.multicast_send_if, 1000)
            row = next((g for g in (await r.state())['mcast'] if g['group'] == wanted), None)
            if row and row['state'] == 'installed':
                break
            assert asyncio.get_running_loop().time() < deadline, row
        last = await r.state()
        assert last['mcast_observed'] - before['mcast_observed'] <= \
            UNWANTED + 1 + BACKGROUND, \
            (before['mcast_observed'], after['mcast_observed'], last['mcast_observed'])
    finally:
        await _mdb(r, TARGET_LAN_IF, wanted, add=False)
        await r.wait(lambda s: not any(g['group'] == wanted for g in s['mcast']), timeout=20)


# ---- one stream, bridged and routed ----------------------------------------
#
# The IPTV VLAN bridged to the set-top box and routed to the rest of the house.
# The bridge hands the stream to the host on br-ftmcast.289, where ipmr routes
# it into another VLAN of the LAN port. One classifier key, so one hardware
# group carrying both copies (A188).

ROUTED_VID = 290
ROUTED_LISTENER = 'askftmr'


def _iptv_row(state, group):
    return next((g for g in state['mcast']
                 if g['group'] == group and g['vid'] == str(IPTV_VID)), None)


async def _iptv_group(r, group, ready, timeout=20):
    state = await r.wait(lambda s: (row := _iptv_row(s, group)) is not None and ready(row),
                         timeout=timeout)
    return _iptv_row(state, group)


def _mroute_row(state, group):
    return next((g for g in state['mroute'] if g['group'] == group), None)


def _assert_routed_copy(result, source_mac, group):
    """What ipmr would have sent: the whole stream, once, from the address of
    the device it sends the copy through to the group's, one hop fewer."""
    assert not result['errors'], result
    assert result['seen'].get('1') == list(range(FRAMING_COUNT)), result
    assert result['duplicates'] == 0 and result['fragments'] == 0, result
    assert result['sources'] == [source_mac.lower()], result
    assert result['destinations'] == [multicast_mac(group).hex(':')], result
    assert result['hops'] == [63], result


@asynccontextmanager
async def _bridge_and_route(r, group, routed_on=BRIDGE):
    """IPTV in on the WAN port, bridged to the set-top box on VLAN 289 and
    routed by smcroute from br-ftmcast.289 into VLAN 290 on the same LAN port,
    with the route added. Yields what a case needs to drive it; the stream is
    not offered yet (see _ride()). Everything it set up is taken down on the
    way out, whatever happened.

    `routed_on` is the device VLAN 290 sits on: the bridge, so the routed copy
    crosses it as the bridged one does, or the LAN port itself, a VLAN device
    on a bridge port, whose copy leaves by the port without the bridge."""
    family = r.multicast_family
    # A source that passes the IPv4 source check on the VIF: the WAN segment.
    source = WAN_IP if family == 4 else r.multicast_source
    iptv_dev, routed_dev = f'{BRIDGE}.{IPTV_VID}', f'{routed_on}.{ROUTED_VID}'
    via_bridge = routed_on == BRIDGE
    undo = []
    lan_created = False

    async def run(*argv, reverse=None):
        await command(r.target, r.session, *argv)
        if reverse:
            undo.append(reverse)

    try:
        links = {i['ifname'] for i in json.loads(
            (await command(r.target, r.session, 'ip', '-j', 'link', 'show'))['stdout'])}
        # Where the WAN PVID is not the IPTV VLAN, the bridge has no device on
        # it yet: the host receives nothing of VLAN 289 without one.
        if iptv_dev not in links:
            await run('bridge', 'vlan', 'add', 'dev', BRIDGE, 'vid', str(IPTV_VID), 'self',
                      reverse=['bridge', 'vlan', 'del', 'dev', BRIDGE, 'vid',
                               str(IPTV_VID), 'self'])
            await run('ip', 'link', 'add', 'link', BRIDGE, 'name', iptv_dev, 'type', 'vlan',
                      'id', str(IPTV_VID), reverse=['ip', 'link', 'del', iptv_dev])
            await run('ip', 'link', 'set', iptv_dev, 'up')
        # The LAN the stream is routed into: VLAN 290, tagged on the LAN port.
        if via_bridge:
            await run('bridge', 'vlan', 'add', 'dev', TARGET_LAN_IF, 'vid', str(ROUTED_VID),
                      reverse=['bridge', 'vlan', 'del', 'dev', TARGET_LAN_IF, 'vid',
                               str(ROUTED_VID)])
            await run('bridge', 'vlan', 'add', 'dev', BRIDGE, 'vid', str(ROUTED_VID), 'self',
                      reverse=['bridge', 'vlan', 'del', 'dev', BRIDGE, 'vid',
                               str(ROUTED_VID), 'self'])
        await run('ip', 'link', 'add', 'link', routed_on, 'name', routed_dev, 'type', 'vlan',
                  'id', str(ROUTED_VID), reverse=['ip', 'link', 'del', routed_dev])
        if via_bridge:
            # Keep the routed copy's source distinct from the shared port MAC.
            await run('ip', 'link', 'set', routed_dev, 'address', r.dut_lan_mac)
        await run('ip', 'link', 'set', routed_dev, 'up')
        # The bridge a multicast router: it hands the IPTV stream to the host.
        await run('ip', 'link', 'set', 'dev', BRIDGE, 'type', 'bridge', 'mcast_router', '2',
                  reverse=['ip', 'link', 'set', 'dev', BRIDGE, 'type', 'bridge',
                           'mcast_router', '1'])
        if family == 4:
            # The VIF may carry no address of its own (the tagged-WAN bench),
            # and strict source validation would drop the stream there.
            # Slashes, because the device name has a dot in it.
            for name in ('all', iptv_dev):
                key = f'net/ipv4/conf/{name}/rp_filter'
                old = (await read(r.target, r.session, f'/proc/sys/{key}')).strip()
                await run('sysctl', '-w', f'{key}=0', reverse=['sysctl', '-w', f'{key}={old}'])
        result = await lan_run_python(r.lan, f'''
import subprocess
subprocess.run(['ip','link','add','link',{LAN_NIC!r},'name',{ROUTED_LISTENER!r},'type','vlan','id',{str(ROUTED_VID)!r}],check=True)
subprocess.run(['ip','link','set',{ROUTED_LISTENER!r},'up'],check=True)
''', label='multicast_bridge_routed_listener', timeout=15)
        assert result.rc == 0, result.stdout
        lan_created = True
        # ipmr builds the routed copy's header on the VLAN device it sends it
        # through, and the bridge forwards it unchanged: its source is that
        # device's address. The VLAN over the bridge has its own MAC so the
        # wire check can distinguish it from the physical egress port.
        egress_mac = await dut_mac(r.target, r.session, routed_dev)
        if via_bridge:
            assert egress_mac != await dut_mac(r.target, r.session, TARGET_LAN_IF), (
                'the routed VLAN device shares the egress port\'s address, so the '
                'source-MAC oracle cannot tell the two apart')

        # The set-top box on VLAN 289, and a listener on the routed LAN, whose
        # membership is what sends ipmr's copy out of the LAN port at all
        # when that copy crosses the bridge.
        await _mdb(r, TARGET_LAN_IF, group)
        if via_bridge:
            await run('bridge', 'mdb', 'replace', 'dev', BRIDGE, 'port', TARGET_LAN_IF,
                      'grp', group, 'vid', str(ROUTED_VID), 'permanent',
                      reverse=['bridge', 'mdb', 'del', 'dev', BRIDGE, 'port', TARGET_LAN_IF,
                               'grp', group, 'vid', str(ROUTED_VID)])
        # A VLAN joining a bound port, or a VLAN device going onto one, latches
        # the flowtable's invalidation, and the service answers by replacing
        # its table: two nftables commits, seconds later through the test's
        # nft wrapper. Each takes every routed group's confirmations back, so
        # one landing after the route is ridden sends the stream to Linux in
        # the middle of a window. Admission is ready again only once the
        # replacement is done.
        await wait_service(r, timeout=30)
        async with _daemon(r.target, r.session, [iptv_dev, routed_dev]) as ctl:
            await ctl('add', iptv_dev, source, group, routed_dev)
            yield SimpleNamespace(ctl=ctl, source=source, iptv_dev=iptv_dev,
                                  egress_mac=egress_mac)
    finally:
        failures = []
        # Already gone on the passing path; a failure midway may have left it.
        await command(r.target, r.session, 'bridge', 'mdb', 'del', 'dev', BRIDGE, 'port',
                      TARGET_LAN_IF, 'grp', group, 'vid', str(IPTV_VID), check=False)
        for argv in reversed(undo):
            result = await command(r.target, r.session, *argv, check=False)
            if result['rc']:
                failures.append(result)
        if lan_created:
            result = await lan_run_python(
                r.lan, f"import subprocess\nsubprocess.run(['ip','link','del',{ROUTED_LISTENER!r}],check=True)\n",
                label='multicast_bridge_routed_listener_cleanup', timeout=15)
            if result.rc:
                failures.append(result.stdout)
        await r.wait(lambda s: not any(g['group'] == group for g in s['mcast']), timeout=20)
        assert not failures, failures


async def _ride(r, group, source):
    """Bursts of the stream until the bridged group carries it and the route
    rides that group: the first frames teach the bridged learner the stream,
    and the route rides it only once ipmr has been seen forwarding a copy. A
    burst sent before either learner was watching teaches nothing. Returns
    the bridged row and the routed one. `sent` is how many frames it took."""
    family = r.multicast_family
    sent = 0
    deadline = time.monotonic() + 20
    while True:
        await _from_wan(r, new_config(family, source, group, FRAMING_PORT, []), 16, r.wan_mac)
        sent += 16
        row = _iptv_row(await r.state(), group)
        if row and row['state'] == 'installed' and row['routed'] != '-':
            break
        assert time.monotonic() < deadline, ('never carried with its route', row)
        await asyncio.sleep(0.3)
    state = await r.wait(lambda s: (m := _mroute_row(s, group)) is not None
                         and m['state'] == 'installed', timeout=15)
    return row, _mroute_row(state, group), sent


async def test_bridge_and_route(multicast_bridge_service):
    """IPTV in on the WAN port, bridged to the set-top box on VLAN 289 and
    routed by smcroute from br-ftmcast.289 into VLAN 290 on the same LAN port.

    Both copies come out of one classifier entry. The bridged one keeps the
    sender's MAC and hop count; the routed one leaves with br-ftmcast.290's
    address and one hop fewer, taken off in its own listener entry because the root
    keeps the count for the bridged copy. That per-copy decrement is the part
    no earlier run has measured: a replica sharing its IP header with its
    siblings would show here as 63 on both, or 62 on the routed one. The
    classifier counts the stream and the ingress CPU does not see it, so
    neither copy is Linux's.

    Then each learner lets go of its own half. The box leaving keeps the group
    in hardware for the routed copy alone; the route going retires it."""
    r = multicast_bridge_service
    family = r.multicast_family
    group = '239.9.5.4' if family == 4 else 'ff1e::9:5:4'
    async with _bridge_and_route(r, group) as route:
        source, egress_mac = route.source, route.egress_mac
        row, routed, _ = await _ride(r, group, source)
        assert row['ports'] == f'{TARGET_LAN_IF}/{IPTV_VID}', row
        assert row['routed'] == f'{TARGET_LAN_IF}/{ROUTED_VID}', row
        assert row['in'] == TARGET_WAN_IF and row['smac'] == r.wan_mac.lower(), row
        assert routed['in'] == BRIDGE, routed
        assert routed['listeners'] == f'{TARGET_LAN_IF}/{ROUTED_VID}', routed
        line, mfc_before = await mroute_line(r.target, r.session, family, source, group)
        assert 'offload' in line, line

        result, before, after, cpu, idle = await _window(
            r, group, source, r.lan, [LISTENER, ROUTED_LISTENER],
            lambda c, n: _from_wan(r, c, n, r.wan_mac), TARGET_WAN_IF,
            'multicast-bridge-and-route')
        _assert_bridged_copy(result[LISTENER], r.wan_mac, group)
        _assert_routed_copy(result[ROUTED_LISTENER], egress_mac, group)
        counted = int(_iptv_row(after, group)['packets']) - \
            int(_iptv_row(before, group)['packets'])
        assert counted >= FRAMING_COUNT * 0.95, (before, after)
        assert cpu < FRAMING_COUNT * 0.1, (cpu, idle)
        # ipmr's own counters are the classifier's, folded: a daemon
        # ageing its routes sees the stream flow. Carried on the bridged
        # entry, the count reaches the MFC in two passes, the bridged
        # learner's and then the routed one's, each on its own five-second
        # timer; before that the MFC shows only what Linux forwarded itself.
        grown = await _mfc_packets(r, group, source) - mfc_before
        assert grown >= counted, (mfc_before, grown, counted)

        # The set-top box leaves. The group stays in hardware for the
        # routed copy alone: its root retires only when both are empty.
        await _mdb(r, TARGET_LAN_IF, group, add=False)
        row = await _iptv_group(r, group, lambda g: g['ports'] == '-'
                                and g['state'] == 'installed')
        assert row['routed'] == f'{TARGET_LAN_IF}/{ROUTED_VID}', row
        result, before, after, cpu, idle = await _window(
            r, group, source, r.lan, [LISTENER, ROUTED_LISTENER],
            lambda c, n: _from_wan(r, c, n, r.wan_mac), TARGET_WAN_IF,
            'multicast-route-alone')
        assert not result[LISTENER]['seen'], result
        _assert_routed_copy(result[ROUTED_LISTENER], egress_mac, group)
        assert cpu < FRAMING_COUNT * 0.1, (cpu, idle)

        # And the route goes: nothing names the group, and it retires.
        await route.ctl('remove', route.iptv_dev, source, group)
        await r.wait(lambda s: _iptv_row(s, group) is None
                     and _mroute_row(s, group) is None, timeout=20)


async def test_route_follows_xfrm(multicast_bridge_service):
    """An XFRM block policy on the routed VLAN, under a route riding the bridged group.

    IPv4: ipmr's copy into VLAN 290 takes an output route whose XFRM step the
    policy blocks. The route leaves hardware (refused-xfrm) with nothing but
    the policy changed, and the bridged group it rode, whose VIF would be
    starved without it, stays in software (refused-routed): the set-top box
    receives the stream from the CPU and the routed LAN nothing, as Linux
    decides. The policy gone, the route rides the group again. IPv6: ip6mr's
    copy takes no XFRM step, and the route stays on."""
    r = multicast_bridge_service
    family = r.multicast_family
    group = '239.9.5.7' if family == 4 else 'ff1e::9:5:7'
    ipx = ['ip'] + (['-6'] if family == 6 else [])
    selector = ['src', '0.0.0.0/0' if family == 4 else '::/0',
                'dst', f'{group}/{32 if family == 4 else 128}',
                'dev', f'{BRIDGE}.{ROUTED_VID}', 'dir', 'out']
    policed = False
    async with _bridge_and_route(r, group) as route:
        source, egress_mac = route.source, route.egress_mac
        try:
            await _ride(r, group, source)
            before = await r.state()
            policed = True
            await command(r.target, r.session, *ipx, 'xfrm', 'policy', 'add', *selector,
                          'action', 'block')
            if family == 4:
                ruled = await r.wait(
                    lambda s: (m := _mroute_row(s, group)) is not None
                    and m['state'] == 'refused-xfrm'
                    and (b := _iptv_row(s, group)) is not None
                    and b['state'] == 'refused-routed' and b['routed'] == '-', timeout=20)
                assert ruled['mroute_ruleset_changes'] == before['mroute_ruleset_changes'], \
                    (before, ruled)
            else:
                ruled = await r.wait(
                    lambda s: s['mroute_xfrm_changes'] > before['mroute_xfrm_changes'], timeout=20)
                await asyncio.sleep(1)
            result, _, after, cpu, idle = await _window(
                r, group, source, r.lan, [LISTENER, ROUTED_LISTENER],
                lambda c, n: _from_wan(r, c, n, r.wan_mac), TARGET_WAN_IF,
                'multicast-route-xfrm-policy')
            _assert_bridged_copy(result[LISTENER], r.wan_mac, group)
            if family == 4:
                assert not result[ROUTED_LISTENER]['seen'], result
                assert cpu >= FRAMING_COUNT, (cpu, idle)
            else:
                _assert_routed_copy(result[ROUTED_LISTENER], egress_mac, group)
                assert cpu < FRAMING_COUNT * 0.1, (cpu, idle)
                assert _mroute_row(after, group)['state'] == 'installed', after

            await command(r.target, r.session, *ipx, 'xfrm', 'policy', 'delete', *selector)
            policed = False
            await _ride(r, group, source)
            result, _, _, cpu, idle = await _window(
                r, group, source, r.lan, [LISTENER, ROUTED_LISTENER],
                lambda c, n: _from_wan(r, c, n, r.wan_mac), TARGET_WAN_IF,
                'multicast-route-xfrm-lifted')
            _assert_bridged_copy(result[LISTENER], r.wan_mac, group)
            _assert_routed_copy(result[ROUTED_LISTENER], egress_mac, group)
            assert cpu < FRAMING_COUNT * 0.1, (cpu, idle)
        finally:
            if policed:
                await command(r.target, r.session, *ipx, 'xfrm', 'policy', 'delete', *selector,
                              check=False)
        await route.ctl('remove', route.iptv_dev, source, group)
        await _mdb(r, TARGET_LAN_IF, group, add=False)
        await r.wait(lambda s: _iptv_row(s, group) is None
                     and _mroute_row(s, group) is None, timeout=20)


# Long enough for the bridged learner to sample its entries into the route and
# the routed learner to fold the route into the MFC, each once: both run every
# five seconds.
FOLD_SETTLE = 11


async def _mfc_packets(r, group, source):
    """ipmr's count for the (S,G), with everything the hardware has counted
    folded in: a /proc read folds, after the learners have both had a pass."""
    await asyncio.sleep(FOLD_SETTLE)
    await r.state()
    line, packets = await mroute_line(r.target, r.session, r.multicast_family, source, group)
    assert line, ('no MFC entry', group)
    return packets


async def test_route_count_across_a_bridge_bounce(
        multicast_bridge_service):
    """The route counts on while the bridge goes down and comes back.

    A group routed through a bridge folds the count of the bridged group
    carrying its copies into the MFC entry. The bridge going down makes the
    routed learner let go of it and derive it again, while its route stays
    published and its count runs on. The learner used to take the baseline of
    that count from zero on the re-derivation, and the next fold added
    everything the route had carried to `ip -s mroute` a second time (A225).

    The routed LAN is a VLAN device on the LAN port rather than on the bridge,
    so the copy it takes leaves by the port and the group can be derived
    while the bridge is down: routed back into the bridge, it would be refused
    then and its route taken back, whose count does start again.

    Every frame of the (S,G) is either forwarded by ipmr, which counts it, or
    matched by the classifier and folded in -- once. So from a settled count
    taken with the route carried, the count may grow by no more than the
    frames sent after it, and by at least the window sent once it is carried
    again. UART control remains available while the bridge is down."""
    r = multicast_bridge_service
    family = r.multicast_family
    group = '239.9.5.6' if family == 4 else 'ff1e::9:5:6'
    async with _bridge_and_route(r, group, routed_on=TARGET_LAN_IF) as route:
        source = route.source
        _, routed, _ = await _ride(r, group, source)
        assert routed['in'] == BRIDGE, routed
        assert routed['listeners'] == f'{TARGET_LAN_IF}/{ROUTED_VID}', routed
        result, _, _, _, _ = await _window(
            r, group, source, r.lan, [ROUTED_LISTENER],
            lambda c, n: _from_wan(r, c, n, r.wan_mac), TARGET_WAN_IF,
            'multicast-route-before-bounce')
        _assert_routed_copy(result[ROUTED_LISTENER], route.egress_mac, group)
        settled = await _mfc_packets(r, group, source)
        assert settled >= FRAMING_COUNT, settled

        with Console.target(log_path=str(artifact_dir() / 'multicast-bridge-bounce-uart.log')) as con:
            await asyncio.to_thread(con.login, 'root', None)
            await console_command(con, 'ip', 'link', 'set', BRIDGE, 'down')
            await asyncio.sleep(2)
            await console_command(con, 'ip', 'link', 'set', BRIDGE, 'up')
        # Wait for the forwarding service to recover after the bridge bounce.
        await wait_service(r, timeout=30)

        # Carried again: the stream is learned from its frames once more.
        _, routed, sent = await _ride(r, group, source)
        assert routed['in'] == BRIDGE, routed
        result, _, _, _, _ = await _window(
            r, group, source, r.lan, [ROUTED_LISTENER],
            lambda c, n: _from_wan(r, c, n, r.wan_mac), TARGET_WAN_IF,
            'multicast-route-after-bounce')
        _assert_routed_copy(result[ROUTED_LISTENER], route.egress_mac, group)
        sent += FRAMING_COUNT
        grown = await _mfc_packets(r, group, source) - settled
        r.record('multicast-route-bounce-count',
                 {'settled': settled, 'grown': grown, 'sent': sent})
        assert FRAMING_COUNT <= grown <= sent, (settled, grown, sent)

        # The route goes, and the set-top box with it: the box's static
        # membership alone keeps the bridged group standing.
        await route.ctl('remove', route.iptv_dev, source, group)
        await _mdb(r, TARGET_LAN_IF, group, add=False)
        await r.wait(lambda s: _iptv_row(s, group) is None
                     and _mroute_row(s, group) is None, timeout=20)


# ---- a member port's egress queues change under an installed group --------

EGRESS_RATE, EGRESS_CEIL = '1gbit', '2gbit'


async def _ceetm_unclassified_dequeued(r, dev):
    """What the queue the port's offloaded HTB tree gives traffic naming no
    class has sent: a replica carries no class, so that is where it leaves --
    the default leaf, or the top channel's class queue 0 when there is none.
    ethtool reports that queue as [default] whether or not a leaf holds it.
    tc never sees an accelerated frame, so the CEETM counters are the only
    witness."""
    text = (await command(r.target, r.session, 'ethtool', '-S', dev))['stdout']
    found = re.findall(r'^\s*ceetm dequeued frames \[default\]:\s*(\d+)', text, re.M)
    assert len(found) == 1, text
    return int(found[0])


async def test_follows_egress_queues(multicast_bridge_service):
    """IPTV in tagged on the LAN port, bridged out of the WAN port. An HTB
    offload tree on the WAN port moves it onto CEETM class queues, and every
    listener entry names the queue the port had when it was built -- which
    nothing dequeues any more. The group is rebuilt in place: the replicas
    keep arriving whole, the classifier keeps carrying them, and they leave by
    the tree, on the queue it gives traffic naming no class. Taking the tree
    away rebuilds it again.

    Each HTB command reaches the adapter's egress hook, which counts the
    change and marks every installed flow whose recorded chain copies out of
    the port (`mcast_egress_rebuilds`, which the waits below read); the
    bridged learner's worker then replaces the chain under the same key."""
    r = multicast_bridge_service
    group = '239.9.5.5' if r.multicast_family == 4 else 'ff1e::9:5:5'
    source = LAN_SOURCE[r.multicast_family]
    capture_if = r.multicast_send_if
    tree = False
    await _mdb(r, TARGET_WAN_IF, group)
    with Console.target(log_path=str(artifact_dir() / 'multicast-egress-uart.log')) as con:
        await asyncio.to_thread(con.login, 'root', None)

        async def tc(*argv, check=True):
            # tc is not in the agent's allowlist; the console carries it.
            return await console_command(con, 'tc', *argv, check=check, timeout=30)

        async def window(label):
            result, before, after, cpu, idle = await _window(
                r, group, source, None, [capture_if],
                lambda c, n: _from_lan(r, c, n), TARGET_LAN_IF, label)
            _assert_bridged_copy(result[capture_if], r.lan_mac, group, WAN_WIRE_VID)
            assert _packets(after, group) - _packets(before, group) >= \
                FRAMING_COUNT * 0.95, (before, after)
            assert cpu < FRAMING_COUNT * 0.1, (cpu, idle)

        async def root():
            shown = (await tc('qdisc', 'show', 'dev', TARGET_WAN_IF, 'root'))['stdout']
            match = re.search(r'qdisc (\S+) (\S+) root', shown)
            assert match, shown
            return match[1], match[2]

        try:
            # The port's own root qdisc is the one the kernel attached, handle
            # 0:, which an added root replaces without a delete and which
            # deleting the tree gives back. A configured one would be lost, so
            # the test does not start over one.
            original = await root()
            assert original[1] == '0:', ('a configured root qdisc on the WAN port', original)
            await _from_lan(r, new_config(r.multicast_family, source, group,
                                          FRAMING_PORT, []), 16)
            await _bridged_row(r, group, lambda g: g['state'] == 'installed')
            rebuilds = (await r.state())['mcast_egress_rebuilds']

            await tc('qdisc', 'add', 'dev', TARGET_WAN_IF, 'root', 'handle', '1:',
                     'htb', 'offload')
            tree = True
            await tc('class', 'add', 'dev', TARGET_WAN_IF, 'parent', '1:',
                     'classid', '1:10', 'htb', 'rate', EGRESS_RATE, 'ceil', EGRESS_CEIL)
            await tc('class', 'add', 'dev', TARGET_WAN_IF, 'parent', '1:10',
                     'classid', '1:100', 'htb', 'rate', EGRESS_RATE,
                     'ceil', EGRESS_CEIL, 'prio', '0')
            state = await r.wait(lambda s: s['mcast_egress_rebuilds'] > rebuilds,
                                 timeout=15)
            await _bridged_row(r, group, lambda g: g['state'] == 'installed')
            dequeued = await _ceetm_unclassified_dequeued(r, TARGET_WAN_IF)
            await window('multicast-egress-htb')
            assert await _ceetm_unclassified_dequeued(r, TARGET_WAN_IF) - dequeued \
                >= FRAMING_COUNT, 'the replicas did not leave by the offloaded tree'

            rebuilds = state['mcast_egress_rebuilds']
            await tc('qdisc', 'del', 'dev', TARGET_WAN_IF, 'root')
            tree = False
            assert await root() == original, 'the kernel did not give the port its root back'
            await r.wait(lambda s: s['mcast_egress_rebuilds'] > rebuilds, timeout=15)
            await _bridged_row(r, group, lambda g: g['state'] == 'installed')
            await window('multicast-egress-plain')
        finally:
            if tree:
                await tc('qdisc', 'del', 'dev', TARGET_WAN_IF, 'root', check=False)
            await _mdb(r, TARGET_WAN_IF, group, add=False)
            await r.wait(lambda s: not any(g['group'] == group for g in s['mcast']),
                         timeout=20)
