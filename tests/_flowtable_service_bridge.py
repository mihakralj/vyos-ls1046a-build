"""Shared support for flowtable service bridge."""

from __future__ import annotations

import asyncio
import errno
import json
import os
import struct
import time
from contextlib import asynccontextmanager

import pytest_asyncio
from _flowtable_rig import DPORT, WAN_IP, artifact_dir, command, console_command, read
from _flowtable_service import managed_service
from _flowtable_service_vlan import received
from _topology import LAN_NIC, TARGET_LAN_IF, TARGET_WAN_IF, lan_run_python
from ask_orch.client import Agent
from ask_orch.uart import Console

BRIDGE = "br-ftsvc"
TRUST_VID, GUEST_VID = 285, 286
TRUST_IF, GUEST_IF = f"{BRIDGE}.{TRUST_VID}", f"{BRIDGE}.{GUEST_VID}"
NETNS, LAN_IF = "ask-ft-service-guest", "askftguest"
GATEWAY, ADDRESS = "172.29.86.1", "172.29.86.2"
# The user-space STP entry point the kernel runs when STP is switched on, and
# a second bridge port that keeps the bridge's carrier while the LAN port is
# blocked.
STP_HELPER, STP_STUB = "/sbin/bridge-stp", "#!/bin/sh\nexit 0\n"
CARRIER_PORT = "askftstp"
# The tail of every reply-tuple probe. The LAN wire probe counts it whichever
# socket, if any, a leaked probe reaches.
MARKER = (b"ASK-BLOCKED-PORT-PROBE-" * 11)[:244]


