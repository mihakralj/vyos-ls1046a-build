"""Shared support for flowtable arp."""

from __future__ import annotations

import asyncio
import base64
import json
import time
from contextlib import asynccontextmanager
from pathlib import Path

import pytest
from _flowtable_rig import (
    HEALTH_BASELINE,
    WAN_IP,
    artifact_dir,
    command,
    console_command,
    read,
)
from _flowtable_tcp import software_tx
from _topology import LAN_NIC, TARGET_LAN_IF, TARGET_WAN_IF, lan_run_python

CHANGED_MAC = "02:9d:99:b2:33:02"
PEER = Path(__file__).with_name("_flowtable_neighbour_peer.py").read_text()


async def lan_neighbour(r, **changes):
    address = getattr(r, "arp_address", r.lan_ip)
    script = PEER + f"\nimport json\nprint(json.dumps(configure_neighbour({LAN_NIC!r}, {address!r}, **{changes!r})))\n"
    result = await lan_run_python(r.lan, script, label="flowtable_arp_config", timeout=20)
    assert result.rc == 0, result.stdout
    return json.loads(result.stdout.strip())


def neighbour_targets(r):
    return getattr(r, "arp_neighbours", [(r.lan_ip, TARGET_LAN_IF), (WAN_IP, TARGET_WAN_IF)])


async def neighbours(r):
    result = await command(r.target, r.session, "ip", "-j", "-s", "neigh", "show")
    return {n["dst"]: n for n in json.loads(result["stdout"])
            if n["dst"] in {ip for ip, _ in neighbour_targets(r)}}


async def wait_neighbour(r, predicate, timeout=8):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        entries = await neighbours(r)
        if predicate(entries):
            return entries
        await asyncio.sleep(0.1)
    pytest.fail(f"neighbour state did not converge: {entries}")


async def observe(r, operation, label):
    task = asyncio.create_task(operation)
    samples = []
    try:
        while not task.done():
            samples.append({"neighbours": await neighbours(r), "offload": await r.state()})
            await asyncio.sleep(0.5)
    finally:
        result = await task
        r.record(f"arp-{r.proto}-{label}-samples", samples)
    return result


async def recover(r, *, global_invalidation=False):
    if not global_invalidation:
        state = await r.state()
        assert state["invalidated"] == 0 and state["bindings"] == 2, state
        return  # Linux retires the old generation and admits fresh traffic.
    await r.delete_table()
    await r.wait(lambda s: s["rearm_ready"] == 1 and s["neighbour_refs"] == 0)
    before = await r.state()
    if r.proto == "udp":
        await r.clear_ct()
    await r.table()
    after = await r.state()
    assert after["rearms"] == before["rearms"] + 1 and after["invalidated"] == 0, (before, after)


async def invalidated(r, before, label, *, global_invalidation=False, counter="neighbour_invalidations"):
    state = await r.wait(lambda s: s["entries"] == 0 and (
        s["invalidation_done"] == 1 if global_invalidation else
        s[counter] > before[counter]))
    assert state["invalidated"] == int(global_invalidation) and state["bindings"] == 2, state
    assert state["handle_refs"] == state["neighbour_refs"] == state["fatal"] == state["quarantine"] == 0, state
    assert state["errors"] == HEALTH_BASELINE["errors"], (state, HEALTH_BASELINE)
    assert state["installs"] == before["installs"] and state["rearms"] == before["rearms"], (before, state)
    r.record(f"arp-{r.proto}-{label}", {"before": before, "after": state, "neighbours": await neighbours(r)})
    return state


