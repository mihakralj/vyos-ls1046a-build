"""Shared flowtable service ipsec fixtures and scenarios."""

from __future__ import annotations

import asyncio
import base64
import json
import os
import re
import secrets
import socket
import struct
import time
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path

import pytest_asyncio
from _flowtable_connections import by_key
from _flowtable_rig import (
    DPORT,
    WAN_IP,
    Echo,
    artifact_dir,
    command,
    console_command,
    read,
)
from _flowtable_selective_neighbour import keys, unchanged
from _flowtable_service import FIRST, managed_service
from _flowtable_service_vlan import balanced, denied
from _flowtable_tcp import software_tx
from _flowtable_tunnel import Capture
from _ipsec_inbound_flow_offload import AUTH, CIPHER, sec_counter
from _topology import (DUT_IPV6_WAN, LAN_NIC, TARGET_LAN_IF, TARGET_WAN_IF, WAN_IPV6, has_address,
                       lan_run_python)
from ask_orch.client import Agent
from ask_orch.uart import Console

INNER = "198.18.102.2"

LAN_INNER = "198.18.102.3"

REQIDS = {"out": "49301", "in": "49302"}

UDP_ENCAP, UDP_ENCAP_ESPINUDP = 100, 2


@dataclass(frozen=True)
class Transform:
    """The fixture's SA pair: the `ip xfrm state` algorithm arguments, UDP
    encapsulation as (DUT port, peer port) or None for bare ESP, and whether
    the tunnel's endpoints are IPv6 (the DUT's and the WAN host's WAN-segment
    addresses) rather than IPv4. The inner traffic is IPv4 either way.

    A test asks for another one by parametrizing `ipsec_service` indirectly."""
    algorithms: tuple = ("enc", "cbc(aes)", CIPHER, "auth-trunc", "hmac(sha256)", AUTH, "128")
    encap: tuple | None = None
    outer6: bool = False
    # A protocol the adapter declines (AH) runs as a software pair.
    proto: str = "esp"
    offload: bool = True


async def xfrm(r, agent, kind):
    # This image's iproute2 does not produce JSON for XFRM (even with -j).
    # Split its normal records at the unindented source/destination header.
    text = (await command(agent, r.session, "ip", "xfrm", kind, "list"))["stdout"]
    return [record.strip() for record in re.split(r"(?m)(?=^src )", text) if record.strip()]


def owned_state(state):
    return any(re.search(r"\breqid " + reqid + r"\b", state) for reqid in REQIDS.values())


def larval_state(state):
    """An acquire's placeholder the fixture's own traffic left: no SPI, one of
    the fixture's reqids, a selector between the fixture's inner addresses.
    xfrm_state_find() leaves one when a packet meets a required policy with no
    state and a key manager listens (an `ip xfrm monitor`, charon); it goes
    only when net.core.xfrm_acq_expires runs out or a state for it is added,
    and no DELSA can name it, having no SPI to be found by."""
    inner = f"({re.escape(LAN_INNER)}|{re.escape(INNER)})/32"
    return bool(owned_state(state) and re.search(r"\bspi 0x0+\b", state)
                and re.search(rf"\bsel src {inner} dst {inner}", state))


FIXTURE_ACQ_EXPIRES = 3


async def acq_expires(r, agent):
    """The host's net.core.xfrm_acq_expires, or the kernel's default of 30 s
    where the agent cannot read it."""
    try:
        result = await agent.fs_read(r.session, "/proc/sys/net/core/xfrm_acq_expires")
        value = bytes.fromhex(result["content_hex"]).decode().strip() if result["errno"] == 0 else ""
    except Exception:
        value = ""
    return int(value) if value.isdigit() else 30


