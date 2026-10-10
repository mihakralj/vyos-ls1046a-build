"""Shared support for flowtable service multicast."""

from __future__ import annotations

import asyncio
import socket
import struct
import time

import pytest
import pytest_asyncio
from _flowtable_connections import peer
from _flowtable_failslab import same_service, slab_fault
from _flowtable_rig import (
    WAN_IP,
    command,
    console_command,
    console_json,
    console_python,
    read,
)
from _flowtable_selective_neighbour import hardware, unchanged, warm
from _flowtable_service import (
    CONF,
    DAEMON,
    FAULT_DIR,
    FLOWS,
    INIT,
    managed_service,
    supervision_status,
    wait_service,
)
from _flowtable_service_vlan import attempts, balanced, denied
from _topology import LAN_NIC, TARGET_LAN_IF, TARGET_WAN_IF

IDENTITY = 'ask-recovery-mcast'
PORT = 49401
GROUPS = {4: ['239.9.4.1', '239.9.4.2'], 6: ['ff1e::9:4:1', 'ff1e::9:4:2']}
SOURCE6, DUT6 = 'fd42:6173:8:2::2', 'fd42:6173:8:2::1'


@pytest_asyncio.fixture(params=[4, 6])
async def multicast_service(rig, request):
    r = rig
    r.multicast_family = request.param
    r.multicast_kind, r.multicast_listener = 'mroute', LAN_NIC
    r.multicast_groups = GROUPS[request.param]
    r.multicast_source = SOURCE6 if request.param == 6 else WAN_IP
    r.multicast_window = 0
    cleanup = []
    before = await r.state()
    r.multicast_initial = before
    assert before['mroute_groups'] == before['mroute_installed'] == 0, before
    try:
        if request.param == 6:
            old = (await read(r.target, r.session, '/proc/sys/net/ipv6/conf/all/forwarding')).strip()
            await command(r.target, r.session, 'sysctl', '-w', 'net.ipv6.conf.all.forwarding=1')
            cleanup.append(['sysctl', '-w', 'net.ipv6.conf.all.forwarding=' + old])
            await command(r.target, r.session, 'ip', '-6', 'addr', 'add', DUT6 + '/64', 'dev', TARGET_WAN_IF, 'nodad')
            cleanup.append(['ip', '-6', 'addr', 'del', DUT6 + '/64', 'dev', TARGET_WAN_IF])
        async with managed_service(r):
            started = False
            try:
                # A separate instance and exact PID ownership. MRT_INIT itself
                # refuses a competing owner; never kill another routing daemon.
                conf = FAULT_DIR + '/smcroute.conf'
                result = await r.target.fs_write(r.session, conf,
                    f'phyint {TARGET_WAN_IF} enable\nphyint {TARGET_LAN_IF} enable\n')
                assert result['errno'] == 0, result
                await console_python(r.service_console, f'''
from pathlib import Path
import subprocess
root=Path({FAULT_DIR!r})
assert not Path('/var/run/' + {IDENTITY!r} + '.pid').exists()
assert not Path('/var/run/' + {IDENTITY!r} + '.sock').exists()
with (root/'smcroute.log').open('w') as log:
 p=subprocess.Popen(['smcrouted','-n','-N','-i',{IDENTITY!r},'-f',{conf!r},'-l','notice'],
                    stdin=subprocess.DEVNULL,stdout=log,stderr=log,start_new_session=True)
(root/'smcroute.pid').write_text(str(p.pid))
''')
                started = True
                await asyncio.sleep(2)
                result = await command(r.target, r.session, 'smcroutectl', '-i', IDENTITY, 'show', 'interfaces')
                r.record('multicast-interfaces', result)
                yield r
            finally:
                if started:
                    await console_command(r.service_console, 'smcroutectl', '-i', IDENTITY, 'kill', check=False)
                    await asyncio.sleep(1)
                    state = await r.wait(lambda s: s['mroute_groups'] == s['mroute_installed'] == 0, timeout=8)
                    r.record('multicast-drained', state)
                for argv in reversed(cleanup):
                    await console_command(r.service_console, *argv)
                cleanup.clear()
    finally:
        # An early configuration error can happen before managed_service owns
        # UART; these changes remain reachable over the unmodified IPv4 path.
        for argv in reversed(cleanup):
            await command(r.target, r.session, *argv)


