"""Jumbo frames through the offloaded path, and a standard port refusing them.

Each DPAA port's MAC accepts frames up to its own MTU -- never less than a
standard frame, and four bytes more when the port carries an upper device --
so a port's MTU bounds what its ingress can deliver. These cases raise every
hop they need to JUMBO for their own duration and put it back on the same
boot, whatever happens (_flowtable_jumbo.jumbo_path):

- routed NAT with every hop at JUMBO carries TCP at line rate in hardware and
  full-size jumbo datagrams byte for byte in both directions;
- a jumbo LAN host sending into a standard-MTU LAN port is dropped by that
  port's MAC rather than fragmented by the microcode with zeroed payloads
  (A316, the A202 class at the port);
- a jumbo LAN behind a standard WAN keeps TCP in hardware through MSS and
  path MTU, leaves oversized non-TCP to Linux -- which fragments IPv4 without
  DF correctly and answers DF and IPv6 with their errors -- and has the
  microcode fragment nothing;
- a tagged jumbo LAN carries jumbo datagrams in hardware under NAT;
- an installed flow follows its port's MTU down and back up at runtime.
"""
from __future__ import annotations

import asyncio
import json
import os
import socket
import time

import pytest

from _flowtable_connections import by_key, healthy
from _flowtable_ipv6 import PayloadEcho, _drive, _drop_tables, _offload_table, _udp_exchange
from _flowtable_jumbo import (
    JUMBO,
    JUMBO_UDP,
    JUMBO_UDP6,
    STANDARD,
    TCP_PORT,
    delta,
    dut_mtu,
    dut_up,
    jumbo_burst,
    jumbo_path,
    lan_mtu,
    lan_route,
    linux_fragments,
    links_up,
    masquerade,
    offload_table,
    oversize_dropped,
    wan_lan_route,
    wan_route,
)
from _flowtable_mtu import fragmenter, udp_size, udp_warm
from _flowtable_rig import DPORT, SPORT, WAN_IP, command, read
from _flowtable_tcp import connection, cpu, cpu_delta, hardware_transfer, installed, software_tx, tcp_table
from _topology import (
    DUT_IPV6_LAN,
    LAN_IPV6,
    LAN_NIC,
    TARGET_LAN_IF,
    TARGET_WAN_IF,
    VLAN_ID_JUMBO,
    WAN_IPV6,
    dut_vlan_subif,
    lan_run,
    lan_run_python,
    lan_vlan_subif,
)
from ask_orch.lifecycle import CleanupStack

STREAMS = 4
IPERF_SECONDS, IPERF_OMIT, WINDOW_SECONDS = 5, 2, 3
# Unidirectional TCP through jumbo NAT. A jumbo frame costs the classifier no
# more than a standard one and carries six times the payload, so the port, not
# the hardware path, is the limit.
MIN_RATE = float(os.environ.get("ASK_FLOWTABLE_JUMBO_MIN_GBPS", "8")) * 1e9

# Apart from every port pair _flowtable_ipv6.PORTS reserves.
IPV6_PORTS = (49040, 49041)

# Deliberately not derived from the VID; see _flowtable_vlan.
VLAN_SUBNET_DUT, VLAN_SUBNET_LAN = "172.29.94.1", "172.29.94.2"


def mtus_of(lan, wan):
    return {TARGET_LAN_IF: lan, TARGET_WAN_IF: wan}


def _lan_mtu_once(lan, mtu):
    """Set the LAN client's NIC MTU and wait for its link to come back."""
    lan.run(f"ip link set dev {LAN_NIC} mtu {mtu}")
    probe = f"cat /sys/class/net/{LAN_NIC}/operstate"
    for _ in range(80):
        if lan.run(probe).stdout.strip() == "up":
            return
        time.sleep(0.25)
    raise AssertionError(f"{LAN_NIC} stayed down after its MTU went to {mtu}")


