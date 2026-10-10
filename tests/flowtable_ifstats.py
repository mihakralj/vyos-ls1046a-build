"""Interface counters for offloaded traffic, read where an operator reads them.

An offloaded frame never reaches the CPU, so nothing the driver counts sees it.
What `ip -s link` and /proc/net/dev show for a port or a VLAN device instead
comes from the firmware's own per-interface records, folded into the device's
counters by dev_get_stats() in the device's own units. These cases send a burst
of known size through a tagged LAN and require that:

- the firmware records count every frame of the burst, with the framing the
  hardware was measured to count -- the frame as it stands once the record's
  own tag has been handled;
- the devices' native counters move by the burst restated into their units,
  while the driver's own software counters (ethtool -S) do not, which is what
  says the frames went through hardware;
- the two native surfaces agree with each other;
- and a frame the flowtable forwards in software is counted by the driver too,
  which it was not before the stack's return value stopped being read as a
  drop report;
- and a device's record stays out of the pool while a hardware entry that may
  still write it is unproven, so the next device is handed a sound one.
"""
from __future__ import annotations

import asyncio
import json
import re

import pytest

from _topology import TARGET_LAN_IF, TARGET_WAN_IF, TopologyStack, dut_vlan_subif
from ask_orch.uart import Console
from _flowtable_rig import artifact_dir, TABLE, command, console_command, read, rearm_ready
from _flowtable_vlan import (DUT_VLAN_ADDR, VLAN_ID, _both_directions)

PAYLOAD = 256
COUNT = 64
# The frame the LAN peer sends: Ethernet, IP and UDP headers around the payload,
# without the FCS, plus one tag per level of the stack.
IP_LEN = 20 + 8 + PAYLOAD
ETH_HLEN, VLAN_HLEN = 14, 4
# Frames that are not the burst -- ARP, the rig's own probes -- also cross the
# devices while the counters are read. They are few, and each is at most one
# frame long.
STRAY_LIMIT, FRAME_MAX = 12, 1518
# CDX's debug knob withholding proofs that hardware entries are gone: each
# barrier a retirement asks for reports failure, retries included, until the
# count is spent. Absent from a production build.
FAIL_SYNC = "/sys/module/cdx/parameters/flowtable_fail_sync"
# The barrier the two directions' unlinks owe -- one, or one each when Linux's
# own cleanup and the adapter's retirement each reach one first -- then the
# retries the invalidation worker makes once a second: the entries stay
# unproven for about three seconds, long past the device's unregistration and
# the reap that follows it.
WITHHELD_PROOFS = 5


@pytest.fixture
def offload_service_stopped():
    """The default-on offload service binds the catch-all at boot; the VLAN rig
    asserts an unbound adapter, and only the offload test's own fixture stops
    the service. Stop it here too, over the console the init script needs."""
    async def stop():
        with Console.target(log_path=str(artifact_dir() / "ifstats-daemon-stop.log")) as con:
            await asyncio.to_thread(con.login, "root", None)
            await console_command(con, "/etc/init.d/ask-flowtable", "stop", check=False,
                                  timeout=45)
    asyncio.run(stop())


def _devices(r):
    devices = [TARGET_LAN_IF, TARGET_WAN_IF, r.dut_vlan_if]
    parent = r.dut_vlan_if.rsplit(".", 1)[0]
    if "." in parent:
        devices.append(parent)
    return devices