async def route(r, group, add=True):
    if r.multicast_kind == 'mcast':
        result = await command(r.target, r.session, 'bridge', 'mdb', 'replace' if add else 'del',
                               'dev', r.multicast_bridge, 'port', TARGET_LAN_IF, 'grp', group,
                               'vid', str(r.multicast_vlan), *(['permanent'] if add else []))
        if add:
            # Membership grants permission; real traffic supplies its source
            # and physical ingress. A static MDB contains neither of those.
            window = r.multicast_window
            r.multicast_window += 1
            await asyncio.to_thread(send, r, [group], window, 16)
        return result
    return await command(r.target, r.session, 'smcroutectl', '-i', IDENTITY,
                         'add' if add else 'remove', TARGET_WAN_IF, r.multicast_source, group,
                         *([TARGET_LAN_IF] if add else []))


def row(state, group, kind='mroute'):
    rows = [g for g in state[kind] if g['group'] == group]
    assert len(rows) <= 1, rows
    return rows[0] if rows else None


async def wait_group(r, group, installed):
    def ready(state):
        current = row(state, group, r.multicast_kind)
        return (current and current['state'] == 'installed') if installed else (current is None or current['state'] != 'installed')
    if not installed:
        return await r.wait(ready, timeout=12)
    deadline = time.monotonic() + 12
    while True:
        state = await r.state()
        if ready(state):
            return state
        if time.monotonic() > deadline:
            pytest.fail(f'{group} never installed: {row(state, group, r.multicast_kind)}')
        await offer(r, group)


async def offer(r, group):
    """A few frames of the group, in a window of their own. Both learners need
    them: a bridged flow is learned from its frames, and a routed group is
    carried only once Linux has been seen forwarding it to every oif."""
    window = r.multicast_window
    r.multicast_window += 1
    await asyncio.to_thread(send, r, [group], window, 4)
    await asyncio.sleep(0.2)


async def offering(r, group):
    """Offer the group until cancelled, for a wait that is not on its state."""
    while True:
        await offer(r, group)


def send(r, groups, window, count):
    from scapy.all import IP, UDP, Dot1Q, Ether, IPv6, Raw, conf
    frames = []
    for group in groups:
        if ':' in group:
            raw = socket.inet_pton(socket.AF_INET6, group)
            mac = '33:33:' + ':'.join(f'{b:02x}' for b in raw[-4:])
            network = IPv6(src=r.multicast_source, dst=group, hlim=64)
        else:
            octets = [int(v) for v in group.split('.')]
            mac = '01:00:5e:%02x:%02x:%02x' % (octets[1] & 127, octets[2], octets[3])
            network = IP(src=r.multicast_source, dst=group, ttl=64)
        ethernet = Ether(src=r.wan_mac, dst=mac)
        if getattr(r, 'multicast_wire_vlan', None):
            ethernet /= Dot1Q(vlan=r.multicast_wire_vlan)
        frames.append(ethernet / network / UDP(sport=PORT, dport=PORT))
    # Bridge multicast tests inject Ethernet directly on the wire;
    # the orchestrator's own bridge snooping must not decide its delivery.
    sock = conf.L2socket(iface=getattr(r, 'multicast_send_if', r.wan_if))
    try:
        for sequence in range(count):
            payload = b'ASKMCv1:' + struct.pack('!II', window, sequence) + b'x' * 496
            for frame in frames:
                sock.send(frame / Raw(payload))
            time.sleep(0.005)
    finally:
        sock.close()


