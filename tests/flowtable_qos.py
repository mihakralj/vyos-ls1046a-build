"""Hardware QoS on offloaded flows, proved at rates only the hardware reaches.

Software forwarding on this rig tops out near 130 Mbit/s and the ports run at
10 Gbit/s, so every rate case here shapes or polices around 2 Gbit/s and offers
three times that. Delivery near the cap then says two things at once: the
hardware enforced the rate, and the flow was in hardware, because nothing else
could have carried it that fast. A low-rate result proves neither. Beside the
rate, two independent oracles:

  - **the adapter's row.** It names the class the hardware entry was given, and
    its packet counter moves only for frames the classifier matched.
  - **the leaf counters `ethtool -S` reports.** One set per leaf slot: what that
    class queue dequeued and what its congestion group rejected. An offloaded
    flow never reaches a leaf's software qdisc, so `tc -s class show` cannot see
    it; these can.

Most traffic runs from the orchestrator to the LAN VM, so the trees are built on
the LAN port. The orchestrator is the side that can offer several times the cap,
and the WAN port carries the agent every oracle is read through. The one case
about changing the WAN port's own egress runs the other way.

Enabling CEETM on a port moves its sub-portal's dequeues onto the LNI scheduler,
and removing a tree once left that switched, with the port reporting healthy
while it transmitted nothing. The fixture's last act is therefore to forward
traffic through both ports after every tree is gone: a case that leaves a port
unable to transmit fails there, not in whichever test happens to run next.

Needs `ask_flowtable.qos_mark_mask` nonzero -- the test image ships 0xf0 -- and
iperf3 on the LAN VM and on the orchestrator.
"""
from __future__ import annotations

import pytest

from _flowtable_qos import HIGH_CQ, HIGH_PRIO, LOW_CQ, LOW_PRIO, PORT_BE, PORT_BULK, PORT_EF, PORT_EF_BEFORE, PORT_EF_MOVED, PORT_EF_SOFTWARE, PORT_HIGH, PORT_LOW, PORT_PROBE, PORT_REMARK_HW, PORT_REMARK_SW, PORT_UNCLASSIFIED_HW, PORT_UNCLASSIFIED_SW, PORT_WEIGHTED, PORT_WEIGHTED_BULK, TCP_FRAME, TCP_PAYLOAD

from _flowtable_qos import (CAP_MBIT, COUNT, DATAGRAM, EF_TOS, OAL, OFFERED_MBIT, POLICE_BURST, PORT_DECLINED, PORT_DEFAULT, PORT_EF_REPLACED, PORT_EGRESS, PORT_POLICED, PORT_SATURATE, PORT_SHAPED, PROBE_SLACK, REMARK_CLASS, REMARK_MASK, SETTLE, TAIL_FRAMES, UDP_HEADERS, UNSHAPED_GBPS, WEIGHTED_CQ, WINDOW, WRED_BANDS, WRED_LIMIT, WRED_MBIT, WRED_PROBABILITY, admit, captured, conntrack_ids, directions, dut_ping, ef_filter, egress, handshakes, inbound, iperf, lan_start, leaf_delta, lockstep, logged, offered, offload, police_counters, probe, qdisc_shown, read_intervals, readmitted, received_bps, received_loss, reload_adapter, shaped_bps, timing_slack, tree)

import asyncio
import json
import re
import statistics
import threading
import time


from _topology import LAN_NIC, TARGET_LAN_IF, TARGET_WAN_IF, lan_run_python
from _flowtable_rig import (WAN_IP, command, read)


# ---- the scheduler ---------------------------------------------------------

async def test_htb_shapes_at_the_cap(qos):
    """One class at the cap, one offloaded flow offered three times it.

    The shaper's own output is measured from the leaf's dequeued bytes over a
    window inside the transfer, which is the hardware's figure rather than the
    receiver's: it has to sit on the cap. The receiver's steady-state goodput is
    held to the same cap less the headers and the shaper's per-frame charge.

    Every frame the classifier matched for the flow is accounted for at the
    leaf -- dequeued or rejected -- and the only frames the leaf may carry beyond
    those are ones the CPU sent before the flow was admitted, which the port's
    software transmit counter bounds. The rejections are the other half of the
    proof: the shaper held the rate by refusing the excess, rather than the
    sender failing to offer it.
    """
    r = qos
    await tree(r, TARGET_LAN_IF, CAP_MBIT, [("1:10", HIGH_PRIO)])
    await offload(r, inbound(r, "udp", PORT_SHAPED, r.mark(HIGH_CQ)))
    await lan_start(r, iperf=[PORT_SHAPED])
    target = f"{r.lan_ip}:{PORT_SHAPED}"
    before = await egress(r, TARGET_LAN_IF)
    client = asyncio.create_task(iperf(r, PORT_SHAPED, udp_mbit=OFFERED_MBIT))
    try:
        await asyncio.sleep(SETTLE)
        first = await egress(r, TARGET_LAN_IF)
        installed = await r.state()
        await asyncio.sleep(WINDOW)
        second = await egress(r, TARGET_LAN_IF)
        report = await client
    finally:
        if not client.done():
            client.cancel()
            await asyncio.gather(client, return_exceptions=True)
    # The class queue drains at the shaped rate; give it time to empty before
    # the totals are compared.
    await asyncio.sleep(0.5)
    after = await egress(r, TARGET_LAN_IF)
    state = await r.state()
    forward = directions(state, ingress=TARGET_WAN_IF, proto=17, dst=target)
    early = directions(installed, ingress=TARGET_WAN_IF, proto=17, dst=target)
    cap = CAP_MBIT * 1e6
    window = leaf_delta(first, second, 0)
    whole = leaf_delta(before, after, 0)
    shaped = shaped_bps(first, second, 0)
    goodput = received_bps(report)
    offered = report["end"]["sum_sent"]["bits_per_second"]
    r.record("qos-htb-shaped", {"installed": installed, "state": state, "window": window,
                                "whole": whole, "shaped_bps": shaped, "goodput_bps": goodput,
                                "offered_bps": offered, "before": before, "first": first,
                                "second": second, "after": after, "report": report})

    assert len(forward) == 1 and len(early) == 1, (early, forward)
    row = forward[0]
    assert row["out"] == TARGET_LAN_IF and int(row["qos"], 16) == HIGH_CQ, row
    # One admission for the whole transfer: the row read mid-window is the row
    # whose packets are accounted below.
    assert early[0]["cookie"] == row["cookie"], (early, row)
    assert offered >= 2 * cap, ("the orchestrator did not offer enough to test a cap", offered)
    slack = timing_slack(first, second)
    assert (0.95 - slack) * cap <= shaped <= (1.03 + slack) * cap, (shaped, cap, slack, window)
    assert window["rejected"] >= window["frames"], window
    software = second["software_tx"] - first["software_tx"]
    assert software <= window["frames"] // 100, (software, window)
    expected = cap * DATAGRAM / (DATAGRAM + UDP_HEADERS + OAL)
    assert 0.9 * expected <= goodput <= 1.02 * expected, (goodput, expected)
    extra = whole["frames"] + whole["rejected"] - int(row["packets"])
    assert 0 <= extra <= after["software_tx"] - before["software_tx"], (
        whole, row["packets"], after["software_tx"] - before["software_tx"])


async def test_strict_priority_keeps_its_rate(qos):
    """Two classes on one channel at the cap: the high-priority one offered half
    of it, the low-priority one three times all of it.

    Strict priority serves the high class whenever it holds a frame, so
    saturating its neighbour must cost it nothing: its leaf dequeues what it
    offered and rejects next to nothing, and its receiver loses next to nothing.
    The low class gets what is left -- the two leaves together sit on the cap --
    and its leaf is where the excess is refused.
    """
    r = qos
    await tree(r, TARGET_LAN_IF, CAP_MBIT, [("1:10", HIGH_PRIO), ("1:11", LOW_PRIO)])
    await offload(r, inbound(r, "udp", PORT_HIGH, r.mark(HIGH_CQ)),
                  inbound(r, "udp", PORT_LOW, r.mark(LOW_CQ)))
    await lan_start(r, iperf=[PORT_HIGH, PORT_LOW])
    high_mbit = CAP_MBIT // 2
    clients = [asyncio.create_task(iperf(r, PORT_HIGH, udp_mbit=high_mbit)),
               asyncio.create_task(iperf(r, PORT_LOW, udp_mbit=OFFERED_MBIT))]
    try:
        await asyncio.sleep(SETTLE)
        first = await egress(r, TARGET_LAN_IF)
        state = await r.state()
        await asyncio.sleep(WINDOW)
        second = await egress(r, TARGET_LAN_IF)
        high, low = await asyncio.gather(*clients)
    finally:
        for client in clients:
            if not client.done():
                client.cancel()
        await asyncio.gather(*clients, return_exceptions=True)
    cap = CAP_MBIT * 1e6
    # Shaper bits per payload bit, for datagrams of DATAGRAM bytes.
    charge = (DATAGRAM + UDP_HEADERS + OAL) / DATAGRAM
    held, starved = leaf_delta(first, second, 0), leaf_delta(first, second, 1)
    high_shaped = shaped_bps(first, second, 0)
    total = shaped_bps(first, second, 0, 1)
    rows = {port: directions(state, ingress=TARGET_WAN_IF, proto=17, dst=f"{r.lan_ip}:{port}")
            for port in (PORT_HIGH, PORT_LOW)}
    r.record("qos-strict-priority", {"state": state, "first": first, "second": second,
                                     "high_leaf": held, "low_leaf": starved,
                                     "high_shaped_bps": high_shaped, "total_bps": total,
                                     "high": high, "low": low})

    for port, cq in ((PORT_HIGH, HIGH_CQ), (PORT_LOW, LOW_CQ)):
        assert len(rows[port]) == 1 and int(rows[port][0]["qos"], 16) == cq, rows
    assert low["end"]["sum_sent"]["bits_per_second"] >= 2 * cap, low["end"]["sum_sent"]
    slack = timing_slack(first, second)
    assert (0.95 - slack) * cap <= total <= (1.03 + slack) * cap, (total, cap, slack, held,
                                                                   starved)
    expected_high = high_mbit * 1e6 * charge
    assert (0.95 - slack) * expected_high <= high_shaped <= (1.05 + slack) * expected_high, (
        "the high class did not keep the rate it offered", high_shaped, expected_high, slack)
    assert held["rejected"] <= held["frames"] // 200, held
    assert received_loss(high) <= 0.01, high["server_output_json"]["intervals"]
    assert starved["rejected"] >= starved["frames"], starved
    expected_low = (cap - high_shaped) / charge
    assert 0.85 * expected_low <= received_bps(low) <= 1.05 * expected_low, (
        received_bps(low), expected_low)