async def _snapshot(r):
    """Every surface at once: the native counters two ways, the driver's own
    software counters, and the firmware's raw records."""
    devices = _devices(r)
    links, procnetdev, ethtool = {}, {}, {}
    for dev in devices:
        link = json.loads((await command(r.target, r.session, "ip", "-s", "-j", "link", "show",
                                         "dev", dev))["stdout"])[0]["stats64"]
        links[dev] = {"rx_packets": link["rx"]["packets"], "rx_bytes": link["rx"]["bytes"],
                      "tx_packets": link["tx"]["packets"], "tx_bytes": link["tx"]["bytes"]}
    for line in (await read(r.target, r.session, "/proc/net/dev")).splitlines():
        name, _, rest = line.partition(":")
        if name.strip() in devices:
            fields = [int(x) for x in rest.split()]
            procnetdev[name.strip()] = {"rx_bytes": fields[0], "rx_packets": fields[1],
                                        "tx_bytes": fields[8], "tx_packets": fields[9]}
    for dev in (TARGET_LAN_IF, TARGET_WAN_IF):
        text = (await command(r.target, r.session, "ethtool", "-S", dev))["stdout"]
        ethtool[dev] = {k: int(v) for k, v in
                        re.findall(r"^\s*((?:rx|tx) packets \[TOTAL\]):\s*(\d+)", text, re.M)}
    state = await r.state()
    flows = {f["cookie"]: {"in": f["in"], "out": f["out"], "in_vlan": f["in_vlan"],
                           "out_vlan": f["out_vlan"], "packets": int(f["packets"]),
                           "bytes": int(f["bytes"])} for f in state["flows"]}
    vlans = {v["dev"]: {k: int(x) for k, x in v.items() if k.startswith(("rx_", "tx_"))}
             for v in state["vlans"]}
    return {"links": links, "procnetdev": procnetdev, "ethtool": ethtool, "flows": flows,
            "vlans": vlans, "state": {k: state[k] for k in ("vlan_records", "vlan_slots")}}


def _delta(before, after):
    out = {}
    for group in ("links", "procnetdev", "ethtool", "vlans"):
        out[group] = {dev: {k: after[group][dev][k] - before[group][dev][k]
                            for k in before[group][dev]} for dev in before[group]}
    out["flows"] = {c: {**{k: after["flows"][c][k] for k in ("in", "out", "in_vlan", "out_vlan")},
                        **{k: after["flows"][c][k] - before["flows"][c][k]
                           for k in ("packets", "bytes")}} for c in before["flows"]}
    return out


def _direction(flows, ingress):
    """The counter deltas of the one direction arriving on `ingress`."""
    matching = [f for f in flows.values() if f["in"] == ingress]
    assert len(matching) == 1, (ingress, flows)
    return {k: matching[0][k] for k in ("packets", "bytes")}


async def _burst(r):
    """Install the flow on a first exchange, then measure a second one."""
    await r.table()
    await r.exchange(count=4, payload_size=PAYLOAD)
    await _both_directions(r)
    before = await _snapshot(r)
    await r.exchange(count=COUNT, payload_size=PAYLOAD)
    after = await _snapshot(r)
    return before, after, _delta(before, after)


def _native(delta, dev, half, hw_packets, hw_bytes):
    """The native counters moved by the hardware's contribution plus whatever
    the CPU forwarded meanwhile. On a port the driver's own counter says how
    many frames that was -- a hardware frame never reaches it -- so the packet
    count is checked against the sum and the bytes against the sum's bounds.
    A VLAN device has no driver counter, so there the strays are bounded
    instead: a few frames, each at most one frame long. Not exact either way,
    because the surfaces are read a moment apart and the strays keep coming."""
    software = delta["ethtool"].get(dev, {}).get(f"{half} packets [TOTAL]")
    for surface in ("links", "procnetdev"):
        counters = delta[surface][dev]
        stray = counters[f"{half}_packets"] - hw_packets
        if software is None:
            assert 0 <= stray <= STRAY_LIMIT, (surface, dev, half, counters, hw_packets)
        else:
            assert abs(stray - software) <= STRAY_LIMIT // 2, \
                (surface, dev, half, counters, hw_packets, software)
        assert hw_bytes <= counters[f"{half}_bytes"] <= hw_bytes + max(stray, 0) * FRAME_MAX, \
            (surface, dev, half, counters, hw_bytes)
    ip, proc = delta["links"][dev], delta["procnetdev"][dev]
    assert abs(ip[f"{half}_packets"] - proc[f"{half}_packets"]) <= STRAY_LIMIT, (ip, proc)


