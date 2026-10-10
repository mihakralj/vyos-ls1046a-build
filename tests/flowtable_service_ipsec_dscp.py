"""An offloaded IPsec tunnel marks packets exactly as Linux does.

At decapsulation RFC 4301 (5.1.2.1) keeps the inner DSCP unless the SA asks
for the outer one, which Linux spells XFRM_STATE_DECAP_DSCP, and RFC 6040
never lets the outer header erase or lower the inner ECN field. The hardware
has to deliver the same inner byte, or a packet's marking depends on whether
its SA was offloaded.

At encapsulation both copy the inner DSCP into the outer header. For ECN SEC
copies the field as it is, RFC 6040's normal mode; Linux sends CE as ECT(0)
(INET_ECN_encapsulate, RFC 3168's full functionality). SEC moves the byte
whole, so it cannot imitate that, and need not: an RFC 6040 decapsulator
delivers the inner CE either way.
"""
from __future__ import annotations

from _flowtable_service_ipsec_provenance import (COUNT, KEY, inject)

import secrets

import pytest

from _ipsec_helpers import endpoints_down, endpoints_up
from _topology import TARGET_LAN_IF, TARGET_WAN_IF
from _flowtable_connections import (by_key, peer)
from _flowtable_rig import DPORT, command
from _flowtable_service import FIRST
from _flowtable_service_ipsec import (INNER, LAN_INNER, Wire, flows_for)
from _flowtable_service_ipsec_replay import CBC

AF41 = 0x88
NOT_ECT, ECT0, CE = 0x00, 0x02, 0x03
OUTBOUND = (TARGET_LAN_IF, "17", f"{LAN_INNER}:{FIRST}", f"{INNER}:{DPORT}")

# Endpoints, SPIs and a reqid of the refusal case's own, from the RFC 2544
# benchmarking block like every other IPsec file's.
FLAG_LOCAL, FLAG_PEER = "198.18.114.1", "198.18.114.2"
FLAG_REQID = "49340"
FLAG_STATES = {
    "out": ["src", FLAG_LOCAL, "dst", FLAG_PEER, "proto", "esp", "spi", hex(0x44530001)],
    "in": ["src", FLAG_PEER, "dst", FLAG_LOCAL, "proto", "esp", "spi", hex(0x44530002)],
}
# The marking a state asks for that SEC, which moves the traffic-class byte
# whole, cannot give it.
FLAG_CASES = {
    "decap-dscp": ("in", ["flag", "decap-dscp"],
                   "cdx: SEC cannot take the outer DSCP without the outer ECN"),
    "dont-encap-dscp": ("out", ["extra-flag", "dont-encap-dscp"],
                        "cdx: SEC copies the inner DSCP into the outer header"),
    "noecn": ("out", ["flag", "noecn"], "cdx: SEC copies the inner ECN into the outer header"),
}


def inner_tos(result):
    """The inner IPv4 TOS byte of each sampled frame on the LAN."""
    return {bytes.fromhex(s["header"])[15] for s in result["wire"]["samples"]}


# Inner byte, outer byte, and the inner byte decapsulation delivers: the inner
# DSCP and ECN kept, and an outer CE copied in where the inner header is
# ECN-capable (RFC 6040 4.2, as Linux does). An outer CE over a Not-ECT inner
# header is a congestion mark the packet cannot carry on, which no compliant
# encapsulator lets happen; RFC 6040 drops it, and so does SEC (None). Linux's
# xfrm forwards it unmarked, its IP tunnels drop it.
DECAP_CASES = {
    "ect0": (AF41 | ECT0, NOT_ECT, AF41 | ECT0),
    "ce": (AF41 | CE, NOT_ECT, AF41 | CE),
    "outer-ce": (AF41 | ECT0, CE, AF41 | CE),
    "outer-ce-not-ect": (AF41 | NOT_ECT, CE, None),
}


