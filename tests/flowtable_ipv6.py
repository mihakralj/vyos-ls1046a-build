"""IPv6 flowtable offload: routed, SNAT, DNAT and TCP, each proved in hardware.

Every case establishes the flow, asserts the adapter's view of both directions,
then sends a second burst and requires the classifier's own packet counters to
account for all of it. A translated case additionally asserts what the far
endpoint observed, which is the only evidence that the 16-byte address rewrite
reached the wire rather than just the rule.
"""
from __future__ import annotations

from _flowtable_ipv6 import HAIRPIN_DPORT, HAIRPIN_MAPPED, HAIRPIN_PUBLIC, HAIRPIN_SPORT

from _flowtable_ipv6 import (Echo, HAIRPIN, HAIRPIN_GATEWAY, NAT_TABLE, PORTS, PayloadEcho, SNAT_PORT, _assert_pair, _drive, _drop_tables, _endpoint, _hairpin_exchange, _hardware_delta, _nft, _offload_table, _settle, _split_endpoint, _tcp_connection, _udp_exchange)

import asyncio
import json
import socket

import pytest

from _topology import DUT_IPV6_LAN, DUT_IPV6_WAN, LAN_IPV6, LAN_NIC, TARGET_LAN_IF, TARGET_WAN_IF, VIRT_IPV6, WAN_IPV6, lan_run_python
from _flowtable_rig import command, read

JUMBO = 9000


@pytest.mark.rfc("8200")
@pytest.mark.parametrize("case", ["routed", "snat", "dnat"])
async def test_udp(ipv6_rig, case):
    r = ipv6_rig
    sport, dport = PORTS[case]
    loop = asyncio.get_running_loop()
    echo = Echo()
    transport, _ = await loop.create_datagram_endpoint(
        lambda: echo, local_addr=(WAN_IPV6, dport), family=socket.AF_INET6)
    # Where the LAN sends, what the WAN endpoint should see as the source, and
    # what the LAN should see replies coming back from.
    destination = VIRT_IPV6 if case == "dnat" else WAN_IPV6
    seen_source = (DUT_IPV6_WAN, SNAT_PORT) if case == "snat" else (LAN_IPV6, sport)
    reply_from = (destination, dport)
    if case == "snat":
        forward = (_endpoint(LAN_IPV6, sport), _endpoint(WAN_IPV6, dport),
                   _endpoint(DUT_IPV6_WAN, SNAT_PORT), _endpoint(WAN_IPV6, dport), WAN_IPV6)
        reverse = (_endpoint(WAN_IPV6, dport), _endpoint(DUT_IPV6_WAN, SNAT_PORT),
                   _endpoint(WAN_IPV6, dport), _endpoint(LAN_IPV6, sport), LAN_IPV6)
    elif case == "dnat":
        forward = (_endpoint(LAN_IPV6, sport), _endpoint(VIRT_IPV6, dport),
                   _endpoint(LAN_IPV6, sport), _endpoint(WAN_IPV6, dport), WAN_IPV6)
        reverse = (_endpoint(WAN_IPV6, dport), _endpoint(LAN_IPV6, sport),
                   _endpoint(VIRT_IPV6, dport), _endpoint(LAN_IPV6, sport), LAN_IPV6)
    else:
        forward = (_endpoint(LAN_IPV6, sport), _endpoint(WAN_IPV6, dport),
                   _endpoint(LAN_IPV6, sport), _endpoint(WAN_IPV6, dport), WAN_IPV6)
        reverse = (_endpoint(WAN_IPV6, dport), _endpoint(LAN_IPV6, sport),
                   _endpoint(WAN_IPV6, dport), _endpoint(LAN_IPV6, sport), LAN_IPV6)
    try:
        if case == "snat":
            await _nft(r, f'table ip6 {NAT_TABLE} {{ chain postrouting {{ '
                          f'type nat hook postrouting priority 90; '
                          f'ip6 saddr {LAN_IPV6} ip6 daddr {WAN_IPV6} udp sport {sport} '
                          f'udp dport {dport} snat to [{DUT_IPV6_WAN}]:{SNAT_PORT}; }}; }}')
        elif case == "dnat":
            await _nft(r, f'table ip6 {NAT_TABLE} {{ chain prerouting {{ '
                          f'type nat hook prerouting priority -100; '
                          f'ip6 saddr {LAN_IPV6} ip6 daddr {VIRT_IPV6} udp sport {sport} '
                          f'udp dport {dport} dnat to [{WAN_IPV6}]:{dport}; }}; }}')
        await _offload_table(r, f'ip6 saddr {LAN_IPV6} udp sport {sport} udp dport {dport}')

        async def send(count=8):
            return await _udp_exchange(r, sport, destination, dport, count, reply_from,
                                       f"flowtable_v6_{case}")

        before_state, before = await _settle(r, socket.IPPROTO_UDP, forward, reverse,
                                             send, f"ipv6-{case}-admission")
        assert echo.sources == {seen_source}, echo.sources

        # Everything from here must be forwarded by the classifier itself,
        # with nothing falling back to software.
        report = await send(64)
        assert report == {"echoed": 64, "lost": 0}, report
        after_state = await r.state()
        after = _assert_pair(after_state, socket.IPPROTO_UDP, forward, reverse)
        delta = _hardware_delta(before, after)
        assert delta == {TARGET_LAN_IF: 64, TARGET_WAN_IF: 64}, (delta, before_state, after_state)
        assert before_state["installs"] == after_state["installs"], (before_state, after_state)
        assert before_state["deletes"] == after_state["deletes"], (before_state, after_state)
        assert after_state["errors"] == r.errors, after_state
        assert echo.sources == {seen_source}, echo.sources
        for direction in before:
            assert after[direction]["cookie"] == before[direction]["cookie"], (before, after)
        r.record(f"ipv6-{case}-hardware", {"before": before_state, "after": after_state,
                                           "hardware_delta": delta,
                                           "observed_sources": sorted(echo.sources)})
    finally:
        transport.close()
        await _drop_tables(r)


