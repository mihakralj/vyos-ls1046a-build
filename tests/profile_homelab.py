"""The gateway as an enthusiast runs it: a plain routed uplink, a segmented
LAN, a VPN and a tunnel, proved as one configuration.

The other profile in this directory is the carrier shape -- a session on a
tagged uplink, IPTV, QoS. This one is the other half of the product's
deployment surface and shares almost nothing with it. The uplink is static
dual-stack on the WAN port with no session at all. The LAN is one physical port
carrying three VLANs, with a firewall policy between them rather than one flat
subnet. There is an IPsec tunnel to a peer on the WAN, a 6o4 tunnel giving the
main VLAN IPv6 through a broker, routed multicast fed by smcroute rather than
by a bridge's snooping, and an ingress policer on the uplink. Six features,
three of which the carrier profile never touches.

The point of running them together is what they share: one physical port on
each side, one conntrack table, one flowtable binding, one classifier. Inter-
VLAN routing between two VLANs on the same port is a hairpin, a bridge and a
tag at once, and it is the case most likely to break when any one of them
changes. The tunnel and the IPsec tunnel both ask the classifier to build an
outer header the CPU would otherwise have built. Multicast asks it to replicate
onto devices the unicast path never names.

The disciplines are this file's copy of the ISP profile's, and they are not
negotiable in either:

  - **the row names the encapsulation.** Delivery proves nothing on its own --
    the CPU forwards, the bridge floods -- so every case reads the adapter's own
    row back, requires it to name the bridge, tags, tunnel or SA it was supposed
    to build, then sends a second burst and requires the classifier's own packet
    counters to account for all of it.
  - **the CPU did not do it.** The physical ports' software receive counters
    stay flat across the measured burst while the hardware counters move.

The lifecycle half asks what happens when the parts move: the default route
changes and comes back, an IPsec SA is replaced under a live flow, a VLAN
membership is withdrawn from the LAN port, and finally the adapter itself is
unloaded and reloaded with the whole profile standing -- after which every
feature above is re-proved by a short burst rather than assumed.

Bench furniture this profile needs, none of it created here:

  - `smcroute` (`smcrouted` and `smcroutectl`) in the DUT image. Both are in the
    agent's argv allowlist already; the case skips, naming them, if the binaries
    are absent.
  - `iperf3` on the LAN VM and on the orchestrator, for the policer and the
    opt-in throughput case.
  - nothing on the WAN switch: unlike the ISP profile, nothing here is tagged
    on the uplink.
"""
from __future__ import annotations

import asyncio
import ipaddress
import json
import os
import re
import secrets
import socket
from types import SimpleNamespace

import aiohttp
import pytest
import pytest_asyncio

from ask_orch.capture import capture_window
from ask_orch.commands import remove_qdisc
from ask_orch.client import Agent
from ask_orch.uart import Console
from _topology import (DUT_IPV6_WAN, LAN_NIC, TARGET_LAN_IF, TARGET_WAN_IF, WAN_IPV6,
                       TopologyStack, dut_vlan_subif, kernel_rx_packets, lan_run,
                       lan_run_python)
from _flowtable_rig import (artifact_dir, Rig, command, console_command, read)
from _flowtable_connections import peer
from _flowtable_policy import (CONFIG, apply, stop)
from _gated_tcp import GatedTcp
from _flowtable_tunnel import (
    Shape,
    _dut_tunnel,
    _orchestrator_tunnel,
    _outer_segment,
    _tunnel_text,
)
from _ipsec_inbound_flow_offload import (crypto, sec_counter)