class SecurityAssociations:
    def __init__(self, r, wan, outer, transform=Transform(), peer=WAN_IP):
        self.r, self.wan, self.outer, self.peer = r, wan, outer, peer
        self.transform = transform
        self.active, self.cleanup = {}, []

    def state(self, direction, spi):
        src, dst = (self.outer, self.peer) if direction == "out" else (self.peer, self.outer)
        return ["src", src, "dst", dst, "proto", self.transform.proto, "spi", hex(spi)]

    def crypto(self, direction):
        encap = []
        if self.transform.encap:
            # The source port is the sending end's: the DUT's going out.
            sport, dport = self.transform.encap if direction == "out" else self.transform.encap[::-1]
            encap = ["encap", "espinudp", str(sport), str(dport), "0.0.0.0"]
        # A state's selector otherwise takes the state's own family, the
        # outer one, and no IPv4 flow would ever find an IPv6-outer state.
        # strongSwan sets the same flag for a tunnel whose families differ.
        mixed = ["flag", "af-unspec"] if self.transform.outer6 else []
        return ["mode", "tunnel", "reqid", REQIDS[direction], *self.transform.algorithms, *encap, *mixed]

    async def add(self, agent, kind, identity, *options, check=True):
        result = await command(agent, self.r.session, "ip", "xfrm", kind, "add", *identity, *options, check=check)
        if result["rc"] == 0:
            self.cleanup.append((agent, ["ip", "xfrm", kind, "delete", *identity]))
        return result

    async def prepare_peer(self, direction, *options):
        spi = 0xA9000000 | secrets.randbits(24)
        await self.add(self.wan, "state", self.state(direction, spi), *self.crypto(direction),
                       "replay-window", "32", *options)
        return spi

    async def install(self, direction, spi, *options, check=True):
        # An inbound SA checks replays only with a window, and 0 turns the
        # hardware's check off as it does software's. 32 is what strongSwan
        # installs, so every case here runs with anti-replay on unless it
        # names its own window.
        if direction == "in" and "replay-window" not in options:
            options = (*options, "replay-window", "32")
        offload = ["offload", "packet", "dev", TARGET_WAN_IF, "dir", direction] if self.transform.offload else []
        result = await self.add(self.r.target, "state", self.state(direction, spi),
                               *self.crypto(direction), *options, *offload, check=check)
        if result["rc"] == 0:
            self.active[direction] = spi
        return result

    async def remove(self, direction):
        spi = self.active.pop(direction)
        await console_command(self.r.service_console, "ip", "xfrm", "state", "delete", *self.state(direction, spi))
        return spi

    async def states(self):
        return [s for s in await xfrm(self.r, self.r.target, "state") if owned_state(s)]

    async def policies(self):
        return [p for p in await xfrm(self.r, self.r.target, "policy") if INNER + "/32" in p.splitlines()[0]]


def wire_interface(r):
    """The WAN host's DUT-facing physical port, where ESP is captured as the
    wire carries it. XFRM reinjects decrypted packets on the host's L3
    interface, so capturing below a bridge keeps those copies from
    masquerading as plaintext on the wire."""
    members = Path('/sys/class/net', r.wan_if, 'brif')
    physical = [p.name for p in members.iterdir()
                if Path('/sys/class/net', p.name, 'device').exists()] if members.exists() else [r.wan_if]
    wire = os.environ.get('ASK_WAN_WIRE_IF')
    if not wire:
        assert len(physical) == 1, ('set ASK_WAN_WIRE_IF to the DUT-facing physical port', physical)
        wire = physical[0]
    return wire