async def test_vlan_and_ports(offload_service_stopped, vlan_rig):
    """One tag on the LAN: the port, the VLAN device and the untagged WAN port
    all account for the burst, in their own units, from the firmware alone."""
    r = vlan_rig
    before, after, delta = await _burst(r)
    tagged, untagged = IP_LEN + ETH_HLEN + VLAN_HLEN, IP_LEN + ETH_HLEN
    forward = _direction(delta["flows"], TARGET_LAN_IF)
    reverse = _direction(delta["flows"], TARGET_WAN_IF)
    # The classifier counted every frame of the burst, whole, on both sides.
    assert forward == {"packets": COUNT, "bytes": COUNT * tagged}, forward
    assert reverse == {"packets": COUNT, "bytes": COUNT * untagged}, reverse
    # The device's record: what the strip left and what the insert made. Both
    # read back through /proc/cdx_flowtable as the firmware keeps them.
    record = delta["vlans"][r.dut_vlan_if]
    assert record == {"rx_packets": COUNT, "rx_bytes": COUNT * untagged,
                      "tx_packets": COUNT, "tx_bytes": COUNT * tagged}, record
    assert after["state"] == {"vlan_records": 1, "vlan_slots": 1}, after["state"]
    # The native counters, restated into each device's own units: a port's
    # receive counter excludes the Ethernet header and its transmit counter
    # counts the frame whole; a VLAN device counts both ways without its own
    # tag, so its receive excludes the header and the tag and its transmit
    # excludes the tag.
    _native(delta, TARGET_LAN_IF, "rx", COUNT, COUNT * (tagged - ETH_HLEN))
    _native(delta, TARGET_LAN_IF, "tx", COUNT, COUNT * tagged)
    _native(delta, TARGET_WAN_IF, "rx", COUNT, COUNT * (untagged - ETH_HLEN))
    _native(delta, TARGET_WAN_IF, "tx", COUNT, COUNT * untagged)
    _native(delta, r.dut_vlan_if, "rx", COUNT, COUNT * IP_LEN)
    _native(delta, r.dut_vlan_if, "tx", COUNT, COUNT * untagged)
    # And none of it crossed the CPU: the driver's own counters on the tagged
    # port saw nothing of the burst.
    software = delta["ethtool"][TARGET_LAN_IF]
    assert software["rx packets [TOTAL]"] < COUNT // 2, software
    assert software["tx packets [TOTAL]"] < COUNT // 2, software
    r.record("ifstats-vlan", {"before": before, "after": after, "delta": delta})


@pytest.mark.parametrize("vlan_rig", ["qinq"], indirect=True)
async def test_qinq(offload_service_stopped, vlan_rig):
    """Two tags, two devices, each with the frame as its own counter would have
    it: the outer device sees the frame with the inner tag still on, the inner
    device sees it bare. That is what pins the order the records are listed in
    for each opcode."""
    r = vlan_rig
    inner_dev, outer_dev = r.dut_vlan_if, r.dut_vlan_if.rsplit(".", 1)[0]
    before, after, delta = await _burst(r)
    double, single, bare = IP_LEN + ETH_HLEN + 2 * VLAN_HLEN, IP_LEN + ETH_HLEN + VLAN_HLEN, \
        IP_LEN + ETH_HLEN
    forward = _direction(delta["flows"], TARGET_LAN_IF)
    assert forward == {"packets": COUNT, "bytes": COUNT * double}, forward
    assert delta["vlans"][outer_dev] == {"rx_packets": COUNT, "rx_bytes": COUNT * single,
                                         "tx_packets": COUNT, "tx_bytes": COUNT * double}, \
        delta["vlans"]
    assert delta["vlans"][inner_dev] == {"rx_packets": COUNT, "rx_bytes": COUNT * bare,
                                         "tx_packets": COUNT, "tx_bytes": COUNT * single}, \
        delta["vlans"]
    assert after["state"] == {"vlan_records": 2, "vlan_slots": 2}, after["state"]
    _native(delta, outer_dev, "rx", COUNT, COUNT * (single - ETH_HLEN))
    _native(delta, outer_dev, "tx", COUNT, COUNT * single)
    _native(delta, inner_dev, "rx", COUNT, COUNT * IP_LEN)
    _native(delta, inner_dev, "tx", COUNT, COUNT * bare)
    _native(delta, TARGET_LAN_IF, "rx", COUNT, COUNT * (double - ETH_HLEN))
    _native(delta, TARGET_LAN_IF, "tx", COUNT, COUNT * double)
    r.record("ifstats-qinq", {"before": before, "after": after, "delta": delta})