async def test_weighted_leaf_outranks_unclassified_traffic(qos):
    """A weighted leaf keeps its rate beside unmarked traffic saturating the
    channel.

    A tree with one `quantum` leaf and no `default`: a marked flow offered half
    the cap lands in the weighted group, and an unmarked one offered three times
    the cap lands where unclassified traffic goes, class queue 0 of the channel,
    which competes for committed tokens. The weighted group sits above that
    queue, so the marked flow must keep the rate it offered and the unmarked one
    gets what is left. With the group placed below class queue 0, as it once
    was, the unmarked flow took every token and the weighted leaf starved.
    """
    r = qos
    dev = TARGET_LAN_IF
    rate = f"{CAP_MBIT}mbit"
    await r.tc("qdisc", "add", "dev", dev, "root", "handle", "1:", "htb", "offload")
    await r.tc("class", "add", "dev", dev, "parent", "1:", "classid", "1:1",
               "htb", "rate", rate, "ceil", rate)
    await r.tc("class", "add", "dev", dev, "parent", "1:1", "classid", "1:10",
               "htb", "rate", rate, "ceil", rate, "quantum", "10")
    # iperf's control connections are TCP on the same ports. Left unmarked,
    # the bulk's would share the queue its own datagrams overrun three times
    # over, and starve until iperf gave up; they carry a few kilobytes, so
    # they ride the weighted leaf without moving its measured rate.
    await offload(r, inbound(r, "udp", PORT_WEIGHTED, r.mark(WEIGHTED_CQ)),
                  inbound(r, "udp", PORT_WEIGHTED_BULK),
                  inbound(r, "tcp", PORT_WEIGHTED, r.mark(WEIGHTED_CQ)),
                  inbound(r, "tcp", PORT_WEIGHTED_BULK, r.mark(WEIGHTED_CQ)))
    await lan_start(r, iperf=[PORT_WEIGHTED, PORT_WEIGHTED_BULK])
    weighted_mbit = CAP_MBIT // 2
    # The bulk starts once the weighted flow is in hardware: until its own
    # entry is installed, three times the cap lands in the CPU's receive queue,
    # and a handshake arriving in that millisecond is dropped with it.
    clients = [asyncio.create_task(iperf(r, PORT_WEIGHTED, udp_mbit=weighted_mbit))]
    try:
        await r.wait(lambda s: directions(s, ingress=TARGET_WAN_IF, proto=17,
                                          dst=f"{r.lan_ip}:{PORT_WEIGHTED}"), timeout=10)
        clients.append(asyncio.create_task(iperf(r, PORT_WEIGHTED_BULK, udp_mbit=OFFERED_MBIT)))
        await asyncio.sleep(SETTLE)
        first = await egress(r, dev)
        state = await r.state()
        await asyncio.sleep(WINDOW)
        second = await egress(r, dev)
        weighted, bulk = await asyncio.gather(*clients)
    finally:
        for client in clients:
            if not client.done():
                client.cancel()
        await asyncio.gather(*clients, return_exceptions=True)
    cap = CAP_MBIT * 1e6
    charge = (DATAGRAM + UDP_HEADERS + OAL) / DATAGRAM
    held, unclassified = leaf_delta(first, second, 0), leaf_delta(first, second, "default")
    weighted_shaped = shaped_bps(first, second, 0)
    total = shaped_bps(first, second, 0, "default")
    rows = {port: directions(state, ingress=TARGET_WAN_IF, proto=17, dst=f"{r.lan_ip}:{port}")
            for port in (PORT_WEIGHTED, PORT_WEIGHTED_BULK)}
    r.record("qos-weighted-leaf", {"state": state, "first": first, "second": second,
                                   "weighted_leaf": held, "unclassified": unclassified,
                                   "weighted_shaped_bps": weighted_shaped, "total_bps": total,
                                   "weighted": weighted, "bulk": bulk})

    # Both in hardware: the marked flow on the weighted queue, the unmarked one
    # with no class, which the port resolves to class queue 0.
    for port, cq in ((PORT_WEIGHTED, WEIGHTED_CQ), (PORT_WEIGHTED_BULK, 0)):
        assert len(rows[port]) == 1 and int(rows[port][0]["qos"], 16) == cq, rows
    assert bulk["end"]["sum_sent"]["bits_per_second"] >= 2 * cap, bulk["end"]["sum_sent"]
    slack = timing_slack(first, second)
    assert (0.95 - slack) * cap <= total <= (1.03 + slack) * cap, (total, cap, slack, held,
                                                                   unclassified)
    expected = weighted_mbit * 1e6 * charge
    assert (0.95 - slack) * expected <= weighted_shaped <= (1.05 + slack) * expected, (
        "the weighted leaf did not keep the rate it offered", weighted_shaped, expected, slack)
    assert held["rejected"] <= held["frames"] // 200, held
    assert received_loss(weighted) <= 0.01, weighted["server_output_json"]["intervals"]
    assert unclassified["rejected"] >= unclassified["frames"], unclassified


async def test_wred_drops_before_the_tail(qos):
    """A RED qdisc on a leaf is that class queue's WRED curve, and the curve is
    what drops -- not the tail.

    An offloaded bulk TCP transfer saturates the class; a probe flow marked into
    the same class queue measures how deep the queue sits, as the round trip it
    adds over an idle one. Three phases on one class:

      - a narrow band: the queue holds within it, far below the tail threshold
        the same qdisc sets, so drops began at the curve;
      - a wider band wholly above the first: the queue rises with it, so where
        drops begin follows the configured threshold rather than a fixed one;
      - no RED at all: the queue fills to the tail-drop depth, which is the
        proof the probe can see this queue in the first place. Without it the
        first two would pass for a probe that bypassed the class entirely.

    Each phase's leaf rejections have to move: they are the drops the curve
    made, and the only counter that sees them for an offloaded flow.
    """
    r = qos
    rate = WRED_MBIT * 1e6
    mark = r.mark(HIGH_CQ)
    await tree(r, TARGET_LAN_IF, WRED_MBIT, [("1:10", HIGH_PRIO)])
    await offload(r, inbound(r, "tcp", PORT_BULK, mark), inbound(r, "udp", PORT_PROBE, mark))
    await lan_start(r, iperf=[PORT_BULK], echo=[PORT_PROBE])
    probe_target = f"{r.lan_ip}:{PORT_PROBE}"
    deadline = time.monotonic() + 20
    while True:
        await asyncio.to_thread(probe, r.lan_ip, 0.5)
        rows = directions(await r.state(), ingress=TARGET_WAN_IF, proto=17, dst=probe_target)
        if rows:
            break
        assert time.monotonic() < deadline, "the probe flow was never admitted"
    assert int(rows[0]["qos"], 16) == HIGH_CQ, rows
    idle = await asyncio.to_thread(probe, r.lan_ip, 1.5)
    base = statistics.median(idle["rtts"])

    async def loaded():
        before = await egress(r, TARGET_LAN_IF)
        bulk = asyncio.create_task(iperf(r, PORT_BULK, seconds=6, streams=4))
        try:
            await asyncio.sleep(2.0)
            sample = await asyncio.to_thread(probe, r.lan_ip, 3.0)
            state = await r.state()
            report = await bulk
        finally:
            if not bulk.done():
                bulk.cancel()
                await asyncio.gather(bulk, return_exceptions=True)
        after = await egress(r, TARGET_LAN_IF)
        return {"added": statistics.median(sample["rtts"]) - base,
                "received": len(sample["rtts"]), "lost": sample["lost"],
                "leaf": leaf_delta(before, after, 0),
                "goodput": report["end"]["sum_received"]["bits_per_second"],
                "rows": directions(state, ingress=TARGET_WAN_IF, proto=6,
                                   dst=f"{r.lan_ip}:{PORT_BULK}")}

    phases = {}
    for name, (low, high) in WRED_BANDS.items():
        # tc wants the averaging burst at least min/avpkt; the hardware curve
        # works on the instantaneous count and ignores it.
        burst = (2 * low + high) // (3 * 1500) + 1
        await r.tc("qdisc", "add", "dev", TARGET_LAN_IF, "parent", "1:10", "handle", "10:",
                   "red", "limit", str(WRED_LIMIT), "min", str(low), "max", str(high),
                   "avpkt", "1500", "burst", str(burst), "probability", WRED_PROBABILITY,
                   "bandwidth", f"{WRED_MBIT}mbit")
        phases[name] = await loaded()
        await r.tc("qdisc", "del", "dev", TARGET_LAN_IF, "parent", "1:10", "handle", "10:")
    phases["tail"] = await loaded()
    r.record("qos-wred", {"base": base, "idle": idle, "phases": phases})

    def delay(size):
        return size * 8 / rate

    for name, phase in phases.items():
        assert phase["leaf"]["rejected"] > 0, (name, phase["leaf"])
        # The probe waits for each reply, so a deeper queue sends fewer in the
        # window, and the curve drops some of them as it should: a floor for
        # the median to rest on, not a count of what was sent.
        assert phase["received"] >= 50, (name, phase["received"], phase["lost"])
        assert phase["goodput"] >= 0.8 * rate * TCP_PAYLOAD / (TCP_FRAME + OAL), (
            name, phase["goodput"])
        assert len(phase["rows"]) >= 4, (name, phase["rows"])
        assert all(int(f["qos"], 16) == HIGH_CQ for f in phase["rows"]), (name, phase["rows"])
    narrow, wide, tail = phases["narrow"], phases["wide"], phases["tail"]
    assert tail["added"] >= 0.4 * delay(TAIL_FRAMES * (TCP_FRAME + OAL)), (
        "the probe does not wait behind the class queue, so nothing below says "
        "anything about it", tail)
    assert narrow["added"] <= 1.3 * delay(WRED_BANDS["narrow"][1]) + PROBE_SLACK, narrow
    assert wide["added"] <= 1.3 * delay(WRED_BANDS["wide"][1]) + PROBE_SLACK, wide
    assert wide["added"] >= 0.5 * delay(WRED_BANDS["wide"][0]), wide
    assert narrow["added"] < wide["added"] < tail["added"], phases