@pytest_asyncio.fixture
async def ipsec_service(rig, request):
    r = rig
    transform = getattr(request, "param", Transform())
    wan = Agent("wan", f"http://{os.environ.get('ASK_WAN_IP', '127.0.0.1')}:9110")
    outer = next(a["local"] for i in json.loads((await command(r.target, r.session, "ip", "-j", "-4", "addr", "show", "dev", TARGET_WAN_IF))["stdout"])
                 for a in i["addr_info"] if a["family"] == "inet")
    # The tunnel's endpoints. The inner routes below stay IPv4 either way:
    # they only bring the inner packets to the policies that encapsulate them.
    assert not (transform.outer6 and transform.encap), "NAT-T is IPv4's"
    endpoint, peer_endpoint = (DUT_IPV6_WAN, WAN_IPV6) if transform.outer6 else (outer, WAN_IP)
    r.ipsec = sa = SecurityAssociations(r, wan, endpoint, transform, peer_endpoint)
    r.ipsec_wire_if = wire_interface(r)
    transport, lan_created, encap = None, False, None
    cleanup = []
    acq_saved = None
    for agent in (r.target, wan):
        assert not json.loads((await command(agent, r.session, "ip", "-j", "route", "show", "table", "all", "exact", INNER + "/32"))["stdout"])
        states = [s for s in await xfrm(r, agent, "state") if owned_state(s)]
        assert not states, "test-owned SA identifiers already exist"
        policies = [p for p in await xfrm(r, agent, "policy") if INNER + "/32" in p.splitlines()[0]]
        assert not policies, policies
    try:
        acq_saved = int((await read(r.target, r.session, "/proc/sys/net/core/xfrm_acq_expires")).strip())
        await command(r.target, r.session, "sysctl", "-w", f"net.core.xfrm_acq_expires={FIXTURE_ACQ_EXPIRES}")
        setup = f'''
import json, subprocess
addresses=json.loads(subprocess.check_output(['ip','-j','-4','addr'],text=True))
assert not any(a.get('local') in {({LAN_INNER, INNER})!r} for i in addresses for a in i['addr_info']), addresses
subprocess.run(['ip','addr','add',{LAN_INNER + '/32'!r},'dev','lo'],check=True)
'''
        result = await lan_run_python(r.lan, setup, label='ipsec_recovery_inner', timeout=15)
        assert result.rc == 0, result.stdout
        lan_created = True
        if transform.outer6:
            # The DUT's WAN-segment address is normally the image's own, and
            # stays; the WAN host's is the fixture's for the duration.
            if not await has_address(r.target, r.session, TARGET_WAN_IF, DUT_IPV6_WAN):
                await command(r.target, r.session, "ip", "-6", "addr", "add", f"{DUT_IPV6_WAN}/64",
                              "dev", TARGET_WAN_IF, "nodad")
                cleanup.append((r.target, ["ip", "-6", "addr", "del", f"{DUT_IPV6_WAN}/64",
                                           "dev", TARGET_WAN_IF]))
            await command(wan, r.session, "ip", "-6", "addr", "add", f"{WAN_IPV6}/64",
                          "dev", r.wan_if, "nodad")
            cleanup.append((wan, ["ip", "-6", "addr", "del", f"{WAN_IPV6}/64", "dev", r.wan_if]))
        for agent, args, undo in [
            (wan, ["ip", "addr", "add", INNER + "/32", "dev", "lo"], ["ip", "addr", "del", INNER + "/32", "dev", "lo"]),
            (r.target, ["ip", "route", "add", INNER + "/32", "via", WAN_IP, "dev", TARGET_WAN_IF, "mtu", "1400"],
             ["ip", "route", "del", INNER + "/32", "via", WAN_IP, "dev", TARGET_WAN_IF]),
            (r.target, ["ip", "route", "add", LAN_INNER + "/32", "via", r.lan_ip, "dev", TARGET_LAN_IF, "mtu", "1200"],
             ["ip", "route", "del", LAN_INNER + "/32", "via", r.lan_ip, "dev", TARGET_LAN_IF]),
            (wan, ["ip", "route", "add", LAN_INNER + "/32", "via", outer, "dev", r.wan_if],
             ["ip", "route", "del", LAN_INNER + "/32", "via", outer, "dev", r.wan_if]),
        ]:
            await command(agent, r.session, *args)
            cleanup.append((agent, undo))
        if transform.encap:
            # The peer's end of ESP-in-UDP: without an encapsulating socket on
            # its port, this host treats the DUT's frames as plain UDP.
            encap = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            encap.bind((WAN_IP, transform.encap[1]))
            encap.setsockopt(socket.IPPROTO_UDP, UDP_ENCAP, UDP_ENCAP_ESPINUDP)
        for direction in ("out", "in"):
            spi = await sa.prepare_peer(direction)
            await sa.install(direction, spi)
            src, dst = (LAN_INNER, INNER) if direction == "out" else (INNER, LAN_INNER)
            outer_src, outer_dst = ((endpoint, peer_endpoint) if direction == "out"
                                    else (peer_endpoint, endpoint))
            selector = ["src", src + "/32", "dst", dst + "/32"]
            template = ["tmpl", "src", outer_src, "dst", outer_dst, "proto", transform.proto, "mode", "tunnel",
                        "reqid", REQIDS[direction], "level", "required"]
            await sa.add(wan, "policy", [*selector, "dir", "in" if direction == "out" else "out"], *template)
            await sa.add(r.target, "policy", [*selector, "dir", direction], *template,
                         *(["offload", "packet", "dev", TARGET_WAN_IF] if transform.offload else []))
            if direction == "in":
                await sa.add(r.target, "policy", [*selector, "dev", TARGET_LAN_IF, "dir", "fwd"], *template)
        # The far inner end's echo, sharing the rig echo's record of what
        # arrived; a test that needs the outbound direction alone silences it.
        r.inner_echo = echo = Echo()
        echo.received = r.echo.received
        transport, _ = await asyncio.get_running_loop().create_datagram_endpoint(lambda: echo, local_addr=(INNER, DPORT))
        async with managed_service(r, extra_paths=[(LAN_INNER, INNER)]):
            yield r
    finally:
        if transport:
            transport.close()
        if encap:
            encap.close()
        failures = []
        # Policies before states, on both hosts: a packet meeting a required
        # policy with its state gone leaves an acquire's placeholder behind
        # (larval_state()), and with the policies out first nothing can.
        undo = list(reversed(sa.cleanup))
        steps = ([step for step in undo if step[1][2] == "policy"]
                 + [step for step in undo if step[1][2] != "policy"] + list(reversed(cleanup)))
        with Console.target(log_path=str(artifact_dir() / "service-ipsec-cleanup-uart.log")) as con:
            await asyncio.to_thread(con.login, "root", None)
            for agent, argv in steps:
                try:
                    # Withdrawn/rekeyed states already disappeared. Exact identities
                    # prevent teardown from touching somebody else's XFRM state.
                    check = "state" not in argv
                    if agent is r.target:
                        await console_command(con, *argv, check=check)
                    else:
                        await command(agent, r.session, *argv, check=check)
                except Exception as error:
                    failures.append(repr(error))
        for agent in (r.target, wan):
            # A placeholder a test's own traffic left earlier -- after a hard
            # expiry, say -- expires on its own and cannot be deleted; it is
            # waited out, for as long as xfrm keeps one there and no longer.
            # Any other test-owned state is a leftover at once.
            deadline = time.monotonic() + await acq_expires(r, agent) + 2
            while True:
                states = [s for s in await xfrm(r, agent, "state") if owned_state(s)]
                if not any(larval_state(s) for s in states) or time.monotonic() > deadline:
                    break
                await asyncio.sleep(0.5)
            policies = [p for p in await xfrm(r, agent, "policy") if INNER + "/32" in p.splitlines()[0]]
            if states or policies:
                failures.append({"agent": str(agent), "states": states, "policies": policies})
        if acq_saved is not None:
            restored = await command(r.target, r.session, "sysctl", "-w",
                                     f"net.core.xfrm_acq_expires={acq_saved}", check=False)
            if restored["rc"]:
                failures.append(restored)
        if lan_created:
            result = await lan_run_python(r.lan,
                f"import subprocess\nsubprocess.run(['ip','addr','del',{LAN_INNER + '/32'!r},'dev','lo'],check=True)\n",
                label='ipsec_recovery_inner_cleanup', timeout=15)
            if result.rc:
                failures.append(result.stdout)
        assert not failures, failures


