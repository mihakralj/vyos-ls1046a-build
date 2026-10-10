"""Shared flowtable snat fixtures and scenarios."""

from __future__ import annotations

import asyncio
import json
import re
import socket

import pytest
from _flowtable_connections import FLOWS, healthy, peer
from _flowtable_policy import CONFIG, apply, candidate, stop
from _flowtable_rig import DPORT, WAN_IP, artifact_dir, command, console_command, read
from _flowtable_tcp import cpu, cpu_delta, software_tx
from _topology import LAN_NIC, TARGET_LAN_IF, TARGET_WAN_IF
from ask_orch.uart import Console
from _flowtable_connections_peer import UDP_SIZE, payload
from _flowtable_udp_wire import udp_wire_payload


def snat_flows(r, state, external, port):
    healthy(state)
    assert state["entries"] == 2, state
    rows = {f["in"]: f for f in state["flows"]}
    local, remote, translated = f"{r.lan_ip}:{FLOWS[0]['sport']}", f"{WAN_IP}:{DPORT}", f"{external}:{port}"
    for dev, expected in ((TARGET_LAN_IF, (local, remote, translated, remote, WAN_IP)),
                          (TARGET_WAN_IF, (remote, translated, remote, local, r.lan_ip))):
        f = rows[dev]
        assert tuple(f[k] for k in ("src", "dst", "new_src", "new_dst", "nexthop")) == expected, state
        assert f["proto"] == "17" and int(f["mtu"]) == r.port_mtu, state
    return rows


async def snat_warm(r, p, external, port, label):
    for _ in range(10):
        await p.batch([0], 64, 0.01)
        state = await r.state()
        if state["entries"] == 2:
            snat_flows(r, state, external, port)
            r.record(label, state)
            return state
    pytest.fail(f"SNAT did not install: {state}")


async def snat_hardware(r, p, external, port, zero_checksum, label):
    before = await r.state()
    old = snat_flows(r, before, external, port)
    tx_before, cpu_before = await software_tx(r), await cpu(r)
    raw = socket.socket(socket.AF_PACKET, socket.SOCK_RAW, socket.htons(0x800))
    raw.bind((r.wan_if, 0))
    raw.setblocking(False)

    async def capture():
        packets = []
        async with asyncio.timeout(15):
            while len(packets) < 256:
                frame = await asyncio.get_running_loop().sock_recv(raw, 65536)
                data = udp_wire_payload(frame, source_ip=external, destination_ip=WAN_IP,
                                       source_port=port, destination_port=DPORT,
                                       source_mac=r.dut_wan_mac, destination_mac=r.wan_mac,
                                       zero_checksum=zero_checksum)
                if data is not None:
                    packets.append(data)
        return packets

    task = asyncio.create_task(capture())
    try:
        reports = await p.batch([0], 256, 0.015625)
        packets = await task
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        raw.close()
    first = reports[0]["first"]
    assert packets == [payload(0, n, UDP_SIZE) for n in range(first, first + 256)]
    cpu_after, tx_after = await cpu(r), await software_tx(r)
    after = await r.state()
    new = snat_flows(r, after, external, port)
    assert before["installs"] == after["installs"] and before["deletes"] == after["deletes"], (before, after)
    for dev in old:
        assert new[dev]["cookie"] == old[dev]["cookie"]
        assert int(new[dev]["packets"]) - int(old[dev]["packets"]) == 256, (before, after)
        assert int(new[dev]["bytes"]) - int(old[dev]["bytes"]) == 256 * (UDP_SIZE + 42), (before, after)
    tx = {dev: tx_after[dev] - tx_before[dev] for dev in tx_before}
    assert all(0 <= count <= 64 for count in tx.values()), tx
    r.record(label, {"before": before, "after": after, "reports": reports, "software_tx": tx,
                     "cpu": cpu_delta(cpu_before, cpu_after), "wan_wire_packets": len(packets),
                     "lan_wire_packets": reports[0]["count"], "zero_udp_checksum": zero_checksum})
    return after


