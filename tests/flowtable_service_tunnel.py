"""Recover persistent 6o4/4o6 connections after device loss and slab failures."""
from __future__ import annotations

import asyncio
import json
import os
import socket
import time

import pytest
import pytest_asyncio

from ask_orch.client import Agent
from ask_orch.uart import Console
from _topology import LAN_NIC, TARGET_LAN_IF, TARGET_WAN_IF, lan_run_python
from _flowtable_connections import (by_key, peer)
from _flowtable_failslab import (same_service, slab_fault)
from _flowtable_rig import (artifact_dir, DPORT, Echo, WAN_IP, command, console_command, read)
from _flowtable_selective_neighbour import (keys, unchanged, warm)
from _flowtable_service import (FIRST, managed_service, supervision_status)
from _flowtable_service_vlan import (attempts, balanced, denied)
from _flowtable_tcp import (software_tx)
from _flowtable_tunnel import (
    Capture,
    Shape,
    _assert_outer,
    _assert_tunnel,
    _tunnel_text,
    _upload_refused,
)


async def create_tunnel(r, agent):
    shape = r.shape
    local, remote = shape.outer if agent is r.target else tuple(reversed(shape.outer))
    inner = shape.inner_dut if agent is r.target else shape.inner_orch
    family = ['-6'] if shape.mode == '4o6' else []
    await command(agent, r.session, 'ip', *family, 'tunnel', 'add', shape.device, 'mode',
                  'sit' if shape.mode == '6o4' else 'ipip6', 'local', local, 'remote', remote,
                  'ttl' if shape.mode == '6o4' else 'hoplimit', '64')
    await command(agent, r.session, 'ip', 'link', 'set', shape.device, 'up', 'mtu', str(shape.mtu))
    await command(agent, r.session, 'ip', 'addr', 'add', inner + ('/64' if shape.family == 6 else '/24'),
                  'dev', shape.device, *(['nodad'] if shape.family == 6 else []))
    return json.loads((await command(agent, r.session, 'ip', '-j', 'link', 'show', 'dev', shape.device))['stdout'])[0]['ifindex']


