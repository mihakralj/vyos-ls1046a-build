"""Selective IPv4 route changes with persistent TCP and an independent peer."""
from __future__ import annotations

import asyncio
from functools import partial
import json
import os

import pytest_asyncio

from ask_orch.client import Agent
from _topology import FULL_FRAME, LAN_NIC, TARGET_LAN_IF, TARGET_WAN_IF, lan_run_python
from _flowtable_connections import (by_key, peer)
from _flowtable_rig import (DPORT, SPORT, TABLE, WAN_IP, command, read)
from _flowtable_selective_neighbour import (keys as peer_keys, unchanged as peer_unchanged)
from _flowtable_selective_neighbour import (warm as peer_warm, hardware as peer_hardware)

PEERS = [dict(netns="ask-ft-route-a", iface="askftra", lan="198.18.30.2", mac="02:9d:99:b2:33:c1"),
         dict(netns="ask-ft-route-b", iface="askftrb", lan="198.18.31.2", mac="02:9d:99:b2:33:d1")]
FLOWS = [{**spec, "id": 2 * i + j, "proto": proto, "sport": SPORT + 128}
         for i, spec in enumerate(PEERS) for j, proto in enumerate(("udp", "tcp"))]
A, B, ALL = [0, 1], [2, 3], [0, 1, 2, 3]
ROUTE = "198.18.30.0/25"
HOST_ROUTE = PEERS[0]["lan"] + "/32"
keys = partial(peer_keys, flows=FLOWS)
unchanged = partial(peer_unchanged, flows=FLOWS)
warm = partial(peer_warm, flows=FLOWS)
hardware = partial(peer_hardware, flows=FLOWS)


def routed(mtu):
    """FLOWS while peer A's route carries `mtu`.

    The route's MTU is what names the route a readmitted flow took, so the
    cases below set a different one on each route they add. Below a full
    frame, A's UDP reply -- arriving on the WAN port, which can deliver 1500
    bytes whatever its MTU -- no longer fits its path, and the adapter leaves
    it to Linux rather than have the microcode fragment it. A's TCP reply
    carries DF and takes the route's MTU into hardware, which is where the
    marker is read."""
    return [{**f, "software": (TARGET_WAN_IF,)}
            if f["id"] in A and f["proto"] == "udp" and mtu < FULL_FRAME else f
            for f in FLOWS]


