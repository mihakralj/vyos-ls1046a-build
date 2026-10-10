"""Stateless IPv6 prefix translation through software and hardware flowtables."""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
import ipaddress
import json
import socket

import pytest

from ask_orch.uart import Console
from _topology import DUT_IPV6_LAN, DUT_IPV6_WAN, LAN_IPV6, LAN_NIC, TARGET_LAN_IF, TARGET_WAN_IF, WAN_IPV6, lan_run
from _flowtable_rig import (artifact_dir, command, console_command)
from _flowtable_ipv6 import (Echo, TABLE, _assert_pair, _drive, _drop_tables, _endpoint, _hardware_delta, _nft, _tcp_connection, _udp_exchange)


def prefix(address):
    return str(ipaddress.IPv6Network(address + '/64', strict=False))


def mapped(address, network):
    """A /64 NPT test address, preserving the one's-complement address sum.

    Our endpoints have a zero first interface-ID word. Use different prefix
    sums so the test requires the adjusted suffix as well as the new prefix.
    """
    words = [int.from_bytes(ipaddress.IPv6Address(address).packed[i:i + 2], 'big')
             for i in range(0, 16, 2)]
    new = [int.from_bytes(ipaddress.IPv6Network(network).network_address.packed[i:i + 2], 'big')
           for i in range(0, 8, 2)]
    assert words[4] == 0
    translated = new + [(sum(words[:4]) - sum(new)) % 65535] + words[5:]
    assert sum(words) % 65535 == sum(translated) % 65535
    return str(ipaddress.IPv6Address(b''.join(w.to_bytes(2, 'big') for w in translated)))


@asynccontextmanager
async def translation(r, case):
    source = mapped(LAN_IPV6, 'fc00:1234::/64') if case in ('source', 'both') else LAN_IPV6
    destination = mapped(WAN_IPV6, 'fc00:4321::/64') if case in ('destination', 'both') else WAN_IPV6
    cleanup = []
    with Console.target(log_path=str(artifact_dir() / 'npt-uart.log')) as con:
        await asyncio.to_thread(con.login, 'root', None)

        async def ip6tables(*args):
            return await console_command(con, 'ip6tables', '-t', 'mangle', *args)

        async def rule(chain, address, target, old, new):
            args = [chain, '-s' if target == 'SNPT' else '-d', address,
                    '-j', target, '--src-pfx', old, '--dst-pfx', new]
            await ip6tables('-I', *args)
            cleanup.append(lambda a=args: ip6tables('-D', *a))

        try:
            if source != LAN_IPV6:
                await command(r.wan, r.session, 'ip', '-6', 'route', 'add', source + '/128',
                              'via', DUT_IPV6_WAN, 'dev', r.wan_if)
                cleanup.append(lambda: command(r.wan, r.session, 'ip', '-6', 'route', 'del', source + '/128'))
                await rule('POSTROUTING', LAN_IPV6, 'SNPT', prefix(LAN_IPV6), prefix(source))
                await rule('PREROUTING', source, 'DNPT', prefix(source), prefix(LAN_IPV6))
            if destination != WAN_IPV6:
                result = await lan_run(r.lan, f'ip -6 route add {destination}/128 via {DUT_IPV6_LAN} dev {LAN_NIC}')
                assert result.rc == 0, result.stdout
                cleanup.append(lambda: lan_run(r.lan, f'ip -6 route del {destination}/128'))
                await rule('PREROUTING', destination, 'DNPT', prefix(destination), prefix(WAN_IPV6))
                await rule('POSTROUTING', WAN_IPV6, 'SNPT', prefix(WAN_IPV6), prefix(destination))
            yield source, destination
        finally:
            await _drop_tables(r)
            await r.wait(lambda state: state['entries'] == state['bindings'] == 0)
            failures = []
            for restore in reversed(cleanup):
                try:
                    await restore()
                except Exception as error:
                    failures.append(str(error))
            assert not failures, failures