@pytest.fixture(scope="module", autouse=True)
def lan_jumbo(lan):
    """Every case here runs the LAN client at JUMBO, so its NIC goes there once
    for the file and back once after it: an ixgbe MTU change resets the port
    and re-trains the link, and the bench's LAN cable has failed to come back
    from one. The cases' own lan_mtu() calls then find it already set."""
    old = int(lan.run(f"cat /sys/class/net/{LAN_NIC}/mtu").stdout.strip())
    if old != JUMBO:
        _lan_mtu_once(lan, JUMBO)
    try:
        yield
    finally:
        if old != JUMBO:
            _lan_mtu_once(lan, old)


@pytest.mark.parametrize("reverse", [False, True], ids=["upload", "download"])
async def test_nat_tcp(rig, reverse):
    """TCP through NAT with every hop at JUMBO: each stream's data direction
    on a hardware entry at the jumbo MTU, carrying jumbo frames, its cookie
    stable through the measured window, the CPU forwarding next to nothing,
    and the rate at least MIN_RATE."""
    r = rig
    stack = CleanupStack()
    server = lan_task = None
    try:
        await jumbo_path(stack, r, nat=True)
        await lan_route(stack, r, JUMBO)
        await offload_table(stack, r)
        server = await asyncio.create_subprocess_exec(
            "iperf3", "-s", "-1", "-B", WAN_IP, "-p", str(TCP_PORT), "-J",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        await asyncio.sleep(0.2)
        assert server.returncode is None, "dedicated iperf server failed to start"
        script = f'''
import json, subprocess
link = subprocess.check_output(['ethtool', {LAN_NIC!r}], text=True)
assert 'Speed: 10000Mb/s' in link and 'Link detected: yes' in link, link
argv = ['iperf3', '-c', {WAN_IP!r}, '-B', {r.lan_ip!r}, '-p', {str(TCP_PORT)!r},
        '-P', {str(STREAMS)!r}, '-t', {str(IPERF_SECONDS)!r}, '-O', {str(IPERF_OMIT)!r}, '-J', '-Z']
argv += {['-R'] if reverse else []!r}
result = subprocess.run(argv, capture_output=True, text=True, timeout=40)
print(json.dumps({{'argv': argv, 'rc': result.returncode, 'stdout': result.stdout, 'stderr': result.stderr}}))
'''
        lan_task = asyncio.create_task(lan_run_python(r.lan, script, label="flowtable_jumbo_rate", timeout=55))
        ingress = TARGET_WAN_IF if reverse else TARGET_LAN_IF
        loop = asyncio.get_running_loop()
        deadline = loop.time() + 8
        while True:
            before = await r.state()
            bulk = [f for f in before["flows"] if f["proto"] == "6"
                    and f"{WAN_IP}:{TCP_PORT}" in (f["src"], f["dst"]) and f["in"] == ingress
                    and int(f["bytes"]) > max(1_000_000, 512 * int(f["packets"]))]
            if len(bulk) == STREAMS:
                break
            assert not lan_task.done(), "iperf ended before hardware admission"
            assert loop.time() < deadline, before
            await asyncio.sleep(0.25)
        healthy(before)
        old = by_key(before)
        keys = set()
        for row in bulk:
            assert int(row["mtu"]) == JUMBO, row
            if reverse:
                assert row["dst"].rsplit(":", 1)[0] == r.external, row
                assert row["new_dst"].rsplit(":", 1)[0] == r.lan_ip, row
            else:
                assert row["src"].rsplit(":", 1)[0] == r.lan_ip, row
                assert row["new_src"].rsplit(":", 1)[0] == r.external, row
            opposite = TARGET_LAN_IF if reverse else TARGET_WAN_IF
            reply = (opposite, "6", row["new_dst"], row["new_src"])
            assert reply in old and int(old[reply]["mtu"]) == JUMBO, (reply, before)
            keys.update({(row["in"], "6", row["src"], row["dst"]), reply})
        data = {(row["in"], "6", row["src"], row["dst"]) for row in bulk}
        tx_before, cpu_before = await software_tx(r), await cpu(r)
        await asyncio.sleep(WINDOW_SECONDS)
        cpu_after, tx_after = await cpu(r), await software_tx(r)
        after = await r.state()
        healthy(after)
        new = by_key(after)
        deltas = []
        for key in keys:
            assert new[key]["cookie"] == old[key]["cookie"], (key, before, after)
            packets = int(new[key]["packets"]) - int(old[key]["packets"])
            size = int(new[key]["bytes"]) - int(old[key]["bytes"])
            assert packets > 1000, (key, packets)
            deltas.append({"key": key, "packets": packets, "bytes": size})
            if key in data:
                # Raw counters include the Ethernet header: a full segment is
                # JUMBO + 14 bytes, and the window has nothing else in it.
                assert size / packets > JUMBO - 1000, ("data direction is not carrying jumbo frames",
                                                       key, packets, size)
        tx = delta(tx_before, tx_after)
        r.record("jumbo-tcp-hardware", {"before": before, "after": after, "hardware": deltas,
                                        "software_tx": tx, "cpu": cpu_delta(cpu_before, cpu_after)})
        assert all(0 <= count <= 512 for count in tx.values()), tx
        result = await lan_task
        lan_task = None
        assert result.rc == 0, result.stdout
        client = json.loads(result.stdout.strip().splitlines()[-1])
        assert client["rc"] == 0, client
        client_json = json.loads(client["stdout"])
        stdout, stderr = await asyncio.wait_for(server.communicate(), 10)
        server_json = json.loads(stdout)
        r.record("jumbo-tcp-iperf", {"client": client_json, "server": server_json, "stderr": stderr.decode()})
        assert server.returncode == 0 and "error" not in client_json and "error" not in server_json
        received = (client_json if reverse else server_json)["end"]["sum_received"]
        assert received["seconds"] >= IPERF_SECONDS - 0.1, received
        assert received["bits_per_second"] >= MIN_RATE, received
    finally:
        try:
            if lan_task:
                result = await lan_task
                r.record("jumbo-tcp-client", {"rc": result.rc, "stdout": result.stdout})
        finally:
            try:
                if server and server.returncode is None:
                    server.terminate()
                    await asyncio.wait_for(server.communicate(), 5)
            finally:
                await stack.teardown("jumbo TCP")


async def test_nat_udp(rig):
    """Jumbo datagrams, each as large as an unfragmented JUMBO packet carries,
    through NAT with every hop at JUMBO: both directions in hardware, each
    datagram crossing unfragmented and byte for byte both ways."""
    r = rig
    stack = CleanupStack()
    try:
        await jumbo_path(stack, r, nat=True)
        await offload_table(stack, r)
        admitted = await udp_warm(r, SPORT, mtus_of(JUMBO, JUMBO))
        rows = {f["in"]: f for f in admitted["flows"]}
        assert rows[TARGET_LAN_IF]["new_src"].rsplit(":", 1)[0] == r.external, admitted
        assert rows[TARGET_WAN_IF]["dst"].rsplit(":", 1)[0] == r.external, admitted
        await jumbo_burst(r, "jumbo-nat-udp")
    finally:
        await stack.teardown("jumbo UDP")


async def test_oversize_into_standard_port(rig):
    """A316: a jumbo LAN host on a standard-MTU LAN port.

    The UDP tuple is installed both ways at the standard MTU and carries
    1400-byte datagrams in hardware. Then the LAN host, at JUMBO, sends
    8000-byte datagrams with DF clear on that tuple. While every port received
    up to the build's maximum frame whatever its MTU, the classifier matched
    them to the 1500-byte entry and the microcode fragmented them with zeroed
    payloads. The port's MAC has to drop them instead: the WAN endpoint sees
    nothing at all from the LAN host, the fragmenter counts nothing, the MAC
    counts each one, and the flow carries 1400-byte datagrams in hardware
    afterwards exactly as before."""
    r = rig
    stack = CleanupStack()
    mtus = {dev: int((await read(r.target, r.session, f"/sys/class/net/{dev}/mtu")).strip())
            for dev in (TARGET_LAN_IF, TARGET_WAN_IF)}
    assert mtus == mtus_of(STANDARD, STANDARD), mtus
    try:
        await lan_mtu(stack, r, JUMBO)
        await links_up(r)
        await r.table()
        await udp_warm(r, SPORT, mtus)
        await udp_size(r, SPORT, 1400, "jumbo-standard-port-before", mtus)
        await oversize_dropped(r, "jumbo-standard-port-oversize", size=8000)
        await udp_size(r, SPORT, 1400, "jumbo-standard-port-after", mtus)
    finally:
        await stack.teardown("jumbo LAN host on a standard port")


@pytest.mark.parametrize("rig", ["tcp"], indirect=True)
async def test_mismatch_tcp(rig):
    """TCP from a jumbo LAN through a standard WAN stays in hardware.

    The DUT's LAN port and the LAN host are at JUMBO, the WAN port and the WAN
    host at the standard MTU. The WAN host is made to advertise a jumbo MSS, so
    the LAN host's segments start at its own MTU and only the DUT's
    Fragmentation Needed -- from Linux, or excepted to it by the entry's DF
    check -- brings them down to the WAN path. Both directions are installed,
    each at the MTU of the port it leaves by; uploads and downloads cross in
    hardware intact; and the LAN host's route to the endpoint has learned the
    WAN path's MTU."""
    r = rig
    stack = CleanupStack()
    try:
        await lan_mtu(stack, r, JUMBO)
        await dut_mtu(stack, r, TARGET_LAN_IF, JUMBO)
        await links_up(r)
        await lan_route(stack, r)
        await wan_lan_route(stack, r, advmss=JUMBO - 40)
        await tcp_table(r)
        async with connection(r) as conn:
            admitted = await installed(r, conn)
            rows = {f["in"]: f for f in admitted["flows"]}
            assert {d: int(f["mtu"]) for d, f in rows.items()} == {TARGET_LAN_IF: STANDARD,
                                                                  TARGET_WAN_IF: JUMBO}, admitted
            await hardware_transfer(r, conn, "upload", label="jumbo-mismatch-tcp-upload")
            await hardware_transfer(r, conn, "download", label="jumbo-mismatch-tcp-download")
            learned = await lan_run(r.lan, f"ip route get {WAN_IP} from {r.lan_ip}")
            r.record("jumbo-mismatch-tcp-pmtu", {"route": learned.stdout})
            assert learned.rc == 0 and f"mtu {STANDARD}" in learned.stdout, learned.stdout
            await conn.close("fin")
    finally:
        await stack.teardown("jumbo LAN behind a standard WAN, TCP")


async def test_mismatch_udp(rig):
    """Non-TCP IPv4 from a jumbo LAN through a standard WAN stays in Linux.

    The upload's ingress can deliver JUMBO and its path carries STANDARD, so
    the adapter refuses it; the download, arriving at a standard port and
    leaving by a jumbo one, is installed at JUMBO. Full-size jumbo datagrams
    with DF clear are fragmented by Linux into whole fragments the endpoint
    reassembles byte for byte, and the microcode fragments nothing; one with
    DF gets Linux's Fragmentation Needed for the WAN path; and standard
    datagrams still cross, the download in hardware."""
    r = rig
    stack = CleanupStack()
    mtus = mtus_of(JUMBO, STANDARD)
    try:
        await lan_mtu(stack, r, JUMBO)
        await dut_mtu(stack, r, TARGET_LAN_IF, JUMBO)
        await links_up(r)
        initial = await r.state()
        await r.table()
        admitted = await udp_warm(r, SPORT, mtus)
        assert admitted["rejects"] > initial["rejects"], (initial, admitted)
        download, = admitted["flows"]
        assert (download["in"], download["out"], int(download["mtu"])) == (
            TARGET_WAN_IF, TARGET_LAN_IF, JUMBO), admitted
        await linux_fragments(r, "jumbo-mismatch-fragments", size=JUMBO_UDP, path_mtu=STANDARD)

        before = await fragmenter(r)
        probe = b"ASK-jumbo-df".ljust(JUMBO_UDP, b".")
        script = f'''
import json
from scapy.all import Ether, IP, UDP, ICMP, Raw, srp1
packet = Ether(dst={r.dut_lan_mac!r})/IP(src={r.lan_ip!r}, dst={WAN_IP!r}, flags='DF')/UDP(sport={SPORT}, dport={DPORT})/Raw({probe!r})
answer = srp1(packet, iface={LAN_NIC!r}, timeout=3, verbose=False)
assert answer is not None and ICMP in answer, answer
assert (answer[ICMP].type, answer[ICMP].code, answer[ICMP].nexthopmtu) == (3, 4, {STANDARD}), answer.show(dump=True)
print(json.dumps({{'type': answer[ICMP].type, 'code': answer[ICMP].code, 'mtu': answer[ICMP].nexthopmtu}}))
'''
        result = await lan_run_python(r.lan, script, timeout=15, label="flowtable_jumbo_df")
        assert result.rc == 0, result.stdout
        await asyncio.sleep(0.5)
        assert not r.echo.received[probe], "oversized DF datagram crossed the standard WAN"
        moved = delta(before, await fragmenter(r))
        r.record("jumbo-mismatch-df", {"answer": result.stdout.strip().splitlines()[-1], "fragmenter": moved})
        assert moved == {name: 0 for name in moved}, moved

        await udp_size(r, SPORT, 1472, "jumbo-mismatch-standard", mtus)
    finally:
        await stack.teardown("jumbo LAN behind a standard WAN, UDP")


async def test_mismatch_ipv6(ipv6_rig):
    """IPv6 from a jumbo LAN through a standard WAN is answered with Packet
    Too Big.

    The LAN port's IPv6 MTU follows its device to JUMBO, so the upload's
    ingress can deliver more than its path carries and the direction stays in
    Linux; the download is installed at JUMBO. A full-size jumbo datagram from
    the LAN host gets Packet Too Big for the WAN path, never reaches the
    endpoint, and the microcode fragments nothing (A198 at a jumbo ingress).
    Standard datagrams still cross."""
    r = ipv6_rig
    stack = CleanupStack()
    sport, dport = IPV6_PORTS
    echo = PayloadEcho()
    transport, _ = await asyncio.get_running_loop().create_datagram_endpoint(
        lambda: echo, local_addr=(WAN_IPV6, dport), family=socket.AF_INET6)
    stack.push(lambda: _drop_tables(r))

    def only_download(s):
        return (s["entries"] == 1 and s["flows"][0]["in"] == TARGET_WAN_IF
                and int(s["flows"][0]["mtu"]) == JUMBO)

    async def send(count=8):
        return await _udp_exchange(r, sport, WAN_IPV6, dport, count, (WAN_IPV6, dport),
                                   "flowtable_jumbo_v6")
    try:
        await lan_mtu(stack, r, JUMBO)
        await dut_mtu(stack, r, TARGET_LAN_IF, JUMBO)
        await links_up(r)
        ipv6_mtus = {dev: (await command(r.target, r.session, "sysctl", "-n",
                                         f"net.ipv6.conf.{dev}.mtu"))["stdout"].strip()
                     for dev in (TARGET_LAN_IF, TARGET_WAN_IF)}
        assert ipv6_mtus == {TARGET_LAN_IF: str(JUMBO), TARGET_WAN_IF: str(STANDARD)}, ipv6_mtus
        # The carrier change can leave the LAN host's neighbour for the DUT
        # unresolved; resolve it before the first counted exchange.
        await lan_run(r.lan, f"ping -6 -c 1 -W 2 {DUT_IPV6_LAN} > /dev/null 2>&1 || true", 10.0)
        initial = await r.state()
        await _offload_table(r, f"ip6 saddr {LAN_IPV6} udp sport {sport} udp dport {dport}")
        admitted = await _drive(r, send, only_download, "only the download should be in hardware")
        assert admitted["rejects"] > initial["rejects"], (initial, admitted)
        r.record("jumbo-ipv6-admitted", admitted)

        before = await fragmenter(r)
        probe = b"J" * JUMBO_UDP6
        script = f'''
import json
from scapy.all import Ether, IPv6, UDP, Raw, ICMPv6PacketTooBig, srp1
packet = IPv6(src={LAN_IPV6!r}, dst={WAN_IPV6!r})/UDP(sport={sport}, dport={dport})/Raw(b'J' * {JUMBO_UDP6})
answer = srp1(Ether(dst={r.dut_lan_mac!r})/packet, iface={LAN_NIC!r}, timeout=3, verbose=False)
assert answer is not None and ICMPv6PacketTooBig in answer, answer
assert answer[ICMPv6PacketTooBig].mtu == {STANDARD}, answer.show(dump=True)
print(json.dumps(answer.summary()))
'''
        result = await lan_run_python(r.lan, script, timeout=20, label="flowtable_jumbo_v6_ptb")
        assert result.rc == 0, result.stdout
        await asyncio.sleep(0.5)
        assert not echo.received[probe], "oversized IPv6 datagram crossed the standard WAN"
        moved = delta(before, await fragmenter(r))
        assert moved == {name: 0 for name in moved}, moved

        report = await send(16)
        assert report == {"echoed": 16, "lost": 0}, report
        final = await r.state()
        assert only_download(final), final
        assert final["errors"] == r.errors, final
        r.record("jumbo-ipv6", {"too_big": result.stdout.strip().splitlines()[-1],
                                "fragmenter": moved, "final": final})
    finally:
        transport.close()
        await stack.teardown("jumbo IPv6 behind a standard WAN")


async def test_vlan_nat_udp(rig):
    """A tagged jumbo LAN under NAT: the VLAN devices on both ends at JUMBO
    over jumbo ports, and full-size jumbo datagrams crossing byte for byte in
    hardware both ways, the tag counted on the tagged ingress."""
    r = rig
    stack = CleanupStack()
    try:
        await jumbo_path(stack, r, nat=False)
        dut_if = await dut_vlan_subif(stack, r.target, r.session, parent=TARGET_LAN_IF,
                                      vid=VLAN_ID_JUMBO, ipv4=f"{VLAN_SUBNET_DUT}/24")
        lan_if = await lan_vlan_subif(stack, r.lan, parent=LAN_NIC, vid=VLAN_ID_JUMBO,
                                      ipv4=f"{VLAN_SUBNET_LAN}/24",
                                      routes=[f"{WAN_IP}/32 via {VLAN_SUBNET_DUT} dev vlan{VLAN_ID_JUMBO}"])
        # Created over jumbo parents, both inherit their MTU.
        assert int((await read(r.target, r.session, f"/sys/class/net/{dut_if}/mtu")).strip()) == JUMBO
        assert int((await lan_run(r.lan, f"cat /sys/class/net/{lan_if}/mtu")).stdout.strip()) == JUMBO
        link = json.loads((await lan_run(r.lan, f"ip -j link show dev {lan_if}")).stdout)[0]
        r.lan_ip = VLAN_SUBNET_LAN
        r.peer_if, r.peer_link = lan_if, LAN_NIC
        r.peer_mac = r.lan_mac = link["address"]
        r.peer_gateway_mac = r.dut_lan_mac = (
            await read(r.target, r.session, f"/sys/class/net/{dut_if}/address")).strip()
        await command(r.target, r.session, "ip", "neigh", "replace", r.lan_ip, "lladdr", r.lan_mac,
                      "nud", "permanent", "dev", dut_if)
        stack.push(lambda: command(r.target, r.session, "ip", "neigh", "del", r.lan_ip, "dev", dut_if,
                                   check=False))
        # NAT on the tagged address: the WAN host routes nothing back to it,
        # and its replies to the DUT's own address take the jumbo /32.
        await wan_route(stack, r, r.external, mtu=JUMBO)
        await masquerade(stack, r)
        r.wan_source = r.external
        await offload_table(stack, r)
        admitted = await udp_warm(r, SPORT, mtus_of(JUMBO, JUMBO))
        rows = {f["in"]: f for f in admitted["flows"]}
        assert rows[TARGET_LAN_IF]["in_vlan"] == str(VLAN_ID_JUMBO), admitted
        assert rows[TARGET_WAN_IF]["out_vlan"] == str(VLAN_ID_JUMBO), admitted
        await jumbo_burst(r, "jumbo-vlan-nat-udp")
    finally:
        await stack.teardown("jumbo VLAN")


async def test_live_mtu_change(rig):
    """An installed flow follows its LAN port's MTU down from JUMBO to
    STANDARD and back, on the same table and tuple.

    Every hop starts at JUMBO and the routed UDP tuple carries full-size jumbo
    datagrams in hardware. Lowering the LAN port retires the flow; it comes
    back with the upload alone, still at the WAN port's JUMBO, and the
    download -- whose ingress can deliver JUMBO into a now standard path --
    left to Linux. At that MTU the port's MAC drops the LAN host's jumbo
    datagrams and standard ones cross. Raising it again retires the flow once
    more, and it returns in both directions carrying jumbo datagrams in
    hardware: the MAC's limit followed the MTU back up."""
    r = rig
    stack = CleanupStack()
    mtus = mtus_of(JUMBO, JUMBO)
    # Installs less deletes is the entries the adapter holds; whatever earlier
    # tests left in that difference is this test's baseline.
    initial = await r.state()
    offset = initial["installs"] - initial["deletes"] - initial["entries"]

    async def change(mtu):
        before = await r.state()
        await command(r.target, r.session, "ip", "link", "set", "dev", TARGET_LAN_IF, "mtu", str(mtu))
        mtus[TARGET_LAN_IF] = mtu
        await dut_up(r, TARGET_LAN_IF)
        retired = await r.wait(lambda s: s["deletes"] >= before["deletes"] + before["entries"], timeout=15)
        # The driver rewrites the MAC's max frame length in place and keeps
        # the link, so the MTU event alone retires the flow: one generation,
        # one handle, however many of its directions were installed.
        assert retired["mtu_invalidations"] == before["mtu_invalidations"] + 1, (before, retired)
        assert retired["link_invalidations"] == before["link_invalidations"], (before, retired)
        await links_up(r)
        readmitted = await udp_warm(r, SPORT, mtus)
        assert readmitted["installs"] - readmitted["deletes"] - readmitted["entries"] == offset, \
            (initial, readmitted)
        assert readmitted["installs"] > retired["installs"], (retired, readmitted)
        r.record(f"jumbo-live-{mtu}", {"before": before, "retired": retired, "readmitted": readmitted})
        return readmitted

    try:
        await jumbo_path(stack, r, nat=False)
        await r.table()
        await udp_warm(r, SPORT, mtus)
        await jumbo_burst(r, "jumbo-live-initial", count=64)

        lowered = await change(STANDARD)
        upload, = lowered["flows"]
        assert (upload["in"], int(upload["mtu"])) == (TARGET_LAN_IF, JUMBO), lowered
        await oversize_dropped(r, "jumbo-live-oversize", size=JUMBO_UDP, count=16)
        await udp_size(r, SPORT, 1400, "jumbo-live-standard", mtus)

        raised = await change(JUMBO)
        assert sorted(f["in"] for f in raised["flows"]) == sorted((TARGET_LAN_IF, TARGET_WAN_IF)), raised
        await jumbo_burst(r, "jumbo-live-raised", count=64)
    finally:
        await stack.teardown("jumbo live MTU change")