async def test_red_reports_what_the_hardware_holds(qos):
    """A RED qdisc on a leaf reads `offloaded` exactly while its class queue
    runs the curve, and a refusal says why in the kernel log.

    sch_red keeps whatever `tc` asked for in software whether or not the driver
    took it, so the flag and the log are the only places a refusal shows. A
    change to a setting the hardware cannot run -- ECN, since it drops and
    cannot mark -- has to take the old curve off too, or the queue would run a
    curve the qdisc no longer shows; the flag clearing is how that reads. The
    curve follows the class, so moving the class to another priority keeps it
    offloaded on the queue it moves to. It is the qdisc's, though: a RED
    replacing another on the class is created before the old one is destroyed,
    and the old one's destroy leaves the new curve running. And a RED grafted
    one level further down, under a qdisc that sits on a leaf, names a minor
    the tree also uses for a leaf; it is not offloaded rather than programming
    that leaf's queue.
    """
    r = qos
    dev = TARGET_LAN_IF
    rate = f"{WRED_MBIT}mbit"
    low, high = WRED_BANDS["narrow"]
    curve = ["limit", str(WRED_LIMIT), "min", str(low), "max", str(high), "avpkt", "1500",
             "burst", str((2 * low + high) // (3 * 1500) + 1),
             "probability", WRED_PROBABILITY, "bandwidth", rate]
    # 1:2 is a leaf whose minor a qdisc under another leaf can also name.
    await tree(r, dev, WRED_MBIT, [("1:10", HIGH_PRIO), ("1:2", LOW_PRIO)])

    async def red(verb, parent, handle, *extra):
        await r.tc("qdisc", verb, "dev", dev, "parent", parent, "handle", handle, "red",
                   *curve, *extra)

    ecn_refusals = await logged(r, "RED qdisc 10: not offloaded")
    await red("add", "1:10", "10:")
    added = await qdisc_shown(r, dev, "10:")
    await red("change", "1:10", "10:", "ecn")
    ecn = await qdisc_shown(r, dev, "10:")
    ecn_logged = await logged(r, "RED qdisc 10: not offloaded")
    await red("change", "1:10", "10:")
    restored = await qdisc_shown(r, dev, "10:")
    await r.tc("class", "change", "dev", dev, "parent", "1:1", "classid", "1:10",
               "htb", "rate", rate, "ceil", rate, "prio", "3")
    moved = await qdisc_shown(r, dev, "10:")
    # A RED qdisc replaced by another on the same class: the new one is
    # created, and its curve programmed, before the old one's destroy names
    # the same class. The curve is the new qdisc's and survives it.
    await red("replace", "1:10", "40:")
    swapped = await qdisc_shown(r, dev, "40:")
    foreign_refusals = await logged(r, "RED qdisc 30: not offloaded")
    await r.tc("qdisc", "add", "dev", dev, "parent", "1:2", "handle", "20:", "prio")
    await red("add", "20:2", "30:")
    foreign = await qdisc_shown(r, dev, "30:")
    foreign_logged = await logged(r, "RED qdisc 30: not offloaded")
    r.record("qos-red-offload-state", {"added": added, "ecn": ecn, "restored": restored,
                                       "moved": moved, "swapped": swapped, "foreign": foreign,
                                       "logged": [ecn_refusals, ecn_logged,
                                                  foreign_refusals, foreign_logged]})

    assert added.get("offloaded") is True, added
    assert not ecn.get("offloaded") and ecn_logged == ecn_refusals + 1, (
        ecn, ecn_refusals, ecn_logged)
    assert restored.get("offloaded") is True, restored
    assert moved.get("offloaded") is True, moved
    assert swapped.get("offloaded") is True, swapped
    assert not foreign.get("offloaded") and foreign_logged == foreign_refusals + 1, (
        foreign, foreign_refusals, foreign_logged)


# ---- DSCP -----------------------------------------------------------------

async def test_dscp_map_classifies_unmarked_frames(qos):
    """A frame whose mark names no class is queued by its DSCP, through the
    egress map, in hardware and in software alike.

    The map is the per-port table a `flower ip_tos ... skbedit priority` filter
    programs, and it answers only for frames that named no class of their own.
    Both paths once failed to reach it. The hardware enable tested the entry's
    whole mark word, which is never zero because every offloaded flow carries a
    valid ingress-policer field, so no offloaded flow could use the map; and the
    software path asked a function that answers a default queue for every
    unmarked frame. Either regression sends EF to the default class queue, and
    the leaf counts below read zero.

    The counts are exact because the queue is one nothing else reaches: EF goes
    to the prio 1 leaf, since the DUT's own unmarked frames leave on class queue
    7, which the prio 0 leaf holds. A best-effort flow on the same port is the
    control, and moves neither leaf.
    """
    r = qos
    dev = TARGET_LAN_IF
    await tree(r, dev, CAP_MBIT, [("1:10", HIGH_PRIO), ("1:11", LOW_PRIO)])
    await r.tc("qdisc", "add", "dev", dev, "clsact")
    await r.tc("filter", "add", "dev", dev, "egress", "protocol", "ip", "pref", "1",
               "flower", "skip_sw", "ip_tos", f"{EF_TOS:#x}/0xfc",
               "action", "skbedit", "priority", "1:11")
    shown = (await r.tc("filter", "show", "dev", dev, "egress"))["stdout"]
    assert "in_hw" in shown and "skip_sw" in shown, shown
    # EF_SOFTWARE is deliberately not offered: it is the software half.
    await offload(r, f"ip saddr {WAN_IP} ip daddr {r.lan_ip} "
                     f"udp dport {{ {PORT_EF}, {PORT_BE} }} flow add @fast")
    await lan_start(r, echo=[PORT_EF, PORT_BE, PORT_EF_SOFTWARE])
    for port, tos in ((PORT_EF, EF_TOS), (PORT_BE, 0)):
        forward, reverse = await admit(r, port, tos=tos)
        assert int(forward["qos"], 16) == int(reverse["qos"], 16) == 0, (forward, reverse)

    async def burst(port, tos):
        target = f"{r.lan_ip}:{port}"
        state = await r.state()
        before = await egress(r, dev)
        echoed = await asyncio.to_thread(lockstep, r.lan_ip, port, COUNT, tos=tos)
        after = await egress(r, dev)
        final = await r.state()
        return {"echoed": echoed,
                "rows": (directions(state, ingress=TARGET_WAN_IF, proto=17, dst=target),
                         directions(final, ingress=TARGET_WAN_IF, proto=17, dst=target)),
                "mapped": leaf_delta(before, after, 1), "other": leaf_delta(before, after, 0),
                "software_tx": after["software_tx"] - before["software_tx"]}

    ef = await burst(PORT_EF, EF_TOS)
    best_effort = await burst(PORT_BE, 0)
    software = await burst(PORT_EF_SOFTWARE, EF_TOS)
    r.record("qos-dscp-map", {"filter": shown, "ef": ef, "best_effort": best_effort,
                              "software": software})

    frame = 256 + UDP_HEADERS
    for name, result in (("ef", ef), ("best_effort", best_effort)):
        assert result["echoed"] == COUNT, (name, result)
        assert [len(rows) for rows in result["rows"]] == [1, 1], (name, result["rows"])
        (old,), (new,) = result["rows"]
        assert old["cookie"] == new["cookie"], (name, old, new)
        assert int(new["packets"]) - int(old["packets"]) == COUNT, (name, old, new)
        assert result["software_tx"] <= COUNT // 4, (name, result)
    # Offloaded and unmarked, and every frame on the class the codepoint names.
    assert ef["mapped"] == {"frames": COUNT, "bytes": COUNT * frame, "rejected": 0}, ef
    assert best_effort["mapped"]["frames"] == 0, best_effort
    assert best_effort["other"]["frames"] < COUNT // 4, best_effort
    # The same codepoint through the CPU lands on the same class.
    assert software["echoed"] == COUNT, software
    assert software["rows"] == ([], []), software
    assert software["software_tx"] >= COUNT, software
    assert software["mapped"] == {"frames": COUNT, "bytes": COUNT * frame, "rejected": 0}, software


async def test_dscp_filter_retires_flows_installed_before_it(qos):
    """A flow offloaded before a port had a DSCP filter is retired when the
    first filter turns the port's map on, and comes back reading it.

    Whether a classifier entry consults the map is fixed when the entry is
    built: one built while the port had no map never reads one, however long
    it lives. So a first filter is an egress change like an HTB command's, and
    every entry leaving by the port is retired and readmitted. Before the
    filter, EF on the offloaded flow reaches neither leaf; after it, on a fresh
    entry, every EF frame lands on the class the filter names -- exactly, since
    the DUT's own frames and every unmarked flow leave elsewhere.
    """
    r = qos
    dev = TARGET_LAN_IF
    target = f"{r.lan_ip}:{PORT_EF_BEFORE}"
    frame = 256 + UDP_HEADERS
    await tree(r, dev, CAP_MBIT, [("1:10", HIGH_PRIO), ("1:11", LOW_PRIO)])
    await r.tc("qdisc", "add", "dev", dev, "clsact")
    await offload(r, f"ip saddr {WAN_IP} ip daddr {r.lan_ip} udp dport {PORT_EF_BEFORE} "
                     f"flow add @fast")
    await lan_start(r, echo=[PORT_EF_BEFORE])
    forward, _ = await admit(r, PORT_EF_BEFORE, tos=EF_TOS)
    first = await egress(r, dev)
    unmapped = await asyncio.to_thread(lockstep, r.lan_ip, PORT_EF_BEFORE, COUNT, tos=EF_TOS)
    second = await egress(r, dev)
    before = await r.state()
    await ef_filter(r, dev)
    fresh = await readmitted(r, PORT_EF_BEFORE, forward["cookie"], tos=EF_TOS)
    changed = await r.state()
    third = await egress(r, dev)
    mapped = await asyncio.to_thread(lockstep, r.lan_ip, PORT_EF_BEFORE, COUNT, tos=EF_TOS)
    fourth = await egress(r, dev)
    final = await r.state()
    rows = directions(final, ingress=TARGET_WAN_IF, proto=17, dst=target)
    r.record("qos-dscp-retire-on", {"forward": forward, "fresh": fresh, "rows": rows,
                                    "before": before, "changed": changed,
                                    "unmapped_leaf": leaf_delta(first, second, 1),
                                    "mapped_leaf": leaf_delta(third, fourth, 1),
                                    "unmapped": unmapped, "mapped": mapped})

    assert unmapped == mapped == COUNT, (unmapped, mapped)
    assert int(forward["qos"], 16) == 0, forward
    # Built without the map, the entry never read it. The prio 0 leaf holds
    # the queue the DUT's own frames leave on, so it is allowed their trickle.
    assert leaf_delta(first, second, 1)["frames"] == 0, leaf_delta(first, second, 1)
    assert leaf_delta(first, second, 0)["frames"] < COUNT // 4, leaf_delta(first, second, 0)
    assert changed["qos_invalidations"] > before["qos_invalidations"], (before, changed)
    assert not [f for f in changed["flows"] if f["cookie"] == forward["cookie"]], changed
    # The fresh entry reads it, and every EF frame lands on the class named.
    assert len(rows) == 1 and rows[0]["cookie"] == fresh["cookie"], (fresh, rows)
    assert int(rows[0]["packets"]) - int(fresh["packets"]) == COUNT, (fresh, rows)
    assert leaf_delta(third, fourth, 1) == {"frames": COUNT, "bytes": COUNT * frame,
                                            "rejected": 0}, leaf_delta(third, fourth, 1)


async def test_dscp_filter_replace_moves_the_codepoint(qos):
    """`tc filter replace` of a DSCP filter moves its codepoint to the new
    class, with the map staying on and the flow on its entry.

    Flower offloads the replacement under a new cookie before it destroys the
    filter it replaces, so the replacement arrives while the old filter still
    holds the codepoint. Taken for a second filter on that DSCP, it was
    refused -- with skip_sw the replace itself failed -- and without skip_sw
    the old filter's destroy then unmapped the codepoint and, as the port's
    last, turned the whole map off. Editing a codepoint while the map stays on
    retires nothing, since an entry reads the table per frame. The leaf counts
    are exact: EF lands on the old class before the replace and on the new one
    after it, on leaves nothing else reaches.
    """
    r = qos
    dev = TARGET_LAN_IF
    target = f"{r.lan_ip}:{PORT_EF_REPLACED}"
    frame = 256 + UDP_HEADERS
    await tree(r, dev, CAP_MBIT, [("1:10", HIGH_PRIO), ("1:11", LOW_PRIO),
                                  ("1:12", LOW_PRIO + 1)])
    await r.tc("qdisc", "add", "dev", dev, "clsact")

    async def ef_to(verb, classid):
        await r.tc("filter", verb, "dev", dev, "egress", "protocol", "ip", "pref", "1",
                   "handle", "1", "flower", "skip_sw", "ip_tos", f"{EF_TOS:#x}/0xfc",
                   "action", "skbedit", "priority", classid)
        return (await r.tc("filter", "show", "dev", dev, "egress"))["stdout"]

    added = await ef_to("add", "1:11")
    await offload(r, f"ip saddr {WAN_IP} ip daddr {r.lan_ip} udp dport {PORT_EF_REPLACED} "
                     f"flow add @fast")
    await lan_start(r, echo=[PORT_EF_REPLACED])
    forward, _ = await admit(r, PORT_EF_REPLACED, tos=EF_TOS)

    async def burst():
        before = await egress(r, dev)
        echoed = await asyncio.to_thread(lockstep, r.lan_ip, PORT_EF_REPLACED, COUNT,
                                         tos=EF_TOS)
        after = await egress(r, dev)
        return {"echoed": echoed, "old": leaf_delta(before, after, 1),
                "new": leaf_delta(before, after, 2)}

    first = await burst()
    state = await r.state()
    replaced = await ef_to("replace", "1:12")
    second = await burst()
    final = await r.state()
    rows = directions(final, ingress=TARGET_WAN_IF, proto=17, dst=target)
    r.record("qos-dscp-filter-replace", {"added": added, "replaced": replaced,
                                         "forward": forward, "rows": rows,
                                         "first": first, "second": second,
                                         "invalidations": [state["qos_invalidations"],
                                                           final["qos_invalidations"]]})

    exact = {"frames": COUNT, "bytes": COUNT * frame, "rejected": 0}
    none = {"frames": 0, "bytes": 0, "rejected": 0}
    assert "in_hw" in added and "1:11" in added, added
    # One filter, in hardware, naming the new class. The word alone: tc
    # prints `in_hw in_hw_count 1` for one filter.
    assert len(re.findall(r"\bin_hw\b", replaced)) == 1 and "1:12" in replaced, replaced
    assert first["echoed"] == second["echoed"] == COUNT, (first, second)
    assert first["old"] == exact and first["new"] == none, first
    assert second["new"] == exact and second["old"] == none, second
    # The map never went off, so nothing was retired: the same entry carried
    # both bursts.
    assert final["qos_invalidations"] == state["qos_invalidations"], (state, final)
    assert len(rows) == 1 and rows[0]["cookie"] == forward["cookie"], (forward, rows)


async def test_dscp_map_moves_ports_without_misrouting(qos):
    """The DSCP map moved from the LAN port to the WAN port leaves nothing on
    the LAN port reading it.

    The microcode's map is one table with no port in it, and an entry built
    while the LAN port held it carries the DSCP bit for good. Were such an
    entry still in the classifier when the WAN port took the map, its EF
    frames would read the WAN port's queues and leave by the wrong wire. So
    deleting the LAN's last filter retires every entry on the LAN port and
    waits for them to leave before the map is free, and the WAN's filter is
    accepted only after that. Across the move the LAN VM keeps receiving every
    EF frame, and the WAN port's EF class counts none of them.
    """
    r = qos
    lan, wan = TARGET_LAN_IF, TARGET_WAN_IF
    target = f"{r.lan_ip}:{PORT_EF_MOVED}"
    frame = 256 + UDP_HEADERS
    for dev in (lan, wan):
        await tree(r, dev, CAP_MBIT, [("1:10", HIGH_PRIO), ("1:11", LOW_PRIO)])
        await r.tc("qdisc", "add", "dev", dev, "clsact")
    await ef_filter(r, lan)
    await offload(r, f"ip saddr {WAN_IP} ip daddr {r.lan_ip} udp dport {PORT_EF_MOVED} "
                     f"flow add @fast")
    await lan_start(r, echo=[PORT_EF_MOVED])
    forward, _ = await admit(r, PORT_EF_MOVED, tos=EF_TOS)
    first = await egress(r, lan)
    held = await asyncio.to_thread(lockstep, r.lan_ip, PORT_EF_MOVED, COUNT, tos=EF_TOS)
    second = await egress(r, lan)
    await ef_filter(r, lan, "del")
    await ef_filter(r, wan)
    moved = await r.state()
    lan_before, wan_before = await egress(r, lan), await egress(r, wan)
    received = await asyncio.to_thread(lockstep, r.lan_ip, PORT_EF_MOVED, COUNT, tos=EF_TOS)
    lan_after, wan_after = await egress(r, lan), await egress(r, wan)
    final = await r.state()
    r.record("qos-dscp-map-move", {"forward": forward, "moved": moved, "final": final,
                                   "held_leaf": leaf_delta(first, second, 1),
                                   "lan_leaf": leaf_delta(lan_before, lan_after, 1),
                                   "wan_leaf": leaf_delta(wan_before, wan_after, 1),
                                   "held": held, "received": received})

    # The entry built under the LAN's map read it.
    assert held == COUNT, held
    assert leaf_delta(first, second, 1) == {"frames": COUNT, "bytes": COUNT * frame,
                                            "rejected": 0}, leaf_delta(first, second, 1)
    # Gone before the WAN port could take the map.
    assert not [f for f in moved["flows"] if f["cookie"] == forward["cookie"]], moved
    # And nothing of the LAN's leaves by the WAN port afterwards.
    assert received == COUNT, received
    assert leaf_delta(wan_before, wan_after, 1)["frames"] == 0, leaf_delta(wan_before, wan_after, 1)
    assert leaf_delta(lan_before, lan_after, 1)["frames"] == 0, leaf_delta(lan_before, lan_after, 1)
    rows = directions(final, ingress=TARGET_WAN_IF, proto=17, dst=target)
    assert all(f["cookie"] != forward["cookie"] for f in rows), rows


async def test_dscp_remark_rewrites_the_wire(qos):
    """A mark that carries a remark makes the hardware rewrite the DSCP on the
    wire, and nothing else rewrites it.

    The WAN host captures what arrives. First, before any class exists, the
    rig's own sender through plain software forwarding: every request at DSCP
    0, which is what the sender puts there itself. Then the same sender with
    the flow in hardware under a mark naming EF: every request at EF, with an
    IP checksum that still verifies, one TTL lower, and each one counted by the
    classifier while the CPU transmitted next to nothing.

    The remark lives in bits the image's mask does not cover, so the adapter
    is reloaded with a wider one for the case and with the image's own options
    afterwards; the two boot-immutable parameters have to read back as they
    did before.
    """
    from scapy.all import IP

    r = qos
    parameters = "/sys/module/ask_flowtable/parameters/"
    original = {name: (await read(r.target, r.session, parameters + name)).strip()
                for name in ("qos_mark_mask", "qos_default_class")}
    plain = await captured(r, lambda: r.exchange(16))
    assert len(plain) == 16 and {p[IP].tos for p in plain} == {0}, [p.summary() for p in plain]
    await r.clear_ct()
    await reload_adapter(r, f"qos_mark_mask={REMARK_MASK:#x}")
    try:
        assert int((await read(r.target, r.session, parameters + "qos_mark_mask")).strip()) \
            == REMARK_MASK
        shift = (REMARK_MASK & -REMARK_MASK).bit_length() - 1
        await r.table(mark=REMARK_CLASS << shift)
        await r.exchange(64)
        installed = await r.wait(lambda s: s["entries"] == 2)
        before = await egress(r, TARGET_WAN_IF)
        packets = await captured(r, lambda: r.exchange(COUNT))
        after = await egress(r, TARGET_WAN_IF)
        final = await r.state()
        r.record("qos-dscp-remark", {"installed": installed, "final": final,
                                     "tos": [p[IP].tos for p in packets],
                                     "software_tx": after["software_tx"] - before["software_tx"]})

        assert all(int(f["qos"], 16) == REMARK_CLASS for f in installed["flows"]), installed
        counted = {f["cookie"]: int(f["packets"]) for f in final["flows"]}
        assert set(counted) == {f["cookie"] for f in installed["flows"]}, (installed, final)
        for flow in installed["flows"]:
            assert counted[flow["cookie"]] - int(flow["packets"]) == COUNT, (flow, final)
        assert after["software_tx"] - before["software_tx"] <= 32, (before, after)
        assert len(packets) == COUNT, len(packets)
        for p in packets:
            assert p[IP].tos == EF_TOS and p[IP].ttl == 63, p.summary()
            saved = p[IP].chksum
            copy = p[IP].copy()
            del copy.chksum
            assert IP(bytes(copy)).chksum == saved, p.summary()
    finally:
        try:
            await r.delete_table()
            await r.clear_ct()
        finally:
            await reload_adapter(r, idle=False)
    restored = {name: (await read(r.target, r.session, parameters + name)).strip()
                for name in original}
    assert restored == original, (original, restored)


async def test_saturated_leaf_starves_no_control_traffic(qos):
    """A leaf held saturated by an offloaded flow leaves the gateway's own
    traffic untouched.

    The tree is on the WAN port: one channel at the cap, rate equal to ceil,
    and one prio 1 leaf that four offloaded TCP streams from the LAN VM keep
    backlogged. A channel whose rate is its ceil has no excess rate, and its
    shaper is coupled, so a queue eligible for excess tokens only transmits
    when the classes leave committed ones unused -- which a backlogged leaf
    never does. The gateway's own frames once went to exactly such a queue,
    and starved. They now take the top channel's control queue, eligible for
    committed tokens and above every leaf in priority, within a budget of a
    sixteenth of the channel: the DUT pings this host with no loss worth the
    name, and fresh TCP handshakes with a temporary DUT listener, whose answers leave by
    the shaped port, all complete.
    """
    r = qos
    dev = TARGET_WAN_IF
    target = f"{WAN_IP}:{PORT_SATURATE}"
    addresses = json.loads((await command(r.target, r.session, "ip", "-j", "-4",
                                          "addr", "show", "dev", dev))["stdout"])
    address = next(a["local"] for a in addresses[0]["addr_info"] if a["family"] == "inet")
    await tree(r, dev, CAP_MBIT, [("1:10", LOW_PRIO)])
    await offload(r, f"ip saddr {r.lan_ip} ip daddr {WAN_IP} tcp dport {PORT_SATURATE} "
                     f"ct mark set {r.mark(LOW_CQ):#x} flow add @fast")
    route = f"{WAN_IP}/32"
    client = f'''
import json, subprocess
route = {route!r}
# A run killed before its own cleanup leaves this route behind, and that one
# is this case's to remove. Anything else holding the prefix is not.
for entry in json.loads(subprocess.check_output(['ip', '-j', 'route', 'show', 'exact', route],
                                               text=True)):
    assert (entry.get('gateway'), entry.get('dev')) == ({r.lan_gateway!r}, {LAN_NIC!r}), entry
    subprocess.run(['ip', 'route', 'del', route, 'dev', {LAN_NIC!r}], check=True)
subprocess.run(['ip', 'route', 'add', route, 'via', {r.lan_gateway!r}, 'dev', {LAN_NIC!r},
                'mtu', '1500'], check=True)
try:
    result = subprocess.run(['iperf3', '-c', {WAN_IP!r}, '-B', {r.lan_ip!r},
                             '-p', {str(PORT_SATURATE)!r}, '-P', '4', '-t', '20'],
                            capture_output=True, text=True, timeout=60)
finally:
    subprocess.run(['ip', 'route', 'del', route, 'dev', {LAN_NIC!r}])
print(json.dumps({{'rc': result.returncode, 'stderr': result.stderr[-400:]}}))
'''
    server = await asyncio.create_subprocess_exec(
        "iperf3", "-s", "-1", "-B", WAN_IP, "-p", str(PORT_SATURATE),
        stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE)
    transfer = probe = None
    try:
        probe = await r.target.request(r.session, "probe/start", {"address": address})
        await asyncio.sleep(0.3)
        assert server.returncode is None, "the endpoint iperf3 did not start"
        transfer = asyncio.create_task(lan_run_python(r.lan, client, label="flowtable_qos_saturate",
                                                      timeout=90))
        deadline = time.monotonic() + 10
        while True:
            state = await r.state()
            bulk = [f for f in directions(state, ingress=TARGET_LAN_IF, proto=6, dst=target)
                    if int(f["bytes"]) > 1_000_000]
            if len(bulk) == 4:
                break
            assert not transfer.done() and time.monotonic() < deadline, state
            await asyncio.sleep(0.25)
        await asyncio.sleep(SETTLE)
        first = await egress(r, dev)
        pinged = await dut_ping(r, WAN_IP, 200)
        connected = await handshakes(address, probe["port"], 20)
        second = await egress(r, dev)
        during = await r.state()
    finally:
        try:
            sender = await transfer if transfer else None
        finally:
            if server.returncode is None:
                server.terminate()
            await asyncio.wait_for(server.wait(), 10)
            if probe:
                await r.target.request(r.session, "probe/stop", {"port": probe["port"]})
    saturated = leaf_delta(first, second, 0)
    rows = directions(during, ingress=TARGET_LAN_IF, proto=6, dst=target)
    r.record("qos-unclassified-saturated", {"leaf": saturated, "pinged": pinged,
                                            "connected": connected, "rows": rows,
                                            "sender": sender.stdout if sender else None})

    # The leaf really was held full by the offloaded flow, in hardware: its
    # four streams, not iperf's control connection on the same port, which
    # carries a few hundred bytes and may be offloaded beside them.
    streams = [f for f in rows if int(f["bytes"]) > 1_000_000]
    assert len(streams) == 4 and all(int(f["qos"], 16) == LOW_CQ for f in streams), rows
    assert saturated["rejected"] > 0, saturated
    slack = timing_slack(first, second)
    assert shaped_bps(first, second, 0) >= (0.9 - slack) * CAP_MBIT * 1e6, saturated
    # And the gateway's own traffic got through beside it.
    sent, received = pinged
    assert sent == 200 and received >= 198, pinged
    assert connected == 20, connected


async def test_unclassified_flow_keeps_its_queue_when_offloaded(qos):
    """An unmarked flow's frames land on the same queue in software and in
    hardware, and the gateway's own frames on the control queue.

    With no `default`, a forwarded frame that names no class goes where the
    hardware has always put its flow: the top channel's class queue 0. A prio 7
    leaf holds that queue, so its counters see both halves of an unmarked flow
    -- the frames the CPU forwards before admission, and the frames the
    classifier forwards after -- exactly. The software half once went to class
    queue 7 instead. The DUT's own pings take class queue 7, which the prio 0
    leaf holds, and none of them reach queue 0.
    """
    r = qos
    dev = TARGET_LAN_IF
    frame = 256 + UDP_HEADERS
    target = f"{r.lan_ip}:{PORT_UNCLASSIFIED_HW}"
    await tree(r, dev, CAP_MBIT, [("1:10", HIGH_PRIO), ("1:17", 7)])
    # Only the second port is offered to the flowtable: the first stays in
    # software for good.
    await offload(r, f"ip saddr {WAN_IP} ip daddr {r.lan_ip} udp dport {PORT_UNCLASSIFIED_HW} "
                     f"flow add @fast")
    await lan_start(r, echo=[PORT_UNCLASSIFIED_SW, PORT_UNCLASSIFIED_HW])
    forward, _ = await admit(r, PORT_UNCLASSIFIED_HW)

    async def burst(port):
        before = await egress(r, dev)
        echoed = await asyncio.to_thread(lockstep, r.lan_ip, port, COUNT)
        after = await egress(r, dev)
        return {"echoed": echoed, "unclassified": leaf_delta(before, after, 1),
                "control": leaf_delta(before, after, 0),
                "software_tx": after["software_tx"] - before["software_tx"]}

    software = await burst(PORT_UNCLASSIFIED_SW)
    hardware = await burst(PORT_UNCLASSIFIED_HW)
    before = await egress(r, dev)
    pinged = await dut_ping(r, r.lan_ip, COUNT)
    after = await egress(r, dev)
    final = await r.state()
    rows = directions(final, ingress=TARGET_WAN_IF, proto=17, dst=target)
    r.record("qos-unclassified-queue", {"software": software, "hardware": hardware,
                                        "pinged": pinged, "rows": rows, "forward": forward,
                                        "ping_control": leaf_delta(before, after, 0),
                                        "ping_unclassified": leaf_delta(before, after, 1)})

    exact = {"frames": COUNT, "bytes": COUNT * frame, "rejected": 0}
    assert software["echoed"] == hardware["echoed"] == COUNT, (software, hardware)
    # Forwarded by the CPU: every frame on the unclassified queue.
    assert software["software_tx"] >= COUNT, software
    assert software["unclassified"] == exact, software
    # Forwarded by the classifier, unmarked: the same queue, frame for frame.
    assert int(forward["qos"], 16) == 0 and len(rows) == 1, (forward, rows)
    assert rows[0]["cookie"] == forward["cookie"], (forward, rows)
    assert hardware["unclassified"] == exact, hardware
    assert hardware["software_tx"] <= COUNT // 4, hardware
    # The gateway's own: the control queue, never the unclassified one.
    assert pinged[0] == pinged[1] == COUNT, pinged
    assert leaf_delta(before, after, 0)["frames"] >= COUNT, leaf_delta(before, after, 0)
    assert leaf_delta(before, after, 1)["frames"] == 0, leaf_delta(before, after, 1)


async def test_declined_flow_keeps_its_class_in_software(qos):
    """A flow the hardware declines is forwarded by the software flowtable, and
    every frame of it still lands on the class its mark names.

    The software flowtable forwards a frame past the stack, and it used to do
    so without the frame's conntrack: the queue selection found no connection,
    so no mark and no class, and every frame of a declined flow went to class
    queue 7 -- the top of the tree, above every class the operator configured,
    and the queue the prio 0 leaf holds here. The flowtable now hands the frame
    its flow's conntrack as act_ct does (patch 147). The flow is declined by a
    mark bit outside the classification mask, which the adapter cannot honour
    and the class decode ignores. The offering rule's counter proves the
    measured frames never reached the forward chain, so the flowtable carried
    them; the leaf counts are exact because nothing else reaches the class
    queue the mark names.
    """
    r = qos
    dev = TARGET_LAN_IF
    frame = 256 + UDP_HEADERS
    target = f"{r.lan_ip}:{PORT_DECLINED}"
    await tree(r, dev, CAP_MBIT, [("1:10", HIGH_PRIO), ("1:11", LOW_PRIO)])
    before = await r.state()
    # The lowest bit the running mask leaves out, as the ARP fallback case
    # takes it: the flow is refused whatever the boot configured.
    outside = ~int(before["qos_mark_mask"]) & 0xffffffff
    mark = r.mark(LOW_CQ) | (outside & -outside)
    await offload(r, f"ip saddr {WAN_IP} ip daddr {r.lan_ip} udp dport {PORT_DECLINED} "
                     f"counter ct mark set {mark:#x} flow add @fast")
    await lan_start(r, echo=[PORT_DECLINED])
    deadline = time.monotonic() + 20
    while True:
        await asyncio.to_thread(lockstep, r.lan_ip, PORT_DECLINED, 8)
        declined = await r.state()
        if declined["rejects"] > before["rejects"]:
            break
        assert time.monotonic() < deadline, "the marked flow was never offered"
        await asyncio.sleep(0.5)
    # Past admission, so every frame from here on is the flowtable's.
    await asyncio.to_thread(lockstep, r.lan_ip, PORT_DECLINED, 8)
    counted = await offered(r)
    first = await egress(r, dev)
    echoed = await asyncio.to_thread(lockstep, r.lan_ip, PORT_DECLINED, COUNT)
    second = await egress(r, dev)
    recounted = await offered(r)
    final = await r.state()
    rows = directions(final, ingress=TARGET_WAN_IF, proto=17, dst=target)
    r.record("qos-declined-software-class", {
        "mark": mark, "declined": declined, "final": final, "rows": rows,
        "offered": [counted, recounted], "echoed": echoed,
        "class_leaf": leaf_delta(first, second, 1),
        "control_leaf": leaf_delta(first, second, 0),
        "software_tx": second["software_tx"] - first["software_tx"]})

    assert echoed == COUNT, echoed
    # Declined, so nothing of it is in hardware and the CPU forwarded it all;
    # never offered to the forward chain again, so the flowtable did.
    assert not rows, rows
    assert second["software_tx"] - first["software_tx"] >= COUNT, (first, second)
    assert recounted == counted, (counted, recounted)
    # Every frame on the class the mark names, and none above the tree.
    assert leaf_delta(first, second, 1) == {"frames": COUNT, "bytes": COUNT * frame,
                                            "rejected": 0}, leaf_delta(first, second, 1)
    assert leaf_delta(first, second, 0)["frames"] < COUNT // 4, leaf_delta(first, second, 0)


async def test_default_class_takes_unclassified_traffic(qos):
    """`default` names the leaf everything unclassified takes, in both paths,
    and control traffic is not unclassified.

    A tree built with `default 20`: an unmarked flow offloaded at three times
    the cap is shaped on leaf 1:20 -- its classifier entry resolves the missing
    class to that leaf -- and every unclassified frame the CPU forwards on the
    port meanwhile, the iperf3 control connection's handshake included, lands
    there too. The prio 1 leaf beside it sees none of it. The DUT's own pings
    do not follow: they are control traffic, which takes the top channel's
    control queue rather than the default -- a default is commonly the lowest
    class, where a saturated class above it would starve them. They used to
    land on the default leaf, as software HTB would put them.
    """
    r = qos
    dev = TARGET_LAN_IF
    target = f"{r.lan_ip}:{PORT_DEFAULT}"
    rate = f"{CAP_MBIT}mbit"
    await r.tc("qdisc", "add", "dev", dev, "root", "handle", "1:", "htb", "offload",
               "default", "20")
    await r.tc("class", "add", "dev", dev, "parent", "1:", "classid", "1:1",
               "htb", "rate", rate, "ceil", rate)
    for classid, prio in (("1:10", LOW_PRIO), ("1:20", 2)):
        await r.tc("class", "add", "dev", dev, "parent", "1:1", "classid", classid,
                   "htb", "rate", rate, "ceil", rate, "prio", str(prio))
    await offload(r, inbound(r, "udp", PORT_DEFAULT))
    await lan_start(r, iperf=[PORT_DEFAULT])
    before = await egress(r, dev)
    client = asyncio.create_task(iperf(r, PORT_DEFAULT, udp_mbit=OFFERED_MBIT))
    try:
        await asyncio.sleep(SETTLE)
        first = await egress(r, dev)
        installed = await r.state()
        await asyncio.sleep(WINDOW)
        second = await egress(r, dev)
        report = await client
    finally:
        if not client.done():
            client.cancel()
            await asyncio.gather(client, return_exceptions=True)
    await asyncio.sleep(0.5)
    after = await egress(r, dev)
    pinged = await dut_ping(r, r.lan_ip, COUNT)
    pinged_after = await egress(r, dev)
    state = await r.state()
    forward = directions(state, ingress=TARGET_WAN_IF, proto=17, dst=target)
    early = directions(installed, ingress=TARGET_WAN_IF, proto=17, dst=target)
    whole = leaf_delta(before, after, 1)
    r.record("qos-default-class", {"installed": installed, "state": state, "whole": whole,
                                   "window": leaf_delta(first, second, 1),
                                   "other": leaf_delta(before, after, 0),
                                   "pinged": pinged,
                                   "ping_default": leaf_delta(after, pinged_after, 1),
                                   "ping_other": leaf_delta(after, pinged_after, 0),
                                   "ping_control": leaf_delta(after, pinged_after, "control"),
                                   "report": report})

    cap = CAP_MBIT * 1e6
    assert len(forward) == 1 and len(early) == 1, (early, forward)
    row = forward[0]
    assert int(row["qos"], 16) == 0 and early[0]["cookie"] == row["cookie"], (early, row)
    assert report["end"]["sum_sent"]["bits_per_second"] >= 2 * cap, report["end"]["sum_sent"]
    # Shaped on the default leaf, at the cap: in hardware, since nothing else
    # carries this much.
    slack = timing_slack(first, second)
    shaped = shaped_bps(first, second, 1)
    assert (0.95 - slack) * cap <= shaped <= (1.03 + slack) * cap, (shaped, cap, slack)
    # Every frame of the flow the classifier matched, and every unclassified
    # frame the CPU sent on the port -- the control connection's handshake
    # among them -- is on the default leaf, dequeued or rejected. The other
    # leaf saw none.
    extra = whole["frames"] + whole["rejected"] - int(row["packets"])
    assert 0 <= extra <= after["software_tx"] - before["software_tx"], (
        whole, row["packets"], after["software_tx"] - before["software_tx"])
    assert extra > 0, whole
    assert leaf_delta(before, after, 0)["frames"] == 0, leaf_delta(before, after, 0)
    # The gateway's own frames are control, and go to the control queue.
    assert pinged[0] == pinged[1] == COUNT, pinged
    assert leaf_delta(after, pinged_after, "control")["frames"] >= COUNT, (
        leaf_delta(after, pinged_after, "control"))
    # None of them on the default leaf, which the port's other unclassified
    # traffic may still touch; the prio 1 leaf takes only its own mark.
    assert leaf_delta(after, pinged_after, 1)["frames"] < COUNT // 4, (
        leaf_delta(after, pinged_after, 1))
    assert leaf_delta(after, pinged_after, 0)["frames"] == 0, leaf_delta(after, pinged_after, 0)