@pytest_asyncio.fixture
async def routes(rig):
    r = rig
    r.route_errors = (await r.state())["errors"]
    wan = Agent("wan", f"http://{os.environ.get('ASK_WAN_IP', '127.0.0.1')}:9110")
    cleanup = []
    # Endpoint addresses are /32s with an explicit on-link gateway. Only the
    # DUT's test FIB provides their return routes, so withdrawal is observable.
    setup = f'''
import pathlib, subprocess
peers = {PEERS!r}
def run(*args): subprocess.run(args, check=True, capture_output=True, text=True)
for p in peers:
    assert not pathlib.Path('/var/run/netns/' + p['netns']).exists()
    assert not pathlib.Path('/sys/class/net/' + p['iface']).exists()
created = []
try:
    for p in peers:
        run('ip', 'netns', 'add', p['netns']); created.append(p)
        run('ip', 'link', 'add', 'link', {LAN_NIC!r}, 'name', p['iface'],
            'netns', p['netns'], 'type', 'macvlan', 'mode', 'bridge')
        run('ip', '-n', p['netns'], 'link', 'set', 'lo', 'up')
        run('ip', '-n', p['netns'], 'link', 'set', p['iface'], 'address', p['mac'], 'up')
        run('ip', '-n', p['netns'], 'addr', 'add', p['lan'] + '/32', 'dev', p['iface'])
        run('ip', '-n', p['netns'], 'route', 'add', {r.lan_gateway + '/32'!r}, 'dev', p['iface'])
        run('ip', '-n', p['netns'], 'route', 'add', 'default', 'via', {r.lan_gateway!r})
except BaseException:
    for p in reversed(created): run('ip', 'netns', 'del', p['netns'])
    raise
'''
    result = await lan_run_python(r.lan, setup, label="flowtable_routes_setup", timeout=20)
    assert result.rc == 0, result.stdout
    try:
        async def change(agent, args, undo):
            await command(agent, r.session, *args)
            cleanup.append((agent, undo))

        dut_ip = next(a["local"] for i in json.loads((await command(r.target, r.session, "ip", "-j", "-4", "addr", "show", "dev", TARGET_WAN_IF))["stdout"])
                      for a in i["addr_info"] if a["family"] == "inet")
        for index, spec in enumerate(PEERS):
            network = f"198.18.{30 + index}.0"
            for agent in (r.target, wan):
                existing = await command(agent, r.session, "ip", "-j", "route", "show", "root", network + "/24")
                assert json.loads(existing["stdout"]) == [], existing
            await change(r.target, ["ip", "route", "add", "blackhole", network + "/24"],
                         ["ip", "route", "del", "blackhole", network + "/24"])
            await change(r.target, ["ip", "route", "add", network + "/25", "dev", TARGET_LAN_IF],
                         ["ip", "route", "del", network + "/25", "dev", TARGET_LAN_IF])
            await change(wan, ["ip", "route", "add", spec["lan"] + "/32", "via", dut_ip, "dev", r.wan_if],
                         ["ip", "route", "del", spec["lan"] + "/32", "via", dut_ip, "dev", r.wan_if])
        for spec in FLOWS:
            nat = ["POSTROUTING", "-s", spec["lan"], "-d", WAN_IP, "-p", spec["proto"],
                   "--sport", str(spec["sport"]), "--dport", str(DPORT), "-j", "ACCEPT"]
            await change(r.target, ["iptables", "-t", "nat", "-I", *nat],
                         ["iptables", "-t", "nat", "-D", *nat])
        await r.nft(f'''table inet {TABLE} {{
 flowtable fast {{ hook ingress priority 0; devices = {{ {TARGET_LAN_IF}, {TARGET_WAN_IF} }}; flags offload; }}
 chain forward {{ type filter hook forward priority 0; policy accept;
 ip saddr {{ {PEERS[0]['lan']}, {PEERS[1]['lan']} }} ip daddr {WAN_IP} udp sport {SPORT + 128} udp dport {DPORT} flow add @fast
 ip saddr {{ {PEERS[0]['lan']}, {PEERS[1]['lan']} }} ip daddr {WAN_IP} tcp sport {SPORT + 128} tcp dport {DPORT} flow add @fast
 }}
}}''')
        await r.wait(lambda s: s["bindings"] == 2)
        yield r
    finally:
        failures = []

        async def attempt(operation):
            try:
                return await operation
            except Exception as error:
                failures.append(str(error))

        final = await attempt(r.delete_table())
        if final:
            r.record("routes-cleanup", final)
            if not (final["entries"] == final["handle_refs"] == final["neighbour_refs"] == final["quarantine"] == 0
                    and final["installs"] == final["deletes"] and final["errors"] == r.route_errors):
                failures.append(final)
        for spec in FLOWS:
            await attempt(command(r.target, r.session, "conntrack", "-D", "-p", spec["proto"],
                                  "--orig-src", spec["lan"], "--orig-dst", WAN_IP,
                                  "--sport", str(spec["sport"]), "--dport", str(DPORT), check=False))
        # Tests can stop while a base route is withdrawn or an alias remains.
        # Exact prefix ownership was checked before setup; verify final absence.
        for prefix in (HOST_ROUTE, ROUTE):
            await attempt(command(r.target, r.session, "ip", "route", "del", prefix,
                                  "tos", "0x10", "dev", TARGET_LAN_IF, check=False))
        await attempt(command(r.target, r.session, "ip", "route", "del", HOST_ROUTE, "dev", TARGET_LAN_IF, check=False))
        for agent, args in reversed(cleanup):
            await attempt(command(agent, r.session, *args, check=args[:3] != ["ip", "route", "del"]))
        for index, spec in enumerate(PEERS):
            for agent in (r.target, wan):
                result = await attempt(command(agent, r.session, "ip", "-j", "route", "show", "root", f"198.18.{30 + index}.0/24"))
                if result and json.loads(result["stdout"]):
                    failures.append(result)
            await attempt(command(r.target, r.session, "ip", "neigh", "del", spec["lan"], "dev", TARGET_LAN_IF, check=False))
        teardown = f'''
import subprocess
errors = []
for p in {PEERS!r}:
    result = subprocess.run(['ip', 'netns', 'del', p['netns']], capture_output=True, text=True)
    if result.returncode: errors.append(result.stderr)
assert not errors, errors
'''
        result = await attempt(lan_run_python(r.lan, teardown, label="flowtable_routes_cleanup", timeout=15))
        if result and result.rc:
            failures.append(result.stdout)
        assert not failures, failures


async def change_route(r, *args):
    return await command(r.target, r.session, "ip", "route", *args)


async def retired(r, before, label):
    state = await r.wait(lambda s: s["entries"] == 4)
    unchanged(before, state, B)
    assert by_key(state).keys() == keys(B), state
    # Both of A's connections retire, with however many directions each had.
    removed = len(by_key(before).keys() & keys(A))
    assert state["route_invalidations"] == before["route_invalidations"] + 2, (before, state)
    assert state["installs"] == before["installs"] and state["deletes"] == before["deletes"] + removed, (before, state)
    assert state["rearms"] == before["rearms"] and not state["invalidation_done"], state
    r.record(label, {"before": before, "after": state})


async def readmitted(r, p, initial, mtu, label):
    flows = routed(mtu)
    state = await peer_warm(r, p, A, label, flows=flows)
    unchanged(initial, state, B)
    for key in peer_keys(A, flows):
        flow = by_key(state)[key]
        assert int(flow["mtu"]) == (mtu if flow["out"] == TARGET_LAN_IF else r.port_mtu), state
    return state


