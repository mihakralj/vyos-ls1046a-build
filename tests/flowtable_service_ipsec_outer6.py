"""An IPsec tunnel between IPv6 endpoints is offloaded like an IPv4 one.

The SAs run between the DUT's and the WAN host's WAN-segment IPv6 addresses;
the inner traffic is IPv4, as in every other IPsec file. Both halves are
taken by the hardware -- the outbound one addressed through IPv6's route and
neighbour discovery -- and the tunnel's flows are carried without Linux and
leave nothing in the clear.
"""
from __future__ import annotations

import pytest

from _flowtable_connections import peer
from _flowtable_selective_neighbour import warm
from _flowtable_service_ipsec import (INNER, Transform, Wire, flows_for, hardware,
                                      plaintext_probe)

OUTER6 = Transform(outer6=True)


@pytest.mark.parametrize("ipsec_service", [OUTER6], indirect=True, ids=["outer6"])
async def test_tunnel_is_offloaded(ipsec_service):
    from scapy.all import ESP, IPv6, rdpcap

    r, flows = ipsec_service, flows_for(ipsec_service)
    states = await r.ipsec.states()
    assert len(states) == 2, states
    assert all("crypto offload parameters" in s for s in states), states
    async with peer(r, flows, initial_ids=[0, 1, 2, 3], lease=300,
                    listen_addresses=[INNER]) as p:
        await warm(r, p, [0, 1, 2, 3], "ipsec-outer6", flows[:4])
        # Every flow in hardware, the tunnel's on its SAs, ESP on the wire.
        await hardware(r, p, "ipsec-outer6-hardware", flows[:4])
        await plaintext_probe(r, p, "ipsec-outer6-plaintext")
        # The outbound frames carry the IPv6 endpoints the SA names.
        async with Wire(r, "ipsec-outer6-endpoints") as wire:
            await p.batch([2], count=32, interval=0.02)
        esp = [packet for packet in rdpcap(str(wire.path)) if ESP in packet]
        assert len(esp) >= 32, len(esp)
        assert {(packet[IPv6].src, packet[IPv6].dst) for packet in esp} == {
            (r.ipsec.outer, r.ipsec.peer)}, {(packet[IPv6].src, packet[IPv6].dst) for packet in esp}
