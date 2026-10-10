"""PPPoE flowtable offload: the WAN sits behind a session, the LAN does not.

The asymmetry is the point, as it is for a tag. One direction arrives inside a
session and leaves bare, the other the reverse, so a single connection
exercises both the ingress strip and the egress insert and neither can be
mistaken for the other.

Two things make a session unlike a tag, and both are what these cases are for.
Netfilter describes an ingress session with *nothing* -- there is no pop action
and no dissector key, so the reverse direction's rule is byte-for-byte the rule
an unencapsulated flow produces, and only the devices say otherwise. And a ppp
device resolves no Ethernet destination at all: it is NOARP with no address, so
the four Ethernet mangles Netfilter writes are zeros and the real destination
is the concentrator the session named. Every case therefore asserts the session
the adapter recorded against `/proc/net/pppoe`, which is where the kernel's own
view of the negotiated session can be read back independently, and then sends a
second burst and requires the classifier's own packet counters to account for
all of it.

The session runs over the temporary tagged WAN interface on dell2.
That is not incidental: `ppp0` over `eth4.3900` over `eth4` puts
a session and a tag on one path, which is both encapsulation slots a direction
has, and it is the shape every case here runs in.

The shared tagged_wan fixture provides and cleans up the Dell-side VLAN.
Session setup and restoration remain in the imported pppoe_rig fixture;
no permanent WAN bridge or access-concentrator service is required.

An IPv4 UDP upload into the session stays in Linux, which is the shipping
behaviour rather than a bench limitation. It arrives on the LAN port, which can
deliver a full 1500-byte frame whatever MTU the port is given, and the session
carries 1492: the microcode would have to fragment it, and the fragments it
builds from a frame an Ethernet port received carry no payload. So the UDP
cases assert that refusal and prove the download -- the strip -- in hardware,
and every property of the upload -- the insert, the session MTU it describes,
the translation in front of it, the LAN tag it pops -- is proved on TCP, which
sets DF and is never the microcode's to fragment.
"""
from __future__ import annotations

from _flowtable_pppoe import QOS_COUNT, QOS_RATE_MBIT, QOS_VOICE_CQ, QOS_VOICE_PRIO

from _flowtable_pppoe import (INNER_LOCAL, INNER_LOCAL6, INNER_REMOTE, INNER_REMOTE6, LAN_VID, LCP_ECHO_INTERVAL, NAT_TABLE, SERVER_IF, PORT_QOS_BULK, PORT_QOS_VOICE, PPP_RX_OVERHEAD, QOS_TABLE, SESSION_MTU, SNAT_ADDR, SPORT6, SourceEcho, UNTAGGED_COUNT, UNTAGGED_DPORT, UNTAGGED_SPORT, WAN_ENDPOINT, WAN_VID, _assert_carried, _assert_session, _assert_undisturbed, _both_directions, _dial, _direction, _download_only, _established, _exchange6, _hangup, _offload_table6, _ppp_link, _qos_bulk, _qos_bulk_stop, _session_halves, _session_identity, _session_row, _session_text, _snat_table, _tcp_carried, _untagged_directions, _untagged_path, _untagged_window, _wait_reachable)

import asyncio
import json
import math
import socket
import subprocess
import time

import pytest

from _flowtable_pppoe import DPORT6, _concentrator_if
from _gated_tcp import GatedTcp
from _topology import LAN_IPV6, TARGET_LAN_IF, TARGET_WAN_IF, lan_run
import _flowtable_rig as ft
from _flowtable_rig import Echo, SPORT, command, console_command, read


@pytest.mark.rfc("2516")
@pytest.mark.rfc("1661")
@pytest.mark.rfc("1332")
@pytest.mark.rfc("1334")
@pytest.mark.rfc("1994")
@pytest.mark.parametrize("pppoe_rig", ["udp", "chap"], indirect=True)
async def test_routed(pppoe_rig):
    """A session on the WAN side and a bare LAN, routed, with no translation.

    The session id and the concentrator the adapter recorded have to be the
    ones the kernel negotiated, on the direction that strips the header, and
    on neither of the LAN-side halves. The UDP upload is Linux's; the
    direction that inserts the header is proved by test_tcp.
    """
    r = pppoe_rig
    flows, delta = await _established(r)
    reverse = _direction(flows, INNER_LOCAL, r.lan_ip)
    _assert_session(r, None, reverse)
    # Nothing on the LAN side is encapsulated, which is what makes the session
    # assertions above about the session rather than about the path.
    assert reverse["out_vlan"] == "-" and reverse["out_br"] == "-", reverse
    # The download leaves by the LAN port, whose full MTU it carries.
    assert int(reverse["mtu"]) == 1500, reverse
    assert all(d == 64 for d in delta.values()), delta
    r.record("pppoe-routed", {"flows": flows, "delta": delta,
                              "session": _session_text(r.session_identity)})


@pytest.mark.parametrize("pppoe_rig", ["ipv6"], indirect=True)
async def test_ipv6_routed(pppoe_rig):
    """IPv6 across the session, which was the one thing a session excluded.

    The exclusion was about the firmware rather than the adapter.
    `en_ehash_insert_pppoe_hdr` carries a version, a type, a code and a session
    id, and no PPP protocol id at all, so the microcode chooses between 0x0021
    and 0x0057 itself and nothing had shown which it picks for an IPv6 frame. A
    wrong choice is a header the concentrator discards, which is silent loss
    rather than a refusal, so it stayed out until measured.

    This is the download's half of the measurement: a complete v6 exchange
    across the session, every reply stripped by the hardware. The UDP upload
    is Linux's, as IPv4's is: the LAN port delivers a full frame and the
    session carries 1492, and the microcode would fragment the excess where
    Linux answers it with Packet Too Big. The insert, where the protocol id is
    chosen, is proved by test_ipv6_tcp.

    One routed case per protocol is the whole of it, deliberately. Nothing
    about the family reaches the session decode -- the walk, the concentrator
    and the id are identical either way -- so what the other shapes would
    re-prove is the adapter's handling of a session, which the IPv4 cases
    already cover, and what is new here belongs to the firmware.
    """
    r = pppoe_rig
    # Errors accumulate for the life of the module, and the fault-injection
    # cases in flowtable_offload.py raise some on purpose, so what this
    # case can claim is that it added none of its own.
    initial = await r.state()
    baseline = initial["errors"]
    await _offload_table6(r)
    # Nothing re-offers a flow on its own, so each attempt sends before it
    # looks; admission needs traffic and the reverse direction needs a reply.
    for _ in range(10):
        await _exchange6(r, 4)
        if (await r.state())["entries"] == 1:
            break
    state = await r.state()
    flows = state["flows"]
    assert len(flows) == 1 and state["rejects"] > initial["rejects"], (initial, state)
    reverse = _direction(flows, f"[{INNER_LOCAL6}]", f"[{LAN_IPV6}]")
    assert reverse["family"] == "6", flows
    # The session, asserted exactly as the v4 routed case asserts it: the id
    # and the concentrator the kernel negotiated on the direction that strips
    # the header, and nothing on its LAN half.
    _assert_session(r, None, reverse)
    assert reverse["out_vlan"] == "-" and reverse["out_br"] == "-", reverse

    before = {f["cookie"]: int(f["packets"]) for f in flows}
    report = await _exchange6(r, 64)
    assert report == {"echoed": 64, "lost": 0}, report
    state = await r.state()
    after = {f["cookie"]: int(f["packets"]) for f in state["flows"]}
    assert set(before) == set(after), (before, state)
    delta = {c: after[c] - before[c] for c in before}
    assert all(d == 64 for d in delta.values()), (delta, state)
    assert state["errors"] == baseline, (baseline, state)
    # And what the far end observed, which is where the protocol id was really
    # decided: the concentrator's stack had to parse the PPP frame before this
    # datagram could reach a socket at all.
    assert r.echo6.sources == {(LAN_IPV6, SPORT6)}, r.echo6.sources
    r.record("pppoe-ipv6-routed", {"flows": flows, "delta": delta,
                                   "session": _session_text(r.session_identity),
                                   "observed": sorted(r.echo6.sources)})


