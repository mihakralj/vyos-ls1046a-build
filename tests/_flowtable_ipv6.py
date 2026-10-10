"""Shared support for flowtable ipv6."""

from __future__ import annotations

import asyncio
import json
import os
import socket
import time
from collections import Counter
from contextlib import asynccontextmanager
from pathlib import Path

import pytest
import pytest_asyncio
from _flowtable_rig import Rig, command, read, stop_boot_daemon
from _topology import (
    DUT_IPV6_LAN,
    DUT_IPV6_WAN,
    LAN_IPV6,
    LAN_NIC,
    TARGET_LAN_IF,
    TARGET_WAN_IF,
    VIRT_IPV6,
    WAN_IPV6,
    has_address,
    lan_ipv6_default,
    lan_run,
    lan_run_python,
)
from ask_orch.client import Agent
from ask_orch.lifecycle import CleanupStack, checked

TABLE = "ask_poc6"
NAT_TABLE = "ask_nat6"
# Distinct per case so a leftover conntrack from one never feeds another.
PORTS = {"routed": (48810, 48811), "snat": (48820, 48821),
         "dnat": (48830, 48831), "tcp": (48840, 48841),
         "masquerade": (48850, 48851), "mtu": (48860, 48861),
         "exceptions": (48870, 48871), "bound": (48880, 48881),
         "neighbour": (48890, 48891), "egress": (48892, 48893),
         "budget": (48900, 48990)}
SNAT_PORT = 49820


class IPv6Rig(Rig):
    """Only the family-independent parts of Rig are reused: state, wait, record.
    Its table, conntrack and exchange helpers are IPv4-shaped by construction."""


def _endpoint(address, port):
    return f"[{address}]:{port}"


async def _nft(r, text):
    return await command(r.target, r.session, "nft", text)


async def _drop_tables(r):
    for family, name in (("inet", TABLE), ("ip6", NAT_TABLE)):
        await command(r.target, r.session, "nft", "delete", "table", family, name,
                      check=False)


async def _offload_table(r, match):
    await _nft(r, f'''table inet {TABLE} {{
 flowtable fast {{ hook ingress priority 0; devices = {{ {TARGET_LAN_IF}, {TARGET_WAN_IF} }};
 flags offload; }}
 chain forward {{ type filter hook forward priority 0; policy accept;
 {match} flow add @fast
 }}
}}''')
    await r.wait(lambda s: s["bindings"] == 2)


class Echo(asyncio.DatagramProtocol):
    """Records the source each datagram arrived from: under SNAT that is the
    translated endpoint, which is what proves the rewrite reached the wire."""

    def __init__(self):
        self.packets = 0
        self.sources = set()

    def connection_made(self, transport):
        self.transport = transport

    def datagram_received(self, data, addr):
        self.packets += 1
        self.sources.add((addr[0], addr[1]))
        self.transport.sendto(data, addr)


class PayloadEcho(Echo):
    """Also counts each payload: what an exception case must or must not have
    delivered across the DUT."""

    def __init__(self):
        super().__init__()
        self.received = Counter()

    def datagram_received(self, data, addr):
        self.received[data] += 1
        super().datagram_received(data, addr)


BLOCK = bytes(range(256)) * 16


class Connection:
    """Controller side of _flowtable_ipv6_tcp_peer, speaking over the tested
    connection itself."""

    def __init__(self, reader, writer, peer):
        self.reader, self.writer, self.peer = reader, writer, peer

    async def transfer(self, op, blocks):
        async with asyncio.timeout(45):
            self.writer.write(json.dumps({"op": op, "blocks": blocks}).encode() + b"\n")
            await self.writer.drain()
            if op == "download":
                for _ in range(blocks):
                    self.writer.write(BLOCK)
                await self.writer.drain()
            else:
                remaining = blocks * len(BLOCK)
                while remaining:
                    chunk = await self.reader.read(min(remaining, 65536))
                    assert chunk, ("peer stopped mid-upload", remaining)
                    remaining -= len(chunk)
            report = json.loads(await self.reader.readline())
            assert report == {"op": op, "bytes": blocks * len(BLOCK)}, report
            return report

    async def close(self):
        async with asyncio.timeout(45):
            self.writer.write(b'{"op": "close"}\n')
            await self.writer.drain()


