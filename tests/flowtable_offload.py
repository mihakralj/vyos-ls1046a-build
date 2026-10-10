"""IPv4/UDP flowtable acceptance on the real DUT.

Run with make ask-test ASK_TEST_ARGS='-k flowtable_offload'.
Healthy invalidation recovers once the invalidated bindings are gone, including
across an atomic reload, and an unproven deletion stops the datapath and
restarts it in the same boot. The terminal tests -- CDX unload, and a deletion
once the restart budget is spent -- still require a fresh boot before using ASK
again.
"""
from __future__ import annotations

from _flowtable_rig import (artifact_dir, DPORT, HEALTH_BASELINE, RX_PORTS_SCRIPT, SPORT, TABLE, WAN_IP, ct_bytes, ct_counts, ct_listing, hardware_proof, latch_barrier_failure, links_restored, rearm_ready, status_text, terminal_stream, upper_roundtrip)

import asyncio
import errno
import json
import os
import re
import threading
import time

import pytest

from ask_orch.commands import command, console_command, console_python, read
from ask_orch.counters import kernel_tx_packets
from ask_orch.uart import Console
from _topology import LAN_NIC, TARGET_LAN_IF, TARGET_WAN_IF, kernel_rx_packets, lan_run_python

# Per-boot floor for the cumulative counters health checks compare against;
# the rig fixture refreshes it for every test.


# Other writers share the UART with our command: the managed service's
# console-fallback log lines, and kernel messages at console level (failslab
# stack dumps, the Wi-Fi driver logging a client associating with the test
# AP, and printk's own "messages dropped" notice when it falls behind). Each is
# a whole line, but it can start anywhere, including in the middle of the
# controller's JSON, so it is removed wherever it lands. None of these shapes
# can occur inside that JSON.


# Row kinds the proc file emits, by their leading word. Everything else is a
# single "key value" counter. A row kind always yields a list, present and
# empty when nothing of that kind exists, so a caller never has to guess
# whether an absent key means none or means an older adapter.


@pytest.mark.skipif(os.environ.get("ASK_FLOWTABLE_BASELINE") not in {"software", "flowtable", "hardware"},
                    reason="explicit forwarding-path loss diagnosis")
@pytest.mark.rfc("768")
async def test_long_exchange(rig):
    assert (await rig.state())["entries"] == 0
    hardware = os.environ["ASK_FLOWTABLE_BASELINE"] == "hardware"
    if hardware:
        assert not (await rig.state())["observe"]
        await rig.table()
    elif os.environ["ASK_FLOWTABLE_BASELINE"] == "flowtable":
        await rig.table(hardware=False)
    await rig.exchange(4096, promiscuous=False)
    assert (await rig.state())["entries"] == (2 if hardware else 0)


async def test_reference_and_lifecycle(rig):
    r = rig
    from _ioctl import CDX_CTRL_DPA_SET_PARAMS, SIZEOF_CDX_CTRL_SET_DPA_PARAMS
    reply = await r.target.ioctl_send(r.session, "/dev/cdx_ctrl", CDX_CTRL_DPA_SET_PARAMS,
                                     bytes(SIZEOF_CDX_CTRL_SET_DPA_PARAMS))
    assert reply["errno"] == errno.ENOTTY, reply
    await r.exchange()  # ordinary routing, no flowtable
    assert (await r.state())["entries"] == 0
    await r.table(hardware=False, counter=True)
    await r.exchange()
    assert (await r.state())["entries"] == 0
    await r.delete_table()
    await r.clear_ct()
    # A counter-enabled hardware table is admitted, and what reaches conntrack
    # accounting is the frame Netfilter counts rather than the frame the
    # classifier saw. A 256-byte payload is 298 bytes on the wire and 284 once
    # the Ethernet header the software path never sees is taken off, so both
    # paths agree and the total is the same whichever forwarded it.
    await r.table(counter=True)
    await r.clear_ct()
    payload, count = 256, 64
    expected = count * (payload + 8 + 20)
    # The whole flow, not a delta around a baseline. The first packets cross in
    # software and the rest in hardware, and the total is the same either way --
    # which is the point: the two now agree on what a frame is worth, so no part
    # of this has to know where the boundary fell.
    await r.exchange(count, payload_size=payload)
    counted = await r.wait(lambda s: s["entries"] == 2)
    total, deadline = None, time.monotonic() + 30
    while time.monotonic() < deadline:
        total = await ct_bytes(r)
        if total >= expected:
            break
        await asyncio.sleep(0.5)
    assert total == expected, (total, expected, counted, await r.state())
    r.record("counter-accounted", {"bytes": total, "expected": expected, "state": await r.state()})
    await r.delete_table()
    await r.clear_ct()
    await r.table()
    await r.exchange()
    if (await r.state())["observe"]:
        state = await r.wait(lambda s: s["validated"] >= 2)
        assert state["entries"] == state["installs"] == 0
        r.record("observe", state)
        return
    installed = await r.wait(lambda s: s["entries"] == 2)
    assert all(int(flow["mtu"]) == r.port_mtu for flow in installed["flows"]), installed
    before = {dev: await kernel_rx_packets(r.target, r.session, dev) for dev in [TARGET_LAN_IF, TARGET_WAN_IF]}
    baseline = {flow["in"]: int(flow["packets"]) for flow in installed["flows"]}
    from scapy.all import AsyncSniffer, Ether, IP, UDP, wrpcap
    capture_ready = threading.Event()
    sniffer = AsyncSniffer(iface=r.wan_if, filter=f"udp port {DPORT}", store=True,
                          started_callback=capture_ready.set)
    sniffer.start()
    try:
        assert await asyncio.to_thread(capture_ready.wait, 5), "endpoint capture did not start"
        report = await r.exchange(512)
    finally:
        packets = sniffer.stop()
        artifact_dir().mkdir(parents=True, exist_ok=True)
        wrpcap(str(artifact_dir() / "hardware-udp.pcap"), packets)
    final = await r.state()
    after = {dev: await kernel_rx_packets(r.target, r.session, dev) for dev in before}
    for flow in final["flows"]:
        assert int(flow["packets"]) - baseline[flow["in"]] == 512, (installed, final)
    for dev in before:
        assert 0 <= after[dev] - before[dev] <= 64, (dev, before, after)
    requests = [p for p in packets if IP in p and UDP in p and p[IP].src == r.lan_ip and p[UDP].dport == DPORT]
    assert len(requests) == 512
    for p in requests:
        assert p[IP].ttl == 63 and p[IP].ihl == 5
        assert p[Ether].src == r.dut_wan_mac and p[Ether].dst == r.wan_mac
        saved = p[IP].chksum
        copy = p[IP].copy(); del copy.chksum
        assert IP(bytes(copy)).chksum == saved
        udp = p[IP].copy(); saved_udp = udp[UDP].chksum; del udp[UDP].chksum
        assert IP(bytes(udp))[UDP].chksum == saved_udp
    r.record("hardware", {"installed": installed, "final": final, "software_rx_before": before,
                          "software_rx_after": after, "exchange": report})
    await r.exchange(32, payload_size=8)
    short_packets = await r.state()
    for old, new in zip(final["flows"], short_packets["flows"], strict=True):
        assert old["cookie"] == new["cookie"]
        assert int(new["packets"]) - int(old["packets"]) == 32
        assert int(new["bytes"]) - int(old["bytes"]) == 32 * 60  # includes Ethernet padding
    r.record("short-packets", {"before": final, "after": short_packets})
    # Three repeated cycles exercise unbind/rebind and resource return under traffic.
    for _ in range(3):
        received = len(r.echo.received)
        traffic = asyncio.create_task(r.exchange(256))
        deadline = time.monotonic() + 10
        while len(r.echo.received) < received + 16 and not traffic.done():
            assert time.monotonic() < deadline, "teardown traffic did not start"
            await asyncio.sleep(0.01)
        try:
            removed = await r.delete_table()
        finally:
            await traffic
        assert removed["quarantine"] == 0 and removed["errors"] == HEALTH_BASELINE["errors"], removed
        await r.exchange()
        assert (await r.state())["entries"] == 0
        await r.clear_ct()
        await r.table()
        await r.exchange()
        await r.wait(lambda s: s["entries"] == 2)
    # Active traffic spans two default 30-second UDP flowtable timeouts.
    end = time.monotonic() + 65
    while time.monotonic() < end:
        await r.exchange(16)
        assert (await r.state())["entries"] == 2
        await asyncio.sleep(2)
    expired = await r.wait(lambda s: s["entries"] == 0, timeout=40)
    assert expired["quarantine"] == 0 and expired["errors"] == HEALTH_BASELINE["errors"], expired
    r.record("idle-expiry", expired)