@pytest.mark.parametrize("pppoe_rig", ["ipv6"], indirect=True)
async def test_ipv6_tcp(pppoe_rig):
    """The v6 insert across the session, over TCP, which the uplink's MSS
    clamp keeps within the session's MTU.

    `en_ehash_insert_pppoe_hdr` carries no PPP protocol id, so the microcode
    chooses between 0x0021 and 0x0057 itself, and a wrong choice is a header
    the concentrator discards: silent loss rather than a refusal. A transfer
    the concentrator's stack completed is the evidence that it parsed every
    frame the hardware inserted a header onto, and the upload's own counter
    says the hardware did the inserting.
    """
    r = pppoe_rig
    baseline = (await r.state())["errors"]
    await _offload_table6(r, "tcp")
    async with GatedTcp(r.run_peer, source=LAN_IPV6, sport=SPORT6, peer=INNER_LOCAL6,
                        dport=DPORT6, label="flowtable_pppoe_v6_tcp") as transfer:
        await transfer.warmed()
        before = await r.wait(lambda s: len(s["flows"]) == 2)
        flows = await _both_directions(r, before)
        await transfer.measure()
        after = await r.state()
    forward = _direction(flows, f"[{LAN_IPV6}]", f"[{INNER_LOCAL6}]")
    reverse = _direction(flows, f"[{INNER_LOCAL6}]", f"[{LAN_IPV6}]")
    assert forward["family"] == reverse["family"] == "6", flows
    _assert_session(r, forward, reverse)
    assert forward["in_vlan"] == "-" and reverse["out_vlan"] == "-", (forward, reverse)
    assert forward["in_br"] == reverse["out_br"] == "-", (forward, reverse)
    # The upload leaves by the session, so it carries the session's MTU --
    # above the IPv6 minimum link MTU, the one extra thing v6 requires of a path.
    assert int(forward["mtu"]) == SESSION_MTU, forward
    new = {f["cookie"]: int(f["packets"]) for f in after["flows"]}
    assert new.keys() == {forward["cookie"], reverse["cookie"]}, after
    assert (after["installs"], after["deletes"]) == (before["installs"], before["deletes"]), (before, after)
    upload = new[forward["cookie"]] - int(forward["packets"])
    download = new[reverse["cookie"]] - int(reverse["packets"])
    assert upload > 100 and download > 100, (upload, download)
    assert after["errors"] == baseline, (baseline, after)
    r.record("pppoe-ipv6-tcp", {"flows": flows, "after": after, "report": transfer.report,
                                "session": _session_text(r.session_identity)})


async def test_session_counters(pppoe_rig):
    """The session's own byte counters, which the firmware keeps for it, and
    where an operator reads them: on the ppp device.

    One record per ppp device, not per flow and not per direction: every
    direction of a connection that crosses the device in hardware holds a
    reference, and counts into the half of the record it uses. Sending a
    measured burst and requiring the record to have moved by it is what
    separates counters the firmware is really maintaining from an index that
    was merely written into an opcode; requiring `ip -s link` on the device to
    have moved by the same burst, restated into the payload the device itself
    counts, is what makes the record an operator's number rather than a
    diagnostic.

    The UDP upload is Linux's, so here the download alone holds the record
    and moves its receive half, while the device's transmit counter moves by
    the upload Linux sent through it. The insert's half of the record is
    counted by test_tcp.
    """
    r = pppoe_rig
    initial = await r.state()
    await r.table()
    await r.exchange(count=4)
    await _download_only(r, initial)
    state = await r.state()
    row = _session_row(state, r.session_identity)
    # Held by the one direction in hardware, and holding a record: the pool
    # is empty only after four sessions, and this bench has one. The record is
    # the device's, and says which device.
    assert row["refs"] == "1", row
    assert row["slot"] == "yes", row
    assert row["dev"] == r.ppp_if, row
    assert state["session_records"] == 1 and state["session_slots"] == 1, state
    before = {k: int(row[k]) for k in
              ("rx_packets", "rx_bytes", "tx_packets", "tx_bytes")}
    link_before = await _ppp_link(r)

    payload = 256
    ip_len = 20 + 8 + payload
    await r.exchange(count=64, payload_size=payload)
    burst = await r.state()
    _assert_undisturbed(r, state, burst, [f["cookie"] for f in burst["flows"]]
                        == [f["cookie"] for f in state["flows"]])
    row = _session_row(burst, r.session_identity)
    link_after = await _ppp_link(r)
    after = {k: int(row[k]) for k in before}
    delta = {k: after[k] - before[k] for k in before}
    link = {k: link_after[k] - link_before[k] for k in before}
    # Received frames are the ones the download stripped the header from; the
    # record's transmit half counts only headers hardware inserted, and the
    # upload inserted none.
    assert delta["rx_packets"] == 64 and delta["tx_packets"] == delta["tx_bytes"] == 0, (before, after)
    # The firmware's session record, measured on this bench and pinned here:
    # the strip counts the frame as it arrived less the session header alone,
    # so the WAN tag the session runs over is still in.
    assert delta["rx_bytes"] == 64 * (ip_len + 14 + 4), delta
    # The device counts the payload alone, both ways -- the download folded in
    # from the record, the upload counted by Linux as it sent it -- and its
    # counters now include the burst restated to exactly that, plus the few
    # frames the session itself exchanges meanwhile (LCP echoes), each at most
    # one frame.
    for half in ("rx", "tx"):
        stray = link[f"{half}_packets"] - 64
        assert 0 <= stray <= 8, (half, link)
        assert 64 * ip_len <= link[f"{half}_bytes"] <= 64 * ip_len + stray * 1518, (half, link)
    r.record("pppoe-session-counters", {"before": before, "after": after, "delta": delta,
                                        "row": row, "link": link})
    # The record belongs to the device rather than to the flows: retiring the
    # connection returns the references and keeps the record and its totals,
    # so the device's counters survive the connection going idle.
    await r.delete_table()
    state = await r.state()
    row = _session_row(state, r.session_identity)
    assert row["refs"] == "0" and row["slot"] == "yes", row
    assert state["session_records"] == 1 and state["session_slots"] == 1, state
    assert {k: int(row[k]) for k in after} == after, (row, after)