@asynccontextmanager
async def _tcp_connection(r, sport, dport, *, destination=WAN_IPV6, expect_source=LAN_IPV6):
    accepted = asyncio.Queue()
    server = await asyncio.start_server(
        lambda rd, wr: accepted.put_nowait((rd, wr)), WAN_IPV6, dport, family=socket.AF_INET6)
    script = (f"LAN_IPV6={LAN_IPV6!r}; WAN_IPV6={destination!r}; SPORT={sport}; DPORT={dport}\n" +
              Path(__file__).with_name("_flowtable_ipv6_tcp_peer.py").read_text())
    peer = asyncio.create_task(lan_run_python(r.lan, script, timeout=180,
                                              label="flowtable_v6_tcp"))
    writer = None
    try:
        reader, writer = await asyncio.wait_for(accepted.get(), 20)
        assert writer.get_extra_info("peername")[:2] == (expect_source, sport)
        assert json.loads(await asyncio.wait_for(reader.readline(), 10)) == {"ready": True}
        yield Connection(reader, writer, peer)
    finally:
        if writer:
            writer.close()
            try:
                await asyncio.wait_for(writer.wait_closed(), 5)
            except (ConnectionResetError, BrokenPipeError, TimeoutError):
                writer.transport.abort()
        server.close()
        try:
            await asyncio.wait_for(server.wait_closed(), 10)
        finally:
            # Join the UART operation before another cleanup can use it.
            result = await peer
            r.record("ipv6-tcp-peer-last", {"rc": result.rc, "stdout": result.stdout})