@pytest.mark.rfc("791")
@pytest.mark.rfc("792")
@pytest.mark.rfc("1812", section="5.3.1")
async def test_same_tuple_exceptions(rig):
    r = rig
    if (await r.state())["observe"]:
        pytest.skip("exception handling requires installed hardware")
    await r.table()
    await r.exchange()
    await r.wait(lambda s: s["entries"] == 2)
    before = await r.state()
    await r.exchange(32, payload_size=8)
    r.record("exception-short-packets", {"before": before, "after": await r.state()})
    # An oversized DF packet is not among these. A UDP direction is installed
    # only where its path carries the largest frame its ingress port can
    # deliver, so nothing arriving on this tuple can exceed its entry; the DF
    # exception is proved on a TCP entry in flowtable_mtu.py.
    script = f'''
import json, socket, struct, time
from scapy.all import Ether, IP, UDP, ICMP, Raw, IPOption, fragment, sendp, srp1, getmacbyip
iface = {LAN_NIC!r}
src, dst = {r.lan_ip!r}, {WAN_IP!r}
sport, dport = {SPORT}, {DPORT}
gateway = getmacbyip({r.lan_gateway!r})
assert gateway
eth = Ether(dst=gateway)
s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
s.bind((src, sport)); s.settimeout(3)
s.setsockopt(socket.SOL_IP, getattr(socket, "IP_RECVTTL", 12), 1)
base = IP(src=src, dst=dst, ttl=64)/UDP(sport=sport, dport=dport)
results = {{}}
for name, pkt, icmp_type, icmp_code in [
    ('ttl', IP(src=src,dst=dst,ttl=1)/UDP(sport=sport,dport=dport)/Raw(b'ASK-expired'), 11, 0),
]:
    answer = srp1(eth/pkt, iface=iface, timeout=3, verbose=False)
    assert answer is not None and ICMP in answer, (name, answer)
    assert (answer[ICMP].type, answer[ICMP].code) == (icmp_type, icmp_code), answer.summary()
    results[name] = answer.summary()
for name, packets, payload in [
    ('options', [IP(src=src,dst=dst,ttl=64,options=[IPOption(b'\\x01'*4)])/UDP(sport=sport,dport=dport)/Raw(b'ASK-options')], b'ASK-options'),
    ('fragments', fragment(base/Raw(b'ASK-fragments'.ljust(1024,b'.')), fragsize=512), b'ASK-fragments'.ljust(1024,b'.')),
]:
    sendp([eth/p for p in packets], iface=iface, verbose=False)
    data, anc, flags, addr = s.recvmsg(4096, 128)
    assert data == payload and addr == (dst, dport), (name, data, addr)
    ttl = [struct.unpack('i', v)[0] for level, kind, v in anc if level == socket.SOL_IP and kind == socket.IP_TTL]
    assert ttl == [63], (name, ttl)
    results[name] = len(data)
# Headers Linux would discard must not be forwarded by the entry for their tuple.
for name, pkt in [
    ('bad_checksum', IP(src=src,dst=dst,ttl=64,chksum=0x1234)/UDP(sport=sport,dport=dport)/Raw(b'ASK-badsum')),
    ('version', IP(src=src,dst=dst,ttl=64,version=5)/UDP(sport=sport,dport=dport)/Raw(b'ASK-version')),
    ('version15', IP(src=src,dst=dst,ttl=64,version=15)/UDP(sport=sport,dport=dport)/Raw(b'ASK-version15')),
]:
    sendp(eth/pkt, iface=iface, verbose=False)
    results[name] = 'sent'
time.sleep(0.5)
s.close()
print(json.dumps(results))
'''
    result = await lan_run_python(r.lan, script, timeout=25, label="flowtable_exceptions")
    assert result.rc == 0, result.stdout
    assert r.echo.received[b"ASK-options"] == 1
    assert r.echo.received[b"ASK-fragments".ljust(1024, b".")] == 1
    assert not r.echo.received[b"ASK-expired"]
    assert not any(r.echo.received[p] for p in (b"ASK-badsum", b"ASK-version", b"ASK-version15"))
    await r.exchange()
    r.record("exceptions", {"results": json.loads(result.stdout.strip()), "state": await r.state()})


async def test_conntrack_timeout_extension(rig):
    """A packet that still reaches conntrack after admission cuts the offloaded
    conntrack's timeout back to its protocol's. The flowtable GC must lift it
    again on its next pass, or the conntrack expires under a flow hardware is
    still carrying and its retirement takes the hardware flow with it.

    The stream timeout is shortened to ten seconds so the window fits in a
    test, and one same-tuple frame software has to handle -- TTL 1, which
    Linux answers with Time Exceeded only after conntrack has seen it -- resets
    the conntrack to it. Thirty seconds of hardware-only traffic follow, three
    of those timeouts, with a table dump every round: a dump evicts any
    expired conntrack it walks past, so one left to lapse dies inside the
    window instead of waiting for conntrack's own GC to find it."""
    r = rig
    if (await r.state())["observe"]:
        pytest.skip("timeout extension requires installed hardware")
    await r.table()
    opened = time.monotonic()
    await r.exchange()
    installed = await r.wait(lambda s: s["entries"] == 2)
    admitted = await ct_listing(r)
    assert "[HW_OFFLOAD]" in admitted, admitted
    identity = re.search(r"\bid=(\d+)", admitted)[1]
    knob = "net.netfilter.nf_conntrack_udp_timeout_stream"
    old = (await read(r.target, r.session, "/proc/sys/" + knob.replace(".", "/"))).strip()
    await command(r.target, r.session, "sysctl", "-w", f"{knob}=10")
    try:
        # udp_packet() applies the stream timeout only to a connection older
        # than two seconds; a younger one would get the unreplied timeout.
        await asyncio.sleep(max(0.0, 2.5 - (time.monotonic() - opened)))
        script = f'''
import json
from scapy.all import Ether, IP, UDP, ICMP, Raw, srp1
packet = IP(src={r.lan_ip!r}, dst={WAN_IP!r}, ttl=1)/UDP(sport={SPORT}, dport={DPORT})/Raw(b'ASK-ct-refresh')
answer = srp1(Ether(dst={r.dut_lan_mac!r})/packet, iface={LAN_NIC!r}, timeout=3, verbose=False)
assert answer is not None and ICMP in answer, answer
assert (answer[ICMP].type, answer[ICMP].code) == (11, 0), answer.summary()
print(json.dumps(answer.summary()))
'''
        result = await lan_run_python(r.lan, script, timeout=15, label="flowtable_ct_refresh")
        assert result.rc == 0, result.stdout
        assert not r.echo.received[b"ASK-ct-refresh"]
        before = await r.state()
        assert {f["cookie"] for f in before["flows"]} == {f["cookie"] for f in installed["flows"]}, before
        tx_before = await kernel_tx_packets(r.target, r.session, TARGET_LAN_IF)
        sent, rounds = 0, []
        window = time.monotonic() + 30
        while time.monotonic() < window:
            await r.exchange(16, promiscuous=False)
            sent += 16
            listing = await ct_listing(r)
            rounds.append({"seconds": round(time.monotonic() - window + 30, 1), "conntrack": listing})
            assert "[HW_OFFLOAD]" in listing and f"id={identity}" in listing.split(), rounds
            await asyncio.sleep(1)
        after = await r.state()
        tx_after = await kernel_tx_packets(r.target, r.session, TARGET_LAN_IF)
        r.record("ct-timeout-extension", {"refresh": result.stdout.strip(), "before": before,
                                          "after": after, "rounds": rounds, "sent": sent,
                                          "software_lan_tx": tx_after - tx_before})
        assert after["installs"] == before["installs"] and after["deletes"] == before["deletes"], (before, after)
        old_rows, new_rows = {f["in"]: f for f in before["flows"]}, {f["in"]: f for f in after["flows"]}
        assert after["entries"] == 2 and new_rows.keys() == old_rows.keys(), after
        for ingress, flow in new_rows.items():
            assert flow["cookie"] == old_rows[ingress]["cookie"], (before, after)
            assert int(flow["packets"]) - int(old_rows[ingress]["packets"]) == sent, (ingress, before, after)
        # Every reply crossed the LAN port; hardware carried all of them.
        assert 0 <= tx_after - tx_before <= 64 < sent, (tx_before, tx_after, sent)
        # The timeout itself is not observable: neither /proc/net/nf_conntrack
        # nor ctnetlink reports one for an offloaded conntrack. Surviving three
        # stream timeouts of dumps that evict an expired entry is the proof.
    finally:
        await command(r.target, r.session, "sysctl", "-w", f"{knob}={old}")