async def test_dscp_remark_agrees_in_software(qos):
    """A remark class rewrites forwarded frames in software as the hardware
    rewrites them, and the DSCP map reads the same codepoint in both paths.

    Two flows from the LAN VM to this host, both marked with a class that
    remarks to EF and names no queue of its own; one is offered to the
    flowtable and one never is. They leave at AF11. On the WAN port two DSCP
    filters wait: AF11 to 1:12 and EF to 1:11. So the capture says whether the
    remark happened, and the leaf a flow's frames land on says which codepoint
    the map read -- the one the frame arrived with, or the remarked one. The
    two paths have to agree on both; before, the software flow left at AF11.

    The remark bits lie outside the image's mask, so the adapter is reloaded
    with a wider one for the case and put back afterwards.
    """
    from scapy.all import IP, AsyncSniffer

    r = qos
    dev = TARGET_WAN_IF
    af11 = 10 << 2
    parameters = "/sys/module/ask_flowtable/parameters/"
    original = {name: (await read(r.target, r.session, parameters + name)).strip()
                for name in ("qos_mark_mask", "qos_default_class")}
    shift = (REMARK_MASK & -REMARK_MASK).bit_length() - 1
    mark = REMARK_CLASS << shift

    async def send(port, count):
        script = f'''
import json, socket, struct, time
s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
s.setsockopt(socket.IPPROTO_IP, socket.IP_TOS, {af11})
s.bind(({r.lan_ip!r}, {port}))
for n in range({count}):
    s.sendto(struct.pack('!Q', n) + b'ASK-remark'.ljust(120, b'.'), ({WAN_IP!r}, {port}))
    time.sleep(0.002)
print(json.dumps({{'sent': {count}}}))
'''
        result = await lan_run_python(r.lan, script, label="flowtable_qos_remark", timeout=60)
        assert result.rc == 0, result.stdout

    async def measured(port):
        ready = threading.Event()
        sniffer = AsyncSniffer(iface=r.wan_if, store=True, started_callback=ready.set,
                               filter=f"udp and src host {r.lan_ip} and dst port {port}")
        sniffer.start()
        try:
            assert await asyncio.to_thread(ready.wait, 5), "the WAN capture did not start"
            before = await egress(r, dev)
            await send(port, COUNT)
            await asyncio.sleep(0.3)
            after = await egress(r, dev)
        finally:
            packets = [p for p in sniffer.stop() if IP in p]
        return {"packets": packets, "ef": leaf_delta(before, after, 0),
                "af11": leaf_delta(before, after, 1),
                "software_tx": after["software_tx"] - before["software_tx"]}

    await r.clear_ct()
    await reload_adapter(r, f"qos_mark_mask={REMARK_MASK:#x}")
    try:
        await tree(r, dev, CAP_MBIT, [("1:11", LOW_PRIO), ("1:12", 2)])
        await r.tc("qdisc", "add", "dev", dev, "clsact")
        for pref, tos, classid in (("1", EF_TOS, "1:11"), ("2", af11, "1:12")):
            await r.tc("filter", "add", "dev", dev, "egress", "protocol", "ip", "pref", pref,
                       "flower", "skip_sw", "ip_tos", f"{tos:#x}/0xfc",
                       "action", "skbedit", "priority", classid)
        await offload(r, f"ip saddr {r.lan_ip} ip daddr {WAN_IP} udp dport {PORT_REMARK_HW} "
                         f"ct mark set {mark:#x} flow add @fast",
                         f"ip saddr {r.lan_ip} ip daddr {WAN_IP} udp dport {PORT_REMARK_SW} "
                         f"ct mark set {mark:#x}")
        target = f"{WAN_IP}:{PORT_REMARK_HW}"
        deadline = time.monotonic() + 20
        while True:
            await send(PORT_REMARK_HW, 16)
            rows = directions(await r.state(), ingress=TARGET_LAN_IF, proto=17, dst=target)
            if rows:
                break
            assert time.monotonic() < deadline, "the remarked flow was never admitted"
            await asyncio.sleep(0.5)
        installed = rows[0]
        hardware = await measured(PORT_REMARK_HW)
        software = await measured(PORT_REMARK_SW)
        final = await r.state()
    finally:
        try:
            await r.delete_table()
            await r.clear_ct()
        finally:
            await reload_adapter(r, idle=False)
    restored = {name: (await read(r.target, r.session, parameters + name)).strip()
                for name in original}
    rows = directions(final, ingress=TARGET_LAN_IF, proto=17, dst=target)
    unoffered = directions(final, ingress=TARGET_LAN_IF, proto=17,
                           dst=f"{WAN_IP}:{PORT_REMARK_SW}")
    r.record("qos-dscp-remark-software", {
        "installed": installed, "rows": rows, "unoffered": unoffered,
        "hardware": {k: v for k, v in hardware.items() if k != "packets"},
        "software": {k: v for k, v in software.items() if k != "packets"},
        "hardware_tos": sorted({p[IP].tos for p in hardware["packets"]}),
        "software_tos": sorted({p[IP].tos for p in software["packets"]})})

    assert restored == original, (original, restored)
    assert int(installed["qos"], 16) == REMARK_CLASS, installed
    assert len(rows) == 1 and rows[0]["cookie"] == installed["cookie"], (installed, rows)
    assert int(rows[0]["packets"]) - int(installed["packets"]) == COUNT, (installed, rows)
    assert hardware["software_tx"] <= COUNT // 4, hardware
    assert not unoffered and software["software_tx"] >= COUNT, (unoffered, software)
    # Both paths put EF on the wire, the ECN bits left as the sender had them
    # and an IPv4 checksum that still verifies.
    for name, result in (("hardware", hardware), ("software", software)):
        packets = result["packets"]
        assert len(packets) == COUNT, (name, len(packets))
        for p in packets:
            assert p[IP].tos == EF_TOS, (name, p.summary())
            saved = p[IP].chksum
            copy = p[IP].copy()
            del copy.chksum
            assert IP(bytes(copy)).chksum == saved, (name, p.summary())
    # And the map read the same codepoint in both: every frame of each flow on
    # one leaf, the same leaf for both. EF means the map reads the remarked
    # codepoint, AF11 the one the frame arrived with.
    def landed(result):
        ef, af11_leaf = result["ef"]["frames"], result["af11"]["frames"]
        assert sorted((ef, af11_leaf)) == [0, COUNT], result
        return "ef" if ef else "af11"
    assert landed(software) == landed(hardware), (software, hardware)