@pytest_asyncio.fixture
async def ipv6_rig(target_agent, aiohttp_session, lan, splat_window):
    """LAN VM <-> DUT <-> WAN host over two ULA /64s, with both endpoints
    pinned as permanent neighbours so admission never races ND. Teardown
    reverses only what actually came up."""
    r = IPv6Rig()
    r.target, r.session, r.lan, r.sequence = target_agent, aiohttp_session, lan, 1
    r.recovery_console = None
    await stop_boot_daemon()
    initial = await r.state()
    assert initial["entries"] == initial["bindings"] == initial["invalidated"] == 0, initial
    # The adapter's error count is cumulative for the boot and never reset;
    # tests that inject failures may already have run. Only errors raised
    # from here are this file's own.
    r.errors = initial["errors"]
    wan = Agent("wan", f"http://{os.environ.get('ASK_WAN_IP', '127.0.0.1')}:9110")
    r.wan = wan
    cleanup = CleanupStack()

    async def target(*argv, check=True):
        return await command(r.target, r.session, *argv, check=check)

    try:
        lan_link = json.loads((await lan_run(lan, f"ip -j link show dev {LAN_NIC}"))
                              .stdout.strip())[0]
        r.lan_mac = lan_link["address"]
        r.dut_lan_mac = (await read(r.target, r.session,
                                    f"/sys/class/net/{TARGET_LAN_IF}/address")).strip()
        r.dut_wan_mac = (await read(r.target, r.session,
                                    f"/sys/class/net/{TARGET_WAN_IF}/address")).strip()
        # The IPv6 endpoint shares the interface that already carries the IPv4
        # WAN address, so the two topologies describe the same physical path.
        wan_v4 = os.environ.get("ASK_WAN_IPERF_IP", "")
        addresses = json.loads((await command(wan, r.session, "ip", "-j", "-4", "addr"))["stdout"])
        r.wan_if = next(i["ifname"] for i in addresses
                        if any(a.get("local") == wan_v4 for a in i["addr_info"]))
        r.wan_mac = json.loads((await command(wan, r.session, "ip", "-j", "link", "show",
                                              "dev", r.wan_if))["stdout"])[0]["address"]

        previous = (await target("sysctl", "-n", "net.ipv6.conf.all.forwarding"))["stdout"].strip()
        cleanup.push(lambda v=previous: target("sysctl", "-w",
                                                 f"net.ipv6.conf.all.forwarding={v}"))
        await target("sysctl", "-w", "net.ipv6.conf.all.forwarding=1")

        # nodad on every static address: DAD would leave it tentative for
        # ~1.5s and the first flow of the session would silently not come up.
        # One the image configures itself (the WAN port's) is left as it is,
        # or teardown would take it from every test after this one.
        for address, interface in ((DUT_IPV6_LAN, TARGET_LAN_IF), (DUT_IPV6_WAN, TARGET_WAN_IF)):
            if await has_address(r.target, r.session, interface, address):
                continue
            await target("ip", "-6", "addr", "add", f"{address}/64", "dev", interface, "nodad")
            cleanup.push(lambda a=address, i=interface: target(
                "ip", "-6", "addr", "del", f"{a}/64", "dev", i, check=False))

        await lan_run(lan, f"ip -6 addr del {LAN_IPV6}/64 dev {LAN_NIC} 2>/dev/null; true")
        result = await lan_run(lan, f"ip -6 addr add {LAN_IPV6}/64 dev {LAN_NIC} nodad")
        assert result.rc == 0, result.stdout

        async def _lan_address():
            return await lan_run(lan, f"ip -6 addr del {LAN_IPV6}/64 dev {LAN_NIC}")
        cleanup.push(_lan_address)

        await lan_ipv6_default(cleanup, lan, DUT_IPV6_LAN, LAN_NIC)
        checked(await lan_run(lan, f"ip -6 neigh replace {DUT_IPV6_LAN} lladdr {r.dut_lan_mac} "
                                   f"nud permanent dev {LAN_NIC}"))
        cleanup.push(lambda: lan_run(lan, f"ip -6 neigh del {DUT_IPV6_LAN} dev {LAN_NIC}"))

        await command(wan, r.session, "ip", "-6", "addr", "del", f"{WAN_IPV6}/64",
                      "dev", r.wan_if, check=False)
        await command(wan, r.session, "ip", "-6", "addr", "add", f"{WAN_IPV6}/64",
                      "dev", r.wan_if, "nodad")
        cleanup.push(lambda: command(wan, r.session, "ip", "-6", "addr", "del",
                                       f"{WAN_IPV6}/64", "dev", r.wan_if, check=False))
        await command(wan, r.session, "ip", "-6", "route", "replace", f"{LAN_IPV6}/128",
                      "via", DUT_IPV6_WAN, "dev", r.wan_if)
        cleanup.push(lambda: command(wan, r.session, "ip", "-6", "route", "del",
                                       f"{LAN_IPV6}/128", "dev", r.wan_if, check=False))
        await command(wan, r.session, "ip", "-6", "neigh", "replace", DUT_IPV6_WAN,
                      "lladdr", r.dut_wan_mac, "nud", "permanent", "dev", r.wan_if)
        cleanup.push(lambda: command(wan, r.session, "ip", "-6", "neigh", "del",
                                       DUT_IPV6_WAN, "dev", r.wan_if, check=False))

        for address, mac, interface in ((LAN_IPV6, r.lan_mac, TARGET_LAN_IF),
                                        (WAN_IPV6, r.wan_mac, TARGET_WAN_IF),
                                        (VIRT_IPV6, r.wan_mac, TARGET_WAN_IF)):
            await target("ip", "-6", "neigh", "replace", address, "lladdr", mac,
                         "nud", "permanent", "dev", interface)
            cleanup.push(lambda a=address, i=interface: target(
                "ip", "-6", "neigh", "del", a, "dev", i, check=False))

        accounting = (await read(r.target, r.session,
                                 "/proc/sys/net/netfilter/nf_conntrack_acct")).strip()
        await target("sysctl", "-w", "net.netfilter.nf_conntrack_acct=1")
        cleanup.push(lambda v=accounting: target(
            "sysctl", "-w", f"net.netfilter.nf_conntrack_acct={v}"))

        await _drop_tables(r)
        r.record("ipv6-fixture", {"lan": LAN_IPV6, "wan": WAN_IPV6, "virtual": VIRT_IPV6,
                                  "lan_mac": r.lan_mac, "wan_mac": r.wan_mac,
                                  "dut_lan_mac": r.dut_lan_mac, "dut_wan_mac": r.dut_wan_mac,
                                  "wan_if": r.wan_if, "initial": initial})
        yield r
    finally:
        cleanup.push(lambda: r.wait(lambda s: not s["bindings"] and not s["entries"]))
        cleanup.push(lambda: _drop_tables(r))
        await cleanup.teardown("IPv6 fixture")