async def test_session_record_ignores_untagged_flows(pppoe_rig):
    """A session's hardware record ignores untagged flows that do not cross it.

    An entry whose ingress names no tag and no session still carries the strip
    that validates it arrived untagged, and that strip's statistics word is a
    count of zero at a pointer to the base of the statistics carve
    (insert_remove_vlan_hm()). The carve opens with the session pool, so the
    pointer is the receive half of the first session record -- the very
    address the download of a session holding that record counts into. Only
    the count tells the two apart. A microcode that followed the pointer
    whatever the count would put every untagged frame into that session's
    record, and through the fold into its ppp device's `ip -s link`; with the
    record free, into the free list's link, which occupies the same bytes.

    So a plain routed flow runs both ways between the bare ports while the
    session holds a record, and the record must not move at all. That is sharp
    while the session holds the first record, and here it does by
    construction: the pool is a stack laid down in carve order, taken from and
    returned to its head, so it hands out the first record every time until
    two sessions hold records at once -- and no case on this bench ever holds
    two, since each dials the only session (_session_identity). What of that
    can be read back is checked: no record held before the admission, exactly
    one after.

    The control is the session's own download: 64 frames through it still
    move the same record by exactly 64. A record that counted nothing at all
    would pass everything else here too.
    """
    r = pppoe_rig
    initial = await r.state()
    assert initial["session_records"] == 0, (
        "a session record outlived its device, so the one this case takes would "
        "not come off the head of the pool", initial["sessions"])
    cleanup = []
    transport = None
    try:
        wan_if, dut_wan_ip = await _untagged_path(r, cleanup)
        await r.table()
        await r.nft(f"add rule inet {ft.TABLE} forward ip saddr {r.lan_ip} "
                    f"ip daddr {WAN_ENDPOINT} udp sport {UNTAGGED_SPORT} "
                    f"udp dport {UNTAGGED_DPORT} flow add @fast")
        await r.exchange(count=4)
        await _download_only(r, initial)
        held = await r.state()
        row = _session_row(held, r.session_identity)
        assert row["slot"] == "yes" and row["dev"] == r.ppp_if, row
        assert held["session_records"] == held["session_slots"] == 1, held
        transport, echo = await asyncio.get_running_loop().create_datagram_endpoint(
            Echo, local_addr=(WAN_ENDPOINT, UNTAGGED_DPORT))
        echo.record_payloads = False
        # The concentrator reaches the LAN VM through the session, so the WAN
        # host's echoes would come back inside it. For the window its route to
        # the LAN VM goes by the DUT's WAN address instead, which leaves both
        # halves of the flow bare; the session's route is back before the
        # control, which needs it.
        await command(r.wan, r.session, "ip", "route", "replace", r.reachable,
                      "via", dut_wan_ip, "dev", wan_if)
        try:
            measured = await _untagged_window(r, echo)
        finally:
            await command(r.wan, r.session, "ip", "route", "replace", r.reachable,
                          "dev", await _concentrator_if(r), check=False)
        r.record("pppoe-untagged-beside-session",
                 {**measured, "session": _session_text(r.session_identity)})

        before, after = measured["before"], measured["after"]
        old = _untagged_directions(r, before["flows"])
        new = _untagged_directions(r, after["flows"])
        _assert_undisturbed(r, before, after, new is not None and
                            [f["cookie"] for f in new] == [f["cookie"] for f in old])
        # Bare both ways: nothing described on either side of either
        # direction, so each entry's strip is the one that names no record.
        for flow, ingress, egress in ((old[0], TARGET_LAN_IF, TARGET_WAN_IF),
                                      (old[1], TARGET_WAN_IF, TARGET_LAN_IF)):
            assert (flow["in"], flow["out"]) == (ingress, egress), flow
            assert all(flow[k] == "-" for k in ("in_vlan", "out_vlan", "in_br", "out_br",
                                                "in_ppp", "out_ppp", "in_tnl", "out_tnl")), flow
        # Every round trip crossed in hardware, both ways.
        assert measured["report"] == {"echoed": UNTAGGED_COUNT, "lost": 0}, measured["report"]
        assert measured["answered"] == UNTAGGED_COUNT, measured["answered"]
        moved = [int(n["packets"]) - int(o["packets"]) for o, n in zip(old, new)]
        assert moved == [UNTAGGED_COUNT, UNTAGGED_COUNT], (moved, old, new)
        # Nothing crossed the session's download in hardware meanwhile, where
        # it is still installed -- it can go idle and expire in the window,
        # which retires the entry and keeps the record. So any movement below
        # is the pointer's and not traffic's.
        download = {f["cookie"]: int(f["packets"]) for f in before["flows"] if f["in_ppp"] != "-"}
        crossed = {f["cookie"]: int(f["packets"]) - download[f["cookie"]]
                   for f in after["flows"] if f["cookie"] in download}
        assert not any(crossed.values()), (crossed, before["flows"], after["flows"])
        record = {k: measured["record_after"][k] - measured["record"][k]
                  for k in measured["record"]}
        assert record == dict.fromkeys(record, 0), (
            f"{2 * UNTAGGED_COUNT} untagged frames moved the session's record",
            measured["record"], measured["record_after"])
        # What the session exchanges on its own meanwhile: each end sends an
        # LCP echo request every LCP_ECHO_INTERVAL seconds and answers the
        # other's, so the DUT receives at most two frames per interval, and a
        # window of w seconds overlaps at most ceil(w / interval) + 1 of them;
        # each is at most a full frame. A record counting the untagged flows
        # would fold thousands in here.
        allowance = 2 * (math.ceil(measured["window"] / LCP_ECHO_INTERVAL) + 1)
        link = {k: measured["link_after"][k] - measured["link"][k] for k in measured["link"]}
        assert 0 <= link["rx_packets"] <= allowance, (
            allowance, measured["window"], measured["link"], measured["link_after"])
        assert link["rx_bytes"] <= link["rx_packets"] * 1518, (
            measured["link"], measured["link_after"])
        assert after["errors"] == initial["errors"], (initial["errors"], after)
        row = _session_row(after, r.session_identity)
        assert row["slot"] == "yes", row
        assert after["session_records"] == after["session_slots"] == 1, after

        # The control. The download may have expired over the window, so it
        # is offered until hardware holds it again, then measured as
        # test_session_counters measures it.
        for _ in range(10):
            await r.exchange(count=4)
            state = await r.state()
            if any(f["in_ppp"] != "-" for f in state["flows"]):
                break
        else:
            pytest.fail(f"the session's download was not readmitted: {state}")
        download = _direction(state["flows"], INNER_LOCAL, r.lan_ip)
        halves = _session_halves(state, r.session_identity)
        payload = 256
        await r.exchange(count=64, payload_size=payload)
        burst = await r.state()
        counted = _direction(burst["flows"], INNER_LOCAL, r.lan_ip)
        _assert_undisturbed(r, state, burst, counted["cookie"] == download["cookie"])
        control = {k: v - halves[k] for k, v in _session_halves(burst, r.session_identity).items()}
        r.record("pppoe-untagged-control", {"before": halves, "delta": control,
                                            "download": [download, counted]})
        assert int(counted["packets"]) - int(download["packets"]) == 64, (download, counted)
        assert control == {"rx_packets": 64,
                           "rx_bytes": 64 * (20 + 8 + payload + PPP_RX_OVERHEAD),
                           "tx_packets": 0, "tx_bytes": 0}, (halves, control)
    finally:
        if transport:
            transport.close()
        # The table first, so its entries retire by unbinding rather than by
        # the routes below going out from under them.
        await command(r.target, r.session, "nft", "delete", "table", "inet", ft.TABLE,
                      check=False)
        for agent, argv in reversed(cleanup):
            await command(agent, r.session, *argv, check=False)