async def test_tcp(ipv6_rig):
    r = ipv6_rig
    sport, dport = PORTS["tcp"]
    forward = (_endpoint(LAN_IPV6, sport), _endpoint(WAN_IPV6, dport),
               _endpoint(LAN_IPV6, sport), _endpoint(WAN_IPV6, dport), WAN_IPV6)
    reverse = (_endpoint(WAN_IPV6, dport), _endpoint(LAN_IPV6, sport),
               _endpoint(WAN_IPV6, dport), _endpoint(LAN_IPV6, sport), LAN_IPV6)
    try:
        await _offload_table(r, f'ip6 saddr {LAN_IPV6} tcp sport {sport} tcp dport {dport}')
        # One connection carries the whole test. The handshake is punted by the
        # soft parser and admission waits for ESTABLISHED, so the flow installs
        # part-way through it and the later transfers are the hardware
        # measurement -- which is why the peer is driven over this connection
        # rather than by a second console operation.
        async with _tcp_connection(r, sport, dport) as conn:
            state, before = await _settle(
                r, socket.IPPROTO_TCP, forward, reverse,
                lambda: conn.transfer("upload", 4), "ipv6-tcp-admission")
            await conn.transfer("upload", 64)
            await conn.transfer("download", 64)
            after_state = await r.state()
            after = _assert_pair(after_state, socket.IPPROTO_TCP, forward, reverse)
            delta = _hardware_delta(before, after)
            # Segments are not payload units: TCP coalesces and acknowledges on
            # its own schedule, and the peer's window is its own business. Half
            # a megabyte each way cannot cross in fewer than a hundred frames,
            # so require that much rather than an exact count.
            assert delta[TARGET_LAN_IF] >= 100 and delta[TARGET_WAN_IF] >= 100, \
                (delta, after_state)
            assert after_state["errors"] == r.errors, after_state
            assert state["installs"] == after_state["installs"], (state, after_state)
            for direction in before:
                assert after[direction]["cookie"] == before[direction]["cookie"], (before, after)
            r.record("ipv6-tcp-hardware", {"before": state, "after": after_state,
                                           "hardware_delta": delta})
            await conn.close()
    finally:
        await _drop_tables(r)


async def test_masquerade(ipv6_rig):
    """MASQUERADE picks both the source address and the port, so pin the
    address to the egress interface's own and let the kernel own the port —
    then require the far endpoint to have seen exactly what it chose."""
    r = ipv6_rig
    sport, dport = PORTS["masquerade"]
    loop = asyncio.get_running_loop()
    echo = Echo()
    transport, _ = await loop.create_datagram_endpoint(
        lambda: echo, local_addr=(WAN_IPV6, dport), family=socket.AF_INET6)
    try:
        await _nft(r, f'table ip6 {NAT_TABLE} {{ chain postrouting {{ '
                      f'type nat hook postrouting priority 90; '
                      f'ip6 saddr {LAN_IPV6} ip6 daddr {WAN_IPV6} udp sport {sport} '
                      f'udp dport {dport} masquerade; }}; }}')
        await _offload_table(r, f'ip6 saddr {LAN_IPV6} udp sport {sport} udp dport {dport}')

        async def send(count=8):
            return await _udp_exchange(r, sport, WAN_IPV6, dport, count, (WAN_IPV6, dport),
                                       "flowtable_v6_masquerade")

        state = await _drive(r, send, lambda s: s["entries"] == 2,
                             "IPv6 masquerade did not install")
        rows = {f["in"]: f for f in state["flows"]}
        assert set(rows) == {TARGET_LAN_IF, TARGET_WAN_IF}, state
        forward, reverse = rows[TARGET_LAN_IF], rows[TARGET_WAN_IF]
        assert forward["family"] == reverse["family"] == "6", state
        translated, port = _split_endpoint(forward["new_src"])
        assert translated == DUT_IPV6_WAN, (forward, DUT_IPV6_WAN)
        assert forward["src"] == _endpoint(LAN_IPV6, sport), state
        assert forward["new_dst"] == _endpoint(WAN_IPV6, dport), state
        # The reverse direction must carry the inverse of whatever was chosen.
        assert reverse["dst"] == _endpoint(DUT_IPV6_WAN, port), state
        assert reverse["new_dst"] == _endpoint(LAN_IPV6, sport), state
        assert echo.sources == {(DUT_IPV6_WAN, port)}, (echo.sources, port)
        r.record("ipv6-masquerade-admission", state)

        before = rows
        report = await send(64)
        assert report == {"echoed": 64, "lost": 0}, report
        after_state = await r.state()
        after = {f["in"]: f for f in after_state["flows"]}
        delta = _hardware_delta(before, after)
        assert delta == {TARGET_LAN_IF: 64, TARGET_WAN_IF: 64}, (delta, after_state)
        assert after_state["errors"] == r.errors, after_state
        assert echo.sources == {(DUT_IPV6_WAN, port)}, echo.sources
        r.record("ipv6-masquerade-hardware", {"after": after_state, "hardware_delta": delta,
                                              "translated_port": port})
    finally:
        transport.close()
        await _drop_tables(r)


