"""Selective route and gateway recovery while the shipping service runs."""
from __future__ import annotations

import asyncio
import json
import os
import time

import pytest
import pytest_asyncio

from ask_orch.client import Agent
from ask_orch.uart import Console
from _topology import LAN_NIC, TARGET_LAN_IF, TARGET_WAN_IF, lan_run_python
from _flowtable_connections import (by_key, peer)
from _flowtable_rig import (artifact_dir, command, console_command, read)
from _flowtable_selective_neighbour import (hardware, keys, unchanged, warm)
from _flowtable_service import (FIRST, managed_service, service_status, supervision_status)
from _flowtable_service_vlan import (attempts, balanced, denied, received)

NETNS, LAN_IF = "ask-ft-service-route", "askftroute"
ADDRESS, NETWORK = "172.29.87.2", "172.29.87.0/24"
NEXT_HOP = "198.18.87.2"
MAC = "02:9d:99:b2:33:e1"
ROUTE = [ADDRESS + "/32", "via", NEXT_HOP, "dev", TARGET_LAN_IF]


@pytest_asyncio.fixture
async def route_service(rig):
    r = rig
    wan = Agent("wan", f"http://{os.environ.get('ASK_WAN_IP', '127.0.0.1')}:9110")
    cleanup, lan_created = [], False
    for agent, prefixes in [(r.target, (NETWORK, NEXT_HOP + "/32")), (wan, (NETWORK,))]:
        for prefix in prefixes:
            result = await command(agent, r.session, "ip", "-j", "route", "show", "root", prefix)
            assert not json.loads(result["stdout"]), result
    neighbours = await command(r.target, r.session, "ip", "-j", "neigh", "show", "to", NEXT_HOP,
                               "dev", TARGET_LAN_IF)
    assert not json.loads(neighbours["stdout"]), neighbours
    dut_wan = json.loads((await command(r.target, r.session, "ip", "-j", "-4", "addr", "show", "dev", TARGET_WAN_IF))["stdout"])
    dut_ip = next(a["local"] for a in dut_wan[0]["addr_info"] if a["family"] == "inet")

    async def change(agent, args, undo):
        await command(agent, r.session, *args)
        cleanup.append((agent, undo))

    try:
        # A distinct gateway address makes this a via-route dependency, not
        # just an on-link endpoint. The destination lives on its loopback.
        setup = f'''
from pathlib import Path
import subprocess
def run(*args): subprocess.run(args, check=True, capture_output=True, text=True)
assert not Path('/var/run/netns/' + {NETNS!r}).exists()
assert not Path('/sys/class/net/' + {LAN_IF!r}).exists()
run('ip', 'netns', 'add', {NETNS!r})
try:
    run('ip', 'link', 'add', 'link', {LAN_NIC!r}, 'name', {LAN_IF!r},
        'netns', {NETNS!r}, 'type', 'macvlan', 'mode', 'bridge')
    run('ip', '-n', {NETNS!r}, 'link', 'set', 'lo', 'up')
    run('ip', '-n', {NETNS!r}, 'link', 'set', {LAN_IF!r}, 'address', {MAC!r}, 'up')
    run('ip', '-n', {NETNS!r}, 'addr', 'add', {NEXT_HOP + '/32'!r}, 'dev', {LAN_IF!r})
    run('ip', '-n', {NETNS!r}, 'addr', 'add', {ADDRESS + '/32'!r}, 'dev', 'lo')
    run('ip', '-n', {NETNS!r}, 'route', 'add', {r.lan_gateway + '/32'!r}, 'dev', {LAN_IF!r})
    run('ip', '-n', {NETNS!r}, 'route', 'add', 'default', 'via', {r.lan_gateway!r})
    run('ip', '-n', {NETNS!r}, 'neigh', 'replace', {r.lan_gateway!r}, 'lladdr',
        {r.dut_lan_mac!r}, 'nud', 'permanent', 'dev', {LAN_IF!r})
except BaseException:
    run('ip', 'netns', 'del', {NETNS!r})
    raise
'''
        result = await lan_run_python(r.lan, setup, label="service_route_setup", timeout=20)
        assert result.rc == 0, result.stdout
        lan_created = True
        # The fallback prevents route withdrawal from following a default
        # route; blackhole avoids an ICMP error aborting the original socket.
        await change(r.target, ["ip", "route", "add", "blackhole", NETWORK],
                     ["ip", "route", "del", "blackhole", NETWORK])
        await change(r.target, ["ip", "route", "add", NEXT_HOP + "/32", "dev", TARGET_LAN_IF],
                     ["ip", "route", "del", NEXT_HOP + "/32", "dev", TARGET_LAN_IF])
        await change(r.target, ["ip", "route", "add", *ROUTE], ["ip", "route", "del", *ROUTE])
        await change(wan, ["ip", "route", "add", ADDRESS + "/32", "via", dut_ip, "dev", r.wan_if],
                     ["ip", "route", "del", ADDRESS + "/32", "via", dut_ip, "dev", r.wan_if])
        for name, value in [("base_reachable_time_ms", 600000), ("retrans_time_ms", 200),
                            ("ucast_solicit", 3), ("mcast_solicit", 3)]:
            key = f"net.ipv4.neigh.{TARGET_LAN_IF}.{name}"
            old = (await read(r.target, r.session, "/proc/sys/" + key.replace(".", "/"))).strip()
            await change(r.target, ["sysctl", "-w", f"{key}={value}"], ["sysctl", "-w", f"{key}={old}"])
        async with managed_service(r, addresses=(r.lan_ip, ADDRESS)):
            yield r
    finally:
        failures = []

        async def attempt(operation):
            try:
                return await operation
            except Exception as error:
                failures.append(repr(error))

        # Independent UART can remove the owned routes even if a fault left
        # management unavailable. Remove all other state after service drain.
        with Console.target(log_path=str(artifact_dir() / "service-route-cleanup-uart.log")) as con:
            await asyncio.to_thread(con.login, "root", None)
            for agent, args in reversed(cleanup):
                if agent is r.target:
                    await attempt(console_command(con, *args))
                else:
                    await attempt(command(agent, r.session, *args))
            await attempt(console_command(con, "ip", "neigh", "del", NEXT_HOP, "dev", TARGET_LAN_IF, check=False))
        if lan_created:
            result = await attempt(lan_run_python(r.lan,
                f"import subprocess\nsubprocess.run(['ip', 'netns', 'del', {NETNS!r}], check=True)\n",
                label="service_route_cleanup", timeout=15))
            if result and result.rc:
                failures.append(result.stdout)
        for agent, prefixes in [(r.target, (NETWORK, NEXT_HOP + "/32")), (wan, (NETWORK,))]:
            for prefix in prefixes:
                result = await attempt(command(agent, r.session, "ip", "-j", "route", "show", "root", prefix))
                if result and json.loads(result["stdout"]):
                    failures.append(result)
        assert not failures, failures