pytestmark = [
    pytest.mark.requires("smcrouted"),
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
#   profile_homelab.py     281/282/283
#
BRIDGE = "br-home"
VID_A = int(os.environ.get("ASK_PROFILE_HOME_VID_A", "281"))   # trusted, PVID
VID_B = int(os.environ.get("ASK_PROFILE_HOME_VID_B", "282"))   # IoT
VID_C = int(os.environ.get("ASK_PROFILE_HOME_VID_C", "283"))   # guest

# Subnets, deliberately not derived from the VLAN ids: an id can exceed an
# octet and a subnet built out of one silently aliases as soon as it does.
SUBNET_A, GATEWAY_A, CLIENT_A = "172.29.81.0/24", "172.29.81.1", "172.29.81.2"
SUBNET_B, GATEWAY_B, CLIENT_B = "172.29.82.0/24", "172.29.82.1", "172.29.82.2"
SUBNET_C, GATEWAY_C, CLIENT_C = "172.29.83.0/24", "172.29.83.1", "172.29.83.2"
MAC_A, MAC_B, MAC_C = ("02:9d:99:b2:81:02", "02:9d:99:b2:82:02", "02:9d:99:b2:83:02")
# VLAN A's IPv6, which arrives through the 6o4 tunnel rather than from the WAN
# port: this is the tunnel-broker shape, where the uplink is v4-only.
PREFIX_A6, GATEWAY_A6, CLIENT_A6 = "fc00:81::/64", "fc00:81::1", "fc00:81::2"

# An address on the orchestrator that is not on the WAN segment, so reaching it
# costs the DUT a default route. Every WAN case here uses it, which is what
# makes the default-route lifecycle case a test of those flows rather than of a
# route nothing was using.
INTERNET = os.environ.get("ASK_PROFILE_HOME_INTERNET", "198.18.50.1")
# The inner address of the IPsec tunnel, on the WAN peer's loopback. 198.18.98.x
# deliberately avoids the inner prefixes used by the other IPsec tests.
IPSEC_INNER = os.environ.get("ASK_PROFILE_HOME_IPSEC_INNER", "198.18.98.2")
IPSEC_REQID_OUT, IPSEC_REQID_IN = "49212", "49213"
# Packets that legitimately travel in software before the entries exist. A
# bidirectional tunnel needs a few: conntrack must confirm, and only an
# original-direction packet can create the flow at all, because every reply
# carries a sec_path and nft_flow_offload_skip() declines it.
IPSEC_SETUP = 5
# Full-size, deliberately: the classifier checks the size of what it transmits
# against the MTU in the entry, and for a direction handed to SEC that is the
# outer frame. A short payload fits whatever is programmed, so a wrong MTU
# excepts nothing and every assertion still passes while the CPU does the work.
IPSEC_PAYLOAD = 1400

# One port per case, so a conntrack left behind by one never feeds another.
PORT = int(os.environ.get("ASK_PROFILE_HOME_PORT", "49200"))
PORT_A, PORT_B, PORT_C = PORT, PORT + 2, PORT + 4
PORT_HAIRPIN, PORT_REFUSED = PORT + 6, PORT + 8
PORT_V6 = PORT + 10
PORT_IPSEC = PORT + 12
PORT_MCAST = PORT + 14
PORT_RATE = PORT + 16
PORT_POLICE = PORT + 18

GROUP = os.environ.get("ASK_PROFILE_HOME_GROUP", "239.9.2.1")
# The daemon's generated config. A path of this profile's own, because
# mcast_e2e.py writes one too and the two must never be each other's.
SMCROUTE_CONF = "/tmp/ask-profile-home-smcroute.conf"
# Above 1, always: the soft parser ends the parse before classification for TTL
# 0 or 1, and ip_mr_forward()'s own rule is the same one. The replicas carry one
# less, which is the routing the hardware did on the way through.
STREAM_TTL = 64
STREAM_S, STREAM_PPS = 3.0, 500

# Below the roughly 9 Gbit/s wire ceiling, above the DUT's software capacity:
# delivery near 2 Gbit/s proves useful hardware forwarding as well as a cap.
# skip_sw/in_hw and the meter's own drop counters remain independent oracles.
POLICE_RATE_MBIT = int(os.environ.get("ASK_PROFILE_HOME_POLICE_MBIT", "2000"))
POLICE_BURST = os.environ.get("ASK_PROFILE_HOME_POLICE_BURST", "4m")
POLICE_OFFERED_MBIT = POLICE_RATE_MBIT * 3

FIREWALL_TABLE = "ask_profile_home"
TUNNEL_DEVICE = os.environ.get("ASK_PROFILE_HOME_TUNNEL", "fthome6o4")
ORCH_IPV4 = os.environ.get("ASK_WAN_IP", "127.0.0.1")
INJECT_IF = os.environ.get("ASK_WAN_INJECT_IF", "")


def orchestrator_source():
    """The orchestrator's WAN address: the multicast source, the IPsec outer
    peer and the DUT's alternate default gateway, from the bench configuration."""
    return os.environ.get("ASK_WAN_IPERF_IP", "")


# ---- reading the adapter ---------------------------------------------------

class Profile(Rig):
    """Rig's reading half -- state, wait, nft, record -- over a whole profile.

    Its table, exchange and conntrack helpers are not reused: each describes one
    LAN address against one WAN endpoint, and this profile has three clients,
    two tunnels and a multicast group. The offload policy is the shipping
    daemon's own catch-all, which is what the box runs by default.
    """


def _direction(flows, source, destination):
    matching = [f for f in flows if f["src"] == source and f["dst"] == destination]
    assert len(matching) == 1, (source, destination, flows)
    return matching[0]


def _directions(flows, source, sport, peer, dport):
    """Both halves of one connection, translated or not. The reverse half can be
    joined back to the client only through `new_dst` once it is masqueraded, and
    this profile has translated and untranslated paths at the same time."""
    forward = _direction(flows, f"{source}:{sport}", f"{peer}:{dport}")
    reverse = [f for f in flows if f["src"] == f"{peer}:{dport}"
               and f["new_dst"] == f"{source}:{sport}"]
    assert len(reverse) == 1, (source, sport, peer, dport, flows)
    return forward, reverse[0]


def _bracketed(address):
    return f"[{address}]" if ":" in address else address


# ---- LAN-side clients ------------------------------------------------------

def _client(name, *, vid, ip, mac, gateway, ip6=None, gateway6=None):
    return {"name": name, "netns": f"ask-home-{name}", "iface": f"askh{name}",
            "vid": vid, "ip": ip, "mac": mac, "gateway": gateway,
            "ip6": ip6, "gateway6": gateway6}


CLIENTS = [
    _client("a", vid=None, ip=CLIENT_A, mac=MAC_A, gateway=GATEWAY_A,
            ip6=CLIENT_A6, gateway6=GATEWAY_A6),
    _client("b", vid=VID_B, ip=CLIENT_B, mac=MAC_B, gateway=GATEWAY_B),
    _client("c", vid=VID_C, ip=CLIENT_C, mac=MAC_C, gateway=GATEWAY_C),
]
BY_NAME = {c["name"]: c for c in CLIENTS}


async def _build_clients(ctx):
    """One network namespace per VLAN, all on the one physical LAN wire.

    The trusted VLAN's client is a macvlan, so it is untagged exactly as a
    laptop is; the IoT and guest clients are VLAN devices moved into their
    namespaces, so their frames carry the tag the bridge classifies on. Distinct
    MACs, so the bridge learns three stations on one port rather than one.

    The whole build is one script, and it removes every namespace it created if
    any step fails: a half-built client would be inherited by the next run as a
    name collision rather than as the failure it is.
    """
    setup = f'''
import pathlib, subprocess, time
clients = {CLIENTS!r}
def run(*args): subprocess.run(args, check=True, capture_output=True, text=True)

# Anything a previous run left behind, before asserting the wire is clean. A
# listener started detached outlives a run that aborted, and while it lives it
# holds its network namespace open -- `ip netns del` only unlinks the name --
# so the macvlan inside keeps its MAC registered against the lower device and
# the next run's client cannot take the same one. It surfaces as "Address
# already in use" on a host where `ip netns list` is empty and `ip link` shows
# nothing, which is how the ISP profile first failed on the rig.
subprocess.run(['pkill', '-9', '-f', '/tmp/ask_profile_home_'], capture_output=True)
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
        run('ip', '-n', c['netns'], 'link', 'set', c['iface'], 'address', c['mac'], 'up')
        run('ip', '-n', c['netns'], 'addr', 'add', c['ip'] + '/24', 'dev', c['iface'])
        run('ip', '-n', c['netns'], 'route', 'add', 'default', 'via', c['gateway'])
        # A multicast stream's source is on another subnet entirely, and a
        # namespace that reverse-path filters drops every frame of it.
        run('ip', 'netns', 'exec', c['netns'], 'sysctl', '-qw',
            'net.ipv4.conf.all.rp_filter=0')
        run('ip', 'netns', 'exec', c['netns'], 'sysctl', '-qw',
            'net.ipv4.conf.%s.rp_filter=0' % c['iface'])
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
    result = await lan_run_python(ctx.lan, setup, label="profile_home_clients",
                                  timeout=60)
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
    await lan_run_python(ctx.lan, teardown, label="profile_home_clients_down",
                         timeout=40)


async def _client_python(ctx, client, script, *, label, timeout=60):
    """Run a script inside one client's namespace on the LAN VM. The same setns
    preamble Rig.run_peer uses, stated here because a profile drives several
    namespaces in one test and the Rig-level attribute would have to be set and
    unset around every call."""
    staged = ("import os\n"
              f"with open('/var/run/netns/' + {client['netns']!r}, 'rb') as ns:\n"
              "    os.setns(ns.fileno(), os.CLONE_NEWNET)\n" + script)
    return await lan_run_python(ctx.lan, staged, label=label, timeout=timeout)


# ---- traffic ---------------------------------------------------------------

def _source_address(client, peer):
    return client["ip6"] if ":" in peer else client["ip"]


async def _exchange(ctx, client, *, peer, dport, sport, count, payload_size=256,
                    label="profile_home", tolerate_loss=False):
    """Echo `count` datagrams from one client and report what came back.

    A reply from the wrong endpoint or with the wrong payload is fatal: either
    means the path is not the one under test. A timeout is only counted, so a
    caller can tolerate loss while a flow is being admitted -- and a case that
    expects the firewall to refuse the traffic entirely can read the count.
    """
    source = _source_address(client, peer)
    family = "socket.AF_INET6" if ":" in source else "socket.AF_INET"
    script = f'''
import json, socket, struct, time
s = socket.socket({family}, socket.SOCK_DGRAM)
s.settimeout({0.5 if tolerate_loss else 2})
s.bind(({source!r}, {sport}))
echoed = lost = 0
for n in range({count}):
    payload = struct.pack('!Q', n) + b'ASK-profile'.ljust({payload_size} - 8, b'.')[:{payload_size} - 8]
    try:
        s.sendto(payload, ({peer!r}, {dport}))
    except OSError:
        lost += 1
        continue
    try:
        data, addr = s.recvfrom(4096)
    except (TimeoutError, OSError):
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


async def _admit(ctx, client, *, peer, dport, sport, timeout=20, label="profile_home"):
    """Send short bursts until both directions are installed. A deadline rather
    than a round count: an admission that loses rtnl_trylock is declined and
    re-offered only after two flowtable GC ticks, and nothing re-offers a
    retired flow on its own -- so every attempt sends before it looks."""
    source, target = _bracketed(_source_address(client, peer)), _bracketed(peer)
    deadline = asyncio.get_running_loop().time() + timeout
    while True:
        await _exchange(ctx, client, peer=peer, dport=dport, sport=sport, count=4,
                        label=label)
        flows = (await ctx.state())["flows"]
        try:
            return _directions(flows, source, sport, target, dport)
        except AssertionError:
            if asyncio.get_running_loop().time() > deadline:
                conntrack = await command(ctx.target, ctx.session, "conntrack", "-L",
                                          "-o", "extended", check=False)
                state = await ctx.state()
                ctx.record("home-partial-admission",
                           {"state": state, "conntrack": conntrack})
                pytest.fail(
                    f"{client['name']} {source}:{sport} -> {target}:{dport} was not "
                    f"admitted in both directions: validated={state['validated']} "
                    f"rejects={state['rejects']} busy={state['busy']} "
                    f"errors={state['errors']}\nflows={state['flows']}")
            await asyncio.sleep(0.5)


async def _accounted(ctx, client, *, peer, dport, sport, count=64, payload_size=256,
                     label="profile_home"):
    """Admit the connection, then measure a second burst against the hardware.

    Three things are required of that burst and all three are needed: the rows
    are the same rows -- the cookies did not move, so nothing was readmitted
    underneath the measurement -- the classifier counted every frame, and the
    physical ports' software receive counters did not. The bound on the WAN port
    allows unrelated traffic on the shared WAN segment; the LAN port carries
    the profile's traffic.
    """
    forward, reverse = await _admit(ctx, client, peer=peer, dport=dport, sport=sport,
                                    label=label)
    before = {f["cookie"]: int(f["packets"]) for f in (forward, reverse)}
    software_before = await _software_rx(ctx)
    report = await _exchange(ctx, client, peer=peer, dport=dport, sport=sport,
                             count=count, payload_size=payload_size, label=label)
    software_after = await _software_rx(ctx)
    state = await ctx.state()
    after = {f["cookie"]: int(f["packets"]) for f in state["flows"]
             if f["cookie"] in before}
    assert report == {"echoed": count, "lost": 0}, report
    assert set(after) == set(before), (
        "a direction was readmitted mid-measurement: "
        + " ".join(f"{k}={state[k]}" for k in sorted(state)
                   if k.endswith("invalidations") or
                   k in ("invalidated", "invalidation_done", "rearms", "errors",
                         "rejects", "busy", "installs", "deletes")))
    delta = {c: after[c] - before[c] for c in before}
    assert all(d == count for d in delta.values()), (delta, state)
    software = {dev: software_after[dev] - software_before[dev]
                for dev in software_before}
    # A software-forwarded burst puts `count` frames through each port's
    # receive path, so anything well under that says the hardware carried it.
    # Allow background frames on the shared WAN segment. DUT control uses UART.
    assert software[TARGET_LAN_IF] < count // 4, (software, count)
    assert software[TARGET_WAN_IF] < count // 2, (software, count)
    ctx.record(f"home-{label}", {"forward": forward, "reverse": reverse,
                                 "delta": delta, "software_rx": software})
    return forward, reverse


async def _tunnel_tcp(ctx, client, label):
    """One TCP connection from `client` through the 6o4 tunnel, both directions
    in hardware and each carrying the measured transfer. TCP rather than UDP: a
    UDP upload into the tunnel is Linux's, the LAN port delivering a full frame
    the tunnel cannot carry, and TCP's segments are clamped to fit. Returns
    (forward, reverse) as installed."""
    peer_address = ctx.shape.inner_orch
    source, target = _bracketed(_source_address(client, peer_address)), _bracketed(peer_address)

    def rows(state):
        forward = [f for f in state["flows"] if f["proto"] == "6"
                   and f["src"].startswith(source + ":") and f["dst"] == f"{target}:{PORT_V6}"]
        reverse = [f for f in state["flows"] if f["proto"] == "6"
                   and f["src"] == f"{target}:{PORT_V6}"]
        return (forward[0], reverse[0]) if len(forward) == len(reverse) == 1 else None

    async def run(script, **kwargs):
        return await _client_python(ctx, client, script, **kwargs)

    async with GatedTcp(run, source=_source_address(client, peer_address), peer=peer_address,
                        dport=PORT_V6, label="home_" + label.replace("-", "_")) as transfer:
        await transfer.warmed()
        for _ in range(20):
            before = await ctx.state()
            if rows(before):
                break
            await asyncio.sleep(0.5)
        assert rows(before), before["flows"]
        forward, reverse = rows(before)
        await transfer.measure()
        after = await ctx.state()
    moved = {f["cookie"]: int(f["packets"]) for f in after["flows"]}
    assert forward["cookie"] in moved and reverse["cookie"] in moved, after["flows"]
    assert moved[forward["cookie"]] - int(forward["packets"]) > 100, (forward, after["flows"])
    assert moved[reverse["cookie"]] - int(reverse["packets"]) > 100, (reverse, after["flows"])
    ctx.record("home-" + label, {"forward": forward, "reverse": reverse, "after": after,
                                 "report": transfer.report})
    return forward, reverse


def _assert_wan(ctx, forward, reverse, *, client):
    """A masqueraded flow out of the LAN bridge and onto the WAN port."""
    assert forward["in"] == TARGET_LAN_IF and forward["out"] == TARGET_WAN_IF, forward
    assert reverse["in"] == TARGET_WAN_IF and reverse["out"] == TARGET_LAN_IF, reverse
    dev = ctx.bridge_text[client["vid"] or VID_A]
    assert forward["in_br"] == dev and forward["out_br"] == "-", forward
    assert reverse["out_br"] == dev and reverse["in_br"] == "-", reverse
    # The port is masquerade's to choose, so only the address is asserted.
    assert forward["new_src"].startswith(ctx.wan_address + ":"), forward
    tag = "-" if client["vid"] is None else str(client["vid"])
    assert forward["in_vlan"] == tag and forward["out_vlan"] == "-", forward
    assert reverse["out_vlan"] == tag and reverse["in_vlan"] == "-", reverse


# ---- the profile -----------------------------------------------------------

def _policy():
    """The shipping offload policy: every eligible forwarded flow, across both
    physical ports."""
    return {"version": 1, "enabled": True, "devices": [TARGET_LAN_IF, TARGET_WAN_IF],
            "scope": [{}], "exclude": []}


async def _bridge_lan(ctx, stack):
    """One VLAN-aware bridge over the one physical LAN port, carrying three
    VLANs: A untagged on the PVID, B and C tagged.

    `vlan_default_pvid 0` first, so the only PVID anywhere is the one this asks
    for; the bridge resolves its FDB lookup inside whichever VLAN a frame ends
    up in, and an unasked-for default of 1 puts frames somewhere nothing else
    describes.
    """
    async def dut(*argv, check=True):
        return await command(ctx.target, ctx.session, *argv, check=check)

    addresses = json.loads((await dut("ip", "-j", "-4", "addr", "show",
                                      "dev", TARGET_LAN_IF))["stdout"])[0]
    original = next(f"{a['local']}/{a['prefixlen']}" for a in addresses["addr_info"]
                    if a["family"] == "inet")
    await dut("ip", "link", "del", BRIDGE, check=False)
    await dut("ip", "link", "add", "name", BRIDGE, "type", "bridge")

    # Restore the address even if deleting the bridge fails.
    stack.push(lambda: dut("ip", "addr", "replace", original, "dev", TARGET_LAN_IF))
    stack.push(lambda: dut("ip", "link", "del", BRIDGE))

    await dut("ip", "link", "set", BRIDGE, "type", "bridge", "vlan_filtering", "1",
              "vlan_default_pvid", "0")
    await dut("ip", "addr", "del", original, "dev", TARGET_LAN_IF)
    await dut("ip", "link", "set", TARGET_LAN_IF, "master", BRIDGE)
    await dut("ip", "link", "set", BRIDGE, "up")
    await dut("bridge", "vlan", "add", "dev", TARGET_LAN_IF, "vid", str(VID_A),
              "pvid", "untagged")
    for vid in (VID_B, VID_C):
        await dut("bridge", "vlan", "add", "dev", TARGET_LAN_IF, "vid", str(vid))
    for vid in (VID_A, VID_B, VID_C):
        await dut("bridge", "vlan", "add", "dev", BRIDGE, "vid", str(vid), "self")

    ctx.bridge_text = {}
    for vid, address, address6 in ((VID_A, GATEWAY_A, GATEWAY_A6),
                                   (VID_B, GATEWAY_B, None),
                                   (VID_C, GATEWAY_C, None)):
        ctx.bridge_text[vid] = await dut_vlan_subif(
            stack, ctx.target, ctx.session, parent=BRIDGE, vid=vid,
            name=f"{BRIDGE}.{vid}", ipv4=f"{address}/24",
            ipv6=f"{address6}/64" if address6 else None)
    # Keep the LAN VM's existing address reachable on the trusted VLAN while
    # the profile's clients use their separate namespaces and subnets.
    await dut("ip", "addr", "add", original, "dev", ctx.bridge_text[VID_A])
    ctx.lan_original = original
    ctx.dut_lan_address = original.split("/")[0]
    ctx.dut_lan_mac = (await read(ctx.target, ctx.session,
                                  f"/sys/class/net/{TARGET_LAN_IF}/address")).strip()


async def _firewall(ctx, cleanup):
    """The segmentation policy: trusted may reach IoT, IoT may not reach
    trusted, guest may reach neither, and everything may reach the WAN
    masqueraded.

    Stated as explicit refusals under an accept policy rather than as a
    default-drop chain. A default-drop forward chain would also have to
    enumerate the decrypted IPsec traffic, the tunnel's inner packets and the
    replicated multicast this profile carries, and the subject of the case is
    the segmentation decision rather than an exhaustive ruleset -- a rule
    missing from the enumeration would fail a feature for a reason that has
    nothing to do with what is being tested.

    Priority matters: this chain runs at the filter hook's own priority, ahead
    of the offload policy's chain, so a refused packet is dropped before
    admission is ever offered it. That is what lets a case assert that the
    refused direction has no row at all rather than a row that never matches.
    """
    trusted, iot, guest = (ctx.bridge_text[VID_A], ctx.bridge_text[VID_B],
                           ctx.bridge_text[VID_C])
    # The image also has a legacy iptables WAN masquerade rule. A return in
    # this nft chain alone cannot exempt VPN traffic from that earlier chain;
    # it would change the inner source and stop the XFRM selector matching.
    exemption = ["POSTROUTING", "-s", CLIENT_A, "-d", IPSEC_INNER, "-j", "ACCEPT"]
    await command(ctx.target, ctx.session, "iptables", "-t", "nat", "-I",
                  exemption[0], "1", *exemption[1:])
    cleanup.append((ctx.target, ["iptables", "-t", "nat", "-D", *exemption]))
    await command(ctx.target, ctx.session, "nft", "delete", "table", "inet",
                  FIREWALL_TABLE, check=False)
    await command(ctx.target, ctx.session, "nft", f'''table inet {FIREWALL_TABLE} {{
 chain forward {{ type filter hook forward priority filter; policy accept;
 ct state established,related accept
 iifname "{iot}" oifname "{trusted}" counter drop
 iifname "{guest}" oifname {{ "{trusted}", "{iot}" }} counter drop
 }}
 chain postrouting {{ type nat hook postrouting priority srcnat; policy accept;
 ip saddr {CLIENT_A} ip daddr {IPSEC_INNER} return
 oifname "{TARGET_WAN_IF}" ip saddr {{ {SUBNET_A}, {SUBNET_B}, {SUBNET_C} }} masquerade
 }}
}}''')
    cleanup.append((ctx.target, ["nft", "delete", "table", "inet", FIREWALL_TABLE]))


async def _firewall_drops(ctx, iif, oif):
    """What the refusal rule has counted.

    The counter is the only oracle that distinguishes a refused direction from
    one that had nothing to answer it: a silent echo proves nothing on its own,
    because an unanswered datagram looks the same whether the firewall dropped
    it or the far side simply never replied.
    """
    listing = await command(ctx.target, ctx.session, "nft", "list", "table", "inet",
                            FIREWALL_TABLE)
    for line in listing["stdout"].splitlines():
        if f'iifname "{iif}"' in line and oif in line:
            match = re.search(r"counter packets (\d+)", line)
            assert match, line
            return int(match.group(1))
    raise AssertionError(f"no refusal rule for {iif} -> {oif} in "
                         f"{listing['stdout']!r}")


async def _udp_listener(ctx, client, port, label):
    """A UDP echo in one client's namespace, detached, and a callable that
    stops it. The listener outlives the command that starts it."""
    path = f"/tmp/ask_profile_home_echo_{label}_{port}.py"
    script = (f"import socket\n"
              f"s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)\n"
              f"s.settimeout(120)\n"
              f"s.bind(({client['ip']!r}, {port}))\n"
              f"while True:\n"
              f"    data, peer = s.recvfrom(2048)\n"
              f"    s.sendto(data, peer)\n")
    staged = (f"import pathlib, subprocess\n"
              f"pathlib.Path({path!r}).write_text({script!r})\n"
              f"with open({path + '.log'!r}, 'wb') as log:\n"
              f"    subprocess.Popen(['python3', {path!r}], stdin=subprocess.DEVNULL,\n"
              f"                     stdout=log, stderr=log, start_new_session=True)\n"
              "print('LISTENER-UP')\n")
    started = await _client_python(ctx, client, staged, label=f"{label}_up", timeout=30)
    assert "LISTENER-UP" in started.stdout, started.stdout

    async def stop_listener():
        await _client_python(ctx, client,
                             f"import subprocess\n"
                             f"subprocess.run(['pkill', '-f', {'^python3 ' + path + '$'!r}])\n",
                             label=f"{label}_down", timeout=20)
    return stop_listener


async def _reachable(ctx, client, peer, attempts=25):
    """Prove a path before measuring anything on it. The first packet after a
    bring-up loses a race with ARP or ND, and the measurement helpers treat one
    lost datagram as the failure it would otherwise be."""
    source = _source_address(client, peer)
    argv = (["ping", "-6"] if ":" in peer else ["ping"]) + \
        ["-c", "3", "-W", "2", "-I", source, peer]
    script = (f"import subprocess\n"
              f"print('rc=%d' % subprocess.run({argv!r}, capture_output=True).returncode)\n")
    for _ in range(attempts):
        probe = await _client_python(ctx, client, script,
                                     label="profile_home_reachable", timeout=30)
        if "rc=0" in probe.stdout:
            return
        await asyncio.sleep(1.0)
    dut = await command(ctx.target, ctx.session, "ip", "-br", "addr", check=False)
    route = await command(ctx.target, ctx.session, "ip", "route", "get", peer,
                          check=False)
    pytest.fail(f"{client['name']} could not reach {peer} after {attempts} attempts: "
                f"{probe.stdout!r}\n  DUT addresses: {dut.get('stdout', dut)!r}\n"
                f"  DUT route:     {route.get('stdout', route)!r}")


# ---- IPsec -----------------------------------------------------------------
#
# The tunnel runs between the DUT and the orchestrator, both endpoints on
# the WAN segment, and carries a routed inner subnet. Only the DUT is
# offloaded: the WAN peer is plain software IPsec and has no idea its peer is not,
# which makes its decryption an independent check on what SEC emitted.

def _ipsec_states(ctx, spi_out, spi_in):
    outer_local, outer_remote = ctx.wan_address, orchestrator_source()
    here = CLIENT_A
    return {
        "out_state": ("src", outer_local, "dst", outer_remote, "proto", "esp",
                      "spi", hex(spi_out)),
        "in_state": ("src", outer_remote, "dst", outer_local, "proto", "esp",
                     "spi", hex(spi_in)),
        "fwd_sel": ("src", here + "/32", "dst", IPSEC_INNER + "/32"),
        "rev_sel": ("src", IPSEC_INNER + "/32", "dst", here + "/32"),
        "fwd_tmpl": ("tmpl", "src", outer_local, "dst", outer_remote, "proto", "esp",
                     "mode", "tunnel", "reqid", IPSEC_REQID_OUT, "level", "required"),
        "rev_tmpl": ("tmpl", "src", outer_remote, "dst", outer_local, "proto", "esp",
                     "mode", "tunnel", "reqid", IPSEC_REQID_IN, "level", "required"),
    }


async def _peer_xfrm_add(ctx, kind, identity, *parameters):
    await command(ctx.wan, ctx.session, "ip", "xfrm", kind, "add",
                  *identity, *parameters)
    ctx.ipsec_peer_objects.append((kind, identity))


async def _clear_peer_ipsec(ctx):
    # The WAN host is shared. Remove only states and policies this fixture
    # successfully created, never flush its XFRM tables.
    cleanup = TopologyStack()
    for kind, identity in ctx.ipsec_peer_objects:
        cleanup.push(lambda k=kind, i=identity: command(
            ctx.wan, ctx.session, "ip", "xfrm", k, "delete", *i))
    await cleanup.teardown("WAN peer XFRM")
    ctx.ipsec_peer_objects.clear()


async def _install_ipsec(ctx, spi_out, spi_in):
    """Paired SAs and policies on the gateway and its software peer.

    The DUT's state and policy are both offloaded, and both have to be:
    xfrm_state_find() skips a packet-offloaded state whenever the policy that
    reached it is not offloaded too, so an SA paired with a software policy is
    never selected and the tunnel carries nothing.

    The forward policy is ordinary IPsec-gateway configuration rather than
    anything about the offload: a decrypted packet that is *forwarded* is
    checked against the fwd policy, and a tunnel-mode sec_path with no matching
    one is rejected outright, so without it the replies never reach the forward
    chain at all.
    """
    s = _ipsec_states(ctx, spi_out, spi_in)

    async def dut(*argv, check=True):
        return await command(ctx.target, ctx.session, *argv, check=check)

    await _clear_peer_ipsec(ctx)
    await _peer_xfrm_add(ctx, "state", s["out_state"],
                         *crypto(IPSEC_REQID_OUT), "replay-window", "32")
    await _peer_xfrm_add(ctx, "state", s["in_state"],
                         *crypto(IPSEC_REQID_IN), "replay-window", "32")
    await _peer_xfrm_add(ctx, "policy", (*s["fwd_sel"], "dir", "in"), *s["fwd_tmpl"])
    await _peer_xfrm_add(ctx, "policy", (*s["rev_sel"], "dir", "out"), *s["rev_tmpl"])

    await dut("ip", "xfrm", "policy", "flush")
    await dut("ip", "xfrm", "state", "flush")
    await dut("ip", "route", "replace", IPSEC_INNER + "/32",
              "via", orchestrator_source(), "dev", TARGET_WAN_IF)
    await dut("ip", "xfrm", "state", "add", *s["out_state"], *crypto(IPSEC_REQID_OUT),
              "offload", "packet", "dev", TARGET_WAN_IF, "dir", "out")
    await dut("ip", "xfrm", "state", "add", *s["in_state"], *crypto(IPSEC_REQID_IN),
              "offload", "packet", "dev", TARGET_WAN_IF, "dir", "in")
    await dut("ip", "xfrm", "policy", "add", *s["fwd_sel"], "dir", "out",
              *s["fwd_tmpl"], "offload", "packet", "dev", TARGET_WAN_IF)
    await dut("ip", "xfrm", "policy", "add", *s["rev_sel"], "dir", "in",
              *s["rev_tmpl"], "offload", "packet", "dev", TARGET_WAN_IF)
    await dut("ip", "xfrm", "policy", "add", *s["rev_sel"], "dir", "fwd", *s["rev_tmpl"])
    # A conntrack entry surviving an earlier scenario carries IPS_OFFLOAD and is
    # never offered to the flowtable again, so the flow would be judged on that
    # scenario's decision rather than on this one's.
    await dut("conntrack", "-F", check=False)
    ctx.ipsec_spis = (spi_out, spi_in)


async def _rekey_ipsec(ctx, spi_out):
    """Replace the outbound SA in place, the way a rekey does: same selectors,
    same reqid, a new SPI.

    The policy names its template by reqid rather than by SPI, so the new state
    is selected without the policy being touched and without conntrack being
    flushed. That is what makes the case a test of the flow's dependency on the
    *state* -- flushing conntrack would retire every flow in the profile and the
    cookies would change for a reason that had nothing to do with the SA.

    The peer learns the new SPI before the DUT starts sending it, and the old
    state is removed from the peer afterwards: in between, either is decodable,
    which is the whole point of a rekey.
    """
    old_out, old_in = ctx.ipsec_spis
    fresh = _ipsec_states(ctx, spi_out, old_in)
    stale = _ipsec_states(ctx, old_out, old_in)
    await _peer_xfrm_add(ctx, "state", fresh["out_state"],
                         *crypto(IPSEC_REQID_OUT), "replay-window", "32")
    await command(ctx.target, ctx.session, "ip", "xfrm", "state", "delete",
                  *stale["out_state"])
    await command(ctx.target, ctx.session, "ip", "xfrm", "state", "add",
                  *fresh["out_state"], *crypto(IPSEC_REQID_OUT),
                  "offload", "packet", "dev", TARGET_WAN_IF, "dir", "out")
    await command(ctx.wan, ctx.session, "ip", "xfrm", "state", "delete",
                  *stale["out_state"])
    ctx.ipsec_peer_objects.remove(("state", stale["out_state"]))
    ctx.ipsec_spis = (spi_out, old_in)


async def _ipsec_traffic(ctx, count):
    """Drive trusted LAN traffic through the WAN VPN and report SEC steering."""
    endpoints = {f"{CLIENT_A}:{PORT_IPSEC}", f"{IPSEC_INNER}:{PORT_IPSEC}"}
    before = {r["cookie"]: int(r["packets"])
              for r in (await ctx.state())["flows"]
              if {r["src"], r["dst"]} == endpoints}
    toenc_before = await sec_counter(ctx.session, ctx.target, TARGET_WAN_IF, "tx toenc")
    todec_before = await sec_counter(ctx.session, ctx.target, TARGET_WAN_IF, "tx todec")
    report = await _exchange(ctx, BY_NAME["a"], peer=IPSEC_INNER,
                             dport=PORT_IPSEC, sport=PORT_IPSEC, count=count,
                             payload_size=IPSEC_PAYLOAD, label="profile_home_ipsec",
                             tolerate_loss=True)
    toenc = await sec_counter(ctx.session, ctx.target, TARGET_WAN_IF, "tx toenc")
    todec = await sec_counter(ctx.session, ctx.target, TARGET_WAN_IF, "tx todec")
    assert ctx.ipsec_echo.sources == {(CLIENT_A, PORT_IPSEC)}, ctx.ipsec_echo.sources
    state = await ctx.state()
    rows = [r for r in state["flows"]
            if {r["src"], r["dst"]} == endpoints]
    return {"echoed": report["echoed"], "state": state,
            "toenc": toenc - toenc_before,
            "todec": todec - todec_before,
            "hardware_packets": {r["cookie"]: int(r["packets"]) - before.get(r["cookie"], 0)
                                 for r in rows},
            "forward": [r for r in rows if r.get("out") == TARGET_WAN_IF
                        and r.get("sa") != "0"],
            "reverse": [r for r in rows if r.get("out") == TARGET_LAN_IF
                        and r.get("in_sa") != "0"]}


def _assert_ipsec(report, count):
    """The asymmetry is the subject. An arriving ESP frame's 5-tuple is the
    tunnel's, so it cannot match a flow entry at all: the inbound SA's own
    classifier entry steers it to SEC on the SPI, and what SEC hands back
    re-enters classification on the offline port rather than on the physical
    one. An entry keyed on the physical port is therefore installed, counted and
    never matched, which is why the reverse direction's own packet counter is
    the oracle rather than the echo."""
    assert report["echoed"] >= count - 2, report
    assert report["forward"], (
        f"the encrypted direction has no hardware flow: {report}")
    assert report["reverse"], (
        f"the decrypted direction has no hardware flow naming its inbound SA: {report}")
    assert len(report["forward"]) == len(report["reverse"]) == 1, report
    assert report["forward"][0]["in_br"] == f"{BRIDGE}.{VID_A}", report
    assert report["reverse"][0]["out_br"] == f"{BRIDGE}.{VID_A}", report
    for row in report["forward"] + report["reverse"]:
        assert report["hardware_packets"][row["cookie"]] >= count - IPSEC_SETUP, report
    # A handful, not `count`: the frames that travelled before the entries
    # existed. On the slow path this counter tracks the transfer one for one.
    assert report["toenc"] <= IPSEC_SETUP, (
        f"{report['toenc']} frames reached SEC through the software path; the "
        f"classifier should have steered all but the first")
    # Zero, not one: inbound steering is on the SPI and needs no first packet
    # through the stack, because the SA's own entry exists before any arrives.
    assert report["todec"] == 0, (
        f"{report['todec']} ESP frames were decrypted from the software path")


# ---- routed multicast ------------------------------------------------------

async def _smcroute(ctx, *oifs):
    """The one place this file speaks to smcroute.

    The routed learner reads ipmr's MFC, and the MFC has no /proc or netlink
    write surface a test could use: an entry is installed by a process holding
    an MRT_INIT socket and by nothing else, so the consumer *is* the control
    plane. Keeping the whole invocation here is what lets it be aligned with
    whatever the learner settles on without touching a case.

    The daemon is started once, with `-N` and a generated config naming exactly
    the interfaces this profile routes between. The default enables every
    multicast-capable interface it can find, and a VIF set that depends on what
    else the image happened to bring up is not a fixture -- a stray VIF shifts
    the index every threshold in the MFC is expressed against. Both outbound
    VLANs are named from the start even though the first route uses one, because
    smcroute matches a phyint by name at startup and a device that had no VIF
    then cannot be added to a route later.

    `remove` before `add` because re-adding an existing (iif, source, group)
    changes nothing: adding an outbound interface means restating the route with
    the whole list, which is the MFC's replication list changing under a live
    daemon rather than at install. With no outbound interfaces the route goes
    and the daemon with it, which is what teardown wants.
    """
    source, group = orchestrator_source(), GROUP
    if not oifs:
        if getattr(ctx, "smcrouted", False):
            # Stopping the daemon removes its routes too. Failure reaches the
            # fixture's aggregate cleanup report without skipping other undo.
            await command(ctx.target, ctx.session, "killall", "smcrouted")
            ctx.smcrouted = False
            await asyncio.sleep(1.0)
        return
    if not getattr(ctx, "smcrouted", False):
        phyints = [TARGET_WAN_IF, ctx.bridge_text[VID_A], ctx.bridge_text[VID_B]]
        written = await ctx.target.fs_write(
            ctx.session, SMCROUTE_CONF,
            "".join(f"phyint {name} enable\n" for name in phyints))
        assert written["errno"] == 0, written
        result = await command(ctx.target, ctx.session, "smcrouted", "-N",
                               "-f", SMCROUTE_CONF, "-l", "notice", check=False)
        assert result["rc"] == 0, result
        ctx.smcrouted = True
        # MRT_INIT, the VIF adds and the registration dump all happen inside the
        # daemon's first second; the adapter's own worker follows them.
        await asyncio.sleep(2.0)
    await command(ctx.target, ctx.session, "smcroutectl", "remove", TARGET_WAN_IF,
                  source, group, check=False)
    result = await command(ctx.target, ctx.session, "smcroutectl", "add",
                           TARGET_WAN_IF, source, group, *oifs, check=False)
    assert result["rc"] == 0, (oifs, result)


async def _routed_installed(ctx, predicate, timeout=20):
    """Short bursts of the stream until the adapter's routed rows satisfy
    `predicate`. A routed group is carried only once Linux has been seen
    forwarding a copy of it to every oif, so a route no frame has used yet
    waits in software as pending-confirm."""
    deadline = asyncio.get_running_loop().time() + timeout
    while True:
        state = await ctx.state()
        if predicate(state["mroute"]):
            return state
        assert asyncio.get_running_loop().time() < deadline, state["mroute"]
        await asyncio.to_thread(_inject_stream, 0.2, 50, PORT_MCAST)
        await asyncio.sleep(0.3)


async def _mroute_row(ctx):
    for row in (await ctx.state())["mroute"]:
        if row["group"] == GROUP and row["src"] == orchestrator_source():
            return row
    return None


def _inject_stream(seconds, pps, port):
    """Put the multicast stream on the WAN wire from the orchestrator.

    A layer-2 send with an explicit interface, because a multicast destination
    has no host route and a layer-3 send would leave by whichever interface the
    kernel likes. The source is pinned because the classifier key is an exact
    (S,G) and so is the MFC entry: a source scapy chose for itself would match
    neither.
    """
    from scapy.all import IP, UDP, Ether, Raw, sendp

    octets = [int(b) for b in GROUP.split(".")]
    mac = "01:00:5e:%02x:%02x:%02x" % (octets[1] & 0x7F, octets[2], octets[3])
    frame = (Ether(dst=mac)
             / IP(src=orchestrator_source(), dst=GROUP, ttl=STREAM_TTL)
             / UDP(sport=port, dport=port) / Raw(b"x" * 512))
    sendp(frame, iface=INJECT_IF, count=int(seconds * pps), inter=1.0 / pps,
          verbose=False)


def _listener_script(client, seconds, port, result):
    """Count the replicas and read their headers back.

    A raw socket rather than a UDP one, and promiscuous, because a routed
    replica is not the frame the source sent: the hardware rebuilt its Ethernet
    header with its oif's own address and decremented the TTL, and those
    two fields are the difference between a replica the classifier made and a
    frame the bridge flooded. Nothing joins the group here -- routed multicast is
    static, and a membership would let a snooping path deliver frames that say
    nothing about the MFC.

    The tag is not read off the frame, and cannot be: a VLAN device is handed
    its frames with the tag already taken off, so a capture on either client
    sees an untagged one. Which device received the copy is the assertion
    instead -- a VLAN device receives nothing that did not carry its tag, and a
    macvlan receives nothing that carried any.

    It writes its answer to a file rather than printing it, because it is
    started detached: see _spawn_listener.
    """
    return f"""
import json, socket, struct, time
raw = socket.socket(socket.AF_PACKET, socket.SOCK_RAW, socket.htons(3))
raw.bind(({client['iface']!r}, 0))
raw.setsockopt(263, 1, struct.pack('IHH8s',
               socket.if_nametoindex({client['iface']!r}), 1, 0, b''))
raw.settimeout(0.2)
group = socket.inet_aton({GROUP!r})
seen, samples = 0, []
deadline = time.monotonic() + {seconds}
while time.monotonic() < deadline:
    try:
        frame = raw.recv(2048)
    except TimeoutError:
        continue
    offset = 14
    if frame[12:14] == b'\\x81\\x00':
        offset = 18
    if frame[offset - 2:offset] != b'\\x08\\x00':
        continue
    if frame[offset + 16:offset + 20] != group:
        continue
    header = offset + (frame[offset] & 15) * 4
    # The port as well as the group: two streams to one group would otherwise
    # be counted as one, and the case that adds an outbound interface is
    # exactly where that would go unnoticed.
    if struct.unpack('!H', frame[header + 2:header + 4])[0] != {port}:
        continue
    seen += 1
    if len(samples) < 4:
        samples.append({{'src_mac': frame[6:12].hex(':'),
                        'dst_mac': frame[0:6].hex(':'),
                        'ttl': frame[offset + 8]}})
raw.close()
with open({result!r}, 'w') as out:
    out.write(json.dumps({{'received': seen, 'samples': samples}}))
"""


async def _spawn_listener(ctx, client, seconds, result):
    """Start one client's counter detached, and return as soon as it is up.

    Several clients listen at once. Start them in the background and read
    their results afterwards, as in _mcast_helpers.spawn_parallel_tcpdumps.
    """
    path = f"/tmp/ask_profile_home_listen_{client['name']}.py"
    staged = (f"import pathlib, subprocess\n"
              f"pathlib.Path({path!r}).write_text("
              f"{_listener_script(client, seconds, PORT_MCAST, result)!r})\n"
              f"pathlib.Path({result!r}).unlink(missing_ok=True)\n"
              f"with open({path + '.log'!r}, 'wb') as log:\n"
              f"    subprocess.Popen(['python3', {path!r}], stdin=subprocess.DEVNULL,\n"
              f"                     stdout=log, stderr=log, start_new_session=True)\n"
              "print('LISTENER-UP')\n")
    started = await _client_python(ctx, client, staged,
                                   label=f"profile_home_listen_{client['name']}",
                                   timeout=30)
    assert "LISTENER-UP" in started.stdout, started.stdout


async def _collect_listener(ctx, client, result, timeout=30):
    """What one detached counter wrote, once it has finished writing it.

    The namespaces share a filesystem -- a network namespace is only the
    network -- so this reads from the LAN VM's ordinary shell rather than
    needing to re-enter the client's.
    """
    deadline = asyncio.get_running_loop().time() + timeout
    while True:
        probe = await lan_run(ctx.lan, f"cat {result} 2>/dev/null; echo", 20)
        body = probe.stdout
        start, end = body.find("{"), body.rfind("}")
        if start >= 0 and end > start:
            return json.loads(body[start:end + 1])
        assert asyncio.get_running_loop().time() < deadline, (
            f"{client['name']}'s multicast counter never wrote {result}: "
            f"{probe.stdout!r}")
        await asyncio.sleep(1.0)


async def _watch_routed(ctx, clients, seconds, label):
    """Run the stream and answer with what every listener saw and what the CPU
    did not see.

    The listeners are started first and left running, and the software receive
    counter is sampled across the injection, because the discriminating oracle
    is the one measured while the frames are in flight. Their window is longer
    than the stream by the time it takes to start them all plus the settling
    pause, so every one of them is counting before the first frame.
    """
    results = {client["name"]: f"/tmp/ask_{label}_{client['name']}.json"
               for client in clients}
    window = seconds + 6.0 + 2.0 * len(clients)
    for client in clients:
        await _spawn_listener(ctx, client, window, results[client["name"]])
    await asyncio.sleep(2.0)
    before = await kernel_rx_packets(ctx.target, ctx.session, TARGET_WAN_IF)
    await asyncio.to_thread(_inject_stream, seconds, STREAM_PPS, PORT_MCAST)
    cpu = await kernel_rx_packets(ctx.target, ctx.session, TARGET_WAN_IF) - before
    observed = {"sent": int(seconds * STREAM_PPS), "cpu": cpu, "clients": {},
                "mroute": await _mroute_row(ctx)}
    for client in clients:
        observed["clients"][client["name"]] = await _collect_listener(
            ctx, client, results[client["name"]], timeout=window + 30)
    return observed


# ---- the fixture -----------------------------------------------------------

@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def homelab(target_agent, lan, request, dmesg_allowlist):
    """The whole homelab profile, built once for the file.

    Module-scoped deliberately: the build is a bridge that takes the LAN port,
    three namespaced clients, a firewall, two tunnels and a multicast routing
    daemon, and the lifecycle cases are only meaningful against a profile that
    has been carrying traffic rather than one built a moment ago.

    Its own aiohttp session, because conftest's is function-scoped and bound to
    the per-test loop. The LAN guest-agent client is session-scoped.

    Two orderings matter. Every device the profile stands on exists before the
    offload policy is applied, because adding an upper to a bound port is a
    configuration change the adapter answers with full invalidation. And the
    DUT's default route is pointed at the orchestrator before anything measures
    a WAN flow, because every WAN case here talks to an address that is
    deliberately off the WAN segment -- which is what gives the default-route
    lifecycle case something to move.
    """
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30)) as session, capture_window(
            target_agent, session, request.node.nodeid, dmesg_allowlist, name="profile-kernel"):
        ctx = Profile()
        ctx.target, ctx.session, ctx.lan = target_agent, session, lan
        ctx.wan = Agent("wan", f"http://{ORCH_IPV4}:9110")
        ctx.sequence, ctx.recovery_console, ctx.proto = 1, None, "udp"
        ctx.echoes = {}
        ctx.ipsec_spis = None
        ctx.ipsec_peer_objects = []
        ctx.ipsec_echo = None
        ctx.smcrouted = False

        stack = TopologyStack()
        cleanup, endpoints = [], []
        ctx.endpoints = endpoints
        console = Console.target(log_path=str(artifact_dir(request.node.nodeid) / "profile-home-uart.log"))
        try:
            initial = await ctx.state()
            ctx.baseline_errors = initial["errors"]
            await asyncio.to_thread(console.login, "root", None)
            ctx.console = console
            # This profile owns the policy, so the boot daemon's catch-all is
            # stopped first and reapplied from here; the init script drains the
            # hardware to an unbound state.
            await console_command(console, "/etc/init.d/ask-flowtable", "stop",
                                  check=False, timeout=45)
            # A known starting point, and the first thing that would be wrong if
            # the boot daemon had not let go: everything this file measures is a
            # delta against an unbound adapter.
            drained = await ctx.wait(lambda s: not s["bindings"] and not s["entries"],
                                     timeout=30)
            assert drained["invalidated"] == drained["fatal"] == 0, drained
            printk = (await read(ctx.target, ctx.session,
                                 "/proc/sys/kernel/printk")).split()
            await command(ctx.target, ctx.session, "sysctl", "-w",
                          "kernel.printk=1 4 1 7")
            # Restored by hand at the end of teardown rather than from the
            # cleanup list: console calls come after that list, and a printk
            # landing mid-marker is what breaks them.
            ctx.printk = " ".join(printk[:4])
            await command(ctx.target, ctx.session, "modprobe", "xt_tcpudp")
            old_acct = (await read(ctx.target, ctx.session,
                                   "/proc/sys/net/netfilter/nf_conntrack_acct")).strip()
            await command(ctx.target, ctx.session, "sysctl", "-w",
                          "net.netfilter.nf_conntrack_acct=1")
            cleanup.append((ctx.target, ["sysctl", "-w",
                                         f"net.netfilter.nf_conntrack_acct={old_acct}"]))
            for key in ("net.ipv4.ip_forward", "net.ipv6.conf.all.forwarding"):
                previous = (await command(ctx.target, ctx.session, "sysctl", "-n",
                                          key))["stdout"].strip()
                cleanup.append((ctx.target, ["sysctl", "-w", f"{key}={previous}"]))
                await command(ctx.target, ctx.session, "sysctl", "-w", f"{key}=1")

            # ---- the WAN: static v4 and v6, no session ----
            addresses = json.loads((await command(ctx.target, ctx.session, "ip", "-j",
                                                  "-4", "addr", "show",
                                                  "dev", TARGET_WAN_IF))["stdout"])[0]
            wan_info = next(a for a in addresses["addr_info"]
                            if a["family"] == "inet")
            ctx.wan_address = wan_info["local"]
            ctx.wan_prefixlen = wan_info["prefixlen"]
            ctx.dut_wan_mac = (await read(ctx.target, ctx.session,
                                          f"/sys/class/net/{TARGET_WAN_IF}/address")).strip()
            orch = json.loads((await command(ctx.wan, ctx.session, "ip", "-j", "-4",
                                             "addr"))["stdout"])
            ctx.wan_if = next(i["ifname"] for i in orch
                              if any(a.get("local") == orchestrator_source()
                                     for a in i["addr_info"]))
            ctx.wan_mac = json.loads((await command(
                ctx.wan, ctx.session, "ip", "-j", "link", "show",
                "dev", ctx.wan_if))["stdout"])[0]["address"]
            # The image's own v6 address on the WAN port, restated rather than
            # assumed, and its counterpart on the orchestrator. Both nodad: DAD
            # leaves a static address tentative for about a second and a half
            # and the first flow silently fails to come up.
            await command(ctx.target, ctx.session, "ip", "-6", "addr", "add",
                          f"{DUT_IPV6_WAN}/64", "dev", TARGET_WAN_IF, "nodad",
                          check=False)
            await command(ctx.wan, ctx.session, "ip", "-6", "addr", "add",
                          f"{WAN_IPV6}/64", "dev", ctx.wan_if, "nodad", check=False)
            cleanup.append((ctx.wan, ["ip", "-6", "addr", "del", f"{WAN_IPV6}/64",
                                      "dev", ctx.wan_if]))

            # The off-segment address every WAN case talks to, and the default
            # route that reaches it. The image's own default is saved first: it
            # is what the lifecycle case moves back to.
            routes = json.loads((await command(ctx.target, ctx.session, "ip", "-j",
                                               "route", "show", "default"))["stdout"])
            ctx.first_gateway = next(
                (r["gateway"] for r in routes if r.get("dev") == TARGET_WAN_IF), None)
            # The test image boots the WAN port with a static management address
            # and no gateway; a deployment's WAN always has one, and the
            # lifecycle case needs a distinct next hop to move away from and back
            # to. When none is present, synthesize an on-link first hop -- the
            # first host of the WAN subnet, skipping the port's own address. It
            # carries no traffic (every WAN flow uses second_gateway below), so
            # it only has to be a valid on-link address; cleanup then removes the
            # route rather than restoring one that was never there.
            synthetic_default = ctx.first_gateway is None
            if synthetic_default:
                wan_net = ipaddress.ip_interface(
                    f"{ctx.wan_address}/{ctx.wan_prefixlen}").network
                ctx.first_gateway = str(next(
                    h for h in wan_net.hosts() if str(h) != ctx.wan_address))
            ctx.second_gateway = orchestrator_source()
            await command(ctx.wan, ctx.session, "ip", "addr", "add",
                          f"{INTERNET}/32", "dev", "lo", check=False)
            cleanup.append((ctx.wan, ["ip", "addr", "del", f"{INTERNET}/32",
                                      "dev", "lo"]))
            if synthetic_default:
                cleanup.append((ctx.target, ["ip", "route", "del", "default",
                                             "dev", TARGET_WAN_IF]))
            else:
                cleanup.append((ctx.target, ["ip", "route", "replace", "default",
                                             "via", ctx.first_gateway, "dev",
                                             TARGET_WAN_IF]))
            await command(ctx.target, ctx.session, "ip", "route", "replace", "default",
                          "via", ctx.second_gateway, "dev", TARGET_WAN_IF)

            # ---- the LAN: one bridge, three VLANs, three clients ----
            await _bridge_lan(ctx, stack)
            await _build_clients(ctx)
            stack.push(lambda: _drop_clients(ctx))
            # Pinned in both families, so admission never races ARP or ND: a
            # direction offered before its neighbour resolves is declined, and
            # nothing re-offers it until the next packet.
            for client in CLIENTS:
                dev = ctx.bridge_text[client["vid"] or VID_A]
                await command(ctx.target, ctx.session, "ip", "neigh", "replace",
                              client["ip"], "lladdr", client["mac"], "nud",
                              "stale", "dev", dev)
                cleanup.append((ctx.target, ["ip", "neigh", "del", client["ip"],
                                             "dev", dev]))
                if client["ip6"]:
                    await command(ctx.target, ctx.session, "ip", "-6", "neigh",
                                  "replace", client["ip6"], "lladdr", client["mac"],
                                  "nud", "stale", "dev", dev)
                    cleanup.append((ctx.target, ["ip", "-6", "neigh", "del",
                                                 client["ip6"], "dev", dev]))
            # The way back to each client subnet, for the policer's blast and
            # for anything the orchestrator initiates.
            for subnet in (SUBNET_A, SUBNET_B, SUBNET_C):
                await command(ctx.wan, ctx.session, "ip", "route", "replace", subnet,
                              "via", ctx.wan_address, "dev", ctx.wan_if)
                cleanup.append((ctx.wan, ["ip", "route", "del", subnet,
                                          "dev", ctx.wan_if]))
            await _firewall(ctx, cleanup)

            # Packet-offloaded SAs require their outer address on the physical
            # offload port. eth3 is now a bridge slave, so the VPN belongs on
            # the plain WAN uplink, as in a normal homelab deployment. Its
            # software peer has an inner address of its own on the WAN host.
            await command(ctx.wan, ctx.session, "ip", "addr", "add",
                          f"{IPSEC_INNER}/32", "dev", "lo")
            cleanup.append((ctx.wan, ["ip", "addr", "del", f"{IPSEC_INNER}/32",
                                      "dev", "lo"]))

            # ---- the 6o4 tunnel, which is where VLAN A's IPv6 comes from ----
            ctx.shape = Shape("6o4", PORT_V6, PORT_V6)
            # A device name of this profile's own, so a leaked one from the
            # tunnel file cannot be mistaken for it.
            ctx.shape.device = TUNNEL_DEVICE
            # Shape takes the outer endpoints from ASK_TARGET_IP and ASK_WAN_IP
            # while everything else here reads them off the interfaces. A stale
            # override makes the tunnel come up against an address nobody holds,
            # which surfaces three steps later as an unreachable peer.
            assert ctx.shape.outer[0] == ctx.wan_address, (
                "ASK_TARGET_IP does not name the address on the DUT's WAN port",
                ctx.shape.outer, ctx.wan_address)
            assert ctx.shape.outer[1] == orchestrator_source(), (
                "ASK_WAN_IP and ASK_WAN_IPERF_IP name different orchestrator "
                "addresses; the tunnel would be built against one and measured "
                "against the other", ctx.shape.outer, orchestrator_source())
            ctx.lan_address = CLIENT_A6
            await _outer_segment(ctx, cleanup)
            await _dut_tunnel(ctx, cleanup)
            await _orchestrator_tunnel(ctx, cleanup)
            # VLAN A tells its hosts the tunnel's MTU, as a 6in4 LAN should,
            # sparing them a Packet Too Big round trip; admission does not
            # read it (see test_mtu_bound). The VLAN is this fixture's and
            # goes with it, so nothing is restored.
            vlan_a_mtu = f"/proc/sys/net/ipv6/conf/{ctx.bridge_text[VID_A]}/mtu"
            assert (await ctx.target.fs_write(ctx.session, vlan_a_mtu,
                                              str(ctx.shape.mtu)))["errno"] == 0

            loop = asyncio.get_running_loop()
            # No endpoint on the throughput port: that case starts a real
            # iperf3 server on the same address, and an echo alongside it would
            # be a second thing bound to one number for no reason.
            for port in (PORT_A, PORT_B, PORT_C):
                transport, echo = await loop.create_datagram_endpoint(
                    _Echo, local_addr=(INTERNET, port))
                endpoints.append(transport)
                ctx.echoes[port] = echo
            transport, echo = await loop.create_datagram_endpoint(
                _Echo, local_addr=(ctx.shape.inner_orch, PORT_V6),
                family=socket.AF_INET6)
            endpoints.append(transport)
            ctx.echoes[PORT_V6] = echo

            await _reachable(ctx, BY_NAME["a"], INTERNET)
            await _reachable(ctx, BY_NAME["b"], INTERNET)
            await _reachable(ctx, BY_NAME["c"], INTERNET)
            await _reachable(ctx, BY_NAME["a"], ctx.shape.inner_orch)

            # ---- the offload policy, last: every device it binds exists ----
            await apply(console, _policy(), r=ctx)
            await ctx.wait(lambda s: s["bindings"] == 2)
            ctx.record("home-fixture", {
                "wan": ctx.wan_address, "internet": INTERNET,
                "gateways": [ctx.first_gateway, ctx.second_gateway],
                "bridge": ctx.bridge_text,
                "ipsec_outer": [ctx.wan_address, orchestrator_source()],
                "tunnel": _tunnel_text(ctx.shape), "clients": CLIENTS,
                "initial": initial})
            yield ctx
        finally:
            for transport in endpoints:
                transport.close()
            failures = []

            async def undo(step, label, timeout=60):
                """Every undo runs, whatever the one before it did.

                A teardown step that raises takes the whole rest of the cleanup
                with it, and the steps that matter most -- the bridge giving the
                LAN port its address back -- are at the end. `check=False` is
                not enough on its own: the agent answers 501 for a binary the
                image does not have and aiohttp raises that, and a console call
                after a failed login raises on its output marker before it ever
                looks at `check`.
                """
                try:
                    async with asyncio.timeout(timeout):
                        await step()
                except (Exception, pytest.fail.Exception) as error:
                    failures.append(f"{label}: {error}")

            async def target(*argv, check=True):
                return await command(ctx.target, ctx.session, *argv, check=check)

            await undo(lambda: stop(console), "ask-flowtable stop")
            # Belt and braces: the multicast case stops its own daemon, and a
            # case that failed part-way through may not have.
            await undo(lambda: _smcroute(ctx), "smcroute")
            await undo(lambda: remove_qdisc(console, TARGET_WAN_IF, "clsact", "clsact"), "qdisc")
            await undo(lambda: target("ip", "xfrm", "policy", "flush"), "xfrm policy")
            await undo(lambda: target("ip", "xfrm", "state", "flush"), "xfrm state")
            async def remove_inner_route():
                await target("ip", "route", "del", f"{IPSEC_INNER}/32", check=False)
                routes = await target("ip", "-j", "route", "show", "exact", f"{IPSEC_INNER}/32")
                assert not json.loads(routes["stdout"]), routes

            await undo(remove_inner_route, "inner route")
            await undo(lambda: _clear_peer_ipsec(ctx), "WAN peer xfrm", timeout=None)
            await undo(lambda: target("conntrack", "-F"), "conntrack")
            for agent, argv in reversed(cleanup):
                await undo(lambda a=agent, v=argv: command(a, ctx.session, *v), " ".join(argv))
            await undo(lambda: stack.teardown("profile-homelab"), "topology", timeout=None)
            try:
                await undo(lambda: console_command(console, "rm", "-f", CONFIG), "config")
                # Last, because everything above drives the console.
                if getattr(ctx, "printk", None):
                    await undo(lambda: target("sysctl", "-w",
                                              f"kernel.printk={ctx.printk}"), "printk")
            finally:
                console.close()
            assert not failures, failures