async def test_mtu_recovery(ipv6_rig):
    """A device MTU change must retire both IPv6 directions and let them come
    back describing the new MTU, exactly as the IPv4 path does.

    Both UDP directions are only in hardware while neither port can deliver
    more than the other's path carries (see test_mtu_bound). A port never
    receives less than a full Ethernet frame, so with the WAN lowered below one
    the LAN still delivers 1500 bytes into the smaller WAN path, and only the
    WAN-to-LAN direction can come back. Both ports move to jumbo together for
    the full re-description, and the WAN alone goes below a full frame for the
    one-sided one."""
    r = ipv6_rig
    sport, dport = PORTS["mtu"]
    loop = asyncio.get_running_loop()
    echo = Echo()
    transport, _ = await loop.create_datagram_endpoint(
        lambda: echo, local_addr=(WAN_IPV6, dport), family=socket.AF_INET6)
    original = int((await read(r.target, r.session, f"/sys/class/net/{TARGET_WAN_IF}/mtu")).strip())
    assert original == 1500, original
    try:
        await _offload_table(r, f'ip6 saddr {LAN_IPV6} udp sport {sport} udp dport {dport}')

        async def send(count=8):
            return await _udp_exchange(r, sport, WAN_IPV6, dport, count, (WAN_IPV6, dport),
                                       "flowtable_v6_mtu")

        async def settled(expected):
            """`expected` maps egress device to MTU for every direction that
            has to be in hardware, and no other: a direction describes the
            path it leaves by, so only the one egressing a changed device
            moves."""
            # The jumbo step below retires the flow once, for the MTU change,
            # and then has one readmission declined for the injected lost RTNL,
            # which the software path offers again about a second later.
            return await _drive(r, send, lambda s: s["entries"] == len(expected) and all(
                int(f["mtu"]) == expected.get(f["out"], -1) for f in s["flows"]),
                f"IPv6 flow did not settle at MTU {expected}", timeout=20)

        async def link_mtu(dev, mtu):
            await command(r.target, r.session, "ip", "link", "set", "dev", dev, "mtu", str(mtu))

        initial = await settled({TARGET_LAN_IF: original, TARGET_WAN_IF: original})
        r.record("ipv6-mtu-initial", initial)
        # Raising a device MTU raises its IPv6 MTU with it. The LAN goes
        # first, which retires the flow; nothing is sent until the WAN has
        # followed, so nothing is readmitted in between.
        #
        # The readmission is made to lose RTNL on its second direction. The
        # backend never waits for RTNL under its transaction: it declines with
        # -EAGAIN and, with no IPsec policy configured, retires nothing -- the
        # first direction stays installed, and the software path forwarding
        # the second offers the flow again about a second later. Any RTNL
        # holder can cause that in production, so the re-description has to
        # survive it every run, not by chance.
        knob = "/sys/module/ask_flowtable/parameters/flowtable_fail_stage"
        assert (await r.target.fs_write(r.session, knob, "4"))["errno"] == 0
        try:
            await link_mtu(TARGET_LAN_IF, JUMBO)
            await link_mtu(TARGET_WAN_IF, JUMBO)
            # Both directions of a connection share one invalidation handle and
            # the counter moves on the transition, so a single connection
            # retiring is one increment -- not one per direction.
            retired = await r.wait(lambda s: s["mtu_invalidations"] >= initial["mtu_invalidations"] + 1)
            raised = await settled({TARGET_LAN_IF: JUMBO, TARGET_WAN_IF: JUMBO})
            assert (await read(r.target, r.session, knob)).strip() == "0", "fault not consumed"
        finally:
            assert (await r.target.fs_write(r.session, knob, "0"))["errno"] == 0
        assert raised["errors"] == r.errors, raised
        assert raised["busy"] >= initial["busy"] + 1, (initial, raised)
        # The lost RTNL declined an offer and retired nothing.
        assert raised["admission_invalidations"] == initial["admission_invalidations"], \
            (initial, raised)
        r.record("ipv6-mtu-raised", {"retired": retired, "raised": raised})
        await link_mtu(TARGET_WAN_IF, original)
        await link_mtu(TARGET_LAN_IF, original)
        restored = await settled({TARGET_LAN_IF: original, TARGET_WAN_IF: original})
        r.record("ipv6-mtu-restored", restored)

        # Below a full frame. The LAN port still receives a full frame, more
        # than the 1400-byte WAN path carries, so the LAN-to-WAN direction
        # stays with Linux, which can say Packet Too Big -- whatever IPv6 MTU
        # the LAN advertises, which is why this leaves it alone. The WAN-to-LAN
        # direction leaves by the 1500-byte LAN and comes back alone.
        await link_mtu(TARGET_WAN_IF, 1400)
        reduced = await settled({TARGET_LAN_IF: original})
        assert reduced["rejects"] > restored["rejects"], (restored, reduced)
        r.record("ipv6-mtu-reduced", reduced)
        await link_mtu(TARGET_WAN_IF, original)
        restored = await settled({TARGET_LAN_IF: original, TARGET_WAN_IF: original})
        assert restored["errors"] == r.errors, restored
        r.record("ipv6-mtu-restored-again", restored)
    finally:
        transport.close()
        for dev in (TARGET_WAN_IF, TARGET_LAN_IF):
            await command(r.target, r.session, "ip", "link", "set", "dev", dev,
                          "mtu", str(original), check=False)
        await _drop_tables(r)