@asynccontextmanager
async def bridge_topology(r, *, guest=True):
    """br-ftsvc over the LAN port, VLAN-filtering, with the trusted VLAN
    untagged on the port and routed on br-ftsvc.285. `guest` adds the tagged
    guest VLAN routed on br-ftsvc.286 and its station in a LAN-VM namespace."""
    wan = Agent("wan", f"http://{os.environ.get('ASK_WAN_IP', '127.0.0.1')}:9110")
    baseline = await r.state()
    links = json.loads((await command(r.target, r.session, "ip", "-j", "link", "show"))["stdout"])
    assert not {BRIDGE, TRUST_IF, GUEST_IF} & {link["ifname"] for link in links}, links
    assert "master" not in next(link for link in links if link["ifname"] == TARGET_LAN_IF), links
    addresses = json.loads((await command(r.target, r.session, "ip", "-j", "-4", "addr", "show", "dev", TARGET_LAN_IF))["stdout"])
    original = [f"{a['local']}/{a['prefixlen']}" for a in addresses[0]["addr_info"] if a["family"] == "inet"]
    assert original, addresses
    routes = json.loads((await command(wan, r.session, "ip", "-j", "route", "show", "exact", ADDRESS + "/32"))["stdout"])
    assert not routes, routes
    dut_wan = json.loads((await command(r.target, r.session, "ip", "-j", "-4", "addr", "show", "dev", TARGET_WAN_IF))["stdout"])
    dut_ip = next(a["local"] for a in dut_wan[0]["addr_info"] if a["family"] == "inet")
    bridge_created = lan_created = wan_route = False
    vlans = [(TRUST_VID, TRUST_IF, original)] + ([(GUEST_VID, GUEST_IF, [GATEWAY + "/24"])] if guest else [])
    hosts = [(r.lan_ip, TRUST_IF)] + ([(ADDRESS, GUEST_IF)] if guest else [])

    async def dut(*args):
        return await command(r.target, r.session, *args)

    async def lan(*args):
        result = await asyncio.to_thread(r.lan.execute, list(args), timeout=10)
        assert result.rc == 0, result.stdout
        return result.stdout

    old_neighbour = json.loads(await lan("ip", "-j", "neigh", "show", "to", r.lan_gateway, "dev", LAN_NIC))
    restore_neighbour = ["ip", "neigh", "del", r.lan_gateway, "dev", LAN_NIC]
    if old_neighbour and old_neighbour[0].get("lladdr"):
        state = "permanent" if "PERMANENT" in old_neighbour[0]["state"] else "stale"
        restore_neighbour = ["ip", "neigh", "replace", r.lan_gateway,
                             "lladdr", old_neighbour[0]["lladdr"], "nud", state, "dev", LAN_NIC]
    r.record("service-bridge-lan-neighbour", old_neighbour)
    try:
        # Blocking also blocks ARP. Pin the trusted peer just like the DUT
        # and guest so recovery measures forwarding, without ARP timeouts.
        await lan("ip", "neigh", "replace", r.lan_gateway, "lladdr", r.dut_lan_mac,
                  "nud", "permanent", "dev", LAN_NIC)
        await dut("ip", "link", "add", "name", BRIDGE, "type", "bridge")
        bridge_created = True
        await dut("ip", "link", "set", BRIDGE, "address", r.dut_lan_mac)
        await dut("ip", "link", "set", BRIDGE, "type", "bridge", "vlan_filtering", "1", "vlan_default_pvid", "0")
        for address in original:
            await dut("ip", "addr", "del", address, "dev", TARGET_LAN_IF)
        await dut("ip", "link", "set", TARGET_LAN_IF, "master", BRIDGE)
        await dut("ip", "link", "set", BRIDGE, "up")
        await dut("bridge", "vlan", "add", "dev", TARGET_LAN_IF, "vid", str(TRUST_VID), "pvid", "untagged")
        if guest:
            await dut("bridge", "vlan", "add", "dev", TARGET_LAN_IF, "vid", str(GUEST_VID))
        for vid, dev, cidrs in vlans:
            await dut("bridge", "vlan", "add", "dev", BRIDGE, "vid", str(vid), "self")
            await dut("ip", "link", "add", "link", BRIDGE, "name", dev, "type", "vlan", "id", str(vid))
            for address in cidrs:
                await dut("ip", "addr", "add", address, "dev", dev)
            await dut("ip", "link", "set", dev, "up")
        for address, dev in hosts:
            await dut("ip", "route", "replace", address + "/32", "dev", dev)
            await dut("ip", "neigh", "replace", address, "lladdr", r.lan_mac, "nud", "permanent", "dev", dev)
        if not guest:
            yield
            return
        setup = f'''
from pathlib import Path
import subprocess
def run(*args): subprocess.run(args, check=True, capture_output=True, text=True)
assert not Path('/var/run/netns/' + {NETNS!r}).exists()
assert not Path('/sys/class/net/' + {LAN_IF!r}).exists()
run('ip', 'netns', 'add', {NETNS!r})
try:
    run('ip', 'link', 'add', 'link', {LAN_NIC!r}, 'name', {LAN_IF!r},
        'netns', {NETNS!r}, 'type', 'vlan', 'id', {str(GUEST_VID)!r})
    run('ip', '-n', {NETNS!r}, 'link', 'set', 'lo', 'up')
    run('ip', '-n', {NETNS!r}, 'addr', 'add', {ADDRESS + '/24'!r}, 'dev', {LAN_IF!r})
    run('ip', '-n', {NETNS!r}, 'link', 'set', {LAN_IF!r}, 'up')
    run('ip', '-n', {NETNS!r}, 'route', 'add', 'default', 'via', {GATEWAY!r})
    run('ip', '-n', {NETNS!r}, 'neigh', 'replace', {GATEWAY!r}, 'lladdr',
        {r.dut_lan_mac!r}, 'nud', 'permanent', 'dev', {LAN_IF!r})
except BaseException:
    run('ip', 'netns', 'del', {NETNS!r})
    raise
'''
        result = await lan_run_python(r.lan, setup, label="service_bridge_setup", timeout=20)
        assert result.rc == 0, result.stdout
        lan_created = True
        await command(wan, r.session, "ip", "route", "add", ADDRESS + "/32", "via", dut_ip, "dev", r.wan_if)
        wan_route = True
        yield
    finally:
        failures = []

        async def attempt(operation):
            try:
                return await operation
            except Exception as error:
                failures.append(repr(error))

        try:
            if wan_route:
                await attempt(command(wan, r.session, "ip", "route", "del", ADDRESS + "/32", "via", dut_ip, "dev", r.wan_if))
            if bridge_created:
                # Restore the base rig's L3 path before its own teardown. The
                # independent console remains usable if bridge setup was partial.
                with Console.target(log_path=str(artifact_dir() / "service-bridge-cleanup-uart.log")) as con:
                    await asyncio.to_thread(con.login, "root", None)
                    await attempt(console_command(con, "ip", "link", "set", TARGET_LAN_IF, "nomaster"))
                    await attempt(console_command(con, "ip", "link", "del", BRIDGE))
                    for address in original:
                        await attempt(console_command(con, "ip", "addr", "replace", address, "dev", TARGET_LAN_IF))
                    await attempt(console_command(con, "ip", "route", "replace", r.lan_ip + "/32", "dev", TARGET_LAN_IF))
                    await attempt(console_command(con, "ip", "neigh", "replace", r.lan_ip, "lladdr", r.lan_mac,
                                                  "nud", "permanent", "dev", TARGET_LAN_IF))
            if lan_created:
                result = await attempt(lan_run_python(r.lan,
                    f"import subprocess\nsubprocess.run(['ip', 'netns', 'del', {NETNS!r}], check=True)\n",
                    label="service_bridge_cleanup", timeout=15))
                if result and result.rc:
                    failures.append(result.stdout)
        finally:
            await attempt(lan(*restore_neighbour))
        final = await attempt(r.state())
        r.record("service-bridge-cleanup", final)
        if final and any(final[k] != baseline[k] for k in ("vlan_records", "vlan_slots", "errors")):
            failures.append({"baseline": baseline, "final": final})
        assert not failures, failures