@pytest.mark.parametrize("target", ["dev-stats", "hardware"])
async def test_admission_failslab(pppoe_rig, target):
    """A session direction's admission takes a reference on the ppp device's
    statistics record before its hardware entry exists. A hardware failure
    after that has to hand the reference back, and the connection recovers by
    traffic alone. A failure creating the record itself is not an admission
    failure: that direction forwards in hardware and counts nowhere, which the
    record's references and counters both show.

    The UDP upload is Linux's and refused before it asks for any record, so
    the download is the one direction the fault can meet. Without a record
    from the download, the device has none at all."""
    from _flowtable_failslab import (slab_fault)
    from _flowtable_service import (FAULT_DIR)

    r = pppoe_rig
    # The guard is staged and cancelled over the DUT console; no service
    # fixture runs here to own the directory it lives in.
    r.service_console = r.console
    await console_command(r.console, "rm", "-rf", FAULT_DIR)
    await console_command(r.console, "mkdir", FAULT_DIR)
    try:
        initial = await r.state()
        await r.table()
        async with slab_fault(r, target, "pppoe-" + target) as fault:
            await r.exchange(count=4)
            hit = await fault.hit()
        deadline = time.monotonic() + 20
        while True:
            await r.exchange(count=4)
            state = await r.state()
            if len(state["flows"]) == 1:
                break
            assert time.monotonic() < deadline, state
    finally:
        await console_command(r.console, "rm", "-rf", FAULT_DIR, check=False)
    flows = await _download_only(r, initial)
    _assert_session(r, None, _direction(flows, INNER_LOCAL, r.lan_ip))
    state = await r.state()
    assert state["errors"] == initial["errors"] and state["fatal"] == state["quarantine"] == 0, state
    assert state["installs"] - state["deletes"] == state["entries"] == 1, state
    session = _session_text(r.session_identity)
    if target == "dev-stats":
        assert state["session_records"] == initial["session_records"], (initial, state)
        assert not [s for s in state["sessions"] if s["pppoe"] == session], state["sessions"]
        counted = None
    else:
        assert state["session_records"] == initial["session_records"] + 1, (initial, state)
        counted = _session_row(state, r.session_identity)
        assert counted["slot"] == "yes", counted
    before = {f["cookie"]: int(f["packets"]) for f in flows}
    installed = state
    await r.exchange(count=64)
    state = await r.state()
    _assert_undisturbed(r, installed, state, {f["cookie"] for f in state["flows"]} == set(before))
    assert {f["cookie"]: int(f["packets"]) - before[f["cookie"]]
            for f in state["flows"]} == {c: 64 for c in before}, (before, state["flows"])
    # The download strips the session header and the burst moved it by 64,
    # into the record's receive half where there is a record at all.
    if counted is None:
        row = None
        assert not [s for s in state["sessions"] if s["pppoe"] == session], state["sessions"]
    else:
        row = _session_row(state, r.session_identity)
        moved = {half: int(row[half + "_packets"]) - int(counted[half + "_packets"])
                 for half in ("rx", "tx")}
        assert row["refs"] == "1" and moved == {"rx": 64, "tx": 0}, (counted, row)
    r.record("pppoe-" + target + "-recovery", {"initial": initial, "state": state,
                                               "hit": hit, "row": row})
    # Retiring the connection returns exactly the references it took.
    await r.delete_table()
    if row is not None:
        row = _session_row(await r.state(), r.session_identity)
        assert row["refs"] == "0", row


async def test_snat(pppoe_rig):
    """Source NAT across the session, proved at the far endpoint.

    The concentrator observing the translated source is what separates a
    rewrite that reached the wire from one that only reached the rule. The UDP
    upload is Linux's, so here the translation on the way out is software's
    and the hardware's half is the download's: the reverse translation, after
    the strip. The upload's translation in front of the insert is
    test_snat_tcp's.
    """
    r = pppoe_rig
    await command(r.target, r.session, "nft", _snat_table(r))
    try:
        flows, delta = await _established(r)
        reverse = _direction(flows, INNER_LOCAL, SNAT_ADDR)
        assert reverse["new_dst"].startswith(r.lan_ip + ":"), reverse
        _assert_session(r, None, reverse)
        assert all(d == 64 for d in delta.values()), delta
        # What the wire carried, not what the rule said it would.
        assert r.echo.sources == {(SNAT_ADDR, SPORT)}, r.echo.sources
        r.record("pppoe-snat", {"flows": flows, "delta": delta,
                                "observed": sorted(r.echo.sources)})
    finally:
        await command(r.target, r.session, "nft", "delete", "table", "ip", NAT_TABLE,
                      check=False)