class _Echo(asyncio.DatagramProtocol):
    """Echoes every datagram back and records where it came from: under
    masquerade that is the translated endpoint, which is the wire-level proof of
    the rewrite."""

    def __init__(self):
        self.packets = 0
        self.sources = set()

    def connection_made(self, transport):
        self.transport = transport

    def datagram_received(self, data, addr):
        self.packets += 1
        self.sources.add((addr[0], addr[1]))
        self.transport.sendto(data, addr)


# ---- traffic ---------------------------------------------------------------

async def test_trusted_vlan_reaches_the_wan(homelab, splat_window):
    """The ordinary case: untagged on the wire, bridged, masqueraded, routed out
    of the WAN port through the default route.

    A VLAN device is in the path and the wire carries no tag, because the port
    is untagged for the PVID -- the configuration OpenWrt ships and the one a
    derivation that stopped at the netdevs would push a tag for.
    """
    ctx = homelab
    client = BY_NAME["a"]
    forward, reverse = await _accounted(ctx, client, peer=INTERNET, dport=PORT_A,
                                        sport=PORT_A, label="trusted")
    _assert_wan(ctx, forward, reverse, client=client)
    # What the far endpoint observed, which is the only evidence the rewrite
    # reached the wire rather than only the rule.
    assert {address for address, _port in ctx.echoes[PORT_A].sources} == \
        {ctx.wan_address}, ctx.echoes[PORT_A].sources


