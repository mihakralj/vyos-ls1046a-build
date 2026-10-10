"""An offloaded tunnel SA answers an oversized IPv4 datagram with DF as Linux does.

Linux bounds a direction into an SA by the bundle's MTU, the smaller of the
SA's MTU on the path to the peer and the inner route's, and answers an
oversized IPv4 datagram with DF by Fragmentation Needed carrying that bound.
The SA's MTU is what xfrm computes for the transform over the path: for
AES-CBC with HMAC-SHA256-128 over IPv4 on a 1500-byte port,
((1500 - 20 - 8 - 16 - 16) & ~15) - 2 = 1438.

The classifier entry is given that bound with SEC's expansion on top, and the
microcode adds the expansion to a packet bound for SEC before the size check
that hands an oversized IPv4 packet with DF to Linux. Three things kept the
two bounds apart. The expansion was the headers alone, because the SA was
programmed while xfrm still held the state as not yet valid, so a DF datagram
between the SA's MTU and the port's less the headers (1439-1456 bytes for that
transform) went to SEC and left it larger than the port (A227). The entry
took the port's MTU in place of the flow's, so an inner route with an MTU of
its own was not the bound at all: DF datagrams between it and the SA's MTU
crossed in hardware where Linux answers them (A230). And the SA's MTU was
taken from the port once, when the SA was installed, so a narrower hop on the
way to the peer -- a route to it with an MTU of its own, or a PMTU learned for
it -- never reached the hardware, which kept encrypting DF datagrams Linux
answers into frames the path cannot carry (A231).

Each case runs with the bound set a different way: by the SA's MTU on the
port, with the inner route carrying no MTU; by the fixture's own inner route
MTU, below the SA's; and by a 1492-byte hop to the peer, either a route with
that MTU or a PMTU learned from the peer's Fragmentation Needed. The hop
narrows after the flow is in hardware, which is the order it happens in
practice: the SA's MTU on the port crosses in hardware first, then the
narrowing retires the directions the SA encrypts and the flow's next packets
readmit them under the path's bound. Every DF size over the bound draws
Linux's answer; one at exactly the bound crosses the tunnel in hardware as
one frame; and, below the SA's MTU, one without DF over the inner route's
crosses too, whole, as Linux itself sends it.
"""
from __future__ import annotations

import asyncio
import json
import secrets
import struct

import pytest

from _topology import TARGET_WAN_IF
from _flowtable_connections import (by_key, peer)
from _flowtable_ipv6_sa import (fragments_sent)
from _flowtable_rig import (WAN_IP, command)
from _flowtable_selective_neighbour import (keys, unchanged, warm)
from _flowtable_service_ipsec import (INNER, Transform, flows_for, sec_counter)
from _flowtable_service_ipsec_replay import (AEAD)

# What esp4 puts around a transform's payload: the cipher's block, which it
# aligns to 4 bytes, and the IV it sends; GCM is a stream cipher with a block
# of one.
GEOMETRY = {"cbc(aes)": (16, 16), "cbc(des3_ede)": (8, 8), "rfc3686(ctr(aes))": (1, 8),
            "rfc4106(gcm(aes))": (1, 8)}
# The fixture routes the far inner address with an MTU of its own, below any
# SA's here; put back when a case is done.
FIXTURE_ROUTE_MTU = 1400
# A hop on the way to the peer narrower than the port: a routed DSL modem.
PATH_MTU = 1492


