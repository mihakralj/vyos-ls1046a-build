"""Portable four-node rig checks and upstream instrumented-agent checks."""

import asyncio
import json
import os
import uuid

import pytest

from ask_orch.lab import Bench
from _flowtable_rig import Echo


@pytest.mark.portable
def test_lab_management_and_links(lab_report):
    assert lab_report["ok"], lab_report
    for node in lab_report["nodes"].values():
        for device, links in node["ports"].items():
            assert len(links) == 1 and "LOWER_UP" in links[0]["flags"], (node["name"], device, links)


@pytest.mark.portable
@pytest.mark.parametrize("family", [4, 6])
@pytest.mark.parametrize("role", ["lan", "wan"])
def test_lab_forwarding(lab_report, family, role):
    bench = Bench.load(os.environ["ASK_LAB_DUT"])
    peer = bench.wan if role == "lan" else bench.lan
    address = peer["address" if family == 4 else "address6"]
    endpoint = bench.endpoint(role)
    route = endpoint.execute(["ip", "-j", f"-{family}", "route", "get", address])
    assert route.rc == 0, route.stdout
    routes = json.loads(route.stdout)
    assert routes[0]["dev"] == bench.dut["nic"] and routes[0].get("gateway"), routes
    result = endpoint.execute(["ping", f"-{family}", "-n", "-c", "4", "-W", "2",
                               "-I", bench.dut["nic"], address], timeout=15)
    assert result.rc == 0, result.stdout


@pytest.mark.portable
@pytest.mark.parametrize("family", [4, 6])
@pytest.mark.parametrize("protocol", ["udp", "tcp"])
async def test_lab_numbered_payloads(lab_report, family, protocol):
    bench = Bench.load(os.environ["ASK_LAB_DUT"])
    address = bench.wan["address" if family == 4 else "address6"]
    payloads = [f"{uuid.uuid4().hex}:{index}".encode() for index in range(8)]

    async def echo_tcp(reader, writer):
        try:
            writer.write(await reader.read())
            await writer.drain()
        finally:
            writer.close()
            await writer.wait_closed()

    if protocol == "udp":
        server, echo = await asyncio.get_running_loop().create_datagram_endpoint(Echo, local_addr=(address, 0))
        port = server.get_extra_info("sockname")[1]
    else:
        server = await asyncio.start_server(echo_tcp, address, 0)
        port = server.sockets[0].getsockname()[1]
    source = f'''
import json, socket
payloads = {payloads!r}
received = []
with socket.socket(socket.AF_INET if {family} == 4 else socket.AF_INET6,
                   socket.SOCK_DGRAM if {protocol!r} == "udp" else socket.SOCK_STREAM) as client:
    client.settimeout(5)
    client.connect(({address!r}, {port}))
    if {protocol!r} == "udp":
        for payload in payloads:
            client.send(payload)
            received.append(client.recv(4096))
        assert received == payloads, (payloads, received)
    else:
        expected = b"".join(payloads)
        client.sendall(expected)
        client.shutdown(socket.SHUT_WR)
        data = bytearray()
        while True:
            part = client.recv(4096)
            if not part:
                break
            data.extend(part)
        assert bytes(data) == expected, (expected, data)
print(json.dumps({{"packets": len(payloads), "ok": True}}))
'''
    try:
        result = await asyncio.to_thread(bench.endpoint("lan").python, source, 30)
        assert result["rc"] == 0, result["stdout"]
        assert json.loads(result["stdout"])["ok"] is True
        if protocol == "udp":
            assert all(echo.received[payload] == 1 for payload in payloads), echo.received
    finally:
        server.close()
        if protocol == "tcp":
            await server.wait_closed()


async def test_target_health(aiohttp_session, target_agent):
    h = await target_agent.health(aiohttp_session)
    assert h["ok"]
    assert "version" in h
    assert h.get("uptime_s", 0) > 0


async def test_no_boot_splats(bench_health):
    # The session preflight reads and verifies boot history before mutations.
    assert bench_health["boot"]["complete"] is True