def flows_for(r, protocol="tcp"):
    return [
        {"id": 0, "proto": "udp", "sport": FIRST, "lan": r.lan_ip},
        {"id": 1, "proto": "tcp", "sport": FIRST, "lan": r.lan_ip},
        {"id": 2, "proto": "udp", "sport": FIRST, "lan": LAN_INNER, "connect_ip": INNER},
        {"id": 3, "proto": "tcp", "sport": FIRST, "lan": LAN_INNER, "connect_ip": INNER, "tcp_timeout": 60},
        {"id": 4, "proto": protocol, "sport": FIRST + 1, "lan": LAN_INNER, "connect_ip": INNER},
        {"id": 5, "proto": "udp", "sport": FIRST + 2, "lan": r.lan_ip},
        {"id": 6, "proto": "udp", "sport": FIRST + 2, "lan": LAN_INNER, "connect_ip": INNER},
    ]


class Wire(Capture):
    def __init__(self, r, label):
        self.path = artifact_dir() / (label + ".pcap")
        self.interface = r.ipsec_wire_if
        esp = "ip6 proto 50" if r.ipsec.transform.outer6 else "ip proto 50"
        if r.ipsec.transform.encap:
            esp = f"(ip proto 50 or udp port {r.ipsec.transform.encap[0]})"
        self.filter = f"ether src {r.dut_wan_mac} and ({esp} or (src host {LAN_INNER} and dst host {INNER}))"

    def check(self, *, encrypted=False, spi=None):
        from scapy.all import ESP, IP, rdpcap
        packets = rdpcap(str(self.path))
        assert not any(IP in p and p[IP].dst == INNER for p in packets), "required IPsec policy leaked plaintext on the WAN"
        esp = [p for p in packets if ESP in p]
        if encrypted:
            assert len(esp) >= 256, (len(esp), self.stderr())
            assert all(p[ESP].spi == spi for p in esp), {p[ESP].spi for p in esp}
        return {"esp": len(esp), "plaintext": 0}