async def test_tagged_vlans_reach_the_wan(homelab, splat_window):
    """The IoT and guest VLANs, which differ from the trusted one in one bridge
    membership each and must differ on the wire in exactly one tag each.

    Same bridge, same port, same uplink. Running both in one case is deliberate:
    two tagged VLANs on one port share a bridge FDB and a classifier, and a
    derivation that keyed on the port rather than on the VLAN would forward both
    into whichever was admitted first.
    """
    ctx = homelab
    for name, port in (("b", PORT_B), ("c", PORT_C)):
        client = BY_NAME[name]
        forward, reverse = await _accounted(ctx, client, peer=INTERNET, dport=port,
                                            sport=port, label=f"tagged-{name}")
        _assert_wan(ctx, forward, reverse, client=client)
        assert forward["in_br"] == ctx.bridge_text[client["vid"]], forward


async def test_inter_vlan_is_a_hairpin(homelab, splat_window):
    """Trusted to IoT: across two VLANs, and on this bench in and out of one
    physical port.

    The product does not deploy that way. A gateway with five ports puts two
    VLANs on two ports and the path is the ordinary two-port one; carrying
    every VLAN on a single trunk is a shape this product is not built for. The
    bench has one LAN port with carrier, so inter-VLAN routing here is
    necessarily a hairpin, and the case is written that way because it is the
    only way this rig can exercise VLAN-to-VLAN routing at all.

    What it does exercise is real and is the most to get wrong at once. The
    frame arrives untagged on the port, the bridge puts it in the trusted VLAN,
    the DUT routes it into the IoT VLAN, and it leaves with a tag on. The
    bridge is on both sides of it, so a derivation that named the bridge once
    would lose half of it, and the tag is pushed on egress only.

    The other half of the policy is asserted here too: the IoT VLAN may not
    open a connection back. That needs a listener on the far side and the
    firewall's own counter, because an unanswered datagram looks identical
    whether the rule dropped it or nothing was bound to the port -- an
    assertion on the echo alone could never fail. With a listener running and
    the drop counter moving by the burst, the refusal is the thing measured.
    """
    ctx = homelab
    trusted, iot = BY_NAME["a"], BY_NAME["b"]
    # Each client answers on one port, so each direction has something to talk
    # to: the hairpin is allowed and must be echoed, the reverse is refused and
    # must not be, and both have a live peer.
    listeners = TopologyStack()
    try:
        listeners.push(await _udp_listener(ctx, iot, PORT_HAIRPIN,
                                          "profile_home_hairpin"))
        listeners.push(await _udp_listener(ctx, trusted, PORT_REFUSED,
                                          "profile_home_refused"))
        await asyncio.sleep(1.0)
        forward, reverse = await _accounted(ctx, trusted, peer=iot["ip"],
                                            dport=PORT_HAIRPIN, sport=PORT_HAIRPIN,
                                            label="hairpin")
        # One port, both ways, and the tag only on the side that carries it.
        assert forward["in"] == forward["out"] == TARGET_LAN_IF, forward
        assert reverse["in"] == reverse["out"] == TARGET_LAN_IF, reverse
        assert forward["in_br"] == ctx.bridge_text[VID_A], forward
        assert forward["out_br"] == ctx.bridge_text[VID_B], forward
        assert forward["in_vlan"] == "-" and forward["out_vlan"] == str(VID_B), forward
        assert reverse["in_vlan"] == str(VID_B) and reverse["out_vlan"] == "-", reverse
        # Not translated: masquerade is scoped to the WAN port, and an
        # inter-VLAN flow that picked it up would hide the client from its peer.
        assert forward["new_src"] == forward["src"], forward

        # And the direction the firewall refuses. A fresh port, so this is a new
        # connection rather than the reply to the one above, and a listener is
        # bound on the far side so silence can only mean the rule.
        before = await ctx.state()
        drops_before = await _firewall_drops(ctx, ctx.bridge_text[VID_B],
                                             ctx.bridge_text[VID_A])
        refused = await _exchange(ctx, iot, peer=trusted["ip"], dport=PORT_REFUSED,
                                  sport=PORT_REFUSED, count=8, label="refused",
                                  tolerate_loss=True)
        drops = await _firewall_drops(ctx, ctx.bridge_text[VID_B],
                                      ctx.bridge_text[VID_A]) - drops_before
        after = await ctx.state()
        assert refused["echoed"] == 0, refused
        assert drops >= 8, (drops, refused)
        # Other clients, including Wi-Fi, can add flows during this window.
        # Require neither direction of the refused connection in hardware;
        # the global install counter cannot attribute a flow to this burst.
        refused_end = f"{iot['ip']}:{PORT_REFUSED}"
        assert not [f for f in after["flows"]
                    if refused_end in (f["src"], f["dst"],
                                       f["new_src"], f["new_dst"])], after
        ctx.record("home-hairpin", {"forward": forward, "reverse": reverse,
                                    "refused": refused, "drops": drops,
                                    "before": before, "after": after})
    finally:
        await listeners.teardown("hairpin listeners")


