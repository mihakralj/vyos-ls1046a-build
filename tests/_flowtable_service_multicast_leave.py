"""Shared support for flowtable service multicast leave."""

from __future__ import annotations

import asyncio
import json
from collections import namedtuple
from contextlib import asynccontextmanager

import pytest_asyncio
from _flowtable_rig import command
from _mcast_windows import frames, mcast_rows, members, same, silenced
from _mroute_capacity import _python
from _topology import (
    LAN_NIC,
    TARGET_LAN_IF,
    TopologyStack,
    dut_vlan_subif,
    lan_vlan_subif,
)
from ask_orch.counters import kernel_tx_packets
from ask_orch.commands import console_python
from ask_orch.uart import Console

# How a membership ends, and what the host does to end it. `mode` is the
# filter mode it joined in; `end` is the socket call, or `silence` for a host
# that stops reporting without leaving.
MECHANISMS = {
    "igmpv2-leave": dict(family=4, version=2, mode="asm", end="leave"),
    "igmpv3-to-in": dict(family=4, version=3, mode="asm", end="leave"),
    "mldv1-done": dict(family=6, version=1, mode="asm", end="leave"),
    "expiry": dict(family=4, version=2, mode="asm", end="silence"),
    "igmpv3-block": dict(family=4, version=3, mode="asm", end="block"),
    "igmpv3-ssm-block": dict(family=4, version=3, mode="ssm", end="leave"),
}
BRIDGED_GROUP = {4: "239.9.8.1", 6: "ff1e::9:8:1"}
ROUTED_GROUP = {4: "239.9.8.2", 6: "ff1e::9:8:2"}
FILTER_GROUP = {4: "239.9.8.3", 6: "ff1e::9:8:3"}
BLOCK_GROUP = {4: "239.9.8.4", 6: "ff1e::9:8:4"}
# A second sender of a bridged group. The bridge validates no source, so any
# address the WAN segment could carry will do.
OTHER_SOURCE = {4: "198.18.166.2", 6: "fd00:166::2"}
# The source-filtering reports: IGMPv3 and MLDv2, with the querier speaking
# them. A blocked source is blocked once its group-and-source query goes
# unanswered, two queries a second apart.
FILTER_TIMERS = dict(igmp_version=3, mld_version=2, last_member_count=2, last_member_interval=100)
FILTER_VERSION = {4: 3, 6: 2}
SECOND_HOST = "askmcb"
LISTENER_BRIDGE = "br-askmcl"
VID_LEFT, VID_KEPT = 321, 322
# Source-specific handling needs the querier speaking IGMPv3. The last-member
# queries a leave provokes are pinned at two, a second apart, so the waits
# below do not depend on what the bridge was created with.
LEAVE_TIMERS = dict(igmp_version=3, last_member_count=2, last_member_interval=100)

ListenerBridge = namedtuple("ListenerBridge", "name left kept lan_left lan_kept")


async def end(stack, member, spec, iface):
    if spec["end"] == "silence":
        await stack.enter_async_context(silenced(member.lan, iface))
    else:
        await member.do(spec["end"])


@asynccontextmanager
async def second_host(lan):
    """A second receiver behind the same LAN port, with its own MAC.

    A macvlan answers the querier from its own address, so the bridge sees
    two hosts' reports arriving on one port -- which is all a second set-top
    box behind a dumb switch is."""
    await _python(lan, f"""
import pathlib, subprocess, time
def run(*args): subprocess.run(args, check=True, capture_output=True, text=True)
assert not pathlib.Path('/sys/class/net/{SECOND_HOST}').exists()
run('ip', 'link', 'add', 'link', {LAN_NIC!r}, 'name', {SECOND_HOST!r}, 'type', 'macvlan', 'mode', 'bridge')
# MLD reports need a usable link-local address the moment the host joins.
pathlib.Path('/proc/sys/net/ipv6/conf/{SECOND_HOST}/accept_dad').write_text('0')
run('ip', 'link', 'set', {SECOND_HOST!r}, 'up')
for _ in range(50):
    if 'scope link' in subprocess.check_output(['ip', '-6', 'addr', 'show', 'dev', {SECOND_HOST!r}], text=True):
        break
    time.sleep(0.1)
else:
    raise AssertionError('no link-local address on {SECOND_HOST}')
""")
    try:
        yield SECOND_HOST
    finally:
        await _python(lan, f"import subprocess\nsubprocess.run(['ip', 'link', 'del', {SECOND_HOST!r}], check=True)\n")