async def test_selective(routes):
    r = routes
    async with peer(r, FLOWS) as p:
        await warm(r, p, ALL, "routes-initial-admission")
        initial = await hardware(r, p, "routes-initial-hardware")
        await p.rpc("start", B, count=0, interval=0.01)
        before = initial
        for label, args, mtu in [
            ("replacement", ["replace", ROUTE, "dev", TARGET_LAN_IF, "mtu", "1100"], 1100),
            ("more-specific", ["add", HOST_ROUTE, "dev", TARGET_LAN_IF, "mtu", "1000"], 1000),
            ("less-specific", ["del", HOST_ROUTE, "dev", TARGET_LAN_IF], 1100),
        ]:
            await change_route(r, *args)
            await retired(r, before, f"routes-{label}-retired")
            before = await readmitted(r, p, initial, mtu, f"routes-{label}-readmitted")

        # Blackhole fallback makes withdrawal real, without sending an ICMP
        # socket error. The original TCP socket remains open across the outage.
        await p.rpc("start", [1], count=0, interval=0.01)
        try:
            await change_route(r, "del", ROUTE, "dev", TARGET_LAN_IF)
            await retired(r, before, "routes-withdrawn")
            for _ in range(6):
                await asyncio.sleep(0.25)
                state = await r.state()
                unchanged(initial, state, B)
                assert by_key(state).keys() == keys(B), state
        finally:
            await change_route(r, "replace", ROUTE, "dev", TARGET_LAN_IF, "mtu", "1100")
        transfer = await p.rpc("stop", [1])
        r.record("routes-tcp-withdrawal", transfer)
        before = await readmitted(r, p, initial, 1100, "routes-restored")

        # A non-selected DSCP alias is omitted by the native selected-alias
        # notifier. Our committed-prefix notification still retires candidates;
        # default-TOS traffic must return using its unchanged 1100-byte route.
        for label, operation in [("alias-add", "add"), ("alias-delete", "del")]:
            await change_route(r, operation, ROUTE, "tos", "0x10", "dev", TARGET_LAN_IF,
                               *(["mtu", "900"] if operation == "add" else []))
            await retired(r, before, f"routes-{label}-retired")
            before = await readmitted(r, p, initial, 1100, f"routes-{label}-readmitted")

        # An exact no-op and a rejected duplicate must not emit a commit event.
        await change_route(r, "replace", ROUTE, "dev", TARGET_LAN_IF, "mtu", "1100")
        duplicate = await command(r.target, r.session, "ip", "route", "add", ROUTE,
                                  "dev", TARGET_LAN_IF, "mtu", "1100", check=False)
        assert duplicate["rc"] != 0, duplicate
        state = await r.state()
        # A's UDP reply re-offers its flow whenever it crosses Linux, and that
        # refusal is decided before RTNL; nothing here should take RTNL at
        # all, and busy moving would name an offer that did.
        assert state["busy"] == before["busy"], ("an offer took RTNL mid-window", before, state)
        peer_unchanged(before, state, ALL, flows=routed(1100))
        reports = await p.rpc("stop", B)
        assert all(report["count"] > 128 for report in reports.values()), reports
        r.record("routes-unaffected-transfers", reports)
        final = await peer_hardware(r, p, "routes-final-hardware", flows=routed(1100))
        unchanged(initial, final, B)
        assert final["rearms"] == initial["rearms"], (initial, final)
        assert final["route_invalidations"] == initial["route_invalidations"] + 12, (initial, final)


async def test_retirement_failure(routes):
    r = routes
    knob = "/proc/fm_ehash_hcsync_fail"
    try:
        async with peer(r, FLOWS) as p:
            before = await warm(r, p, ALL, "routes-barrier-admission")
            # The route change retires every direction behind one barrier,
            # whichever retirement reaches each first; failing that barrier
            # is one failed deletion, and recovery's own proves them all.
            r.route_errors += 1
            result = await r.target.fs_write(r.session, knob, "1")
            assert result["errno"] == 0, result
            await change_route(r, "replace", ROUTE, "dev", TARGET_LAN_IF, "mtu", "1100")
            state = await r.wait(lambda s: s["invalidation_done"] == 1 and s["entries"] == 0)
            assert state["invalidated"] == 1 and state["bindings"] == 2, state
            assert state["handle_refs"] == state["neighbour_refs"] == state["fatal"] == state["quarantine"] == 0, state
            assert state["errors"] == r.route_errors and state["deletes"] == before["deletes"] + 8, state
            assert (await read(r.target, r.session, knob)).strip() == "armed=0"
            reports = await p.batch(ALL)
            after = await r.state()
            assert after["entries"] == 0 and after["installs"] == before["installs"], after
            r.record("routes-barrier", {"before": before, "retired": state,
                                       "software": after, "transfers": reports})
    finally:
        result = await r.target.fs_write(r.session, knob, "0")
        assert result["errno"] == 0, result
        await r.delete_table()
        state = await r.state()
        if state["invalidated"] and not state["fatal"]:
            await r.wait(lambda s: s["rearm_ready"] == 1)
            await r.table()
            await r.delete_table()