# ---- ingress policing ------------------------------------------------------

async def test_flower_police_caps_the_flow(qos):
    """A `flower` police filter on the WAN port's ingress, offloaded, holds one
    offloaded flow offered three times its rate.

    The filter is bound to the flow at admission: the adapter matches the
    finished tuple against it and writes the profile it allocated into the
    entry, so the forward row names a policer and the reverse row, which
    arrives on the other port, names none. The filter reports zero the moment it
    exists -- its baseline is seeded from the profile, which an earlier filter
    may have used -- and afterwards the frames it metered are the frames the
    classifier matched for the flow. Which exact reading that is depends on
    whether the microcode counts an entry hit before or after the meter's
    verdict, which its headers do not say, so both exact readings are
    accepted and nothing between them.
    """
    r = qos
    dev = TARGET_WAN_IF
    target = f"{r.lan_ip}:{PORT_POLICED}"
    await r.tc("qdisc", "add", "dev", dev, "clsact")
    await r.tc("filter", "add", "dev", dev, "ingress", "protocol", "ip", "pref", "1",
               "flower", "skip_sw", "ip_proto", "udp", "dst_ip", r.lan_ip,
               "dst_port", str(PORT_POLICED),
               "action", "police", "rate", f"{CAP_MBIT}mbit", "burst", POLICE_BURST,
               "conform-exceed", "drop")
    installed = (await r.tc("-s", "filter", "show", "dev", dev, "ingress"))["stdout"]
    assert "in_hw" in installed and "skip_sw" in installed, installed
    assert police_counters(installed) == (0, 0), installed
    # The filter exists before the flow does, because the binding is made at
    # admission and a flow admitted earlier would never meet it.
    await offload(r, inbound(r, "udp", PORT_POLICED))
    await lan_start(r, iperf=[PORT_POLICED])
    report = await iperf(r, PORT_POLICED, udp_mbit=OFFERED_MBIT)
    state = await r.state()
    shown = (await r.tc("-s", "filter", "show", "dev", dev, "ingress"))["stdout"]
    metered, dropped = police_counters(shown)
    forward = directions(state, ingress=dev, proto=17, dst=target)
    reverse = directions(state, ingress=TARGET_LAN_IF, proto=17, src=target)
    goodput = received_bps(report)
    offered = report["end"]["sum_sent"]["bits_per_second"]
    r.record("qos-flower-police", {"filter": shown, "state": state, "metered": metered,
                                   "dropped": dropped, "goodput_bps": goodput,
                                   "offered_bps": offered, "report": report})

    cap = CAP_MBIT * 1e6
    assert len(forward) == len(reverse) == 1, (forward, reverse)
    profile = int(forward[0]["qos"], 16) >> 8 & 0xf
    assert 1 <= profile <= 7, forward
    assert int(reverse[0]["qos"], 16) >> 8 & 0xf == 0, reverse
    assert offered >= 2 * cap, ("the orchestrator did not offer enough to test a meter", offered)
    hits = int(forward[0]["packets"])
    assert hits in (metered, metered - dropped), (hits, metered, dropped)
    # Three times the rate offered: about two frames in three are red.
    assert dropped >= metered // 2, (metered, dropped)
    # The profile meters the frame the port received, headers and all.
    expected = cap * DATAGRAM / (DATAGRAM + UDP_HEADERS)
    assert 0.9 * expected <= goodput <= 1.03 * expected, (goodput, expected)