async def test_ipsec_carries_both_directions(homelab, splat_window):
    """A packet-offload tunnel to a WAN peer, both directions in
    hardware.

    Inbound is not the mirror of outbound and the asymmetry is the whole
    subject: an arriving ESP frame's 5-tuple is the tunnel's, so it cannot match
    a flow entry, and what SEC hands back re-enters classification on the
    offline port. The reverse direction's own packet counter is therefore the
    oracle -- it read zero for whole transfers while the echo still worked.
    """
    ctx = homelab
    spi_out = 0x0A980000 | (secrets.randbelow(0xFFFF) + 1)
    await _install_ipsec(ctx, spi_out, spi_out ^ 0x8000)
    await _start_ipsec_echo(ctx)
    report = await _ipsec_traffic(ctx, 60)
    ctx.record("home-ipsec", report)
    _assert_ipsec(report, 60)


async def test_tunnel_gives_the_trusted_vlan_ipv6(homelab,
                                                                  splat_window):
    """6o4 to a broker on the WAN side, which is where VLAN A's IPv6 comes from.

    The forward direction inserts the outer IPv4 header and the reverse strips
    it, so the adapter names the tunnel on exactly those two and on neither LAN
    half -- while both still name the physical ports, because a tunnel device
    never becomes one. The LAN side of the same connection is bridged and
    untagged, which is the combination this profile adds over the tunnel file's.
    Over TCP: a UDP upload into the tunnel is Linux's, the LAN port delivering a
    full frame, and TCP's segments are clamped to fit.
    """
    ctx = homelab
    forward, reverse = await _tunnel_tcp(ctx, BY_NAME["a"], "tunnel6-tcp")
    expected = _tunnel_text(ctx.shape)
    assert forward["out_tnl"] == expected and forward["in_tnl"] == "-", forward
    assert reverse["in_tnl"] == expected and reverse["out_tnl"] == "-", reverse
    assert forward["in"] == TARGET_LAN_IF and forward["out"] == TARGET_WAN_IF, forward
    assert reverse["in"] == TARGET_WAN_IF and reverse["out"] == TARGET_LAN_IF, reverse
    assert forward["family"] == reverse["family"] == "6", (forward, reverse)
    assert forward["in_br"] == ctx.bridge_text[VID_A], forward
    # The forward direction leaves by the tunnel, so it carries the tunnel's
    # MTU: 1500 less the outer IPv4 header, and nothing here computed that.
    assert int(forward["mtu"]) == ctx.shape.mtu, forward