def esp_geometry(transform):
    """The transform's outer header length and its SA's inner MTU on a path of
    `path_mtu`, as xfrm_state_mtu() computes them for a tunnel-mode ESP state
    over IPv4."""
    algorithms = transform.algorithms
    if algorithms[0] == "aead":
        _, cipher, _, icv_bits = algorithms
    else:
        _, cipher, _, kind, _, _, icv_bits = algorithms
        assert kind == "auth-trunc", algorithms
    block, iv = GEOMETRY[cipher]
    header = 20 + 8 + iv + (8 if transform.encap else 0)
    align = -(-block // 4) * 4

    def inner_mtu(path_mtu):
        return (path_mtu - header - int(icv_bits) // 8) // align * align - 2
    return header, inner_mtu


def forward_key(flow):
    """The probe flow's LAN-to-WAN direction, the one the SA encrypts."""
    return next(key for key in keys([0], [flow]) if key[0] != TARGET_WAN_IF)


async def probe(r, p, flow, size, df=True):
    """One datagram of `size` bytes, all headers included, and what it did:
    whether the LAN end heard Fragmentation Needed, whether the far end got
    it intact, and what the hardware direction, the software SEC submit and
    the microcode's fragmenter counted meanwhile.

    The LAN peer sends it from the flow's own socket.
    The far end does not answer it. A reply would cross the other direction
    and, without DF, be fragmented on the way to the LAN end's narrower
    route, as Linux would; the fragmenter's counts are global and would read
    as this direction's."""
    payload = (b"ASK-ipsec-mtu-" + secrets.token_bytes(8)).ljust(size - 28, b".")
    forward = forward_key(flow)
    before, fragments = await r.state(), await fragments_sent(r)
    # Every probe is on a direction in hardware; a missing one expired or
    # was retired before this probe, not by it.
    assert forward in by_key(before), (forward, sorted(by_key(before)))
    submitted = await sec_counter(r.session, r.target, TARGET_WAN_IF, "tx toenc")
    r.inner_echo.reply = False
    try:
        lan = await p.rpc("df_probe", ident=flow["id"], data=payload.hex(), df=df)
        await asyncio.sleep(0.5)
    finally:
        r.inner_echo.reply = True
    after = await r.state()
    assert lan["length"] == size, lan
    return {"size": size, "df": df, "lan": lan, "delivered": r.echo.received[payload],
            "hardware": int(by_key(after)[forward]["packets"]) - int(by_key(before)[forward]["packets"]),
            "toenc": await sec_counter(r.session, r.target, TARGET_WAN_IF, "tx toenc") - submitted,
            "fragments": {k: v - fragments[k] for k, v in (await fragments_sent(r)).items()},
            "before": before, "after": after}


async def narrow_route(r):
    """Route the peer with a hop's MTU of its own."""
    await command(r.target, r.session, "ip", "route", "replace", WAN_IP + "/32", "dev", TARGET_WAN_IF,
                  "mtu", str(PATH_MTU))


async def narrow_pmtu(r):
    """Report the hop the way a router on the path would: Fragmentation Needed
    quoting one of the SA's own frames, from which Linux learns a PMTU for the
    peer (esp4_err()) and tells no notifier. The quote needs only the outer
    header and the SPI for Linux to find the SA."""
    from scapy.all import Ether, ICMP, IP, Raw, sendp
    quoted = (IP(src=r.ipsec.outer, dst=WAN_IP, proto=50, flags="DF")
              / Raw(struct.pack("!II", r.ipsec.active["out"], 1)))
    frame = (Ether(src=r.wan_mac, dst=r.dut_wan_mac) / IP(src=WAN_IP, dst=r.ipsec.outer)
             / ICMP(type=3, code=4, nexthopmtu=PATH_MTU) / quoted)
    await asyncio.to_thread(sendp, frame, iface=r.wan_if, verbose=False)


NARROWING = {"path": narrow_route, "pmtu": narrow_pmtu}


@pytest.mark.parametrize("bound_by", ["sa", "route", "path", "pmtu"],
                         ids=["sa-bound", "route-bound", "path-bound", "pmtu-bound"])
@pytest.mark.parametrize("ipsec_service", [Transform(), AEAD["rfc4106-icv16"]],
                         ids=["cbc-sha256", "rfc4106-icv16"], indirect=True)
async def test_df_mtu(ipsec_service, bound_by):
    """A DF datagram over the direction's bound -- the SA's inner MTU on the
    path to the peer, or the inner route's where that is smaller -- up to the
    port's less the headers, draws Fragmentation Needed with that bound; one
    at exactly the bound crosses the tunnel in hardware as one frame."""
    r = ipsec_service
    flows = flows_for(r, "udp")
    flow = flows[4]
    forward = forward_key(flow)
    link = json.loads((await command(r.target, r.session, "ip", "-j", "link", "show", "dev",
                                     TARGET_WAN_IF))["stdout"])
    port_mtu = int(link[0]["mtu"])
    header, inner_mtu = esp_geometry(r.ipsec.transform)
    sa_mtu = inner_mtu(port_mtu)
    path_mtu = PATH_MTU if bound_by in NARROWING else port_mtu
    route_mtu = FIXTURE_ROUTE_MTU if bound_by == "route" else None
    bound = min(route_mtu or sa_mtu, inner_mtu(path_mtu))
    assert path_mtu <= port_mtu and (route_mtu is None or route_mtu < sa_mtu), (port_mtu, sa_mtu)
    assert bound_by not in NARROWING or bound < sa_mtu, (bound, sa_mtu)
    # Just over the bound, a little further, the SA's MTU on the port where
    # something below it is the bound, and the largest the headers alone
    # would have let through, on the path and on the port.
    oversized = sorted({bound + 1, bound + 12, sa_mtu, path_mtu - header, port_mtu - header} - {bound})
    assert bound < oversized[0] and oversized[-1] <= port_mtu, (bound, oversized)
    label = f"ipsec-mtu-{bound_by}-bound"
    context = {"sa_mtu": sa_mtu, "path_mtu": path_mtu, "bound": bound}
    route = ["ip", "route", "replace", INNER + "/32", "via", WAN_IP, "dev", TARGET_WAN_IF]
    # Set explicitly either way rather than trusted from the fixture, since
    # the answer depends on it.
    await command(r.target, r.session, *route, *(["mtu", str(route_mtu)] if route_mtu else []))
    results = {}
    try:
        async with peer(r, flows, initial_ids=[4], lease=400, listen_addresses=[INNER]) as p:
            admitted = await warm(r, p, [4], label + "-admitted", [flow])
            if bound_by in NARROWING:
                # Before the hop narrows, the port's bound holds: the SA's
                # MTU on the port crosses in hardware.
                result = results[f"df-{sa_mtu}-wide"] = await probe(r, p, flow, sa_mtu)
                r.record(label, {**context, "results": results})
                assert result["lan"]["frag_needed"] == [], result
                assert result["delivered"] == 1 and result["hardware"] == 1, result
                assert result["toenc"] == 0 and result["fragments"] == {4: 0, 6: 0}, result
                unchanged(result["before"], result["after"], [0], [flow])
                cookie = by_key(result["after"])[forward]["cookie"]
                await NARROWING[bound_by](r)
                # Every direction the SA encrypts was admitted under the
                # port's bound, so the narrowing retires it -- announced by
                # the route's event, or found by the accounting pass for a
                # PMTU nothing announces -- and the flow's next packets
                # readmit it under the path's.
                retired = await r.wait(
                    lambda s: s["mtu_invalidations"] > admitted["mtu_invalidations"]
                    and by_key(s).get(forward, {}).get("cookie") != cookie, timeout=15)
                readmitted = await warm(r, p, [4], label + "-readmitted", [flow])
                learned = await command(r.target, r.session, "ip", "route", "get", WAN_IP,
                                        "from", r.ipsec.outer)
                r.record(label + "-narrowed", {"retired": retired, "readmitted": readmitted,
                                               "route": learned["stdout"]})
            for size in oversized:
                result = results[f"df-{size}"] = await probe(r, p, flow, size)
                r.record(label, {**context, "results": results})
                # Linux's answer, once, with the bound, and the datagram
                # dropped: never handed to SEC by the CPU or the hardware, so
                # nothing reached the far end and nothing was fragmented.
                assert result["lan"]["frag_needed"] == [{"mtu": bound, "length": size}], result
                assert result["delivered"] == 0 and result["toenc"] == 0, result
                assert result["fragments"] == {4: 0, 6: 0}, result
                unchanged(result["before"], result["after"], [0], [flow])
            result = results[f"df-{bound}"] = await probe(r, p, flow, bound)
            r.record(label, {**context, "results": results})
            # The largest the bound admits: SEC encrypts it in hardware and it
            # leaves as one frame the path carries -- exactly the port's MTU
            # when the SA's MTU on the port is the bound, at most the hop's
            # when the hop is.
            assert result["lan"]["frag_needed"] == [], result
            assert result["delivered"] == 1 and result["hardware"] == 1, result
            assert result["toenc"] == 0 and result["fragments"] == {4: 0, 6: 0}, result
            unchanged(result["before"], result["after"], [0], [flow])
            if route_mtu is not None:
                # Without DF, a datagram over the inner route's MTU and within
                # the SA's is Linux's to encrypt whole: the bundle carries it
                # in one frame. The hardware does the same -- the size check
                # hands Linux only DF packets, and the enqueue to SEC
                # fragments nothing -- so it reaches the far end intact, as
                # one frame, without an answer, and encrypted in hardware.
                size = (bound + sa_mtu) // 2
                result = results[f"no-df-{size}"] = await probe(r, p, flow, size, df=False)
                r.record(label, {**context, "results": results})
                assert result["lan"]["frag_needed"] == [] and result["delivered"] == 1, result
                assert result["hardware"] == 1 and result["toenc"] == 0, result
                assert result["fragments"] == {4: 0, 6: 0}, result
                unchanged(result["before"], result["after"], [0], [flow])
    finally:
        await command(r.target, r.session, *route, "mtu", str(FIXTURE_ROUTE_MTU))
        if bound_by in NARROWING:
            # Back to the fixture's route to the peer, which carries no MTU of
            # its own, and a PMTU learned for it forgotten: the flush drops
            # route exceptions along with the cache.
            await command(r.target, r.session, "ip", "route", "replace", WAN_IP + "/32",
                          "dev", TARGET_WAN_IF, check=False)
            await command(r.target, r.session, "sysctl", "-w", "net.ipv4.route.flush=1",
                          check=False)
