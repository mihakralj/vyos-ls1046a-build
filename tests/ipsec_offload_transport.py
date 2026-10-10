"""A packet-offloaded transport-mode SA, which its peer decrypts.

Transport mode keeps the packet's own IP header, so SEC has to be told how
long that header is and where its next-header byte sits: it moves that byte
into the ESP trailer, writes ESP in its place and encrypts what follows. The
DPAA driver hands SEC those values per frame in DPOVRD, which overrides the
SA's PDB. A tunnel's value there -- the inner protocol, with header length and
offset zero -- makes SEC encrypt the IP header itself and name IPIP in the
trailer, and nothing the peer receives decodes.

The DUT sends UDP from its WAN address to an address on the WAN host's
loopback through an offloaded transport SA; the WAN host's half is ordinary
software. Half the datagrams carry IPv4 options, whose longer header the SA's
PDB alone would get wrong. Every datagram must arrive once and intact, the
DUT's port must have handed SEC exactly that many frames, the peer's SA must
have decrypted as many, and the peer must have refused nothing.

A second case selects every protocol between the two hosts, which is what
strongSwan installs for a host-to-host SA and so what a transport SA usually
runs under, and sends datagrams as large as the SA carries: each leaves SEC as
a frame exactly the port's MTU. A selector that wide also covers the SA's own
ESP, and the adapter once asked for the route to the peer through xfrm, which
answered with the SA's own bundle and its inner MTU. The SA's entry then
bounded every frame leaving SEC by that, and excepted or fragmented the
full-size ones (A231). Every frame must cross whole: the peer sees each as one
ESP frame of the full size, the microcode fragments nothing, and every
datagram arrives, with DF and without.
"""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import secrets
import struct
import threading

import pytest

from ask_orch.client import Agent
from ask_orch.uart import Console
from _topology import TARGET_WAN_IF
from _flowtable_ipv6_sa import (fragments_sent)
from _flowtable_rig import (artifact_dir, WAN_IP, Echo, command, console_python)
from _flowtable_service_ipsec import (wire_interface)
from _flowtable_service_ipsec_replay import (PEER_ERRORS, xfrm_mib)
from _ipsec_inbound_flow_offload import (AUTH, CIPHER, sec_counter)
from _ipsec_offload_egress_device import (PEER, wan_outer)
from _ipsec_packet_offload_traffic import (decrypted_packets)

PORT = 48992
FULL_PORT = 48993
REQID = "49521"
COUNT = 32
# Four option bytes: three no-ops and the end of the list, so the header is
# 24 bytes rather than 20.
OPTIONS = b"\x01\x01\x01\x00"
ALGORITHMS = ["mode", "transport", "reqid", REQID, "enc", "cbc(aes)", CIPHER,
              "auth-trunc", "hmac(sha256)", AUTH, "128"]


def payload(n):
    return struct.pack("!Q", n) + b"ASK-transport-sa".ljust(48, b".")


def full_payload(n, size):
    """Datagram `n` of an IPv4 packet `size` bytes long, headers included."""
    return struct.pack("!Q", n) + b"ASK-transport-full".ljust(size - 36, b".")


def sender(source):
    return f'''
import socket, struct, time
for options, first in ((b'', 0), ({OPTIONS!r}, {COUNT})):
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    if options:
        s.setsockopt(socket.IPPROTO_IP, socket.IP_OPTIONS, options)
    s.bind(({source!r}, 0))
    for n in range(first, first + {COUNT}):
        s.sendto(struct.pack('!Q', n) + b'ASK-transport-sa'.ljust(48, b'.'), ({PEER!r}, {PORT}))
        time.sleep(0.01)
    s.close()
print('sent')
'''


def full_sender(source, size):
    """COUNT datagrams of `size` bytes with DF and COUNT without."""
    return f'''
import socket, struct, time
# IP_MTU_DISCOVER and its IP_PMTUDISC_DO and _DONT (linux/in.h).
for pmtu, first in ((2, 0), (0, {COUNT})):
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.setsockopt(socket.IPPROTO_IP, 10, pmtu)
    s.bind(({source!r}, 0))
    for n in range(first, first + {COUNT}):
        s.sendto(struct.pack('!Q', n) + b'ASK-transport-full'.ljust({size} - 36, b'.'), ({PEER!r}, {FULL_PORT}))
        time.sleep(0.01)
    s.close()
print('sent')
'''