@pytest_asyncio.fixture
async def bridge_service(rig):
    r = rig
    async with bridge_topology(r):
        async with managed_service(r, addresses=(r.lan_ip, ADDRESS)):
            yield r


@pytest_asyncio.fixture
async def bridge_software(rig):
    """The bridge with no service and no guest: the rig's own flowtable, which
    a test builds itself, with or without hardware offload.

    That table goes before the bridge does. Taking a bound port out of the
    bridge is an upper change the adapter answers by invalidating the whole
    backend, and only the next bind clears that, so the rig's own teardown,
    which runs after this one, would leave it latched for the next test."""
    r = rig
    async with bridge_topology(r, guest=False):
        try:
            yield r
        finally:
            await r.delete_table()


async def membership(r):
    rows = json.loads((await console_command(r.service_console, "bridge", "-j", "vlan", "show", "dev", TARGET_LAN_IF))["stdout"])
    entries = [v for row in rows if row["ifname"] == TARGET_LAN_IF for v in row["vlans"]]
    return {vid: v.get("flags", []) for v in entries
            for vid in range(v["vlan"], v.get("vlanEnd", v["vlan"]) + 1)}


async def topology(r):
    result = {}
    for dev in (TRUST_IF, GUEST_IF):
        link = json.loads((await command(r.target, r.session, "ip", "-j", "-4", "addr", "show", "dev", dev))["stdout"])[0]
        routes = json.loads((await command(r.target, r.session, "ip", "-j", "route", "show", "dev", dev))["stdout"])
        neighbours = json.loads((await command(r.target, r.session, "ip", "-j", "neigh", "show", "dev", dev))["stdout"])
        result[dev] = {"ifindex": link["ifindex"], "addresses": link["addr_info"], "routes": routes,
                       "pinned_neighbours": [n for n in neighbours if "PERMANENT" in n["state"]]}
    return result


def guest_rows(state):
    return [f for f in state["flows"] if f["src"].startswith(ADDRESS + ":") or f["dst"].startswith(ADDRESS + ":")]


def bridge_paths(state, r):
    for flow in state["flows"]:
        guest = flow in guest_rows(state)
        endpoint = ADDRESS if guest else r.lan_ip
        forward = flow["src"].startswith(endpoint + ":")
        logical = GUEST_IF if guest else TRUST_IF
        tag = str(GUEST_VID) if guest else "-"
        assert (flow["in_br"], flow["out_br"]) == ((logical, "-") if forward else ("-", logical)), flow
        assert (flow["in_vlan"], flow["out_vlan"]) == ((tag, "-") if forward else ("-", tag)), flow