@asynccontextmanager
async def arp_environment(r):
    tag = getattr(r, "arp_tag", r.proto)
    original_mac = r.lan_mac
    assert original_mac != CHANGED_MAC
    ignore = await console_command(r.lan, "sysctl", "-n", f"net.ipv4.conf.{LAN_NIC}.arp_ignore")
    old_ignore = int(ignore["stdout"].strip())
    assert old_ignore == 0, "ARP test requires normal initial LAN responses"
    base = f"/tmp/ask_flowtable_arp_{time.monotonic_ns()}"
    start = f'''
import json, pathlib, subprocess, time
base = {base!r}
with open(base + '.err', 'wb') as log:
    proc = subprocess.Popen(['timeout', '180', 'tcpdump', '-p', '-U', '-n', '-i', {LAN_NIC!r},
                             '-w', base + '.pcap', 'arp'], stdout=subprocess.DEVNULL,
                            stderr=log, start_new_session=True)
for _ in range(50):
    assert proc.poll() is None, pathlib.Path(base + '.err').read_text()
    if 'listening on' in pathlib.Path(base + '.err').read_text(): break
    time.sleep(0.1)
else: raise AssertionError('ARP capture did not start')
print(json.dumps({{'pid': proc.pid}}))
'''
    result = await lan_run_python(r.lan, start, label="flowtable_arp_capture", timeout=15)
    assert result.rc == 0, result.stdout
    pid = json.loads(result.stdout.strip())["pid"]
    saved = []
    try:
        assert (await r.state())["neighbour_refs"] == 0
        for dev in (TARGET_LAN_IF, TARGET_WAN_IF):
            for name, value in [("base_reachable_time_ms", 1000), ("delay_first_probe_time", 1),
                                ("retrans_time_ms", 200), ("ucast_solicit", 3),
                                ("mcast_solicit", 3), ("app_solicit", 0)]:
                key = f"net.ipv4.neigh.{dev}.{name}"
                old = (await read(r.target, r.session, "/proc/sys/" + key.replace(".", "/"))).strip()
                await command(r.target, r.session, "sysctl", "-w", f"{key}={value}")
                saved.append((key, old))
        for ip, dev in neighbour_targets(r):
            await command(r.target, r.session, "ip", "neigh", "del", ip, "dev", dev)
        yield
    finally:
        try:
            r.record(f"arp-{tag}-before-cleanup", await r.state())
            await r.delete_table()
            assert (await r.state())["neighbour_refs"] == 0
        finally:
            try:
                await lan_neighbour(r, arp_ignore=old_ignore,
                                    **({"mac": original_mac} if r.lan_mac != original_mac else {}))
                r.lan_mac = original_mac
                for key, value in reversed(saved):
                    await command(r.target, r.session, "sysctl", "-w", f"{key}={value}")
            finally:
                stop = f'''
import base64, json, os, pathlib, signal, time
try: os.killpg({pid}, signal.SIGINT)
except ProcessLookupError: pass
time.sleep(0.3)
base = pathlib.Path({base!r})
pcap = base.with_suffix('.pcap'); log = base.with_suffix('.err')
print(json.dumps({{'pcap': base64.b64encode(pcap.read_bytes()).decode(), 'log': log.read_text()}}))
pcap.unlink(); log.unlink()
'''
                result = await lan_run_python(r.lan, stop, label="flowtable_arp_capture_stop", timeout=10)
                assert result.rc == 0, result.stdout
                report = json.loads(result.stdout.strip())
                artifact_dir().mkdir(parents=True, exist_ok=True)
                (artifact_dir() / f"arp-{tag}.pcap").write_bytes(base64.b64decode(report.pop("pcap")))
                r.record(f"arp-{tag}-capture", report)


def check_arp_trace(r, original_mac, failure_start, failure_end):
    from scapy.all import ARP, Ether, rdpcap
    tag = getattr(r, "arp_tag", r.proto)
    packets = rdpcap(str(artifact_dir() / f"arp-{tag}.pcap"))
    addresses = {ip for ip, dev in neighbour_targets(r) if dev == TARGET_LAN_IF}
    probes = [p for p in packets if ARP in p and p[ARP].op == 1 and
              p[ARP].psrc == r.lan_gateway and p[ARP].pdst in addresses]
    healthy = [p for p in probes if p[Ether].dst == original_mac]
    assert len(healthy) >= 2, "no repeated unicast ARP probes during hardware use"
    answered = [probe for probe in healthy if any(
        ARP in reply and reply[ARP].op == 2 and reply[ARP].hwsrc == original_mac and
        reply[ARP].psrc == probe[ARP].pdst and reply[ARP].hwdst == r.dut_lan_mac and
        0 <= float(reply.time - probe.time) <= 1 for reply in packets)]
    assert len(answered) >= 2, "no repeated successful ARP probes during hardware use"
    failed = [p for p in probes if failure_start <= float(p.time) < failure_end]
    assert len(failed) >= 3, "failure did not exhaust ordinary ARP probes"
    assert not any(ARP in p and p[ARP].op == 2 and p[ARP].psrc in addresses and
                   failure_start <= float(p.time) < failure_end for p in packets), "ARP replied during fault"
    r.record(f"arp-{tag}-proof", {"healthy_unicast_probes": len(healthy),
                                     "answered_unicast_probes": len(answered),
                                     "unanswered_failure_probes": len(failed),
                                     "failure_start": failure_start, "failure_end": failure_end})


async def udp_hardware(r, count=512, interval=0.003, label="forwarding"):
    # Admission can legitimately lose RTNL trylock. Linux retries hardware
    # from software traffic when its flow timeout advances by more than HZ;
    # keep verified traffic moving instead of idly polling after a short burst.
    warmup = []
    for _ in range(5):
        await r.exchange(128, interval=0.01, promiscuous=False)
        before = await r.state()
        warmup.append(before)
        if before["entries"] == before["neighbour_refs"] == 2:
            break
    else:
        pytest.fail(f"UDP did not install during admission traffic: {warmup}")
    r.record(f"arp-udp-{label}-admission", warmup)
    tx_before = await software_tx(r)
    result = await observe(r, r.exchange(count, interval=interval, promiscuous=False), label)
    after, tx_after = await r.state(), await software_tx(r)
    old = {f["in"]: int(f["packets"]) for f in before["flows"]}
    assert after["entries"] == after["neighbour_refs"] == 2 and not after["invalidated"], after
    assert after["installs"] == before["installs"], (before, after)
    for flow in after["flows"]:
        assert flow["proto"] == "17" and int(flow["packets"]) - old[flow["in"]] == count, (before, after)
    assert 0 <= tx_after[TARGET_LAN_IF] - tx_before[TARGET_LAN_IF] <= 32, (tx_before, tx_after)
    r.record(f"arp-udp-{label}", {"before": before, "after": after, "exchange": result,
                                 "software_tx_before": tx_before, "software_tx_after": tx_after})
    return after