async def transfer(r, p, label, target=True, probe_target=True):
    groups = r.multicast_groups
    before = await r.state()
    window = r.multicast_window
    r.multicast_window += 1
    await asyncio.gather(asyncio.to_thread(send, r, groups if probe_target else groups[1:], window, 256),
                         p.batch([0, 1], count=128, interval=0.01))
    await asyncio.sleep(0.2)
    after = await r.state()
    results = {}
    for i, group in enumerate(groups):
        result = await p.rpc('multicast', changes={'action': 'status', 'group': group})
        assert not result['errors'], result
        sample = result['windows'].get(str(window), {'received': 0})
        results[group] = sample
        r.record(label, {'before': before, 'after': after, 'received': results})
        if i == 1 or target:
            assert sample['received'] == 256 and sample['duplicates'] == 0, sample
            assert sample['hops'] == [64 if r.multicast_kind == 'mcast' else 63] and sample['sources'] == [r.multicast_source], sample
            current, previous = row(after, group, r.multicast_kind), row(before, group, r.multicast_kind)
            assert current and previous and current['state'] == previous['state'] == 'installed'
            assert int(current['packets']) - int(previous['packets']) == 256, (previous, current)
            # Reading proc above folds hardware statistics into Linux's MFC.
            argv = (['bridge', 'mdb', 'show', 'dev', r.multicast_bridge] if r.multicast_kind == 'mcast' else
                    ['ip', '-6' if r.multicast_family == 6 else '-4', '-s', 'mroute', 'show'])
            table = (await command(r.target, r.session, *argv))['stdout']
            assert any(group in line and 'offload' in line for line in table.splitlines()), table
        else:
            assert sample['received'] == 0, sample
            current = row(after, group, r.multicast_kind)
            # smcrouted may install a negative MFC after traffic requests a
            # withdrawn route. It has no listeners and must stay in software.
            # A bridged stream nobody wants any more is dropped in hardware.
            assert current is None or (
                current['state'] == 'discarding' and current['ports'] == '-'
                if r.multicast_kind == 'mcast' else
                current['state'] == 'refused-listener' and current['listeners'] == '-'), after
    r.record(label, {'before': before, 'after': after, 'received': results})
    return after


async def recover(r, fault):
    flows = [{**f, 'lan': r.lan_ip} for f in FLOWS]
    service = await supervision_status(r)
    async with peer(r, flows, initial_ids=[0, 1, 3], lease=300) as p:
        await warm(r, p, [0, 1], 'multicast-control-warm', flows[:2])
        initial = await hardware(r, p, 'multicast-controls', flows[:2])
        initial_attempts = await attempts(r)
        for group in r.multicast_groups:
            await p.rpc('multicast', changes={'action': 'join', 'group': group, 'iface': r.multicast_listener, 'port': PORT})
            await route(r, group)
            await wait_group(r, group, True)
        await transfer(r, p, 'multicast-baseline')
        target, control = r.multicast_groups
        for cycle in range(2 if fault == 'withdrawal' else 1):
            label = f'{r.multicast_kind}-{r.multicast_family}-{fault}-{cycle}'
            before = await r.state()
            # Timings start once a fault is armed: staging its guard over the
            # console takes tens of seconds, none of them the datapath's.
            started = time.monotonic()
            if fault == 'delete-event-failslab':
                async with slab_fault(r, 'mroute-event', label, keep_alive=(p, [0, 1])) as injection:
                    started = time.monotonic()
                    await route(r, target, False)
                    hit = await injection.hit()
                    retired = await wait_group(r, target, False)
                    assert retired['mroute_lost'] == before['mroute_lost'] + 1, (before, retired)
                    r.record(label + '-injection', hit)
            else:
                await route(r, target, False)
                retired = await wait_group(r, target, False)
            retirement = time.monotonic() - started
            assert retirement < 12
            unchanged(initial, retired, [0, 1], flows)
            held = time.monotonic()
            while time.monotonic() - held < 6:
                # Traffic can create a negative MFC or a hardware discard
                # entry. Keep allocation cases quiet so restoration adds a
                # missing group instead of updating an existing entry.
                await transfer(r, p, label + '-unavailable', target=False,
                               probe_target=fault not in ('install-failslab', 'add-event-failslab', 'group-failslab'))
            if fault == 'install-failslab' and r.multicast_kind == 'mcast':
                # An idle discard retires at the next refresh. Require that
                # before arming an ADD fault; a live entry uses REPLACE.
                await r.wait(lambda s: row(s, target, 'mcast') is None, timeout=12)
            # Restoration touches only the MFC owner. No apply, rearm, daemon
            # restart, traffic socket replacement, or module reload occurs.
            started = time.monotonic()
            if fault in ('install-failslab', 'add-event-failslab', 'group-failslab'):
                selected = {'install-failslab': 'multicast-install', 'add-event-failslab': 'mroute-event',
                            'group-failslab': 'mroute-group'}[fault]
                async with slab_fault(r, selected, label, keep_alive=(p, [0, 1])) as injection:
                    started = time.monotonic()
                    await route(r, target)
                    # The install the fault waits for happens only once the
                    # group has frames to learn or confirm it from.
                    offered = asyncio.create_task(offering(r, target))
                    try:
                        hit = await injection.hit()
                    finally:
                        offered.cancel()
                        await asyncio.gather(offered, return_exceptions=True)
                    admitted = await wait_group(r, target, True)
                    if fault == 'install-failslab':
                        worker = 'ft_mc_work_fn' if r.multicast_kind == 'mcast' else 'ft_mr_work_fn'
                        assert worker in ''.join(hit['kernel_records']), hit
                        counter = r.multicast_kind + '_install_errors'
                        assert admitted[counter] == before[counter] + 1, (before, admitted)
                    else:
                        assert admitted['mroute_lost'] == before['mroute_lost'] + 1, (before, admitted)
                    r.record(label + '-injection', hit)
            else:
                await route(r, target)
                admitted = await wait_group(r, target, True)
            readmission = time.monotonic() - started
            assert readmission < 15
            after = await transfer(r, p, label + '-recovered')
            assert time.monotonic() - started < 25
            unchanged(initial, after, [0, 1], flows)
            balanced(after, initial['errors'])
            assert int(row(after, control, r.multicast_kind)['packets']) > int(row(before, control, r.multicast_kind)['packets'])
            assert after[r.multicast_kind + '_installed'] >= 2
            assert {g['group'] for g in after[r.multicast_kind] if g['group'] in r.multicast_groups} == set(r.multicast_groups)
            assert after['rearms'] == initial['rearms'] and await attempts(r) == initial_attempts
            await denied(r, p, 3)
            await same_service(r, service)
            r.record(label + '-recovery', {'retirement_seconds': retirement, 'ready_seconds': readmission,
                                          'before': before, 'retired': retired, 'admitted': admitted, 'after': after})
        await p.rpc('open', [2])
        await warm(r, p, [0, 1, 2], 'multicast-new-control', flows[:3])
        await hardware(r, p, 'multicast-final-controls', flows[:3])
        for group in r.multicast_groups:
            await route(r, group, False)
            await wait_group(r, group, False)
            await p.rpc('multicast', changes={'action': 'leave', 'group': group})