@pytest.mark.rfc("4861", section="7.2.5")
async def test_neighbour_mac_change(ipv6_rig):
    """The LAN host moves to a new MAC and says so with an unsolicited Neighbor
    Advertisement (Override set): the DUT's learned neighbour takes the new
    address, the offloaded flow is retired, and it comes back sending there."""
    r = ipv6_rig
    sport, dport = PORTS["neighbour"]
    loop = asyncio.get_running_loop()
    transport, _ = await loop.create_datagram_endpoint(
        Echo, local_addr=(WAN_IPV6, dport), family=socket.AF_INET6)
    moved = "02:9d:99:b2:33:02"

    async def announce(mac):
        result = await lan_run_python(r.lan, f'''
import subprocess
from scapy.all import Ether, IPv6, ICMPv6ND_NA, ICMPv6NDOptDstLLAddr, sendp
subprocess.run(['ip', 'link', 'set', 'dev', {LAN_NIC!r}, 'address', {mac!r}], check=True)
# A new link address flushes this host's neighbours, the fixture's pinned
# one for the DUT among them; pin it again as the fixture made it.
subprocess.run(['ip', '-6', 'neigh', 'replace', {DUT_IPV6_LAN!r}, 'lladdr', {r.dut_lan_mac!r},
                'nud', 'permanent', 'dev', {LAN_NIC!r}], check=True)
sendp(Ether(src={mac!r}, dst='33:33:00:00:00:01') / IPv6(src={LAN_IPV6!r}, dst='ff02::1') /
      ICMPv6ND_NA(R=0, S=0, O=1, tgt={LAN_IPV6!r}) / ICMPv6NDOptDstLLAddr(lladdr={mac!r}),
      iface={LAN_NIC!r}, count=2, inter=0.05, verbose=False)
''', label="flowtable_v6_neighbour", timeout=20)
        assert result.rc == 0, result.stdout

    async def neighbour():
        rows = json.loads((await command(r.target, r.session, "ip", "-j", "-6", "neigh", "show",
                                         LAN_IPV6, "dev", TARGET_LAN_IF))["stdout"])
        return rows[0] if rows else {}

    async def send(count=8):
        return await _udp_exchange(r, sport, WAN_IPV6, dport, count, (WAN_IPV6, dport),
                                   "flowtable_v6_neighbour")
    try:
        # Learned, not pinned: only a learned entry follows an advertisement.
        await command(r.target, r.session, "ip", "-6", "neigh", "del", LAN_IPV6,
                      "dev", TARGET_LAN_IF)
        await _offload_table(r, f'ip6 saddr {LAN_IPV6} udp sport {sport} udp dport {dport}')
        before = await _drive(r, send, lambda s: s["entries"] == 2, "IPv6 flow did not install")
        assert (await neighbour()).get("lladdr") == r.lan_mac
        await announce(moved)
        retired = await r.wait(lambda s: s["entries"] == 0 and
                               s["neighbour_invalidations"] > before["neighbour_invalidations"])
        assert (await neighbour()).get("lladdr") == moved
        assert retired["invalidated"] == 0 and retired["errors"] == r.errors, retired
        back = await _drive(r, send, lambda s: s["entries"] == 2, "IPv6 flow did not come back")
        assert back["installs"] == before["installs"] + 2, (before, back)
        assert await send(64) == {"echoed": 64, "lost": 0}
        r.record("ipv6-neighbour-mac-change", {"before": before, "retired": retired, "back": back})
    finally:
        await announce(r.lan_mac)
        transport.close()
        await _drop_tables(r)


