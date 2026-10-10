"""Shared support for flowtable mtu."""

from __future__ import annotations

import asyncio
import json
import struct
import threading
import time

import pytest
from _flowtable_connections import by_key, healthy
from _flowtable_rig import (
    DPORT,
    SPORT,
    TABLE,
    WAN_IP,
    artifact_dir,
    command,
    ct_listing,
    read,
)
from _flowtable_tcp import software_tx
from _topology import FULL_FRAME, TARGET_LAN_IF, TARGET_WAN_IF, lan_run_python

# A path below a full frame on either port, built by the cases that need one.
SMALL_PATH = 1200


def udp_ingresses(mtus):
    """The ports whose arriving UDP direction hardware carries at these MTUs.

    `mtus` maps each port to its MTU, which is also the path MTU of a
    direction leaving by it. The microcode fragments a non-DF IPv4 packet
    larger than its entry's MTU, and fragments it builds from a frame an
    Ethernet port received have zero-filled payloads, so the adapter installs
    a non-TCP direction only where its path carries the largest packet its
    ingress port can deliver. Every other one stays in Linux, which fragments
    correctly or answers DF with Fragmentation Needed."""
    return {dev for dev, out in ((TARGET_LAN_IF, TARGET_WAN_IF), (TARGET_WAN_IF, TARGET_LAN_IF))
            if mtus[out] >= max(mtus[dev], FULL_FRAME)}


def carried(flows, mtus):
    """`flows` with each UDP spec naming the directions Linux keeps at `mtus`."""
    kept = tuple(sorted({TARGET_LAN_IF, TARGET_WAN_IF} - udp_ingresses(mtus)))
    return [{**f, "software": kept} if f["proto"] == "udp" else f for f in flows]


async def table_identity(r):
    result = await command(r.target, r.session, "nft", "-a", "-j", "list", "table", "inet", TABLE)
    tables = [item["flowtable"] for item in json.loads(result["stdout"])["nftables"] if "flowtable" in item]
    assert len(tables) == 1 and "handle" in tables[0], result
    return tables[0]


def current_mtus(state, mtus):
    healthy(state)
    assert state["handle_refs"] == state["entries"], state
    assert all(int(f["mtu"]) == mtus[f["out"]] for f in state["flows"]), (mtus, state)


async def udp_warm(r, sport, mtus):
    """Exchange until the UDP tuple holds exactly the directions `mtus` admits."""
    wanted = udp_ingresses(mtus)
    for _ in range(8):
        await r.exchange(64, sport=sport, promiscuous=False)
        state = await r.state()
        if (sorted(f["in"] for f in state["flows"]) == sorted(wanted)
                and all(f["proto"] == "17" for f in state["flows"])):
            current_mtus(state, mtus)
            return state
    pytest.fail(f"UDP was not re-admitted on {sorted(wanted)}: {state}")


async def udp_size(r, sport, size, label, mtus=None):
    """256 datagrams of `size` bytes each way. The directions `mtus` admits
    (both, when it is not given) are counted by the classifier; one Linux
    keeps crosses in software and is counted by the port it leaves."""
    wanted = udp_ingresses(mtus) if mtus else {TARGET_LAN_IF, TARGET_WAN_IF}
    before, tx_before = await r.state(), await software_tx(r)
    # PROBE bypasses a PMTU learned by this endpoint during earlier changes;
    # the packet must reach the DUT to test its current MTU.
    reports = await r.exchange(256, payload_size=size, sport=sport,
                               ignore_pmtu=True, promiscuous=False)
    after, tx_after = await r.state(), await software_tx(r)
    healthy(after)
    if len(wanted) < 2:
        # The direction Linux keeps re-offers the flow about once a second.
        # Its refusal is decided before RTNL and the installed direction's
        # offer is answered without it, so nothing here should take RTNL at
        # all; busy moving would name an offer that did, before anything
        # below could only report what it cost.
        assert after["busy"] == before["busy"], ("an offer took RTNL mid-burst", before, after)
    assert before["installs"] == after["installs"] and before["deletes"] == after["deletes"], (before, after)
    old, new = by_key(before), by_key(after)
    assert old.keys() == new.keys() and sorted(key[0] for key in new) == sorted(wanted), (before, after)
    for key in old:
        assert old[key]["cookie"] == new[key]["cookie"], (key, before, after)
        assert int(new[key]["packets"]) - int(old[key]["packets"]) == 256
        assert int(new[key]["bytes"]) - int(old[key]["bytes"]) == 256 * (size + 42)
    tx = {dev: tx_after[dev] - tx_before[dev] for dev in tx_before}
    # A direction Linux keeps leaves by the port it did not arrive on.
    software = {dev: 0 for dev in tx}
    for ingress in {TARGET_LAN_IF, TARGET_WAN_IF} - wanted:
        software[TARGET_WAN_IF if ingress == TARGET_LAN_IF else TARGET_LAN_IF] += 256
    assert software[TARGET_LAN_IF] <= tx[TARGET_LAN_IF] <= 64 + software[TARGET_LAN_IF], tx
    assert software[TARGET_WAN_IF] <= tx[TARGET_WAN_IF] <= 128 + software[TARGET_WAN_IF], tx
    r.record(label, {"before": before, "after": after, "software_tx": tx, "transfers": reports})


async def fragmenter(r):
    """The microcode fragmenter's cumulative counters, by their proc labels."""
    text = await read(r.target, r.session, "/proc/ucode_frag/stats")
    return {label.strip(): int(value) for label, value in
            (line.rsplit(":", 1) for line in text.splitlines() if ":" in line)}


