"""The gateway as an ISP ships it, proved as one configuration rather than one
feature at a time.

Every other flowtable file isolates a single encapsulation and builds the
smallest topology that can carry it, which is what makes a failure legible.
None of them answers the question a release actually turns on: whether the
features hold *together*, on the box a subscriber has, while the events a real
line produces happen underneath them. That is this file.

The configuration is the production one. The uplink is PPPoE on a tagged
carrier VLAN, so every forwarded frame spends both encapsulation slots the WAN
direction has. The LAN is a VLAN-aware bridge over the one physical LAN port,
untagged on the subscriber VLAN and tagged for a guest network, which is the
shape OpenWrt renders. IPTV arrives on its own VLAN and is bridged at layer 2
into the same bridge, where the bridge's own IGMP snooping is the entire
control plane. Traffic is classified into CEETM queues by a conntrack mark, and
one port is forwarded from the WAN side into a LAN client. Five features, one
bridge, one physical port on each side.

One behaviour of the shipping configuration shapes every traffic case: a
subscriber's UDP upload into the session stays in Linux, IPv4 or IPv6. The LAN
port can deliver a full 1500-byte frame whatever MTU it is given or advertises
and the session carries 1492, so the microcode would have to fragment it -- and
its fragments of a frame an Ethernet port received carry no payload. The
download still crosses in hardware, and so does all of TCP, its MSS clamped to
the session. So each case proves the UDP download and the refusal of its
upload, and proves the upload itself on a TCP connection.

Two disciplines every case here keeps, because a profile test is exactly where
they are easiest to lose:

  - **the row names the encapsulation.** A frame that arrives is not evidence
    of anything: the Linux bridge floods, the CPU routes, and a software path
    produces the same delivery. Each case reads the adapter's own row back and
    requires it to name the session, the tags, the bridge and the class it was
    supposed to build, then sends a *second* burst and requires the classifier's
    own packet counters to account for all of it.
  - **the CPU did not do it.** The physical ports' software receive counters are
    read across the measured burst and have to stay flat while the hardware
    counters move. Those two together are the only pair that separates
    acceleration from a working stack.

The lifecycle half is the other reason the file exists. A line redials, a
subscriber changes channel, a LAN cable is pulled, an operator reloads the
offload policy -- and each of those has to retire exactly what depends on it and
readmit everything afterwards, with no reconfiguration in between, because a
subscriber does not reconfigure anything. Those cases run last, in that order,
against a profile the traffic cases have already driven.

Bench resources this profile uses:

    - dell2's session-owned tagged WAN interface and a temporary PPPoE access
        concentrator; the shared topology, server and dial helpers own restoration;
    - dell2 as the multicast source on the WAN wire, injecting untagged by
        default; `ASK_PROFILE_IPTV_TAGGED=1` selects tagged injection when the
        selected topology carries the IPTV VLAN;
  - the QoS case needs the adapter booted with a nonzero
    `ask_flowtable.qos_mark_mask`; it skips, naming the parameter, otherwise.
"""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
import json
import os
import re
import socket
import time

import aiohttp
import pytest
import pytest_asyncio

from ask_orch.capture import capture_window
from ask_orch.commands import remove_qdisc
from ask_orch.client import Agent
from ask_orch.uart import Console
from ask_orch.counters import kernel_tx_packets
from _gated_tcp import GatedTcp
from _topology import (LAN_NIC, TARGET_LAN_IF, TARGET_WAN_IF, TopologyStack,
                       dut_vlan_subif, kernel_rx_packets, lan_run_python)
from _flowtable_rig import (artifact_dir, Rig, assert_undisturbed, command, console_command, read)
from _flowtable_pppoe import (
    INNER_LOCAL,
    INNER_LOCAL6,
    INNER_REMOTE6,
    SESSION_MTU,
    SourceEcho,
    WAN_VID,
    _dial,
    _hangup,
    _server_start,
    _server_stop,
    _session_identity,
    _session_text,
)
from _flowtable_policy import (CONFIG, apply, stop)

pytestmark = [
    pytest.mark.requires("pppd", "smcrouted"),
    # Every case runs on the loop the profile fixture was built on. Without
    # this the suite's default function loop scope gives each test a loop of
    # its own, and the fixture's loop -- the one holding the echo endpoints the
    # traffic helpers talk to -- never runs again after setup: nothing is
    # echoed, no error is raised anywhere, and every case fails as an
    # unreachable peer. Verified both ways before it was written down.
    pytest.mark.asyncio(loop_scope="module"),
]

# ---- constants that belong in _topology.py's VLAN conventions block --------
#
# Claimed here only because this file may not edit that one while it is being
# changed elsewhere. Move them across with the rest of the block:
#
#   profile_isp.py     291/292/293 + 3999 (IPTV, the production VLAN)
#
# 3900 is the standing bench VLAN the concentrator sits behind, imported rather
# than restated. 3999 is the VLAN the shipping gateway receives IPTV on;
# mcast_e2e.py names the same id for the same reason and the two never run
# in one boot, because this file owns both physical ports while it runs.
BRIDGE = "br-isp"
LAN_VID = int(os.environ.get("ASK_PROFILE_ISP_LAN_VID", "291"))
GUEST_VID = int(os.environ.get("ASK_PROFILE_ISP_GUEST_VID", "292"))
IPTV_VID = int(os.environ.get("ASK_PROFILE_ISP_IPTV_VID", "3999"))

# Subnets, deliberately not derived from the VLAN ids: an id can exceed an
# octet and a subnet built out of one silently aliases as soon as it does.
LAN_SUBNET, LAN_GATEWAY, LAN_CLIENT = "172.29.91.0/24", "172.29.91.1", "172.29.91.2"
GUEST_SUBNET, GUEST_GATEWAY, GUEST_CLIENT = "172.29.92.0/24", "172.29.92.1", "172.29.92.2"
IPTV_GATEWAY, IPTV_CLIENT = "172.29.93.1", "172.29.93.2"
# The subscriber VLAN's IPv6, which the session carries natively. A ULA rather
# than a delegated prefix: the bench has no upstream IPv6 and the subject is the
# path, not the address.
LAN_PREFIX6, LAN_GATEWAY6, LAN_CLIENT6 = "fc00:91::/64", "fc00:91::1", "fc00:91::2"

CLIENT_MAC = "02:9d:99:b2:91:02"
GUEST_MAC = "02:9d:99:b2:92:02"
IPTV_MAC = "02:9d:99:b2:93:02"

# One port per case, so a conntrack left behind by one never feeds another.
# Above every other flowtable file's range; the highest in use is the tunnel
# file's 48951.
PORT_MAIN = int(os.environ.get("ASK_PROFILE_ISP_PORT", "49100"))
PORT_GUEST = PORT_MAIN + 2
PORT_V6 = PORT_MAIN + 4
PORT_VOICE = PORT_MAIN + 6
PORT_BULK = PORT_MAIN + 8
PORT_PUBLIC = PORT_MAIN + 10
PORT_IPTV = PORT_MAIN + 12      # and +13, one per channel
PORT_RATE = PORT_MAIN + 16
# The throughput floor's ramp: past it, the receiver's count is steady state.
RAMP_SECONDS = 3

# Two channels, so the channel-change case has a survivor to keep watching.
# Each carries its own destination port: two sockets bound to one port both
# receive every group, and the counts could not then be told apart.
GROUPS = ("239.9.1.1", "239.9.1.2")
CHANNELS = [(group, PORT_IPTV + index) for index, group in enumerate(GROUPS)]
# Above 1, always: the soft parser ends the parse before classification for TTL
# 0 or 1, so a TTL-1 stream is never matched in hardware however correct the
# entry is -- and a plain multicast socket sends TTL 1 by default.
STREAM_TTL = 64
STREAM_S, STREAM_PPS = 3.0, 500
# How long the consumers listen before the stream starts: enough for the reports
# to reach the bridge and for the learner to act on them. A membership change
# mid-stream is timed from the consumer's start, so it is offset by this too.
PRE_ROLL = 2.0

# The class queue a leaf of each priority holds. cdx_htb_cq_get() indexes from
# the top of the eight strict-priority queues (NUM_PQS - 1 - prio), so prio 0 is
# queue 7 and it is the one that wins. The mark has to name that queue, because
# the mark is what the classifier reads; the tc tree only says what shapes it.
VOICE_PRIO, BULK_PRIO = 0, 1
VOICE_CQ, BULK_CQ = 7, 6
CHANNEL_RATE, CHANNEL_CEIL = "200mbit", "800mbit"

QOS_TABLE = "ask_profile_isp_qos"
NAT_TABLE = "ask_profile_isp_nat"
ORCH_IPV4 = os.environ.get("ASK_WAN_IP", "127.0.0.1")
# Where the multicast stream is put on the wire: the orchestrator's own bridge
# on the shared WAN segment, which delivers untagged frames to the DUT's WAN
# port. The switch between them does not trunk the IPTV VLAN today.
INJECT_IF = os.environ.get("ASK_WAN_INJECT_IF", "")
IPTV_TAGGED = os.environ.get("ASK_PROFILE_IPTV_TAGGED") == "1"


def orchestrator_source():
    """The source address the stream carries, and the one the classifier keys
    on. The injector and consumer read the same configured traffic endpoint."""
    return os.environ.get("ASK_WAN_IPERF_IP", "")


# ---- reading the adapter ---------------------------------------------------

class Profile(Rig):
    """Rig's reading half over a whole profile rather than one connection.

    `state`, `wait`, `nft` and `record` are reused as they are. `table`,
    `exchange` and `clear_ct` are not: each describes one LAN address against
    one WAN endpoint, and a profile has several of both. The offload policy is
    the shipping daemon's own catch-all instead -- which is what a subscriber
    runs -- and the traffic helpers below take a client.
    """


def _direction(flows, source, destination):
    """One installed direction, named by the endpoints of its match."""
    matching = [f for f in flows if f["src"] == source and f["dst"] == destination]
    assert len(matching) == 1, (source, destination, flows)
    return matching[0]


def _directions(flows, source, sport, peer, dport, upload=True):
    """Both halves of one connection, whether or not it is translated.

    The forward half is named by the tuple the client sent. The reverse half is
    named by the tuple that arrives from the peer, which under masquerade
    carries the translated address and can be joined back to the client only
    through `new_dst`. Matching on that covers both cases with one helper, and a
    profile has both at once. With `upload` false the forward half is Linux's
    by design (see _upload_in_linux): it must be absent, and is returned as
    None.
    """
    reverse = [f for f in flows if f["src"] == f"{peer}:{dport}"
               and f["new_dst"] == f"{source}:{sport}"]
    assert len(reverse) == 1, (source, sport, peer, dport, flows)
    if not upload:
        assert not [f for f in flows if f["src"] == f"{source}:{sport}"
                    and f["dst"] == f"{peer}:{dport}"], (source, sport, peer, dport, flows)
        return None, reverse[0]
    return _direction(flows, f"{source}:{sport}", f"{peer}:{dport}"), reverse[0]


def _upload_in_linux(peer):
    """Whether a UDP connection's LAN-to-WAN direction stays in Linux.

    It always does here. It arrives on the LAN port, which can deliver a full
    1500-byte frame whatever MTU the port is given -- and a host may ignore
    the MTU a DHCP server offers or a router advertisement carries -- and
    leaves into the session's 1492. The microcode would have to fragment it,
    and its fragments of a frame an Ethernet port received carry no payload,
    so the adapter leaves that direction to Linux, which fragments IPv4 and
    answers IPv6 with Packet Too Big. The download still crosses in hardware,
    and so does a TCP upload, its MSS clamped to the session; TCP is how every
    property of the upload is proved here."""
    return True


def _bracketed(address):
    return f"[{address}]" if ":" in address else address


# ---- LAN-side clients ------------------------------------------------------

def _client(name, *, vid, ip, mac, gateway, ip6=None, gateway6=None):
    return {"name": name, "netns": f"ask-profile-{name}", "iface": f"askp{name[:4]}",
            "vid": vid, "ip": ip, "mac": mac, "gateway": gateway,
            "ip6": ip6, "gateway6": gateway6}


