"""Shared support for flowtable jumbo frames.

Every hop of a jumbo path is raised for one test and put back on the same
boot, however the test ends: the DUT's two ports, the LAN client's NIC, and
the WAN host's bridge with its members. The WAN host's bridge also carries the
host's own LAN, which is not a jumbo network, so before anything is raised the
host's existing routes over it are pinned to a standard frame; only /32 routes
the test owns towards the DUT carry the jumbo MTU.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import os
import re
import socket
import struct
import threading
import time
from contextlib import asynccontextmanager

from _flowtable_connections import healthy
from _flowtable_mtu import fragmenter
from _flowtable_rig import DPORT, SPORT, TABLE, WAN_IP, artifact_dir, command, ct_listing, read
from _flowtable_tcp import software_tx
from _topology import LAN_NIC, TARGET_LAN_IF, TARGET_WAN_IF, lan_run, lan_run_python
from ask_orch.client import Agent
from ask_orch.lifecycle import checked

JUMBO = 9000
STANDARD = 1500
# The largest UDP payload a jumbo IPv4 and IPv6 packet carries unfragmented.
JUMBO_UDP = JUMBO - 20 - 8
JUMBO_UDP6 = JUMBO - 40 - 8
# What the WAN host's own LAN keeps while the bridge carries jumbo frames.
STANDARD_ADVMSS = STANDARD - 40

# IP_MTU_DISCOVER values. PROBE sets DF and sends at the device MTU whatever
# path MTU the socket's route has learned; INTERFACE clears DF and also sends
# at the device MTU, so a datagram leaves the LAN client whole either way and
# what happens to it is the DUT's doing.
IP_MTU_DISCOVER = 10
PMTUDISC_PROBE = 3
PMTUDISC_INTERFACE = 4

NAT_TABLE = "ask_jumbo_nat"
# The TCP rate case's server port, apart from the throughput test's.
TCP_PORT = DPORT + 3100


def payload(serial, size):
    """A datagram of `size` bytes: its serial, then a pattern with no zero
    byte, so a payload the microcode's fragmenter zeroed cannot pass for it."""
    pattern = bytes(range(1, 256))
    body = (pattern * (size // len(pattern) + 2))[serial % len(pattern):][:size - 8]
    return struct.pack("!Q", serial) + body


def wan_agent():
    return Agent("wan", f"http://{os.environ.get('ASK_WAN_IP', '127.0.0.1')}:9110")


# ---- waiting for links -------------------------------------------------------

async def dut_up(r, dev, timeout=20):
    """Wait for a DUT port to be operationally up. An MTU change can renegotiate
    a port's link, and traffic sent before it is back is simply lost."""
    deadline = time.monotonic() + timeout
    while True:
        state = (await read(r.target, r.session, f"/sys/class/net/{dev}/operstate")).strip()
        if state == "up":
            return
        assert time.monotonic() < deadline, (dev, state)
        await asyncio.sleep(0.25)


async def lan_up(r, dev=LAN_NIC, timeout=20):
    """The LAN client's NIC reinitialises on an MTU change and drops its link."""
    result = await lan_run_python(r.lan, f'''
import pathlib, time
state = pathlib.Path('/sys/class/net/{dev}/operstate')
for _ in range({int(timeout * 4)}):
    if state.read_text().strip() == 'up':
        print('up')
        break
    time.sleep(0.25)
else:
    raise SystemExit('{dev} stayed ' + state.read_text().strip())
''', timeout=timeout + 10, label="jumbo_lan_up")
    assert result.rc == 0, result.stdout


async def links_up(r):
    """Both ends of the LAN cable and the DUT's WAN port, then a moment for
    the switch and neighbours to settle."""
    await lan_up(r)
    for dev in (TARGET_LAN_IF, TARGET_WAN_IF):
        await dut_up(r, dev)
    await asyncio.sleep(1)


# ---- MTU changes, each restored by the stack ---------------------------------

async def dut_mtu(stack, r, dev, mtu):
    """Set a DUT port's MTU; the stack puts back what it found."""
    old = int((await read(r.target, r.session, f"/sys/class/net/{dev}/mtu")).strip())
    if old == mtu:
        return

    async def restore():
        result = await command(r.target, r.session, "ip", "link", "set", "dev", dev, "mtu", str(old),
                               check=False)
        if not result["rc"]:
            await dut_up(r, dev)
        return result
    await command(r.target, r.session, "ip", "link", "set", "dev", dev, "mtu", str(mtu))
    stack.push(restore)
    assert int((await read(r.target, r.session, f"/sys/class/net/{dev}/mtu")).strip()) == mtu
    await dut_up(r, dev)


async def lan_mtu(stack, r, mtu, dev=LAN_NIC):
    old = int((await lan_run(r.lan, f"cat /sys/class/net/{dev}/mtu")).stdout.strip())
    if old == mtu:
        return

    async def restore():
        result = checked(await lan_run(r.lan, f"ip link set dev {dev} mtu {old}"))
        await lan_up(r, dev)
        return result
    checked(await lan_run(r.lan, f"ip link set dev {dev} mtu {mtu}"))
    stack.push(restore)
    await lan_up(r, dev)


# ---- the WAN host -----------------------------------------------------------

def route_spec(route):
    """`ip route` arguments that name an `ip -j route` row as it stands,
    without its metrics: `replace` with these alone puts a route back with
    none."""
    argv = [route["dst"]]
    if "gateway" in route:
        argv += ["via", route["gateway"]]
    argv += ["dev", route["dev"]]
    for key, word in (("protocol", "proto"), ("scope", "scope"), ("prefsrc", "src"),
                      ("metric", "metric")):
        if key in route:
            argv += [word, str(route[key])]
    if "onlink" in route.get("flags", []):
        argv.append("onlink")
    return argv


async def _main_routes(agent, session, dev):
    rows = json.loads((await command(agent, session, "ip", "-j", "-4", "route", "show",
                                     "table", "main"))["stdout"])
    return [row for row in rows if row.get("dev") == dev]


def _dst(route):
    return route["dst"].removesuffix("/32")


async def pin_routes(stack, agent, session, dev, owned):
    """Lock every unicast main-table route over `dev` to a standard frame
    before `dev` is raised, except the /32s in `owned`, which a test carries
    its jumbo traffic on. A route that already carries metrics or nexthops of
    its own cannot be pinned without taking over what its owner set, and left
    unpinned it would send the host's own traffic at the raised MTU, so the
    test refuses to raise anything while one exists."""
    original = await _main_routes(agent, session, dev)
    foreign = [route for route in original
               if _dst(route) not in owned and route.get("type", "unicast") == "unicast"
               and ("metrics" in route or "nexthops" in route or "nhid" in route)]
    assert not foreign, ("WAN host routes with their own metrics; not raising the MTU under them",
                         foreign)
    pinned = []
    for route in original:
        if _dst(route) in owned or route.get("type", "unicast") != "unicast":
            continue
        spec = route_spec(route)
        await command(agent, session, "ip", "route", "replace", *spec,
                      "mtu", "lock", str(STANDARD), "advmss", str(STANDARD_ADVMSS))
        stack.push(lambda spec=spec: command(agent, session, "ip", "route", "replace", *spec,
                                             check=False))
        pinned.append(route)
    return original, pinned


async def wan_jumbo(stack, r, *, owned):
    """Carry jumbo frames between the WAN host and the DUT.

    `r.wan_if` is the device holding the WAN endpoint's address; on this
    bench it is a bridge over the switch-facing NIC and the LAN client's
    control tap, and the bridge's MTU cannot usefully exceed its members'. The
    host's own LAN shares that bridge, so its routes over it are pinned to a
    standard frame first, and its IPv6 MTU, which follows the device's, is put
    back right after the raise. A restore that leaves any of it different
    fails the test's teardown."""
    wan = wan_agent()
    bridge = r.wan_if

    async def link_mtu(dev):
        rows = json.loads((await command(wan, r.session, "ip", "-j", "link", "show", "dev", dev))["stdout"])
        return rows[0]["mtu"]

    members = [row["ifname"] for row in json.loads(
        (await command(wan, r.session, "ip", "-j", "link", "show", "master", bridge))["stdout"])]
    # The bench's injection device is this segment's too; one elsewhere means
    # the endpoint address is not on the segment the DUT is cabled to.
    inject = os.environ.get("ASK_WAN_INJECT_IF")
    assert not inject or inject in (bridge, *members), (inject, bridge, members)
    mtus = {dev: await link_mtu(dev) for dev in (*members, bridge)}
    ipv6_key = f"net.ipv6.conf.{bridge}.mtu"
    ipv6_mtu = (await command(wan, r.session, "sysctl", "-n", ipv6_key, check=False))["stdout"].strip()

    original = None

    async def verify():
        """Last out: the host is as it was found."""
        now = {dev: await link_mtu(dev) for dev in mtus}
        assert now == mtus, ("WAN host MTUs not restored", mtus, now)
        if ipv6_mtu:
            current = (await command(wan, r.session, "sysctl", "-n", ipv6_key))["stdout"].strip()
            assert current == ipv6_mtu, ("WAN host IPv6 MTU not restored", ipv6_mtu, current)
        if original is not None:
            routes = {(_dst(x), x.get("gateway"), x.get("metric")): x
                      for x in await _main_routes(wan, r.session, bridge)}
            for route in original:
                key = (_dst(route), route.get("gateway"), route.get("metric"))
                assert key in routes, ("WAN host route missing after restore", route, routes)
                assert routes[key].get("metrics") == route.get("metrics"), \
                    ("WAN host route metrics not restored", route, routes[key])
    stack.push(verify)

    original, pinned = await pin_routes(stack, wan, r.session, bridge, owned)
    # Every link MTU change resets the IPv6 MTU to the link's, including the
    # restores below, so it goes back last: registered before them.
    if ipv6_mtu:
        stack.push(lambda: command(wan, r.session, "sysctl", "-w", f"{ipv6_key}={ipv6_mtu}",
                                   check=False))
    for dev in members:
        if mtus[dev] < JUMBO:
            await command(wan, r.session, "ip", "link", "set", "dev", dev, "mtu", str(JUMBO))
            stack.push(lambda dev=dev: command(wan, r.session, "ip", "link", "set", "dev", dev,
                                               "mtu", str(mtus[dev]), check=False))
    # A bridge whose MTU nobody set follows its smallest member up. Only one
    # that does not is set by hand, and only that one is set back by hand:
    # doing so marks the MTU as user-set and stops it following its members.
    if await link_mtu(bridge) < JUMBO:
        await command(wan, r.session, "ip", "link", "set", "dev", bridge, "mtu", str(JUMBO))
        stack.push(lambda: command(wan, r.session, "ip", "link", "set", "dev", bridge,
                                   "mtu", str(mtus[bridge]), check=False))
    assert await link_mtu(bridge) == JUMBO, bridge
    if ipv6_mtu:
        await command(wan, r.session, "sysctl", "-w", f"{ipv6_key}={ipv6_mtu}")
    r.record("jumbo-wan-host", {"bridge": bridge, "members": members, "mtus": mtus,
                                "ipv6_mtu": ipv6_mtu, "routes": original, "pinned": pinned})
    return wan


async def wan_route(stack, r, address, **attributes):
    """A WAN host /32 to `address` over the DUT's WAN segment that the test
    owns; a fresh route has no path MTU learned by an earlier test."""
    wan = wan_agent()
    existing = json.loads((await command(wan, r.session, "ip", "-j", "route", "show", "exact",
                                         address + "/32"))["stdout"])
    assert not existing, ("jumbo path requires an unused endpoint host route", existing)
    extra = [str(item) for pair in attributes.items() for item in pair]
    await command(wan, r.session, "ip", "route", "add", address + "/32", "dev", r.wan_if, *extra)
    stack.push(lambda: command(wan, r.session, "ip", "route", "del", address + "/32",
                               "dev", r.wan_if, check=False))


async def wan_lan_route(stack, r, **attributes):
    """Give the WAN host's route back to the LAN client attributes for one
    test -- the rig's route, or one that was there before it -- and put its
    own back afterwards."""
    wan = wan_agent()
    rows = json.loads((await command(wan, r.session, "ip", "-j", "route", "show", "exact",
                                     r.lan_ip + "/32"))["stdout"])
    assert len(rows) == 1 and "metrics" not in rows[0], rows
    spec = route_spec({**rows[0], "dst": r.lan_ip + "/32"})
    extra = [str(item) for pair in attributes.items() for item in pair]
    await command(wan, r.session, "ip", "route", "replace", *spec, *extra)
    stack.push(lambda: command(wan, r.session, "ip", "route", "replace", *spec, check=False))


async def lan_route(stack, r, mtu=None):
    """A LAN client /32 to the WAN endpoint through the DUT: a fresh nexthop,
    without the path MTU earlier MTU tests taught the default route."""
    existing = json.loads((await lan_run(r.lan, f"ip -j route show exact {WAN_IP}/32")).stdout.strip() or "[]")
    assert not existing, existing
    checked(await lan_run(r.lan, f"ip route add {WAN_IP}/32 via {r.lan_gateway} dev {LAN_NIC}"
                                 + (f" mtu {mtu}" if mtu else "")))
    stack.push(lambda: lan_run(r.lan, f"ip route del {WAN_IP}/32 dev {LAN_NIC}"))


async def echo_probes(stack, r):
    """The rig's WAN echo answers at its device MTU, whatever path MTU the
    host learned for the LAN client during earlier MTU tests."""
    sock = r.echo.transport.get_extra_info("socket")
    old = sock.getsockopt(socket.SOL_IP, IP_MTU_DISCOVER)
    sock.setsockopt(socket.SOL_IP, IP_MTU_DISCOVER, PMTUDISC_PROBE)

    async def restore():
        sock.setsockopt(socket.SOL_IP, IP_MTU_DISCOVER, old)
    stack.push(restore)


async def dut_wan_address(r):
    rows = json.loads((await command(r.target, r.session, "ip", "-j", "-4", "addr", "show",
                                     "dev", TARGET_WAN_IF))["stdout"])
    return next(a["local"] for a in rows[0]["addr_info"] if a["family"] == "inet")


async def masquerade(stack, r):
    """Translate everything the LAN client sends the WAN endpoint. It sits
    ahead of the rig's own NAT exemption at the iptables priority, so it is the
    binding that wins."""
    assert (await command(r.target, r.session, "nft", "list", "table", "ip", NAT_TABLE,
                          check=False))["rc"]
    await command(r.target, r.session, "nft",
                  f"table ip {NAT_TABLE} {{ chain postrouting {{ type nat hook postrouting priority 90; "
                  f"ip saddr {r.lan_ip} ip daddr {WAN_IP} masquerade; }}; }}")
    stack.push(lambda: command(r.target, r.session, "nft", "delete", "table", "ip", NAT_TABLE,
                               check=False))


async def offload_table(stack, r):
    """The rig's own table, offering its UDP tuple and the TCP rate port."""
    await r.nft(f'''table inet {TABLE} {{
 flowtable fast {{ hook ingress priority 0; devices = {{ {TARGET_LAN_IF}, {TARGET_WAN_IF} }}; flags offload; }}
 chain forward {{ type filter hook forward priority 0; policy accept;
 ip saddr {r.lan_ip} ip daddr {WAN_IP} udp sport {SPORT} udp dport {DPORT} flow add @fast
 ip saddr {r.lan_ip} ip daddr {WAN_IP} tcp dport {TCP_PORT} flow add @fast
 }}
}}''')

    async def drop():
        await r.delete_table()
    stack.push(drop)
    await r.wait(lambda s: s["bindings"] == 2)


async def clear_tcp(r):
    # conntrack(8) exits 1 when it deleted nothing.
    result = await command(r.target, r.session, "conntrack", "-D", "-p", "tcp", "--orig-src", r.lan_ip,
                           "--orig-dst", WAN_IP, "--dport", str(TCP_PORT), check=False)
    assert result["rc"] in (0, 1), result


async def jumbo_path(stack, r, *, nat):
    """Every hop at JUMBO: both DUT ports, the LAN client's NIC and the WAN
    host. Under `nat` the LAN client's traffic to the WAN endpoint leaves as
    the DUT's own WAN address, which the WAN host reaches by a /32 of the
    test's; routed, it keeps the LAN client's address, whose /32 the rig owns
    and which carries the bridge's MTU because it has none of its own."""
    for dev in (TARGET_LAN_IF, TARGET_WAN_IF):
        await dut_mtu(stack, r, dev, JUMBO)
    await lan_mtu(stack, r, JUMBO)
    r.external = await dut_wan_address(r)
    await wan_jumbo(stack, r, owned={r.lan_ip, r.external})
    if nat:
        await wan_route(stack, r, r.external, mtu=JUMBO)
        await masquerade(stack, r)
    stack.push(lambda: clear_tcp(r))
    if getattr(r, "echo", None):
        await echo_probes(stack, r)
    r.wan_source = r.external if nat else r.lan_ip
    await links_up(r)


# ---- traffic ---------------------------------------------------------------

async def datagrams(r, *, first, count, size, mode, echo, sport=SPORT, interval=0.003, label="jumbo"):
    """Send `count` datagrams of `size` bytes from the LAN client's socket to
    the WAN endpoint, serials `first` onwards, at the given IP_MTU_DISCOVER
    mode. With `echo`, each one's echo must come back byte for byte before the
    next is sent."""
    script = "import json, socket, struct, time\n" + inspect.getsource(payload) + f'''
s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
s.setsockopt(socket.SOL_IP, {IP_MTU_DISCOVER}, {mode})
s.bind(({r.lan_ip!r}, {sport}))
s.settimeout(2)
received = 0
for n in range({first}, {first + count}):
    data = payload(n, {size})
    s.sendto(data, ({WAN_IP!r}, {DPORT}))
    if {echo!r}:
        try:
            reply, addr = s.recvfrom(65535)
        except TimeoutError:
            print(json.dumps({{'timeout_sequence': n, 'received': received}}), flush=True)
            raise
        assert addr == ({WAN_IP!r}, {DPORT}), (n, addr)
        assert len(reply) == len(data), (n, len(reply))
        assert reply == data, (n, 'first difference', next(i for i in range(len(data)) if reply[i] != data[i]))
        received += 1
    time.sleep({interval})
s.close()
print(json.dumps({{'sent': {count}, 'received': received}}))
'''
    result = await lan_run_python(r.lan, script, timeout=count * (interval + 0.05) + 20, label=label)
    assert result.rc == 0, result.stdout
    return json.loads(result.stdout.strip().splitlines()[-1])


@asynccontextmanager
async def wan_capture(r, label, source=None):
    """Every IPv4 frame from `source` (the address the LAN client's traffic
    leaves the DUT with) to the WAN endpoint, as the endpoint received it:
    whole datagrams and fragments alike. Saved as a pcap artifact."""
    from scapy.all import AsyncSniffer, wrpcap
    source = source or getattr(r, "wan_source", r.lan_ip)
    ready = threading.Event()
    sniffer = AsyncSniffer(iface=r.wan_if, filter=f"ip src {source} and ip dst {WAN_IP}",
                           store=True, started_callback=ready.set)
    sniffer.start()
    frames = []
    try:
        assert await asyncio.to_thread(ready.wait, 5), "endpoint capture did not start"
        yield frames
        await asyncio.sleep(0.5)
    finally:
        frames.extend(sniffer.stop() or [])
        artifact_dir().mkdir(parents=True, exist_ok=True)
        wrpcap(str(artifact_dir() / f"{label}.pcap"), frames)


def wire_datagrams(frames, size, first, count):
    """The captured datagrams, one per serial: each whole, unfragmented, one
    hop away and carrying exactly what was sent. Only UDP counts -- the
    capture also sees the DUT's own traffic to this host, such as the
    harness's pings -- and a fragment of a datagram is UDP too, so it still
    fails the check."""
    from scapy.all import IP
    serials = []
    for frame in frames:
        ip = frame[IP]
        if ip.proto != socket.IPPROTO_UDP:
            continue
        assert (int(ip.flags) & 1, ip.frag, ip.len, ip.ttl) == (0, 0, size + 28, 63), frame.summary()
        datagram = bytes(ip)[4 * ip.ihl:ip.len]
        serial = struct.unpack("!Q", datagram[8:16])[0]
        assert datagram[8:] == payload(serial, size), (serial, "payload differs on the wire")
        serials.append(serial)
    assert sorted(serials) == list(range(first, first + count)), serials


async def mac_rx(r, dev):
    """The port's mEMAC receive counters by register name, each 64-bit value
    read from its _l/_u halves (the SDK's register dump)."""
    text = await read(r.target, r.session, f"/sys/class/net/{dev}/mac_rx_stats")
    halves = {}
    for value, name, half in re.findall(r"0x([0-9a-fA-F]{8})\s+(\w+?)_([lu])\s*$", text, re.M):
        halves.setdefault(name, {})[half] = int(value, 16)
    assert {"rovr", "rerr"} <= halves.keys(), text
    return {name: (h.get("u", 0) << 32) | h.get("l", 0) for name, h in halves.items()}


async def rx_errors(r, dev):
    """The driver's view of receive errors: its ethtool error counters and
    the netdev's rx_errors, recorded beside the MAC's own."""
    stdout = (await command(r.target, r.session, "ethtool", "-S", dev))["stdout"]
    stats = {}
    for line in stdout.splitlines():
        key, sep, value = line.rpartition(":")
        if sep and "error" in key and value.strip().isdigit():
            stats[key.strip()] = int(value)
    stats["netdev rx_errors"] = int(await read(r.target, r.session,
                                               f"/sys/class/net/{dev}/statistics/rx_errors"))
    return stats


def delta(before, after):
    return {key: after[key] - before[key] for key in before}


async def jumbo_burst(r, label, *, size=JUMBO_UDP, count=128):
    """`count` datagrams of `size` bytes each way, both directions installed:
    the hardware counts every one on its entry, the CPU forwards none of them,
    the microcode fragments none of them, and each crosses the WAN segment as
    one unfragmented datagram carrying exactly what the LAN client sent -- and
    comes back to it the same."""
    before = await r.state()
    healthy(before)
    assert before["entries"] == 2, before
    tx_before, fragments_before = await software_tx(r), await fragmenter(r)
    first = r.sequence
    r.sequence += count
    async with wan_capture(r, label) as frames:
        report = await datagrams(r, first=first, count=count, size=size, mode=PMTUDISC_PROBE,
                                 echo=True, label=label.replace("-", "_"))
    after, tx_after, fragments_after = await r.state(), await software_tx(r), await fragmenter(r)
    listing = await ct_listing(r)
    tx, moved = delta(tx_before, tx_after), delta(fragments_before, fragments_after)
    r.record(label, {"before": before, "after": after, "software_tx": tx, "fragmenter": moved,
                     "report": report, "frames": len(frames), "conntrack": listing})
    assert report == {"sent": count, "received": count}, report
    missing = [n for n in range(first, first + count) if r.echo.received[payload(n, size)] != 1]
    assert not missing, ("WAN endpoint did not receive each datagram exactly once", missing[:16])
    wire_datagrams(frames, size, first, count)
    healthy(after)
    assert (after["installs"], after["deletes"]) == (before["installs"], before["deletes"]), (before, after)
    old = {f["cookie"]: f for f in before["flows"]}
    new = {f["cookie"]: f for f in after["flows"]}
    assert old.keys() == new.keys(), (before, after)
    for cookie, flow in new.items():
        # The raw counters include the Ethernet header and an ingress tag.
        framing = 14 + 20 + 8 + (4 if flow.get("in_vlan", "-") != "-" else 0)
        assert int(flow["mtu"]) == JUMBO, flow
        assert int(flow["packets"]) - int(old[cookie]["packets"]) == count, (old[cookie], flow)
        assert int(flow["bytes"]) - int(old[cookie]["bytes"]) == count * (size + framing), (old[cookie], flow)
    assert 0 <= tx[TARGET_LAN_IF] <= 64 and 0 <= tx[TARGET_WAN_IF] <= 128, tx
    assert moved == {name: 0 for name in moved}, moved
    assert "[HW_OFFLOAD]" in listing, listing
    return after


async def oversize_dropped(r, label, *, size, count=32):
    """Non-DF datagrams of `size` bytes into the DUT's LAN port at a standard
    MTU, on its installed UDP tuple. Its MAC must drop each one: none reaches
    the WAN endpoint whole, fragmented or zero-filled, the microcode fragments
    nothing, and the entries are untouched.

    Which mEMAC counter an over-MAXFRM frame lands in is not verified on this
    hardware: the reference manual has it truncated and flagged, which should
    count it as oversized (rovr) and as an error (rerr). Either reaching the
    count passes; the driver's counters are recorded beside them."""
    before, fragments_before = await r.state(), await fragmenter(r)
    mac_before, errors_before = await mac_rx(r, TARGET_LAN_IF), await rx_errors(r, TARGET_LAN_IF)
    tx_before = await software_tx(r)
    delivered = r.echo.packets
    first = r.sequence
    r.sequence += count
    r.echo.reply = False
    try:
        async with wan_capture(r, label) as frames:
            report = await datagrams(r, first=first, count=count, size=size, mode=PMTUDISC_INTERFACE,
                                     echo=False, interval=0.01, label=label.replace("-", "_"))
            await asyncio.sleep(0.5)
    finally:
        r.echo.reply = True
    after, fragments_after = await r.state(), await fragmenter(r)
    mac = delta(mac_before, await mac_rx(r, TARGET_LAN_IF))
    errors = delta(errors_before, await rx_errors(r, TARGET_LAN_IF))
    tx = delta(tx_before, await software_tx(r))
    moved = delta(fragments_before, fragments_after)
    r.record(label, {"before": before, "after": after, "mac_rx": mac, "rx_errors": errors,
                     "software_tx": tx, "fragmenter": moved, "report": report,
                     "frames": [frame.summary() for frame in frames[:64]],
                     "delivered": r.echo.packets - delivered})
    assert report == {"sent": count, "received": 0}, report
    assert not frames, ("oversized datagrams crossed the DUT", len(frames))
    assert r.echo.packets == delivered, ("WAN endpoint received", r.echo.packets - delivered)
    assert moved == {name: 0 for name in moved}, moved
    assert max(mac["rovr"], mac["rerr"]) >= count, mac
    assert (after["installs"], after["deletes"]) == (before["installs"], before["deletes"]), (before, after)
    old = {f["cookie"]: f for f in before["flows"]}
    new = {f["cookie"]: f for f in after["flows"]}
    assert old.keys() == new.keys(), (before, after)
    # Dropped by the MAC, so no frame reached the classifier and no entry
    # counted it. That a MAC-flagged frame is not classified is expected of
    # the BMI but not verified on this hardware; the wire assertions above are
    # the regression's own.
    assert {c: int(new[c]["packets"]) - int(old[c]["packets"]) for c in old} == {c: 0 for c in old}, \
        (before, after)
    assert 0 <= tx[TARGET_WAN_IF] <= 128, tx
    return after


async def linux_fragments(r, label, *, size, path_mtu, count=32):
    """Non-DF datagrams of `size` bytes from the jumbo LAN into a path of
    `path_mtu`, whose direction Linux keeps: each must leave the WAN port as
    contiguous fragments within the path that between them carry the
    datagram's own bytes, the endpoint must reassemble each exactly once, and
    the microcode must fragment nothing."""
    from scapy.all import IP, Ether
    before, fragments_before = await r.state(), await fragmenter(r)
    first = r.sequence
    r.sequence += count
    r.echo.reply = False
    try:
        tx_before = await software_tx(r)
        async with wan_capture(r, label) as frames:
            report = await datagrams(r, first=first, count=count, size=size, mode=PMTUDISC_INTERFACE,
                                     echo=False, interval=0.01, label=label.replace("-", "_"))
            deadline = time.monotonic() + 5
            while (sum(r.echo.received[payload(n, size)] for n in range(first, first + count)) < count
                   and time.monotonic() < deadline):
                await asyncio.sleep(0.1)
        tx_after = await software_tx(r)
    finally:
        r.echo.reply = True
    after, fragments_after = await r.state(), await fragmenter(r)
    moved = delta(fragments_before, fragments_after)
    tx = delta(tx_before, tx_after)
    r.record(label, {"before": before, "after": after, "fragmenter": moved, "software_tx": tx,
                     "report": report, "frames": len(frames)})
    assert report == {"sent": count, "received": 0}, report
    assert [r.echo.received[payload(n, size)] for n in range(first, first + count)] == [1] * count
    assert moved == {name: 0 for name in moved}, moved
    per_datagram = -(-(size + 8) // ((path_mtu - 20) // 8 * 8))
    assert tx[TARGET_WAN_IF] >= per_datagram * count, (per_datagram, tx)
    groups = {}
    for frame in frames:
        groups.setdefault(frame[IP].id, []).append(frame)
    assert len(groups) == count, sorted(groups)
    serials = set()
    for ident, fragments in groups.items():
        fragments.sort(key=lambda f: f[IP].frag)
        assert len(fragments) == per_datagram, (ident, [f.summary() for f in fragments])
        offset = 0
        for index, fragment in enumerate(fragments):
            ip = fragment[IP]
            more = index < len(fragments) - 1
            assert (ip.frag * 8, int(ip.flags) & 1, int(ip.flags) & 2) == (offset, int(more), 0), \
                fragment.summary()
            assert ip.len <= path_mtu and ip.ttl == 63, fragment.summary()
            assert (fragment[Ether].src, fragment[Ether].dst) == (r.dut_wan_mac, r.wan_mac), fragment.summary()
            offset += ip.len - 4 * ip.ihl
        assert offset == size + 8, (ident, offset)
        datagram = b"".join(bytes(f[IP])[4 * f[IP].ihl:f[IP].len] for f in fragments)
        assert struct.unpack("!HHH", datagram[:6]) == (SPORT, DPORT, 8 + size), (ident, datagram[:8].hex())
        serial = struct.unpack("!Q", datagram[8:16])[0]
        assert first <= serial < first + count and datagram[8:] == payload(serial, size), (ident, serial)
        serials.add(serial)
    assert serials == set(range(first, first + count)), sorted(set(range(first, first + count)) - serials)
    return after