async def transport_sa(r, wan, step, outer, spi, selector, policy_first=False):
    """The SA from the DUT's WAN address to PEER on the WAN host, with the
    policy selecting `selector` on both ends: the DUT's half packet-offloaded,
    the WAN host's ordinary software. `policy_first` installs the policies
    before the states, the order a trap policy has them in."""
    state = ["src", outer, "dst", PEER, "proto", "esp", "spi", hex(spi)]
    template = ["tmpl", "src", outer, "dst", PEER, "proto", "esp", "mode", "transport",
                "reqid", REQID, "level", "required"]
    states = [
        (wan, ["ip", "xfrm", "state", "add", *state, *ALGORITHMS, "replay-window", "32"],
         ["ip", "xfrm", "state", "delete", *state]),
        (r.target, ["ip", "xfrm", "state", "add", *state, *ALGORITHMS,
                    "offload", "packet", "dev", TARGET_WAN_IF, "dir", "out"],
         ["ip", "xfrm", "state", "delete", *state]),
    ]
    policies = [
        (wan, ["ip", "xfrm", "policy", "add", *selector, "dir", "in", *template],
         ["ip", "xfrm", "policy", "delete", *selector, "dir", "in"]),
        (r.target, ["ip", "xfrm", "policy", "add", *selector, "dir", "out", *template,
                    "offload", "packet", "dev", TARGET_WAN_IF],
         ["ip", "xfrm", "policy", "delete", *selector, "dir", "out"]),
    ]
    for group in ((policies, states) if policy_first else (states, policies)):
        for agent, argv, undo in group:
            await step(agent, argv, undo)


async def peer_on_loopback(r, wan, step):
    await step(wan, ["ip", "addr", "add", PEER + "/32", "dev", "lo"],
               ["ip", "addr", "del", PEER + "/32", "dev", "lo"])
    await step(r.target, ["ip", "route", "add", PEER + "/32", "via", WAN_IP, "dev",
                          TARGET_WAN_IF],
               ["ip", "route", "del", PEER + "/32"])


async def test_offloaded_transport_sa_is_decrypted(rig):
    r = rig
    wan = Agent("wan", f"http://{os.environ.get('ASK_WAN_IP', '127.0.0.1')}:9110")
    outer = await wan_outer(r)
    spi = 0xA8000000 | secrets.randbits(24)
    echo = Echo()
    echo.reply = False
    transport = None
    cleanup = []

    async def step(agent, argv, undo):
        await command(agent, r.session, *argv)
        cleanup.append((agent, undo))

    try:
        await peer_on_loopback(r, wan, step)
        # Only this test's datagrams: the selector names the port. UDP by
        # number: the image carries no /etc/protocols for ip to resolve names.
        selector = ["src", outer + "/32", "dst", PEER + "/32", "proto", "17", "dport", str(PORT)]
        await transport_sa(r, wan, step, outer, spi, selector)
        transport, _ = await asyncio.get_running_loop().create_datagram_endpoint(
            lambda: echo, local_addr=(PEER, PORT))

        refused = xfrm_mib(Path("/proc/net/xfrm_stat").read_text())
        toenc = await sec_counter(r.session, r.target, TARGET_WAN_IF, "tx toenc")
        with Console.target(log_path=str(artifact_dir() / "ipsec-offload-transport-uart.log")) as console:
            await asyncio.to_thread(console.login, "root", None)
            result = await console_python(console, sender(outer), timeout=30)
        assert "sent" in result["stdout"], result
        await asyncio.sleep(0.5)

        now = xfrm_mib(Path("/proc/net/xfrm_stat").read_text())
        peer = (await command(wan, r.session, "ip", "-s", "xfrm", "state"))["stdout"]
        observed = {
            "delivered": sum(echo.received[payload(n)] for n in range(2 * COUNT)),
            "duplicates": sum(c - 1 for c in echo.received.values() if c > 1),
            "toenc": await sec_counter(r.session, r.target, TARGET_WAN_IF, "tx toenc") - toenc,
            "peer_decrypted": decrypted_packets(peer, spi),
            "peer_refused": {name: now[name] - refused[name] for name in PEER_ERRORS
                             if now[name] != refused[name]},
        }
        r.record("ipsec-offload-transport", observed)
        assert observed == {"delivered": 2 * COUNT, "duplicates": 0, "toenc": 2 * COUNT,
                            "peer_decrypted": 2 * COUNT, "peer_refused": {}}, observed
        assert all(echo.received[payload(n)] == 1 for n in range(2 * COUNT)), echo.received
    finally:
        if transport:
            transport.close()
        for agent, argv in reversed(cleanup):
            await command(agent, r.session, *argv, check=False)


