"""Shared support for flowtable selective neighbour."""

from __future__ import annotations

import json
import os
from collections import Counter

import pytest
import pytest_asyncio
from _flowtable_connections import by_key, healthy
from _flowtable_rig import DPORT, SPORT, TABLE, WAN_IP, command, read
from _flowtable_tcp import cpu, cpu_delta, software_tx
from _topology import LAN_NIC, TARGET_LAN_IF, TARGET_WAN_IF, lan_run_python
from ask_orch.client import Agent

GATEWAY = "198.18.29.1"
PEERS = [dict(netns="ask-ft-neigh-a", iface="askftna", lan="198.18.29.2", mac="02:9d:99:b2:33:a1"),
         dict(netns="ask-ft-neigh-b", iface="askftnb", lan="198.18.29.3", mac="02:9d:99:b2:33:b1")]
FLOWS = [{**spec, "id": 2 * i + j, "proto": proto, "sport": SPORT + 96}
         for i, spec in enumerate(PEERS) for j, proto in enumerate(("udp", "tcp"))]
A, B, ALL = [0, 1], [2, 3], [0, 1, 2, 3]
CHANGED_MAC = "02:9d:99:b2:33:a2"


def keys(ids, flows=FLOWS):
    """The hardware directions of `ids`. A spec's `software` names the ports
    whose arriving direction the adapter is expected to leave to Linux -- a
    UDP direction into a path smaller than a full frame -- and those are left
    out."""
    result = set()
    for ident in ids:
        spec = flows[ident]
        proto = "6" if spec["proto"] == "tcp" else "17"
        def endpoint(address, port):
            return f"[{address}]:{port}" if ":" in address else f"{address}:{port}"
        src = endpoint(spec["lan"], spec["sport"])
        dst = endpoint(spec.get("connect_ip", WAN_IP), spec.get("connect_port", DPORT))
        result.update(key for key in ((TARGET_LAN_IF, proto, src, dst), (TARGET_WAN_IF, proto, dst, src))
                      if key[0] not in spec.get("software", ()))
    return result


def software_egress(ids, flows=FLOWS):
    """How many of `ids`' directions Linux forwards, by the port each leaves."""
    counts = Counter()
    for ident in ids:
        for ingress in flows[ident].get("software", ()):
            counts[TARGET_WAN_IF if ingress == TARGET_LAN_IF else TARGET_LAN_IF] += 1
    return counts


def unchanged(before, after, ids, flows=FLOWS, bindings=2):
    healthy(after, bindings)
    assert after["handle_refs"] == after["entries"], after
    old, new = by_key(before), by_key(after)
    for key in keys(ids, flows):
        assert new[key]["cookie"] == old[key]["cookie"], (key, before, after)
        for field in ("packets", "bytes"):
            assert int(new[key][field]) >= int(old[key][field]), (key, before, after)


