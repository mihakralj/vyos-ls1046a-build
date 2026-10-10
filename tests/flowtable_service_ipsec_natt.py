"""UDP-encapsulated ESP (NAT-T) through the offload.

xfrm hands the adapter an SA's encapsulation ports in network byte order, and
the SA cache keeps them in host order: cdx_ipsec_set_natt() converts them.
The outbound PDB writes them into the UDP header SEC puts in front of ESP, and
the inbound SA's classifier entry is keyed on them. A symmetric pair such as
4500/4500 cannot tell a byte swap, or the two ports swapped with each other,
from the right answer. So the DUT's end here is 4500 and the peer's 31000, as
behind a NAT, and the wire is checked per direction.

Transport mode is refused: SEC builds the UDP header, and the decapsulation
offset past it, only on its tunnel arms, so a transport SA would leave as bare
ESP. Packet offload has no software fallback (xfrm_dev_state_add() returns
the driver's error for it), so the add fails with the adapter's reason. And an
in-place update may not change an offloaded SA's ports, which the hardware SA
carries, or its output mark, which chose the route that addressed it; no
update reaches the driver.
"""
from __future__ import annotations

from _flowtable_service_ipsec_natt import LOCAL, MARK, OTHER_MARK, PEER

from _flowtable_service_ipsec_natt import (Encapsulated, NATT, PORTS, REQID, encapsulations, natt)

import asyncio
import re
import secrets

import pytest

from _ipsec_helpers import endpoints_down, endpoints_up
from _topology import TARGET_WAN_IF
from _flowtable_connections import (peer)
from _flowtable_rig import (command, read)
from _flowtable_selective_neighbour import (warm)
from _flowtable_service_ipsec import (INNER, Transform, flows_for, hardware, negative, plaintext_probe)
from _flowtable_service_ipsec_replay import (sa_state)


@pytest.mark.parametrize("ipsec_service", [NATT], ids=["dut4500-peer31000"], indirect=True)
@pytest.mark.rfc("3948", section="2.2")
async def test_non_esp_beside_offloaded_natt(ipsec_service):
    """IKE (a zero non-ESP marker, RFC 3948 §2.2) and NAT keepalives (one 0xFF
    byte, §2.3) share the offloaded SA's UDP ports. They must reach the DUT's
    own stack, never SEC, while the SA's traffic runs in hardware."""
    from scapy.all import IP, UDP, Raw, send

    r, flows = ipsec_service, flows_for(ipsec_service)
    dut, peer_port = PORTS

    async def udp_in():
        rows = [line.split() for line in (await read(r.target, r.session, "/proc/net/snmp")).splitlines()
                if line.startswith("Udp:")]
        stats = dict(zip(rows[0][1:], map(int, rows[1][1:])))
        return stats["InDatagrams"] + stats["NoPorts"] + stats["InErrors"]

    async with peer(r, flows, initial_ids=[0, 1, 2, 3], lease=400, listen_addresses=[INNER]) as p:
        await warm(r, p, [0, 1, 2, 3], "natt-coexist-baseline", flows[:4])
        before, received = await r.state(), await udp_in()
        ike = b"\0\0\0\0" + secrets.token_bytes(8) + bytes(8) + b"\x21\x20\x22\x08" + bytes(8)
        send([IP(src=r.ipsec.peer, dst=r.ipsec.outer) / UDP(sport=peer_port, dport=dut) / Raw(data)
              for data in [b"\xff"] * 4 + [ike] * 4], verbose=False)
        await asyncio.sleep(1)
        arrived = await udp_in() - received
        after = await hardware(r, p, "natt-coexist-hardware", flows[:4])
    assert arrived >= 8, f"only {arrived} of 8 non-ESP datagrams reached the DUT's UDP stack"
    refused = {k: after[k] - before[k] for k in before if k.startswith("ipsec_sec_refused")}
    assert not any(refused.values()), refused