@pytest.mark.parametrize("pppoe_rig", ["tcp"], indirect=True)
async def test_snat_tcp(pppoe_rig):
    """Source NAT in front of the insert, in hardware.

    The concentrator seeing the translated source connect, while the upload
    that carried the connection was in hardware, says the translation and the
    encapsulation were applied to the same frame in the right order: the
    rewrite before the session header went on, and the reverse translation
    after it came off.
    """
    r = pppoe_rig
    await command(r.target, r.session, "nft", _snat_table(r))
    try:
        await r.table()
        measured = await _tcp_carried(r, "flowtable_pppoe_snat_tcp")
        flows = measured["flows"]
        r.record("pppoe-snat-tcp", measured)
        forward = _direction(flows, r.lan_ip, INNER_LOCAL)
        assert forward["new_src"].startswith(SNAT_ADDR + ":"), forward
        reverse = _direction(flows, INNER_LOCAL, SNAT_ADDR)
        assert reverse["new_dst"].startswith(r.lan_ip + ":"), reverse
        _assert_session(r, forward, reverse)
        _assert_carried(r, measured)
        assert measured["peer"] == (SNAT_ADDR, SPORT), measured["peer"]
    finally:
        await command(r.target, r.session, "nft", "delete", "table", "ip", NAT_TABLE,
                      check=False)


@pytest.mark.parametrize("pppoe_rig", ["tagged", "tagged-tcp"], indirect=True)
async def test_tagged_lan(pppoe_rig):
    """A tag on the LAN and a session on the WAN, so every slot is spent.

    The WAN path already costs a tag and a session, which is both
    encapsulation slots that direction has. Adding a tag on the LAN gives each
    direction something to describe on each side at once: the forward rule pops
    the LAN tag, pushes the WAN tag and pushes the session, and the reverse one
    is the mirror. A derivation that counted the session against the wrong
    direction's budget, or emitted its push in the wrong place, produces an
    action list of the right length for the wrong reason -- so the tags are
    asserted per direction, not as a set.

    Over UDP the upload is Linux's and the mirror is what is proved; over TCP
    both directions are.
    """
    r = pppoe_rig
    if r.proto == "tcp":
        await r.table()
        delta = await _tcp_carried(r, "flowtable_pppoe_tagged_tcp")
        flows = delta["flows"]
        forward = _direction(flows, r.lan_ip, INNER_LOCAL)
        assert forward["in_vlan"] == str(LAN_VID), forward
        _assert_carried(r, delta)
    else:
        flows, delta = await _established(r)
        forward = None
        assert all(d == 64 for d in delta.values()), delta
    reverse = _direction(flows, INNER_LOCAL, r.lan_ip)
    _assert_session(r, forward, reverse)
    assert reverse["out_vlan"] == str(LAN_VID), reverse
    r.record("pppoe-tagged-lan-" + r.proto, {"flows": flows, "delta": delta,
                                             "lan_vid": LAN_VID, "wan_vid": WAN_VID})


async def test_full_mtu_datagram(pppoe_rig):
    """A datagram filling the session MTU still crosses it.

    The frame the session carries is twelve bytes longer than the datagram
    inside it: eight for the PPPoE and PPP headers and four for the tag the
    session stands on. If the hardware's own size check counted any of them,
    this is the payload that would be dropped or punted while a shorter one was
    forwarded, so the counters have to account for it exactly as for any other
    burst. 1492 is the path MTU rather than a number chosen here, which is what
    makes the reply the same size as the request.

    The UDP upload is Linux's, so the hardware's datagram here is the reply,
    stripped of all twelve; the insert's large segments are
    test_tcp's.
    """
    r = pppoe_rig
    initial = await r.state()
    await r.table()
    await r.exchange(count=4)
    before = {f["cookie"]: int(f["packets"]) for f in await _download_only(r, initial)}
    installed = await r.state()
    # The session MTU less the IPv4 and UDP headers: the largest datagram the
    # path takes without fragmenting, and exactly the one the eight bytes of
    # PPPoE would push over if they were counted twice.
    await r.exchange(count=16, payload_size=SESSION_MTU - 28)
    state = await r.state()
    after = {f["cookie"]: int(f["packets"]) for f in state["flows"]}
    _assert_undisturbed(r, installed, state, set(before) == set(after))
    delta = {c: after[c] - before[c] for c in before}
    assert all(d == 16 for d in delta.values()), delta
    r.record("pppoe-full-mtu", {"delta": delta, "payload": SESSION_MTU - 28})