@pytest_asyncio.fixture
async def listener_bridge(multicast_rig):
    """The LAN port in a VLAN-aware snooping bridge, two VLANs routed onto it.

    Each VLAN device is an oif of its own, and a routed group expands each
    through the bridge's membership for that VLAN: two tag stacks on the one
    port, which the hardware carries as two listeners. That is the only way a
    replica set larger than one exists on this rig. The port's addresses move
    to the bridge and back so the LAN VM keeps its gateway."""
    r = multicast_rig
    topology = TopologyStack()
    interfaces = json.loads((await command(r.target, r.session, "ip", "-j", "-4", "addr", "show",
                                           "dev", TARGET_LAN_IF))["stdout"])
    addresses = [f"{a['local']}/{a['prefixlen']}" for a in interfaces[0]["addr_info"]
                 if a["family"] == "inet"]
    links = json.loads((await command(r.target, r.session, "ip", "-j", "link", "show"))["stdout"])
    assert LISTENER_BRIDGE not in {link["ifname"] for link in links}, links

    async def run(*argv, check=True):
        return await command(r.target, r.session, *argv, check=check)

    try:
        # IGMPv3 and MLDv2 from the first query, as mcast_bridge does.
        await run("ip", "link", "add", "name", LISTENER_BRIDGE, "type", "bridge", "vlan_filtering", "1",
                  "mcast_snooping", "1", "mcast_querier", "1",
                  "mcast_igmp_version", "3", "mcast_mld_version", "2")

        async def restore():
            await run("ip", "link", "set", TARGET_LAN_IF, "nomaster", check=False)
            await run("ip", "link", "del", LISTENER_BRIDGE, check=False)
            for address in addresses:
                await run("ip", "addr", "replace", address, "dev", TARGET_LAN_IF, check=False)
            await run("ip", "link", "set", TARGET_LAN_IF, "up", check=False)
        topology.push(restore)
        for address in addresses:
            await run("ip", "addr", "del", address, "dev", TARGET_LAN_IF)
        await run("ip", "link", "set", TARGET_LAN_IF, "master", LISTENER_BRIDGE)
        for vid in (VID_LEFT, VID_KEPT):
            await run("bridge", "vlan", "add", "dev", TARGET_LAN_IF, "vid", str(vid))
            await run("bridge", "vlan", "add", "dev", LISTENER_BRIDGE, "vid", str(vid), "self")
        await run("ip", "link", "set", LISTENER_BRIDGE, "up")
        for address in addresses:
            await run("ip", "addr", "add", address, "dev", LISTENER_BRIDGE)
        left, kept = [await dut_vlan_subif(topology, r.target, r.session, parent=LISTENER_BRIDGE, vid=vid,
                                           ipv4=f"198.18.{vid - 160}.1/24", ipv6=f"fd00:{vid:x}::1/64")
                      for vid in (VID_LEFT, VID_KEPT)]
        lan_left, lan_kept = [await lan_vlan_subif(topology, r.lan, parent=LAN_NIC, vid=vid)
                              for vid in (VID_LEFT, VID_KEPT)]
        # The querier's startup queries, and the VLAN devices' link-local
        # addresses, before anything joins.
        await asyncio.sleep(3)
        yield ListenerBridge(LISTENER_BRIDGE, left, kept, lan_left, lan_kept)
    finally:
        await topology.teardown("listener bridge")


DISCARD_GROUP = {4: "239.9.8.5", 6: "ff1e::9:8:5"}