CLIENTS = [
    _client("main", vid=None, ip=LAN_CLIENT, mac=CLIENT_MAC, gateway=LAN_GATEWAY,
            ip6=LAN_CLIENT6, gateway6=LAN_GATEWAY6),
    _client("guest", vid=GUEST_VID, ip=GUEST_CLIENT, mac=GUEST_MAC,
            gateway=GUEST_GATEWAY),
    _client("iptv", vid=IPTV_VID, ip=IPTV_CLIENT, mac=IPTV_MAC, gateway=None),
]
BY_NAME = {c["name"]: c for c in CLIENTS}


async def _build_clients(ctx):
    """One network namespace per LAN client, on the one physical LAN wire.

    The subscriber's client is a macvlan, so it is untagged on the wire exactly
    as a laptop is; the guest and IPTV clients are VLAN devices moved into their
    namespaces, so their frames carry the tag the bridge classifies on. Each
    gets a MAC of its own, so the bridge learns three distinct stations on one
    port -- which is what a household looks like to the FDB, and what makes an
    egress port chosen from it worth pinning.

    The whole build is one script and it removes every namespace it created if
    any step fails. A half-built client left behind would be inherited by the
    next run as a name collision rather than as the failure it is.
    """
    setup = f'''
import pathlib, subprocess, time
clients = {CLIENTS!r}
def run(*args): subprocess.run(args, check=True, capture_output=True, text=True)

# Anything a previous run left behind, before asserting the wire is clean.
#
# A listener started detached outlives a run that aborted, and while it lives
# it holds its network namespace open -- `ip netns del` only unlinks the name.
# The macvlan inside that namespace therefore keeps its MAC registered against
# the lower device, and the next run's client cannot take the same one: the
# failure is "Address already in use" on a host where `ip netns list` is empty
# and `ip link` shows nothing. Measured, not imagined; it is how this fixture
# first failed on the rig.
subprocess.run(['pkill', '-9', '-f', '/tmp/ask_profile_isp_'], capture_output=True)
for c in clients:
    subprocess.run(['ip', 'netns', 'del', c['netns']], capture_output=True)
    subprocess.run(['ip', 'link', 'del', c['iface']], capture_output=True)
time.sleep(0.5)

for c in clients:
    assert not pathlib.Path('/var/run/netns/' + c['netns']).exists(), c['netns']
    assert not pathlib.Path('/sys/class/net/' + c['iface']).exists(), c['iface']
created = []
try:
    for c in clients:
        run('ip', 'netns', 'add', c['netns']); created.append(c)
        if c['vid'] is None:
            run('ip', 'link', 'add', 'link', {LAN_NIC!r}, 'name', c['iface'],
                'netns', c['netns'], 'type', 'macvlan', 'mode', 'bridge')
        else:
            run('ip', 'link', 'add', 'link', {LAN_NIC!r}, 'name', c['iface'],
                'type', 'vlan', 'id', str(c['vid']))
            run('ip', 'link', 'set', c['iface'], 'netns', c['netns'])
        run('ip', '-n', c['netns'], 'link', 'set', 'lo', 'up')
        run('ip', '-n', c['netns'], 'link', 'set', c['iface'],
            'address', c['mac'], 'up')
        run('ip', '-n', c['netns'], 'addr', 'add', c['ip'] + '/24', 'dev', c['iface'])
        # A multicast stream's source is on another subnet entirely, and a
        # namespace that reverse-path filters would drop every frame of it.
        run('ip', 'netns', 'exec', c['netns'], 'sysctl', '-qw',
            'net.ipv4.conf.all.rp_filter=0')
        run('ip', 'netns', 'exec', c['netns'], 'sysctl', '-qw',
            'net.ipv4.conf.%s.rp_filter=0' % c['iface'])
        if c['gateway']:
            run('ip', '-n', c['netns'], 'route', 'add', 'default', 'via', c['gateway'])
        if c['ip6']:
            run('ip', '-n', c['netns'], 'addr', 'add', c['ip6'] + '/64',
                'dev', c['iface'], 'nodad')
            run('ip', '-n', c['netns'], 'route', 'add', 'default', 'via', c['gateway6'])
except BaseException:
    for c in reversed(created):
        subprocess.run(['ip', 'netns', 'del', c['netns']], capture_output=True)
    raise
print('CLIENTS-UP')
'''
    result = await lan_run_python(ctx.lan, setup, label="profile_isp_clients", timeout=60)
    assert result.rc == 0 and "CLIENTS-UP" in result.stdout, result.stdout


async def _drop_clients(ctx):
    teardown = f'''
import os, signal, subprocess
errors = []
for c in {CLIENTS!r}:
    pids = subprocess.check_output(['ip', 'netns', 'pids', c['netns']], text=True)
    for pid in pids.split():
        try:
            os.kill(int(pid), signal.SIGTERM)
        except ProcessLookupError:
            pass
    result = subprocess.run(['ip', 'netns', 'del', c['netns']],
                            capture_output=True, text=True)
    if result.returncode:
        errors.append(result.stderr)
assert not errors, errors
'''
    await lan_run_python(ctx.lan, teardown, label="profile_isp_clients_down", timeout=40)


async def _client_python(ctx, client, script, *, label, timeout=60):
    """Run a script inside one client's namespace on the LAN VM.

    The same setns preamble Rig.run_peer uses, stated here because a profile
    drives several namespaces within one test and the Rig-level `peer_netns`
    attribute would have to be set and unset around every call.
    """
    staged = ("import os\n"
              f"with open('/var/run/netns/' + {client['netns']!r}, 'rb') as ns:\n"
              "    os.setns(ns.fileno(), os.CLONE_NEWNET)\n" + script)
    return await lan_run_python(ctx.lan, staged, label=label, timeout=timeout)


# ---- traffic ---------------------------------------------------------------

def _source_address(client, peer):
    """Which of a client's two addresses reaches `peer`."""
    return client["ip6"] if ":" in peer else client["ip"]


async def _exchange(ctx, client, *, peer, dport, sport, count, payload_size=256,
                    label="profile_isp"):
    """Echo `count` datagrams from one client and report what came back.

    A reply from the wrong endpoint or with the wrong payload is fatal, because
    either means the path is not the one under test. A timeout is only counted,
    so a caller can tolerate loss while a flow is still being admitted and
    forbid it once the row says it is installed.
    """
    source = _source_address(client, peer)
    family = "socket.AF_INET6" if ":" in source else "socket.AF_INET"
    script = f'''
import json, socket, struct, time
s = socket.socket({family}, socket.SOCK_DGRAM)
s.settimeout(2)
s.bind(({source!r}, {sport}))
echoed = lost = 0
for n in range({count}):
    payload = struct.pack('!Q', n) + b'ASK-profile'.ljust({payload_size} - 8, b'.')[:{payload_size} - 8]
    s.sendto(payload, ({peer!r}, {dport}))
    try:
        data, addr = s.recvfrom(4096)
    except TimeoutError:
        lost += 1
        continue
    assert data == payload, (n, len(data), len(payload))
    assert (addr[0], addr[1]) == ({peer!r}, {dport}), (n, addr)
    echoed += 1
    time.sleep(0.005)
s.close()
print(json.dumps({{'echoed': echoed, 'lost': lost}}))
'''
    result = await _client_python(ctx, client, script, label=label,
                                  timeout=count * 0.3 + 40)
    assert result.rc == 0, result.stdout
    return json.loads(result.stdout.strip().splitlines()[-1])


async def _software_rx(ctx):
    return {dev: await kernel_rx_packets(ctx.target, ctx.session, dev)
            for dev in (TARGET_LAN_IF, TARGET_WAN_IF)}


DOWNLOAD_CPU_TABLE = "ask_isp_cpu"


@asynccontextmanager
async def _download_cpu_counter(ctx, *, peer, dport):
    """Count one UDP download's frames that reach the CPU on the WAN port.

    The WAN port's own receive counter also moves for everything else on the
    office LAN behind it, in bursts larger than a burst's whole budget, so it
    cannot say whether a download stayed in hardware. A netdev ingress chain
    on the port can: a frame the classifier forwards never gets there, and the
    kernel has taken the carrier VLAN's tag off before it, which leaves the
    PPPoE session header at the network header. Matched there, by the PPP
    protocol, UDP and the far end's port, which NAT leaves alone; the
    software flowtable's own hook comes after this one, so every CPU frame of
    the download is counted, whichever path then forwards it.

    Installed before the connection is admitted, so the commit cannot
    readmit anything under the measurement."""
    if ":" in peer:     # PPP IPv6, next header, UDP source port after 40 bytes
        match = ["@nh,48,16", "0x0057", "@nh,112,8", "17", "@nh,384,16", str(dport)]
    else:               # PPP IPv4 with a 20-byte header, protocol, source port
        match = ["@nh,48,16", "0x0021", "@nh,64,8", "0x45", "@nh,136,8", "17",
                 "@nh,224,16", str(dport)]
    await command(ctx.target, ctx.session, "nft", "delete", "table", "netdev", DOWNLOAD_CPU_TABLE,
                  check=False)
    await command(ctx.target, ctx.session, "nft", "add", "table", "netdev", DOWNLOAD_CPU_TABLE)
    try:
        await command(ctx.target, ctx.session, "nft", "add", "chain", "netdev", DOWNLOAD_CPU_TABLE,
                      TARGET_WAN_IF, "{", "type", "filter", "hook", "ingress", "device",
                      TARGET_WAN_IF, "priority", "-500", ";", "policy", "accept", ";", "}")
        await command(ctx.target, ctx.session, "nft", "add", "rule", "netdev", DOWNLOAD_CPU_TABLE,
                      TARGET_WAN_IF, "meta", "protocol", "0x8864", *match, "counter")

        async def frames():
            listed = json.loads((await command(ctx.target, ctx.session, "nft", "-j", "list",
                                               "chain", "netdev", DOWNLOAD_CPU_TABLE,
                                               TARGET_WAN_IF))["stdout"])
            return sum(e["counter"]["packets"] for item in listed["nftables"] if "rule" in item
                       for e in item["rule"]["expr"] if "counter" in e)
        yield frames
    finally:
        await command(ctx.target, ctx.session, "nft", "delete", "table", "netdev", DOWNLOAD_CPU_TABLE,
                      check=False)


async def _software_tx(ctx):
    """The transmit side of the same counters.

    Used where a case has to prove the CPU *did* carry the traffic. The receive
    counter is the wrong one for that: an ingress hook that consumes the skb can
    return before the driver increments RX, so a software flowtable forwards
    frames that never appear in it (ask_orch/counters.py says so). Transmit
    counts every frame the software path put on the wire either way.
    """
    return {dev: await kernel_tx_packets(ctx.target, ctx.session, dev)
            for dev in (TARGET_LAN_IF, TARGET_WAN_IF)}


async def _admit(ctx, client, *, peer, dport, sport, timeout=20, label="profile_isp"):
    """Send short bursts until the connection's hardware directions are
    installed: both of them, or the download alone where the upload is
    Linux's (see _upload_in_linux), whose refusal is then counted.

    A deadline rather than a round count: an admission that loses rtnl_trylock
    is declined, and the software path offers the flow again only about a
    second later, while traffic keeps it there; nothing re-offers a retired
    flow on its own -- so every attempt has to send before it looks.
    """
    source = _bracketed(_source_address(client, peer))
    target = _bracketed(peer)
    upload = not _upload_in_linux(peer)
    initial = await ctx.state()
    deadline = asyncio.get_running_loop().time() + timeout
    while True:
        await _exchange(ctx, client, peer=peer, dport=dport, sport=sport, count=4,
                        label=label)
        state = await ctx.state()
        try:
            directions = _directions(state["flows"], source, sport, target, dport,
                                     upload=upload)
            assert upload or state["rejects"] > initial["rejects"], (initial, state)
            return directions
        except AssertionError:
            if asyncio.get_running_loop().time() > deadline:
                conntrack = await command(ctx.target, ctx.session, "conntrack", "-L",
                                          "-o", "extended", check=False)
                state = await ctx.state()
                ctx.record("isp-partial-admission",
                           {"state": state, "conntrack": conntrack})
                pytest.fail(
                    f"{client['name']} {source}:{sport} -> {target}:{dport} was not "
                    f"admitted as {'both directions' if upload else 'the download alone'}: "
                    f"validated={state['validated']} "
                    f"rejects={state['rejects']} busy={state['busy']} "
                    f"errors={state['errors']}\nflows={state['flows']}")
            await asyncio.sleep(0.5)