@pytest.mark.rfc("8200", section="4.5")
@pytest.mark.rfc("5722")
async def test_fragments(ipv6_rig):
    """Shuffled fragments of many datagrams arrive whole, and a datagram whose
    fragments overlap is dropped entirely, as RFC 5722 requires."""
    r = ipv6_rig
    sizes = [2000 + 41 * i for i in range(60)]
    overlapping = set(range(0, len(sizes), 5))
    sock = socket.socket(socket.AF_INET6, socket.SOCK_DGRAM)
    # Read only once all is sent: the buffer holds every datagram.
    sock.setsockopt(socket.SOL_SOCKET, 33, 8 << 20)  # SO_RCVBUFFORCE
    sock.bind((WAN_IPV6, 5204))
    sock.setblocking(False)
    payload = lambda i: i.to_bytes(4, "big") + bytes((i * 7 + k) % 251 for k in range(sizes[i] - 4))
    script = f'''
import random
from scapy.all import IPv6, IPv6ExtHdrFragment, UDP, Raw, fragment6, raw, send
frags = []
for i, size in enumerate({sizes!r}):
    data = i.to_bytes(4, "big") + bytes((i * 7 + k) % 251 for k in range(size - 4))
    ip = IPv6(src={LAN_IPV6!r}, dst={WAN_IPV6!r})
    parts = fragment6(ip / IPv6ExtHdrFragment(id=0x8200 + i) / UDP(sport=30600, dport=5204) / Raw(data), 1280)
    if i in {sorted(overlapping)!r}:
        # The first fragment again as two that overlap: no subset of what is
        # sent reassembles without an overlap, whatever the arrival order.
        first = raw(parts[0][IPv6ExtHdrFragment].payload)
        parts[:1] = [ip / IPv6ExtHdrFragment(id=0x8200 + i, offset=0, m=1, nh=17) / Raw(first[:1024]),
                     ip / IPv6ExtHdrFragment(id=0x8200 + i, offset=64, m=1, nh=17) / Raw(first[512:])]
    frags += parts
random.Random(8200).shuffle(frags)
send(frags, verbose=0)
'''
    try:
        result = await lan_run_python(r.lan, script, label="flowtable_v6_fragments", timeout=120)
        assert result.rc == 0, result.stdout
        received, loop = {}, asyncio.get_running_loop()
        while len(received) < len(sizes) - len(overlapping):
            try:
                data = await asyncio.wait_for(loop.sock_recv(sock, 8192), 5)
            except TimeoutError:
                break
            received[int.from_bytes(data[:4], "big")] = data
        expected = set(range(len(sizes))) - overlapping
        assert set(received) == expected, (expected - set(received), set(received) - expected)
        assert all(data == payload(i) for i, data in received.items())
    finally:
        sock.close()


@pytest.mark.rfc("8200")
@pytest.mark.rfc("4443")
async def test_same_tuple_exceptions(ipv6_rig):
    """Packets on an offloaded IPv6 tuple that Linux must handle still reach it.

    The IPv6 counterpart of test_same_tuple_exceptions. With
    both directions in hardware, the same 5-tuple carries a hop limit of 1,
    which must come back as an ICMPv6 error from Linux rather than leave the
    WAN port, and hop-by-hop options, destination options, a chain of both and
    fragments, which Linux forwards intact. The entries must still be carrying
    the flow afterwards. An oversized packet is not among them: a path smaller
    than the LAN's IPv6 MTU never has that direction in hardware at all, which
    test_mtu_bound covers.
    """
    r = ipv6_rig
    sport, dport = PORTS["exceptions"]
    loop = asyncio.get_running_loop()
    echo = PayloadEcho()
    transport, _ = await loop.create_datagram_endpoint(
        lambda: echo, local_addr=(WAN_IPV6, dport), family=socket.AF_INET6)
    forward = (_endpoint(LAN_IPV6, sport), _endpoint(WAN_IPV6, dport),
               _endpoint(LAN_IPV6, sport), _endpoint(WAN_IPV6, dport), WAN_IPV6)
    reverse = (_endpoint(WAN_IPV6, dport), _endpoint(LAN_IPV6, sport),
               _endpoint(WAN_IPV6, dport), _endpoint(LAN_IPV6, sport), LAN_IPV6)
    try:
        await _offload_table(r, f'ip6 saddr {LAN_IPV6} udp sport {sport} udp dport {dport}')

        async def send(count=8):
            return await _udp_exchange(r, sport, WAN_IPV6, dport, count, (WAN_IPV6, dport),
                                       "flowtable_v6_exceptions")

        admitted, admitted_rows = await _settle(r, socket.IPPROTO_UDP, forward, reverse, send,
                                                "ipv6-exceptions-admission")
        script = f'''
import json, socket, struct, time
from scapy.all import (Ether, IPv6, UDP, Raw, ICMPv6TimeExceeded,
                       IPv6ExtHdrHopByHop, IPv6ExtHdrDestOpt, IPv6ExtHdrFragment,
                       fragment6, sendp, srp1)
iface = {LAN_NIC!r}
src, dst = {LAN_IPV6!r}, {WAN_IPV6!r}
sport, dport = {sport}, {dport}
eth = Ether(dst={r.dut_lan_mac!r})
s = socket.socket(socket.AF_INET6, socket.SOCK_DGRAM)
s.bind((src, sport)); s.settimeout(3)
s.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_RECVHOPLIMIT, 1)
def udp(payload, *headers, hlim=64):
    packet = IPv6(src=src, dst=dst, hlim=hlim)
    for header in headers:
        packet = packet/header
    return packet/UDP(sport=sport, dport=dport)/Raw(payload)
results = {{}}
answer = srp1(eth/udp(b'ASK6-expired', hlim=1), iface=iface, timeout=3, verbose=False)
assert answer is not None and ICMPv6TimeExceeded in answer, answer
results['hoplimit'] = answer.summary()
fragments = b'ASK6-fragments'.ljust(1024, b'.')
for name, packets, payload in [
    ('hop_by_hop', [udp(b'ASK6-hbh', IPv6ExtHdrHopByHop())], b'ASK6-hbh'),
    ('destination', [udp(b'ASK6-dest', IPv6ExtHdrDestOpt())], b'ASK6-dest'),
    ('chain', [udp(b'ASK6-chain', IPv6ExtHdrHopByHop(), IPv6ExtHdrDestOpt())], b'ASK6-chain'),
    ('fragments', fragment6(udp(fragments, IPv6ExtHdrFragment()), 600), fragments),
]:
    sendp([eth/p for p in packets], iface=iface, verbose=False)
    data, anc, flags, addr = s.recvmsg(4096, 128)
    assert data == payload and addr[:2] == (dst, dport), (name, data, addr)
    hops = [struct.unpack('i', v)[0] for level, kind, v in anc
            if level == socket.IPPROTO_IPV6 and kind == socket.IPV6_HOPLIMIT]
    assert hops == [63], (name, hops)
    results[name] = len(data)
time.sleep(0.5)
s.close()
print(json.dumps(results))
'''
        result = await lan_run_python(r.lan, script, timeout=40, label="flowtable_v6_exceptions")
        assert result.rc == 0, result.stdout
        for payload in (b"ASK6-hbh", b"ASK6-dest", b"ASK6-chain", b"ASK6-fragments".ljust(1024, b".")):
            assert echo.received[payload] == 1, (payload, echo.received)
        assert not echo.received[b"ASK6-expired"], echo.received

        # The exceptions went to Linux; the entries kept the flow.
        before_state = await r.state()
        before = _assert_pair(before_state, socket.IPPROTO_UDP, forward, reverse)
        report = await send(16)
        assert report == {"echoed": 16, "lost": 0}, report
        after_state = await r.state()
        after = _assert_pair(after_state, socket.IPPROTO_UDP, forward, reverse)
        delta = _hardware_delta(before, after)
        assert delta == {TARGET_LAN_IF: 16, TARGET_WAN_IF: 16}, (delta, before_state, after_state)
        for direction in before:
            assert after[direction]["cookie"] == admitted_rows[direction]["cookie"], (admitted, after_state)
        assert after_state["errors"] == r.errors, after_state
        r.record("ipv6-exceptions", {"results": json.loads(result.stdout.strip().splitlines()[-1]),
                                     "admitted": admitted, "after": after_state,
                                     "hardware_delta": delta})
    finally:
        transport.close()
        await _drop_tables(r)


