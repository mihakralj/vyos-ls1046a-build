"""Combined IPv4 NAT across ports and hairpin routing on one physical port."""
import asyncio
import json
import re

import pytest
import pytest_asyncio

from ask_orch.uart import Console
from _topology import LAN_NIC, TARGET_LAN_IF, TARGET_WAN_IF, lan_run_python
from _flowtable_connections import (healthy, peer)
from _flowtable_dnat import (PUBLIC_PORT, hardware, warm, dnat as _dnat)
from _flowtable_rig import (artifact_dir, DPORT, HEALTH_BASELINE, SPORT, command, console_command, read)
from _flowtable_policy import (CONFIG, apply, candidate, stop)
from _flowtable_tcp import (software_tx)

GATEWAY = "198.18.42.1"
CLIENT = {"netns": "ask-ft-hairpin-client", "iface": "askfthc", "lan": "198.18.42.2", "mac": "02:9d:99:b2:42:02"}
SERVER = {"netns": "ask-ft-hairpin-server", "iface": "askfths", "lan": "198.18.42.3", "mac": "02:9d:99:b2:42:03"}


@pytest.mark.parametrize("zero_checksum", [False, True], ids=["checksum", "zero-checksum"])
async def test_flowtable_double_nat(rig, zero_checksum):
    await _dnat(rig, zero_checksum, double_nat=True)


@pytest_asyncio.fixture
async def hairpin(rig):
    r = rig
    peers = [CLIENT, SERVER]
    setup = f'''
import pathlib, subprocess
peers = {peers!r}
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
        run('ip', '-n', p['netns'], 'addr', 'add', p['lan'] + '/24', 'dev', p['iface'])
        run('ip', '-n', p['netns'], 'route', 'add', 'default', 'via', {GATEWAY!r})
except BaseException:
    for p in reversed(created): run('ip', 'netns', 'del', p['netns'])
    raise
'''
    result = await lan_run_python(r.lan, setup, label="flowtable_hairpin_setup", timeout=20)
    assert result.rc == 0, result.stdout
    cleanup = []
    try:
        async def change(args, undo):
            await command(r.target, r.session, *args)
            cleanup.append(undo)
        await change(["ip", "addr", "add", GATEWAY + "/24", "dev", TARGET_LAN_IF],
                     ["ip", "addr", "del", GATEWAY + "/24", "dev", TARGET_LAN_IF])
        for p in peers:
            await change(["ip", "route", "add", p["lan"] + "/32", "dev", TARGET_LAN_IF],
                         ["ip", "route", "del", p["lan"] + "/32", "dev", TARGET_LAN_IF])
            await change(["ip", "neigh", "add", p["lan"], "lladdr", p["mac"], "nud", "permanent", "dev", TARGET_LAN_IF],
                         ["ip", "neigh", "del", p["lan"], "dev", TARGET_LAN_IF])
        yield r
    finally:
        errors = []
        for argv in reversed(cleanup):
            try:
                await command(r.target, r.session, *argv)
            except Exception as error:
                errors.append(str(error))
        teardown = f'''
import subprocess
errors = []
for p in {peers!r}:
    result = subprocess.run(['ip', 'netns', 'del', p['netns']], capture_output=True, text=True)
    if result.returncode: errors.append(result.stderr)
assert not errors, errors
'''
        result = await lan_run_python(r.lan, teardown, label="flowtable_hairpin_cleanup", timeout=15)
        assert result.rc == 0 and not errors, (result.stdout, errors)