async def _accounted(ctx, client, *, peer, dport, sport, count=128, payload_size=256,
                     label="profile_isp"):
    """Admit the connection, then measure a second burst against the hardware.

    Returns the two rows as they stood before the measured burst, so a case
    asserts the encapsulation on rows whose counters it has just accounted for
    rather than on rows it has merely seen. The forward row is None where the
    upload is Linux's.

    Three things are required of that burst and all three are needed: the rows
    are the same rows -- the cookies did not move and no offer took RTNL,
    so nothing was readmitted underneath the measurement -- the classifier
    counted every frame, and none of the download's own frames reached the CPU
    (_download_cpu_counter); on the LAN side, the port's software receive
    counter did not move for an upload in hardware. An upload Linux keeps must
    leave the WAN port in software once per datagram.
    """
    initial = await ctx.state()
    async with _download_cpu_counter(ctx, peer=peer, dport=dport) as download_cpu:
        forward, reverse = await _admit(ctx, client, peer=peer, dport=dport, sport=sport,
                                        label=label)
        installed = await ctx.state()
        admitted_cpu = await download_cpu()
        before = {f["cookie"]: int(f["packets"]) for f in (forward, reverse) if f}
        software_before, sent_before = await _software_rx(ctx), await _software_tx(ctx)
        report = await _exchange(ctx, client, peer=peer, dport=dport, sport=sport,
                                 count=count, payload_size=payload_size, label=label)
        software_after, sent_after = await _software_rx(ctx), await _software_tx(ctx)
        measured_cpu = await download_cpu() - admitted_cpu
        state = await ctx.state()
    after = {f["cookie"]: int(f["packets"]) for f in state["flows"]
             if f["cookie"] in before}
    assert report == {"echoed": count, "lost": 0}, report
    assert_undisturbed(ctx, installed, state, set(after) == set(before),
                       label=f"isp-{label}-readmitted")
    delta = {c: after[c] - before[c] for c in before}
    assert all(d == count for d in delta.values()), (delta, state)
    software = {dev: software_after[dev] - software_before[dev]
                for dev in software_before}
    sent = {dev: sent_after[dev] - sent_before[dev] for dev in sent_before}
    ctx.record(f"isp-{label}", {"forward": forward, "reverse": reverse,
                                "delta": delta, "software_rx": software,
                                "software_tx": sent, "download_cpu_admitted": admitted_cpu,
                                "download_cpu_measured": measured_cpu})
    # The counter's own proof that it matches the download: a download newly
    # installed by this admission crossed the CPU at least once before it was.
    if reverse["cookie"] not in {f["cookie"] for f in initial["flows"]}:
        assert admitted_cpu >= 1, (admitted_cpu, reverse)
    # Every frame of the measured burst in hardware, none of it on the CPU.
    assert measured_cpu == 0, (measured_cpu, count, software)
    if forward:
        assert software[TARGET_LAN_IF] < count // 4, (software, count)
    else:
        # Whether the upload's arrival shows in the LAN port's receive count
        # depends on the path Linux forwarded it by (see _software_tx), so the
        # proof that Linux carried it is the WAN port's transmit count.
        assert sent[TARGET_WAN_IF] >= count, (sent, count)
    return forward, reverse


async def _tcp_accounted(ctx, client, *, peer, dport, label="profile_isp"):
    """One TCP connection from a client to the far end, read back while it is
    open and idle (see _gated_tcp): a first phase admits it, a second is
    measured. Returns (forward, reverse) as installed.

    TCP is how the IPv4 upload is proved on this profile (see
    _upload_in_linux). The measured phase must be hardware's: the same two
    rows, a hundred packets or more each, nothing installed, retired or
    declined meanwhile, and the WAN port's software transmit count well below
    the upload. The client lets the kernel pick its port, so a connection an
    earlier case left in TIME_WAIT is never in the way; the rows are found by
    the far end's port, which no other TCP connection here uses, and joined
    back to the client's port once it reports it.
    """
    source, target = _bracketed(_source_address(client, peer)), _bracketed(peer)

    def rows(state):
        forward = [f for f in state["flows"] if f["proto"] == "6"
                   and f["src"].startswith(source + ":") and f["dst"] == f"{target}:{dport}"]
        reverse = [f for f in state["flows"] if f["proto"] == "6"
                   and f["src"] == f"{target}:{dport}"
                   and f["new_dst"].startswith(source + ":")]
        assert len(forward) == len(reverse) == 1, (
            f"{client['name']} TCP to {target}:{dport} is not in hardware both ways: "
            f"validated={state['validated']} rejects={state['rejects']} "
            f"busy={state['busy']}\nflows={state['flows']}")
        return forward[0], reverse[0]

    async def run(script, **kwargs):
        return await _client_python(ctx, client, script, **kwargs)

    async with GatedTcp(run, source=_source_address(client, peer), peer=peer, dport=dport,
                        label=label) as transfer:
        await transfer.warmed()
        before = await ctx.state()
        forward, reverse = rows(before)
        sent = (await _software_tx(ctx))[TARGET_WAN_IF]
        await transfer.measure()
        sent = (await _software_tx(ctx))[TARGET_WAN_IF] - sent
        after = await ctx.state()
        now = rows(after)
    ctx.record(f"isp-{label}", {"before": before, "after": after, "software_wan_tx": sent,
                                "report": transfer.report})
    assert_undisturbed(ctx, before, after,
                       (now[0]["cookie"], now[1]["cookie"]) == (forward["cookie"], reverse["cookie"])
                       and (after["installs"], after["deletes"]) == (before["installs"], before["deletes"]),
                       label=f"isp-{label}-readmitted")
    upload = int(now[0]["packets"]) - int(forward["packets"])
    download = int(now[1]["packets"]) - int(reverse["packets"])
    assert upload > 100 and download > 100, (upload, download)
    # Only the handful of frames the reads above cost, and the session's own
    # echoes, left the WAN port in software while the measured phase crossed.
    assert sent < upload // 4, (sent, upload)
    port = transfer.report["port"]
    assert forward["src"] == f"{source}:{port}", (forward, transfer.report)
    assert reverse["new_dst"] == f"{source}:{port}", (reverse, transfer.report)
    return forward, reverse


def _assert_session(ctx, forward, reverse):
    """The session is named on the direction that inserts it and the one that
    strips it, and on neither LAN half; the carrier tag is under it on both.
    `forward` is None where the upload is Linux's.

    Both directions still name the physical ports. Neither a ppp device nor a
    bridge ever becomes one, which is the invariant a profile is likeliest to
    break: there are four upper devices in this path and only two ports.
    """
    expected = _session_text(ctx.session_identity)
    assert reverse["in_ppp"] == expected and reverse["out_ppp"] == "-", reverse
    assert reverse["in_vlan"] == str(WAN_VID), reverse
    assert reverse["in"] == TARGET_WAN_IF and reverse["out"] == TARGET_LAN_IF, reverse
    if forward is None:
        return
    assert forward["out_ppp"] == expected and forward["in_ppp"] == "-", forward
    assert forward["out_vlan"] == str(WAN_VID), forward
    assert forward["in"] == TARGET_LAN_IF and forward["out"] == TARGET_WAN_IF, forward


# ---- multicast -------------------------------------------------------------
#
# The oracles are copied from mcast_e2e.py rather than imported: that file
# is being changed for the routed learner, and a profile gate must not fail
# because a helper it borrowed moved. The CPU-side one is deliberately not a
# copy -- see _stream_reached_cpu.

async def mdb_reports_offload(ctx, group):
    """The `bridge mdb show` lines for a group that say a driver took it on.

    br_switchdev_mdb_complete() sets MDB_PG_FLAGS_OFFLOAD only when the
    switchdev object was handled without error, and nothing in the bridge
    consults the flag afterwards -- which is what makes it a report rather than a
    mechanism this test could be driving.
    """
    result = await command(ctx.target, ctx.session, "bridge", "mdb", "show", check=False)
    return [line for line in (result.get("stdout") or "").splitlines()
            if group in line and "offload" in line]


async def hardware_group(ctx, group):
    """The adapter's own row for a group, or None.

    `installed` means the classifier holds the key. `pending` means the
    membership is a permission the traffic has not completed yet, and the two
    must never be read as the same thing.
    """
    for row in (await ctx.state())["mcast"]:
        if row["group"] == group:
            return row
    return None


async def _stream_reached_cpu(ctx, window_s):
    """How much of a stream the DUT's CPU saw, as a software receive delta.

    mcast_e2e.py measures this with the agent's capture window, which is a
    *dmesg* window: it carries no packet summaries at all, so that oracle counts
    zero whatever happens and can never fail. The WAN port's own software
    receive counter is the honest measurement -- a hardware-replicated frame is
    matched and transmitted by the FMAN and never enqueued to the host, so this
    stays at the segment's background noise while the client counts thousands.
    """
    before = await kernel_rx_packets(ctx.target, ctx.session, TARGET_WAN_IF)
    await asyncio.sleep(window_s)
    return await kernel_rx_packets(ctx.target, ctx.session, TARGET_WAN_IF) - before


def _inject_streams(channels, seconds, pps):
    """Put one multicast UDP stream per channel on the WAN wire, interleaved.

    A layer-2 send with an explicit interface, because a multicast destination
    has no host route and a layer-3 send would leave by whichever interface the
    kernel likes. The source address is pinned for the same reason the group is:
    the classifier key is an exact (S,G), so a source scapy chose for itself
    would miss the entry and every frame would punt unreplicated.
    """
    from scapy.all import IP, UDP, Dot1Q, Ether, Raw, sendp

    frames = []
    for group, port in channels:
        octets = [int(b) for b in group.split(".")]
        mac = "01:00:5e:%02x:%02x:%02x" % (octets[1] & 0x7F, octets[2], octets[3])
        frame = Ether(dst=mac)
        if IPTV_TAGGED:
            frame = frame / Dot1Q(vlan=IPTV_VID)
        frames.append(frame / IP(src=orchestrator_source(), dst=group, ttl=STREAM_TTL)
                      / UDP(sport=port, dport=port) / Raw(b"x" * 512))
    sendp(frames * int(seconds * pps), iface=INJECT_IF,
          inter=1.0 / (pps * len(frames)), verbose=False)


def _watch_script(client, channels, seconds, drop_after=None):
    """A consumer that joins every channel and counts what arrives on each.

    Real sockets rather than `ip maddr`, because the socket option is what
    decides which report the kernel emits and the report is what the bridge
    learns from. IP_ADD_SOURCE_MEMBERSHIP names the source, so the MDB gets an
    (S,G) the membership alone can key -- the shape a set-top box uses.

    Each channel has its own port. Two sockets bound to one port both receive
    every group, because per-socket filtering is by source and not by group, and
    the two counts could not then be told apart.

    With `drop_after` the second channel's membership is dropped that many
    seconds in and the counts split in two around it, which is a channel change
    with the first channel still playing.
    """
    return f"""
import json, select, socket, time
plan = {channels!r}
source = socket.inet_aton({orchestrator_source()!r})
local = socket.inet_aton({client['ip']!r})
socks, by_fd, mreqs = [], {{}}, []
for index, (group, port) in enumerate(plan):
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind(('', port))
    mreq = socket.inet_aton(group) + local + source
    s.setsockopt(socket.IPPROTO_IP, 39, mreq)     # IP_ADD_SOURCE_MEMBERSHIP
    s.setblocking(False)
    socks.append(s); mreqs.append(mreq); by_fd[s.fileno()] = index
print('JOINED', flush=True)
counts = [[0, 0] for _ in plan]
poller = select.poll()
for s in socks:
    poller.register(s, select.POLLIN)
start = time.monotonic()
drop_at, dropped = {drop_after!r}, False
while time.monotonic() - start < {seconds}:
    phase = 1 if dropped else 0
    for fd, _event in poller.poll(50):
        index = by_fd[fd]
        try:
            while True:
                socks[index].recv(2048)
                counts[index][phase] += 1
        except BlockingIOError:
            pass
    if drop_at is not None and not dropped and time.monotonic() - start >= drop_at:
        # IP_DROP_SOURCE_MEMBERSHIP. The socket stays bound, so a group the
        # bridge kept flooding would still be counted -- which is the point.
        socks[-1].setsockopt(socket.IPPROTO_IP, 40, mreqs[-1])
        dropped = True
for s in socks:
    s.close()
print(json.dumps({{'counts': counts}}))
"""