@pytest.mark.parametrize("policy_first", [False, True], ids=["state-first", "policy-first"])
async def test_offloaded_transport_sa_carries_full_size_frames(rig, policy_first):
    """Datagrams as large as a host-to-host transport SA carries, with DF and
    without, leave SEC as single frames of the port's full MTU, whichever of
    the SA and its any-protocol policy came first."""
    from scapy.all import IP, AsyncSniffer
    r = rig
    wan = Agent("wan", f"http://{os.environ.get('ASK_WAN_IP', '127.0.0.1')}:9110")
    outer = await wan_outer(r)
    link = json.loads((await command(r.target, r.session, "ip", "-j", "link", "show", "dev",
                                     TARGET_WAN_IF))["stdout"])
    port_mtu = int(link[0]["mtu"])
    # xfrm_state_mtu() for AES-CBC with HMAC-SHA256-128 in transport mode:
    # the 8-byte ESP header, the 16-byte IV and ICV and the IPv4 header come
    # off, the rest is aligned to the block and two trailer bytes come off
    # that. Such a datagram pads to nothing, so it leaves SEC exactly 42
    # bytes longer: at most the port's MTU, and exactly it on 1500 bytes.
    size = ((port_mtu - 60) & ~15) + 18
    frame = size + 42
    assert frame <= port_mtu and (port_mtu != 1500 or frame == 1500), (port_mtu, size, frame)
    spi = 0xA8000000 | secrets.randbits(24)
    echo = Echo()
    echo.reply = False
    transport = sniffer = None
    cleanup = []

    async def step(agent, argv, undo):
        await command(agent, r.session, *argv)
        cleanup.append((agent, undo))

    try:
        await peer_on_loopback(r, wan, step)
        # Every protocol between the two hosts: it covers the SA's own ESP
        # from the DUT to the peer, which is what exposes the lookup.
        selector = ["src", outer + "/32", "dst", PEER + "/32"]
        await transport_sa(r, wan, step, outer, spi, selector, policy_first)
        transport, _ = await asyncio.get_running_loop().create_datagram_endpoint(
            lambda: echo, local_addr=(PEER, FULL_PORT))
        # Two accounting passes, each of which asks every SA's path again:
        # an SA installed before its policy met the policy there.
        await asyncio.sleep(2.5)

        refused = xfrm_mib(Path("/proc/net/xfrm_stat").read_text())
        toenc = await sec_counter(r.session, r.target, TARGET_WAN_IF, "tx toenc")
        fragments = await fragments_sent(r)
        # Everything the DUT sends the peer, as the wire carries it.
        ready = threading.Event()
        sniffer = AsyncSniffer(iface=wire_interface(r), store=True, started_callback=ready.set,
                               filter=f"ip and src host {outer} and dst host {PEER}")
        sniffer.start()
        assert await asyncio.to_thread(ready.wait, 5), "wire capture did not start"
        with Console.target(log_path=str(artifact_dir() / "ipsec-offload-transport-full-uart.log")) as console:
            await asyncio.to_thread(console.login, "root", None)
            result = await console_python(console, full_sender(outer, size), timeout=30)
        assert "sent" in result["stdout"], result
        await asyncio.sleep(0.5)
        captured = await asyncio.to_thread(sniffer.stop)
        sniffer = None

        now = xfrm_mib(Path("/proc/net/xfrm_stat").read_text())
        peer = (await command(wan, r.session, "ip", "-s", "xfrm", "state"))["stdout"]
        esp = [p[IP] for p in captured if IP in p and p[IP].proto == 50]
        observed = {
            "delivered": sum(echo.received[full_payload(n, size)] for n in range(2 * COUNT)),
            "duplicates": sum(c - 1 for c in echo.received.values() if c > 1),
            "toenc": await sec_counter(r.session, r.target, TARGET_WAN_IF, "tx toenc") - toenc,
            "fragmented": {k: v - fragments[k] for k, v in (await fragments_sent(r)).items()},
            "frames": len(esp),
            "sizes": sorted({p.len for p in esp}),
            "split": sum(1 for p in esp if p.flags.MF or p.frag),
            "df": sum(1 for p in esp if p.flags.DF),
            "peer_decrypted": decrypted_packets(peer, spi),
            "peer_refused": {name: now[name] - refused[name] for name in PEER_ERRORS
                             if now[name] != refused[name]},
        }
        r.record("ipsec-offload-transport-full", {"size": size, "frame": frame, **observed})
        # Every datagram crossed SEC once, left it whole at the full size --
        # the DF half with DF, which transport mode copies from the packet's
        # own header -- and reached the far end: none was excepted after
        # SEC, which would have lost it, nor fragmented, which the microcode
        # would have counted.
        assert observed == {"delivered": 2 * COUNT, "duplicates": 0, "toenc": 2 * COUNT,
                            "fragmented": {4: 0, 6: 0}, "frames": 2 * COUNT, "sizes": [frame],
                            "split": 0, "df": COUNT, "peer_decrypted": 2 * COUNT,
                            "peer_refused": {}}, observed
    finally:
        if sniffer:
            sniffer.stop()
        if transport:
            transport.close()
        for agent, argv in reversed(cleanup):
            await command(agent, r.session, *argv, check=False)