async def test_add_failures(rig):
    r = rig
    if (await r.state())["observe"]:
        pytest.skip("installation faults require hardware mode")
    for stage in [1, 2, 3]:
        result = await r.target.fs_write(r.session, "/sys/module/ask_flowtable/parameters/flowtable_fail_stage", str(stage))
        assert result["errno"] == 0, result
        for attempt in range(3):
            before = await r.state()
            await r.table()
            await r.exchange()
            remaining = (await read(r.target, r.session, "/sys/module/ask_flowtable/parameters/flowtable_fail_stage")).strip()
            if remaining == "0":
                break
            # RTNL contention declines admission before fault injection. The
            # software path offers the flow again about a second later, but
            # only while traffic keeps it there, so a fresh connection is the
            # dependable way to reach the requested stage.
            state = await r.state()
            assert state["busy"] > before["busy"] and state["invalidated"] == 0, state
            await r.delete_table()
            await r.clear_ct()
        assert remaining == "0", (stage, await r.state())
        # Native Netfilter may install the other direction: each accepted
        # direction is independent. The rejected request leaves no owned object.
        await r.wait(lambda s: s["rejects"] > before["rejects"])
        assert (await read(r.target, r.session, "/sys/module/ask_flowtable/parameters/flowtable_fail_stage")).strip() == "0"
        state = await r.delete_table()
        assert state["errors"] == HEALTH_BASELINE["errors"] and state["quarantine"] == 0, state
        assert state["installs"] == state["deletes"], state
        await r.clear_ct()
        r.record(f"add-failure-{stage}", state)


async def test_rearm(rig):
    """Recover after upper-device and routing-policy changes and a retried delete barrier."""
    r = rig
    initial = await r.state()
    if initial["observe"]:
        pytest.skip("rearm proof requires installed hardware")
    original_mtu = (await read(r.target, r.session, f"/sys/class/net/{TARGET_LAN_IF}/mtu")).strip()
    rule_priority = "32001"
    rules = json.loads((await command(r.target, r.session, "ip", "-j", "rule", "show"))["stdout"])
    assert not any(rule.get("priority") == int(rule_priority) for rule in rules), rules
    rule_added = False
    boot_id = await read(r.target, r.session, "/proc/sys/kernel/random/boot_id")
    await r.table()
    await r.exchange(128, promiscuous=False)
    await r.wait(lambda s: s["entries"] == 2)
    try:
        for cycle, trigger in enumerate(("device", "rule", "barrier"), 1):
            before = await r.state()
            traffic = None
            knob = "/proc/fm_ehash_hcsync_fail"
            try:
                if trigger == "device":
                    received = len(r.echo.received)
                    traffic = asyncio.create_task(terminal_stream(r, duration=6))
                    deadline = time.monotonic() + 5
                    while len(r.echo.received) < received + 16:
                        assert not traffic.done() and time.monotonic() < deadline
                        await asyncio.sleep(0.02)
                    await upper_roundtrip(r, TARGET_LAN_IF)
                elif trigger == "rule":
                    await command(r.target, r.session, "ip", "rule", "add", "pref", rule_priority,
                                  "from", "198.18.254.0/24", "table", "main")
                    rule_added = True
                else:
                    result = await r.target.fs_write(r.session, knob, "2")
                    assert result["errno"] == 0, result
                    await r.delete_table()
                    assert (await read(r.target, r.session, knob)).strip() == "armed=0"
                invalid = await r.wait(lambda s: s["invalidation_done"] == 1 and s["entries"] == 0)
                assert invalid["invalidated"] == 1 and invalid["fatal"] == invalid["quarantine"] == 0
                assert invalid["installs"] == before["installs"]
                assert invalid["rearms"] == before["rearms"]
                assert invalid["errors"] - before["errors"] == (2 if trigger == "barrier" else 0)
                assert invalid["bindings"] == (0 if trigger == "barrier" else 2)
                assert invalid["rearm_ready"] == (1 if trigger == "barrier" else 0)
            finally:
                if traffic:
                    r.record("rearm-transition", await traffic)
                if trigger == "barrier":
                    result = await r.target.fs_write(r.session, knob, "0")
                    assert result["errno"] == 0, result
            # An MTU event must not turn an unrelated global invalidation into
            # automatic recovery, including after all directions have drained.
            try:
                await command(r.target, r.session, "ip", "link", "set", "dev", TARGET_LAN_IF, "mtu", "1400")
            finally:
                await command(r.target, r.session, "ip", "link", "set", "dev", TARGET_LAN_IF, "mtu", original_mtu)
            held = await r.state()
            assert held["invalidated"] == held["invalidation_done"] == 1, held
            assert held["installs"] == before["installs"] and held["rearms"] == before["rearms"], held
            software_before = await kernel_tx_packets(r.target, r.session, TARGET_LAN_IF)
            await r.exchange(64, promiscuous=False)
            software_after = await kernel_tx_packets(r.target, r.session, TARGET_LAN_IF)
            blocked = await r.state()
            assert software_after - software_before >= 64
            assert blocked["entries"] == 0 and blocked["installs"] == before["installs"]
            assert blocked["invalidated"] == 1 and blocked["rearms"] == before["rearms"]
            if trigger == "device":
                # Hook removal alone leaves cached Linux flows. Such a table
                # must not reopen hardware admission, even with zero bindings.
                await r.nft(f"delete flowtable inet {TABLE} fast {{ "
                            f"devices = {{ {TARGET_LAN_IF}, {TARGET_WAN_IF} }}; }}")
                await r.wait(lambda s: s["bindings"] == 0 and s["rearm_ready"] == 1)
                refused = await command(r.target, r.session, "nft",
                                        f"add flowtable inet {TABLE} fast {{ hook ingress priority 0; "
                                        f"devices = {{ {TARGET_LAN_IF}, {TARGET_WAN_IF} }}; flags offload; }}",
                                        check=False)
                assert refused["rc"] != 0 and "Operation not supported" in refused["stderr"], refused
                same_table = await r.state()
                assert same_table["bindings"] == same_table["entries"] == 0
                assert same_table["invalidated"] == 1 and same_table["rearms"] == before["rearms"]
                r.record("rearm-populated-refused", {"state": same_table, "nft": refused})
            await r.delete_table()
            detached = await r.wait(lambda s: s["rearm_ready"] == 1)
            assert detached["bindings"] == detached["entries"] == detached["quarantine"] == 0
            await r.clear_ct()
            await r.table()
            rearmed = await r.state()
            assert rearmed["rearms"] == initial["rearms"] + cycle
            assert rearmed["invalidated"] == rearmed["invalidation_done"] == rearmed["rearm_ready"] == 0
            assert rearmed["errors"] == invalid["errors"] and rearmed["fatal"] == 0
            await r.exchange(128, promiscuous=False)
            installed = await r.wait(lambda s: s["entries"] == 2)
            for flow in installed["flows"]:
                assert int(flow["mtu"]) == r.port_mtu, installed
            tx_before = {d: await kernel_tx_packets(r.target, r.session, d)
                         for d in (TARGET_LAN_IF, TARGET_WAN_IF)}
            report = await r.exchange(512, promiscuous=False)
            final = await r.state()
            tx_after = {d: await kernel_tx_packets(r.target, r.session, d) for d in tx_before}
            packets = {f["in"]: int(f["packets"]) for f in installed["flows"]}
            assert final["entries"] == 2 and final["installs"] == installed["installs"]
            for flow in final["flows"]:
                assert int(flow["packets"]) - packets[flow["in"]] == 512, (installed, final)
            for dev in tx_before:
                assert 0 <= tx_after[dev] - tx_before[dev] <= 64, (dev, tx_before, tx_after)
            assert final["errors"] == invalid["errors"]
            assert final["invalidated"] == final["fatal"] == final["quarantine"] == 0
            assert await read(r.target, r.session, "/proc/sys/kernel/random/boot_id") == boot_id
            r.record(f"rearm-{trigger}", {"before": before, "invalid": invalid, "blocked": blocked,
                     "software_tx_delta": software_after - software_before, "detached": detached,
                     "rearmed": rearmed, "installed": installed, "final": final,
                     "software_tx_before": tx_before, "software_tx_after": tx_after,
                     "exchange": report, "boot_id": boot_id})
    finally:
        try:
            await r.delete_table()
        finally:
            if rule_added:
                await command(r.target, r.session, "ip", "rule", "del", "pref", rule_priority,
                              "from", "198.18.254.0/24", "table", "main")