async def _watch(ctx, channels, seconds, label, drop_after=None):
    """Join, run the streams, and answer with everything the oracles need."""
    client = BY_NAME["iptv"]
    # The consumer times its own membership change from its own start, so the
    # offset it is given puts the change in the middle of the stream rather
    # than in the pre-roll.
    joiner = asyncio.create_task(_client_python(
        ctx, client, _watch_script(client, channels, seconds + PRE_ROLL + 2.0,
                                   None if drop_after is None
                                   else PRE_ROLL + drop_after),
        label=label, timeout=seconds + 60))
    # Let the reports reach the bridge and the learner act on them before the
    # streams start: an entry that is still pending forwards in software, and
    # the measurement would describe the bring-up rather than the steady state.
    await asyncio.sleep(PRE_ROLL)
    cpu = asyncio.create_task(_stream_reached_cpu(ctx, seconds))
    try:
        await asyncio.to_thread(_inject_streams, channels, seconds, STREAM_PPS)
    except BaseException:
        # Both of these outlive a failed injection, and an abandoned task makes
        # "Task was destroyed but it is pending" the last thing in the log
        # instead of the reason the case failed. Finish the joiner before
        # fixture teardown changes its network.
        cpu.cancel()
        await asyncio.gather(joiner, cpu, return_exceptions=True)
        raise
    result = await joiner
    assert result.rc == 0 and "JOINED" in result.stdout, result.stdout
    counts = json.loads(result.stdout.strip().splitlines()[-1])["counts"]
    observed = {"sent": int(seconds * STREAM_PPS), "cpu": await cpu, "channels": {}}
    for index, (group, _port) in enumerate(channels):
        observed["channels"][group] = {
            "before": counts[index][0], "after": counts[index][1],
            "received": counts[index][0] + counts[index][1],
            "mdb": await mdb_reports_offload(ctx, group),
            "row": await hardware_group(ctx, group)}
    return observed


def _assert_replicated(group, observed):
    """The three oracles, which deliberately mean three different things:
    accepted, installed, and matching. A case that passes all three has been
    checked at three independent points rather than three times at one."""
    channel, sent = observed["channels"][group], observed["sent"]
    # Forwarding first: without it everything below describes a broken path
    # rather than an accelerated one.
    assert channel["received"] > sent * 0.5, (group, channel, sent)
    assert channel["row"] and channel["row"]["state"] == "installed", (group, channel)
    assert channel["mdb"], (
        f"{group}: installed in hardware, but `bridge mdb show` does not report "
        f"offload. The flag is set when the adapter accepts the switchdev "
        f"object, which is strictly earlier than the install, so an entry "
        f"present without it reached hardware by some path other than the "
        f"membership it was supposed to come from")
    assert observed["cpu"] < sent * 0.05, (
        f"{group}: {observed['cpu']} of {sent} frames reached the DUT's CPU. A "
        f"hardware-replicated frame never does, so the bridge is still flooding "
        f"in software whatever the table says")


# ---- the profile -----------------------------------------------------------

def _policy():
    """The shipping policy: every eligible forwarded flow, across both physical
    ports. `devices auto` resolves to the same two; they are named so the
    profile does not depend on which other ports happen to be up."""
    return {"version": 1, "enabled": True, "devices": [TARGET_LAN_IF, TARGET_WAN_IF],
            "scope": [{}], "exclude": []}


async def _bridge_lan(ctx, stack):
    """The subscriber's LAN: one VLAN-aware bridge over the one physical port.

    The DUT's LAN address moves off the port and onto `<bridge>.<pvid>`, so a
    flow's logical egress device is a VLAN on a bridge while its physical egress
    is still the port -- which is the whole of what the shipping configuration
    asks the adapter to resolve. `vlan_default_pvid 0` first: the default of 1
    would install a PVID nothing here asked for, and the bridge resolves its FDB
    lookup inside whichever VLAN the frame ends up in.
    """
    async def dut(*argv, check=True):
        return await command(ctx.target, ctx.session, *argv, check=check)

    addresses = json.loads((await dut("ip", "-j", "-4", "addr", "show",
                                      "dev", TARGET_LAN_IF))["stdout"])[0]
    original = next(f"{a['local']}/{a['prefixlen']}" for a in addresses["addr_info"]
                    if a["family"] == "inet")
    await dut("ip", "link", "del", BRIDGE, check=False)
    # IGMPv3/MLDv2 queries: a v2 query heard on the LAN keeps a host's
    # interface in v2 mode for minutes, and later cases need v3 hosts.
    await dut("ip", "link", "add", "name", BRIDGE, "type", "bridge",
              "mcast_snooping", "1", "mcast_querier", "1",
              "mcast_igmp_version", "3", "mcast_mld_version", "2")

    # Restore the address even if deleting the bridge fails.
    stack.push(lambda: dut("ip", "addr", "replace", original, "dev", TARGET_LAN_IF))
    stack.push(lambda: dut("ip", "link", "del", BRIDGE))

    await dut("ip", "link", "set", BRIDGE, "type", "bridge", "vlan_filtering", "1",
              "vlan_default_pvid", "0")
    await dut("ip", "addr", "del", original, "dev", TARGET_LAN_IF)
    await dut("ip", "link", "set", TARGET_LAN_IF, "master", BRIDGE)
    await dut("ip", "link", "set", BRIDGE, "up")
    await dut("bridge", "vlan", "add", "dev", TARGET_LAN_IF, "vid", str(LAN_VID),
              "pvid", "untagged")
    for vid in (GUEST_VID, IPTV_VID):
        await dut("bridge", "vlan", "add", "dev", TARGET_LAN_IF, "vid", str(vid))
    for vid in (LAN_VID, GUEST_VID, IPTV_VID):
        await dut("bridge", "vlan", "add", "dev", BRIDGE, "vid", str(vid), "self")

    subscriber = await dut_vlan_subif(stack, ctx.target, ctx.session, parent=BRIDGE,
                                      vid=LAN_VID, name=f"{BRIDGE}.{LAN_VID}",
                                      ipv4=f"{LAN_GATEWAY}/24",
                                      ipv6=f"{LAN_GATEWAY6}/64")
    guest = await dut_vlan_subif(stack, ctx.target, ctx.session, parent=BRIDGE,
                                 vid=GUEST_VID, name=f"{BRIDGE}.{GUEST_VID}",
                                 ipv4=f"{GUEST_GATEWAY}/24")
    # The LAN VM keeps whatever address the box handed it, so restate the
    # original prefix on the subscriber VLAN: the bridge is transparent and the
    # station is still on the other side of it.
    await dut("ip", "addr", "add", original, "dev", subscriber)
    ctx.bridge_text = {LAN_VID: subscriber, GUEST_VID: guest}
    ctx.dut_lan_mac = (await read(ctx.target, ctx.session,
                                  f"/sys/class/net/{TARGET_LAN_IF}/address")).strip()


async def _bridge_wan_for_iptv(ctx, stack):
    """Bridge IPTV at L2, controlled through the shared UART session.

    The PPPoE carrier VLAN still receives tagged frames before the bridge's
    rx_handler. Untagged IPTV enters through the WAN port's PVID.
    """
    console = ctx.console
    iptv_dev = f"{BRIDGE}.{IPTV_VID}"
    undo = [["ip", "link", "del", iptv_dev],
            ["ip", "link", "set", TARGET_WAN_IF, "nomaster"]]
    for argv in reversed(undo):
        stack.push(lambda argv=argv: console_command(console, *argv, timeout=30))
    steps = [["ip", "link", "set", TARGET_WAN_IF, "master", BRIDGE],
             ["bridge", "vlan", "add", "dev", TARGET_WAN_IF, "vid", str(IPTV_VID),
              *([] if IPTV_TAGGED else ["pvid", "untagged"])],
             ["bridge", "vlan", "add", "dev", BRIDGE, "vid", str(IPTV_VID), "self"],
             ["ip", "link", "add", "link", BRIDGE, "name", iptv_dev, "type", "vlan",
              "id", str(IPTV_VID)],
             ["ip", "link", "set", iptv_dev, "up"],
             ["ip", "addr", "add", f"{IPTV_GATEWAY}/24", "dev", iptv_dev]]
    for argv in steps:
        await console_command(console, *argv, timeout=30)
    ctx.bridge_text[IPTV_VID] = iptv_dev


async def _session_address(ctx):
    """The IPv4 address this session actually negotiated.

    Not the constant. `pppoe-server -R` names the *start* of a pool, so the
    address a dial receives depends on how many sessions the concentrator has
    already handed out -- a lingering one from an earlier run moves the next
    dial along by one. Asserting the constant made this profile pass while the
    bench was fresh and fail once it had dialled a few times, which reads as
    flakiness and is really the test knowing something it should have asked.
    """
    addresses = json.loads((await command(ctx.target, ctx.session, "ip", "-j",
                                          "-4", "addr", "show",
                                          "dev", ctx.ppp_if))["stdout"])
    return next(a["local"] for a in addresses[0]["addr_info"]
                if a["family"] == "inet")


async def _session_ipv6(ctx, cleanup):
    """Global IPv6 on both ends of the session, and a subscriber VLAN that can
    reach it.

    IPV6CP negotiates interface identifiers and forms link-local addresses; a
    global address it does not assign, so each end of the point-to-point link
    gets one out of a prefix that belongs to neither segment. The subscriber
    VLAN's own /64 is routed down the session from the far end, which is what an
    ISP does with a delegated prefix.
    """
    async def dut(*argv, check=True):
        return await command(ctx.target, ctx.session, *argv, check=check)

    # The subscriber VLAN tells its hosts the session's MTU, as a PPPoE LAN
    # should, sparing them a Packet Too Big round trip. Admission does not
    # read it (see test_mtu_bound), so the IPv6 UDP upload stays in Linux all
    # the same. The VLAN is this fixture's and goes with it, so nothing is
    # restored.
    subscriber_mtu = f"/proc/sys/net/ipv6/conf/{BRIDGE}.{LAN_VID}/mtu"
    assert (await ctx.target.fs_write(ctx.session, subscriber_mtu,
                                      str(SESSION_MTU)))["errno"] == 0

    addresses = json.loads((await command(ctx.wan, ctx.session, "ip", "-j", "-4",
                                          "addr"))["stdout"])
    ctx.server_ppp_if = next(
        i["ifname"] for i in addresses
        if any(a.get("local") == INNER_LOCAL for a in i["addr_info"]))
    await dut("ip", "-6", "addr", "del", f"{INNER_REMOTE6}/64", "dev", ctx.ppp_if,
              check=False)
    await dut("ip", "-6", "addr", "add", f"{INNER_REMOTE6}/64", "dev", ctx.ppp_if,
              "nodad")
    cleanup.append((ctx.target, ["ip", "-6", "addr", "del", f"{INNER_REMOTE6}/64",
                                 "dev", ctx.ppp_if]))
    await command(ctx.wan, ctx.session, "ip", "-6", "addr", "del",
                  f"{INNER_LOCAL6}/64", "dev", ctx.server_ppp_if, check=False)
    await command(ctx.wan, ctx.session, "ip", "-6", "addr", "add",
                  f"{INNER_LOCAL6}/64", "dev", ctx.server_ppp_if, "nodad")
    cleanup.append((ctx.wan, ["ip", "-6", "addr", "del", f"{INNER_LOCAL6}/64",
                              "dev", ctx.server_ppp_if]))
    # The concentrator has no route to the subscriber's prefix at all. Point to
    # point, so no next hop: the session is the only way there.
    await command(ctx.wan, ctx.session, "ip", "-6", "route", "replace", LAN_PREFIX6,
                  "dev", ctx.server_ppp_if)
    cleanup.append((ctx.wan, ["ip", "-6", "route", "del", LAN_PREFIX6,
                              "dev", ctx.server_ppp_if]))


