"""Shared flowtable dnat fixtures and scenarios."""

import asyncio
import json
import re

import pytest
from _flowtable_connections import by_key, healthy, peer
from _flowtable_policy import CONFIG, apply, candidate, stop
from _flowtable_rig import (
    DPORT,
    HEALTH_BASELINE,
    SPORT,
    WAN_IP,
    artifact_dir,
    command,
    console_command,
    read,
)
from _flowtable_tcp import cpu, cpu_delta, software_tx
from _topology import LAN_NIC, TARGET_LAN_IF, TARGET_WAN_IF
from ask_orch.uart import Console
from _flowtable_connections_peer import Flow
from _flowtable_udp_wire import udp_capture_socket, udp_wire_payload

PUBLIC_PORT = DPORT + 1000

WAN_SPORT = SPORT + 160

assert DPORT not in (WAN_SPORT, WAN_SPORT + 1), (WAN_SPORT, DPORT)


class LocalClients:
    """The same exact-record client used on the LAN VM, initiated from the WAN host."""
    def __init__(self, flows):
        self.flows = {f["id"]: Flow(f, udp_wire_payload, udp_capture_socket) for f in flows}
        self.controller = None

    async def batch(self, ids, count=64, interval=0.01):
        async with asyncio.TaskGroup() as group:
            tasks = {i: group.create_task(self.flows[i].run(count, interval)) for i in ids}
        result = {i: task.result() for i, task in tasks.items()}
        for ident, report in result.items():
            size = 16384 if self.flows[ident].spec["proto"] == "tcp" else 256
            assert report["count"] == report["received"] == count and report["lost"] == 0, report
            assert report["bytes"] == count * size, report
        if self.controller:
            status = await self.controller.rpc("servers")
            assert not status["errors"], status
        return result

    async def close(self):
        await asyncio.gather(*(f.close() for f in self.flows.values()))


def translation_rows(r, state, expected):
    healthy(state)
    rows = by_key(state)
    assert rows.keys() == expected.keys(), (state, expected)
    for key, values in expected.items():
        assert tuple(rows[key][k] for k in ("new_src", "new_dst", "nexthop")) == values, rows[key]
        assert int(rows[key]["mtu"]) == r.port_mtu, rows[key]
    return rows


async def warm(r, clients, expected):
    for _ in range(10):
        await clients.batch([0, 1])
        state = await r.state()
        if state["entries"] == len(expected):
            translation_rows(r, state, expected)
            return state
    pytest.fail(f"NAT admission did not converge: {state}")


async def hardware(r, clients, expected, label):
    before = await r.state()
    old = translation_rows(r, before, expected)
    tx_before, cpu_before = await software_tx(r), await cpu(r)
    # 256 packets is the assertion below; the spacing only has to keep some
    # wall-clock in the window so an event-driven retirement would have room
    # to fire. It does not soak against a timer: nothing time-driven can
    # retire a flow carrying continuous traffic, since idle expiry needs 30s
    # and the spurious-retirement defect needs ninety plus a competing flow on
    # the same tuple. Four seconds at 64/s, not eight at 32/s.
    reports = await clients.batch([0, 1], 256, 0.015625)
    cpu_after, tx_after = await cpu(r), await software_tx(r)
    after = await r.state()
    new = translation_rows(r, after, expected)
    assert (after["installs"], after["deletes"]) == (before["installs"], before["deletes"]), (before, after)
    for key in old:
        assert new[key]["cookie"] == old[key]["cookie"], (before, after)
        packets = int(new[key]["packets"]) - int(old[key]["packets"])
        if key[1] == "17":
            assert packets == 256 and int(new[key]["bytes"]) - int(old[key]["bytes"]) == 76288, (before, after)
        else:
            assert packets >= 256 * 16384 // 1500, (before, after)
    tx = {dev: tx_after[dev] - tx_before[dev] for dev in tx_before}
    assert all(0 <= count <= 512 for count in tx.values()), tx
    r.record(label, {"before": before, "after": after, "transfers": reports,
                     "software_tx": tx, "cpu": cpu_delta(cpu_before, cpu_after)})
    return after