async def test_software_path(offload_service_stopped, vlan_rig):
    """A software flowtable forwards from the ingress hook, and the frame it
    consumes comes back to the driver looking like a drop. The driver used to
    count nothing for it; now the port's receive counters see the burst."""
    r = vlan_rig
    await r.table(hardware=False)
    await r.exchange(count=4, payload_size=PAYLOAD)
    before = await _snapshot(r)
    await r.exchange(count=COUNT, payload_size=PAYLOAD)
    after = await _snapshot(r)
    delta = _delta(before, after)
    assert not delta["flows"] and not after["flows"], after["flows"]
    software = delta["ethtool"][TARGET_LAN_IF]
    assert software["rx packets [TOTAL]"] >= COUNT, software
    for surface in ("links", "procnetdev"):
        counters = delta[surface][TARGET_LAN_IF]
        assert counters["rx_packets"] >= COUNT, (surface, counters)
        assert counters["rx_bytes"] >= COUNT * (IP_LEN + VLAN_HLEN), (surface, counters)
    # The same frames leave the LAN through the VLAN device in software, which
    # is where the device's own transmit convention can be read straight off
    # Linux: the frame without the device's tag, per frame. This is the number
    # the hardware fold restates its record to.
    vlan = delta["links"][r.dut_vlan_if]
    stray = vlan["tx_packets"] - COUNT
    assert 0 <= stray <= STRAY_LIMIT, vlan
    assert COUNT * (IP_LEN + ETH_HLEN) <= vlan["tx_bytes"] <= \
        COUNT * (IP_LEN + ETH_HLEN) + stray * FRAME_MAX, vlan
    r.record("ifstats-software", {"delta": delta})