async def test_routed_multicast_replicates(homelab, splat_window):
    """A stream routed from the WAN port into two VLANs on the LAN port.

    The routed learner reads ipmr's MFC, where every fact the bridged learner
    has to recover from traffic is already stated: mfc_origin and mfc_mcastgrp
    are an exact (S,G), mfc_parent names the ingress, and ttls[] is the
    replication list. So there is nothing to learn. What the MFC cannot say is
    whether the firewall forwards the stream, so an entry is carried once
    Linux has been seen forwarding a copy to each oif, and the row says
    pending-confirm until then.

    Four oracles. `ip mroute show` reports offload, which is the kernel's own
    view of what a driver claimed. The adapter's row says `installed`, which is
    the classifier holding the key. The DUT's software receive counter says the
    stream never reached the CPU. And the replicas themselves carry the egress
    port's MAC and one less TTL than was sent, which is the routing the hardware
    did on the way through -- a flooded frame would carry the source's own
    header untouched.

    Then a second outbound interface is added and both VLANs receive, which is
    the replication list changing under a live entry rather than at install.
    """
    ctx = homelab
    trusted, iot = BY_NAME["a"], BY_NAME["b"]
    try:
        # The agent answers 501 for a binary it does not have, and aiohttp
        # raises that rather than returning it, so the absence is caught rather
        # than read.
        try:
            await _smcroute(ctx, ctx.bridge_text[VID_A])
        except aiohttp.ClientResponseError as error:
            if error.status != 501:
                raise
            pytest.skip("smcroute is not in the DUT image; add `smcroute` to "
                        "IMAGE_INSTALL")
        # The entry is installed by the daemon and adopted by the learner, and
        # carried once a few frames have been seen forwarded; settle that
        # before the stream starts, or the first second of it is measured
        # against a route that is still pending.
        await _routed_installed(ctx, lambda rows: any(
            r["group"] == GROUP and r["state"] == "installed" for r in rows))
        observed = await _watch_routed(ctx, [trusted], STREAM_S, "profile_home_mcast")
        ctx.record("home-mroute-one", observed)
        sent = observed["sent"]
        listener = observed["clients"]["a"]
        assert listener["received"] >= sent * 0.95, (listener, sent)
        row = observed["mroute"]
        assert row and row["state"] == "installed", row
        assert row["in"] == TARGET_WAN_IF, row
        assert ctx.bridge_text[VID_A] in row["oifs"], row
        assert int(row["packets"]) > 0, (
            "the entry is installed but the classifier matched nothing: the "
            f"stream is going past it, {row}")
        mroute = await command(ctx.target, ctx.session, "ip", "mroute", "show")
        assert any(GROUP in line and "offload" in line
                   for line in mroute["stdout"].splitlines()), mroute["stdout"]
        assert observed["cpu"] < sent * 0.05, (
            f"{observed['cpu']} of {sent} frames reached the DUT's CPU; the "
            f"stream is being replicated in software")
        # The replica is not the frame the source sent: the address of the
        # VLAN device ipmr sends it through is on it -- which that device took
        # from the bridge, not the port it leaves by -- and the TTL is one
        # lower, which is the routing the hardware did on the way through. A
        # flooded copy would carry neither.
        oif_mac = {vid: (await read(ctx.target, ctx.session,
                                    f"/sys/class/net/{ctx.bridge_text[vid]}/address")).strip()
                   for vid in (VID_A, VID_B)}
        for sample in listener["samples"]:
            assert sample["src_mac"] == oif_mac[VID_A], (sample, oif_mac)
            assert sample["ttl"] == STREAM_TTL - 1, sample

        # A second outbound interface: the IoT VLAN joins the same group.
        await _smcroute(ctx, ctx.bridge_text[VID_A], ctx.bridge_text[VID_B])
        # The new oif is confirmed from the stream like the first was.
        await _routed_installed(ctx, lambda rows: any(
            r["group"] == GROUP and r["state"] == "installed"
            and ctx.bridge_text[VID_B] in r["oifs"] for r in rows))
        both = await _watch_routed(ctx, [trusted, iot], STREAM_S,
                                   "profile_home_mcast_two")
        ctx.record("home-mroute-two", both)
        for name in ("a", "b"):
            assert both["clients"][name]["received"] >= both["sent"] * 0.95, \
                (name, both)
        # One entry, two egress framings. The IoT client's device is a VLAN
        # device and receives nothing that did not carry its tag, so its count
        # is the assertion that the copy for that VLAN was tagged -- and the
        # trusted client's macvlan receives nothing that carried one.
        for sample in both["clients"]["b"]["samples"]:
            assert sample["src_mac"] == oif_mac[VID_B], (sample, oif_mac)
            assert sample["ttl"] == STREAM_TTL - 1, sample
        assert both["cpu"] < both["sent"] * 0.05, both
    finally:
        # Takes the route and the daemon with it: nothing else in the profile
        # wants a VIF set, and the next case's counters should not be measured
        # against a stream this one is still routing.
        await _smcroute(ctx)


