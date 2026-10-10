"""Shared support for flowtable capacity."""

from __future__ import annotations

import asyncio
import json
import os
import socket
import struct
from pathlib import Path

from ask_orch.commands import console_python
from ask_orch.uart import Console
from _flowtable_connections import healthy
from _flowtable_rig import DPORT, HEALTH_BASELINE, WAN_IP
from _flowtable_tcp import cpu, cpu_delta, software_tx
from _topology import LAN_NIC

CAPACITY = 32768
CONNECTIONS = CAPACITY // 2
REPLACEMENTS = 256
# Detaching a full table retires every hardware entry serially before nft
# returns: 13.7 s on 2026-09-21, 14.6 s isolated and over 15 s mid-suite on
# 2026-09-22 (KASAN). The wait is generous; the bound is what is asserted, at
# about twice the measured time.
DETACH_SECONDS = 60
DETACH_BOUND_SECONDS = 30
# The longest a /proc/cdx_flowtable header read may wait while a full table is
# retired. One walk under the adapter's transaction held readers out for 13 s;
# in batches the longest wait measured 0.9 s (KASAN), the reader queueing
# behind Linux's own deletion and offer callbacks.
READER_BOUND_SECONDS = 2
# Keep explicit data sockets below the lab's ephemeral port range.
BASE = 20000


def socket_drops(sock):
    inode = str(os.fstat(sock.fileno()).st_ino)
    table = "/proc/net/udp6" if sock.family == socket.AF_INET6 else "/proc/net/udp"
    for line in Path(table).read_text().splitlines()[1:]:
        fields = line.split()
        if fields[9] == inode:
            return int(fields[-1])
    raise AssertionError(("UDP socket missing", inode))


async def delete_udp(r, sports, *, allow_missing=False, source=None, destination=None):
    # conntrack(8)'s filtered deletion dumps the entire table per invocation.
    # Use the existing agent's raw netlink transport for an exact original
    # tuple deletion (nfnetlink_conntrack.h), checking every kernel ACK locally.
    def attribute(kind, value):
        length = 4 + len(value)
        return struct.pack("=HH", length, kind) + value + bytes((-length) % 4)

    source, destination = source or r.lan_ip, destination or WAN_IP
    family = socket.AF_INET6 if ":" in source else socket.AF_INET
    # CTA_IP_V4_SRC/DST are 1 and 2; CTA_IP_V6_SRC/DST are 3 and 4.
    kinds = (3, 4) if family == socket.AF_INET6 else (1, 2)
    ip = (attribute(kinds[0], socket.inet_pton(family, source))
          + attribute(kinds[1], socket.inet_pton(family, destination)))
    messages = []
    for sport in sports:
        proto = (attribute(1, bytes([socket.IPPROTO_UDP])) + attribute(2, struct.pack("!H", sport))
                 + attribute(3, struct.pack("!H", DPORT)))
        original = attribute(0x8001, ip) + attribute(0x8002, proto)
        body = struct.pack("!BBH", family, 0, 0) + attribute(0x8001, original)
        messages.append((sport, body.hex()))
    result = await console_python(Console.target(), f'''
import asyncio, errno, json, struct
from askd_agent.agent import netlink_send
async def main():
    missing = 0
    for sport, payload in {messages!r}:
        result = await netlink_send({{'protocol': 12, 'body_hex': payload,
                                     'nlmsg_type': 0x102, 'nlmsg_flags': 5,
                                     'timeout_ms': 500}}, {{}})
        reply = bytes.fromhex(result['reply_hex'])
        assert len(reply) >= 20 and struct.unpack_from('=H', reply, 4)[0] == 2, (sport, result)
        error = struct.unpack_from('=i', reply, 16)[0]
        if {allow_missing!r} and error == -errno.ENOENT:
            missing += 1
        else:
            assert error == 0, (sport, result)
    print(json.dumps({{'missing': missing}}))
asyncio.run(main())
''', timeout=30)
    # Already reclaimed tuples are counted, never silently accepted everywhere.
    return json.loads(result["stdout"])["missing"]


async def lan_counters(r):
    result = await r.run_peer(
        "import json,subprocess; print(json.dumps({k: subprocess.check_output(v,text=True) "
        f"for k,v in {{'link':['ethtool',{LAN_NIC!r}], 'stats':['ethtool','-S',{LAN_NIC!r}]}}.items()}}))",
        label="capacity_counters", timeout=15)
    assert result.rc == 0, result.stdout
    return json.loads(result.stdout.strip())


async def summary(r):
    return await r.target.observe(r.session, "summary")