# A group first learned while acceleration is stopped, per learner and family.
STOPPED_GROUP = {('mroute', 4): '239.9.4.3', ('mroute', 6): 'ff1e::9:4:3',
                 ('mcast', 4): '239.9.5.3', ('mcast', 6): 'ff1e::9:5:3'}
DISABLED_CONF = FAULT_DIR + '/disabled.conf'


async def stop_acceleration(r, how):
    """Stop acceleration the way `how` names; returns what it said it drained,
    where it says so."""
    if how == 'stop':
        result = await console_command(r.service_console, DAEMON, 'stop', timeout=40)
        return console_json(result['stdout'])['drained']
    if how == 'service-stop':
        await console_command(r.service_console, INIT, 'stop', timeout=45)
        return None
    if how == 'disabled-config':
        # Nothing returns: the running service picks the edit up at its next
        # check, within its five-second health interval, and drains then.
        r.enabled_conf = await read(r.target, r.session, CONF)
        result = await r.target.fs_write(r.session, CONF, 'enabled no\n')
        assert result['errno'] == 0, result
        await r.wait(lambda s: s['mcast_enabled'] == 0 and s['bindings'] == s['entries'] == 0 and
                     s['mcast_installed'] == s['mroute_installed'] == 0, timeout=25)
        return None
    result = await r.target.fs_write(r.session, DISABLED_CONF, 'enabled no\n')
    assert result['errno'] == 0, result
    result = await console_command(r.service_console, DAEMON, 'apply', '--config', DISABLED_CONF, timeout=40)
    return console_json(result['stdout'])['drained']