@pytest.mark.rfc("4443", section="3.2")
async def test_mtu_bound(ipv6_rig):
    """The microcode fragments an IPv6 packet over its entry's MTU instead of
    handing it to Linux, so a UDP direction whose path is smaller than what
    its ingress port accepts must stay in software, where Linux answers with
    Packet Too Big. The route to the WAN host is locked to 1280, the minimum.

    Telling the LAN's hosts 1280 changes nothing for the LAN-to-WAN direction:
    the port still takes a full frame from a host that ignores the advertised
    MTU, so it stays in software, and the WAN-to-LAN one with it, the LAN path
    now carrying 1280 against a 1500-byte WAN. With the LAN back at 1500 the
    WAN-to-LAN direction goes to hardware alone. Then an oversized packet gets
    its Packet Too Big and the microcode fragments nothing.
    """
    r = ipv6_rig
    sport, dport = PORTS["bound"]
    loop = asyncio.get_running_loop()
    echo = PayloadEcho()
    transport, _ = await loop.create_datagram_endpoint(
        lambda: echo, local_addr=(WAN_IPV6, dport), family=socket.AF_INET6)
    lan_mtu = f"net.ipv6.conf.{TARGET_LAN_IF}.mtu"
    original = (await command(r.target, r.session, "sysctl", "-n", lan_mtu))["stdout"].strip()
    assert original == "1500", original
    # IPv6 forwarding ignores an unlocked route MTU (ip6_dst_mtu_maybe_forward),
    # in software and so in the flow too.
    route = ["ip", "-6", "route", "replace", f"{WAN_IPV6}/128", "dev", TARGET_WAN_IF,
             "mtu", "lock", "1280"]

    def one_direction(ingress, mtu):
        def settled(s):
            return (s["entries"] == 1 and s["flows"][0]["in"] == ingress
                    and int(s["flows"][0]["mtu"]) == mtu)
        return settled

    async def send(count=8):
        return await _udp_exchange(r, sport, WAN_IPV6, dport, count, (WAN_IPV6, dport),
                                   "flowtable_v6_bound")

    async def fragments_sent():
        text = await read(r.target, r.session, "/proc/ucode_frag/stats")
        return int(text.split("Number of IPv6 fragments sent :")[1].split()[0])

    try:
        await command(r.target, r.session, "sysctl", "-w", f"{lan_mtu}=1280")
        await command(r.target, r.session, *route)
        initial = await r.state()
        await _offload_table(r, f'ip6 saddr {LAN_IPV6} udp sport {sport} udp dport {dport}')
        advertised = await _drive(r, send, lambda s: s["entries"] == 0 and s["rejects"] > initial["rejects"] + 1,
                                  "neither direction may be in hardware")
        r.record("ipv6-bound-advertised", advertised)

        await command(r.target, r.session, "sysctl", "-w", f"{lan_mtu}={original}")
        # Linux's flow keeps the path MTUs it was created with, so the
        # WAN-to-LAN direction's offers would still carry 1280 until it ended:
        # a new table makes a new flow.
        await _drop_tables(r)
        await _offload_table(r, f'ip6 saddr {LAN_IPV6} udp sport {sport} udp dport {dport}')
        unbounded = await _drive(r, send, one_direction(TARGET_WAN_IF, 1500),
                                 "only the WAN-to-LAN direction should be in hardware")
        r.record("ipv6-bound-wan", unbounded)

        before = await fragments_sent()
        script = f'''
import json
from scapy.all import Ether, IPv6, UDP, Raw, ICMPv6PacketTooBig, srp1
packet = IPv6(src={LAN_IPV6!r}, dst={WAN_IPV6!r})/UDP(sport={sport}, dport={dport})/Raw(b'M' * 1400)
answer = srp1(Ether(dst={r.dut_lan_mac!r})/packet, iface={LAN_NIC!r}, timeout=3, verbose=False)
assert answer is not None and ICMPv6PacketTooBig in answer, answer
assert answer[ICMPv6PacketTooBig].mtu == 1280, answer.show(dump=True)
print(json.dumps(answer.summary()))
'''
        result = await lan_run_python(r.lan, script, timeout=20, label="flowtable_v6_bound")
        assert result.rc == 0, result.stdout
        await asyncio.sleep(0.5)
        assert not echo.received[b"M" * 1400], echo.received
        assert await fragments_sent() == before

        # Small packets still cross, the bounded direction in software.
        report = await send(16)
        assert report == {"echoed": 16, "lost": 0}, report
        final = await r.state()
        assert one_direction(TARGET_WAN_IF, 1500)(final), final
        assert final["errors"] == r.errors, final
        r.record("ipv6-bound", {"too_big": result.stdout.strip().splitlines()[-1], "final": final})
    finally:
        transport.close()
        await _drop_tables(r)
        await command(r.target, r.session, "ip", "-6", "route", "del", f"{WAN_IPV6}/128",
                      "dev", TARGET_WAN_IF, check=False)
        await command(r.target, r.session, "sysctl", "-w", f"{lan_mtu}={original}", check=False)