@pytest_asyncio.fixture
async def selective(rig):
    r = rig
    r.selective_errors = (await r.state())["errors"]
    wan = Agent("wan", f"http://{os.environ.get('ASK_WAN_IP', '127.0.0.1')}:9110")
    cleanup = []
    setup = f'''
import pathlib, subprocess
peers = {PEERS!r}
def run(*args): subprocess.run(args, check=True, capture_output=True, text=True)
for p in peers:
    assert not pathlib.Path('/var/run/netns/' + p['netns']).exists()
    assert not pathlib.Path('/sys/class/net/' + p['iface']).exists()
created = []
try:
    for p in peers:
        run('ip', 'netns', 'add', p['netns']); created.append(p)
        run('ip', 'link', 'add', 'link', {LAN_NIC!r}, 'name', p['iface'],
            'netns', p['netns'], 'type', 'macvlan', 'mode', 'bridge')
        run('ip', '-n', p['netns'], 'link', 'set', 'lo', 'up')
        run('ip', '-n', p['netns'], 'link', 'set', p['iface'], 'address', p['mac'], 'up')
        run('ip', '-n', p['netns'], 'addr', 'add', p['lan'] + '/24', 'dev', p['iface'])
        run('ip', '-n', p['netns'], 'route', 'add', 'default', 'via', {GATEWAY!r})
except BaseException:
    for p in reversed(created): run('ip', 'netns', 'del', p['netns'])
    raise
'''
    result = await lan_run_python(r.lan, setup, label="flowtable_selective_setup", timeout=20)
    assert result.rc == 0, result.stdout
    try:
        async def change(agent, args, undo):
            await command(agent, r.session, *args)
            cleanup.append((agent, undo))

        dut_ip = next(a["local"] for i in json.loads((await command(r.target, r.session, "ip", "-j", "-4", "addr", "show", "dev", TARGET_WAN_IF))["stdout"])
                      for a in i["addr_info"] if a["family"] == "inet")
        await change(r.target, ["ip", "addr", "add", GATEWAY + "/24", "dev", TARGET_LAN_IF],
                     ["ip", "addr", "del", GATEWAY + "/24", "dev", TARGET_LAN_IF])
        for spec in PEERS:
            address = spec["lan"] + "/32"
            await change(r.target, ["ip", "route", "add", address, "dev", TARGET_LAN_IF],
                         ["ip", "route", "del", address, "dev", TARGET_LAN_IF])
            await change(wan, ["ip", "route", "add", address, "via", dut_ip, "dev", r.wan_if],
                         ["ip", "route", "del", address, "via", dut_ip, "dev", r.wan_if])
        for spec in FLOWS:
            nat = ["POSTROUTING", "-s", spec["lan"], "-d", WAN_IP, "-p", spec["proto"],
                   "--sport", str(spec["sport"]), "--dport", str(DPORT), "-j", "ACCEPT"]
            await change(r.target, ["iptables", "-t", "nat", "-I", *nat],
                         ["iptables", "-t", "nat", "-D", *nat])
        for name, value in [("base_reachable_time_ms", 1000), ("delay_first_probe_time", 1),
                            ("retrans_time_ms", 200), ("ucast_solicit", 3),
                            ("mcast_solicit", 3), ("app_solicit", 0)]:
            key = f"net.ipv4.neigh.{TARGET_LAN_IF}.{name}"
            old = (await read(r.target, r.session, "/proc/sys/" + key.replace(".", "/"))).strip()
            await change(r.target, ["sysctl", "-w", f"{key}={value}"], ["sysctl", "-w", f"{key}={old}"])
        await r.nft(f'''table inet {TABLE} {{
 flowtable fast {{ hook ingress priority 0; devices = {{ {TARGET_LAN_IF}, {TARGET_WAN_IF} }}; flags offload; }}
 chain forward {{ type filter hook forward priority 0; policy accept;
 ip saddr {{ {PEERS[0]['lan']}, {PEERS[1]['lan']} }} ip daddr {WAN_IP} udp sport {SPORT + 96} udp dport {DPORT} flow add @fast
 ip saddr {{ {PEERS[0]['lan']}, {PEERS[1]['lan']} }} ip daddr {WAN_IP} tcp sport {SPORT + 96} tcp dport {DPORT} flow add @fast
 }}
}}''')
        await r.wait(lambda s: s["bindings"] == 2)
        yield r
    finally:
        failures = []

        async def attempt(operation):
            try:
                return await operation
            except Exception as error:
                failures.append(str(error))

        final = await attempt(r.delete_table())
        if final:
            r.record("selective-cleanup", final)
            if not (final["entries"] == final["handle_refs"] == final["neighbour_refs"] == final["quarantine"] == 0
                    and final["installs"] == final["deletes"] and final["errors"] == r.selective_errors):
                failures.append(final)
        for spec in FLOWS:
            await attempt(command(r.target, r.session, "conntrack", "-D", "-p", spec["proto"],
                                  "--orig-src", spec["lan"], "--orig-dst", WAN_IP,
                                  "--sport", str(spec["sport"]), "--dport", str(DPORT), check=False))
        for agent, args in reversed(cleanup):
            await attempt(command(agent, r.session, *args))
        for spec in PEERS:
            await attempt(command(r.target, r.session, "ip", "neigh", "del", spec["lan"], "dev", TARGET_LAN_IF, check=False))
        teardown = f'''
import subprocess
errors = []
for p in {PEERS!r}:
    result = subprocess.run(['ip', 'netns', 'del', p['netns']], capture_output=True, text=True)
    if result.returncode: errors.append(result.stderr)
assert not errors, errors
'''
        result = await attempt(lan_run_python(r.lan, teardown, label="flowtable_selective_cleanup", timeout=15))
        if result and result.rc:
            failures.append(result.stdout)
        assert not failures, failures