async def port_state(r, port=TARGET_LAN_IF):
    rows = json.loads((await command(r.target, r.session, "bridge", "-j", "link", "show", "dev", port))["stdout"])
    return rows[0]["state"]


async def vlan_states(r):
    """Each VLAN's own STP state on the LAN port, as `bridge -d vlan` has it."""
    rows = json.loads((await command(r.target, r.session, "bridge", "-j", "-d", "vlan", "show",
                                     "dev", TARGET_LAN_IF))["stdout"])
    entries = [v for row in rows if row["ifname"] == TARGET_LAN_IF for v in row["vlans"]]
    return {vid: v.get("state") for v in entries
            for vid in range(v["vlan"], v.get("vlanEnd", v["vlan"]) + 1)}


async def offloaded(r, lan, sport):
    """Whether conntrack holds the UDP connection in a flowtable, in software
    ([OFFLOAD]) or in hardware ([HW_OFFLOAD])."""
    listing = await command(r.target, r.session, "conntrack", "-L", "-p", "udp", "--orig-src", lan,
                            "--orig-dst", WAN_IP, "--sport", str(sport), "--dport", str(DPORT), check=False)
    return "OFFLOAD]" in listing["stdout"]


async def settle(r, streams, sport, timeout=5):
    """Wait for the connections a block retired to leave the flowtable. Both
    the hardware retirement and the software sweep run after the state
    change, not in it."""
    deadline = time.monotonic() + timeout
    while True:
        cached = {ident: await offloaded(r, lan, sport) for ident, lan in streams.items()}
        if not any(cached.values()):
            return
        assert time.monotonic() < deadline, ("a flow through the stopped port stayed cached", cached)
        await asyncio.sleep(0.1)


def reply_probe(ident):
    """A datagram on a connection's reply tuple that its socket can never
    accept: the serial lies outside every range the connection sends, so a
    leaked one fails the stream reading it, and the tail is the marker the LAN
    wire probe counts."""
    return struct.pack("!IQ", ident, (1 << 63) - 1) + MARKER


@asynccontextmanager
async def pinned(r, vids):
    """Pin the LAN station's address to the LAN port in each VLAN. A port or
    VLAN that stops forwarding stops learning too, and an entry that aged out
    meanwhile would make the forward-path walk miss rather than refuse."""
    done = []
    try:
        for vid in vids:
            await command(r.target, r.session, "bridge", "fdb", "replace", r.lan_mac, "dev", TARGET_LAN_IF,
                          "master", "static", "vlan", str(vid))
            done.append(vid)
        yield
    finally:
        failures = []
        for vid in done:
            result = await command(r.target, r.session, "bridge", "fdb", "del", r.lan_mac, "dev", TARGET_LAN_IF,
                                   "master", "vlan", str(vid), check=False)
            if result["rc"]:
                failures.append(result)
        assert not failures, failures