@pytest.mark.parametrize("ipsec_service", [NATT], ids=["dut4500-peer31000"], indirect=True)
@pytest.mark.rfc("3948")
async def test_flowtable_service_ipsec_natt(ipsec_service):
    """An ESP-in-UDP SA pair with asymmetric ports is offloaded and carries the
    tunnel in hardware both ways, with each direction's outer ports exactly as
    configured on the wire and SEC's own counters moving."""
    r, flows = ipsec_service, flows_for(ipsec_service)
    dut, peer_port = PORTS
    sas = {"out": r.ipsec.active["out"], "in": r.ipsec.active["in"]}
    # The kernel's view first, so a port mix-up in the configuration is not
    # blamed on the hardware.
    for direction, spi in sas.items():
        shown = (await command(r.target, r.session, "ip", "xfrm", "state", "get",
                               *r.ipsec.state(direction, spi)))["stdout"]
        sport, dport = PORTS if direction == "out" else PORTS[::-1]
        assert f"encap type espinudp sport {sport} dport {dport}" in shown, shown
        assert re.search(rf"crypto offload parameters: dev {TARGET_WAN_IF} dir {direction} mode packet", shown), shown
    before = {direction: await sa_state(r, spi, direction) for direction, spi in sas.items()}
    capture = Encapsulated(r, "ipsec-natt-wire")
    async with capture, peer(r, flows, initial_ids=[0, 1, 2, 3, 5, 6], lease=400, listen_addresses=[INNER]) as p:
        await warm(r, p, [0, 1, 2, 3], "natt-baseline", flows[:4])
        # Flow counters per direction, SA handles on both, and neither SEC
        # submit counter tracking the transfer: the inbound frames were
        # matched by the SA's UDP-keyed entry, not handed up by the CPU.
        await hardware(r, p, "natt-hardware", flows[:4])
        await plaintext_probe(r, p, "natt-plaintext")
        await negative(r, p)
    carried = encapsulations(capture.path, set(sas.values()))
    # The accounting pass publishes SEC's per-SA counters once a second.
    await asyncio.sleep(1.5)
    after = {direction: await sa_state(r, spi, direction) for direction, spi in sas.items()}
    r.record("ipsec-natt", {"wire": {f"{spi:#x} {source} {how}": count for (spi, source, how), count in carried.items()},
                            "before": before, "after": after})
    expected = {(sas["out"], r.dut_wan_mac, (dut, peer_port)), (sas["in"], r.wan_mac, (peer_port, dut))}
    assert set(carried) == expected, (
        "each direction's frames must carry exactly the configured ports: the DUT's from "
        f"{dut} to {peer_port}, the peer's back", carried)
    assert all(count >= 256 for count in carried.values()), carried
    # SEC's per-SA counters, as the accounting pass publishes them.
    for direction in sas:
        assert after[direction]["packets"] - before[direction]["packets"] >= 256, (direction, before, after)


async def test_ipsec_natt_transport_refused(aiohttp_session, target_agent, splat_window):
    """Transport-mode ESP-in-UDP is refused with the adapter's reason, in both
    directions, and leaves no state. The same SA in tunnel mode installs,
    offloaded, with its ports as configured."""
    await endpoints_up(target_agent, aiohttp_session, iface=TARGET_WAN_IF, local=LOCAL, peer=PEER,
                       lladdr="02:00:00:00:04:02")
    identities, results = [], []
    try:
        for direction in ("out", "in"):
            src, dst = (LOCAL, PEER) if direction == "out" else (PEER, LOCAL)
            sport, dport = PORTS if direction == "out" else PORTS[::-1]
            for mode in ("transport", "tunnel"):
                identity = ["src", src, "dst", dst, "proto", "esp", "spi", hex(0x4E000000 | secrets.randbits(24))]
                identities.append(identity)
                added = await command(target_agent, aiohttp_session, "ip", "xfrm", "state", "add", *identity,
                                      "mode", mode, "reqid", REQID, *Transform().algorithms,
                                      "encap", "espinudp", str(sport), str(dport), "0.0.0.0",
                                      "offload", "packet", "dev", TARGET_WAN_IF, "dir", direction, check=False)
                shown = await command(target_agent, aiohttp_session, "ip", "xfrm", "state", "get", *identity,
                                      check=False)
                results.append({"direction": direction, "mode": mode, "add": added, "get": shown})
                if mode == "transport":
                    assert added["rc"] != 0, results[-1]
                    assert "cdx: UDP encapsulation needs tunnel mode" in added["stderr"], results[-1]
                    assert shown["rc"] != 0, ("a refused packet-offload SA was installed anyway", results[-1])
                else:
                    assert added["rc"] == 0, results[-1]
                    assert f"encap type espinudp sport {sport} dport {dport}" in shown["stdout"], results[-1]
                    assert re.search(rf"crypto offload parameters: dev {TARGET_WAN_IF} dir {direction} mode packet",
                                     shown["stdout"]), results[-1]
    finally:
        for identity in identities:
            await command(target_agent, aiohttp_session, "ip", "xfrm", "state", "delete", *identity, check=False)
        await endpoints_down(target_agent, aiohttp_session, iface=TARGET_WAN_IF, local=LOCAL, peer=PEER)