async def _reachable(ctx, client, peer, attempts=25):
    """Prove the path before measuring anything on it.

    The first packet after a bring-up loses a race with the peer route and with
    the concentrator's own ARP, and the measurement helpers treat one lost
    datagram as the failure it would otherwise be. Absorb the bring-up here
    rather than in whichever case happens to run first.
    """
    source = _source_address(client, peer)
    argv = (["ping", "-6"] if ":" in peer else ["ping"]) + \
        ["-c", "3", "-W", "2", "-I", source, peer]
    script = (f"import subprocess\n"
              f"print('rc=%d' % subprocess.run({argv!r}, capture_output=True).returncode)\n")
    for _ in range(attempts):
        probe = await _client_python(ctx, client, script,
                                     label="profile_isp_reachable", timeout=30)
        if "rc=0" in probe.stdout:
            return
        await asyncio.sleep(1.0)
    dut = await command(ctx.target, ctx.session, "ip", "-br", "addr", check=False)
    route = await command(ctx.target, ctx.session, "ip", "route", "get", peer,
                          check=False)
    pytest.fail(f"{client['name']} could not reach {peer} after {attempts} attempts: "
                f"{probe.stdout!r}\n  DUT addresses: {dut.get('stdout', dut)!r}\n"
                f"  DUT route:     {route.get('stdout', route)!r}")


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def isp(target_agent, lan, request, dmesg_allowlist):
    """The whole ISP profile, built once for the file.

    Module-scoped deliberately. The build is a PPPoE dial, a bridge that takes
    both physical ports, three namespaced clients and a QoS tree; paying for
    that per case would cost more than the cases do, and -- more to the point --
    the lifecycle cases are only meaningful against a profile that has been
    carrying traffic rather than one built a moment ago.

    Its own aiohttp session, because conftest's is function-scoped and bound to
    the per-test event loop. The LAN guest-agent client is session-scoped.

    Order matters twice. Every device the session and the bridge stand on exists
    before the offload policy is applied, because adding an upper to a bound
    port is a configuration change the adapter answers with full invalidation.
    And the WAN port joins the bridge before the policy, for the same reason.
    """
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30)) as session, capture_window(
            target_agent, session, request.node.nodeid, dmesg_allowlist, name="profile-kernel"):
        ctx = Profile()
        ctx.target, ctx.session, ctx.lan = target_agent, session, lan
        ctx.wan = Agent("wan", f"http://{ORCH_IPV4}:9110")
        ctx.sequence, ctx.recovery_console, ctx.proto = 1, None, "udp"
        ctx.echoes = {}
        stack = TopologyStack()
        cleanup, endpoints = [], []
        server = None
        console = Console.target(log_path=str(artifact_dir(request.node.nodeid) / "profile-isp-uart.log"))
        try:
            initial = await ctx.state()
            ctx.baseline_errors = initial["errors"]
            await asyncio.to_thread(console.login, "root", None)
            ctx.console = console
            # This profile owns the policy, so the boot daemon's catch-all is
            # stopped first and reapplied from here. The init script drains the
            # hardware to an unbound state.
            await console_command(console, "/etc/init.d/ask-flowtable", "stop",
                                  check=False, timeout=45)
            # A known starting point, and the first thing that would be wrong if
            # the boot daemon had not let go: everything this file measures is a
            # delta against an unbound adapter.
            drained = await ctx.wait(lambda s: not s["bindings"] and not s["entries"],
                                     timeout=30)
            assert drained["invalidated"] == drained["fatal"] == 0, drained
            # Quiet the kernel's own console before driving it: every console
            # command is framed by a marker the reader matches on, and a printk
            # landing mid-marker truncates it. Restored in teardown.
            printk = (await read(ctx.target, ctx.session,
                                 "/proc/sys/kernel/printk")).split()
            await command(ctx.target, ctx.session, "sysctl", "-w",
                          "kernel.printk=1 4 1 7")
            # Restored by hand at the very end of teardown rather than from the
            # cleanup list: the last thing teardown does is drive the console
            # to take the WAN port back out of the bridge, and that is exactly
            # the sequence a printk landing mid-marker breaks.
            ctx.printk = " ".join(printk[:4])
            await command(ctx.target, ctx.session, "modprobe", "xt_tcpudp")
            old_acct = (await read(ctx.target, ctx.session,
                                   "/proc/sys/net/netfilter/nf_conntrack_acct")).strip()
            await command(ctx.target, ctx.session, "sysctl", "-w",
                          "net.netfilter.nf_conntrack_acct=1")
            cleanup.append((ctx.target, ["sysctl", "-w",
                                         f"net.netfilter.nf_conntrack_acct={old_acct}"]))
            # The image already forwards both families; restated because the
            # profile is not worth debugging as a routing failure if it ever
            # stops doing so, and restored either way.
            for key in ("net.ipv4.ip_forward", "net.ipv6.conf.all.forwarding"):
                previous = (await command(ctx.target, ctx.session, "sysctl", "-n",
                                          key))["stdout"].strip()
                cleanup.append((ctx.target, ["sysctl", "-w", f"{key}={previous}"]))
                await command(ctx.target, ctx.session, "sysctl", "-w", f"{key}=1")
            addresses = json.loads((await command(ctx.wan, ctx.session, "ip", "-j",
                                                  "-4", "addr"))["stdout"])
            ctx.wan_if = next(i["ifname"] for i in addresses
                              if any(a.get("local") == orchestrator_source()
                                     for a in i["addr_info"]))

            # ---- the uplink: a session over the carrier tag ----
            await console_command(console, "modprobe", "pppoe")
            probe = await console_command(console, "sh", "-c", "command -v pppd",
                                          check=False)
            if probe["rc"] != 0:
                pytest.skip("pppd is not in the DUT image; add `ppp` to IMAGE_INSTALL")
            ctx.ppp_lower = await dut_vlan_subif(stack, ctx.target, ctx.session,
                                                 parent=TARGET_WAN_IF, vid=WAN_VID)
            server = _server_start(ipv6=True)
            # ~0.5s to bind. A bad interface or a port already in use exits
            # fast, and catching it here beats a bring-up timeout later.
            await asyncio.sleep(0.5)
            if server.poll() is not None:
                _, err = server.communicate(timeout=2)
                pytest.fail(f"the access concentrator exited rc={server.returncode}: "
                            f"{err.decode('utf-8', 'replace')[:1000]!r}")
            ctx.ppp_if, ctx.ppp_pid = await _dial(console, ctx.ppp_lower, ipv6=True)
            stack.push(lambda: _hangup(console))
            ctx.session_identity = await _session_identity(ctx)
            ctx.ppp_local = await _session_address(ctx)

            # ---- the LAN, and the IPTV VLAN bridged in from the WAN port ----
            await _bridge_lan(ctx, stack)
            await _bridge_wan_for_iptv(ctx, stack)
            await _build_clients(ctx)
            stack.push(lambda: _drop_clients(ctx))
            # Pinned in both families, so admission never races ARP or ND: a
            # direction offered before its neighbour resolves is declined, and
            # nothing re-offers it until the next packet.
            for client in CLIENTS:
                dev = ctx.bridge_text[client["vid"] or LAN_VID]
                await command(ctx.target, ctx.session, "ip", "neigh", "replace",
                              client["ip"], "lladdr", client["mac"], "nud",
                              "permanent", "dev", dev)
                cleanup.append((ctx.target, ["ip", "neigh", "del", client["ip"],
                                             "dev", dev]))
                if client["ip6"]:
                    await command(ctx.target, ctx.session, "ip", "-6", "neigh",
                                  "replace", client["ip6"], "lladdr", client["mac"],
                                  "nud", "permanent", "dev", dev)
                    cleanup.append((ctx.target, ["ip", "-6", "neigh", "del",
                                                 client["ip6"], "dev", dev]))

            # ---- translation: the subscriber's own address never leaves ----
            for subnet in (LAN_SUBNET, GUEST_SUBNET):
                nat = ["POSTROUTING", "-s", subnet, "-o", ctx.ppp_if, "-j", "MASQUERADE"]
                await command(ctx.target, ctx.session, "iptables", "-t", "nat", "-I",
                              *nat)
                cleanup.append((ctx.target, ["iptables", "-t", "nat", "-D", *nat]))

            await _session_ipv6(ctx, cleanup)
            await _reachable(ctx, BY_NAME["main"], INNER_LOCAL)
            await _reachable(ctx, BY_NAME["guest"], INNER_LOCAL)
            await _reachable(ctx, BY_NAME["main"], INNER_LOCAL6)

            loop = asyncio.get_running_loop()
            for port in (PORT_MAIN, PORT_GUEST, PORT_VOICE, PORT_BULK):
                transport, echo = await loop.create_datagram_endpoint(
                    SourceEcho, local_addr=(INNER_LOCAL, port))
                endpoints.append(transport)
                ctx.echoes[port] = echo
            transport, echo = await loop.create_datagram_endpoint(
                SourceEcho, local_addr=(INNER_LOCAL6, PORT_V6), family=socket.AF_INET6)
            endpoints.append(transport)
            ctx.echoes[PORT_V6] = echo

            # ---- the offload policy, last: every device it binds exists ----
            await apply(console, _policy(), r=ctx)
            await ctx.wait(lambda s: s["bindings"] == 2)
            ctx.record("isp-fixture", {
                "session": _session_text(ctx.session_identity), "ppp": ctx.ppp_if,
                "lower": ctx.ppp_lower, "bridge": ctx.bridge_text,
                "tagged_iptv": IPTV_TAGGED,
                "clients": CLIENTS, "initial": initial})
            yield ctx
        finally:
            for transport in endpoints:
                transport.close()
            failures = []

            async def undo(step, label, timeout=60):
                """Every undo runs, whatever the one before it did.

                A teardown step that raises takes the whole rest of the cleanup
                with it, and the step that matters most here -- taking the WAN
                port back out of the bridge, which is what makes the agent
                reachable again -- is near the end. `check=False` is not enough
                on its own: a console call after a failed login raises on its
                output marker before it ever looks at `check`.
                """
                try:
                    async with asyncio.timeout(timeout):
                        await step()
                except (Exception, pytest.fail.Exception) as error:
                    failures.append(f"{label}: {error}")

            async def target(*argv, check=True):
                return await command(ctx.target, ctx.session, *argv, check=check)

            await undo(lambda: stop(console), "ask-flowtable stop")
            async def remove_table(table):
                await target("nft", "delete", "table", "inet", table, check=False)
                remaining = await target("nft", "-j", "list", "tables")
                assert not any(entry.get("table", {}).get("name") == table
                               for entry in json.loads(remaining["stdout"])["nftables"]), remaining

            for table in (QOS_TABLE, NAT_TABLE):
                await undo(lambda t=table: remove_table(t), f"nft {table}")
            await undo(lambda: remove_qdisc(console, TARGET_WAN_IF, "root", "htb"), "qdisc")
            await undo(lambda: target("conntrack", "-F"), "conntrack")
            for agent, argv in reversed(cleanup):
                await undo(lambda a=agent, v=argv: command(a, ctx.session, *v), " ".join(argv))
            await undo(lambda: stack.teardown("profile-isp"), "topology", timeout=None)
            if server:
                _server_stop(server)
            try:
                await undo(lambda: console_command(console, "rm", "-f", CONFIG), "config")
                # Last, because everything above drives the console.
                if getattr(ctx, "printk", None):
                    await undo(lambda: target("sysctl", "-w",
                                              f"kernel.printk={ctx.printk}"), "printk")
            finally:
                console.close()
            assert not failures, failures


# ---- traffic: the profile carrying what it exists to carry -----------------