async def hardware(r, p, label, flows):
    before = await r.state()
    forwarded = await r.software_forwarded()
    enc = await sec_counter(r.session, r.target, TARGET_WAN_IF, "tx toenc")
    dec = await sec_counter(r.session, r.target, TARGET_WAN_IF, "tx todec")
    tx = await software_tx(r)
    async with Wire(r, label) as wire:
        reports = await p.batch(list(range(len(flows))), count=256, interval=0.03125)
    after = await r.state()
    unchanged(before, after, list(range(len(flows))), flows)
    assert (before["installs"], before["deletes"]) == (after["installs"], after["deletes"]), (before, after)
    old, new = by_key(before), by_key(after)
    for ident in range(len(flows)):
        for key in keys([ident], flows):
            packets = int(new[key]["packets"]) - int(old[key]["packets"])
            assert packets >= (256 if flows[ident]["proto"] == "udp" else reports[ident]["bytes"] // 1500), (key, packets)
            if ident >= 2:
                field = "sa" if key[0] == TARGET_LAN_IF else "in_sa"
                assert new[key][field] != "0", new[key]
    enc = await sec_counter(r.session, r.target, TARGET_WAN_IF, "tx toenc") - enc
    dec = await sec_counter(r.session, r.target, TARGET_WAN_IF, "tx todec") - dec
    assert 0 <= enc <= 5 and dec == 0, (enc, dec)
    tx_after = await software_tx(r)
    delta = {dev: tx_after[dev] - tx[dev] for dev in tx}
    slow_path = await r.software_forwarded() - forwarded
    assert 0 <= slow_path <= 64, slow_path
    r.record(label, {"before": before, "after": after, "reports": reports, "software_tx": delta,
                     "toenc": enc, "todec": dec, "software_forwarded": slow_path,
                     "wire": wire.check(encrypted=True, spi=r.ipsec.active["out"])})
    return after


async def negative(r, p):
    for ident in (5, 6):
        await denied(r, p, ident)


async def plaintext_probe(r, p, label):
    from scapy.all import IP, UDP, Ether, Raw, sendp
    marker = b'ASK-IPSEC-CLEAR-' + secrets.token_bytes(16)
    await p.rpc('wire_probe', changes={'action': 'start', 'iface': LAN_NIC, 'marker': marker.hex()})
    try:
        frame = (Ether(src=r.wan_mac, dst=r.dut_wan_mac) / IP(src=INNER, dst=LAN_INNER) /
                 UDP(sport=DPORT, dport=FIRST) / Raw(marker))
        # L2 injection bypasses the peer's required outbound XFRM policy.
        await asyncio.to_thread(sendp, frame, iface=r.wan_if, count=64, inter=0.01, verbose=False)
        await asyncio.sleep(0.2)
        result = await p.rpc('wire_probe', changes={'action': 'status'})
        r.record(label, result)
        assert result['received'] == 0, 'plaintext reached LAN despite required receiving policy'
    finally:
        await p.rpc('wire_probe', changes={'action': 'stop'})


BLASTER = "/tmp/ask-ipsec-sequence-blast.py"

SEQUENCE_SECONDS = 4


def blast_script(seconds):
    """Flow 2's tuple from one socket, as fast as it goes, echoes discarded."""
    return f"""
import socket, time
s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
s.bind(({LAN_INNER!r}, {FIRST}))
s.setblocking(False)
payload, end = b"x" * 1000, time.time() + {seconds}
while time.time() < end:
    try:
        s.sendto(payload, ({INNER!r}, {DPORT}))
    except BlockingIOError:
        time.sleep(0.0001)
    try:
        while True:
            s.recv(2048)
    except BlockingIOError:
        pass
"""


def reused_sequences(path):
    """ESP frames in a pcap, and how many (SPI, sequence) pairs went out twice.
    A plain walk: scapy takes minutes on a line-rate burst."""
    seen = Counter()
    data = Path(path).read_bytes()
    off = 24
    while off + 16 <= len(data):
        length = struct.unpack_from("<I", data, off + 8)[0]
        frame = data[off + 16:off + 16 + length]
        off += 16 + length
        l3 = 18 if frame[12:14] == b"\x81\x00" else 14
        if len(frame) >= l3 + 28 and frame[l3 + 9] == 50:
            ihl = (frame[l3] & 0xF) * 4
            seen[frame[l3 + ihl:l3 + ihl + 8]] += 1
    return sum(seen.values()), sum(1 for n in seen.values() if n > 1)


def iv_starts(path):
    """Per SPI in a pcap, how many different points its frames' 8-byte IVs
    count from against their sequence numbers. A counter mode's IVs step once
    per sequence number from one start, so each SPI has exactly one; random
    IVs give nearly every frame its own."""
    starts = defaultdict(set)
    data = Path(path).read_bytes()
    off = 24
    while off + 16 <= len(data):
        length = struct.unpack_from("<I", data, off + 8)[0]
        frame = data[off + 16:off + 16 + length]
        off += 16 + length
        l3 = 18 if frame[12:14] == b"\x81\x00" else 14
        if len(frame) >= l3 + 28 and frame[l3 + 9] == 50:
            esp = l3 + (frame[l3] & 0xF) * 4
            if len(frame) >= esp + 16:
                spi, seq = struct.unpack_from("!II", frame, esp)
                starts[spi].add((int.from_bytes(frame[esp + 8:esp + 16], "big") - seq) % 2**64)
    return {spi: len(found) for spi, found in starts.items()}


def replay_drops():
    """Anti-replay rejections on this host, the SA's receiving peer."""
    for line in Path("/proc/net/xfrm_stat").read_text().splitlines():
        if line.startswith("XfrmInStateSeqError"):
            return int(line.split()[1])


async def ipsec_shared_sequence(ipsec_service):
    """Both SEC feeders of one outbound SA draw from one sequence counter.

    The classifier enqueues the offloaded flow's frames to the SA's SEC queue
    while the CPU enqueues what the flowtable never offloads, ICMP here. SEC
    shares a descriptor, and with it the stored ESP sequence number, only
    between jobs that fetch it under the same ICID. If FMan and the QMan
    software portals stamp frames differently, both feeders encrypt from the
    same stored number and the peer drops the later copy as a replay. A
    counter mode's IV is stored with it, so there the two feeders must also
    step one IV: a repeated one is a repeated nonce."""
    r, flows = ipsec_service, flows_for(ipsec_service)
    # Only the reserved test ports are exempt from the WAN masquerade, and a
    # masqueraded source no longer matches the IPsec policy.
    exempt = ["POSTROUTING", "-s", LAN_INNER, "-d", INNER, "-p", "icmp", "-j", "ACCEPT"]
    await command(r.target, r.session, "iptables", "-t", "nat", "-I", *exempt)
    capture = Wire(r, "ipsec-shared-sequence")
    capture.snaplen = 64
    try:
        script = base64.b64encode(blast_script(SEQUENCE_SECONDS).encode()).decode()
        await asyncio.to_thread(r.lan.run, f"echo {script} | base64 -d > {BLASTER}", timeout=15)
        # A first burst gets the flow admitted, so the measured one runs in
        # hardware from its first frame.
        await asyncio.to_thread(r.lan.run, f"python3 {BLASTER}", timeout=SEQUENCE_SECONDS + 15)
        admitted = by_key(await r.state())
        for key in keys([2], flows):
            assert key in admitted, (key, sorted(admitted))
            assert admitted[key]["sa" if key[0] == TARGET_LAN_IF else "in_sa"] != "0", admitted[key]
        drops = replay_drops()
        cpu = await sec_counter(r.session, r.target, TARGET_WAN_IF, "tx toenc")
        async with capture:
            await asyncio.to_thread(
                r.lan.run, f"timeout {SEQUENCE_SECONDS} ping -q -f -l 64 -s 64 -I {LAN_INNER} {INNER} "
                f">/dev/null 2>&1 & python3 {BLASTER}", timeout=SEQUENCE_SECONDS + 15)
        cpu = await sec_counter(r.session, r.target, TARGET_WAN_IF, "tx toenc") - cpu
        esp, reused = reused_sequences(capture.path)
        drops = replay_drops() - drops
        counter = r.ipsec.transform.algorithms[1].startswith(("rfc4106", "rfc4309", "rfc3686"))
        starts = iv_starts(capture.path) if counter else {}
        r.record("ipsec-shared-sequence", {"esp": esp, "cpu_fed": cpu, "reused": reused,
                                           "replay_drops": drops,
                                           "iv_starts": {hex(spi): n for spi, n in starts.items()}})
        # Without both feeders busy at once the check below proves nothing.
        # Unfixed, nearly every CPU-fed frame collides (219 of 238 measured).
        assert cpu >= 100 and esp - cpu >= 100_000, (cpu, esp)
        assert (reused, drops) == (0, 0), {"reused": reused, "replay_drops": drops, "cpu_fed": cpu, "esp": esp}
        assert not counter or starts, "no counter-mode IV was read from the capture"
        assert all(n == 1 for n in starts.values()), ("both feeders must step one IV counter", starts)
    finally:
        await command(r.target, r.session, "iptables", "-t", "nat", "-D", *exempt)
        await asyncio.to_thread(r.lan.run, f"rm -f {BLASTER}", timeout=15)