async def test_ipsec_natt_update_keeps_hardware_ports(aiohttp_session, target_agent, splat_window):
    """An in-place update of an offloaded NAT-T SA may not move its ports or
    its output mark.

    The ports are in the hardware SA: SEC writes them in front of every frame
    it encrypts, and the classifier keys the SA's inbound frames on them. The
    output mark chose the route to the peer that addressed the SA's frames,
    and is what the adapter routes the peer with again when it moves. An
    update reaches no driver, so changing either in place would leave the
    hardware on the old value while `ip xfrm state` showed the new one. Each
    such update is refused and the state keeps its ports, mark and offload.
    An update that keeps both still applies: here, a new hard lifetime."""
    await endpoints_up(target_agent, aiohttp_session, iface=TARGET_WAN_IF, local=LOCAL, peer=PEER,
                       lladdr="02:00:00:00:04:02")
    identities, results = [], []
    try:
        for direction in ("out", "in"):
            src, dst = (LOCAL, PEER) if direction == "out" else (PEER, LOCAL)
            ports = PORTS if direction == "out" else PORTS[::-1]
            identity = ["src", src, "dst", dst, "proto", "esp", "spi", hex(0x4F000000 | secrets.randbits(24))]
            state = [*identity, "mode", "tunnel", "reqid", REQID, *Transform().algorithms]
            await command(target_agent, aiohttp_session, "ip", "xfrm", "state", "add", *state, *natt(*ports),
                          "output-mark", MARK, "offload", "packet", "dev", TARGET_WAN_IF, "dir", direction)
            identities.append(identity)
            # The DUT's own port, whichever side of the pair it is on.
            moved = (ports[0] + 1, ports[1]) if direction == "out" else (ports[0], ports[1] + 1)
            # An update names the state's direction: xfrm_state_update()
            # answers ESRCH to one whose direction differs, none included.
            refused = {
                "ports": await command(target_agent, aiohttp_session, "ip", "xfrm", "state", "update", *state,
                                       *natt(*moved), "output-mark", MARK, "dir", direction, check=False),
                "mark": await command(target_agent, aiohttp_session, "ip", "xfrm", "state", "update", *state,
                                      *natt(*ports), "output-mark", OTHER_MARK, "dir", direction, check=False),
            }
            kept = await command(target_agent, aiohttp_session, "ip", "xfrm", "state", "update", *state,
                                 *natt(*ports), "output-mark", MARK, "limit", "time-hard", "86400",
                                 "dir", direction, check=False)
            shown = (await command(target_agent, aiohttp_session, "ip", "-s", "xfrm", "state", "get",
                                   *identity))["stdout"]
            results.append({"direction": direction, "refused": refused, "kept": kept, "shown": shown})
            for what in refused.values():
                assert what["rc"] != 0 and "Invalid argument" in what["stderr"], results[-1]
            assert kept["rc"] == 0, results[-1]
            assert f"encap type espinudp sport {ports[0]} dport {ports[1]}" in shown, results[-1]
            assert re.search(rf"output-mark {MARK}\b", shown), results[-1]
            assert "hard 86400(sec)" in shown, results[-1]
            assert re.search(rf"crypto offload parameters: dev {TARGET_WAN_IF} dir {direction} mode packet",
                             shown), results[-1]
    finally:
        for identity in identities:
            await command(target_agent, aiohttp_session, "ip", "xfrm", "state", "delete", *identity, check=False)
        await endpoints_down(target_agent, aiohttp_session, iface=TARGET_WAN_IF, local=LOCAL, peer=PEER)