async def test_subscriber_reaches_the_internet(isp, splat_window):
    """The ordinary case, and the one that spends every encapsulation slot.

    A subscriber's frame arrives untagged on a port that is untagged in the
    bridge's subscriber VLAN, is translated, and leaves inside a PPPoE session
    that itself rides the carrier tag. A bridge on one side and a session over a
    tag on the other, on one connection: the row has to name all three, and
    nothing else.

    A UDP upload into the session is Linux's (see _upload_in_linux), so the
    UDP connection proves the download and its refusal; the upload the row
    describes is a TCP connection's.
    """
    ctx = isp
    _, download = await _accounted(ctx, BY_NAME["main"], peer=INNER_LOCAL,
                                   dport=PORT_MAIN, sport=PORT_MAIN, label="subscriber")
    _assert_session(ctx, None, download)
    assert download["out_br"] == ctx.bridge_text[LAN_VID] and download["in_br"] == "-", download
    assert download["out_vlan"] == "-", download
    forward, reverse = await _tcp_accounted(ctx, BY_NAME["main"], peer=INNER_LOCAL,
                                            dport=PORT_MAIN, label="subscriber-tcp")
    _assert_session(ctx, forward, reverse)
    # The bridge is named on the direction that crosses it and only there, and
    # the subscriber's wire carries no tag although a VLAN device is in the
    # path -- the port is untagged for the PVID.
    assert forward["in_br"] == ctx.bridge_text[LAN_VID] and forward["out_br"] == "-", \
        forward
    assert reverse["out_br"] == ctx.bridge_text[LAN_VID] and reverse["in_br"] == "-", \
        reverse
    assert forward["in_vlan"] == "-" and reverse["out_vlan"] == "-", (forward, reverse)
    # Translated to the session's own address, which is what an ISP sees. The
    # port is masquerade's to choose, so only the address is asserted.
    assert forward["new_src"].startswith(ctx.ppp_local + ":"), (forward, ctx.ppp_local)
    # The forward direction leaves by the session, so it carries the session's
    # MTU. Nothing here set that; the eight bytes of overhead are already in it.
    assert int(forward["mtu"]) == SESSION_MTU, forward


async def test_guest_vlan_is_tagged_on_the_wire(isp, splat_window):
    """The guest network, which differs from the subscriber's in one bridge
    membership and must differ on the wire in exactly one tag.

    Same bridge, same port, same session. A derivation that stopped at the
    netdevs would push the subscriber VLAN's tag here too, or none at all, and
    either way the guest receives frames it cannot parse while every counter
    looks healthy. The UDP download pushes the guest tag; the upload that pops
    it is a TCP connection's, a UDP one being Linux's.
    """
    ctx = isp
    _, download = await _accounted(ctx, BY_NAME["guest"], peer=INNER_LOCAL,
                                   dport=PORT_GUEST, sport=PORT_GUEST, label="guest")
    _assert_session(ctx, None, download)
    assert download["out_br"] == ctx.bridge_text[GUEST_VID], download
    assert download["out_vlan"] == str(GUEST_VID), download
    forward, reverse = await _tcp_accounted(ctx, BY_NAME["guest"], peer=INNER_LOCAL,
                                            dport=PORT_GUEST, label="guest-tcp")
    _assert_session(ctx, forward, reverse)
    assert forward["in_br"] == ctx.bridge_text[GUEST_VID], forward
    assert forward["in_vlan"] == str(GUEST_VID), forward
    assert reverse["out_vlan"] == str(GUEST_VID), reverse
    assert forward["new_src"].startswith(ctx.ppp_local + ":"), (forward, ctx.ppp_local)


async def test_ipv6_rides_the_session_natively(isp, splat_window):
    """IPv6 through the session, untranslated, from a bridged subscriber VLAN.

    The v4 half of this profile is translated and the v6 half is not, which is
    what a dual-stack line is. Both cross the same session, so a complete TCP
    transfer here also says the microcode chose the right PPP protocol id for
    an IPv6 frame it inserted the header onto: a wrong choice is a header the
    concentrator discards, which is silent loss rather than a refusal. The UDP
    upload is Linux's (see _upload_in_linux), so UDP proves the download.
    """
    ctx = isp
    _, download = await _accounted(ctx, BY_NAME["main"], peer=INNER_LOCAL6,
                                   dport=PORT_V6, sport=PORT_V6, label="ipv6")
    _assert_session(ctx, None, download)
    assert download["family"] == "6" and download["out_br"] == ctx.bridge_text[LAN_VID], download
    # What the far end observed: the download's replies answered every
    # datagram Linux sent up the session.
    assert ctx.echoes[PORT_V6].sources == {(LAN_CLIENT6, PORT_V6)}, \
        ctx.echoes[PORT_V6].sources
    forward, reverse = await _tcp_accounted(ctx, BY_NAME["main"], peer=INNER_LOCAL6,
                                            dport=PORT_V6, label="ipv6-tcp")
    _assert_session(ctx, forward, reverse)
    assert forward["family"] == reverse["family"] == "6", (forward, reverse)
    assert forward["in_br"] == ctx.bridge_text[LAN_VID], forward
    assert int(forward["mtu"]) == SESSION_MTU, forward


async def test_iptv_is_replicated_by_the_hardware(isp, splat_window):
    """Two IPTV channels, bridged from the WAN VLAN to the set-top box.

    Three oracles, meaning three different things, which is why all three are
    needed: `bridge mdb show` says the adapter accepted the group, the adapter's
    own row says it installed it, and the DUT's software receive counter says
    the frames never reached the CPU. Arrival alone proves nothing -- the Linux
    bridge floods multicast perfectly well in software.
    """
    ctx = isp
    for group in GROUPS:
        assert not await hardware_group(ctx, group), (
            f"{group} was already in the hardware table before this test joined "
            f"it; a stale entry would satisfy every oracle here")
    observed = await _watch(ctx, CHANNELS, STREAM_S, "profile_isp_iptv")
    ctx.record("isp-iptv", observed)
    for group in GROUPS:
        _assert_replicated(group, observed)
        row = observed["channels"][group]["row"]
        assert row["br"] == BRIDGE and row["vid"] == str(IPTV_VID), row
        assert row["in"] == TARGET_WAN_IF, row
        assert TARGET_LAN_IF in row["ports"], row
        assert int(row["packets"]) > 0, (
            f"{group}: installed, but the classifier matched nothing -- the entry "
            f"is in the table and the stream is going past it")


async def test_qos_marks_pick_the_class(isp, splat_window):
    """Two flows, one marked, and the queue each one lands in.

    The tree is the three levels the hardware has: the port is the root qdisc, a
    class under it is a CEETM channel with a rate and a ceiling, and the leaves
    under that are class queues. A conntrack mark set at forward/mangle -- early
    enough in the same traversal that admission sees it -- names the queue for
    the voice flow; the bulk flow is unmarked and has to land on the configured
    default. The class the row reports is the value the hardware entry was
    given, so a row carrying the right class is the proof the mark arrived.

    The leaf counters come from `ethtool -S`, not from `tc -s class show`: an
    accelerated frame never passes through a leaf's software qdisc, so tc has
    nothing to report and `cdx_htb` refuses TCA statistics outright rather than
    return a zero that reads like an answer.

    Both flows are TCP. The queues are the WAN port's, so what they count is
    the upload, and a UDP upload into the session is Linux's (see
    _upload_in_linux): its frames would reach the port through the software
    qdisc and land on the default class whatever their mark.
    """
    ctx = isp
    mask = int((await read(ctx.target, ctx.session,
                           "/sys/module/ask_flowtable/parameters/qos_mark_mask")).strip())
    if not mask:
        pytest.skip("classification is off in this boot; reboot with "
                    "ask_flowtable.qos_mark_mask=0xf0 for the QoS case")
    default_class = int((await read(
        ctx.target, ctx.session,
        "/sys/module/ask_flowtable/parameters/qos_default_class")).strip())
    if default_class & 0xf in (VOICE_CQ, BULK_CQ):
        pytest.skip(f"qos_default_class names class queue {default_class & 0xf}, "
                    f"which is one of the two this case marks for; traffic that "
                    f"missed its mark would be indistinguishable from traffic "
                    f"that carried it")
    shift = (mask & -mask).bit_length() - 1
    voice_mark = VOICE_CQ << shift
    # The bulk flow is marked too, rather than left to the default class.
    #
    # What this case is about is a mark picking a class, and two marks name two
    # leaves unambiguously. Leaving bulk unmarked instead made the assertion
    # depend on where the *default* lands, and that is a different question
    # with a different answer: the default is a class-queue nibble in the
    # adapter's numbering, where 0 is the lowest strict priority, while a tc
    # leaf of `prio N` holds queue NUM_PQS-1-N in the opposite direction. The
    # two scales meet nowhere this test can assert on, and the run that found
    # it showed the default's traffic on the voice leaf's counter.
    bulk_mark = BULK_CQ << shift

    async def dut(*argv, check=True):
        return await command(ctx.target, ctx.session, *argv, check=check)

    async def tc(*argv, check=True):
        """`tc` is not in the agent's argv allowlist -- deliberately, since
        argv[0] is the whole gate -- so the qdisc tree is built on the console
        the fixture already holds open."""
        return await console_command(ctx.console, "tc", *argv, check=check, timeout=30)

    async def leaves():
        text = (await dut("ethtool", "-S", TARGET_WAN_IF))["stdout"]
        return {int(slot): int(value) for slot, value in re.findall(
            r"^\s*ceetm dequeued frames \[leaf (\d+)\]:\s*(\d+)", text, re.M)}

    await tc("qdisc", "del", "dev", TARGET_WAN_IF, "root", check=False)
    await tc("qdisc", "add", "dev", TARGET_WAN_IF, "root", "handle", "1:",
             "htb", "offload")
    try:
        # One channel under the root, shaped, with two class queues on it. The
        # leaf created first takes the first Tx queue slot, which is the slot
        # ethtool reports under; TC_HTB_LEAF_TO_INNER hands the parent's slot to
        # its first child rather than allocating a new one.
        await tc("class", "add", "dev", TARGET_WAN_IF, "parent", "1:",
                 "classid", "1:10", "htb", "rate", CHANNEL_RATE, "ceil", CHANNEL_CEIL)
        # Each leaf carries a rate because `sch_htb` requires one of every
        # class and rejects the add outright without it; only `prio` is what
        # this profile is actually asserting on, and it is what selects the
        # strict-priority class queue the leaf maps to.
        await tc("class", "add", "dev", TARGET_WAN_IF, "parent", "1:10",
                 "classid", "1:100", "htb", "rate", CHANNEL_RATE,
                 "ceil", CHANNEL_CEIL, "prio", str(VOICE_PRIO))
        await tc("class", "add", "dev", TARGET_WAN_IF, "parent", "1:10",
                 "classid", "1:101", "htb", "rate", CHANNEL_RATE,
                 "ceil", CHANNEL_CEIL, "prio", str(BULK_PRIO))
        tree = (await tc("-s", "class", "show", "dev", TARGET_WAN_IF))["stdout"]
        for classid in ("1:10", "1:100", "1:101"):
            assert classid in tree, (classid, tree)
        await dut("nft", f'''table inet {QOS_TABLE} {{
 chain mangle {{ type filter hook forward priority -150; policy accept;
 tcp dport {PORT_VOICE} ct mark set {voice_mark:#x}
 tcp sport {PORT_VOICE} ct mark set {voice_mark:#x}
 tcp dport {PORT_BULK} ct mark set {bulk_mark:#x}
 tcp sport {PORT_BULK} ct mark set {bulk_mark:#x}
 }}
}}''')
        # The mark is sampled at admission, so a connection that predates the
        # rule would carry the old class for the rest of its life.
        await dut("conntrack", "-F", check=False)

        before = await leaves()
        voice_fwd, voice_rev = await _tcp_accounted(ctx, BY_NAME["main"], peer=INNER_LOCAL,
                                                    dport=PORT_VOICE, label="voice")
        voiced = await leaves()
        bulk_fwd, bulk_rev = await _tcp_accounted(ctx, BY_NAME["main"], peer=INNER_LOCAL,
                                                  dport=PORT_BULK, label="bulk")
        bulked = await leaves()

        for row in (voice_fwd, voice_rev):
            assert int(row["qos"], 16) == VOICE_CQ, (row, voice_mark, mask)
        for row in (bulk_fwd, bulk_rev):
            assert int(row["qos"], 16) == BULK_CQ, (row, bulk_mark, mask)
        _assert_session(ctx, voice_fwd, voice_rev)

        # The voice burst left by exactly one leaf, and it is the one created
        # first: the prio 0 class, which holds the queue the mark named.
        moved = {slot: voiced[slot] - before.get(slot, 0) for slot in voiced}
        assert moved.get(0, 0) >= 64, (moved, before, voiced)
        assert all(value == 0 for slot, value in moved.items() if slot), moved
        # And the bulk burst left by the other leaf, and only that one. Two
        # marks, two leaves: this is the discrimination the case exists for,
        # and it fails if the class word reaches the hardware for one mark and
        # not the other, or if both land in the same queue.
        after_bulk = {slot: bulked[slot] - voiced[slot] for slot in bulked}
        assert after_bulk.get(1, 0) >= 64, (after_bulk, voiced, bulked)
        # Local traffic, including the session's LCP, uses class queue 7
        # (leaf 0). Queues outside the voice and bulk leaves must stay idle.
        for slot, value in after_bulk.items():
            if slot not in (0, 1):
                assert value == 0, (slot, after_bulk)
        ctx.record("isp-qos", {"mask": mask, "voice_mark": voice_mark,
                               "default_class": default_class, "tree": tree,
                               "leaves_before": before, "after_voice": voiced,
                               "after_bulk": bulked})
    finally:
        await dut("nft", "delete", "table", "inet", QOS_TABLE, check=False)
        await tc("qdisc", "del", "dev", TARGET_WAN_IF, "root", check=False)
        await dut("conntrack", "-F", check=False)