async def slow_packets(r):
    listing = json.loads((await command(r.target, r.session, 'nft', '-j', 'list', 'counter',
                                      'inet', TABLE, 'slow'))['stdout'])
    return next(item['counter']['packets'] for item in listing['nftables'] if 'counter' in item)


@pytest.mark.parametrize('case', ['source', 'destination', 'both'])
@pytest.mark.parametrize('proto', ['udp', 'tcp'])
@pytest.mark.parametrize('hardware', [False, True], ids=['software', 'hardware'])
@pytest.mark.rfc("6296")
async def test_flowtable_npt(ipv6_rig, case, proto, hardware):
    r = ipv6_rig
    sport = 49010 + 10 * ['source', 'destination', 'both'].index(case) + int(hardware) * 100
    dport = sport + 1
    async with translation(r, case) as (source, destination):
        flags = 'flags offload;' if hardware else ''
        await _nft(r, f'''table inet {TABLE} {{
 counter slow {{ }}
 flowtable fast {{ hook ingress priority 0; devices = {{ {TARGET_LAN_IF}, {TARGET_WAN_IF} }}; {flags} }}
 chain forward {{ type filter hook forward priority 0; policy accept;
 meta l4proto {proto} ct original proto-src {sport} ct original proto-dst {dport} counter name slow flow add @fast
 }}
}}''')
        forward = (_endpoint(LAN_IPV6, sport), _endpoint(destination, dport),
                   _endpoint(source, sport), _endpoint(WAN_IPV6, dport), WAN_IPV6)
        reverse = (_endpoint(WAN_IPV6, dport), _endpoint(source, sport),
                   _endpoint(destination, dport), _endpoint(LAN_IPV6, sport), LAN_IPV6)

        async def exercise(send, measure):
            if hardware:
                before = await _drive(r, send, lambda s: s['entries'] == 2, 'NPT did not reach hardware')
                rows = _assert_pair(before, 17 if proto == 'udp' else 6, forward, reverse)
            else:
                for _ in range(4):
                    await send()
                    await asyncio.sleep(0.5)
                before = await r.state()
                assert before['entries'] == 0, before
                listing = await command(r.target, r.session, 'conntrack', '-L', '-f', 'ipv6', '-p', proto,
                                        '--orig-src', LAN_IPV6, '--sport', str(sport), '--dport', str(dport))
                assert '[OFFLOAD]' in listing['stdout'], listing
            slow = await slow_packets(r)
            await measure()
            after = await r.state()
            assert await slow_packets(r) == slow, 'measured NPT packets reached ordinary forwarding'
            if hardware:
                current = _assert_pair(after, 17 if proto == 'udp' else 6, forward, reverse)
                delta = _hardware_delta(rows, current)
                if proto == 'udp':
                    assert delta == {TARGET_LAN_IF: 64, TARGET_WAN_IF: 64}, delta
                else:
                    assert min(delta.values()) >= 100, delta
                assert before['installs'] == after['installs'] and before['deletes'] == after['deletes']
            else:
                assert after['entries'] == 0, after
            assert after['errors'] == r.errors, after
            r.record(f'npt-{case}-{proto}-{hardware}', {'before': before, 'after': after,
                     'source_on_wire': source, 'destination_on_lan': destination, 'slow_packets': slow})

        if proto == 'udp':
            echo = Echo()
            transport, _ = await asyncio.get_running_loop().create_datagram_endpoint(
                lambda: echo, local_addr=(WAN_IPV6, dport), family=socket.AF_INET6)
            try:
                async def send(count=8):
                    result = await _udp_exchange(r, sport, destination, dport, count,
                                                 (destination, dport), 'npt_udp')
                    assert result == {'echoed': count, 'lost': 0}, result
                    assert echo.sources == {(source, sport)}, echo.sources
                await exercise(send, lambda: send(64))
            finally:
                transport.close()
        else:
            async with _tcp_connection(r, sport, dport, destination=destination, expect_source=source) as conn:
                async def measure():
                    await conn.transfer('upload', 64)
                    await conn.transfer('download', 64)
                await exercise(lambda: conn.transfer('upload', 4), measure)
                await conn.close()
