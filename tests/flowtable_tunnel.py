"""6o4 and 4o6 tunnel offload on the Linux flowtable: the outer IP header is
inserted and stripped in hardware.

A tunnel is an encapsulation the same shape as a PPPoE session, one layer up:
one direction of a connection leaves the DUT by the tunnel device and the
hardware has to prepend the outer header the kernel would, and the other
direction arrives inside that header on the physical WAN port and the
hardware has to strip it before it can match the inner tuple. Both halves are
asserted separately, because the ingress half is the one Netfilter describes
with nothing at all -- no pop action, no dissector key -- and is therefore the
half most likely to be silently refused.

Two things make a tunnel unlike a session. The kernel resolves the outer
route and its next hop when it walks the forwarding path, so the adapter
records what the kernel walked rather than deriving it, and every case here
asserts the adapter's record against the tunnel device's own configuration.
And the outer header is a real IP header with a TTL and a checksum, so the
routed cases capture the frames the hardware put on the wire and read those
fields back, which the counters alone cannot show. The outer header carries no
don't-fragment bit: the insert opcode fills the fragment field itself and
ignores the template's, exactly as the legacy owner met and hardcoded around,
so the tunnel's pmtudisc reaches the wire only for CPU-forwarded frames.

Shapes: `6o4` is IPv6 inside IPv4 (`sit`, proto 41), the tunnel-broker and
6rd shape; `4o6` is IPv4 inside IPv6 (`ipip6`, next header 4), the DS-Lite
shape. The outer endpoints are the DUT's WAN address and the orchestrator's;
the inner ones a /64 or a /24 that belongs to neither segment, so a routing
mistake cannot look like a working path.

A 4o6 UDP upload stays in Linux. It arrives on the LAN port, which can deliver
a full 1500-byte frame whatever MTU it is given, and the tunnel's path is
smaller: the microcode would have to fragment it, and the fragments it builds
from a frame an Ethernet port received carry no payload. So the 4o6 UDP cases
assert that refusal and the download half, and the 4o6 upload -- the insert
opcode -- is carried by TCP, which sets DF and is never the microcode's to
fragment.
"""
from __future__ import annotations

from _flowtable_tunnel import CHANGED_TTL

from _flowtable_tunnel import (Capture, _admit, _assert_outer, _assert_tunnel, _both_directions, _directions, _established, _expected, _offload_table, _tunnel_counters, _tunnel_record, _udp_exchange)


import subprocess

import pytest

from ask_orch.counters import kernel_tx_packets
from _gated_tcp import GatedTcp
from _topology import TARGET_WAN_IF, lan_run_python
from _flowtable_rig import assert_undisturbed, command


# ---- cases ---------------------------------------------------------------

@pytest.mark.rfc("4213")
@pytest.mark.rfc("2473", section="4.1.1")
@pytest.mark.parametrize("tunnel_rig", ["6o4", "4o6"], indirect=True)
async def test_routed(tunnel_rig):
    """A routed UDP flow through the tunnel.

    The forward direction inserts the outer header and the reverse strips it;
    the adapter names the tunnel on exactly those two and on neither LAN half.
    The frames the DUT put on the wire carry the header the kernel would have
    built, and the tunnel device's counters moved by the burst although no
    packet of its hardware directions reached the CPU. A 4o6 upload is
    Linux's (see the module docstring), so there the strip is what is proved
    and the insert is test_tcp's.
    """
    r = tunnel_rig
    flows, delta = await _established(r)
    forward, reverse = _directions(r, flows)
    _assert_tunnel(r, forward, reverse)
    if r.shape.mode == "4o6":
        # The far end's ip6tnl sends a Tunnel Encapsulation Limit option
        # (RFC 2473 §4.1.1), so the strip counted below matched past it.
        detail = subprocess.run(["ip", "-d", "link", "show", r.shape.device],
                                capture_output=True, text=True, check=True).stdout
        assert "encaplimit 4" in detail, detail
    assert reverse["out_vlan"] == reverse["out_ppp"] == "-", reverse
    if forward:
        assert forward["in_vlan"] == forward["in_ppp"] == "-", forward
    assert all(d == 64 for d in delta.values()), delta