@pytest.mark.parametrize("trigger", [os.environ.get("ASK_FLOWTABLE_INVALIDATION", "neighbour")])
async def test_invalidation(rig, trigger):
    r = rig
    if (await r.state())["observe"]:
        pytest.skip("invalidation requires installed hardware")
    await r.table()
    await r.exchange()
    before = await r.wait(lambda s: s["entries"] == 2)
    assert trigger in {"neighbour", "barrier"}
    if trigger == "barrier":
        knob = "/proc/fm_ehash_hcsync_fail"
        result = await r.target.fs_write(r.session, knob, "2")
        assert result["errno"] == 0, result
        try:
            await r.delete_table()
            assert (await read(r.target, r.session, knob)).strip() == "armed=0"
        finally:
            result = await r.target.fs_write(r.session, knob, "0")
            assert result["errno"] == 0, result
    else:
        await command(r.target, r.session, "ip", "neigh", "del", WAN_IP, "dev", TARGET_WAN_IF)
    state = await r.wait(lambda s: s["entries"] == 0 and (
        s["neighbour_invalidations"] > before["neighbour_invalidations"] if trigger == "neighbour"
        else s["invalidation_done"] == 1))
    assert state["invalidated"] == int(trigger != "neighbour") and state["fatal"] == state["quarantine"] == 0
    assert state["handle_refs"] == state["neighbour_refs"] == 0, state
    assert state["errors"] - before["errors"] == (2 if trigger == "barrier" else 0)
    await r.exchange()
    if trigger != "neighbour":
        assert (await r.state())["entries"] == 0
    r.record(f"invalidation-{trigger}", state)


async def test_table_reload(rig):
    """A consumer reloads its ruleset by deleting its table and creating it
    again in one transaction, which is how `nft -f` with a flush and fw4 both
    apply a change. Netfilter binds the new flowtable while preparing and
    releases the old one only at commit, so for that instant two tables hold
    every port: the reload has to go through with hardware rather than fall
    back to software, and so does fw4's check-mode probe of a second offload
    table. A third table at once is still refused, and nothing is left behind
    by any of it."""
    r = rig
    if (await r.state())["observe"]:
        pytest.skip("the reload proof requires installed hardware")
    await r.table()
    await r.exchange(count=4)
    before = await r.wait(lambda s: s["entries"] == 2)
    ports = f"devices = {{ {TARGET_LAN_IF}, {TARGET_WAN_IF} }}; flags offload;"
    probe = f"table inet {TABLE}_probe {{ flowtable probe {{ hook ingress priority 0; {ports} }}; }}"
    third = f"table inet {TABLE}_third {{ flowtable third {{ hook ingress priority 0; {ports} }}; }}"
    checked = await command(r.target, r.session, "nft", "-c", probe, check=False)
    assert checked["rc"] == 0, checked
    crowded = await command(r.target, r.session, "nft", "-c", probe + "\n" + third, check=False)
    assert crowded["rc"] != 0 and "busy" in crowded["stderr"].lower(), crowded
    probed = await r.state()
    assert probed["bindings"] == 2 and probed["entries"] == 2, probed
    assert {f["cookie"] for f in probed["flows"]} == {f["cookie"] for f in before["flows"]}, (before, probed)
    reloaded = await command(r.target, r.session, "nft", f"delete table inet {TABLE}\n" + r.ruleset(),
                             check=False)
    assert reloaded["rc"] == 0, reloaded
    # The old flowtable took its flows with it; conntrack still holds the
    # connection, so the next packets offer it to the new one.
    await r.wait(lambda s: s["bindings"] == 2 and not s["entries"])
    await r.exchange(count=4)
    admitted = await r.wait(lambda s: s["entries"] == 2)
    baseline = {f["cookie"]: int(f["packets"]) for f in admitted["flows"]}
    await r.exchange(count=64)
    after = await r.state()
    assert {f["cookie"]: int(f["packets"]) - baseline[f["cookie"]] for f in after["flows"]} == \
        {c: 64 for c in baseline}, (admitted, after)
    for key in ("errors", "fatal", "quarantine", "invalidated"):
        assert after[key] == before[key], (key, before, after)
    assert after["installs"] - after["deletes"] == after["entries"] == 2, after
    assert after["handle_refs"] == after["neighbour_refs"] == 2, after
    r.record("table-reload", {"before": before, "probed": probed, "after": after,
                              "crowded": crowded["stderr"]})