async def test_qos_upload_keeps_its_class(pppoe_rig):
    """A marked upload through the session lands on its class, and unmarked
    bulk through the same session cannot starve it.

    Every frame the CPU sends into a PPPoE session loses its conntrack and its
    ingress index before it reaches the port: ppp_start_xmit() scrubs both. The
    port's queue selection used to take such a frame for the gateway's own,
    which put every upload over the session -- marked or not -- on class queue
    7, above every class in the tree. The marked flow lost its class, and an
    unmarked one could take the whole channel from every leaf. Now the port
    reads the frame through the tag and the session header and finds the
    connection again by the translated packet's tuple.

    The tree is on the WAN port: one channel at QOS_RATE_MBIT and one prio 1
    leaf. The marked flow is translated, as a subscriber's is, and echoed one
    datagram at a time while the unmarked one offers three times the channel.
    Nothing is offloaded -- no flowtable is bound -- so this is the software
    path alone. The oracles are the port's CEETM counters: every marked frame
    on the leaf, the unmarked flow holding the unclassified queue full, and
    the control queue carrying only control traffic.
    """
    from _flowtable_qos import (OAL, egress, leaf_delta, timing_slack)

    r = pppoe_rig
    mask = int((await read(r.target, r.session,
                           "/sys/module/ask_flowtable/parameters/qos_mark_mask")).strip())
    if not mask:
        pytest.skip("classification is off in this boot; the QoS case needs "
                    "ask_flowtable.qos_mark_mask=0xf0, which the test image ships")
    mark = QOS_VOICE_CQ << ((mask & -mask).bit_length() - 1)
    dev = TARGET_WAN_IF
    rate = f"{QOS_RATE_MBIT}mbit"

    async def tc(*argv, check=True):
        """`tc` is not in the agent's argv allowlist, so the tree is built on
        the console the fixture holds."""
        return await console_command(r.console, "tc", *argv, check=check, timeout=30)

    async def clear_ct():
        for port in (PORT_QOS_VOICE, PORT_QOS_BULK):
            await command(r.target, r.session, "conntrack", "-D", "-p", "udp",
                          "--orig-src", r.lan_ip, "--dport", str(port), check=False)

    # A port nothing reads: the far end queues what arrives until its buffer
    # is full and drops the rest, rather than answering every datagram with an
    # ICMP error back down the session.
    sink = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sink.bind((INNER_LOCAL, PORT_QOS_BULK))
    transport, echo = await asyncio.get_running_loop().create_datagram_endpoint(
        SourceEcho, local_addr=(INNER_LOCAL, PORT_QOS_VOICE))
    bulk = False
    try:
        await clear_ct()
        # A run killed before its teardown leaves its tree behind.
        await tc("qdisc", "del", "dev", dev, "root", check=False)
        await tc("qdisc", "add", "dev", dev, "root", "handle", "1:", "htb", "offload")
        await tc("class", "add", "dev", dev, "parent", "1:", "classid", "1:1",
                 "htb", "rate", rate, "ceil", rate)
        await tc("class", "add", "dev", dev, "parent", "1:1", "classid", "1:10",
                 "htb", "rate", rate, "ceil", rate, "prio", str(QOS_VOICE_PRIO))
        # The mark at forward/mangle, and the translation at the priority the
        # SNAT case uses, ahead of the image's own masquerade.
        await command(r.target, r.session, "nft", f"""table ip {QOS_TABLE} {{
 chain forward {{ type filter hook forward priority -150; policy accept;
 ip saddr {r.lan_ip} udp dport {PORT_QOS_VOICE} ct mark set {mark:#x}; }}
 chain postrouting {{ type nat hook postrouting priority 90; policy accept;
 ip saddr {r.lan_ip} ip daddr {INNER_LOCAL} udp dport {PORT_QOS_VOICE} snat to {SNAT_ADDR}; }}
}}""")
        await _qos_bulk(r, seconds=30)
        bulk = True
        await asyncio.sleep(3)
        first = await egress(r, dev)
        voice = f'''
import json, socket, struct, time
s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
s.bind(({r.lan_ip!r}, {PORT_QOS_VOICE}))
s.settimeout(1)
echoed = lost = 0
for n in range({QOS_COUNT}):
    payload = struct.pack('!Q', n) + b'ASK-pppoe-qos'.ljust(120, b'.')
    s.sendto(payload, ({INNER_LOCAL!r}, {PORT_QOS_VOICE}))
    try:
        while s.recv(2048) != payload:
            pass
        echoed += 1
    except TimeoutError:
        lost += 1
    time.sleep(0.02)
print(json.dumps({{'echoed': echoed, 'lost': lost}}))
'''
        result = await r.run_peer(voice, label="pppoe_qos_voice", timeout=QOS_COUNT * 1.1 + 30)
        assert result.rc == 0, result.stdout
        report = json.loads(result.stdout.strip().splitlines()[-1])
        second = await egress(r, dev)
        conntrack = await command(r.target, r.session, "conntrack", "-L", "-p", "udp",
                                  "--dport", str(PORT_QOS_VOICE), "-o", "extended", check=False)
    finally:
        if bulk:
            await _qos_bulk_stop(r)
        transport.close()
        sink.close()
        await tc("qdisc", "del", "dev", dev, "root", check=False)
        await command(r.target, r.session, "nft", "delete", "table", "ip", QOS_TABLE,
                      check=False)
        await clear_ct()
    voice_leaf = leaf_delta(first, second, 0)
    unclassified = leaf_delta(first, second, "default")
    control = leaf_delta(first, second, "control")
    window = second["at"] - first["at"]
    shaped = sum((q["bytes"] + OAL * q["frames"]) * 8
                 for q in (voice_leaf, unclassified, control)) / window
    r.record("pppoe-qos", {"report": report, "voice_leaf": voice_leaf,
                           "unclassified": unclassified, "control": control,
                           "shaped_bps": shaped, "window": window,
                           "sources": sorted(echo.sources),
                           "conntrack": conntrack["stdout"]})

    # Translated on the way, so the connection was found by the inverse of a
    # tuple the table does not hold.
    assert echo.sources == {(SNAT_ADDR, PORT_QOS_VOICE)}, echo.sources
    # Every marked frame on the leaf its mark names, and it lost nothing to
    # the unmarked flow beside it: the leaf is above the queue that flow is on.
    assert report["echoed"] + report["lost"] == QOS_COUNT, report
    assert report["lost"] <= QOS_COUNT // 100, report
    assert QOS_COUNT - report["lost"] <= voice_leaf["frames"] <= QOS_COUNT, (voice_leaf, report)
    assert voice_leaf["rejected"] == 0, voice_leaf
    # The unmarked flow held the unclassified queue full -- the queue refused
    # what the channel could not carry -- and the channel carried what it was
    # shaped to.
    assert unclassified["rejected"] > 0 and unclassified["frames"] > 0, unclassified
    slack = timing_slack(first, second)
    assert shaped >= (0.85 - slack) * QOS_RATE_MBIT * 1e6, (shaped, slack)
    # None of it rode the control queue, which now carries only control
    # traffic: the session's LCP, the gateway's own sessions.
    assert control["frames"] * 20 < unclassified["frames"], (control, unclassified)


@pytest.mark.parametrize("pppoe_rig", ["tcp"], indirect=True)
async def test_tcp(pppoe_rig):
    """An established TCP connection across the session.

    The classifier punts SYN, FIN and RST before its own lookup, so what the
    hardware actually carries is the bulk transfer in the middle. The cookies
    staying put is what proves the connection was never readmitted underneath
    it -- and with a session that matters more than elsewhere, because a
    readmission against a changed session would still forward, just to a
    header the concentrator no longer answers.

    It is also the upload in hardware, which UDP cannot be: the insert, the
    session MTU it carries, its large segments crossing whole, and the
    record's transmit half counting them into the ppp device's own counters.
    """
    r = pppoe_rig
    await r.table()
    measured = await _tcp_carried(r, "flowtable_pppoe_tcp")
    flows = measured["flows"]
    r.record("pppoe-tcp", {**measured, "session": _session_text(r.session_identity)})
    forward = _direction(flows, r.lan_ip, INNER_LOCAL)
    reverse = _direction(flows, INNER_LOCAL, r.lan_ip)
    _assert_session(r, forward, reverse)
    # The forward direction leaves by the session, so it carries the session's
    # MTU; nothing in this test set it, and the eight bytes are already in it.
    assert int(forward["mtu"]) == SESSION_MTU, forward
    _assert_carried(r, measured)