async def test_mixed_traffic_survives_rekey(homelab, splat_window):
    """Keep WAN, VLANs, IPv6 tunnel and multicast busy through an IPsec rekey."""
    ctx = homelab
    spi = 0x0A990000 | (secrets.randbelow(0x7FFF) + 1)
    await _install_ipsec(ctx, spi, spi ^ 0x8000)
    await _start_ipsec_echo(ctx)
    paths = [(BY_NAME[name], INTERNET, port) for name, port in
             (("a", PORT_A), ("b", PORT_B), ("c", PORT_C))]
    paths += [(BY_NAME["a"], ctx.shape.inner_orch, PORT_V6),
              (BY_NAME["a"], IPSEC_INNER, PORT_IPSEC)]
    flows = [{"id": ident, "proto": "udp", "sport": port, "netns": client["netns"],
              "lan": _source_address(client, destination), "connect_ip": destination,
              "connect_port": port} for ident, (client, destination, port) in enumerate(paths)]
    ids = list(range(len(flows)))
    proxy = SimpleNamespace(lan=ctx.lan, lan_ip=CLIENT_A, record=ctx.record,
                            target=ctx.target, session=ctx.session)

    def pairs(state):
        found = []
        for index, flow in enumerate(flows):
            lan = f"{_bracketed(flow['lan'])}:{flow['sport']}"
            remote = f"{_bracketed(flow['connect_ip'])}:{flow['connect_port']}"
            if index == 3:
                # A UDP upload into the tunnel is Linux's: the LAN port takes
                # a full frame and the tunnel carries 1480. The download alone.
                assert not [f for f in state["flows"] if f["src"] == lan], state["flows"]
                reverse = [f for f in state["flows"] if f["src"] == remote and f["new_dst"] == lan]
                assert len(reverse) == 1, (flow, state["flows"])
                found.append((None, reverse[0]))
            else:
                found.append(_directions(state["flows"], _bracketed(flow["lan"]), flow["sport"],
                                         _bracketed(flow["connect_ip"]), flow["connect_port"]))
        return found

    try:
        await _smcroute(ctx, ctx.bridge_text[VID_A], ctx.bridge_text[VID_B])
        await _routed_installed(ctx, lambda rows: any(
            row["group"] == GROUP and row["state"] == "installed" for row in rows))
        async with peer(proxy, flows, lease=240) as p:
            for _ in range(12):
                await p.batch(ids, 32, 0.02)
                before = await ctx.state()
                try:
                    original = pairs(before)
                    break
                except AssertionError:
                    continue
            else:
                pytest.fail(f"mixed profile did not admit every direction: {before}")
            for index, (forward, reverse) in enumerate(original[:3]):
                _assert_wan(ctx, forward, reverse, client=paths[index][0])
            assert original[3][1]["in_tnl"] == _tunnel_text(ctx.shape), original[3]
            assert original[4][0]["sa"] != "0" and original[4][1]["in_sa"] != "0", original[4]
            await p.rpc("start", ids, count=0, interval=0.01, allow_loss=True, udp_timeout=0.1)
            multicast = asyncio.create_task(_watch_routed(ctx, [BY_NAME["a"], BY_NAME["b"]],
                                                        60, "profile_home_mixed"))
            try:
                await asyncio.sleep(25)
                await _rekey_ipsec(ctx, spi + 0x10000)
                observed = await multicast
                after = await ctx.state()
            finally:
                if not multicast.done():
                    # The bounded stream and listeners finish before cleanup.
                    await multicast
                reports = await p.rpc("stop", ids)
                ctx.record("home-mixed-traffic", reports)
            current = pairs(after)
            for ident, report in reports.items():
                assert report["seconds"] >= 60 and report["received"] >= 3000, (ident, report)
                assert report["lost"] <= (20 if int(ident) == 4 else 0), (ident, report)
            for old_pair, new_pair in zip(original[:4], current[:4]):
                for old, new in zip(old_pair, new_pair):
                    if old is None:
                        continue
                    assert new["cookie"] == old["cookie"], (old, new)
                    assert int(new["packets"]) - int(old["packets"]) >= 3000, (old, new)
            assert current[4][0]["sa"] != original[4][0]["sa"], (original[4], current[4])
            counts = {row["cookie"]: int(row["packets"]) for row in current[4]}
            await p.batch([4], 128, 0.02)
            final = await ctx.state()
            for row in pairs(final)[4]:
                assert int(row["packets"]) - counts[row["cookie"]] >= 120, (counts, row)
            assert final["errors"] == ctx.baseline_errors and final["fatal"] == final["quarantine"] == 0, final
            assert observed["mroute"]["state"] == "installed", observed
            assert observed["cpu"] < observed["sent"] * 0.05, observed
            for report in observed["clients"].values():
                assert report["received"] >= observed["sent"] * 0.95, observed
                assert all(sample["ttl"] == STREAM_TTL - 1 for sample in report["samples"]), report
            ctx.record("home-mixed-hardware", {"before": before, "after": after,
                                               "final": final, "multicast": observed})
    finally:
        await _smcroute(ctx)