async def test_reload_invalidated(rig):
    """An atomic reload while an invalidation is latched goes through, and the
    reloaded table takes hardware over by itself.

    Netfilter binds a reload's new flowtable while the invalidated one is still
    bound. That bind used to be refused, which failed the consumer's whole
    firewall reload -- every fw4 reload, once anything had invalidated -- and
    left hardware offload unrecoverable without a separate detach. The adapter
    parks it instead: bound and counted, declining every flow, until the old
    bindings are gone and the hardware has drained, and then makes it live in
    the same transaction. Two shapes of reload:

      - fw4's, where the old flowtable goes in the same transaction: admission
        reopens at its commit and the next packets enter hardware;
      - one where the old table outlives the reload: the flow forwards in
        software through the parked table, and returns to hardware once the
        old table is deleted, with no further change to the new one."""
    r = rig
    if (await r.state())["observe"]:
        pytest.skip("the reload proof requires installed hardware")
    replacement = f"{TABLE}_next"
    try:
        await r.table()
        await r.exchange()
        await r.wait(lambda s: s["entries"] == 2)
        latched = await latch_barrier_failure(r)
        assert latched["bindings"] == 2 and latched["parked"] == 0, latched

        # fw4's own reload: the flowtable is deleted and declared again in one
        # transaction, inside a table that survives it.
        reload = (f"table inet {TABLE}\nflush table inet {TABLE}\n"
                  f"delete flowtable inet {TABLE} fast\n" + r.ruleset())
        reloaded = await command(r.target, r.session, "nft", reload, check=False)
        assert reloaded["rc"] == 0 and not reloaded["stderr"], reloaded
        rearmed = await r.state()
        assert rearmed["bindings"] == 2 and rearmed["parked"] == 0, rearmed
        assert rearmed["invalidated"] == rearmed["invalidation_done"] == rearmed["rearm_ready"] == 0, rearmed
        assert rearmed["rearms"] == latched["rearms"] + 1 and rearmed["errors"] == latched["errors"], rearmed
        await r.exchange()
        await r.wait(lambda s: s["entries"] == 2)
        reopened = await hardware_proof(r)
        r.record("reload-invalidated-fw4", {"latched": latched, "rearmed": rearmed,
                                            "hardware": reopened, "nft": reloaded})

        # The old table outlives the reload: the replacement is added and the
        # old chain emptied in one transaction, leaving the old flowtable
        # bound. The connection was removed to latch the invalidation, so the
        # next packet makes a new one, and only the replacement offers it.
        latched = await latch_barrier_failure(r)
        takeover = f'''table inet {replacement} {{
 flowtable fast {{ hook ingress priority 0; devices = {{ {TARGET_LAN_IF}, {TARGET_WAN_IF} }}; flags offload; }}
 chain forward {{ type filter hook forward priority 0; policy accept;
 ip saddr {r.lan_ip} ip daddr {WAN_IP} {r.proto} sport {SPORT} {r.proto} dport {DPORT} flow add @fast
 }}
}}
flush chain inet {TABLE} forward'''
        moved = await command(r.target, r.session, "nft", takeover, check=False)
        assert moved["rc"] == 0 and not moved["stderr"], moved
        parked = await r.state()
        assert parked["bindings"] == 4 and parked["parked"] == 2, parked
        assert parked["invalidated"] == 1 and parked["rearms"] == latched["rearms"], parked
        software_before = await kernel_tx_packets(r.target, r.session, TARGET_LAN_IF)
        await r.exchange(64, promiscuous=False)
        software_after = await kernel_tx_packets(r.target, r.session, TARGET_LAN_IF)
        carried = await r.state()
        listing = await ct_listing(r)
        # The replacement's software fast path carried it, declined by the
        # parked bindings rather than by anything about the flow.
        assert "[OFFLOAD]" in listing and "[HW_OFFLOAD]" not in listing, listing
        assert software_after - software_before >= 64, (software_before, software_after)
        assert carried["entries"] == 0 and carried["installs"] == latched["installs"], carried
        assert carried["rejects"] > parked["rejects"] and carried["parked"] == 2, carried
        assert carried["invalidated"] == 1 and carried["errors"] == latched["errors"], carried

        # Deleting the old table releases its last binding, which is what the
        # parked ones waited for. Nothing touches the replacement.
        await command(r.target, r.session, "nft", "delete", "table", "inet", TABLE)
        live = await r.state()
        assert live["bindings"] == 2 and live["parked"] == 0, live
        assert live["invalidated"] == live["invalidation_done"] == 0, live
        assert live["rearms"] == latched["rearms"] + 1 and live["errors"] == latched["errors"], live
        # Netfilter offers a software flow again on its next refresh, at most
        # once a second under traffic, so keep it flowing until it is taken.
        # With an XFRM policy anywhere in the namespace the software fast path
        # is bypassed and the offer waits for the flow to expire, so allow for
        # the 30-second flowtable timeout too.
        deadline = time.monotonic() + 45
        while True:
            await r.exchange(32, promiscuous=False)
            admitted = await r.state()
            if admitted["entries"] == 2:
                break
            assert time.monotonic() < deadline, admitted
        final = await hardware_proof(r)
        assert final["errors"] == latched["errors"] and final["fatal"] == final["quarantine"] == 0, final
        assert final["invalidated"] == 0 and final["rearms"] == live["rearms"], final
        r.record("reload-invalidated-outlived", {"latched": latched, "parked": parked,
                                                 "carried": carried, "conntrack": listing,
                                                 "software_lan_tx": software_after - software_before,
                                                 "live": live, "admitted": admitted, "final": final})
    finally:
        await command(r.target, r.session, "nft", "delete", "table", "inet", replacement, check=False)
        left = await r.delete_table()
        # A failure between a latch and its rearm would leave the next test's
        # fixture an invalidation with nothing bound. The next bind clears it,
        # so make one and give it back -- but only one the adapter can rearm
        # on: a fatal adapter binds passively, and a quarantine holds the
        # rearm until its barrier completes. Either way this runs because
        # something already failed, and a rebind that cannot finish would
        # replace that error with its own.
        if left["invalidated"] and not left["fatal"] and await rearm_ready(r):
            await r.table()
            await r.wait(lambda s: not s["invalidated"])
            await r.delete_table()


async def test_counter_enabled_live(rig):
    """Enabling `counter` on a table whose flows are already in hardware keeps
    them there and starts accounting their hardware traffic in conntrack.

    Netfilter applies the flag to the live flowtable without unbinding it, so
    the adapter sees no event: entries, cookies and bindings stay as they were
    and nothing is invalidated. From the next statistics pass the flowtable
    core adds each hardware delta to conntrack, restated in Netfilter's units,
    so a 256-byte payload counts 284 bytes in either direction.

    Until then hardware traffic reaches the flowtable core but not conntrack.
    A statistics pass runs within about four seconds of the last packet at the
    default 30-second timeout, so the pause before the change lets one consume
    everything earlier, and the delta measured after it is this test's own."""
    r = rig
    if (await r.state())["observe"]:
        pytest.skip("live counter enablement requires installed hardware")
    await r.table()
    await r.exchange()
    installed = await r.wait(lambda s: s["entries"] == 2)
    await r.exchange(32)
    await asyncio.sleep(8)
    await r.nft(f"add flowtable inet {TABLE} fast {{ hook ingress priority 0; "
                f"devices = {{ {TARGET_LAN_IF}, {TARGET_WAN_IF} }}; flags offload; counter; }}")
    listed = await command(r.target, r.session, "nft", "list", "flowtable", "inet", TABLE, "fast")
    assert "counter" in listed["stdout"], listed
    enabled = await r.state()
    counted = await ct_counts(r)
    tx_before = await kernel_tx_packets(r.target, r.session, TARGET_LAN_IF)
    count, payload = 64, 256
    await r.exchange(count, payload_size=payload)
    tx_after = await kernel_tx_packets(r.target, r.session, TARGET_LAN_IF)
    after = await r.state()
    expected = [(packets + count, octets + count * (payload + 8 + 20)) for packets, octets in counted]
    accounted, deadline = counted, time.monotonic() + 15
    while time.monotonic() < deadline:
        accounted = await ct_counts(r)
        if all(now[0] >= wanted[0] for now, wanted in zip(accounted, expected)):
            break
        await asyncio.sleep(0.5)
    r.record("counter-enabled-live", {"installed": installed, "enabled": enabled, "after": after,
                                      "conntrack_before": counted, "conntrack_after": accounted,
                                      "expected": expected, "software_lan_tx": tx_after - tx_before})
    for state in (enabled, after):
        assert state["entries"] == state["bindings"] == 2, state
        assert state["invalidated"] == state["invalidation_done"] == state["fatal"] == 0, state
        assert (state["installs"], state["deletes"], state["rearms"], state["errors"]) == (
            installed["installs"], installed["deletes"], installed["rearms"], installed["errors"]), (installed, state)
    old, new = {f["in"]: f for f in enabled["flows"]}, {f["in"]: f for f in after["flows"]}
    assert {f["in"]: f["cookie"] for f in installed["flows"]} == {i: f["cookie"] for i, f in new.items()}
    for ingress, flow in new.items():
        assert int(flow["packets"]) - int(old[ingress]["packets"]) == count, (enabled, after)
    assert 0 <= tx_after - tx_before < count // 2, (tx_before, tx_after)
    assert accounted == expected, (counted, accounted, expected)