async def test_mtu_retires(pppoe_rig):
    """The ppp device carries its own MTU, and a flow through it depends on it.

    Each direction carries the MTU of the interface it leaves by. The UDP
    upload is Linux's at any session MTU below a full frame, so the direction
    in hardware is the download, which arrives by the session and leaves by
    the LAN port at the port's MTU. It still depends on the ppp device it
    arrives on: lowering the session retires the connection -- one increment,
    for the one invalidation handle both directions share -- and the download
    comes back as it was, the upload still refused. That the session direction
    describes the session's MTU is test_tcp's to show.
    """
    r = pppoe_rig
    await r.table()

    async def settled(expected, since=None):
        """`expected` maps the egress port of each direction hardware should
        hold to the MTU it should describe; `since`, a state the directions
        must have been installed after. Readmission needs traffic, so each
        attempt sends before it looks; nothing re-offers a retired flow on its
        own."""
        for _ in range(10):
            await r.exchange(count=4)
            state = await r.state()
            if (sorted(f["out"] for f in state["flows"]) == sorted(expected)
                    and all(int(f["mtu"]) == expected[f["out"]] for f in state["flows"])
                    and (since is None or state["installs"] > since["installs"])):
                return state
        pytest.fail(f"flow did not settle at {expected}: {state}")

    before = await settled({TARGET_LAN_IF: 1500})
    await command(r.target, r.session, "ip", "link", "set", r.ppp_if, "mtu", "1400")
    try:
        invalidated = await r.wait(
            lambda s: s["mtu_invalidations"] >= before["mtu_invalidations"] + 1)
        # The same shape as before, so what tells the readmitted download from
        # the retired one is that it was installed since.
        reduced = await settled({TARGET_LAN_IF: 1500}, since=before)
        assert reduced["errors"] == before["errors"], reduced
        assert reduced["rejects"] > invalidated["rejects"], (invalidated, reduced)
        # The session survived the MTU change, so the flow came back describing
        # the same session rather than a different one.
        assert await _session_identity(r) == r.session_identity
        r.record("pppoe-mtu", {"before": before, "invalidated": invalidated,
                               "reduced": reduced})
    finally:
        await command(r.target, r.session, "ip", "link", "set", r.ppp_if,
                      "mtu", str(SESSION_MTU), check=False)