# Every frame the discard drops is a buffer FMan took from a pool. QMan
# discards an enqueue its queue rejects -- both FMan portals are set that way
# -- and has to give the buffer back; one kept per frame would drain the pool
# many times over across this many.
BURST = 1_000_000
BMAN_POOL_CONTENT = 0x1890600      # BMan CCSR + 0x600, one word per pool


async def hardware_tx(r, dev):
    """Packets `dev` has counted as sent by the hardware: its netdev count,
    which folds CDX's in, less what the driver itself sent."""
    shown = json.loads((await command(r.target, r.session, "ip", "-j", "-s", "link", "show",
                                      "dev", dev))["stdout"])
    return shown[0]["stats64"]["tx"]["packets"] - await kernel_tx_packets(r.target, r.session, dev)


async def pool_contents(r):
    """Every BMan pool's free count, from its big-endian content register."""
    # Read locally in one operation. Spawning devmem for every pool takes
    # most of a discard entry's five-second idle window before traffic starts.
    result = await console_python(Console.target(), f'''
import json, mmap, struct
address = {BMAN_POOL_CONTENT}
with open('/dev/mem', 'rb', buffering=0) as memory:
    with mmap.mmap(memory.fileno(), mmap.PAGESIZE, flags=mmap.MAP_SHARED,
                   prot=mmap.PROT_READ, offset=address & -mmap.PAGESIZE) as registers:
        print(json.dumps({{b: struct.unpack_from('>I', registers,
            address % mmap.PAGESIZE + 4*b)[0] for b in range(64)}}))
''', timeout=10)
    pools = {int(bpid): count for bpid, count in json.loads(result["stdout"]).items() if count}
    assert pools, "BMan reported no populated buffer pools"
    return pools


def burst(config, iface, count):
    """One stream's frame, `count` times, as fast as a raw socket goes.

    The frame the windows send, byte for byte: the entry is keyed on the
    Ethernet pair too, and a sender address differing from the one it learned
    is another shape of the stream -- which reaches the CPU and takes the key
    over, rather than being dropped."""
    import socket
    frame = bytes(frames({**config, "count": 1})[0])
    sock = socket.socket(socket.AF_PACKET, socket.SOCK_RAW)
    sock.bind((iface, 0))
    sent = refused = 0
    try:
        while sent < count:
            try:
                sock.send(frame)
                sent += 1
                refused = 0
            except OSError:
                # A full queue clears; a port gone down does not.
                refused += 1
                if refused > 1_000_000:
                    raise
    finally:
        sock.close()
    return sent


def flow_row(state, group, source):
    """The bridged learner's row for one source of a group: one per flow."""
    rows = [row for row in mcast_rows(state, group) if same(row["src"], source)]
    assert len(rows) <= 1, rows
    return rows[0] if rows else None


def carried_to(state, group, source, port=f"{TARGET_LAN_IF}/0"):
    row = flow_row(state, group, source)
    return bool(row) and row["state"] == "installed" and members(row, "ports") == {port}


def withheld(state, group, source):
    """Learned, and the bridge forwards it nowhere: dropped in hardware, where
    the bridge would have dropped it on the CPU."""
    row = flow_row(state, group, source)
    return bool(row) and row["state"] == "discarding" and row["ports"] == "-"


# A query every two seconds: each round has both hosts report again.
CHURN_QUERY_INTERVAL = 200
CHURN_ROUNDS = 10


async def s1_listed(r, bridge, group, source, port=TARGET_LAN_IF):
    """Whether the bridge holds `source` for `group` on `port`: an (S,G) port
    group of its own, or an entry on the (*,G) port group's source list."""
    entries = json.loads((await command(r.target, r.session, "bridge", "-j", "-d", "mdb", "show",
                                        "dev", bridge))["stdout"] or "[]")
    for block in entries:
        for entry in block.get("mdb", []):
            if entry.get("port") != port or not same(entry.get("grp", ""), group):
                continue
            if entry.get("src") and same(entry["src"], source):
                return True
            if any(same(s.get("address", ""), source) for s in entry.get("source_list", [])):
                return True
    return False