@pytest.mark.rfc("4443", section="3.2")
async def test_egress_mtu_drop(ipv6_rig):
    """Lowering the WAN's IPv6 MTU retires the installed LAN-to-WAN direction.

    The sysctl moves the path MTU IPv6 forwarding reads, and no event reports
    it, so the stats pass has to notice an installed direction programmed with
    more than its egress now carries. Left installed, it would forward what
    Linux answers with Packet Too Big. The flow comes back with only the
    WAN-to-LAN direction in hardware, the LAN still delivering a full frame
    into the smaller path, and an oversized packet gets its Packet Too Big.
    """
    r = ipv6_rig
    sport, dport = PORTS["egress"]
    loop = asyncio.get_running_loop()
    echo = PayloadEcho()
    transport, _ = await loop.create_datagram_endpoint(
        lambda: echo, local_addr=(WAN_IPV6, dport), family=socket.AF_INET6)
    wan_mtu = f"net.ipv6.conf.{TARGET_WAN_IF}.mtu"
    original = (await command(r.target, r.session, "sysctl", "-n", wan_mtu))["stdout"].strip()
    assert original == "1500", original

    async def send(count=8):
        return await _udp_exchange(r, sport, WAN_IPV6, dport, count, (WAN_IPV6, dport),
                                   "flowtable_v6_egress")

    try:
        await _offload_table(r, f'ip6 saddr {LAN_IPV6} udp sport {sport} udp dport {dport}')
        both = await _drive(r, send, lambda s: s["entries"] == 2, "both directions should be in hardware")
        await command(r.target, r.session, "sysctl", "-w", f"{wan_mtu}=1400")
        retired = await r.wait(lambda s: s["mtu_invalidations"] > both["mtu_invalidations"])
        lowered = await _drive(r, send, lambda s: (s["entries"] == 1 and s["flows"][0]["in"] == TARGET_WAN_IF),
                               "only the WAN-to-LAN direction should be in hardware")
        r.record("ipv6-egress-mtu", {"both": both, "retired": retired, "lowered": lowered})
        script = f'''
from scapy.all import Ether, IPv6, UDP, Raw, ICMPv6PacketTooBig, srp1
packet = IPv6(src={LAN_IPV6!r}, dst={WAN_IPV6!r})/UDP(sport={sport}, dport={dport})/Raw(b'E' * 1420)
answer = srp1(Ether(dst={r.dut_lan_mac!r})/packet, iface={LAN_NIC!r}, timeout=3, verbose=False)
assert answer is not None and ICMPv6PacketTooBig in answer, answer
assert answer[ICMPv6PacketTooBig].mtu == 1400, answer.show(dump=True)
'''
        result = await lan_run_python(r.lan, script, timeout=20, label="flowtable_v6_egress")
        assert result.rc == 0, result.stdout
        await asyncio.sleep(0.5)
        assert not echo.received[b"E" * 1420], echo.received
    finally:
        transport.close()
        await _drop_tables(r)
        await command(r.target, r.session, "sysctl", "-w", f"{wan_mtu}={original}", check=False)