@pytest.mark.parametrize("zero_checksum", [False, True], ids=["checksum", "zero-checksum"])
async def test_flowtable_hairpin(hairpin, zero_checksum):
    r = hairpin
    case = "hairpin-zero-checksum" if zero_checksum else "hairpin-checksum"
    record = r.record
    r.record = lambda name, data: record(f"{case}-{name}", data)
    # The original public destination lives on the WAN interface, but every
    # data frame must traverse the DUT's LAN MAC twice. Client/server namespaces
    # have distinct MACs and no local route that can bypass the NAT gateway.
    addresses = json.loads((await command(r.target, r.session, "ip", "-j", "-4", "addr", "show",
                                         "dev", TARGET_WAN_IF))["stdout"])
    external = next(a["local"] for a in addresses[0]["addr_info"] if a["family"] == "inet")
    sport = SPORT + 16 + int(zero_checksum)
    mapped_port = sport + 1024
    flows, expected = [], {}
    for ident, proto in enumerate(("udp", "tcp")):
        spec = {**CLIENT, "id": ident, "proto": proto, "sport": sport,
                "connect_ip": external, "connect_port": PUBLIC_PORT, "server_peer": [GATEWAY, mapped_port]}
        if proto == "udp":
            spec.update(wire={"source_ip": external, "destination_ip": CLIENT["lan"],
                "source_port": PUBLIC_PORT, "destination_port": sport,
                "source_mac": r.dut_lan_mac, "destination_mac": CLIENT["mac"], "zero_checksum": zero_checksum},
                server_wire={"source_ip": GATEWAY, "destination_ip": SERVER["lan"],
                "source_port": mapped_port, "destination_port": DPORT,
                "source_mac": r.dut_lan_mac, "destination_mac": SERVER["mac"], "zero_checksum": zero_checksum})
        flows.append(spec)
        protocol = "17" if proto == "udp" else "6"
        client, public = f"{CLIENT['lan']}:{sport}", f"{external}:{PUBLIC_PORT}"
        server, mapped = f"{SERVER['lan']}:{DPORT}", f"{GATEWAY}:{mapped_port}"
        expected[(TARGET_LAN_IF, protocol, client, public)] = (mapped, server, SERVER["lan"])
        expected[(TARGET_LAN_IF, protocol, server, mapped)] = (public, client, CLIENT["lan"])
    servers = [{**SERVER, "address": SERVER["lan"], "port": DPORT, "zero_checksum": zero_checksum}]
    source = " ".join(f"ip saddr {CLIENT['lan']} ip daddr {SERVER['lan']} {p} sport {sport} {p} dport {DPORT} "
                      f"snat to {GATEWAY}:{mapped_port};" for p in ("udp", "tcp"))
    destination = " ".join(f"ip saddr {CLIENT['lan']} ip daddr {external} {p} sport {sport} {p} dport {PUBLIC_PORT} "
                           f"dnat to {SERVER['lan']}:{DPORT};" for p in ("udp", "tcp"))
    nat_table = "ask_hairpin_test"
    nat = (f"table ip {nat_table} {{ chain prerouting {{ type nat hook prerouting priority -110; {destination} }}; "
           f"chain postrouting {{ type nat hook postrouting priority 90; {source} }}; }}")
    policy = candidate(r)
    policy["scope"] = [{"source": CLIENT["lan"], "destination": external,
                        "source_port": sport, "destination_port": PUBLIC_PORT}]
    with Console.target(log_path=str(artifact_dir() / f"{case}-uart.log")) as con:
        await asyncio.to_thread(con.login, "root", None)
        async def conntracks():
            result = {}
            for proto in ("udp", "tcp"):
                result[proto] = (await command(r.target, r.session, "conntrack", "-L", "-p", proto,
                    "--orig-src", CLIENT["lan"], "--orig-dst", external, "--sport", str(sport),
                    "--dport", str(PUBLIC_PORT), "-o", "extended,id"))["stdout"]
            return result
        assert (await command(r.target, r.session, "nft", "list", "table", "ip", nat_table, check=False))["rc"] != 0
        await command(r.target, r.session, "nft", nat)
        try:
            await apply(con, policy, r=r)
            armed = await r.target.fs_write(
                r.session, "/sys/module/ask_flowtable/parameters/flowtable_fail_stage", "3")
            assert armed["errno"] == 0, armed
            async with peer(r, flows, servers=servers) as p:
                await warm(r, p, expected)
                assert (await read(r.target, r.session, "/sys/module/ask_flowtable/parameters/flowtable_fail_stage")).strip() == "0"
                initial = await hardware(r, p, expected, "hardware")
                assert all(f["in"] == f["out"] == TARGET_LAN_IF for f in initial["flows"]), initial
                ct_before = await conntracks()
                # A real route change that leaves the path a full frame, as
                # in the DNAT case: a smaller MTU would keep the UDP direction
                # toward the server in Linux.
                await console_command(con, "ip", "route", "replace", SERVER["lan"] + "/32", "dev", TARGET_LAN_IF,
                                      "advmss", "1100")
                routed = await warm(r, p, expected)
                assert routed["route_invalidations"] > initial["route_invalidations"], (initial, routed)
                assert routed["deletes"] >= initial["deletes"] + 4, (initial, routed)
                await hardware(r, p, expected, "route-hardware")
                await p.rpc("start", [0, 1], count=0, interval=0.01)
                await stop(con)
                crossing = await p.rpc("stop", [0, 1])
                assert all(v["count"] > 0 and v["lost"] == 0 for v in crossing.values()), crossing
                tx_before = await software_tx(r)
                reports = await p.batch([0, 1], 256, 0.005)
                tx_after = await software_tx(r)
                tx = {dev: tx_after[dev] - tx_before[dev] for dev in tx_before}
                assert tx[TARGET_LAN_IF] >= 512, tx
                drained = await r.state()
                assert drained["entries"] == drained["bindings"] == drained["handle_refs"] == drained["neighbour_refs"] == 0
                ct_after = await conntracks()
                for proto in ct_before:
                    assert re.findall(r"\bid=\d+", ct_before[proto]) == re.findall(r"\bid=\d+", ct_after[proto]) != []
                r.record("software", {"crossing": crossing, "transfers": reports, "software_tx": tx,
                                      "state": drained, "ct_before": ct_before, "ct_after": ct_after})
                await apply(con, policy, r=r)
                await warm(r, p, expected)
                await hardware(r, p, expected, "restored-hardware")
                status = await p.rpc("servers")
                assert not status["errors"], status
                r.record("received", status)
                await p.rpc("close", [0, 1])
                closed = await r.wait(lambda s: s["entries"] == 2 and all(f["proto"] == "17" for f in s["flows"]), timeout=8)
                healthy(closed)
                ct_closed = await conntracks()
                assert any(state in ct_closed["tcp"] for state in ("TIME_WAIT", "LAST_ACK")), ct_closed
                r.record("fin", {"state": closed, "conntracks": ct_closed})
        finally:
            try:
                await stop(con)
            finally:
                await command(r.target, r.session, "nft", "delete", "table", "ip", nat_table)
                await console_command(con, "rm", "-f", CONFIG)
                for proto in ("tcp", "udp"):
                    await command(r.target, r.session, "conntrack", "-D", "-p", proto, "--orig-src", CLIENT["lan"],
                        "--orig-dst", external, "--sport", str(sport), "--dport", str(PUBLIC_PORT), check=False)
            final = await r.state()
            assert final["installs"] == final["deletes"] and final["fatal"] == final["quarantine"] == 0, final
            assert final["errors"] == HEALTH_BASELINE["errors"], (final, HEALTH_BASELINE)
            r.record("cleanup", final)