async def route(r):
    return json.loads((await command(r.target, r.session, "ip", "-j", "route", "show", "exact", ADDRESS + "/32"))["stdout"])


async def neighbour(r):
    return json.loads((await command(r.target, r.session, "ip", "-j", "neigh", "show", "to", NEXT_HOP,
                                    "dev", TARGET_LAN_IF))["stdout"])


def selective(initial, state, flows):
    balanced(state, initial["errors"])
    unchanged(initial, state, [0, 1], flows)
    assert state["rearms"] == initial["rearms"] and state["invalidated"] == 0, state
    for flow in state["flows"]:
        if flow["dst"].startswith(ADDRESS + ":"):
            assert flow["nexthop"] == NEXT_HOP, flow


@pytest.mark.parametrize("fault", ["route-withdrawal", "next-hop-unreachable"])
async def test_recovery(route_service, fault):
    r = route_service
    endpoint = {"lan": ADDRESS, "netns": NETNS, "iface": LAN_IF}
    flows = [
        {"id": 0, "proto": "udp", "sport": FIRST, "lan": r.lan_ip},
        {"id": 1, "proto": "tcp", "sport": FIRST, "lan": r.lan_ip},
        {"id": 2, "proto": "udp", "sport": FIRST, **endpoint},
        {"id": 3, "proto": "tcp", "sport": FIRST, **endpoint},
        {"id": 4, "proto": "tcp", "sport": FIRST + 1, **endpoint},
        {"id": 5, "proto": "udp", "sport": FIRST + 2, "lan": r.lan_ip},
        {"id": 6, "proto": "udp", "sport": FIRST + 2, **endpoint},
    ]
    service, original_route = await supervision_status(r), await route(r)
    assert len(original_route) == 1 and original_route[0]["gateway"] == NEXT_HOP, original_route
    async with peer(r, flows, initial_ids=[0, 1, 2, 3, 5, 6], lease=300) as p:
        await warm(r, p, [0, 1, 2, 3], f"service-{fault}-baseline", flows[:4])
        initial = await hardware(r, p, f"service-{fault}-baseline-hardware", flows[:4])
        initial_attempts = await attempts(r)
        for ident in (5, 6):
            await denied(r, p, ident)
        for cycle in range(2):
            label = f"service-{fault}-cycle-{cycle}"
            before = await r.state()
            await p.rpc("start", [2], count=0, interval=0.05, allow_loss=True)
            await p.rpc("start", [3], count=0, interval=0.05)
            started = time.monotonic()
            try:
                if fault == "route-withdrawal":
                    await console_command(r.service_console, "ip", "route", "del", *ROUTE)
                else:
                    armed = await p.rpc("neighbour", ident=2, changes={"arp_ignore": 8, "restore_after": 15})
                    assert armed["arp_ignore"] == 8, armed
                    # Force a real probe of the existing gateway. Recovery
                    # later restores ARP replies only, never inserts a MAC.
                    await console_command(r.service_console, "ip", "neigh", "change", NEXT_HOP,
                                          "dev", TARGET_LAN_IF, "nud", "probe")
                retired = await r.wait(lambda s: by_key(s).keys() == keys([0, 1], flows), timeout=5)
                retirement_seconds = time.monotonic() - started
                assert retirement_seconds < 5, retirement_seconds
                selective(initial, retired, flows)
                counter = "route_invalidations" if fault == "route-withdrawal" else "neighbour_invalidations"
                assert retired[counter] == before[counter] + 2, (before, retired)
                assert retired["deletes"] == before["deletes"] + 4 and retired["installs"] == before["installs"], (before, retired)
                r.record(label + "-retired", {"state": retired, "seconds": retirement_seconds})
                await asyncio.sleep(0.2)  # settle in-flight replies
                stopped = await p.rpc("status")
                wan_before = received(r, 2)
                held_at, samples = time.monotonic(), []
                while time.monotonic() - held_at < 6:
                    await p.batch([0, 1], count=32, interval=0.01)
                    progress = await p.rpc("status")
                    assert not progress["errors"], progress
                    assert all(progress["received"][str(i)] == stopped["received"][str(i)] for i in (2, 3)), progress
                    state = await r.state()
                    selective(initial, state, flows)
                    assert by_key(state).keys() == keys([0, 1], flows), state
                    samples.append({"seconds": time.monotonic() - held_at, "state": state, "peer": progress})
                if fault == "route-withdrawal":
                    assert not await route(r), "controller recreated the withdrawn route"
                else:
                    assert await route(r) == original_route
                    unresolved = await neighbour(r)
                    assert unresolved and set(unresolved[0]["state"]) & {"FAILED", "INCOMPLETE"}, unresolved
                    assert (await p.rpc("neighbour", ident=2, changes={}))["arp_ignore"] == 8
                    assert received(r, 2) > wan_before, "no traffic exercised the unresolved reply path"
                await denied(r, p, 5)
                assert await attempts(r) == initial_attempts
                r.record(label + "-unavailable", {"peer_before": stopped, "samples": samples,
                    "wan_received_before": wan_before, "wan_received_after": received(r, 2),
                    "route": await route(r), "neighbour": await neighbour(r)})
            finally:
                if fault == "route-withdrawal":
                    await console_command(r.service_console, "ip", "route", "replace", *ROUTE)
                else:
                    await p.rpc("neighbour", ident=2, changes={"arp_ignore": 0})
            restored_at = time.monotonic()
            # Keep the loss-tolerant UDP receive window open while neighbour
            # queues drain. Delayed valid replies remain part of that window.
            while True:
                await p.batch([0, 1], count=32, interval=0.01)
                progress = await p.rpc("status")
                assert not progress["errors"], progress
                if (progress["received"]["2"] >= stopped["received"]["2"] + 16
                        and progress["received"]["3"] > stopped["received"]["3"]):
                    break
                assert time.monotonic() - restored_at < 20, progress
            reports = await p.rpc("stop", [2, 3])
            assert reports["2"]["lost"] > 0 and reports["3"]["lost"] == 0 and reports["3"]["count"] > 0, reports
            await warm(r, p, [0, 1, 2, 3], label + "-readmitted", flows[:4])
            ready_seconds = time.monotonic() - restored_at
            assert ready_seconds < 20, ready_seconds
            after = await hardware(r, p, label + "-hardware", flows[:4])
            hardware_seconds = time.monotonic() - restored_at
            assert hardware_seconds < 40, hardware_seconds
            selective(initial, after, flows)
            assert after["installs"] == before["installs"] + 4 and after["deletes"] == before["deletes"] + 4, (before, after)
            assert await route(r) == original_route
            resolved = await neighbour(r)
            assert resolved and resolved[0].get("lladdr") == MAC and "PERMANENT" not in resolved[0]["state"], resolved
            # The hardware proof's identities held for its whole burst, longer
            # than a health-check period: nothing reapplied the policy since.
            assert await attempts(r) == initial_attempts
            for ident in (5, 6):
                await denied(r, p, ident)
            status = await service_status(r)
            assert status["admission_ready"] and status["policy_hash"] == r.service_hash, status
            assert await supervision_status(r) == service
            assert (await read(r.target, r.session, "/proc/sys/kernel/random/boot_id")).strip() == r.service_boot
            r.record(label + "-recovery", {"before": before, "after": after,
                "retirement_seconds": retirement_seconds, "ready_seconds": ready_seconds,
                "hardware_seconds": hardware_seconds, "transfers": reports, "policy_installs": 0})
        await p.rpc("open", [4])
        await warm(r, p, [0, 1, 2, 3, 4], f"service-{fault}-new-connection", flows[:5])
        final = await hardware(r, p, f"service-{fault}-new-hardware", flows[:5])
        selective(initial, final, flows)
        assert await attempts(r) == initial_attempts
        for ident in (5, 6):
            await denied(r, p, ident)