async def test_port_forward_reaches_the_subscriber(isp, splat_window):
    """One port opened from the WAN side, through the session, to a LAN client.

    The direction under test is the one a session makes hardest: it arrives
    inside the PPPoE header, is translated, and leaves across the bridge.
    Netfilter describes an ingress session with nothing at all -- no pop action,
    no dissector key -- so this rule is byte-for-byte the rule an unencapsulated
    flow produces, and is the half likelier to be refused without saying so.

    The subscriber's replies are an upload into the session. Over UDP that is
    Linux's (see _upload_in_linux), so the UDP knock proves the inbound half
    and the refusal of the reply, and a TCP connection to the same port proves
    both halves, the reply translated back in front of the insert.
    """
    ctx = isp
    client = BY_NAME["main"]
    receiver = f"/tmp/ask_profile_isp_forward_{os.getpid()}.py"
    listener = f'''
import socket, threading
address = ({client['ip']!r}, {PORT_PUBLIC})
def serve(conn):
    with conn:
        while True:
            data = conn.recv(65536)
            if not data:
                break
            conn.sendall(data)
def stream():
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind(address)
    s.listen()
    while True:
        conn, _ = s.accept()
        threading.Thread(target=serve, args=(conn,), daemon=True).start()
threading.Thread(target=stream, daemon=True).start()
s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
s.settimeout(120)
s.bind(address)
while True:
    data, peer = s.recvfrom(2048)
    s.sendto(data, peer)
'''
    await command(ctx.target, ctx.session, "nft", f'''table inet {NAT_TABLE} {{
 chain prerouting {{ type nat hook prerouting priority -110; policy accept;
 iif {ctx.ppp_if} ip daddr {ctx.ppp_local} udp dport {PORT_PUBLIC} dnat ip to {client['ip']}:{PORT_PUBLIC}
 iif {ctx.ppp_if} ip daddr {ctx.ppp_local} tcp dport {PORT_PUBLIC} dnat ip to {client['ip']}:{PORT_PUBLIC}
 }}
}}''')
    staged = (f"import pathlib, subprocess\n"
              f"pathlib.Path({receiver!r}).write_text({listener!r})\n"
              f"with open({receiver + '.log'!r}, 'wb') as log:\n"
              f"    subprocess.Popen(['python3', {receiver!r}], stdin=subprocess.DEVNULL,\n"
              f"                     stdout=log, stderr=log, start_new_session=True)\n"
              "print('RECEIVER-UP')\n")

    # The address the session negotiated, not the pool's first: the
    # concentrator hands out the next one on every dial, so a constant here
    # aims the knock and the DNAT rule at an address the DUT may not hold.
    public = ctx.ppp_local

    def _knock(count):
        """The orchestrator is the far end here, so this runs in this process.
        Off the event loop, because the socket blocks and the echo endpoints of
        every other case are on that loop."""
        echoed = 0
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.bind((INNER_LOCAL, PORT_PUBLIC))
        sock.settimeout(2)
        try:
            for index in range(count):
                sock.sendto(b"ASK-profile-forward-%04d" % index,
                            (public, PORT_PUBLIC))
                try:
                    sock.recv(2048)
                    echoed += 1
                except OSError:
                    pass
        finally:
            sock.close()
        return echoed

    def _stream(seconds):
        """The same knock over TCP: a connection echoing 4 KiB blocks for
        `seconds`, from a port the kernel picks."""
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(20)
        block, blocks = bytes(range(256)) * 16, 0
        try:
            sock.bind((INNER_LOCAL, 0))
            sock.connect((public, PORT_PUBLIC))
            port = sock.getsockname()[1]
            deadline = time.monotonic() + seconds
            while time.monotonic() < deadline:
                sock.sendall(block)
                remaining = len(block)
                while remaining:
                    chunk = sock.recv(remaining)
                    assert chunk, "the subscriber closed mid-transfer"
                    remaining -= len(chunk)
                blocks += 1
                time.sleep(0.002)
        finally:
            sock.close()
        return {"port": port, "blocks": blocks}

    def _tcp_rows(flows):
        inbound = [f for f in flows if f["proto"] == "6"
                   and f["src"].startswith(INNER_LOCAL + ":")
                   and f["dst"] == f"{ctx.ppp_local}:{PORT_PUBLIC}"]
        reply = [f for f in flows if f["proto"] == "6"
                 and f["src"] == f"{client['ip']}:{PORT_PUBLIC}"
                 and f["new_src"] == f"{ctx.ppp_local}:{PORT_PUBLIC}"]
        if (len(inbound) == len(reply) == 1 and int(inbound[0]["packets"]) > 100
                and int(reply[0]["packets"]) > 100):
            return inbound[0], reply[0]
        return None

    try:
        result = await _client_python(ctx, client, staged,
                                      label="profile_isp_forward_receiver", timeout=30)
        assert "RECEIVER-UP" in result.stdout, result.stdout
        await asyncio.sleep(1.0)
        initial = await ctx.state()
        sent = 64
        echoed = await asyncio.to_thread(_knock, sent)
        assert echoed >= sent - 4, (echoed, sent)
        state = await ctx.state()
        flows = state["flows"]
        forward = _direction(flows, f"{INNER_LOCAL}:{PORT_PUBLIC}",
                             f"{ctx.ppp_local}:{PORT_PUBLIC}")
        # The reply is a UDP upload into the session: Linux's, and refused.
        assert not [f for f in flows if f["src"] == f"{client['ip']}:{PORT_PUBLIC}"
                    and f["new_src"] == f"{ctx.ppp_local}:{PORT_PUBLIC}"], flows
        assert state["rejects"] > initial["rejects"], (initial, state)
        assert forward["new_dst"] == f"{client['ip']}:{PORT_PUBLIC}", forward
        assert forward["in_ppp"] == _session_text(ctx.session_identity), forward
        assert forward["in"] == TARGET_WAN_IF and forward["out"] == TARGET_LAN_IF, \
            forward
        assert forward["out_br"] == ctx.bridge_text[LAN_VID], forward
        # A second knock, accounted for by the classifier's own counters: the
        # rule alone never proves the frame reached the wire. Every reply to it
        # came back through Linux.
        counted = {forward["cookie"]: int(forward["packets"])}
        again = await asyncio.to_thread(_knock, sent)
        assert again == sent, (again, sent)
        after = {f["cookie"]: int(f["packets"]) for f in (await ctx.state())["flows"]
                 if f["cookie"] in counted}
        assert set(after) == set(counted), (counted, after)
        assert all(after[c] - counted[c] == sent for c in counted), (counted, after)

        # The same port over TCP, with both halves read back while it runs.
        streaming = asyncio.create_task(asyncio.to_thread(_stream, 5))
        rows = None
        try:
            while rows is None and not streaming.done():
                rows = _tcp_rows((await ctx.state())["flows"])
                if rows is None:
                    await asyncio.sleep(0.5)
        finally:
            report = await streaming
        assert rows, f"the forwarded TCP connection was not carried both ways: {await ctx.state()}"
        inbound, reply = rows
        assert inbound["src"] == f"{INNER_LOCAL}:{report['port']}", (inbound, report)
        assert inbound["new_dst"] == f"{client['ip']}:{PORT_PUBLIC}", inbound
        _assert_session(ctx, reply, inbound)
        assert inbound["out_br"] == ctx.bridge_text[LAN_VID], inbound
        assert reply["in_br"] == ctx.bridge_text[LAN_VID], reply
        ctx.record("isp-port-forward", {"forward": forward, "echoed": echoed,
                                        "second": again, "tcp": {"inbound": inbound,
                                                                 "reply": reply,
                                                                 "report": report}})
    finally:
        await command(ctx.target, ctx.session, "nft", "delete", "table", "inet",
                      NAT_TABLE, check=False)
        await _client_python(ctx, client,
                             f"import subprocess\n"
                             f"subprocess.run(['pkill', '-f', {'^python3 ' + receiver + '$'!r}])\n",
                             label="profile_isp_forward_stop", timeout=20)