@asynccontextmanager
async def blockable(r, con):
    """Make the LAN port's STP state the test's to set, and to keep.

    With STP off, br_set_port_state() goes on to br_port_state_selection(),
    which finds every port designated and puts it straight back to
    FORWARDING: `bridge link set ... state 4` never outlives the command.
    User-space STP leaves port states to user space, and the kernel starts it
    only once /sbin/bridge-stp answers "start" with success -- so a stub that
    always succeeds stands in for the daemon. A dummy port, enslaved while STP
    is still off so that it forwards, keeps the bridge's carrier: a bridge with
    no forwarding port drops it, and the link retirement that follows would
    race the one under test."""
    existing = await r.target.fs_read(r.session, STP_HELPER, max_bytes=4096)
    assert existing["errno"] == errno.ENOENT or (
        existing["errno"] == 0 and bytes.fromhex(existing["content_hex"]).decode() == STP_STUB), existing
    links = json.loads((await command(r.target, r.session, "ip", "-j", "link", "show"))["stdout"])
    assert CARRIER_PORT not in {link["ifname"] for link in links}, links
    undo = []
    try:
        await command(r.target, r.session, "modprobe", "dummy", "numdummies=0")
        await command(r.target, r.session, "ip", "link", "add", CARRIER_PORT, "type", "dummy")
        undo.append(("ip", "link", "del", CARRIER_PORT))
        await command(r.target, r.session, "ip", "link", "set", CARRIER_PORT, "master", BRIDGE)
        await command(r.target, r.session, "ip", "link", "set", CARRIER_PORT, "up")
        # The port is enabled once link watch reports it operational, which
        # trails the command; with user-space STP on, it would stay blocked.
        deadline = time.monotonic() + 5
        while (state := await port_state(r, CARRIER_PORT)) != "forwarding":
            assert time.monotonic() < deadline, state
            await asyncio.sleep(0.1)
        undo.append(("rm", "-f", STP_HELPER))
        written = await r.target.fs_write(r.session, STP_HELPER, STP_STUB)
        assert written["errno"] == 0, written
        await console_command(con, "chmod", "755", STP_HELPER)
        undo.append(("ip", "link", "set", BRIDGE, "type", "bridge", "stp_state", "0"))
        await command(r.target, r.session, "ip", "link", "set", BRIDGE, "type", "bridge", "stp_state", "1")
        # 2 is user-space STP. 1 would be the kernel's own, which refuses a
        # port state set from outside.
        mode = (await read(r.target, r.session, f"/sys/class/net/{BRIDGE}/bridge/stp_state")).strip()
        assert mode == "2", mode
        assert await port_state(r) == "forwarding"
        yield
    finally:
        failures = []
        # Forwarding again before STP stops, because stopping user-space STP
        # leaves a blocked port blocked; the helper goes only after the stop,
        # which runs it.
        for argv in [("bridge", "link", "set", "dev", TARGET_LAN_IF, "state", "3")] + undo[::-1]:
            try:
                result = await console_command(con, *argv, check=False)
                if result["rc"]:
                    failures.append(result)
            except Exception as error:
                failures.append(repr(error))
        assert not failures, failures


async def blocked_window(r, streams, sport, label, seconds=6, check=None):
    """Hold a block for `seconds` while both directions of every stream keep
    trying: the LAN side sends throughout, and the WAN side answers on each
    reply tuple. Neither may cross, nor may any of the connections be cached
    in a flowtable."""
    await settle(r, streams, sport)
    before = {ident: received(r, ident) for ident in streams}
    started, samples = time.monotonic(), []
    while time.monotonic() - started < seconds:
        for ident, lan in streams.items():
            r.echo.transport.sendto(reply_probe(ident), (lan, sport))
        await asyncio.sleep(0.2)
        state = await r.state()
        cached = {ident: await offloaded(r, lan, sport) for ident, lan in streams.items()}
        samples.append({"seconds": time.monotonic() - started, "state": state, "cached": cached})
        assert not any(cached.values()), ("a flow through the stopped port was cached", samples[-1])
        assert {ident: received(r, ident) for ident in streams} == before, (
            "a LAN datagram crossed the stopped port", before)
        if check:
            check(state)
    r.record(label, samples)


async def crossed_nothing(r, p, streams, label):
    """After the block: nothing the WAN sent reached the LAN wire, no stream
    met a reply it did not send, and every stream lost what it sent while
    blocked."""
    wire = await p.rpc("wire_probe", changes={"action": "status"})
    await p.rpc("wire_probe", changes={"action": "stop"})
    peer_state = await p.rpc("status")
    r.record(label, {"wire": wire, "peer": peer_state})
    assert wire["received"] == 0, ("a reply crossed the stopped port", wire)
    # Before stopping: a stream that met a stray reply has failed, and
    # stopping it would take the peer down with the error.
    assert not peer_state["errors"], ("a reply reached a stream", peer_state)
    reports = await p.rpc("stop", list(streams))
    r.record(label + "-streams", reports)
    assert all(report["lost"] > 0 for report in reports.values()), reports