async def _udp_exchange(r, sport, destination, dport, count, expect_from, label):
    """Echo `count` datagrams from the LAN VM. A wrong payload or a reply from
    the wrong endpoint is always fatal — under DNAT `expect_from` is the
    pre-translation destination, which only matches if the reverse rewrite was
    applied. A timeout is merely counted, so the caller can tolerate loss while
    the flow is still being admitted and forbid it once it is installed."""
    script = f'''
import json, socket, struct, time
s = socket.socket(socket.AF_INET6, socket.SOCK_DGRAM)
s.settimeout(2)
s.bind(({LAN_IPV6!r}, {sport}))
echoed = lost = 0
for n in range({count}):
    payload = struct.pack('!Q', n) + b'ASK-flowtable-v6'.ljust(48, b'.')
    s.sendto(payload, ({destination!r}, {dport}))
    try:
        data, addr = s.recvfrom(2048)
    except TimeoutError:
        lost += 1
        continue
    assert data == payload, (n, data)
    assert (addr[0], addr[1]) == ({expect_from[0]!r}, {expect_from[1]}), (n, addr)
    echoed += 1
    time.sleep(0.01)
s.close()
print(json.dumps({{'echoed': echoed, 'lost': lost}}))
'''
    result = await lan_run_python(r.lan, script, timeout=count * 0.3 + 40, label=label)
    assert result.rc == 0, result.stdout
    return json.loads(result.stdout.strip().splitlines()[-1])


def _assert_pair(state, proto, forward, reverse):
    """Both directions installed, family-tagged, and carrying the expected
    translation. `forward`/`reverse` are (src, dst, new_src, new_dst, nexthop)."""
    assert state["entries"] == 2, state
    rows = {f["in"]: f for f in state["flows"]}
    assert set(rows) == {TARGET_LAN_IF, TARGET_WAN_IF}, state
    for interface, expected in ((TARGET_LAN_IF, forward), (TARGET_WAN_IF, reverse)):
        flow = rows[interface]
        assert flow["family"] == "6", state
        assert flow["proto"] == str(proto), state
        assert tuple(flow[k] for k in ("src", "dst", "new_src", "new_dst", "nexthop")) \
            == expected, state
    return rows


def _hardware_delta(before, after):
    """Per-direction classifier packet counts, keyed by ingress interface."""
    return {i: int(after[i]["packets"]) - int(before[i]["packets"]) for i in before}


async def _drive(r, send, settled, failure, timeout=10):
    """Exchange traffic until `settled(state)` holds, failing with `failure`
    once `timeout` seconds have passed. A deadline, not a round count: an
    admission can lose rtnl_trylock in the backend, which declines it, and the
    software path offers the flow again only about a second later -- or, where
    an IPsec policy keeps the flow off that path, the generation is retired
    and re-offered after two flowtable GC ticks. A round is one short LAN
    exchange plus a state read, well under a second, so the default covers
    that retry several times."""
    deadline = time.monotonic() + timeout
    while True:
        await send()
        state = await r.state()
        if settled(state):
            return state
        if time.monotonic() >= deadline:
            pytest.fail(f"{failure}: {state}")


async def _settle(r, proto, forward, reverse, send, label):
    """Drive traffic until both directions are installed, then record them."""
    state = await _drive(r, send, lambda s: s["entries"] == 2, "IPv6 flow did not install")
    rows = _assert_pair(state, proto, forward, reverse)
    r.record(label, state)
    return state, rows


def _split_endpoint(text):
    """Parse a proc row's `[addr]:port` into its parts."""
    address, _, port = text.rpartition(":")
    return address.strip("[]"), int(port)


# Client and server both behind the LAN port, so a translated flow has to
# leave by the interface it arrived on. Distinct MACs and no on-link route to
# each other's address: reaching the server means going through the gateway.
HAIRPIN_GATEWAY = DUT_IPV6_LAN
HAIRPIN = {"client": {"netns": "ask-ft6-client", "iface": "askft6c",
                      "lan": "fc00:dead::12", "mac": "02:9d:99:b2:d6:12"},
           "server": {"netns": "ask-ft6-server", "iface": "askft6s",
                      "lan": "fc00:dead::13", "mac": "02:9d:99:b2:d6:13"}}
HAIRPIN_PUBLIC, HAIRPIN_MAPPED = 48870, 49870
HAIRPIN_SPORT, HAIRPIN_DPORT = 48871, 48872


def _hairpin_script(body):
    return (f"CLIENT={HAIRPIN['client']!r}\nSERVER={HAIRPIN['server']!r}\n"
            f"GATEWAY={HAIRPIN_GATEWAY!r}\nLAN_NIC={LAN_NIC!r}\n" + body)