async def test_ingress_policer_holds_the_rate(homelab, splat_window):
    """A port-wide meter on the uplink, as a tc filter.

    `skip_sw` is what makes this a hardware assertion rather than a rate
    measurement: the filter is offloaded or it does not exist, and `tc filter
    show` reporting `in_hw` is the kernel agreeing that a driver took it. The
    rate that follows is then the hardware's, and the profile's own colour
    counters are what the drops are read from -- green is enqueued, yellow and
    red are dropped, so every frame the meter saw is the sum of the three.

    The 2 Gbit/s cap is below line rate and above software forwarding capacity.
    Received throughput near that cap, in_hw and accounted drops prove both
    enforcement and hardware forwarding under the complete profile.
    """
    ctx = homelab
    client = BY_NAME["a"]

    async def tc(*argv, check=True):
        """`tc` is not in the agent's argv allowlist -- argv[0] is the whole
        gate -- so the filter is installed on the console the fixture holds."""
        return await console_command(ctx.console, "tc", *argv, check=check, timeout=30)

    await tc("qdisc", "del", "dev", TARGET_WAN_IF, "clsact", check=False)
    await tc("qdisc", "add", "dev", TARGET_WAN_IF, "clsact")
    try:
        await tc("filter", "add", "dev", TARGET_WAN_IF, "ingress", "matchall",
                 "skip_sw", "action", "police", "rate", f"{POLICE_RATE_MBIT}mbit",
                 "burst", POLICE_BURST, "conform-exceed", "drop")
        shown = await tc("-s", "filter", "show", "dev", TARGET_WAN_IF, "ingress")
        assert "in_hw" in shown["stdout"], shown["stdout"]
        assert "skip_sw" in shown["stdout"], shown["stdout"]
        dropped_before = _police_drops(shown["stdout"])

        server = f'''
import json, subprocess
result = subprocess.run(['iperf3', '-s', '-1', '-B', {client['ip']!r},
                         '-p', {str(PORT_POLICE)!r}, '-J'],
                        capture_output=True, text=True, timeout=60)
print(json.dumps({{'rc': result.returncode, 'stdout': result.stdout}}))
'''
        listener = asyncio.create_task(_client_python(
            ctx, client, server, label="profile_home_police_server", timeout=90))
        await asyncio.sleep(1.5)
        blast = await asyncio.create_subprocess_exec(
            "iperf3", "-c", client["ip"], "-u", "-b", f"{POLICE_OFFERED_MBIT}M",
            "-l", "1400", "-t", "5", "-p", str(PORT_POLICE), "-J",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        stdout, stderr = await asyncio.wait_for(blast.communicate(), 60)
        result = await listener
        assert result.rc == 0, result.stdout
        report = json.loads(result.stdout.strip().splitlines()[-1])
        assert report["rc"] == 0, (report, stderr[:400])
        server_report = json.loads(report["stdout"])
        client_report = json.loads(stdout)
        received = server_report["end"]["sum_received"]
        offered = client_report["end"]["sum_sent"]
        shown = await tc("-s", "filter", "show", "dev", TARGET_WAN_IF, "ingress")
        dropped = _police_drops(shown["stdout"]) - dropped_before
        ctx.record("home-policer", {"offered": offered, "received": received,
                                    "dropped": dropped, "filter": shown["stdout"],
                                    "server_report": server_report,
                                    "client_report": client_report})
        cap = POLICE_RATE_MBIT * 1e6
        assert offered["bits_per_second"] > cap * 2, (
            "the orchestrator could not offer enough to exercise the meter", offered)
        # A token bucket drops rather than queues, so the received rate lands at
        # or just under the cap; the margin is the burst draining at the start.
        assert received["bits_per_second"] <= cap * 1.15, (received, cap)
        assert received["bits_per_second"] >= cap * 0.8, (received, cap)
        # And the meter says it did it. Nothing else on this port dropped
        # anything, so the filter's own red count has to account for the loss.
        assert dropped > 0, (dropped, shown["stdout"])
        assert dropped >= received["lost_packets"] * 0.5, (dropped, received)
    finally:
        await tc("qdisc", "del", "dev", TARGET_WAN_IF, "clsact", check=False)


def _police_drops(text):
    """The police action's dropped count, from `tc -s filter show`.

    The driver reports green, yellow and red through flow_stats_update() as
    packets and drops, and red is what the profile is programmed to drop. tc
    prints a stats block for the filter and another for the action, so the
    larger of the two is the one that has the driver's numbers in it.
    """
    return max((int(value) for value in re.findall(r"\(dropped (\d+)", text)),
               default=0)


async def test_throughput(homelab, splat_window):
    """What the profile forwards when nothing is in its way.

    A number below the ceiling means the CPU carried it, because the CPU cannot
    carry this much: the point is the floor rather than the measurement, and it
    runs against the same bridged, firewalled, masqueraded profile as everything
    above rather than a bare NAT path. A floor needs a steady-state sample, not
    a long one: five measured seconds after a two-second ramp.
    """
    ctx = homelab
    client = BY_NAME["a"]
    server = await asyncio.create_subprocess_exec(
        "iperf3", "-s", "-1", "-B", INTERNET, "-p", str(PORT_RATE), "-J",
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    try:
        await asyncio.sleep(0.3)
        assert server.returncode is None, "the endpoint iperf3 did not start"
        script = f'''
import json, subprocess
argv = ['iperf3', '-c', {INTERNET!r}, '-B', {client['ip']!r}, '-p', {str(PORT_RATE)!r},
        '-P', '4', '-t', '5', '-O', '2', '-Z', '-J']
result = subprocess.run(argv, capture_output=True, text=True, timeout=30)
print(json.dumps({{'rc': result.returncode, 'stdout': result.stdout}}))
'''
        result = await _client_python(ctx, client, script, label="profile_home_rate",
                                      timeout=70)
        assert result.rc == 0, result.stdout
        report = json.loads(result.stdout.strip().splitlines()[-1])
        stdout, _ = await asyncio.wait_for(server.communicate(), 15)
        measured = json.loads(stdout)["end"]["sum_received"]
        ctx.record("home-throughput", {"client": report, "server": measured})
        assert report["rc"] == 0, report
        floor = float(os.environ.get("ASK_FLOWTABLE_MIN_GBPS", "9")) * 1e9
        assert measured["bits_per_second"] >= floor, (measured, floor)
    finally:
        if server.returncode is None:
            server.terminate()
            await asyncio.wait_for(server.communicate(), 10)


# ---- lifecycle -------------------------------------------------------------

async def test_default_route_moves_and_returns(homelab, splat_window):
    """The uplink's next hop changes, and the flows that borrowed it do too.

    Every WAN flow in this profile reaches its peer through the default route,
    so moving it has to retire them: a flow admitted against the old next hop
    would keep sending frames to a gateway that is no longer the one Linux would
    use, and every counter would look healthy while they went nowhere.

    Selective is the other half. The inter-VLAN flow borrowed no default route
    at all, so it must survive untouched -- same cookie, counters still climbing
    -- which is what separates a targeted retirement from a global invalidation
    that happens to look like one.
    """
    ctx = homelab
    client = BY_NAME["a"]
    forward, _ = await _accounted(ctx, client, peer=INTERNET, dport=PORT_A,
                                  sport=PORT_A, label="route-before")
    before = await ctx.state()
    await command(ctx.target, ctx.session, "ip", "route", "replace", "default",
                  "via", ctx.first_gateway, "dev", TARGET_WAN_IF)
    try:
        retired = await ctx.wait(
            lambda s: s["route_invalidations"] > before["route_invalidations"]
            and not [f for f in s["flows"] if f["cookie"] == forward["cookie"]],
            timeout=30)
        assert retired["bindings"] == 2, retired
        assert retired["invalidated"] == 0 and retired["invalidation_done"] == 0, retired
        assert retired["rearms"] == before["rearms"], retired
        assert retired["errors"] == before["errors"], retired
    finally:
        await command(ctx.target, ctx.session, "ip", "route", "replace", "default",
                      "via", ctx.second_gateway, "dev", TARGET_WAN_IF)
    after_forward, after_reverse = await _accounted(ctx, client, peer=INTERNET,
                                                    dport=PORT_A, sport=PORT_A,
                                                    label="route-after")
    _assert_wan(ctx, after_forward, after_reverse, client=client)
    # The retirement is proved by the wait above, which required the old cookie
    # to be gone. It is deliberately not re-proved by requiring the readmitted
    # flow to carry a *different* cookie: the cookie is the address of a
    # Netfilter tuple, and a flow retired and immediately readmitted often
    # lands on the same slab object and comes back with the same one.
    after = await ctx.state()
    assert after["errors"] == before["errors"], (before, after)
    ctx.record("home-route-change", {"before": before, "retired": retired,
                                     "after": after})


async def test_ipsec_sa_is_replaced(homelab, splat_window):
    """The SA is rekeyed under a live tunnel, and the flows follow the new one.

    Nothing about an SA reaches the rule in a form that could be revalidated:
    the flow was built with a handle to the state it was admitted against. So
    replacing the state has to retire the flows -- otherwise the hardware keeps
    encrypting with a key the peer has already forgotten, which is silent loss
    with healthy counters, exactly as a stale PPPoE session id would be.

    Nothing else is disturbed while it happens: the policy is untouched, the
    conntrack table is not flushed and no device changes, so a flow that comes
    back with a new cookie came back because its SA went and for no other
    reason.
    """
    ctx = homelab
    first = 0x0A990000 | (secrets.randbelow(0xFFFF) + 1)
    await _install_ipsec(ctx, first, first ^ 0x8000)
    await _start_ipsec_echo(ctx)
    before = await _ipsec_traffic(ctx, 40)
    _assert_ipsec(before, 40)
    old_cookies = {row["cookie"] for row in before["forward"] + before["reverse"]}
    state_before = await ctx.state()

    second = first ^ 0x1000
    await _rekey_ipsec(ctx, second)
    retired = await ctx.wait(
        lambda s: not [f for f in s["flows"] if f["cookie"] in old_cookies],
        timeout=30)
    assert retired["bindings"] == 2, retired
    assert retired["errors"] == state_before["errors"], (state_before, retired)
    assert retired["fatal"] == retired["quarantine"] == 0, retired

    # Hardware retirement precedes native flowtable GC clearing IPS_OFFLOAD.
    # Wait for readmission with the same connection, without flushing it, then
    # count only the measured burst. An immediate 200 ms burst can finish
    # entirely before the next one-second GC tick has made admission possible.
    await _admit(ctx, BY_NAME["a"], peer=IPSEC_INNER, dport=PORT_IPSEC,
                 sport=PORT_IPSEC, label="ipsec-rekey-admission")
    after = await _ipsec_traffic(ctx, 40)
    ctx.record("home-ipsec-rekey", {"before": before, "retired": retired,
                                    "after": after,
                                    "spis": [hex(first), hex(second)]})
    # The retirement is the `ctx.wait` above, which required every cookie the
    # old SA's flows carried to be gone; the traffic here is the readmission.
    # The two are deliberately not joined by requiring the new cookies to
    # differ -- a cookie is the address of a Netfilter tuple and a readmitted
    # flow often reuses the slab object, so that would be a coin toss.
    _assert_ipsec(after, 40)


async def test_bridge_vlan_change_retires(homelab, splat_window):
    """A VLAN is withdrawn from the LAN port and given back.

    PORT_VLAN changes deliberately drain the shared flowtable and stop
    admission until the policy is reapplied. The guest must stop receiving,
    trusted traffic must survive in software, and both must return to hardware
    after membership is restored and the controller reconciles its policy.
    """
    ctx = homelab
    guest, trusted = BY_NAME["c"], BY_NAME["a"]
    await _accounted(ctx, trusted, peer=INTERNET, dport=PORT_A,
                     sport=PORT_A, label="vlan-change-trusted-before")
    await _accounted(ctx, guest, peer=INTERNET, dport=PORT_C,
                     sport=PORT_C, label="vlan-change-before")
    before = await ctx.state()
    await command(ctx.target, ctx.session, "bridge", "vlan", "del", "dev",
                  TARGET_LAN_IF, "vid", str(VID_C))
    try:
        retired = await ctx.wait(
            lambda s: s["invalidated"] == s["invalidation_done"] == 1
            and not s["entries"] and not s["neighbour_refs"] and not s["handle_refs"],
            timeout=30)
        assert retired["errors"] == before["errors"], retired
        assert retired["fatal"] == retired["quarantine"] == 0, retired
        assert not retired["flows"], retired
        blocked = await _exchange(ctx, guest, peer=INTERNET, dport=PORT_C,
                                   sport=PORT_C, count=8, tolerate_loss=True,
                                   label="vlan-change-blocked")
        assert blocked == {"echoed": 0, "lost": 8}, blocked
        software_before = await _software_rx(ctx)
        surviving = await _exchange(ctx, trusted, peer=INTERNET, dport=PORT_A,
                                     sport=PORT_A, count=16,
                                     label="vlan-change-software")
        software_after = await _software_rx(ctx)
        assert surviving == {"echoed": 16, "lost": 0}, surviving
        assert software_after[TARGET_LAN_IF] - software_before[TARGET_LAN_IF] >= 16
        assert not (await ctx.state())["entries"]
        ctx.record("home-vlan-withdrawn", {"retired": retired, "blocked": blocked,
                                          "surviving": surviving})
    finally:
        await command(ctx.target, ctx.session, "bridge", "vlan", "add", "dev",
                      TARGET_LAN_IF, "vid", str(VID_C))
    await _reachable(ctx, guest, INTERNET)
    await apply(ctx.console, _policy(), r=ctx)
    ready = await ctx.wait(lambda s: not s["invalidated"] and s["bindings"] == 2)
    assert ready["rearms"] > before["rearms"], (before, ready)
    forward, reverse = await _accounted(ctx, trusted, peer=INTERNET, dport=PORT_A,
                                        sport=PORT_A, label="vlan-change-trusted-after")
    _assert_wan(ctx, forward, reverse, client=trusted)
    forward, reverse = await _accounted(ctx, guest, peer=INTERNET, dport=PORT_C,
                                        sport=PORT_C, label="vlan-change-after")
    _assert_wan(ctx, forward, reverse, client=guest)
    ctx.record("home-vlan-change", {"before": before, "retired": retired,
                                    "after": await ctx.state()})


async def test_module_reload_reproves_the_profile(homelab,
                                                                  splat_window):
    """The adapter is unloaded and reloaded with the whole profile standing.

    Nothing in Linux replays a device-specific flowtable binding when a driver
    registers again, so the table that was there stays in software and the
    policy has to be applied afresh -- which is the contract, not a defect, and
    is what `apply` does. What must survive the round trip is everything the
    adapter did *not* own: the bridge, the VLANs, the firewall, the tunnels and
    the routes are the kernel's, and a reload that needed any of them rebuilt
    would mean an operator's `modprobe` costs them their configuration.

    Then every feature is re-proved by a short burst rather than assumed. The
    IPsec tunnel is the exception that has to be reinstalled: its hardware state
    belonged to the module, so its SAs are offered again -- which is itself worth
    proving, because it is what an operator would do.

    It is also the one thing the operator has to take down first. Every
    offloaded state and policy holds a reference on the module that provides
    its device operations, so the kernel can never call into unloaded text,
    and while the tunnel stands the unload is refused.
    """
    ctx = homelab
    console = ctx.console
    await console_command(console, "test", "-e",
                          "/sys/module/cdx/holders/ask_flowtable")
    pinned = await console_command(console, "rmmod", "ask_flowtable", check=False,
                                   timeout=30)
    assert pinned["rc"] != 0 and "in use" in pinned["stdout"], pinned
    await console_command(console, "ip", "xfrm", "policy", "flush")
    await console_command(console, "ip", "xfrm", "state", "flush")
    await console_command(console, "rmmod", "ask_flowtable", timeout=30)
    for path in ("/sys/module/ask_flowtable", "/proc/cdx_flowtable",
                 "/sys/module/cdx/holders/ask_flowtable"):
        absent = await console_command(console, "test", "-e", path, check=False)
        assert absent["rc"] == 1, path
    # Still a working router while the adapter is gone: the profile is the
    # kernel's configuration and only the acceleration was the module's.
    report = await _exchange(ctx, BY_NAME["a"], peer=INTERNET, dport=PORT_A,
                             sport=PORT_A, count=16, label="reload-software")
    assert report == {"echoed": 16, "lost": 0}, report
    await console_command(console, "modprobe", "ask_flowtable", timeout=30)
    detached = await ctx.state()
    assert detached["bindings"] == detached["entries"] == 0, detached
    assert detached["fatal"] == 0, detached
    await apply(console, _policy(), r=ctx)
    await ctx.wait(lambda s: s["bindings"] == 2)

    # ---- and now every feature again, on the profile that was never rebuilt.
    trusted, iot, guest = BY_NAME["a"], BY_NAME["b"], BY_NAME["c"]
    forward, reverse = await _accounted(ctx, trusted, peer=INTERNET, dport=PORT_A,
                                        sport=PORT_A, label="reload-wan")
    _assert_wan(ctx, forward, reverse, client=trusted)
    forward, reverse = await _accounted(ctx, guest, peer=INTERNET, dport=PORT_C,
                                        sport=PORT_C, label="reload-guest")
    _assert_wan(ctx, forward, reverse, client=guest)
    forward, reverse = await _tunnel_tcp(ctx, trusted, "reload-tunnel")
    assert forward["out_tnl"] == _tunnel_text(ctx.shape), forward
    assert reverse["in_tnl"] == _tunnel_text(ctx.shape), reverse
    # The firewall is the kernel's, not the module's, so it has to still be
    # refusing. Counted rather than inferred from silence, for the reason the
    # hairpin case gives.
    stop_trusted = await _udp_listener(ctx, trusted, PORT_REFUSED,
                                       "profile_home_reload_refused")
    try:
        drops_before = await _firewall_drops(ctx, ctx.bridge_text[VID_B],
                                             ctx.bridge_text[VID_A])
        refused = await _exchange(ctx, iot, peer=trusted["ip"], dport=PORT_REFUSED,
                                  sport=PORT_REFUSED, count=8, label="reload-refused",
                                  tolerate_loss=True)
        drops = await _firewall_drops(ctx, ctx.bridge_text[VID_B],
                                      ctx.bridge_text[VID_A]) - drops_before
        assert refused["echoed"] == 0, refused
        assert drops >= 8, (drops, refused)
    finally:
        await stop_trusted()

    spi = 0x0A9A0000 | (secrets.randbelow(0xFFFF) + 1)
    await _install_ipsec(ctx, spi, spi ^ 0x8000)
    await _start_ipsec_echo(ctx)
    ipsec = await _ipsec_traffic(ctx, 40)
    ctx.record("home-reload-ipsec", ipsec)
    _assert_ipsec(ipsec, 40)
    ctx.record("home-module-reload", {"state": await ctx.state()})


async def _start_ipsec_echo(ctx):
    """The WAN peer echoes decrypted payloads on the fixture's running loop."""
    if ctx.ipsec_echo is None:
        transport, ctx.ipsec_echo = await asyncio.get_running_loop().create_datagram_endpoint(
            _Echo, local_addr=(IPSEC_INNER, PORT_IPSEC))
        ctx.endpoints.append(transport)