async def linux_fragments(r):
    from scapy.all import IP, AsyncSniffer, Ether, wrpcap
    initial = await r.state()
    await r.table()
    await r.exchange()
    # Both directions are offered together: the upload is refused and the
    # download, whose path carries a full frame, is installed.
    admitted = await r.wait(lambda s: s["entries"] == 1 and s["rejects"] > initial["rejects"])
    reverse, = admitted["flows"]
    assert (reverse["in"], reverse["out"]) == (TARGET_WAN_IF, TARGET_LAN_IF), admitted
    assert int(reverse["mtu"]) == r.port_mtu, admitted
    count, size = 64, 1250
    payloads = [struct.pack("!Q", n) + b"ASK-fragment".ljust(size - 8, b".") for n in range(count)]
    script = f'''
import json, socket, struct, time
s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
s.setsockopt(socket.SOL_IP, 10, 4)  # IP_MTU_DISCOVER: IP_PMTUDISC_INTERFACE, DF clear
s.bind(({r.lan_ip!r}, {SPORT}))
for n in range({count}):
    s.sendto(struct.pack('!Q', n) + b'ASK-fragment'.ljust({size - 8}, b'.'), ({WAN_IP!r}, {DPORT}))
    time.sleep(0.01)
s.close()
print(json.dumps({{'sent': {count}}}))
'''
    before, counters_before = await r.state(), await fragmenter(r)
    ready = threading.Event()
    sniffer = AsyncSniffer(iface=r.wan_if, filter=f"ip src {r.lan_ip} and ip dst {WAN_IP}",
                           store=True, started_callback=ready.set)
    sniffer.start()
    r.echo.reply = False
    try:
        assert await asyncio.to_thread(ready.wait, 5), "endpoint capture did not start"
        # Management traffic also leaves by the WAN port, so nothing but the
        # counter reads themselves sits between these two.
        tx_before = await software_tx(r)
        result = await lan_run_python(r.lan, script, timeout=20, label="flowtable_fragment")
        assert result.rc == 0, result.stdout
        deadline = time.monotonic() + 5
        while sum(r.echo.received[p] for p in payloads) < count and time.monotonic() < deadline:
            await asyncio.sleep(0.1)
        await asyncio.sleep(0.5)
        tx_after = await software_tx(r)
    finally:
        r.echo.reply = True
        frames = sniffer.stop()
        artifact_dir().mkdir(parents=True, exist_ok=True)
        wrpcap(str(artifact_dir() / "fragments-non-df.pcap"), frames)
    after, counters_after = await r.state(), await fragmenter(r)
    listing = await ct_listing(r)
    moved = {label: counters_after[label] - counters_before[label] for label in counters_before}
    r.record("fragments-non-df", {"before": before, "after": after, "fragmenter": moved,
                                  "software_tx": {d: tx_after[d] - tx_before[d] for d in tx_before},
                                  "conntrack": listing, "frames": len(frames)})
    # The endpoint reassembled each packet, once.
    assert [r.echo.received[p] for p in payloads] == [1] * count, [r.echo.received[p] for p in payloads]
    # The microcode fragmented nothing: no entry could have asked it to.
    assert moved == {label: 0 for label in counters_before}, moved
    # The download's entry is untouched: the same generation, and no traffic
    # of its own, since the endpoint did not answer.
    assert (after["installs"], after["deletes"], after["entries"]) == (
        before["installs"], before["deletes"], 1), (before, after)
    old, new = before["flows"][0], after["flows"][0]
    assert (new["in"], new["cookie"], new["packets"]) == (old["in"], old["cookie"], old["packets"]), (old, new)
    # Linux sent every fragment itself.
    assert tx_after[TARGET_WAN_IF] - tx_before[TARGET_WAN_IF] >= 2 * count, (tx_before, tx_after)
    # The wire: two fragments per packet, each within the path MTU, DF clear,
    # offsets contiguous, forwarded once with the DUT's own addressing -- and
    # between them the datagram's own header and payload, byte for byte,
    # which is exactly what the microcode's fragments of such a frame lack.
    groups = {}
    for frame in frames:
        if IP in frame:
            groups.setdefault(frame[IP].id, []).append(frame)
    assert len(groups) == count, sorted(groups)
    serials = set()
    for ident, fragments in groups.items():
        assert len(fragments) == 2, (ident, [f.summary() for f in fragments])
        first, last = sorted(fragments, key=lambda f: f[IP].frag)
        assert (first[IP].frag, int(first[IP].flags), int(last[IP].flags)) == (0, 1, 0), (first, last)
        assert last[IP].frag * 8 == first[IP].len - 4 * first[IP].ihl, (first, last)
        assert sum(f[IP].len - 4 * f[IP].ihl for f in fragments) == 8 + size, (first, last)
        for fragment in fragments:
            assert fragment[IP].len <= SMALL_PATH and fragment[IP].ttl == 63, fragment.summary()
            assert (fragment[Ether].src, fragment[Ether].dst) == (r.dut_wan_mac, r.wan_mac), fragment.summary()
            header = fragment[IP].copy()
            del header.chksum
            assert IP(bytes(header)).chksum == fragment[IP].chksum, fragment.summary()
        datagram = b"".join(bytes(f[IP])[4 * f[IP].ihl:f[IP].len] for f in (first, last))
        assert struct.unpack("!HHH", datagram[:6]) == (SPORT, DPORT, 8 + size), (ident, datagram[:8].hex())
        serial = struct.unpack("!Q", datagram[8:16])[0]
        assert serial < count and datagram[8:] == payloads[serial], (ident, datagram[8:40].hex())
        serials.add(serial)
    assert serials == set(range(count)), sorted(set(range(count)) - serials)
