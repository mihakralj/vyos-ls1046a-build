"""IPv6 recovery under the shipping service, with independent IPv4 controls."""
from __future__ import annotations

import asyncio
import json
import os
import socket
import time

import pytest
import pytest_asyncio

from ask_orch.client import Agent
from ask_orch.uart import Console
from _topology import LAN_NIC, TARGET_LAN_IF, TARGET_WAN_IF, lan_run_python
from _flowtable_connections import (by_key, peer)
from _flowtable_failslab import (same_service, slab_fault)
from _flowtable_rig import (artifact_dir, DPORT, Echo, command, console_command, read)
from _flowtable_selective_neighbour import (hardware, keys, unchanged, warm)
from _flowtable_service import (FIRST, managed_service, supervision_status, wait_service)
from _flowtable_service_vlan import (attempts, balanced, denied, received)

NETNS, LAN_IF = "ask-ft-service-ipv6", "askftsv6"
LAN_GATEWAY, NEXT_HOP = "fd42:6173:6:1::1", "fd42:6173:6:1::2"
WAN_GATEWAY, WAN = "fd42:6173:6:2::1", "fd42:6173:6:2::2"
ADDRESS, NETWORK = "fd42:6173:6:3::2", "fd42:6173:6:3::/64"
MAC = "02:9d:99:b2:33:e6"
ROUTE = [ADDRESS + "/128", "via", NEXT_HOP, "dev", TARGET_LAN_IF, "mtu", "1400"]


@pytest_asyncio.fixture
async def ipv6_service(rig):
    r = rig
    wan = Agent("wan", f"http://{os.environ.get('ASK_WAN_IP', '127.0.0.1')}:9110")
    cleanup, lan_created, transport = [], False, None
    for agent in (r.target, wan):
        routes = await command(agent, r.session, "ip", "-j", "-6", "route", "show", "table", "all", "root", "fd42:6173:6::/48")
        assert not json.loads(routes["stdout"]), routes

    async def change(agent, args, undo):
        await command(agent, r.session, *args)
        cleanup.append((agent, undo))

    try:
        forwarding = (await read(r.target, r.session, "/proc/sys/net/ipv6/conf/all/forwarding")).strip()
        await change(r.target, ["sysctl", "-w", "net.ipv6.conf.all.forwarding=1"],
                     ["sysctl", "-w", "net.ipv6.conf.all.forwarding=" + forwarding])
        for agent, address, dev in [(r.target, LAN_GATEWAY, TARGET_LAN_IF),
                                    (r.target, WAN_GATEWAY, TARGET_WAN_IF), (wan, WAN, r.wan_if)]:
            await change(agent, ["ip", "-6", "addr", "add", address + "/64", "dev", dev, "nodad"],
                         ["ip", "-6", "addr", "del", address + "/64", "dev", dev])
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
    run('ip', '-n', {NETNS!r}, '-6', 'addr', 'add', {NEXT_HOP + '/64'!r}, 'dev', {LAN_IF!r}, 'nodad')
    run('ip', '-n', {NETNS!r}, '-6', 'addr', 'add', {ADDRESS + '/128'!r}, 'dev', 'lo', 'nodad')
    run('ip', '-n', {NETNS!r}, '-6', 'route', 'add', 'default', 'via', {LAN_GATEWAY!r})
    run('ip', '-n', {NETNS!r}, '-6', 'neigh', 'replace', {LAN_GATEWAY!r}, 'lladdr',
        {r.dut_lan_mac!r}, 'nud', 'permanent', 'dev', {LAN_IF!r})
except BaseException:
    run('ip', 'netns', 'del', {NETNS!r})
    raise