@pytest_asyncio.fixture(params=['6o4', '4o6'])
async def tunnel_service(rig, request):
    r = rig
    r.wan = wan = Agent('wan', f"http://{os.environ.get('ASK_WAN_IP', '127.0.0.1')}:9110")
    r.shape = shape = Shape(request.param, FIRST, DPORT)
    shape.device = 'askft' + shape.mode
    dut_ip = next(a['local'] for i in json.loads((await command(r.target, r.session, 'ip', '-j', '-4', 'addr', 'show', 'dev', TARGET_WAN_IF))['stdout'])
                  for a in i['addr_info'] if a['family'] == 'inet')
    if shape.mode == '6o4':
        shape.outer = (dut_ip, WAN_IP)
        shape.inner_dut, shape.inner_orch = 'fd42:6173:7:3::1', 'fd42:6173:7:3::2'
        r.tunnel_source, gateway = 'fd42:6173:7:1::2', 'fd42:6173:7:1::1'
        network = 'fd42:6173:7:3::/64'
    else:
        shape.outer = ('fd42:6173:7:2::1', 'fd42:6173:7:2::2')
        shape.inner_dut, shape.inner_orch = '198.18.100.1', '198.18.100.2'
        r.tunnel_source, gateway = '198.18.101.2', r.lan_gateway
        network = '198.18.100.0/24'
    cleanup, lan_cleanup, transport = [], [], None
    for agent in (r.target, wan):
        links = json.loads((await command(agent, r.session, 'ip', '-j', 'link', 'show'))['stdout'])
        assert not any(i['ifname'] == shape.device for i in links), links
        for prefix in (network, r.tunnel_source + ('/128' if shape.family == 6 else '/32')):
            routes = json.loads((await command(agent, r.session, 'ip', '-j', '-6' if ':' in prefix else '-4',
                                               'route', 'show', 'table', 'all', 'root', prefix))['stdout'])
            assert not routes, routes

    async def change(agent, argv, undo):
        await command(agent, r.session, *argv)
        cleanup.append((agent, undo))

    async def lan_change(argv, undo):
        result = await lan_run_python(r.lan, f'import subprocess\nsubprocess.run({argv!r},check=True)\n',
                                      label='tunnel_recovery_setup', timeout=15)
        assert result.rc == 0, result.stdout
        lan_cleanup.append(undo)

    try:
        previous = (await read(r.target, r.session, '/proc/sys/net/ipv6/conf/all/forwarding')).strip()
        await change(r.target, ['sysctl', '-w', 'net.ipv6.conf.all.forwarding=1'], ['sysctl', '-w', 'net.ipv6.conf.all.forwarding=' + previous])
        if shape.family == 6:
            await change(r.target, ['ip', '-6', 'addr', 'add', gateway + '/64', 'dev', TARGET_LAN_IF, 'nodad'],
                         ['ip', '-6', 'addr', 'del', gateway + '/64', 'dev', TARGET_LAN_IF])
            await lan_change(['ip', '-6', 'addr', 'add', r.tunnel_source + '/64', 'dev', LAN_NIC, 'nodad'],
                             ['ip', '-6', 'addr', 'del', r.tunnel_source + '/64', 'dev', LAN_NIC])
            await lan_change(['ip', '-6', 'neigh', 'add', gateway, 'lladdr', r.dut_lan_mac, 'nud', 'permanent', 'dev', LAN_NIC],
                             ['ip', '-6', 'neigh', 'del', gateway, 'dev', LAN_NIC])
            await change(r.target, ['ip', '-6', 'neigh', 'add', r.tunnel_source, 'lladdr', r.lan_mac, 'nud', 'permanent', 'dev', TARGET_LAN_IF],
                         ['ip', '-6', 'neigh', 'del', r.tunnel_source, 'dev', TARGET_LAN_IF])
        else:
            await lan_change(['ip', 'addr', 'add', r.tunnel_source + '/32', 'dev', 'lo'],
                             ['ip', 'addr', 'del', r.tunnel_source + '/32', 'dev', 'lo'])
            # No MTU of its own: the download arrives in the tunnel, and a
            # path back to the LAN smaller than that could deliver would keep
            # its UDP direction in Linux too.
            await change(r.target, ['ip', 'route', 'add', r.tunnel_source + '/32', 'via', r.lan_ip, 'dev', TARGET_LAN_IF],
                         ['ip', 'route', 'del', r.tunnel_source + '/32', 'via', r.lan_ip, 'dev', TARGET_LAN_IF])
            for agent, address, other, mac, dev in [
                (r.target, shape.outer[0], shape.outer[1], r.wan_mac, TARGET_WAN_IF),
                (wan, shape.outer[1], shape.outer[0], r.dut_wan_mac, r.wan_if),
            ]:
                await change(agent, ['ip', '-6', 'addr', 'add', address + '/64', 'dev', dev, 'nodad'],
                             ['ip', '-6', 'addr', 'del', address + '/64', 'dev', dev])
                await change(agent, ['ip', '-6', 'neigh', 'add', other, 'lladdr', mac, 'nud', 'permanent', 'dev', dev],
                             ['ip', '-6', 'neigh', 'del', other, 'dev', dev])
        prefix = '/128' if shape.family == 6 else '/32'
        family = ['-6'] if shape.family == 6 else []
        await lan_change(['ip', *family, 'route', 'add', shape.inner_orch + prefix, 'via', gateway, 'dev', LAN_NIC],
                         ['ip', *family, 'route', 'del', shape.inner_orch + prefix, 'via', gateway, 'dev', LAN_NIC])
        # A missing device must not send the unencapsulated destination through
        # a default route. The connected tunnel route wins while it exists.
        await change(r.target, ['ip', *family, 'route', 'add', 'blackhole', network, 'metric', '32760'],
                     ['ip', *family, 'route', 'del', 'blackhole', network, 'metric', '32760'])
        for agent in (r.target, wan):
            await command(agent, r.session, 'modprobe', 'sit' if shape.mode == '6o4' else 'ip6_tunnel')
            cleanup.append((agent, ['ip', 'link', 'del', shape.device]))
            await create_tunnel(r, agent)
        await change(wan, ['ip', *family, 'route', 'add', r.tunnel_source + prefix, 'dev', shape.device],
                     ['ip', *family, 'route', 'del', r.tunnel_source + prefix, 'dev', shape.device])
        echo = Echo()
        echo.received = r.echo.received
        transport, _ = await asyncio.get_running_loop().create_datagram_endpoint(lambda: echo,
            local_addr=(shape.inner_orch, DPORT), family=socket.AF_INET6 if shape.family == 6 else socket.AF_INET)
        async with managed_service(r, extra_paths=[(r.tunnel_source, shape.inner_orch)]):
            yield r
    finally:
        if transport:
            transport.close()
        failures = []
        with Console.target(log_path=str(artifact_dir() / 'service-tunnel-cleanup-uart.log')) as con:
            await asyncio.to_thread(con.login, 'root', None)
            for agent, argv in reversed(cleanup):
                try:
                    if agent is r.target:
                        await console_command(con, *argv)
                    else:
                        await command(agent, r.session, *argv)
                except Exception as error:
                    failures.append(repr(error))
        for argv in reversed(lan_cleanup):
            try:
                result = await lan_run_python(r.lan, f'import subprocess\nsubprocess.run({argv!r},check=True)\n',
                                              label='tunnel_recovery_cleanup', timeout=15)
                assert result.rc == 0, result.stdout
            except Exception as error:
                failures.append(repr(error))
        assert not failures, failures