@pytest.mark.parametrize("case", list(DECAP_CASES))
async def test_decap_keeps_the_inner_marking(ipsec_service, case):
    r = ipsec_service
    tos, outer_tos, expected = DECAP_CASES[case]
    async with peer(r, flows_for(r), initial_ids=[2], lease=300,
                    listen_addresses=[INNER]) as p:
        await p.batch([2], count=32, interval=0.03)
        await r.wait(lambda s: KEY in by_key(s))
        spi = r.ipsec.active["in"]
        # SEC decrypts every packet of an offloaded SA: the flow's own tuple
        # is then forwarded by hardware, another port by Linux. Either way the
        # inner header must arrive as Linux's own decapsulation leaves it.
        dropped = expected is None
        forwarded = await inject(r, p, spi, b"ASK-DSCP-HW-" + secrets.token_bytes(8),
                                 tos=tos, outer_tos=outer_tos, seq=100000, dropped=dropped)
        missed = await inject(r, p, spi, b"ASK-DSCP-SW-" + secrets.token_bytes(8),
                              sport=DPORT + 9, tos=tos, outer_tos=outer_tos, seq=100000 + COUNT,
                              dropped=dropped)
        assert forwarded["software_forwarded"] == 0, forwarded
        for result in (forwarded, missed):
            if dropped:
                assert result["wire"]["received"] == 0, result
            else:
                assert result["wire"]["received"] == COUNT, result
                assert inner_tos(result) == {expected}, result
        if dropped:
            # The flow itself is unharmed: its ordinary traffic still crosses.
            await p.batch([2], count=32, interval=0.03)


@pytest.mark.parametrize("ecn", [NOT_ECT, ECT0, CE], ids=["not-ect", "ect0", "ce"])
async def test_encap_carries_the_inner_marking(ipsec_service, ecn):
    from scapy.all import ESP, IP, rdpcap

    r = ipsec_service
    flows = flows_for(r)
    flows[2] = {**flows[2], "tos": AF41 | ecn}
    async with peer(r, flows, initial_ids=[2], lease=300, listen_addresses=[INNER]) as p:
        await p.batch([2], count=32, interval=0.03)
        await r.wait(lambda s: OUTBOUND in by_key(s))
        before = by_key(await r.state())
        async with Wire(r, f"dscp-encap-{ecn}") as wire:
            await p.batch([2], count=64, interval=0.02)
        after = by_key(await r.state())
        carried = int(after[OUTBOUND]["packets"]) - int(before[OUTBOUND]["packets"])
        assert carried >= 64, (before[OUTBOUND], after[OUTBOUND])
        outer = [packet[IP].tos for packet in rdpcap(str(wire.path)) if ESP in packet]
        assert len(outer) >= 64, len(outer)
        assert set(outer) == {AF41 | ecn}, sorted(set(outer))


@pytest.mark.parametrize("case", list(FLAG_CASES))
async def test_unexpressible_marking_is_refused(aiohttp_session, target_agent, splat_window,
                                                case):
    """A state asking for a marking SEC cannot produce is refused packet
    offload, with the reason, and the same state installs in software, which
    honours it."""
    direction, flags, reason = FLAG_CASES[case]
    identity = FLAG_STATES[direction]
    await endpoints_up(target_agent, aiohttp_session, iface=TARGET_WAN_IF, local=FLAG_LOCAL,
                       peer=FLAG_PEER, lladdr="02:00:00:00:0a:02")
    try:
        added = await command(target_agent, aiohttp_session, "ip", "xfrm", "state", "add", *identity,
                              "mode", "tunnel", "reqid", FLAG_REQID, *CBC, *flags,
                              "offload", "packet", "dev", TARGET_WAN_IF, "dir", direction, check=False)
        shown = await command(target_agent, aiohttp_session, "ip", "xfrm", "state", "get", *identity,
                              check=False)
        result = {"add": added, "get": shown}
        assert added["rc"] != 0 and reason in added["stderr"], result
        assert shown["rc"] != 0, result
        added = await command(target_agent, aiohttp_session, "ip", "xfrm", "state", "add", *identity,
                              "mode", "tunnel", "reqid", FLAG_REQID, *CBC, *flags,
                              check=False)
        shown = await command(target_agent, aiohttp_session, "ip", "xfrm", "state", "get", *identity,
                              check=False)
        result = {"add": added, "get": shown}
        assert added["rc"] == 0 and shown["rc"] == 0, result
        # iproute2 shows a state's flags but not its extra flags; the refusal
        # above already proved the kernel received dont-encap-dscp.
        if flags[0] == "flag":
            assert flags[1] in shown["stdout"], result
        assert "crypto offload parameters" not in shown["stdout"], result
    finally:
        await command(target_agent, aiohttp_session, "ip", "xfrm", "state", "delete", *identity,
                      check=False)
        await endpoints_down(target_agent, aiohttp_session, iface=TARGET_WAN_IF, local=FLAG_LOCAL,
                             peer=FLAG_PEER)