async def test_throughput(isp, splat_window):
    """What the profile forwards when nothing is in its way.

    A number below the ceiling means the CPU carried it, because the CPU cannot
    carry this much: the point of the case is the floor, not the measurement. It
    runs against the same profile as everything above, so what it measures is
    the shipping configuration rather than a bare NAT path. A floor needs a
    steady-state sample, not a long one: the receiver's own count over the
    five seconds after a three-second ramp, in which a slow start that
    overshoots has recovered. Taken from the receiver's per-second intervals
    rather than iperf3's omit period, whose first interval after the omit
    claims two seconds for one second's bytes when the two timers fire in the
    same microsecond.
    """
    ctx = isp
    client = BY_NAME["main"]
    server = await asyncio.create_subprocess_exec(
        "iperf3", "-s", "-1", "-B", INNER_LOCAL, "-p", str(PORT_RATE), "-J",
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    try:
        await asyncio.sleep(0.3)
        assert server.returncode is None, "the endpoint iperf3 did not start"
        script = f'''
import json, subprocess
argv = ['iperf3', '-c', {INNER_LOCAL!r}, '-B', {client['ip']!r}, '-p', {str(PORT_RATE)!r},
        '-P', '4', '-t', '8', '-Z', '-J']
result = subprocess.run(argv, capture_output=True, text=True, timeout=30)
print(json.dumps({{'rc': result.returncode, 'stdout': result.stdout}}))
'''
        result = await _client_python(ctx, client, script, label="profile_isp_rate",
                                      timeout=70)
        assert result.rc == 0, result.stdout
        report = json.loads(result.stdout.strip().splitlines()[-1])
        stdout, _ = await asyncio.wait_for(server.communicate(), 15)
        received = json.loads(stdout)
        settled = [i["sum"] for i in received["intervals"] if i["sum"]["start"] >= RAMP_SECONDS - 0.01]
        assert settled, received["intervals"]
        measured = {"bits_per_second": sum(i["bytes"] for i in settled) * 8 / sum(i["seconds"] for i in settled),
                    "seconds": sum(i["seconds"] for i in settled)}
        ctx.record("isp-throughput", {"client": report, "server": received["end"]["sum_received"],
                                      "settled": measured})
        assert report["rc"] == 0, report
        # This path's own ceiling, not the plain-NAT one. Every frame here
        # spends both encapsulation slots -- a session inside a carrier tag --
        # and the roadmap's paired measurement of exactly this shape records
        # 8.948 and 8.936 Gb/s under the flowtable against 8.984 and 8.931
        # under CMM. A 9 Gb/s floor borrowed from the untagged benchmark
        # therefore fails a path that is at its ceiling, which is the opposite
        # of what a throughput gate is for.
        floor = float(os.environ.get("ASK_PROFILE_ISP_MIN_GBPS", "8.8")) * 1e9
        assert measured["bits_per_second"] >= floor, (measured, floor)
    finally:
        if server.returncode is None:
            server.terminate()
            await asyncio.wait_for(server.communicate(), 10)


# ---- lifecycle: the events a real line produces ----------------------------

async def test_channel_change_keeps_the_other_group(isp, splat_window):
    """A set-top box leaves one channel and the other must not notice.

    This is what the listener-chain swap exists for. Replacing a group's
    listener set by deleting the key and re-adding it would take it out of the
    classifier in between, so every remaining viewer would lose frames because
    somebody else changed channel -- and in an IPTV deployment membership changes
    constantly.

    Both channels play, one membership is dropped halfway, and the assertion is
    on what happened *after* that moment: the survivor kept receiving at rate,
    the channel that was left went quiet, and the CPU stayed out of the path for
    the whole window rather than picking the survivor up in software.
    """
    ctx = isp
    survivor, leaving = GROUPS
    before = await hardware_group(ctx, survivor)
    window = STREAM_S * 2
    observed = await _watch(ctx, CHANNELS, window, "profile_isp_channel_change",
                            drop_after=STREAM_S)
    ctx.record("isp-channel-change", {"before": before, "observed": observed})
    kept = observed["channels"][survivor]
    left = observed["channels"][leaving]
    _assert_replicated(survivor, observed)
    # The half of the window after the drop is what this case is about.
    assert kept["after"] > STREAM_S * STREAM_PPS * 0.5, (kept, observed)
    assert left["after"] < left["before"] * 0.25, (left, observed)
    after = kept["row"]
    assert after["state"] == "installed", after
    if before:
        assert int(after["packets"]) > int(before["packets"]), (before, after)


async def test_redial_readmits_every_flow(isp, splat_window):
    """The line drops and comes back, and everything that depended on it does.

    Nothing about a session reaches the rule that could be revalidated later:
    the id is in an action the flow was built from once, and the concentrator's
    address is in no action at all. So the hangup has to retire the flows --
    otherwise the hardware keeps inserting a session header the concentrator has
    forgotten, and the frames vanish with every counter looking healthy.

    What must *not* happen is collateral. The retirement is driven by the peer
    route dying with the device, so it costs the directions that borrowed that
    destination and nothing else: the bindings stay up, admission is never
    disabled, and the IPTV group -- which depends on the bridge and not on the
    session -- keeps its hardware entry throughout.

    A UDP upload into the session is Linux's, so the UDP connection carries the
    strip across the redial, and the insert readmitted against the new session
    is a TCP connection's.
    """
    ctx = isp
    await _accounted(ctx, BY_NAME["main"], peer=INNER_LOCAL, dport=PORT_MAIN,
                     sport=PORT_MAIN, label="redial-before")
    before = await ctx.state()
    first = ctx.session_identity
    # This case owns a live viewer; a previous test's socket has already
    # closed and its membership can legitimately have disappeared.
    viewer = asyncio.create_task(_watch(ctx, CHANNELS[:1], 20,
                                         "profile_isp_redial_iptv"))
    try:
        await ctx.wait(lambda s: any(g["group"] == GROUPS[0] and g["state"] == "installed"
                                    for g in s["mcast"]), timeout=10)
        group_before = await hardware_group(ctx, GROUPS[0])
        await _hangup(ctx.console)
        retired = await ctx.wait(
            lambda s: s["route_invalidations"] >= before["route_invalidations"] + 1
            and not [f for f in s["flows"] if f["out_ppp"] != "-" or f["in_ppp"] != "-"],
            timeout=40)
        # Selective, and that is the result: the table is untouched, so admission
        # was never disabled and nothing has to be rebuilt to get it back.
        assert retired["bindings"] == 2, retired
        assert retired["invalidated"] == 0 and retired["invalidation_done"] == 0, retired
        assert retired["rearms"] == before["rearms"], retired
        assert retired["errors"] == before["errors"], retired
        group_retired = await hardware_group(ctx, GROUPS[0])
        assert group_retired and group_retired["state"] == "installed", group_retired
        # The device is gone, so /proc/net/pppoe has nothing left to describe.
        assert not (await read(ctx.target, ctx.session, "/proc/net/pppoe")).splitlines()[1:]

        ctx.ppp_if, ctx.ppp_pid = await _dial(ctx.console, ctx.ppp_lower, ipv6=True)
        ctx.session_identity = await _session_identity(ctx)
        # A redial takes the next address out of the concentrator's pool, so the
        # cases that run after this one have to be told what it is.
        ctx.ppp_local = await _session_address(ctx)
        # The device went and took its addresses and rules with it. Restated only
        # where it is missing, so a redial onto the same device name does not leave
        # the same rule twice.
        for subnet in (LAN_SUBNET, GUEST_SUBNET):
            nat = ["POSTROUTING", "-s", subnet, "-o", ctx.ppp_if, "-j", "MASQUERADE"]
            present = await command(ctx.target, ctx.session, "iptables", "-t", "nat", "-C",
                                    *nat, check=False)
            if present["rc"]:
                await command(ctx.target, ctx.session, "iptables", "-t", "nat", "-I", *nat)
        # The concentrator's own device went too, and with it the only route to the
        # subscriber's IPv6 prefix. Restated here rather than left to the fixture,
        # so what the profile carried before the drop it carries after it.
        await _session_ipv6(ctx, [])
    finally:
        # Finish the viewer's bounded window before cleanup changes its network.
        observed = await viewer
    _assert_replicated(GROUPS[0], observed)
    ctx.record("isp-redial-iptv", observed)
    await _reachable(ctx, BY_NAME["main"], INNER_LOCAL)

    _, download = await _accounted(ctx, BY_NAME["main"], peer=INNER_LOCAL,
                                   dport=PORT_MAIN, sport=PORT_MAIN, label="redial-after")
    # Against the session that exists now. A flow that had survived the hangup,
    # or been readmitted from anything cached, would name the old one -- which is
    # exactly the failure that forwards happily and delivers nothing.
    _assert_session(ctx, None, download)
    forward, reverse = await _tcp_accounted(ctx, BY_NAME["main"], peer=INNER_LOCAL,
                                            dport=PORT_MAIN, label="redial-after-tcp")
    _assert_session(ctx, forward, reverse)
    after = await ctx.state()
    assert after["bindings"] == 2 and after["rearms"] == before["rearms"], after
    assert after["invalidated"] == after["invalidation_done"] == 0, after
    assert after["errors"] == before["errors"], after
    ctx.record("isp-redial", {"first": _session_text(first),
                              "second": _session_text(ctx.session_identity),
                              "before": before, "retired": retired, "after": after,
                              "group_before": group_before,
                              "group_after": await hardware_group(ctx, GROUPS[0])})


async def test_lan_port_flap_readmits(isp, splat_window):
    """Somebody unplugs the LAN cable and plugs it back in.

    The bridged flows and the multicast group both hang off that port: the flows
    through the FDB entry that pinned their egress, the group through its
    listener list. Taking the port down has to retire both rather than keep
    forwarding to a port that is not there, and bringing it back has to relearn
    everything from traffic and from the next membership report, with nothing
    reconfigured in between.
    """
    ctx = isp
    await _accounted(ctx, BY_NAME["main"], peer=INNER_LOCAL, dport=PORT_MAIN,
                     sport=PORT_MAIN, label="flap-before")
    before = await ctx.state()
    await command(ctx.target, ctx.session, "ip", "link", "set", TARGET_LAN_IF, "down")
    try:
        retired = await ctx.wait(
            lambda s: not [f for f in s["flows"]
                           if TARGET_LAN_IF in (f["in"], f["out"])], timeout=30)
        assert retired["fatal"] == retired["quarantine"] == 0, retired
        assert retired["errors"] == before["errors"], retired
    finally:
        await command(ctx.target, ctx.session, "ip", "link", "set", TARGET_LAN_IF, "up")
    # Carrier, then the station's own frames, then admission: each step needs
    # the one before it, and nothing re-offers a retired flow on its own.
    await asyncio.sleep(3.0)
    await _reachable(ctx, BY_NAME["main"], INNER_LOCAL)
    # The UDP connection's download, and a TCP connection for the upload a UDP
    # one leaves to Linux.
    _, download = await _accounted(ctx, BY_NAME["main"], peer=INNER_LOCAL,
                                   dport=PORT_MAIN, sport=PORT_MAIN, label="flap-after")
    _assert_session(ctx, None, download)
    assert download["out_br"] == ctx.bridge_text[LAN_VID], download
    forward, reverse = await _tcp_accounted(ctx, BY_NAME["main"], peer=INNER_LOCAL,
                                            dport=PORT_MAIN, label="flap-after-tcp")
    _assert_session(ctx, forward, reverse)
    assert forward["in_br"] == ctx.bridge_text[LAN_VID], forward
    # And the group is relearned from the next report rather than resurrected.
    observed = await _watch(ctx, CHANNELS[:1], STREAM_S, "profile_isp_iptv_after_flap")
    _assert_replicated(GROUPS[0], observed)
    after = await ctx.state()
    assert after["bindings"] == 2 and after["invalidated"] == 0, after
    ctx.record("isp-lan-flap", {"before": before, "retired": retired, "after": after,
                                "observed": observed})


async def test_policy_revokes_and_readmits(isp, splat_window):
    """The operator stops the offload and starts it again, under live sockets.

    This is the revocation contract. Once a flow is cached in hardware its
    packets bypass the forward hooks entirely, so a policy change reaches it
    only by taking it out: `stop` has to drain every reference the backend holds
    -- entries, bindings, handles, neighbours, quarantine -- while the traffic
    keeps flowing in software on the same sockets, and `apply` has to put the
    whole profile back with nothing rebuilt.

    The software half is the mirror of every other assertion in this file: here
    the CPU counter is *required* to move, which is what makes the flat readings
    elsewhere mean something.
    """
    ctx = isp
    await _accounted(ctx, BY_NAME["main"], peer=INNER_LOCAL, dport=PORT_MAIN,
                     sport=PORT_MAIN, label="policy-before")
    before = await ctx.state()
    await stop(ctx.console)
    drained = await ctx.wait(lambda s: not s["bindings"] and not s["entries"],
                             timeout=30)
    assert drained["handle_refs"] == drained["neighbour_refs"] == 0, drained
    assert drained["quarantine"] == 0 and drained["fatal"] == 0, drained
    before_tx = await _software_tx(ctx)
    report = await _exchange(ctx, BY_NAME["main"], peer=INNER_LOCAL, dport=PORT_MAIN,
                             sport=PORT_MAIN, count=64, label="policy-software")
    after_tx = await _software_tx(ctx)
    software = {dev: after_tx[dev] - before_tx[dev] for dev in before_tx}
    assert report == {"echoed": 64, "lost": 0}, report
    # Both ports, because the connection leaves by one in each direction, and
    # transmit rather than receive for the reason _software_tx gives.
    assert all(value >= 64 for value in software.values()), software
    assert not (await ctx.state())["entries"]

    await apply(ctx.console, _policy(), r=ctx)
    await ctx.wait(lambda s: s["bindings"] == 2)
    # The same UDP socket's download back in hardware, its upload Linux's as
    # before the stop, and a TCP connection for the upload in hardware.
    _, download = await _accounted(ctx, BY_NAME["main"], peer=INNER_LOCAL,
                                   dport=PORT_MAIN, sport=PORT_MAIN, label="policy-after")
    _assert_session(ctx, None, download)
    assert download["out_br"] == ctx.bridge_text[LAN_VID], download
    forward, reverse = await _tcp_accounted(ctx, BY_NAME["main"], peer=INNER_LOCAL,
                                            dport=PORT_MAIN, label="policy-after-tcp")
    _assert_session(ctx, forward, reverse)
    assert forward["in_br"] == ctx.bridge_text[LAN_VID], forward
    after = await ctx.state()
    assert after["errors"] == before["errors"], (before, after)
    assert after["fatal"] == after["quarantine"] == 0, after
    ctx.record("isp-policy-cycle", {"before": before, "drained": drained,
                                    "after": after})