async def restart_acceleration(r, how):
    """Undo stop_acceleration(r, how) the way an operator would: resume the
    controller, which reconciles its configured (enabled) policy, or reload the
    service, which resumes it and starts it again."""
    if how == 'service-stop':
        await console_command(r.service_console, INIT, 'reload', timeout=45)
    elif how == 'disabled-config':
        result = await r.target.fs_write(r.session, CONF, r.enabled_conf)
        assert result['errno'] == 0, result
    else:
        await console_command(r.service_console, DAEMON, 'resume')
    await wait_service(r, timeout=20)
    # The table the restart installed is a ruleset commit, which takes back
    # every routed group's confirmations; a group is carried again only once
    # the ruleset has stood still, so the windows below start from there.
    if r.multicast_kind == 'mroute':
        await r.wait(lambda s: s['mroute_ruleset_settled'] == 1, timeout=10)


async def stopped_window(r, p, groups, label):
    """Every stream still reaches its listener -- Linux forwarding it, since
    nothing of either learner is in hardware -- and each group says why."""
    before = await r.state()
    window = r.multicast_window
    r.multicast_window += 1
    await asyncio.gather(asyncio.to_thread(send, r, groups, window, 64),
                         p.batch([0, 1], count=64, interval=0.01))
    await asyncio.sleep(0.2)
    after = await r.state()
    r.record(label, {'before': before, 'after': after})
    assert after['mcast_enabled'] == 0, after
    assert after['mcast_installed'] == after['mroute_installed'] == 0, after
    for group in groups:
        result = await p.rpc('multicast', changes={'action': 'status', 'group': group})
        assert not result['errors'], result
        sample = result['windows'].get(str(window), {'received': 0})
        assert sample['received'] == 64 and sample['duplicates'] == 0, (group, sample)
        current = row(after, group, r.multicast_kind)
        assert current and current['state'] == 'refused-paused', (group, current)


async def acceleration_stopped(r, how):
    """A stop, by any public route, is global: when it returns, neither
    learner has anything in hardware, streams keep flowing through Linux, and a
    group learned meanwhile stays there too. Restarting acceleration carries
    them all again without the memberships or routes being touched."""
    flows = [{**f, 'lan': r.lan_ip} for f in FLOWS]
    label = f'{r.multicast_kind}-{r.multicast_family}-{how}'
    new = STOPPED_GROUP[r.multicast_kind, r.multicast_family]
    installed = r.multicast_kind + '_installed'
    async with peer(r, flows, initial_ids=[0, 1, 3], lease=300) as p:
        await warm(r, p, [0, 1], label + '-control-warm', flows[:2])
        await hardware(r, p, label + '-controls', flows[:2])
        for group in r.multicast_groups:
            await p.rpc('multicast', changes={'action': 'join', 'group': group, 'iface': r.multicast_listener, 'port': PORT})
            await route(r, group)
            await wait_group(r, group, True)
        await transfer(r, p, label + '-baseline')

        drained = await stop_acceleration(r, how)
        stopped = await r.state()
        r.record(label + '-stopped', {'drained': drained, 'state': stopped})
        # Already true when the stop returns: that is what it promises.
        if drained is not None:
            assert drained['mcast_enabled'] == drained['mcast_installed'] == drained['mroute_installed'] == 0, drained
        assert stopped['mcast_enabled'] == 0 and stopped[installed] == 0, stopped
        assert stopped['entries'] == stopped['bindings'] == 0, stopped
        await stopped_window(r, p, r.multicast_groups, label + '-software')

        # Learned while stopped: in software from the start, and saying why.
        await p.rpc('multicast', changes={'action': 'join', 'group': new, 'iface': r.multicast_listener, 'port': PORT})
        try:
            await route(r, new)
            await offer(r, new)
            await stopped_window(r, p, [*r.multicast_groups, new], label + '-learned-stopped')

            await restart_acceleration(r, how)
            for group in [*r.multicast_groups, new]:
                await wait_group(r, group, True)
            after = await transfer(r, p, label + '-restarted')
            assert after['mcast_enabled'] == 1 and after[installed] >= 3, after
            await hardware(r, p, label + '-controls-restarted', flows[:2])
        finally:
            await route(r, new, False)
            await p.rpc('multicast', changes={'action': 'leave', 'group': new})
        for group in r.multicast_groups:
            await route(r, group, False)
            await wait_group(r, group, False)
            await p.rpc('multicast', changes={'action': 'leave', 'group': group})