async def test_record_waits_for_an_unproven_delete(offload_service_stopped,
                                                                     vlan_rig):
    """A VLAN device goes while the deletes of the entries naming its record
    cannot be proven: the record stays out of the pool until they are, and the
    next device on the tag gets a record that counts exactly.

    The microcode writes a record every time it runs an entry's opcodes, and a
    retired entry may still be walked until a barrier completes. Returned to
    the pool before that, the record's first word is the free list's link for
    the late write to overwrite, and the next device is handed a record the old
    entry still counts into. CDX's debug knob withholds the proofs, so the
    device's record is released while both entries are still retired; the
    release is counted as deferred, and the slot as retained until recovery
    settles them."""
    r = vlan_rig
    knob = await r.target.fs_read(r.session, FAIL_SYNC, max_bytes=64)
    if knob["errno"]:
        pytest.skip("withholding a delete's proof needs a CDX_DEBUG_FLOWTABLE build")
    if (await r.state())["observe"]:
        pytest.skip("retirement needs installed hardware")
    try:
        # Inside the try, so a count some earlier run left behind is still
        # cleared on the way out.
        assert bytes.fromhex(knob["content_hex"]).decode().strip() == "0", knob
        await r.table()
        await r.exchange(count=4, payload_size=PAYLOAD)
        await _both_directions(r)
        before = await r.state()
        record = [v for v in before["vlans"] if v["dev"] == r.dut_vlan_if]
        # Both directions name the one record: the strip counts into its
        # receive half, the insert into its transmit half.
        assert len(record) == 1 and record[0]["slot"] == "yes" and record[0]["refs"] == "2", \
            before["vlans"]
        assert before["entries"] == 2 and before["stats_retained"] == 0, before
        result = await r.target.fs_write(r.session, FAIL_SYNC, str(WITHHELD_PROOFS))
        assert result["errno"] == 0, result
        await command(r.target, r.session, "ip", "link", "del", r.dut_vlan_if)
        # Whichever came last, the unregistration's reap or the last
        # direction's release, freed the record while the entries were still
        # retired: one deferred release, one slot held.
        deferred = await r.wait(lambda s: s["stats_deferred"] != before["stats_deferred"])
        assert deferred["stats_deferred"] == before["stats_deferred"] + 1, (before, deferred)
        assert deferred["stats_retained"] == 1, deferred
        assert not [v for v in deferred["vlans"] if v["ifindex"] == record[0]["ifindex"]], \
            deferred["vlans"]
        # Recovery runs its ordinary course once the withheld proofs are spent:
        # a barrier completes, both entries are released, and the record goes
        # back with them. Nothing is left retained -- no slot leaked.
        settled = await r.wait(lambda s: s["invalidation_done"] == 1 and not s["quarantine"]
                               and not s["stats_retained"], timeout=20)
        assert settled["invalidated"] == 1 and not settled["fatal"] and not settled["entries"], \
            settled
        assert 1 <= settled["errors"] - before["errors"] <= 2, (before, settled)
        assert settled["stats_deferred"] == deferred["stats_deferred"], settled
        assert settled["vlan_records"] == 0, settled
        assert (await read(r.target, r.session, FAIL_SYNC)).strip() == "0"
        r.record("ifstats-unproven-delete", {"before": before, "deferred": deferred,
                                             "settled": settled})

        # A fresh device on the same tag. Its record is the one the retired
        # entries named -- the pool hands back the last record returned -- so
        # an exact count is what says the late writes and the free list never
        # met.
        await r.delete_table()
        await r.wait(lambda s: s["rearm_ready"] == 1, timeout=20)
        await r.clear_ct()
        # The fixture's own teardown deletes the device by name.
        await dut_vlan_subif(TopologyStack(), r.target, r.session, parent=TARGET_LAN_IF,
                             vid=VLAN_ID, ipv4=f"{DUT_VLAN_ADDR}/24")
        await command(r.target, r.session, "ip", "neigh", "replace", r.lan_ip, "lladdr",
                      r.lan_mac, "nud", "permanent", "dev", r.dut_vlan_if)
        fresh_before, fresh_after, delta = await _burst(r)
        tagged, untagged = IP_LEN + ETH_HLEN + VLAN_HLEN, IP_LEN + ETH_HLEN
        assert delta["vlans"][r.dut_vlan_if] == {"rx_packets": COUNT, "rx_bytes": COUNT * untagged,
                                                 "tx_packets": COUNT,
                                                 "tx_bytes": COUNT * tagged}, delta["vlans"]
        assert fresh_after["state"] == {"vlan_records": 1, "vlan_slots": 1}, fresh_after["state"]
        _native(delta, r.dut_vlan_if, "rx", COUNT, COUNT * IP_LEN)
        _native(delta, r.dut_vlan_if, "tx", COUNT, COUNT * untagged)
        final = await r.state()
        assert not final["stats_retained"] and not final["invalidated"], final
        assert final["stats_deferred"] == deferred["stats_deferred"], final
        r.record("ifstats-unproven-delete-fresh", {"before": fresh_before, "after": fresh_after,
                                                   "delta": delta})
    finally:
        # Unspent proofs would fail the next test's deletes, and an
        # invalidation left latched would refuse its fixture. The next bind
        # clears the latch, but only on an adapter that can rearm: a fatal one
        # binds passively, and an unproven retirement holds the rearm. This
        # runs because something may already have failed, so it asks rather
        # than fails, and the fixture gives the table back.
        await r.target.fs_write(r.session, FAIL_SYNC, "0")
        left = await r.state()
        if left["invalidated"] and not left["fatal"]:
            await command(r.target, r.session, "nft", "delete", "table", "inet", TABLE,
                          check=False)
            if await rearm_ready(r, timeout=20):
                await r.clear_ct()
                await r.table()
