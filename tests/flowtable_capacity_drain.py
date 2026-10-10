"""Retire a full table and send at once, pass after pass (A305).

Both sightings of A305 wedged the two 10G ports in the first traffic after a
full table's 32,768 entries were retired: the capacity test's closing detach,
then its peer's teardown. Each pass here fills the table, retires all of it --
by detaching the table, or by a route change -- and sends across hundreds of
connections with no pause in between, then requires both 10G receive ports to
have counted frames since. The peer's exit after the last pass closes every
connection, as the capacity test's did.

Off unless ASK_DRAIN_SOAK_PASSES names a number of passes: one takes about
two minutes. On a wedge it reads the MAC registers and nothing else -- ethtool
and QMan debugfs hang a wedged board (A305).
"""
from __future__ import annotations

import json
import os
import resource
import socket
import time

import pytest

from _flowtable_capacity import (BASE, CAPACITY, CONNECTIONS, DETACH_SECONDS, batch, enable_snapshots,
                                 socket_drops, start, wait_entries)
from _flowtable_connections import peer
from _flowtable_rig import DPORT, TABLE, WAN_IP, command, read
from _topology import TARGET_LAN_IF, TARGET_WAN_IF

PASSES = int(os.environ.get("ASK_DRAIN_SOAK_PASSES", "0"))
FMAN = "/sys/devices/platform/soc/1a00000.fman"
# The two 10G ports' receive halves, by their BMI register blocks.
RX_PORTS = ("1a90000", "1a91000")
# Sent at once after each retirement: enough connections to cross both ports
# many times over, few enough to finish in seconds in software.
FIRST = 512


async def rx_frames(r):
    return {port: int((await read(r.target, r.session,
                                  f"{FMAN}/{port}.port/statistics/port_frame")).split()[-1])
            for port in RX_PORTS}


@pytest.mark.skipif(not PASSES, reason="set ASK_DRAIN_SOAK_PASSES to soak")
async def test_drain_then_traffic(rig):
    r = rig
    receiver = r.echo.transport.get_extra_info("socket")
    receiver.setsockopt(socket.SOL_SOCKET, 33, 4 << 20)  # Linux SO_RCVBUFFORCE
    address = json.loads((await command(r.target, r.session, "ip", "-j", "-4", "addr",
                                       "show", "dev", TARGET_WAN_IF))["stdout"])[0]
    public = next(a["local"] for a in address["addr_info"] if a["family"] == "inet")
    specs = [{"id": i, "proto": "tcp" if i & 1 else "udp", "sport": BASE + i // 2,
              "remote": [public, BASE + i // 2]} for i in range(CONNECTIONS)]
    ids = list(range(CONNECTIONS))
    await enable_snapshots(r, lan=r.lan_ip, wan=WAN_IP, public=public, lan_if=TARGET_LAN_IF,
                           wan_if=TARGET_WAN_IF, dport=DPORT, base=BASE, count=CONNECTIONS)
    table = f'''table inet {TABLE} {{
 flowtable fast {{ hook ingress priority 0; devices = {{ {TARGET_LAN_IF}, {TARGET_WAN_IF} }}; flags offload; }}
 chain forward {{ type filter hook forward priority 0; policy accept;
 ip saddr {r.lan_ip} ip daddr {WAN_IP} meta l4proto {{ tcp, udp }} th sport {BASE}-{BASE + CONNECTIONS // 2 - 1} th dport {DPORT} flow add @fast
 }}
}}'''
    cleanup = [["ip", "route", "replace", f"{WAN_IP}/32", "dev", TARGET_WAN_IF]]
    limits = resource.getrlimit(resource.RLIMIT_NOFILE)
    resource.setrlimit(resource.RLIMIT_NOFILE, (max(limits[0], 2 * len(specs) + 512), limits[1]))
    passes = []
    try:
        for proto in ("udp", "tcp"):
            name = f"net.netfilter.nf_flowtable_{proto}_timeout"
            value = (await read(r.target, r.session, "/proc/sys/" + name.replace(".", "/"))).strip()
            await command(r.target, r.session, "sysctl", "-w", f"{name}=120")
            cleanup.append(["sysctl", "-w", f"{name}={value}"])
            await command(r.target, r.session, "conntrack", "-D", "-p", proto, "--orig-src", r.lan_ip,
                          "--orig-dst", WAN_IP, "--dport", str(DPORT), check=False, quiet=True)
        async with peer(r, specs, initial_ids=[], lease=600 + 240 * PASSES, tcp_size=1024) as p:
            for offset in range(0, len(ids), 256):
                await p.rpc("open", ids[offset:offset + 256])
            for n in range(PASSES):
                await r.nft(table)
                for offset in range(0, len(ids), 256):
                    group = ids[offset:offset + 256]
                    await batch(p, group, count=4, interval=0.025)
                    await start(p, group)
                await wait_entries(r, CAPACITY, p, timeout=150)
                await p.rpc("stop", ids)
                before = await rx_frames(r)
                started = time.monotonic()
                if n % 2 == 0:
                    how = "detach"
                    drained = await r.delete_table(timeout=DETACH_SECONDS)
                else:
                    how = "route"
                    await command(r.target, r.session, "ip", "route", "replace", f"{WAN_IP}/32",
                                  "dev", TARGET_WAN_IF, "advmss", str(1300 + n))
                    drained = await wait_entries(r, 0, p)
                retired = time.monotonic() - started
                # No pause: the first traffic after the retirement.
                reports = await batch(p, ids[:FIRST], count=4, interval=0.005)
                after = await rx_frames(r)
                passes.append({"pass": n, "how": how, "retire_seconds": retired, "rx_before": before,
                               "rx_after": after, "installs": drained["installs"],
                               "deletes": drained["deletes"]})
                r.record("drain-soak-pass", passes[-1])
                assert all(after[port] > before[port] for port in RX_PORTS), passes[-1]
                assert drained["installs"] == drained["deletes"], drained
                assert len(reports) == FIRST
                if how == "route":
                    await r.delete_table(timeout=DETACH_SECONDS)
        assert socket_drops(receiver) == 0, "generator UDP receive queue overflow"
    except BaseException:
        regs = {}
        for dev in (TARGET_LAN_IF, TARGET_WAN_IF):
            try:
                regs[dev] = await read(r.target, r.session, f"/sys/class/net/{dev}/mac_regs")
            except Exception as error:
                regs[dev] = repr(error)
        r.record("drain-soak-wedge", {"passes": passes, "mac_regs": regs})
        raise
    finally:
        try:
            await r.delete_table(timeout=DETACH_SECONDS)
        finally:
            for argv in reversed(cleanup):
                await command(r.target, r.session, *argv, check=False)
            resource.setrlimit(resource.RLIMIT_NOFILE, limits)