@pytest.mark.rfc("2516", section="5")
@pytest.mark.rfc("1661", section="5.8")
async def test_session_retires_and_redials(pppoe_rig):
    """The session going away retires the flow, and a redial readmits it.

    This is the dependency proof. The session is what the hardware inserts and
    strips, and nothing about it reaches the rule that could be revalidated
    later: the id is in an action the flow was built from once, and the
    concentrator's address is in no action at all. So hanging the session up
    has to retire the flow -- otherwise the hardware keeps inserting a session
    header the concentrator has already forgotten, and the frames vanish with
    every counter looking healthy.

    What notices is the route. pppd's peer route dies with the device, and the
    flow borrowed that destination, so the route watch retires its directions,
    normally before the device is even unregistered. Should the unregistration
    arrive while the entries are still being taken out, it retires them too
    rather than stopping admission: a ppp device is neither bound nor a port.
    That makes a session drop *selective*: the retirement costs
    the directions it should and nothing else, the bindings stay up, and
    admission is never disabled. A drop is therefore self-healing -- the table
    is not touched, nothing re-arms, and the next packet re-offers the flow
    against whatever session exists then, which is the assertion that matters
    and the one a stale entry would fail. The UDP upload is Linux's, so the
    direction in hardware on either side of the redial is the download, and
    the session it names is the one it strips.
    """
    r = pppoe_rig
    flows, delta = await _established(r)
    reverse = _direction(flows, INNER_LOCAL, r.lan_ip)
    _assert_session(r, None, reverse)
    assert all(d == 64 for d in delta.values()), delta
    before = await r.state()
    first = r.session_identity

    await _hangup(r.console)
    # The route is what goes first, so that is what the convergence waits on.
    retired = await r.wait(
        lambda s: not s["entries"] and
        s["route_invalidations"] >= before["route_invalidations"] + 1, timeout=30)
    # Selective, and that is the result: the table is untouched, so admission
    # was never disabled and nothing has to be rebuilt to get it back.
    assert retired["bindings"] == 2, retired
    assert retired["invalidated"] == 0 and retired["invalidation_done"] == 0, retired
    assert retired["rearms"] == before["rearms"], retired
    assert retired["handle_refs"] == retired["neighbour_refs"] == 0, retired
    assert retired["fatal"] == retired["quarantine"] == 0, retired
    assert retired["errors"] == before["errors"], retired
    # The device is gone, so /proc/net/pppoe has nothing left to describe.
    assert not (await read(r.target, r.session, "/proc/net/pppoe")).splitlines()[1:]

    # The redial's discovery and the session's keepalive, as the concentrator
    # sees them on the wire.
    pcap = ft.artifact_dir() / "pppoe-redial.pcap"
    ft.artifact_dir().mkdir(parents=True, exist_ok=True)
    dump = subprocess.Popen(["tcpdump", "-i", SERVER_IF, "-U", "-s", "96", "-w", str(pcap),
                             "pppoed or pppoes"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    await asyncio.sleep(1.5)
    r.ppp_if, r.ppp_pid = await _dial(r.console, r.ppp_lower, ipv6=r.session_ipv6)
    await asyncio.sleep(2 * LCP_ECHO_INTERVAL + 1)
    dump.terminate()
    dump.wait(5)
    from scapy.all import PPPoED, PPP_LCP_Echo, rdpcap
    frames = rdpcap(str(pcap))
    # PADI, PADO, PADR, PADS in order (RFC 2516 §5), then an echo request
    # answered by a reply on the session (RFC 1661 §5.8).
    discovery = [p[PPPoED].code for p in frames if PPPoED in p]
    assert discovery[:4] == [0x09, 0x07, 0x19, 0x65], discovery
    echoes = {p[PPP_LCP_Echo].code for p in frames if PPP_LCP_Echo in p}
    assert echoes >= {9, 10}, echoes
    # The device went and took its routes with it; the concentrator still has
    # no other way back to the LAN.
    server_ppp_if = await _concentrator_if(r)
    for prefix in (r.reachable, f"{SNAT_ADDR}/32"):
        await command(r.wan, r.session, "ip", "route", "replace", prefix,
                      "dev", server_ppp_if)
    second = await _session_identity(r)
    r.session_identity = second
    # The path went down and came back, so let the bring-up settle before any
    # measurement rather than charging a lost first datagram to the adapter.
    await _wait_reachable(r)

    # Nothing re-offers a retired flow on its own, so each attempt sends before
    # it looks. What it does not need is the table: the retirement was
    # selective, so the bindings that were there before are the ones admitting
    # this flow again.
    for _ in range(10):
        await r.exchange(count=4)
        state = await r.state()
        if state["entries"] == 1:
            break
    else:
        pytest.fail(f"the flow was not readmitted after the redial: {state}")
    readmitted = await _download_only(r, retired)
    reverse = _direction(readmitted, INNER_LOCAL, r.lan_ip)
    # Against the session that exists now. A flow that had survived the hangup,
    # or been readmitted from anything cached, would name the old one -- which
    # is exactly the failure that forwards happily and delivers nothing.
    _assert_session(r, None, reverse, session=second)
    after = await r.state()
    # Readmitted through the bindings that were never disturbed: no global
    # invalidation to clear, and nothing to re-arm.
    assert after["bindings"] == 2 and after["rearms"] == before["rearms"], after
    assert after["invalidated"] == after["invalidation_done"] == 0, after
    assert after["errors"] == before["errors"], after
    # And the readmitted flow forwards, measured the same way as any other.
    counts = {f["cookie"]: int(f["packets"]) for f in readmitted}
    await r.exchange(count=32)
    measured = await r.state()
    final = {f["cookie"]: int(f["packets"]) for f in measured["flows"]}
    _assert_undisturbed(r, after, measured, set(final) == set(counts))
    assert all(final[c] - counts[c] == 32 for c in counts), (counts, final)
    r.record("pppoe-redial", {"first": _session_text(first),
                              "second": _session_text(second),
                              "before": before, "retired": retired, "after": after})


@pytest.mark.parametrize("mode", ["6o4", "4o6"])
@pytest.mark.parametrize("pppoe_rig", ["ipv6"], indirect=True)
async def test_tunnel(pppoe_rig, mode):
    """A tunnel whose outer packets leave by the session: 6rd or a tunnel
    broker on a PPPoE WAN for 6o4, DS-Lite on one for 4o6.

    One direction is both encapsulations at once -- the tunnel's outer header,
    then the session's, then the tag the session runs over -- and the other
    arrives inside all three. The outer header is addressed to the far end of
    the tunnel, which is not the neighbour the frame is for: the concentrator
    is, and the only place its Ethernet address is recorded is the session.
    Frames that reach the concentrator's ppp device, where the capture sits,
    are ones its PPPoE stack took for this session, which is the proof that
    the frame was addressed to it.

    A UDP upload is Linux's -- an Ethernet LAN can deliver a full frame and
    the tunnel's path is smaller -- so over UDP the direction proved is the one
    that arrives inside all three, and the records are held by it alone. The
    upload that leaves inside all three is proved over TCP, which the uplink's
    MSS clamp keeps within the tunnel.
    """
    import _flowtable_tunnel as tunnel

    r = pppoe_rig
    shape = tunnel.Shape(mode, 48960, 48961)
    if mode == "6o4":
        shape.outer, shape.mtu = (INNER_REMOTE, INNER_LOCAL), SESSION_MTU - 20
    else:
        shape.outer, shape.mtu = (INNER_REMOTE6, INNER_LOCAL6), SESSION_MTU - 60
    r.shape = shape
    # The concentrator's ppp device is where the outer packets are plain IP.
    r.wan_if = r.server_ppp_if
    cleanup, lan_cleanup = [], []
    transport = None
    try:
        await tunnel._lan_side(r, cleanup, lan_cleanup)
        if shape.family == 4:
            for proto in ("udp", "tcp"):
                accept = ["POSTROUTING", "-s", r.lan_address, "-d", shape.inner_orch, "-p", proto,
                          "--sport", str(shape.sport), "--dport", str(shape.dport), "-j", "ACCEPT"]
                await command(r.target, r.session, "iptables", "-t", "nat", "-I", *accept)
                cleanup.append((r.target, ["iptables", "-t", "nat", "-D", *accept]))
        await tunnel._dut_tunnel(r, cleanup)
        await tunnel._orchestrator_tunnel(r, cleanup)
        await tunnel._wait_reachable(r)
        await tunnel._clear_ct(r)
        transport, _ = await asyncio.get_running_loop().create_datagram_endpoint(
            tunnel.EchoServer, local_addr=(shape.inner_orch, shape.dport),
            family=socket.AF_INET6 if shape.family == 6 else socket.AF_INET)
        before = await r.state()
        flows, delta = await tunnel._established(r, name=f"pppoe-{mode}")
        forward, reverse = tunnel._directions(r, flows)
        tunnel._assert_tunnel(r, forward, reverse)
        _assert_session(r, forward, reverse)
        assert all(d == 64 for d in delta.values()), delta
        state = await r.state()
        # One tunnel record and the one session record, each held by every
        # direction of the one connection that is in hardware.
        row = _session_row(state, r.session_identity)
        assert row["refs"] == str(len(flows)), (row, flows)
        tunnels = [t for t in state["tunnels"] if t["dev"] == shape.device]
        assert len(tunnels) == 1 and tunnels[0]["refs"] == str(len(flows)), state["tunnels"]
        assert state["errors"] == before["errors"], (before, state)
        r.record(f"pppoe-tunnel-{mode}", {"flows": flows, "delta": delta, "session": row,
                                          "tunnel": tunnels[0]})

        # The upload inside all three, over TCP.
        await tunnel._offload_table(r, "tcp")

        def tcp(s):
            return [f for f in s["flows"] if f["proto"] == "6"]

        async with GatedTcp(r.run_peer, source=r.lan_address, sport=shape.sport,
                            peer=shape.inner_orch, dport=shape.dport,
                            label=f"flowtable_pppoe_{mode}_tcp") as transfer:
            await transfer.warmed()
            started = await r.wait(lambda s: len(tcp(s)) == 2)
            await transfer.measure()
            ended = await r.state()
        forward, reverse = tunnel._directions(r, tcp(started))
        assert forward is not None, tcp(started)
        tunnel._assert_tunnel(r, forward, reverse)
        _assert_session(r, forward, reverse)
        moved = {f["cookie"]: int(f["packets"]) for f in tcp(ended)}
        assert moved.keys() == {forward["cookie"], reverse["cookie"]}, ended
        upload = moved[forward["cookie"]] - int(forward["packets"])
        assert upload > 100, (upload, transfer.report)
        assert ended["errors"] == before["errors"], (before, ended)
        r.record(f"pppoe-tunnel-{mode}-tcp", {"flows": tcp(started), "ended": ended,
                                              "report": transfer.report})
    finally:
        if transport:
            transport.close()
        failures = []
        await command(r.target, r.session, "nft", "delete", "table", "inet", tunnel.TABLE,
                      check=False)
        if hasattr(r, "lan_address"):
            await tunnel._clear_ct(r)
        for agent, argv in reversed(cleanup):
            result = await command(agent, r.session, *argv, check=False)
            if result["rc"] and "Cannot find device" not in (result.get("stderr") or ""):
                failures.append(result)
        for cmd in reversed(lan_cleanup):
            await lan_run(r.lan, cmd)
        assert not failures, failures
