"""A280/A287: authenticate the decrypting SA before hardware forwarding.

Also exercise faithful CPU exceptions and legitimate rekey overlap.
"""
from __future__ import annotations

from _flowtable_service_ipsec_provenance import (COUNT, KEY, SPI_B, add_b, inject)

import asyncio
import json
import re
import secrets

import pytest

from _topology import TARGET_LAN_IF, TARGET_WAN_IF
from _flowtable_connections import (by_key, healthy, peer)
from _flowtable_rig import (DPORT, WAN_IP, command, read)
from _flowtable_service import (FIRST, OBSERVE_TABLE)
from _flowtable_selective_neighbour import (warm)
from _flowtable_service_ipsec import (INNER, LAN_INNER, Transform, Wire, flows_for, hardware)
from _flowtable_service_ipsec_replay import (AEAD, esp, sa_state, xfrm_mib)


@pytest.mark.parametrize("ipsec_service", [Transform(), AEAD["rfc4106-icv16"]],
                         ids=["cbc", "gcm"], indirect=True)
async def test_ipsec_provenance_binding(ipsec_service):
    r, flows = ipsec_service, flows_for(ipsec_service)
    async with peer(r, flows, initial_ids=[0, 1, 2, 3], lease=400,
                    listen_addresses=[INNER]) as p:
        await warm(r, p, [0, 1, 2, 3], "provenance-binding", flows[:4])
        await hardware(r, p, "provenance-binding-hardware", flows[:4])
        # Raw A injection advances its replay window beyond the peer kernel.
        # Close TCP first so teardown does not need replies with old sequences.
        await p.rpc("close", [1, 3])
        await add_b(r)
        marker = b"ASK-IPSEC-SA-" + secrets.token_bytes(8)
        before = xfrm_mib(await read(r.target, r.session, "/proc/net/xfrm_stat"))
        b = await inject(r, p, SPI_B, marker + b"B")
        after = xfrm_mib(await read(r.target, r.session, "/proc/net/xfrm_stat"))
        r.record("provenance-binding-policy", {"before": before, "after": after})
        assert b["wire"]["received"] == 0, "B matched A's decrypted forwarding entry"
        assert after["XfrmInTmplMismatch"] > before["XfrmInTmplMismatch"], (before, after)
        a = await inject(r, p, r.ipsec.active["in"], marker + b"A")
        assert a["wire"]["received"] == COUNT, a
        assert a["software_forwarded"] == 0, a
        for sample in a["wire"]["samples"]:
            frame = bytes.fromhex(sample["header"])
            assert sample["length"] == 270, sample
            assert frame[12:14] == b"\x08\x00", sample
        healthy(a["after"])


async def test_ipsec_provenance_misses_and_mtu(ipsec_service):
    r = ipsec_service
    async with peer(r, flows_for(r), initial_ids=[2], lease=300,
                    listen_addresses=[INNER]) as p:
        await p.batch([2], count=32, interval=0.03)
        await r.wait(lambda s: KEY in by_key(s))
        spi = r.ipsec.active["in"]
        # The MTU cases first, while the flow under test is fresh in hardware;
        # their injections are on its own tuple and keep it there. Once the
        # injected sequence numbers lead, the peer's own replies fall behind
        # the replay window, so nothing else can refresh the flow -- and the
        # MAC-miss cycles below, on other ports, take about the flowtable's
        # 30 s timeout between them. Each block's sequence numbers follow the
        # last one's.
        for cycle, size in enumerate([256, 1100, 1200]):
            result = await inject(r, p, spi, b"ASK-MAC-MTU-" + secrets.token_bytes(8),
                                  size=size, seq=100000 + cycle * COUNT)
            assert result["wire"]["received"] == COUNT, result
            assert result["software_forwarded"] == 0, result
            assert all(s["length"] == size + 14 for s in result["wire"]["samples"]), result
            healthy(result["after"])
        for cycle in range(4):
            mac = f"02:53:00:12:34:{cycle:02x}"
            counter = f"original_mac_{cycle}"
            await command(r.target, r.session, "nft", f"""
add counter inet {OBSERVE_TABLE} {counter}
add rule inet {OBSERVE_TABLE} forward ether saddr {mac} ip saddr {INNER} udp sport {DPORT + cycle + 1} counter name {counter}
""")
            result = await inject(r, p, spi, b"ASK-MAC-MISS-" + secrets.token_bytes(8),
                                  sport=DPORT + cycle + 1, seq=100000 + (cycle + 3) * COUNT,
                                  source_mac=mac)
            assert result["wire"]["received"] == COUNT, result
            observed = await command(r.target, r.session, "nft", "-j", "list", "counter",
                                     "inet", OBSERVE_TABLE, counter)
            counts = [item["counter"]["packets"] for item in
                      json.loads(observed["stdout"])["nftables"] if "counter" in item]
            r.record(counter, {"source_mac": mac, "counts": counts})
            assert counts == [COUNT], "Linux must observe each packet's original source MAC"
            healthy(result["after"])