async def test_partial_accounting(rig):
    """The hardware half of a partially offloaded flow reaches conntrack while
    its software half is still forwarding.

    A host route smaller than a full frame keeps the UDP upload in Linux, and
    the download goes to hardware. Linux refreshes the flow's timeout on every
    upload packet, and the flowtable collector used to ask the hardware only
    once a tenth of that timeout had run down -- which it never does while the
    upload flows -- so the download's bytes reached conntrack only after the
    upload stopped, and the adapter's statistics pass, with the neighbour
    keepalive it carries, never ran for it. The collector now also asks a flow
    with a direction in hardware once that period has passed since it last
    asked. So conntrack's reply direction has to grow mid-exchange, in
    Netfilter's units: 284 bytes per 256-byte datagram."""
    r = rig
    if r.proto != "udp":
        pytest.skip("a TCP direction is carried into the smaller path")
    if (await r.state())["observe"]:
        pytest.skip("requires installed hardware")
    timeout = int((await read(r.target, r.session,
                              "/proc/sys/net/netfilter/nf_flowtable_udp_timeout")).strip())
    await command(r.target, r.session, "ip", "route", "replace", f"{WAN_IP}/32",
                  "dev", TARGET_WAN_IF, "mtu", "1400")
    try:
        await r.table(counter=True)
        await r.clear_ct()
        payload, datagram = 256, 256 + 8 + 20
        initial = await r.state()
        await r.exchange(16, payload_size=payload)
        admitted = await r.wait(lambda s: s["entries"] == 1 and s["rejects"] > initial["rejects"])
        assert [f["in"] for f in admitted["flows"]] == [TARGET_WAN_IF], admitted
        before = await ct_counts(r)
        # Two statistics periods and some, with an upload packet every 50 ms
        # refreshing the timeout throughout.
        period = max(1, timeout // 10)
        window = 2 * period + 6
        exchange = asyncio.create_task(r.exchange(int((window + 4) / 0.05), interval=0.05,
                                                  payload_size=payload))
        grown, samples = None, []
        try:
            deadline = time.monotonic() + window
            while time.monotonic() < deadline and not exchange.done():
                now = await ct_counts(r)
                samples.append(now)
                if now[1][1] - before[1][1] >= 20 * datagram and not exchange.done():
                    grown = now
                    break
                await asyncio.sleep(1)
        finally:
            report = await exchange
        after = await r.state()
        r.record("partial-accounting", {"timeout": timeout, "admitted": admitted,
                                        "before": before, "samples": samples,
                                        "after": after, "exchange": report})
        assert grown, ("the download's hardware bytes did not reach conntrack while the "
                       "upload forwarded in software", before, samples, after)
        # Whole datagrams, in Netfilter's units.
        packets, octets = grown[1][0] - before[1][0], grown[1][1] - before[1][1]
        assert octets == packets * datagram, (before, grown)
        assert after["busy"] == admitted["busy"] and after["entries"] == 1, (admitted, after)
        assert after["installs"] == admitted["installs"], (admitted, after)
    finally:
        await command(r.target, r.session, "ip", "route", "replace", f"{WAN_IP}/32",
                      "dev", TARGET_WAN_IF)


# The two 10G receive ports' enable bits, read through each port's own ioctl
# over the console: a terminal failure stops classification there while a
# fixed link keeps its carrier, and takes the agent's path with it.


@pytest.mark.skipif(os.environ.get("ASK_FLOWTABLE_TERMINAL") not in {"unload", "budget"},
                    reason="explicit terminal lifecycle test; fresh boot required")
async def test_terminal(rig):
    """What still ends in a reboot: unloading CDX, and an unproven deletion
    once the restart budget is spent. The second leaves the datapath stopped
    for good -- every check of the window a restart would end, with no
    restart -- and CDX's unload then gives the ports back to Linux."""
    from _flowtable_restart import LIMIT, UNICAST_FAULT, knob, require_knobs, timed_restart, write
    r = rig
    kind = os.environ["ASK_FLOWTABLE_TERMINAL"]
    assert not (await r.state())["observe"]
    if kind == "budget":
        await require_knobs(r.target, r.session, UNICAST_FAULT)
    r.recovery_console = Console.target(log_path=str(artifact_dir() / "terminal-uart.log"))
    con = r.recovery_console
    await asyncio.to_thread(con.login, "root", None)
    # Recovery reports from a worker after the delete has returned, and a
    # printk landing inside a console read corrupts that read. dmesg keeps
    # every line for the assertions; the level is put back at the end.
    printk = (await console_command(con, "cat", "/proc/sys/kernel/printk"))["stdout"].split()
    await console_command(con, "sysctl", "-w", "kernel.printk=1 4 1 7")
    if kind == "budget":
        # A fresh boot has restarted nothing, so with a budget of one the
        # first unproven deletion restarts the datapath and the second is
        # for the reboot. The fixture puts the limit back if CDX is still
        # loaded by then.
        r.restart_limit = await knob(con, LIMIT)
        await write(con, LIMIT, 1)
        await r.table()
        await r.exchange(64)
        await r.wait(lambda s: s["entries"] == 2)
        r.record("budget-first", await timed_restart(con, ["nft", "delete", "table", "inet", TABLE]))
        await r.clear_ct()
    await r.table()
    await r.exchange(128)
    initial = await r.wait(lambda s: s["entries"] == 2)
    assert all(int(f["packets"]) > 0 for f in initial["flows"]), initial
    baseline = len(r.echo.received)
    # The sender has to outlast every console check before the stopped-port
    # observation at the end: about 45 s of UART round trips for budget.
    traffic = asyncio.create_task(terminal_stream(r, duration=75 if kind == "budget" else 12))
    unloaded = False
    try:
        deadline = time.monotonic() + 5
        while len(r.echo.received) < baseline + 32:
            assert not traffic.done(), "traffic stopped before terminal operation"
            assert time.monotonic() < deadline, "terminal stream did not reach WAN"
            await asyncio.sleep(0.05)
        live = await r.state()
        assert live["entries"] == 2 and all(
            int(after["packets"]) > int(before["packets"])
            for before, after in zip(initial["flows"], live["flows"])
        ), live
        r.record(f"{kind}-live", live)
        if kind == "budget":
            # Read physical receive-port enable state, not netdev carrier:
            # fixed links can retain carrier while classification is stopped.
            before_ports = await console_python(con, RX_PORTS_SCRIPT)
            assert json.loads(before_ports["stdout"]) == {"6": 1, "7": 1}
            await console_python(con, "from pathlib import Path; Path('/sys/module/cdx/parameters/flowtable_fail_unlink').write_text('1')")
            await console_command(con, "nft", "delete", "table", "inet", TABLE)
            deadline = time.monotonic() + 10
            while True:
                result = await console_command(con, "cat", "/proc/cdx_flowtable")
                stopped = status_text(result["stdout"].strip())
                if stopped["invalidation_done"]:
                    break
                assert time.monotonic() < deadline, stopped
                await asyncio.sleep(0.1)
            assert stopped["fatal"] == stopped["invalidated"] == stopped["fatal_terminal"] == 1, stopped
            assert stopped["restarts"] == live["restarts"], stopped
            assert stopped["errors"] - live["errors"] == 1, stopped
            assert stopped["entries"] == stopped["bindings"] == stopped["quarantine"] == 0, stopped
            assert stopped["rearm_ready"] == 0, stopped
            # A hardware table can still be created -- refusing it would fail
            # a consumer's whole firewall transaction -- but its ports are
            # bound passively: no binding, no admission, no rearm.
            await console_command(con, "nft", "add", "table", "inet", TABLE)
            attempted = await console_command(con, "nft", f"add flowtable inet {TABLE} fast {{ "
                                  f"hook ingress priority 0; devices = {{ {TARGET_LAN_IF}, {TARGET_WAN_IF} }}; "
                                  "flags offload; }", check=False)
            assert attempted["rc"] == 0, attempted
            refused = status_text((await console_command(con, "cat", "/proc/cdx_flowtable"))["stdout"].strip())
            assert refused["fatal"] == refused["invalidated"] == refused["invalidation_done"] == 1
            assert refused["bindings"] == refused["parked"] == refused["entries"] == refused["rearm_ready"] == 0
            assert refused["passive"] == stopped["passive"] + 2, (stopped, refused)
            assert refused["rearms"] == live["rearms"] and refused["errors"] == stopped["errors"]
            r.record("budget-rearm-passive", {"state": refused, "nft": attempted})
            await console_command(con, "nft", "delete", "table", "inet", TABLE)
            ports = await console_python(con, RX_PORTS_SCRIPT)
            assert json.loads(ports["stdout"]) == {"6": 0, "7": 0}, ports
            fault = await console_command(con, "cat", "/sys/module/cdx/parameters/flowtable_fail_unlink")
            assert fault["stdout"].strip() == "N", fault
            log = (await console_command(con, "dmesg"))["stdout"]
            assert log.count("hardware stopped after unproven deletion; reboot required "
                             "(restart budget exhausted)") == 1, log
            r.record("budget-stopped", {"state": stopped, "ports": json.loads(ports["stdout"]), "dmesg": log})
            mtu_checks = []
            for dev in (TARGET_LAN_IF, TARGET_WAN_IF):
                original_mtu = (await console_command(con, "cat", f"/sys/class/net/{dev}/mtu"))["stdout"].strip()
                try:
                    await console_command(con, "ip", "link", "set", "dev", dev, "mtu", "1400")
                finally:
                    await console_command(con, "ip", "link", "set", "dev", dev, "mtu", original_mtu)
                held = status_text((await console_command(con, "cat", "/proc/cdx_flowtable"))["stdout"].strip())
                for field in ("fatal", "invalidated", "invalidation_done", "rearm_ready", "entries", "bindings",
                              "installs", "deletes", "rearms", "errors", "quarantine"):
                    assert held[field] == stopped[field], (field, held, stopped)
                ports = json.loads((await console_python(con, RX_PORTS_SCRIPT))["stdout"])
                assert ports == {"6": 0, "7": 0}, ports
                mtu_checks.append({"dev": dev, "state": held, "ports": ports})
            r.record("budget-mtu-refused", mtu_checks)
            restart_checks = []
            for dev in (TARGET_LAN_IF, TARGET_WAN_IF):
                await console_command(con, "ip", "link", "set", "dev", dev, "down")
                restart = await console_command(con, "ip", "link", "set", "dev", dev, "up", check=False)
                assert restart["rc"] != 0 and "Input/output error" in restart["stdout"], restart
                link = json.loads((await console_command(con, "ip", "-j", "link", "show", "dev", dev))["stdout"])[0]
                assert "UP" not in link["flags"], link
                ports = json.loads((await console_python(con, RX_PORTS_SCRIPT))["stdout"])
                assert ports == {"6": 0, "7": 0}, ports
                restart_checks.append({"dev": dev, "restart": restart, "ports": ports})
            r.record("budget-port-restart-refused", restart_checks)
            # Allow already queued datagrams to arrive, then prove ingress
            # remains stopped while the LAN sender is still running.
            await asyncio.sleep(0.2)
            received = len(r.echo.received)
            assert not traffic.done(), "traffic ended before stopped-port observation"
            await asyncio.sleep(1)
            assert len(r.echo.received) == received, "traffic passed stopped classifier ports"
        else:
            await console_command(con, "rmmod", "ask_flowtable", timeout=25)
            await console_command(con, "rmmod", "cdx", timeout=25)
            unloaded = True
        r.record(f"{kind}-traffic", await traffic)
    finally:
        # Finish the LAN script before fixture cleanup changes its network.
        try:
            await traffic
        finally:
            if kind == "budget" and not unloaded:
                await console_command(con, "rmmod", "ask_flowtable", timeout=25)
                # The fatal latch belongs to the still-loaded provider. A
                # fresh consumer must not turn an unproven deletion healthy.
                refused = await console_command(con, "modprobe", "ask_flowtable", check=False)
                assert refused["rc"] != 0 and "Operation not supported" in refused["stdout"], refused
                await console_command(con, "test", "-e", "/sys/module/cdx")
                for path in ("/sys/module/ask_flowtable", "/proc/cdx_flowtable",
                             "/sys/module/cdx/holders/ask_flowtable"):
                    assert (await console_command(con, "test", "-e", path, check=False))["rc"] == 1, path
                r.record("budget-module-reload-refused", refused)
                for dev in (TARGET_LAN_IF, TARGET_WAN_IF):
                    restart = await console_command(con, "ip", "link", "set", "dev", dev, "up", check=False)
                    assert restart["rc"] != 0 and "Input/output error" in restart["stdout"], restart
                ports = json.loads((await console_python(con, RX_PORTS_SCRIPT))["stdout"])
                assert ports == {"6": 0, "7": 0}, ports
                r.record("budget-provider-guard-retained", {"ports": ports, "adapter_absent": True})
                await console_command(con, "rmmod", "cdx", timeout=25)
                unloaded = True
    for dev in (TARGET_LAN_IF, TARGET_WAN_IF):
        await console_command(con, "ip", "link", "set", "dev", dev, "up")
    # DOWN can discard the fixture's /32 routes and permanent neighbours.
    # Restore these before its normal undo actions and software proof.
    for address, mac, dev in ((r.lan_ip, r.lan_mac, TARGET_LAN_IF),
                              (WAN_IP, r.wan_mac, TARGET_WAN_IF)):
        await console_command(con, "ip", "route", "replace", address + "/32", "dev", dev)
        await console_command(con, "ip", "neigh", "replace", address, "lladdr", mac,
                              "nud", "permanent", "dev", dev)
    absent = await console_command(con, "test", "-e", "/sys/module/cdx", check=False)
    assert absent["rc"] == 1, absent
    assert (await console_command(con, "test", "-e", "/proc/cdx_flowtable", check=False))["rc"] == 1
    await console_command(con, "nft", "delete", "table", "inet", TABLE, check=False)
    await r.clear_ct()
    await r.exchange(64)
    r.record(f"{kind}-complete", {"module_absent": True, "post_unload_echoes": 64,
                                "boot_id": await read(r.target, r.session, "/proc/sys/kernel/random/boot_id")})
    await console_command(con, "sysctl", "-w", "kernel.printk=" + " ".join(printk[:4]))


async def test_unproven_delete_restarts(rig):
    """A unicast delete CDX cannot prove stops the datapath, and CDX restarts
    it in the same boot.

    The key may still be linked, so the latch stops every classifier port;
    with the ports idle CDX settles the key, and the ports start again. Held
    by the test image's knob, the stopped window shows what the latch
    promises: nothing forwarded, no port opened, no adapter loaded, Linux's own
    configuration changes taken without effect. Released, the latch clears,
    the restart is counted and logged with the key it settled, the adapter
    loads, and the flow goes back into hardware with exact counts. A restart
    nothing holds comes back within the bound."""
    from _flowtable_restart import (HOLD, RUNNING, STOPPED, UNICAST_FAULT, assert_port_start_refused,
                                    assert_restarted_cleanly, knob, log_marks, ports, proc,
                                    quiet_console, require_knobs, restart_budget, restart_counts,
                                    timed_restart, wait_restarted, wait_running, wait_stopped, write)
    r = rig
    await require_knobs(r.target, r.session, UNICAST_FAULT)
    assert not (await r.state())["observe"]
    r.recovery_console = con = Console.target(log_path=str(artifact_dir() / "restart-uart.log"))
    await asyncio.to_thread(con.login, "root", None)
    # The case restarts twice, under a budget of its own that is let go of
    # first, then its links brought back, then the console's kernel messages.
    async with quiet_console(con), links_restored(r, con), restart_budget(con, r, (UNICAST_FAULT,)):
        await r.table()
        await r.exchange(128)
        initial = await r.wait(lambda s: s["entries"] == 2)
        assert all(int(f["packets"]) > 0 for f in initial["flows"]), initial
        marks = await log_marks(con)
        baseline = len(r.echo.received)
        # Outlasts the window's console checks, and then shows forwarding
        # back after the restart.
        traffic = asyncio.create_task(terminal_stream(r, duration=75))
        try:
            deadline = time.monotonic() + 5
            while len(r.echo.received) < baseline + 32:
                assert not traffic.done(), "traffic stopped before the failed delete"
                assert time.monotonic() < deadline, "the stream did not reach WAN"
                await asyncio.sleep(0.05)
            live = await r.state()
            assert live["entries"] == 2 and all(
                int(after["packets"]) > int(before["packets"])
                for before, after in zip(initial["flows"], live["flows"])
            ), live
            assert await ports(con) == RUNNING
            await write(con, HOLD, 1)
            await write(con, UNICAST_FAULT, 1)
            await console_command(con, "nft", "delete", "table", "inet", TABLE)
            stopped, _ = await wait_stopped(con, live)
            deadline = time.monotonic() + 10
            while not stopped["invalidation_done"]:
                assert time.monotonic() < deadline, stopped
                await asyncio.sleep(0.1)
                stopped = await proc(con)
            assert stopped["invalidated"] == 1 and stopped["errors"] - live["errors"] == 1, stopped
            assert stopped["entries"] == stopped["bindings"] == stopped["quarantine"] == 0, stopped
            assert stopped["rearm_ready"] == 0, stopped
            assert await knob(con, UNICAST_FAULT) == "N"
            # The stopped ports carry nothing while the sender keeps sending.
            await asyncio.sleep(0.2)
            received = len(r.echo.received)
            assert not traffic.done(), "traffic ended before the stopped window"
            await asyncio.sleep(1)
            assert len(r.echo.received) == received, "traffic passed stopped classifier ports"
            # Linux's own configuration is Linux's: an MTU change is taken
            # and leaves the latch, the adapter and the ports as they were.
            mtu_checks = []
            for dev in (TARGET_LAN_IF, TARGET_WAN_IF):
                original_mtu = (await console_command(con, "cat", f"/sys/class/net/{dev}/mtu"))["stdout"].strip()
                try:
                    await console_command(con, "ip", "link", "set", "dev", dev, "mtu", "1400")
                finally:
                    await console_command(con, "ip", "link", "set", "dev", dev, "mtu", original_mtu)
                held = await proc(con)
                for field in ("fatal", "fatal_terminal", "restarts", "invalidated", "invalidation_done",
                              "rearm_ready", "entries", "bindings", "installs", "deletes", "rearms",
                              "errors", "quarantine"):
                    assert held[field] == stopped[field], (field, held, stopped)
                assert await ports(con) == STOPPED
                mtu_checks.append({"dev": dev, "state": held})
            # No port opens under the latch, and no adapter claims it:
            # unloading one works, loading one is refused until the restart.
            refused_ports = await assert_port_start_refused(con, (TARGET_LAN_IF, TARGET_WAN_IF))
            await console_command(con, "rmmod", "ask_flowtable", timeout=25)
            reload = await console_command(con, "modprobe", "ask_flowtable", check=False, timeout=30)
            assert reload["rc"] != 0 and "Operation not supported" in reload["stdout"], reload
            assert await ports(con) == STOPPED
            r.record("restart-stopped", {"live": live, "stopped": stopped, "mtu": mtu_checks,
                                         "ports": refused_ports, "reload": reload})
            # Released: CDX settles the key and restarts, and the adapter
            # loads again once it has.
            restarted = await wait_restarted(con, live, adapter=False)
            assert restarted["entries"] == restarted["bindings"] == restarted["quarantine"] == 0, restarted
            for dev in (TARGET_LAN_IF, TARGET_WAN_IF):
                await console_command(con, "ip", "link", "set", "dev", dev, "up")
            # DOWN can discard the fixture's /32 routes and permanent
            # neighbours; they come back before the agent is used again.
            for address, mac, dev in ((r.lan_ip, r.lan_mac, TARGET_LAN_IF),
                                      (WAN_IP, r.wan_mac, TARGET_WAN_IF)):
                await console_command(con, "ip", "route", "replace", address + "/32", "dev", dev)
                await console_command(con, "ip", "neigh", "replace", address, "lladdr", mac,
                                      "nud", "permanent", "dev", dev)
            assert await wait_running(con) == RUNNING
            line = await assert_restarted_cleanly(con, marks)
            resolved, released, _ = restart_counts(line)
            assert resolved == 1 and released == 0, line
            # Forwarding resumes, in software until the table is back.
            resumed, deadline = len(r.echo.received), time.monotonic() + 10
            while len(r.echo.received) < resumed + 16:
                assert not traffic.done(), "traffic ended before forwarding resumed"
                assert time.monotonic() < deadline, "forwarding did not resume after the restart"
                await asyncio.sleep(0.1)
            r.record("restart-resumed", {"state": restarted, "log": line})
        finally:
            # Finish the LAN script before clearing its traffic state.
            r.record("restart-traffic", await traffic)
        # The flow goes back into hardware, exactly counted, with nothing left
        # parked and the restart counted once.
        await r.clear_ct()
        await r.table()
        await r.exchange(64)
        await r.wait(lambda s: s["entries"] == 2)
        proven = await hardware_proof(r)
        assert proven["fatal"] == proven["quarantine"] == 0, proven
        assert proven["restarts"] == live["restarts"] + 1, proven
        # A restart nothing holds comes back within the bound, and the flow
        # with it once the table is back.
        timed = await timed_restart(con, ["nft", "delete", "table", "inet", TABLE])
        await assert_restarted_cleanly(con, marks, restarts=2)
        await r.clear_ct()
        await r.table()
        await r.exchange(64)
        await r.wait(lambda s: s["entries"] == 2)
        final = await hardware_proof(r)
        assert final["restarts"] == live["restarts"] + 2 and final["fatal"] == 0, final
        assert final["resume_failures"] == live["resume_failures"], final
        r.record("restart-complete", {"proven": proven, "timed": timed, "final": final,
                                      "boot_id": await read(r.target, r.session,
                                                            "/proc/sys/kernel/random/boot_id")})