'''
        result = await lan_run_python(r.lan, setup, label="service_ipv6_setup", timeout=20)
        assert result.rc == 0, result.stdout
        lan_created = True
        await change(r.target, ["ip", "-6", "route", "add", "blackhole", NETWORK],
                     ["ip", "-6", "route", "del", "blackhole", NETWORK])
        await change(r.target, ["ip", "-6", "route", "add", *ROUTE], ["ip", "-6", "route", "del", *ROUTE])
        await change(wan, ["ip", "-6", "route", "add", ADDRESS + "/128", "via", WAN_GATEWAY, "dev", r.wan_if],
                     ["ip", "-6", "route", "del", ADDRESS + "/128", "via", WAN_GATEWAY, "dev", r.wan_if])
        for agent, address, mac, dev in [(r.target, WAN, r.wan_mac, TARGET_WAN_IF),
                                        (wan, WAN_GATEWAY, r.dut_wan_mac, r.wan_if)]:
            await change(agent, ["ip", "-6", "neigh", "replace", address, "lladdr", mac, "nud", "permanent", "dev", dev],
                         ["ip", "-6", "neigh", "del", address, "dev", dev])
        for name, value in [("base_reachable_time_ms", 600000), ("retrans_time_ms", 200),
                            ("ucast_solicit", 3), ("mcast_solicit", 3)]:
            key = f"net.ipv6.neigh.{TARGET_LAN_IF}.{name}"
            old = (await read(r.target, r.session, "/proc/sys/" + key.replace(".", "/"))).strip()
            await change(r.target, ["sysctl", "-w", f"{key}={value}"], ["sysctl", "-w", f"{key}={old}"])
        echo = Echo()
        echo.received = r.echo.received  # unique flow IDs across both families
        transport, _ = await asyncio.get_running_loop().create_datagram_endpoint(
            lambda: echo, local_addr=(WAN, DPORT), family=socket.AF_INET6)
        async with managed_service(r, extra_paths=[(ADDRESS, WAN)]):
            yield r
    finally:
        if transport:
            transport.close()
        failures = []

        async def attempt(operation):
            try:
                return await operation
            except Exception as error:
                failures.append(repr(error))

        with Console.target(log_path=str(artifact_dir() / "service-ipv6-cleanup-uart.log")) as con:
            await asyncio.to_thread(con.login, "root", None)
            await attempt(console_command(con, "ip", "-6", "neigh", "del", NEXT_HOP, "dev", TARGET_LAN_IF, check=False))
            for agent, args in reversed(cleanup):
                if agent is r.target:
                    await attempt(console_command(con, *args))
                else:
                    await attempt(command(agent, r.session, *args))
        if lan_created:
            result = await attempt(lan_run_python(r.lan,
                f"import subprocess\nsubprocess.run(['ip', 'netns', 'del', {NETNS!r}], check=True)\n",
                label="service_ipv6_cleanup", timeout=15))
            if result and result.rc:
                failures.append(result.stdout)
        for agent in (r.target, wan):
            result = await attempt(command(agent, r.session, "ip", "-j", "-6", "route", "show", "table", "all", "root", "fd42:6173:6::/48"))
            if result and json.loads(result["stdout"]):
                failures.append(result)
        assert not failures, failures


def flows_for(r, protocol="tcp"):
    endpoint = {"lan": ADDRESS, "netns": NETNS, "iface": LAN_IF, "connect_ip": WAN}
    return [
        {"id": 0, "proto": "udp", "sport": FIRST, "lan": r.lan_ip},
        {"id": 1, "proto": "tcp", "sport": FIRST, "lan": r.lan_ip},
        {"id": 2, "proto": "udp", "sport": FIRST, **endpoint},
        {"id": 3, "proto": "tcp", "sport": FIRST, **endpoint},
        {"id": 4, "proto": protocol, "sport": FIRST + 1, **endpoint},
        {"id": 5, "proto": "udp", "sport": FIRST + 2, "lan": r.lan_ip},
        {"id": 6, "proto": "udp", "sport": FIRST + 2, **endpoint},
    ]


async def route(r):
    return json.loads((await command(r.target, r.session, "ip", "-j", "-6", "route", "show", "exact", ADDRESS + "/128"))["stdout"])


async def neighbour(r):
    return json.loads((await command(r.target, r.session, "ip", "-j", "-6", "neigh", "show", "to", NEXT_HOP, "dev", TARGET_LAN_IF))["stdout"])


async def negative(r, p):
    for ident in (5, 6):
        await denied(r, p, ident)


def selective(initial, state, flows):
    balanced(state, initial["errors"])
    unchanged(initial, state, [0, 1], flows)
    assert state["rearms"] == initial["rearms"] and state["invalidated"] == 0, state
    for flow in state["flows"]:
        if flow["dst"].startswith("[" + ADDRESS + "]:"):
            assert flow["family"] == "6" and flow["nexthop"] == NEXT_HOP, flow


@pytest.mark.parametrize("fault", ["route-withdrawal", "ndp-unreachable"])
async def test_prerequisite(ipv6_service, fault):
    r = ipv6_service
    flows = flows_for(r)
    service, original_route = await supervision_status(r), await route(r)
    async with peer(r, flows, initial_ids=[0, 1, 2, 3, 5, 6], lease=300, listen_addresses=[WAN]) as p:
        await warm(r, p, [0, 1, 2, 3], "ipv6-baseline", flows[:4])
        initial = await hardware(r, p, "ipv6-baseline-hardware", flows[:4])
        initial_attempts = await attempts(r)
        await negative(r, p)
        for cycle in range(2):
            label = f"ipv6-{fault}-{cycle}"
            before = await r.state()
            await p.rpc("start", [2], count=0, interval=0.05, allow_loss=True)
            await p.rpc("start", [3], count=0, interval=0.05)
            started = time.monotonic()
            try:
                if fault == "route-withdrawal":
                    await console_command(r.service_console, "ip", "-6", "route", "del", *ROUTE)
                else:
                    armed = await p.rpc("ndp", ident=2, changes={"blocked": True, "restore_after": 15})
                    assert armed["blocked"], armed
                    await console_command(r.service_console, "ip", "-6", "neigh", "change", NEXT_HOP,
                                          "dev", TARGET_LAN_IF, "nud", "probe")
                retired = await r.wait(lambda s: by_key(s).keys() == keys([0, 1], flows), timeout=5)
                retirement_seconds = time.monotonic() - started
                assert retirement_seconds < 5, retirement_seconds
                selective(initial, retired, flows)
                counter = "route_invalidations" if fault == "route-withdrawal" else "neighbour_invalidations"
                assert retired[counter] == before[counter] + 2, (before, retired)
                r.record(label + "-retired", {"state": retired, "seconds": retirement_seconds})
                await asyncio.sleep(0.2)
                stopped, wan_before = await p.rpc("status"), received(r, 2)
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
                    assert not await route(r), "controller recreated the withdrawn IPv6 route"
                else:
                    assert await route(r) == original_route
                    unresolved = await neighbour(r)
                    assert unresolved and set(unresolved[0]["state"]) & {"FAILED", "INCOMPLETE"}, unresolved
                    hit = await p.rpc("ndp", ident=2, changes={})
                    assert hit["blocked"] and hit["packets"] > 0, hit
                    assert received(r, 2) > wan_before
                await denied(r, p, 5)
                assert await attempts(r) == initial_attempts
                r.record(label + "-unavailable", {"peer_before": stopped, "samples": samples,
                    "route": await route(r), "neighbour": await neighbour(r)})
            finally:
                if fault == "route-withdrawal":
                    await console_command(r.service_console, "ip", "-6", "route", "replace", *ROUTE)
                else:
                    await p.rpc("ndp", ident=2, changes={"blocked": False})
            restored_at = time.monotonic()
            while True:
                await p.batch([0, 1], count=32, interval=0.01)
                progress = await p.rpc("status")
                assert not progress["errors"], progress
                if (progress["received"]["2"] >= stopped["received"]["2"] + 16
                        and progress["received"]["3"] > stopped["received"]["3"]):
                    break
                assert time.monotonic() - restored_at < 20, progress
            reports = await p.rpc("stop", [2, 3])
            assert reports["2"]["lost"] > 0 and reports["3"]["lost"] == 0, reports
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
            assert resolved[0].get("lladdr") == MAC and "PERMANENT" not in resolved[0]["state"], resolved
            # The hardware proof's identities held for its whole burst, longer
            # than a health-check period: nothing reapplied the policy since.
            assert await attempts(r) == initial_attempts
            await negative(r, p)
            await same_service(r, service)
            r.record(label + "-recovery", {"before": before, "after": after,
                "retirement_seconds": retirement_seconds, "ready_seconds": ready_seconds,
                "hardware_seconds": hardware_seconds, "transfers": reports, "policy_installs": 0})
        await p.rpc("open", [4])
        await warm(r, p, [0, 1, 2, 3, 4], "ipv6-new-connection", flows[:5])
        final = await hardware(r, p, "ipv6-new-hardware", flows[:5])
        selective(initial, final, flows)
        assert await attempts(r) == initial_attempts
        await negative(r, p)


async def test_missing_table(ipv6_service):
    r, flows = ipv6_service, flows_for(ipv6_service)
    service = await supervision_status(r)
    async with peer(r, flows, initial_ids=[0, 1, 2, 3, 5, 6], lease=240, listen_addresses=[WAN]) as p:
        await warm(r, p, [0, 1, 2, 3], "ipv6-table-baseline", flows[:4])
        initial = await hardware(r, p, "ipv6-table-before", flows[:4])
        for cycle in range(2):
            label, before_attempts = f"ipv6-table-{cycle}", await attempts(r)
            await negative(r, p)
            started = time.monotonic()
            await console_command(r.service_console, "nft", "delete", "table", "inet", "ask_flowtable")
            await p.batch([0, 1, 2, 3], count=64, interval=0.01)
            status = await wait_service(r, timeout=20, policy_hash=r.service_hash)
            await warm(r, p, [0, 1, 2, 3], label + "-readmitted", flows[:4])
            ready_seconds = time.monotonic() - started
            assert ready_seconds < 20, ready_seconds
            after = await hardware(r, p, label + "-hardware", flows[:4])
            hardware_seconds = time.monotonic() - started
            assert hardware_seconds < 40, hardware_seconds
            balanced(after, initial["errors"])
            # One reinstall for the deleted table, and none across the
            # hardware proof's burst, a full health-check period.
            assert await attempts(r) == before_attempts + 1
            await negative(r, p)
            await same_service(r, service)
            r.record(label + "-recovery", {"status": status, "after": after,
                "ready_seconds": ready_seconds, "hardware_seconds": hardware_seconds})
        await p.rpc("open", [4])
        await warm(r, p, [0, 1, 2, 3, 4], "ipv6-table-new", flows[:5])
        await hardware(r, p, "ipv6-table-new-hardware", flows[:5])
        await negative(r, p)


@pytest.mark.parametrize("target", ["actions", "hardware"])
@pytest.mark.parametrize("protocol", ["udp", "tcp"])
async def test_failslab(ipv6_service, target, protocol):
    r, flows = ipv6_service, flows_for(ipv6_service, protocol)
    service = await supervision_status(r)
    label = f"ipv6-slab-{target}-{protocol}"
    async with peer(r, flows, initial_ids=[0, 1, 2, 3, 5, 6], lease=180, listen_addresses=[WAN]) as p:
        await warm(r, p, [0, 1, 2, 3], label + "-baseline", flows[:4])
        initial = await hardware(r, p, label + "-before", flows[:4])
        initial_attempts = await attempts(r)
        await negative(r, p)
        async with slab_fault(r, target, label, keep_alive=(p, [0, 1, 2, 3])) as fault:
            started = time.monotonic()
            await p.rpc("open", [4])
            await p.batch([4], count=32, interval=0.01)
            hit = await fault.hit()
            await warm(r, p, [0, 1, 2, 3, 4], label + "-readmitted", flows[:5])
            ready_seconds = time.monotonic() - started
            assert ready_seconds < 20, ready_seconds
            after = await hardware(r, p, label + "-hardware", flows[:5])
            hardware_seconds = time.monotonic() - started
            assert hardware_seconds < 40, hardware_seconds
            unchanged(initial, after, [0, 1, 2, 3], flows)
            assert after["rearms"] == initial["rearms"] and after["errors"] == initial["errors"]
            assert await attempts(r) == initial_attempts
            await negative(r, p)
            await same_service(r, service)
            r.record(label + "-recovery", {"before": initial, "after": after, "hit": hit,
                "ready_seconds": ready_seconds, "hardware_seconds": hardware_seconds})