async def wait_entries(r, count, p, timeout=90):
    task = asyncio.create_task(r.target.observe(r.session, "wait_entries", count=count, timeout=timeout))
    try:
        while not task.done():
            jobs = await p.rpc("status", compact=True)
            assert not jobs["errors"], jobs
            await asyncio.wait({task}, timeout=1)
        state = await task
        assert state["errors"] == HEALTH_BASELINE["errors"], (state, HEALTH_BASELINE)
        assert not any(state[k] for k in ("fatal", "invalidated", "quarantine")), state
        return state
    finally:
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


async def enable_snapshots(r, *, lan, wan, public, lan_if, wan_if, dport, base, count, survivors=0):
    await r.target.observe(r.session, "workload", lan=lan, wan=wan, public=public,
                           lan_if=lan_if, wan_if=wan_if, dport=dport, base=base,
                           count=count, survivors=survivors)
    r.bulk = True


async def release(r, *states):
    for state in states:
        if "snapshot" in state:
            await r.target.observe(r.session, "release", snapshot=state["snapshot"])


async def check_rows(r, state, **options):
    result = await r.target.observe(r.session, "check", snapshot=state["snapshot"], **options)
    assert result["checked"] == state["flow_count"], result
    assert result["missing_count"] == result["unexpected_count"] == 0, result
    assert result["translation_errors"] == result["mtu_errors"] == 0, result
    return result


async def compare(r, before, after, **options):
    return await r.target.observe(r.session, "compare", before=before["snapshot"],
                                  after=after["snapshot"], **options)


async def unchanged(r, before, after, **options):
    healthy(after)
    result = await compare(r, before, after, **options)
    assert result["unchanged_errors"] == 0, result
    return result


async def start(p, ids, *, count=0, interval=2):
    for proto in ("tcp", "udp"):
        selected = [i for i in ids if p.flows[i]["proto"] == proto]
        if selected:
            await p.rpc("start", selected, count=count, interval=interval,
                        allow_loss=proto == "udp", udp_timeout=5)


def delivery(p, reports):
    udp_sent = udp_lost = 0
    for ident, report in reports.items():
        proto = p.flows[int(ident)]["proto"]
        assert report["received"] > 0, (ident, report)
        size = p.tcp_size if proto == "tcp" else 256
        assert report["bytes"] == report["received"] * size, (ident, report)
        if proto == "tcp":
            assert report["received"] == report["count"] and not report["lost"], (ident, report)
        else:
            udp_sent += report["count"]
            udp_lost += report["lost"]
    # The lab has independently observed link errors. Bound and report isolated
    # UDP losses; do not turn a single bad frame into a capacity failure. Payload
    # corruption still fails in the peer, and every connection must deliver.
    assert udp_lost <= max(4, udp_sent // 1000), (udp_lost, udp_sent)
    return {"udp_sent": udp_sent, "udp_lost": udp_lost}


async def batch(p, ids, count, interval):
    await start(p, ids, count=count, interval=interval)
    reports = await p.rpc("wait", ids)
    assert set(map(int, reports)) == set(ids)
    assert all(report["count"] == count for report in reports.values())
    delivery(p, reports)
    return reports


def record_delivery(r, p, label, reports):
    # Preserve evidence even when the loss budget or delivery assertion fails.
    r.record(label, {"reports": reports})
    r.record(label, {"reports": reports, **delivery(p, reports)})


async def hardware_window(r, before, label, *, turnover=0):
    # Linux can retire and readmit a live flow on its own initiative; `turnover`
    # is how many directions this window tolerates doing so. It defaults to none,
    # so a caller that expects a completely static table still gets that.
    tx0, cpu0 = await software_tx(r), await cpu(r)
    await asyncio.sleep(10)
    cpu1, tx1 = await cpu(r), await software_tx(r)
    after = await r.state()
    # A recycled cookie cannot identify a new generation on its own; a restarted
    # packet count can. See the same reasoning in the churn proof.
    compared = await compare(r, before, after, allow_regenerated=True)
    tx = {dev: tx1[dev] - tx0[dev] for dev in tx0}
    # Record before asserting: a window that loses or turns over a flow is
    # exactly the one whose evidence is worth keeping.
    r.record(label, {"state": after, "software_tx": tx, "cpu": cpu_delta(cpu0, cpu1),
                     "comparison": compared,
                     "installs": after["installs"] - before["installs"],
                     "deletes": after["deletes"] - before["deletes"]})
    assert compared["missing_count"] == compared["unexpected_count"] == 0, compared
    assert compared["regenerated_count"] <= turnover, compared
    assert compared["unchanged_errors"] == compared["progress_errors"] == 0, compared
    assert after["installs"] - before["installs"] == compared["regenerated_count"], (before, after)
    assert after["deletes"] - before["deletes"] == compared["regenerated_count"], (before, after)
    assert all(0 <= n <= 128 for n in tx.values()), tx
    return after