@pytest_asyncio.fixture
async def hairpin6(ipv6_rig):
    r = ipv6_rig
    setup = _hairpin_script('''
import json, subprocess
def run(*args):
    subprocess.run(args, check=True, capture_output=True, text=True)
created = []
for peer in (CLIENT, SERVER):
    subprocess.run(['ip', 'netns', 'del', peer['netns']], capture_output=True, text=True)
for peer in (CLIENT, SERVER):
    run('ip', 'netns', 'add', peer['netns'])
    created.append(peer)
    run('ip', 'link', 'add', peer['iface'], 'link', LAN_NIC, 'netns', peer['netns'],
        'type', 'macvlan', 'mode', 'bridge')
    run('ip', '-n', peer['netns'], 'link', 'set', 'lo', 'up')
    run('ip', '-n', peer['netns'], 'link', 'set', peer['iface'], 'address', peer['mac'], 'up')
    run('ip', '-n', peer['netns'], 'addr', 'add', peer['lan'] + '/64',
        'dev', peer['iface'], 'nodad')
    run('ip', '-n', peer['netns'], 'route', 'add', 'default', 'via', GATEWAY,
        'dev', peer['iface'])
print(json.dumps({'created': [p['netns'] for p in created]}))
''')
    result = await lan_run_python(r.lan, setup, label="flowtable_v6_hairpin_setup", timeout=40)
    assert result.rc == 0, result.stdout
    cleanup = []
    for peer in HAIRPIN.values():
        await command(r.target, r.session, "ip", "-6", "neigh", "replace", peer["lan"],
                      "lladdr", peer["mac"], "nud", "permanent", "dev", TARGET_LAN_IF)
        cleanup.append(peer["lan"])
    try:
        yield r
    finally:
        for address in cleanup:
            await command(r.target, r.session, "ip", "-6", "neigh", "del", address,
                          "dev", TARGET_LAN_IF, check=False)
        teardown = _hairpin_script('''
import json, subprocess
left = []
for peer in (CLIENT, SERVER):
    result = subprocess.run(['ip', 'netns', 'del', peer['netns']], capture_output=True, text=True)
    if result.returncode:
        left.append(peer['netns'])
print(json.dumps({'left': left}))
''')
        await lan_run_python(r.lan, teardown, label="flowtable_v6_hairpin_cleanup", timeout=30)


async def _hairpin_exchange(r, external, count):
    """Both endpoints live on the LAN VM in separate namespaces, so one script
    owns the echo server and the client and stops both together."""
    script = _hairpin_script(f'''
import json, os, socket, struct, time
def enter(netns):
    with open('/var/run/netns/' + netns, 'rb') as handle:
        os.setns(handle.fileno(), os.CLONE_NEWNET)
child = os.fork()
if child == 0:
    try:
        enter(SERVER['netns'])
        server = socket.socket(socket.AF_INET6, socket.SOCK_DGRAM)
        server.bind((SERVER['lan'], {HAIRPIN_DPORT}))
        while True:
            data, peer = server.recvfrom(2048)
            server.sendto(data, peer)
    finally:
        os._exit(0)
time.sleep(1.0)
enter(CLIENT['netns'])
client = socket.socket(socket.AF_INET6, socket.SOCK_DGRAM)
client.settimeout(2)
client.bind((CLIENT['lan'], {HAIRPIN_SPORT}))
echoed = lost = 0
try:
    for n in range({count}):
        payload = struct.pack('!Q', n) + b'ASK-v6-hairpin'.ljust(48, b'.')
        client.sendto(payload, ({external!r}, {HAIRPIN_PUBLIC}))
        try:
            data, addr = client.recvfrom(2048)
        except TimeoutError:
            lost += 1
            continue
        assert data == payload, (n, data)
        # The reply must appear to come from the public endpoint the client
        # addressed, never from the server's own address.
        assert (addr[0], addr[1]) == ({external!r}, {HAIRPIN_PUBLIC}), (n, addr)
        echoed += 1
        time.sleep(0.01)
finally:
    os.kill(child, 9)
    os.waitpid(child, 0)
print(json.dumps({{'echoed': echoed, 'lost': lost}}))
''')
    result = await lan_run_python(r.lan, script, timeout=count * 0.3 + 60,
                                  label="flowtable_v6_hairpin")
    assert result.rc == 0, result.stdout
    return json.loads(result.stdout.strip().splitlines()[-1])