@pytest.mark.rfc("4213", section="3.2")
@pytest.mark.rfc("2473", section="7.1")
@pytest.mark.parametrize("tunnel_rig", ["6o4/mtu", "4o6/mtu"], indirect=True)
async def test_full_mtu(tunnel_rig):
    """A datagram that fills the tunnel's MTU is still carried in hardware.

    The microcode compares what it transmits against the programmed MTU, and
    what it transmits is the outer packet; a direction programmed with the
    tunnel-reduced inner MTU excepts every full-size frame to the CPU while
    every counter says the flow is offloaded. The payload here is exactly the
    inner MTU less its own headers. Only the strip carries it in hardware: a
    UDP insert into a tunnel smaller than a full frame is Linux's, which is
    what answers the oversized probe below. The full-size insert is proved by
    test_tcp, whose segments fill the tunnel.
    """
    r = tunnel_rig
    shape = r.shape
    payload = shape.mtu - (40 if shape.family == 6 else 20) - 8
    flows, delta = await _established(r, count=32, payload_size=payload, name="mtu")
    forward, reverse = _directions(r, flows)
    _assert_tunnel(r, forward, reverse)
    assert all(d == 32 for d in delta.values()), delta
    # One byte over, DF set: the entry point reports the tunnel's MTU back to
    # the sender rather than fragmenting or dropping it (RFC 4213 §3.2,
    # RFC 2473 §7.1), whatever MTU the LAN advertises.
    if shape.family == 6:
        probe = (f"IPv6(src={r.lan_address!r}, dst={shape.inner_orch!r})"
                 f"/UDP(sport={shape.sport}, dport={shape.dport})/Raw(b'x' * {payload + 1})")
        check = f"ICMPv6PacketTooBig in a and a[ICMPv6PacketTooBig].mtu == {shape.mtu}"
    else:
        probe = (f"IP(src={r.lan_address!r}, dst={shape.inner_orch!r}, flags='DF')"
                 f"/UDP(sport={shape.sport}, dport={shape.dport})/Raw(b'x' * {payload + 1})")
        check = f"ICMP in a and (a[ICMP].type, a[ICMP].code, a[ICMP].nexthopmtu) == (3, 4, {shape.mtu})"
    result = await lan_run_python(r.lan, f'''
from scapy.all import IP, IPv6, UDP, ICMP, ICMPv6PacketTooBig, Raw, sr1
a = sr1({probe}, timeout=3, verbose=False)
assert a is not None and {check}, a and a.show(dump=True)
''', label="flowtable_tunnel_ptb", timeout=30)
    assert result.rc == 0, result.stdout