async def dnat(rig, zero_checksum, double_nat=False):
    r = rig
    case = "zero-checksum" if zero_checksum else "checksum"
    if double_nat:
        case = "double-" + case
    record = r.record
    r.record = lambda name, data: record(f"{case}-{name}", data)
    addresses = json.loads((await command(r.target, r.session, "ip", "-j", "-4", "addr", "show",
                                         "dev", TARGET_WAN_IF))["stdout"])
    external = next(a["local"] for a in addresses[0]["addr_info"] if a["family"] == "inet")
    sport = WAN_SPORT + int(zero_checksum)
    nat_table = "ask_double_nat_test" if double_nat else "ask_dnat_test"
    server_peer = [r.lan_gateway, sport + 1024] if double_nat else [WAN_IP, sport]
    flows, expected = [], {}
    for ident, proto in enumerate(("udp", "tcp")):
        spec = {"id": ident, "proto": proto, "sport": sport, "lan": WAN_IP,
                "connect_ip": external, "connect_port": PUBLIC_PORT,
                "server_peer": server_peer}
        if proto == "udp":
            spec.update(iface=r.wan_if, wire={"source_ip": external, "destination_ip": WAN_IP,
                "source_port": PUBLIC_PORT, "destination_port": sport,
                "source_mac": r.dut_wan_mac, "destination_mac": r.wan_mac, "zero_checksum": zero_checksum},
                server_wire={"source_ip": server_peer[0], "destination_ip": r.lan_ip,
                "source_port": server_peer[1], "destination_port": DPORT,
                "source_mac": r.dut_lan_mac, "destination_mac": r.lan_mac, "zero_checksum": zero_checksum})
        flows.append(spec)
        protocol = "17" if proto == "udp" else "6"
        client, public, server = f"{WAN_IP}:{sport}", f"{external}:{PUBLIC_PORT}", f"{r.lan_ip}:{DPORT}"
        translated = f"{server_peer[0]}:{server_peer[1]}"
        expected[(TARGET_WAN_IF, protocol, client, public)] = (translated, server, r.lan_ip)
        expected[(TARGET_LAN_IF, protocol, server, translated)] = (public, client, WAN_IP)
    servers = [{"address": r.lan_ip, "port": DPORT, "iface": LAN_NIC, "zero_checksum": zero_checksum}]
    rules = " ".join(f"ip saddr {WAN_IP} ip daddr {external} {p} sport {sport} {p} dport {PUBLIC_PORT} "
                     f"dnat to {r.lan_ip}:{DPORT};" for p in ("udp", "tcp"))
    source_rules = ""
    if double_nat:
        translations = " ".join(f"ip saddr {WAN_IP} ip daddr {r.lan_ip} {p} sport {sport} {p} dport {DPORT} "
                                f"snat to {server_peer[0]}:{server_peer[1]};" for p in ("udp", "tcp"))
        source_rules = f"chain postrouting {{ type nat hook postrouting priority 90; {translations} }};"
    nat = f"table ip {nat_table} {{ chain prerouting {{ type nat hook prerouting priority -110; {rules} }}; {source_rules} }}"
    policy = candidate(r)
    policy["scope"] = [{"source": WAN_IP, "destination": external,
                        "source_port": sport, "destination_port": PUBLIC_PORT}]
    clients = LocalClients(flows)
    with Console.target(log_path=str(artifact_dir() / f"dnat-{case}-uart.log")) as con:
        await asyncio.to_thread(con.login, "root", None)
        async def conntracks():
            result = {}
            for proto in ("udp", "tcp"):
                result[proto] = (await command(r.target, r.session, "conntrack", "-L", "-p", proto,
                    "--orig-src", WAN_IP, "--orig-dst", external, "--sport", str(sport),
                    "--dport", str(PUBLIC_PORT), "-o", "extended,id"))["stdout"]
            return result

        assert (await command(r.target, r.session, "nft", "list", "table", "ip", nat_table, check=False))["rc"] != 0
        await command(r.target, r.session, "nft", nat)
        try:
            await apply(con, policy, r=r)
            armed = await r.target.fs_write(
                r.session, "/sys/module/ask_flowtable/parameters/flowtable_fail_stage", "3")
            assert armed["errno"] == 0, armed
            async with peer(r, flows, initial_ids=[], servers=servers) as controller:
                clients.controller = controller
                try:
                    for flow in clients.flows.values():
                        await asyncio.wait_for(flow.open({"lan": WAN_IP, "wan": external, "dport": PUBLIC_PORT}), 10)
                    await warm(r, clients, expected)
                    assert (await read(r.target, r.session, "/sys/module/ask_flowtable/parameters/flowtable_fail_stage")).strip() == "0"
                    initial = await hardware(r, clients, expected, "dnat-hardware")
                    ct_before = await conntracks()
                    # A real route change that leaves the path a full frame:
                    # the advertised MSS is no forwarding input, and a smaller
                    # MTU would keep the UDP direction toward the LAN in Linux.
                    await console_command(con, "ip", "route", "replace", r.lan_ip + "/32", "dev", TARGET_LAN_IF,
                                          "advmss", "1100")
                    routed = await warm(r, clients, expected)
                    assert routed["route_invalidations"] > initial["route_invalidations"], (initial, routed)
                    assert routed["deletes"] >= initial["deletes"] + 4, (initial, routed)
                    await hardware(r, clients, expected, "dnat-route-hardware")
                    crossing = asyncio.create_task(clients.batch([0, 1], 256, 0.01))
                    try:
                        await stop(con)
                        reports = await crossing
                    finally:
                        crossing.cancel()
                        await asyncio.gather(crossing, return_exceptions=True)
                    tx_before = await software_tx(r)
                    software = await clients.batch([0, 1], 256, 0.005)
                    tx_after = await software_tx(r)
                    tx = {dev: tx_after[dev] - tx_before[dev] for dev in tx_before}
                    assert all(count >= 256 for count in tx.values()), tx
                    drained = await r.state()
                    assert drained["entries"] == drained["bindings"] == drained["handle_refs"] == drained["neighbour_refs"] == 0
                    ct_after = await conntracks()
                    for proto in ct_before:
                        assert re.findall(r"\bid=\d+", ct_before[proto]) == re.findall(r"\bid=\d+", ct_after[proto]) != []
                    r.record("dnat-software", {"crossing": reports, "transfers": software, "software_tx": tx,
                                              "state": drained, "ct_before": ct_before, "ct_after": ct_after})
                    await apply(con, policy, r=r)
                    await warm(r, clients, expected)
                    await hardware(r, clients, expected, "dnat-restored-hardware")
                    status = await controller.rpc("servers")
                    assert not status["errors"], status
                    assert all(status["counts"][str(i)] == f.serial for i, f in clients.flows.items()), status
                    r.record("dnat-received", status)
                finally:
                    await clients.close()
                closed = await r.wait(lambda s: s["entries"] == 2 and all(f["proto"] == "17" for f in s["flows"]), timeout=8)
                healthy(closed)
                ct_closed = await conntracks()
                # The final ACK may precede asynchronous hardware deletion,
                # leaving native conntrack in LAST_ACK (the routed TCP contract).
                assert any(state in ct_closed["tcp"] for state in ("TIME_WAIT", "LAST_ACK")), ct_closed
                r.record("dnat-fin", {"state": closed, "conntracks": ct_closed})
        finally:
            try:
                await stop(con)
            finally:
                await command(r.target, r.session, "nft", "delete", "table", "ip", nat_table)
                await console_command(con, "rm", "-f", CONFIG)
                for proto in ("tcp", "udp"):
                    await command(r.target, r.session, "conntrack", "-D", "-p", proto, "--orig-src", WAN_IP,
                        "--orig-dst", external, "--sport", str(sport), "--dport", str(PUBLIC_PORT), check=False)
            final = await r.state()
            assert final["installs"] == final["deletes"] and final["fatal"] == final["quarantine"] == 0, final
            assert final["errors"] == HEALTH_BASELINE["errors"], (final, HEALTH_BASELINE)
            r.record("dnat-cleanup", final)
