"""ICMP errors for a hardware-translated flow reach the inside host translated.

RFC 5508 REQ-3 and REQ-4 (RFC 3022 4.3): an ICMP error a router beyond the NAT
sends about a translated datagram is addressed to the external mapping and
quotes the translated header. The NAT must deliver it to the inside host with
the outer destination, the quoted source and every checksum rewritten, or
path MTU discovery and traceroute stop working behind it.

The flow the error is about runs in hardware, which only ever sees its own
5-tuple. The error is a different packet -- ICMP, to the gateway's own
address -- so it reaches Linux, whose conntrack still holds the mapping the
hardware entry was built from. This checks that offload leaves that mapping
usable and that the error does not disturb the entry.
"""
from __future__ import annotations

import asyncio
import json
import secrets
import struct

import pytest

from _flowtable_connections import FLOWS, peer
from _flowtable_policy import CONFIG, apply, candidate, stop
from _flowtable_rig import DPORT, WAN_IP, artifact_dir, command, console_command
from _flowtable_snat import snat_flows, snat_warm
from _topology import LAN_NIC, TARGET_WAN_IF
from ask_orch.uart import Console

NAT_TABLE = "ask_nat_icmp_test"
ERRORS = 8
# Type, code and the next-hop MTU a Fragmentation Needed carries. Time
# Exceeded is the one a connected UDP socket does not turn into an error.
CASES = {
    "frag-needed": (3, 4, 1400),
    "port-unreachable": (3, 3, 0),
    "time-exceeded": (11, 0, 0),
}


def checksum(data: bytes) -> int:
    if len(data) % 2:
        data += b"\0"
    total = sum(struct.unpack(f"!{len(data) // 2}H", data))
    while total >> 16:
        total = (total & 0xFFFF) + (total >> 16)
    return ~total & 0xFFFF


def address(raw: bytes) -> str:
    return ".".join(map(str, raw))


def translated(sample):
    """The delivered error's fields and checksums (0 when valid), from a
    sampled Ethernet frame, which holds the whole of these small errors."""
    frame = bytes.fromhex(sample["header"])
    assert sample["length"] == len(frame), sample
    ip = frame[14:]
    ihl, total = (ip[0] & 0xF) * 4, struct.unpack("!H", ip[2:4])[0]
    icmp = ip[ihl:total]
    quoted = icmp[8:]
    qihl, qtotal = (quoted[0] & 0xF) * 4, struct.unpack("!H", quoted[2:4])[0]
    udp = quoted[qihl:qtotal]
    pseudo = quoted[12:20] + struct.pack("!BBH", 0, 17, len(udp))
    return {
        "outer": (address(ip[12:16]), address(ip[16:20])),
        "icmp": (icmp[0], icmp[1], struct.unpack("!H", icmp[6:8])[0]),
        "quoted": (address(quoted[12:16]), address(quoted[16:20]), *struct.unpack("!HH", udp[:4])),
        "checksums": (checksum(ip[:ihl]), checksum(icmp), checksum(quoted[:qihl]),
                      checksum(pseudo + udp)),
    }


@pytest.mark.parametrize("kind", list(CASES))
@pytest.mark.rfc("792")
@pytest.mark.rfc("3022")
async def test_icmp_error_is_translated(connections, kind):
    from scapy.all import ICMP, IP, UDP, Ether, Raw, sendp

    r = connections
    icmp_type, icmp_code, mtu = CASES[kind]
    addresses = json.loads((await command(r.target, r.session, "ip", "-j", "-4", "addr", "show",
                                         "dev", TARGET_WAN_IF))["stdout"])
    external = next(a["local"] for a in addresses[0]["addr_info"] if a["family"] == "inet")
    port = FLOWS[0]["sport"] + 1024
    nat = (f"table ip {NAT_TABLE} {{ chain postrouting {{ type nat hook postrouting priority 90; "
           f"ip saddr {r.lan_ip} ip daddr {WAN_IP} udp sport {FLOWS[0]['sport']} udp dport {DPORT} "
           f"snat to {external}:{port}; }}; }}")
    marker = b"ASK-NAT-ICMP-" + secrets.token_bytes(16)
    await r.delete_table()
    with Console.target(log_path=str(artifact_dir() / "nat-icmp-uart.log")) as con:
        await asyncio.to_thread(con.login, "root", None)
        existing = await command(r.target, r.session, "nft", "list", "table", "ip", NAT_TABLE, check=False)
        assert existing["rc"] != 0, existing
        await command(r.target, r.session, "nft", nat)
        try:
            await apply(con, candidate(r), r=r)
            async with peer(r, [FLOWS[0]]) as p:
                before = await snat_warm(r, p, external, port, f"nat-icmp-{kind}-admission")
                # What a router beyond the gateway quotes: the datagram as it
                # left the NAT, translated source and all, with our marker in
                # its payload so the LAN copy can be told from anything else.
                quoted = IP(src=external, dst=WAN_IP, ttl=1) / UDP(sport=port, dport=DPORT) / Raw(marker)
                error = (Ether(src=r.wan_mac, dst=r.dut_wan_mac)
                         / IP(src=WAN_IP, dst=external)
                         / ICMP(type=icmp_type, code=icmp_code, nexthopmtu=mtu)
                         / IP(bytes(quoted)))
                await p.rpc("wire_probe", changes={"action": "start", "iface": LAN_NIC,
                                                   "marker": marker.hex(), "samples": 4,
                                                   "incoming_only": True})
                try:
                    await asyncio.to_thread(sendp, error, iface=r.wan_if, count=ERRORS, inter=0.02,
                                            verbose=False)
                    await asyncio.sleep(0.3)
                    result = await p.rpc("wire_probe", changes={"action": "status"})
                finally:
                    await p.rpc("wire_probe", changes={"action": "stop"})
                after = await r.state()
                fields = [translated(s) for s in result.get("samples", [])]
                r.record(f"nat-icmp-{kind}", {"probe": result, "fields": fields,
                                              "before": before, "after": after})
                assert result["received"] == ERRORS and len(fields) == 4, result
                for f in fields:
                    assert f["outer"] == (WAN_IP, r.lan_ip), f
                    assert f["icmp"] == (icmp_type, icmp_code, mtu), f
                    assert f["quoted"] == (r.lan_ip, WAN_IP, FLOWS[0]["sport"], DPORT), f
                    # Outer IP, ICMP, quoted IP and quoted UDP all still sum.
                    assert f["checksums"] == (0, 0, 0, 0), f
                # The error went through Linux and left the entry alone.
                rows, now = snat_flows(r, before, external, port), snat_flows(r, after, external, port)
                assert {d: f["cookie"] for d, f in rows.items()} == {d: f["cookie"] for d, f in now.items()}
                assert (before["installs"], before["deletes"]) == (after["installs"], after["deletes"])
        finally:
            # A Fragmentation Needed taught the LAN host a smaller path to the
            # WAN host; later tests expect the link's.
            await asyncio.to_thread(r.lan.run, "ip route flush cache", 15)
            try:
                await stop(con)
            finally:
                try:
                    await command(r.target, r.session, "nft", "delete", "table", "ip", NAT_TABLE)
                finally:
                    await console_command(con, "rm", "-f", CONFIG)