# Linux's police passes a frame only when it fits both buckets -- the rate's,
# `burst` deep, and the peak rate's, `mtu` deep -- and never one longer than
# `mtu` (tcf_police_act()). A peak rate therefore bounds bursts, not the rate:
# what crosses is the committed rate. RFC 2698's yellow, above the committed
# rate and within the peak, is excess to Linux, and has to be dropped.
TWO_RATE = {
    # Committed rate half the cap, peak at the cap: the cap must not be what
    # comes through.
    "peak": (CAP_MBIT // 2, CAP_MBIT, 9216, CAP_MBIT // 2),
    # Every frame is longer than the mtu: none fits, whatever the rate.
    "oversize": (CAP_MBIT, None, 1000, 0),
}


@pytest.mark.parametrize("case", list(TWO_RATE))
async def test_flower_police_follows_linux_buckets(qos, case):
    """A police action with a peak rate, or an mtu shorter than the frames,
    delivers in hardware what Linux's police would."""
    r = qos
    dev = TARGET_WAN_IF
    rate, peak, mtu, through = TWO_RATE[case]
    target = f"{r.lan_ip}:{PORT_POLICED}"
    second = ["peakrate", f"{peak}mbit"] if peak else []
    await r.tc("qdisc", "add", "dev", dev, "clsact")
    await r.tc("filter", "add", "dev", dev, "ingress", "protocol", "ip", "pref", "1",
               "flower", "skip_sw", "ip_proto", "udp", "dst_ip", r.lan_ip,
               "dst_port", str(PORT_POLICED),
               "action", "police", "rate", f"{rate}mbit", *second, "burst", POLICE_BURST,
               "mtu", str(mtu), "conform-exceed", "drop")
    installed = (await r.tc("-s", "filter", "show", "dev", dev, "ingress"))["stdout"]
    assert "in_hw" in installed, installed
    await offload(r, inbound(r, "udp", PORT_POLICED))
    await lan_start(r, iperf=[PORT_POLICED])
    report = await iperf(r, PORT_POLICED, udp_mbit=OFFERED_MBIT)
    state = await r.state()
    shown = (await r.tc("-s", "filter", "show", "dev", dev, "ingress"))["stdout"]
    metered, dropped = police_counters(shown)
    forward = directions(state, ingress=dev, proto=17, dst=target)
    goodput = received_bps(report)
    offered = report["end"]["sum_sent"]["bits_per_second"]
    r.record(f"qos-police-{case}", {"filter": shown, "state": state, "metered": metered,
                                     "dropped": dropped, "goodput_bps": goodput,
                                     "offered_bps": offered, "report": report})

    assert offered >= 2 * rate * 1e6, ("the orchestrator did not offer enough to test a meter", offered)
    # skip_sw: Linux carries the first datagrams unmetered, which admits the
    # flow, and from then on the entry names the filter's profile.
    assert len(forward) == 1 and 1 <= int(forward[0]["qos"], 16) >> 8 & 0xf <= 7, forward
    expected = through * 1e6 * DATAGRAM / (DATAGRAM + UDP_HEADERS)
    if through:
        assert 0.9 * expected <= goodput <= 1.03 * expected, (goodput, expected)
    else:
        assert goodput <= 0.001 * offered, (goodput, offered)
    # What did not cross was dropped by the meter, and tc is told so.
    assert dropped and metered and dropped <= metered, (metered, dropped)


# Linux's length check takes the frame as tc ingress sees it -- the IP datagram
# and its Ethernet header, no FCS -- and passes it at exactly `mtu` bytes.
BOUNDARY_MTU = 1000
BOUNDARY_COUNT = 16


async def test_flower_police_mtu_boundary(qos):
    """A frame of exactly the police action's mtu crosses the offloaded meter,
    and one a byte longer never does, as in Linux."""
    r = qos
    dev = TARGET_WAN_IF
    target = f"{r.lan_ip}:{PORT_POLICED}"
    await r.tc("qdisc", "add", "dev", dev, "clsact")
    await r.tc("filter", "add", "dev", dev, "ingress", "protocol", "ip", "pref", "1",
               "flower", "skip_sw", "ip_proto", "udp", "dst_ip", r.lan_ip,
               "dst_port", str(PORT_POLICED),
               "action", "police", "rate", f"{CAP_MBIT}mbit", "burst", POLICE_BURST,
               "mtu", str(BOUNDARY_MTU), "conform-exceed", "drop")
    await offload(r, inbound(r, "udp", PORT_POLICED))
    await lan_start(r, echo=[PORT_POLICED])
    # Small datagrams pass Linux unmetered (skip_sw) and admit the flow; the
    # entry then names the filter's profile.
    deadline = time.monotonic() + 20
    while not directions(await r.state(), ingress=dev, proto=17, dst=target):
        await asyncio.to_thread(lockstep, r.lan_ip, PORT_POLICED, 8)
        assert time.monotonic() < deadline, "the policed flow was never admitted"
    forward = directions(await r.state(), ingress=dev, proto=17, dst=target)
    # Frames from a few bytes under the mtu to one over it, as Linux measures
    # them: Ethernet 14 + IPv4 20 + UDP 8 around the payload.
    crossed = {}
    for length in range(BOUNDARY_MTU - 6, BOUNDARY_MTU + 2):
        crossed[length] = await asyncio.to_thread(
            lockstep, r.lan_ip, PORT_POLICED, BOUNDARY_COUNT,
            payload_size=length - 42, timeout=0.3)
    after = directions(await r.state(), ingress=dev, proto=17, dst=target)
    shown = (await r.tc("-s", "filter", "show", "dev", dev, "ingress"))["stdout"]
    r.record("qos-police-mtu-boundary", {"crossed": crossed, "forward": forward,
                                          "after": after, "filter": shown})
    assert len(forward) == 1 and 1 <= int(forward[0]["qos"], 16) >> 8 & 0xf <= 7, forward
    assert [row["cookie"] for row in after] == [forward[0]["cookie"]], (forward, after)
    assert crossed == {length: BOUNDARY_COUNT if length <= BOUNDARY_MTU else 0
                       for length in crossed}, crossed


# Linux charges a police action's rate the IP datagram (qdisc_pkt_len() at tc
# ingress), not the Ethernet header or FCS around it. Small datagrams make the
# difference large: 92 IP bytes against a 110-byte frame.
SMALL_DATAGRAM = 64
SMALL_RATE_MBIT = 50


async def test_flower_police_charges_ip_bytes(qos):
    """An offloaded police action admits as many small datagrams per second as
    Linux's would: its rate counts IP bytes."""
    r = qos
    dev = TARGET_WAN_IF
    target = f"{r.lan_ip}:{PORT_POLICED}"
    await r.tc("qdisc", "add", "dev", dev, "clsact")
    await r.tc("filter", "add", "dev", dev, "ingress", "protocol", "ip", "pref", "1",
               "flower", "skip_sw", "ip_proto", "udp", "dst_ip", r.lan_ip,
               "dst_port", str(PORT_POLICED),
               "action", "police", "rate", f"{SMALL_RATE_MBIT}mbit", "burst", POLICE_BURST,
               "conform-exceed", "drop")
    await offload(r, inbound(r, "udp", PORT_POLICED))
    await lan_start(r, iperf=[PORT_POLICED])
    report = await iperf(r, PORT_POLICED, udp_mbit=3 * SMALL_RATE_MBIT, datagram=SMALL_DATAGRAM)
    state = await r.state()
    shown = (await r.tc("-s", "filter", "show", "dev", dev, "ingress"))["stdout"]
    forward = directions(state, ingress=dev, proto=17, dst=target)
    goodput = received_bps(report)
    ip_bytes = SMALL_DATAGRAM + 8 + 20
    expected = SMALL_RATE_MBIT * 1e6 * SMALL_DATAGRAM / ip_bytes
    r.record("qos-police-ip-bytes", {"filter": shown, "state": state, "goodput_bps": goodput,
                                      "expected_bps": expected, "report": report})
    assert len(forward) == 1 and 1 <= int(forward[0]["qos"], 16) >> 8 & 0xf <= 7, forward
    assert police_counters(shown)[1], shown
    # Charging the whole frame would land 16% under; the token bucket itself
    # runs a little under the rate.
    assert 0.95 * expected <= goodput <= 1.03 * expected, (goodput, expected)


async def test_egress_change_readmits_under_the_tree(qos):
    """A tree built or removed under live offloaded flows retires and readmits
    every one of them, with nothing but the next packet doing it.

    A hardware entry carries the egress frame queue it was installed with, so
    when HTB offload switches a port into or out of CEETM, each entry leaving
    by that port names a queue the port no longer schedules. cdx tells the
    adapter after every HTB command, and the adapter retires every entry using
    the port, counting each as a QoS invalidation. Linux readmits the flow on
    its next packet, against whatever the port has by then.

    One four-stream TCP transfer from the LAN VM runs through all of it, and
    its connections are the same conntrack entries at the end as at the start:
    no flush, no reconnect. Unshaped, it runs at hardware line rate; once the
    tree is up every stream is back on a fresh entry, carries the class its
    mark names, and is held at the cap by the leaf that class holds; once the
    tree is gone every stream is back on a fresh entry again, at line rate.
    """
    r = qos
    dev = TARGET_WAN_IF
    target = f"{WAN_IP}:{PORT_EGRESS}"
    await offload(r, f"ip saddr {r.lan_ip} ip daddr {WAN_IP} tcp dport {PORT_EGRESS} "
                     f"ct mark set {r.mark(HIGH_CQ):#x} flow add @fast")
    route = f"{WAN_IP}/32"
    client = f'''
import json, subprocess
route = {route!r}
# A run killed before its own cleanup leaves this route behind, and that one
# is this case's to remove. Anything else holding the prefix is not.
for entry in json.loads(subprocess.check_output(['ip', '-j', 'route', 'show', 'exact', route],
                                               text=True)):
    assert (entry.get('gateway'), entry.get('dev')) == ({r.lan_gateway!r}, {LAN_NIC!r}), entry
    subprocess.run(['ip', 'route', 'del', route, 'dev', {LAN_NIC!r}], check=True)
subprocess.run(['ip', 'route', 'add', route, 'via', {r.lan_gateway!r}, 'dev', {LAN_NIC!r},
                'mtu', '1500'], check=True)
try:
    result = subprocess.run(['iperf3', '-c', {WAN_IP!r}, '-B', {r.lan_ip!r},
                             '-p', {str(PORT_EGRESS)!r}, '-P', '4', '-t', '60'],
                            capture_output=True, text=True, timeout=90)
finally:
    subprocess.run(['ip', 'route', 'del', route, 'dev', {LAN_NIC!r}])
print(json.dumps({{'rc': result.returncode, 'stderr': result.stderr[-400:]}}))
'''
    # The receiving end streams its intervals, so a rate can be read for each
    # phase of the one transfer. It is stopped once the last phase has been
    # sampled, which ends the sender too.
    server = await asyncio.create_subprocess_exec(
        "iperf3", "-s", "-1", "-B", WAN_IP, "-p", str(PORT_EGRESS), "--json-stream",
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    intervals = []
    reader = asyncio.create_task(read_intervals(server.stdout, intervals))
    transfer = None
    try:
        await asyncio.sleep(0.3)
        assert server.returncode is None, "the endpoint iperf3 did not start"
        transfer = asyncio.create_task(lan_run_python(r.lan, client, label="flowtable_qos_egress",
                                                      timeout=120))
        deadline = time.monotonic() + 10
        while True:
            state = await r.state()
            bulk = [f for f in directions(state, ingress=TARGET_LAN_IF, proto=6, dst=target)
                    if int(f["bytes"]) > 1_000_000]
            if len(bulk) == 4:
                break
            assert not transfer.done() and time.monotonic() < deadline, state
            await asyncio.sleep(0.25)
        streams = {f["src"] for f in bulk}
        admitted = time.monotonic()
        connections = await conntrack_ids(r, PORT_EGRESS)
        await asyncio.sleep(3.0)

        async def change(step):
            """Change the WAN port's tree, then wait until every stream is back
            on an entry installed after the change, and the port's QoS
            invalidations account for every entry that was using it."""
            before = await r.state()
            using = sum(1 for f in before["flows"] if dev in (f["in"], f["out"]))
            old = {f["cookie"] for f in before["flows"]}
            started = time.monotonic()
            await step()
            # A change is several HTB commands, and a flow readmitted between
            # two of them is retired again by the next. The retirement itself
            # is queued work, so give it a moment: an entry still listed for
            # that instant must not pass for the readmission this waits for.
            await asyncio.sleep(0.5)

            # A retired connection is readmitted as the same Netfilter flow,
            # so its cookie is kept; what shows the readmission is an install
            # for each of its directions. Invalidations count connections,
            # whose two directions share one handle. Only the bulk streams
            # are waited for: iperf3's idle control connection is retired
            # too, and sends nothing that would readmit it.
            def readmitted(state):
                present = {f["src"] for f in directions(state, ingress=TARGET_LAN_IF, proto=6,
                                                        dst=target)}
                return (streams <= present and
                        state["installs"] - before["installs"] >= 2 * len(streams) and
                        state["qos_invalidations"] >= before["qos_invalidations"] + len(streams))
            after = await r.wait(readmitted, timeout=20)
            return {"started": started, "readmitted": time.monotonic(), "before": before,
                    "after": after, "using": using, "old": sorted(old)}

        shaping = await change(lambda: tree(r, dev, CAP_MBIT, [("1:10", HIGH_PRIO)]))
        await asyncio.sleep(1.0)
        first = await egress(r, dev)
        await asyncio.sleep(WINDOW)
        second = await egress(r, dev)
        unshaping = await change(lambda: r.tc("qdisc", "del", "dev", dev, "root"))
        await asyncio.sleep(4.5)
        ended = time.monotonic()
        survived = await conntrack_ids(r, PORT_EGRESS)
    finally:
        if server.returncode is None:
            server.terminate()
        await asyncio.wait_for(server.wait(), 10)
        await asyncio.gather(reader, return_exceptions=True)
        # Finish the sender before fixture teardown changes its network.
        sender = await transfer if transfer else None
    r.record("qos-egress-change", {"intervals": intervals, "admitted": admitted,
                                   "shaping": shaping, "unshaping": unshaping, "ended": ended,
                                   "first": first, "second": second,
                                   "connections": sorted(connections),
                                   "survived": sorted(survived),
                                   "sender": sender.stdout if sender else None})

    def median_between(start, end):
        rates = [bps for arrived, bps in intervals if arrived - 1.0 >= start and arrived <= end]
        assert len(rates) >= 2, (start, end, intervals)
        return statistics.median(rates)

    cap = CAP_MBIT * 1e6
    assert len(connections) >= 4 and survived == connections, (connections, survived)
    for phase in (shaping, unshaping):
        after = phase["after"]
        assert phase["using"] >= 2 * len(streams), phase["before"]
        # Every connection using the port was retired, and each stream's
        # directions installed again.
        assert after["qos_invalidations"] - phase["before"]["qos_invalidations"] >= phase["using"] // 2, (
            "an entry using the port outlived the change to its egress", phase)
        assert after["installs"] - phase["before"]["installs"] >= 2 * len(streams), phase
        assert after["invalidated"] == after["fatal"] == 0 and after["bindings"] == 2, after
    shaped_rows = directions(shaping["after"], ingress=TARGET_LAN_IF, proto=6, dst=target)
    assert all(int(f["qos"], 16) == HIGH_CQ for f in shaped_rows), shaped_rows
    assert median_between(admitted, shaping["started"]) >= UNSHAPED_GBPS * 1e9, intervals
    expected = cap * TCP_PAYLOAD / (TCP_FRAME + OAL)
    shaped = median_between(shaping["readmitted"] + 1.0, unshaping["started"])
    assert 0.88 * expected <= shaped <= 1.01 * expected, (shaped, expected)
    # The agent's own frames leave by this port through the same class queue as
    # the transfer while the tree stands, so its reads are slower here than
    # anywhere else in the file, and the window's edges less certain.
    held = shaped_bps(first, second, 0)
    slack = timing_slack(first, second)
    assert (0.95 - slack) * cap <= held <= (1.03 + slack) * cap, (
        held, cap, slack, leaf_delta(first, second, 0))
    assert median_between(unshaping["readmitted"] + 1.0, ended) >= UNSHAPED_GBPS * 1e9, intervals