async def warm(r, p, ids, label, flows=FLOWS):
    expected = keys(range(len(flows)), flows)
    samples = []
    for _ in range(8):
        await p.batch(ids, count=128, interval=0.01)
        state = await r.state()
        samples.append(state)
        if state["entries"] == len(expected):
            healthy(state)
            assert state["handle_refs"] == len(expected) and by_key(state).keys() == expected, state
            r.record(label, samples)
            return state
    pytest.fail(f"automatic hardware admission failed: {samples}")


async def hardware(r, p, label, flows=FLOWS, bindings=2):
    ids = list(range(len(flows)))
    before = await r.state()
    forwarded = await r.software_forwarded() if hasattr(r, "software_forwarded") else None
    tx_before, cpu_before = await software_tx(r), await cpu(r)
    reports = await p.batch(ids, count=256, interval=0.03125)
    after, tx_after, cpu_after = await r.state(), await software_tx(r), await cpu(r)
    if any(flows[i].get("software") for i in ids):
        # A direction Linux keeps re-offers its flow about once a second while
        # it carries traffic. Its refusal is decided before RTNL and the
        # installed direction's offer is answered without it, so nothing here
        # should take RTNL at all; busy moving names an offer that did.
        assert after["busy"] == before["busy"], ("an offer took RTNL mid-window", before, after)
    unchanged(before, after, ids, flows, bindings)
    assert before["installs"] == after["installs"] and before["deletes"] == after["deletes"], (before, after)
    old, new = by_key(before), by_key(after)
    for ident in ids:
        for key in keys([ident], flows):
            packets = int(new[key]["packets"]) - int(old[key]["packets"])
            if flows[ident]["proto"] == "udp":
                assert packets == 256, (key, packets)
                # Proc reports raw ingress bytes, including any VLAN tags.
                tags = old[key]["in_vlan"]
                vlan_bytes = 0 if tags == "-" else 4 * len(tags.split("."))
                ip_bytes = 40 if ":" in flows[ident]["lan"] else 20
                assert int(new[key]["bytes"]) - int(old[key]["bytes"]) == 256 * (256 + 14 + ip_bytes + 8 + vlan_bytes), (key, old, new)
            else:
                assert packets >= reports[ident]["bytes"] // 1500, (key, packets)
    tx = {dev: tx_after[dev] - tx_before[dev] for dev in tx_before}
    slow_path = await r.software_forwarded() - forwarded if forwarded is not None else None
    r.record(label, {"before": before, "after": after, "transfers": reports,
                     "software_tx": tx, "software_forwarded": slow_path, "cpu": cpu_delta(cpu_before, cpu_after)})
    # A direction left to Linux crosses the software flowtable, which bypasses
    # the forward hook the slow-path counter sits on but not the port's own
    # transmit count: all 256 of its datagrams, on top of the usual allowance.
    carried = {dev: 256 * n for dev, n in software_egress(ids, flows).items()}
    if slow_path is not None:
        assert 0 <= slow_path <= 64, slow_path
    else:
        assert carried.get(TARGET_LAN_IF, 0) <= tx[TARGET_LAN_IF] <= 64 + carried.get(TARGET_LAN_IF, 0), tx
        assert carried.get(TARGET_WAN_IF, 0) <= tx[TARGET_WAN_IF] <= 512 + carried.get(TARGET_WAN_IF, 0), tx
    return after


async def retired(r, before, label):
    state = await r.wait(lambda s: s["entries"] == 4)
    unchanged(before, state, B)
    assert by_key(state).keys() == keys(B), state
    assert state["neighbour_invalidations"] == before["neighbour_invalidations"] + 2, (before, state)
    assert state["installs"] == before["installs"] and state["deletes"] == before["deletes"] + 4, (before, state)
    assert state["rearms"] == before["rearms"] and not state["invalidation_done"], state
    r.record(label, {"before": before, "after": state})
    return state