@pytest.mark.parametrize("tunnel_rig", ["6o4/tcp", "4o6/tcp"], indirect=True)
async def test_tcp(tunnel_rig):
    """An established TCP connection through the tunnel.

    TCP is a classifier table of its own, so a UDP proof says nothing about
    it; and the classifier punts SYN, FIN and RST before its own lookup, so
    what the hardware carries is the bulk transfer in the middle. The cookies
    staying put proves the connection was never readmitted underneath it.

    It is also the large-segment insert. The far end advertises the MSS its
    tunnel allows, so every full data segment of the upload comes within its
    TCP options of the tunnel's MTU; a size check that counted the outer
    header against that MTU would except each one to Linux, which would then
    send it out of the WAN port itself. For 4o6 this is the only hardware
    insert there is, the UDP upload being Linux's.
    """
    r = tunnel_rig
    shape = r.shape
    await _offload_table(r, "tcp")

    async def run(script, **kwargs):
        return await lan_run_python(r.lan, script, **kwargs)

    # Read while the connection is open and idle (see _gated_tcp): a FIN
    # retires the entries within about a second of the peer closing.
    async with GatedTcp(run, source=r.lan_address, sport=shape.sport, peer=shape.inner_orch,
                        dport=shape.dport, label="flowtable_tunnel_tcp") as transfer:
        await transfer.warmed()
        # Admission is asynchronous (rtnl_trylock, deferred a second or two
        # under RTNL contention), so warmed()'s brief settle can miss a late
        # direction; wait admission in before reading the baseline the record
        # and the direction check share.
        before = await r.wait(lambda s: len(s["flows"]) == _expected(r, "tcp"))
        flows = await _both_directions(r, "tcp", before)
        record = _tunnel_record(r, before)
        link = await _tunnel_counters(r)
        sent = await kernel_tx_packets(r.target, r.session, TARGET_WAN_IF)
        # The outer headers of the hardware's own inserts, for 4o6 the only
        # ones there are (RFC 2473 §3).
        capture = Capture(r, "tcp")
        capture.snaplen = 128
        async with capture:
            await transfer.measure()
        sent = await kernel_tx_packets(r.target, r.session, TARGET_WAN_IF) - sent
        after = await r.state()
        record = {k: v - record[k] for k, v in _tunnel_record(r, after).items()}
        link = {k: v - link[k] for k, v in (await _tunnel_counters(r)).items()}
    forward, reverse = _directions(r, flows)
    r.record("tunnel-tcp", {"flows": flows, "after": after, "record": record, "link": link,
                            "software_wan_tx": sent, "report": transfer.report})
    _assert_tunnel(r, forward, reverse)
    _assert_outer(r, capture.packets(), 100)
    assert forward["proto"] == reverse["proto"] == "6", flows
    new = {f["cookie"]: f for f in after["flows"]}
    assert_undisturbed(r, before, after, new.keys() == {forward["cookie"], reverse["cookie"]}
                       and (after["installs"], after["deletes"]) == (before["installs"], before["deletes"]),
                       label="tunnel-readmitted")
    upload = int(new[forward["cookie"]]["packets"]) - int(forward["packets"])
    download = int(new[reverse["cookie"]]["packets"]) - int(reverse["packets"])
    assert upload > 100 and download > 100, (upload, download)
    # The tunnel device's record counts the same frames the two entries did:
    # the insert's the upload's, the strip's the download's.
    assert (record["tx_packets"], record["rx_packets"]) == (upload, download), (record, upload, download)
    # And `ip -s link` on the device moved by the record restated into the
    # inner packets it counts itself -- the Ethernet and outer headers off what
    # the insert counted, the Ethernet header off what the strip did -- which
    # is the transmit fold of inserted frames that a UDP upload in Linux cannot
    # show, plus at most a few frames the device sent or took itself.
    for half, overhead in (("tx", 14 + shape.header), ("rx", 14)):
        stray = link[half] - record[half + "_packets"]
        assert 0 <= stray <= 8, (half, record, link)
        inner = record[half + "_bytes"] - overhead * record[half + "_packets"]
        assert inner <= link[half + "_bytes"] <= inner + stray * 1518, (half, record, link)
    # Only the handful of frames the reads above cost left the WAN port in
    # software while the measured phase crossed it.
    assert 0 <= sent < upload // 4, (sent, upload)


@pytest.mark.parametrize("tunnel_rig", ["6o4/change"], indirect=True)
async def test_change_retires(tunnel_rig):
    """Reconfiguring the tunnel under a live flow retires it, and the flow
    readmitted afterwards carries the new outer header.

    `ip tunnel change` rewrites the device's parameters in place and raises
    only NETDEV_CHANGE, on a device that is always running with carrier. A
    flow admitted against the old parameters would otherwise keep sending the
    old header from hardware while software sent the new one. The TTL is what
    changes here because it is visible on the wire.
    """
    r = tunnel_rig
    flows, _ = await _established(r)
    before = await r.state()
    await command(r.target, r.session, "ip", "tunnel", "change", r.shape.device,
                  "ttl", str(CHANGED_TTL))
    retired = await r.wait(lambda s: s["entries"] == 0)
    assert retired["bindings"] == 2, retired
    assert retired["link_invalidations"] > before["link_invalidations"], (before, retired)
    # Readmitted against the new configuration, and the wire says so.
    flows = await _admit(r)
    forward, reverse = _directions(r, flows)
    _assert_tunnel(r, forward, reverse)
    async with Capture(r, "change") as capture:
        report = await _udp_exchange(r, 32)
    assert report == {"echoed": 32, "lost": 0}, report
    _assert_outer(r, capture.packets(), 32, ttl=CHANGED_TTL)
    r.record("tunnel-change", {"before": before, "retired": retired, "flows": flows})


@pytest.mark.parametrize("tunnel_rig", ["6o4/delete"], indirect=True)
async def test_delete_retires(tunnel_rig):
    """Deleting the tunnel device retires both directions and leaves the
    bindings up: the ports are untouched, admission stays open, and the next
    flow is judged against whatever tunnel exists then."""
    r = tunnel_rig
    flows, _ = await _established(r)
    assert len(flows) == _expected(r), flows
    before = await r.state()
    await command(r.target, r.session, "ip", "link", "del", r.shape.device)
    retired = await r.wait(lambda s: s["entries"] == 0)
    assert retired["bindings"] == 2 and retired["invalidated"] == 0, retired
    assert retired["rearms"] == before["rearms"], (before, retired)
    r.record("tunnel-delete", {"before": before, "retired": retired})