async def test_shares_the_admission_budget(ipv6_rig):
    """Both families draw on one budget and one pair of software indexes, so
    many concurrent IPv6 flows must each consume two directions and nothing
    else. This is accounting at a readable scale, not a capacity fill."""
    r = ipv6_rig
    base, dport = PORTS["budget"]
    count = 24
    loop = asyncio.get_running_loop()
    echo = Echo()
    transport, _ = await loop.create_datagram_endpoint(
        lambda: echo, local_addr=(WAN_IPV6, dport), family=socket.AF_INET6)
    try:
        await _offload_table(r, f'ip6 saddr {LAN_IPV6} udp dport {dport}')
        script = f'''
import json, socket, struct, time
sockets = []
for n in range({count}):
    s = socket.socket(socket.AF_INET6, socket.SOCK_DGRAM)
    s.settimeout(2)
    s.bind(({LAN_IPV6!r}, {base} + n))
    sockets.append(s)
echoed = lost = 0
for _round in range(6):
    for n, s in enumerate(sockets):
        payload = struct.pack('!Q', n) + b'ASK-v6-budget'.ljust(48, b'.')
        s.sendto(payload, ({WAN_IPV6!r}, {dport}))
        try:
            data, addr = s.recvfrom(2048)
        except TimeoutError:
            lost += 1
            continue
        assert data == payload, (n, data)
        echoed += 1
    time.sleep(0.05)
for s in sockets:
    s.close()
print(json.dumps({{'echoed': echoed, 'lost': lost}}))
'''
        async def send():
            result = await lan_run_python(r.lan, script, timeout=120, label="flowtable_v6_budget")
            assert result.rc == 0, result.stdout

        # Each round drives every connection, so a round is long; the budget
        # still has to hold a lost-RTNL retry on any one of them.
        state = await _drive(r, send, lambda s: s["entries"] == 2 * count,
                             "IPv6 budget did not fill", timeout=60)
        # One budget, one index pair: every IPv6 direction is counted like an
        # IPv4 one, holds its own references, and none is double-counted.
        assert state["handle_refs"] == state["neighbour_refs"] == 2 * count, state
        assert state["max_entries"] == 32768, state
        assert state["errors"] == r.errors, state
        assert state["fatal"] == state["quarantine"] == 0, state
        assert all(f["family"] == "6" for f in state["flows"]), state
        # Every reverse direction legitimately shares one source endpoint --
        # the echo server -- so identity is the pair, not the source alone.
        assert len({(f["src"], f["dst"]) for f in state["flows"]}) == 2 * count, state
        assert len({f["src"] for f in state["flows"] if f["in"] == TARGET_LAN_IF}) == count, state
        r.record("ipv6-budget", state)
    finally:
        transport.close()
        await _drop_tables(r)


async def test_hairpin(hairpin6):
    """Source and destination translation together, with both directions
    leaving by the port they arrived on."""
    r = hairpin6
    external = DUT_IPV6_WAN
    client, server = HAIRPIN["client"]["lan"], HAIRPIN["server"]["lan"]
    public = _endpoint(external, HAIRPIN_PUBLIC)
    mapped = _endpoint(HAIRPIN_GATEWAY, HAIRPIN_MAPPED)
    origin = _endpoint(client, HAIRPIN_SPORT)
    target = _endpoint(server, HAIRPIN_DPORT)
    destination = (f'ip6 saddr {client} ip6 daddr {external} udp sport {HAIRPIN_SPORT} '
                   f'udp dport {HAIRPIN_PUBLIC} dnat to [{server}]:{HAIRPIN_DPORT};')
    source = (f'ip6 saddr {client} ip6 daddr {server} udp sport {HAIRPIN_SPORT} '
              f'udp dport {HAIRPIN_DPORT} snat to [{HAIRPIN_GATEWAY}]:{HAIRPIN_MAPPED};')
    try:
        await _nft(r, f'table ip6 {NAT_TABLE} {{ '
                      f'chain prerouting {{ type nat hook prerouting priority -110; {destination} }}; '
                      f'chain postrouting {{ type nat hook postrouting priority 90; {source} }}; }}')
        # The forward hook runs after prerouting, so the destination here is
        # already the translated one: matching the public port never fires.
        await _offload_table(r, f'ip6 saddr {client} ip6 daddr {server} '
                                f'udp sport {HAIRPIN_SPORT} udp dport {HAIRPIN_DPORT}')
        state = await _drive(r, lambda: _hairpin_exchange(r, external, 8),
                             lambda s: s["entries"] == 2, "IPv6 hairpin did not install")
        rows = {(f["src"], f["dst"]): f for f in state["flows"]}
        # Client to server carries both translations at once; the reply carries
        # both inverses. Every direction enters and leaves by the LAN port.
        assert rows.keys() == {(origin, public), (target, mapped)}, state
        assert all(f["in"] == f["out"] == TARGET_LAN_IF for f in state["flows"]), state
        assert all(f["family"] == "6" for f in state["flows"]), state
        forward, reverse = rows[(origin, public)], rows[(target, mapped)]
        assert (forward["new_src"], forward["new_dst"]) == (mapped, target), state
        assert (reverse["new_src"], reverse["new_dst"]) == (public, origin), state
        r.record("ipv6-hairpin-admission", state)

        before = {(f["src"], f["dst"]): f for f in state["flows"]}
        report = await _hairpin_exchange(r, external, 64)
        assert report == {"echoed": 64, "lost": 0}, report
        after_state = await r.state()
        after = {(f["src"], f["dst"]): f for f in after_state["flows"]}
        delta = {key: int(after[key]["packets"]) - int(before[key]["packets"]) for key in before}
        assert delta == {(origin, public): 64, (target, mapped): 64}, (delta, after_state)
        assert after_state["errors"] == r.errors, after_state
        r.record("ipv6-hairpin-hardware", {"after": after_state,
                                           "hardware_delta": {str(k): v for k, v in delta.items()}})
    finally:
        await _drop_tables(r)