async def test_ipsec_provenance_accounting(ipsec_service):
    """Conntrack must not charge SEC's internal identity tag to the packet."""
    r = ipsec_service
    await command(r.target, r.session, "nft",
                  "add flowtable inet ask_flowtable fast { hook ingress priority 0; "
                  f"devices = {{ {TARGET_LAN_IF}, {TARGET_WAN_IF} }}; flags offload; counter; }}")

    async def reply_count():
        result = await command(r.target, r.session, "conntrack", "-L", "-p", "udp",
                               "--orig-src", LAN_INNER, "--orig-dst", INNER,
                               "--sport", str(FIRST), "--dport", str(DPORT), "-o", "extended")
        counts = re.findall(r"packets=(\d+) bytes=(\d+)", result["stdout"])
        assert len(counts) == 2, result
        return tuple(map(int, counts[1]))

    async with peer(r, flows_for(r), initial_ids=[2], lease=180,
                    listen_addresses=[INNER]) as p:
        await p.batch([2], count=32, interval=0.03)
        await r.wait(lambda s: KEY in by_key(s))
        # Let the delayed statistics pass consume the initial software/HW mix.
        await asyncio.sleep(8)
        before = await reply_count()
        result = await inject(r, p, r.ipsec.active["in"],
                              b"ASK-SA-ACCOUNT-" + secrets.token_bytes(8))
        assert result["wire"]["received"] == COUNT and result["software_forwarded"] == 0, result
        expected = (before[0] + COUNT, before[1] + COUNT * 256)
        for _ in range(30):
            after = await reply_count()
            if after[0] >= expected[0]:
                break
            await asyncio.sleep(0.5)
        r.record("provenance-accounting", {"before": before, "after": after, "expected": expected})
        assert after == expected


@pytest.mark.parametrize("ipsec_service", [Transform(), Transform(encap=(4500, 31000)),
                                         AEAD["rfc4106-icv16"],
                                         Transform(AEAD["rfc4106-icv16"].algorithms, (4500, 31000))],
                         ids=["esp-cbc", "natt-cbc", "esp-gcm", "natt-gcm"], indirect=True)
async def test_ipsec_provenance_outbound_root(ipsec_service):
    """A287: B's decrypted ESP/UDP must not use A's encrypted-output root."""
    from scapy.all import Ether, IP, UDP, Raw, sendp, rdpcap
    import struct

    r, flows = ipsec_service, flows_for(ipsec_service)
    label = "provenance-root-natt" if r.ipsec.transform.encap else "provenance-root-esp"
    async with peer(r, flows, initial_ids=[0, 1, 2, 3], lease=300,
                    listen_addresses=[INNER]) as p:
        await warm(r, p, [0, 1, 2, 3], label, flows[:4])
        await hardware(r, p, label + "-hardware", flows[:4])
        await add_b(r)
        marker = b"ASK-SA-ROOT-" + secrets.token_bytes(16)
        payload = struct.pack("!II", r.ipsec.active["out"], 12345) + marker.ljust(128, b".")
        # Exactly the tuple/SPI keyed by A's outbound root, but carried as
        # plaintext inside B. There is no inner ICV: the old root emits it
        # without SEC or Linux ever validating the inner packet.
        inner = IP(src=r.ipsec.outer, dst=WAN_IP)
        if r.ipsec.transform.encap:
            sport, dport = r.ipsec.transform.encap
            inner = inner / UDP(sport=sport, dport=dport)
        else:
            inner.proto = 50
        inner = bytes(inner / Raw(payload))
        frames = [Ether(src=r.wan_mac, dst=r.dut_wan_mac) /
                  IP(src=WAN_IP, dst=r.ipsec.outer, proto=50) /
                  Raw(esp(r.ipsec.transform.algorithms, SPI_B, 100000 + i, inner))
                  for i in range(COUNT)]
        before = await sa_state(r, SPI_B, "in")
        wire = Wire(r, label + "-forged")
        # The ingress ESP is a positive capture control.
        # Match Ethernet, not plain `ip`: also catch a leaked internal VLAN.
        wire.filter = f"ether host {r.wan_mac}"
        async with wire:
            await asyncio.to_thread(sendp, frames, iface=r.wan_if, inter=0.01, verbose=False)
            await asyncio.sleep(2)
        packets = rdpcap(str(wire.path))
        controls = [packet for packet in packets if packet[Ether].src == r.wan_mac
                    and IP in packet and packet[IP].proto == 50]
        leaked = [packet for packet in packets if marker in bytes(packet)]
        after = await sa_state(r, SPI_B, "in")
        state = await r.state()
        r.record(label + "-result", {"encap": r.ipsec.transform.encap,
                 "before": before, "after": after, "ingress": len(controls),
                 "leaked": [bytes(packet).hex() for packet in leaked], "state": state})
        assert len(controls) >= COUNT, len(controls)
        assert after["packets"] - before["packets"] >= COUNT, (before, after)
        assert not leaked, "B's plaintext used A's encrypted-output root"
        healthy(state)