def flows_for(r, protocol='tcp'):
    endpoint = {'lan': r.tunnel_source, 'connect_ip': r.shape.inner_orch}
    flows = [
        {'id': 0, 'proto': 'udp', 'sport': FIRST, 'lan': r.lan_ip},
        {'id': 1, 'proto': 'tcp', 'sport': FIRST, 'lan': r.lan_ip},
        {'id': 2, 'proto': 'udp', 'sport': FIRST, **endpoint},
        {'id': 3, 'proto': 'tcp', 'sport': FIRST, **endpoint},
        {'id': 4, 'proto': protocol, 'sport': FIRST + 1, **endpoint},
        {'id': 5, 'proto': 'udp', 'sport': FIRST + 2, 'lan': r.lan_ip},
        {'id': 6, 'proto': 'udp', 'sport': FIRST + 2, **endpoint},
    ]
    # A UDP upload stays in Linux: the LAN port can deliver a full frame and
    # the tunnel's path is smaller (see test_flowtable_tunnel).
    for flow in flows[2:]:
        if _upload_refused(r.shape, flow['proto']):
            flow['software'] = (TARGET_LAN_IF,)
    return flows


async def hardware(r, p, label, flows):
    before, tx = await r.state(), await software_tx(r)
    forwarded = await r.software_forwarded()
    async with Capture(r, label) as capture:
        reports = await p.batch(list(range(len(flows))), count=256, interval=0.03125)
    after, tx_after = await r.state(), await software_tx(r)
    # A 4o6 UDP upload Linux keeps re-offers its flow about once a second. Its
    # refusal is decided before RTNL and the installed direction's offer is
    # answered without it, so nothing here should take RTNL at all; busy
    # moving names an offer that did.
    assert after['busy'] == before['busy'], ('an offer took RTNL mid-window', before, after)
    unchanged(before, after, list(range(len(flows))), flows)
    assert (before['installs'], before['deletes']) == (after['installs'], after['deletes'])
    old, new = by_key(before), by_key(after)
    for ident in range(len(flows)):
        for key in keys([ident], flows):
            delta = int(new[key]['packets']) - int(old[key]['packets'])
            assert delta >= (256 if flows[ident]['proto'] == 'udp' else reports[ident]['bytes'] // 1500), (key, delta)
        if ident >= 2:
            rows = [new[key] for key in keys([ident], flows)]
            _assert_tunnel(r, next((f for f in rows if f['in'] == TARGET_LAN_IF), None),
                           next(f for f in rows if f['in'] == TARGET_WAN_IF))
    tx_delta = {dev: tx_after[dev] - tx[dev] for dev in tx}
    # The slow-path counter sits on the forward hook, which a direction
    # Linux keeps by design bypasses through the software flowtable.
    slow_path = await r.software_forwarded() - forwarded
    assert 0 <= slow_path <= 64, slow_path
    assert after['tunnel_records'] == after['tunnel_slots'] == 1, after
    record = after['tunnels'][0]
    assert record['dev'] == r.shape.device and record['tnl'] == _tunnel_text(r.shape), record
    # One reference per tunnel direction in hardware.
    assert int(record['refs']) == len(keys(range(2, len(flows)), flows)), record
    _assert_outer(r, capture.packets(), 256)
    r.record(label, {'before': before, 'after': after, 'reports': reports, 'software_tx': tx_delta,
                     'software_forwarded': slow_path})
    return after


async def negative(r, p):
    for ident in (5, 6):
        await denied(r, p, ident)


async def test_recreated(tunnel_service):
    r, flows = tunnel_service, flows_for(tunnel_service)
    # Removing an IPv4 nexthop conservatively retires all native flow
    # generations. A 4o6 device owns one; a 6o4 device owns only IPv6 routes.
    # Bindings and established sockets must survive in either case.
    selective = r.shape.mode == '6o4'
    service = await supervision_status(r)
    async with peer(r, flows, initial_ids=[0, 1, 2, 3, 5, 6], lease=360, listen_addresses=[r.shape.inner_orch]) as p:
        await warm(r, p, [0, 1, 2, 3], 'tunnel-baseline', flows[:4])
        initial = await hardware(r, p, 'tunnel-baseline-hardware', flows[:4])
        initial_attempts = await attempts(r)
        for cycle in range(2):
            label = f'{r.shape.mode}-recreate-{cycle}'
            await negative(r, p)
            before = await r.state()
            old_index = int(before['tunnels'][0]['ifindex'])
            await p.rpc('start', [2], count=0, interval=0.05, allow_loss=True)
            await p.rpc('start', [3], count=0, interval=0.05)
            started = time.monotonic()
            await console_command(r.service_console, 'ip', 'link', 'del', r.shape.device)
            try:
                retired = await r.wait(lambda s: by_key(s).keys() == (keys([0, 1], flows) if selective else set())
                                       and s['tunnel_records'] == s['tunnel_slots'] == 0, timeout=5)
                retire_seconds = time.monotonic() - started
                assert retire_seconds < 5
                if selective:
                    unchanged(initial, retired, [0, 1], flows)
                assert retired['link_invalidations'] == before['link_invalidations'] + (2 if selective else 4)
                await warm(r, p, [0, 1], label + '-controls', flows[:2])
                controls = await r.state()
                await asyncio.sleep(0.2)
                stopped = await p.rpc('status')
                held = time.monotonic()
                while time.monotonic() - held < 6:
                    await p.batch([0, 1], count=32, interval=0.01)
                    progress = await p.rpc('status')
                    assert not progress['errors'], progress
                    assert all(progress['received'][str(i)] == stopped['received'][str(i)] for i in (2, 3)), progress
                    state = await r.state()
                    unchanged(controls, state, [0, 1], flows)
                    assert by_key(state).keys() == keys([0, 1], flows)
                    assert state['tunnel_records'] == state['tunnel_slots'] == 0
                links = json.loads((await command(r.target, r.session, 'ip', '-j', 'link', 'show'))['stdout'])
                assert not any(link['ifname'] == r.shape.device for link in links)
                assert await attempts(r) == initial_attempts
                r.record(label + '-absent', {'state': retired, 'seconds': retire_seconds})
            finally:
                new_index = await create_tunnel(r, r.target)
                assert new_index != old_index
            restored = time.monotonic()
            while True:
                await p.batch([0, 1], count=32, interval=0.01)
                progress = await p.rpc('status')
                assert not progress['errors'], progress
                if progress['received']['2'] >= stopped['received']['2'] + 16 and progress['received']['3'] > stopped['received']['3']:
                    break
                assert time.monotonic() - restored < 25, progress
            transfers = await p.rpc('stop', [2, 3])
            assert transfers['2']['lost'] > 0 and transfers['3']['lost'] == 0, transfers
            await warm(r, p, [0, 1, 2, 3], label + '-readmitted', flows[:4])
            ready_seconds = time.monotonic() - restored
            assert ready_seconds < 25
            after = await hardware(r, p, label + '-hardware', flows[:4])
            hardware_seconds = time.monotonic() - restored
            assert hardware_seconds < 45
            assert int(after['tunnels'][0]['ifindex']) == new_index
            balanced(after, initial['errors'])
            if selective:
                unchanged(initial, after, [0, 1], flows)
            assert after['rearms'] == initial['rearms']
            if selective:
                moved = len(keys([2, 3], flows))
                assert (after['installs'], after['deletes']) == (before['installs'] + moved, before['deletes'] + moved)
            else:
                # Recreating the nexthop can retire controls again. All
                # displaced directions must balance and re-enter hardware.
                assert after['installs'] - before['installs'] == after['deletes'] - before['deletes'] >= \
                    len(keys([0, 1, 2, 3], flows))
            # The hardware proof's identities and busy count held for its
            # whole burst, longer than a health-check period: nothing
            # reapplied the policy since.
            assert await attempts(r) == initial_attempts
            await same_service(r, service)
            r.record(label + '-recovery', {'old_ifindex': old_index, 'new_ifindex': new_index,
                'ready_seconds': ready_seconds, 'hardware_seconds': hardware_seconds,
                'before': before, 'after': after, 'transfers': transfers})
        await p.rpc('open', [4])
        await warm(r, p, [0, 1, 2, 3, 4], 'tunnel-new-connection', flows[:5])
        await hardware(r, p, 'tunnel-new-hardware', flows[:5])
        await negative(r, p)


@pytest.mark.parametrize('protocol', ['udp', 'tcp'])
async def test_failslab(tunnel_service, protocol):
    r, flows = tunnel_service, flows_for(tunnel_service, protocol)
    service = await supervision_status(r)
    label = f'{r.shape.mode}-slab-{protocol}'
    async with peer(r, flows, initial_ids=[0, 1, 2, 3, 5, 6], lease=200, listen_addresses=[r.shape.inner_orch]) as p:
        await warm(r, p, [0, 1, 2, 3], label + '-baseline', flows[:4])
        initial = await hardware(r, p, label + '-before', flows[:4])
        initial_attempts = await attempts(r)
        await negative(r, p)
        async with slab_fault(r, 'hardware', label, keep_alive=(p, [0, 1, 2, 3])) as fault:
            started = time.monotonic()
            await p.rpc('open', [4])
            await p.batch([4], count=32, interval=0.01)
            hit = await fault.hit()
            await warm(r, p, [0, 1, 2, 3, 4], label + '-readmitted', flows[:5])
            ready_seconds = time.monotonic() - started
            assert ready_seconds < 25
            after = await hardware(r, p, label + '-hardware', flows[:5])
            hardware_seconds = time.monotonic() - started
            assert hardware_seconds < 45
            unchanged(initial, after, [0, 1, 2, 3], flows)
            assert after['rearms'] == initial['rearms'] and after['errors'] == initial['errors']
            assert await attempts(r) == initial_attempts
            await negative(r, p)
            await same_service(r, service)
            r.record(label + '-recovery', {'before': initial, 'after': after, 'hit': hit,
                'ready_seconds': ready_seconds, 'hardware_seconds': hardware_seconds})