async def udp_snat(connections, zero_checksum, nat_kind="snat"):
    r = connections
    addresses = json.loads((await command(r.target, r.session, "ip", "-j", "-4", "addr", "show",
                                         "dev", TARGET_WAN_IF))["stdout"])
    external = next(a["local"] for a in addresses[0]["addr_info"] if a["family"] == "inet")
    port = FLOWS[0]["sport"] + 1024
    nat_table = "ask_snat_test"
    assert nat_kind in {"snat", "masquerade"}
    translation = f"snat to {external}:{port}" if nat_kind == "snat" else f"masquerade to :{port}"
    nat = (f"table ip {nat_table} {{ chain postrouting {{ type nat hook postrouting priority 90; "
           f"ip saddr {r.lan_ip} ip daddr {WAN_IP} udp sport {FLOWS[0]['sport']} udp dport {DPORT} "
           f"{translation}; }}; }}")
    flow = {**FLOWS[0], "iface": LAN_NIC, "wire": {
        "source_ip": WAN_IP, "destination_ip": r.lan_ip, "source_port": DPORT,
        "destination_port": FLOWS[0]["sport"], "source_mac": r.dut_lan_mac,
        "destination_mac": r.lan_mac, "zero_checksum": zero_checksum}}
    # Test zero checksums in both directions. Receive captures see the actual
    # post-DUT checksum, independently of endpoint transmit checksum offload.
    echo_socket = r.echo.transport.get_extra_info("socket")
    saved_no_check = echo_socket.getsockopt(socket.SOL_SOCKET, 11)
    echo_socket.setsockopt(socket.SOL_SOCKET, 11, int(zero_checksum))
    await r.delete_table()
    with Console.target(log_path=str(artifact_dir() / "snat-uart.log")) as con:
        await asyncio.to_thread(con.login, "root", None)
        existing = await command(r.target, r.session, "nft", "list", "table", "ip", nat_table, check=False)
        assert existing["rc"] != 0, existing
        # Run before the fixture's legacy priority-100 NAT exemption. Linux
        # retains the resolved mapping when this independent policy is drained.
        await command(r.target, r.session, "nft", nat)
        try:
            await apply(con, candidate(r), r=r)
            # Roll back a hardware insertion once before establishing the same
            # mapping normally. Directional retry must preserve all references.
            armed = await r.target.fs_write(
                r.session, "/sys/module/ask_flowtable/parameters/flowtable_fail_stage", "3")
            assert armed["errno"] == 0, armed
            async with peer(r, [flow]) as p:
                await snat_warm(r, p, external, port, "snat-admission")
                assert (await read(r.target, r.session, "/sys/module/ask_flowtable/parameters/flowtable_fail_stage")).strip() == "0"
                initial = await snat_hardware(r, p, external, port, zero_checksum, "snat-hardware")
                ct_before = await command(r.target, r.session, "conntrack", "-L", "-p", "udp", "--orig-src", r.lan_ip,
                                          "--sport", str(FLOWS[0]["sport"]), "-o", "extended,id")
                # A LAN route update retires both NAT directions and readmits
                # the same socket and conntrack mapping without table recreation.
                # Only the advertised MSS changes, which forwarding never reads:
                # a smaller MTU would keep the UDP reply in Linux.
                await console_command(con, "ip", "route", "replace", f"{r.lan_ip}/32", "dev", TARGET_LAN_IF,
                                      "advmss", "1100")
                routed = await snat_warm(r, p, external, port, "snat-route-readmission")
                assert routed["route_invalidations"] > initial["route_invalidations"]
                assert routed["deletes"] >= initial["deletes"] + 2
                await snat_hardware(r, p, external, port, zero_checksum, "snat-route-hardware")
                # Keep sending while the policy drains. No socket or conntrack
                # replacement is permitted before software forwarding is proved.
                await p.rpc("start", [0], count=0, interval=0.01)
                await stop(con)
                crossing = await p.rpc("stop", [0])
                assert crossing["0"]["count"] > 0 and crossing["0"]["lost"] == 0, crossing
                tx_before = await software_tx(r)
                reports = await p.batch([0], 256, 0.005)
                tx_after = await software_tx(r)
                tx = {dev: tx_after[dev] - tx_before[dev] for dev in tx_before}
                assert all(count >= 256 for count in tx.values()), tx
                drained = await r.state()
                assert drained["entries"] == drained["bindings"] == drained["handle_refs"] == drained["neighbour_refs"] == 0
                ct_after = await command(r.target, r.session, "conntrack", "-L", "-p", "udp", "--orig-src", r.lan_ip,
                                         "--sport", str(FLOWS[0]["sport"]), "-o", "extended,id")
                assert re.findall(r"\bid=\d+", ct_before["stdout"]) == re.findall(r"\bid=\d+", ct_after["stdout"]) != []
                r.record("snat-software", {"crossing": crossing, "reports": reports, "software_tx": tx,
                                           "state": drained, "ct_before": ct_before, "ct_after": ct_after})
                await apply(con, candidate(r), r=r)
                await snat_warm(r, p, external, port, "snat-restored-admission")
                await snat_hardware(r, p, external, port, zero_checksum, "snat-restored-hardware")
        finally:
            echo_socket.setsockopt(socket.SOL_SOCKET, 11, saved_no_check)
            try:
                await stop(con)
            finally:
                try:
                    await command(r.target, r.session, "nft", "delete", "table", "ip", nat_table)
                finally:
                    await console_command(con, "rm", "-f", CONFIG)
