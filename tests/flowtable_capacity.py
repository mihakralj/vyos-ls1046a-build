"""Fill the production admission budget, overflow it, and reuse it under traffic."""
from __future__ import annotations

from _flowtable_capacity import BASE, CAPACITY, CONNECTIONS, DETACH_BOUND_SECONDS, DETACH_SECONDS, READER_BOUND_SECONDS, REPLACEMENTS, batch, check_rows, enable_snapshots, delete_udp, hardware_window, lan_counters, record_delivery, socket_drops, start, unchanged, wait_entries

import json
import resource
import socket
import struct
import time


from _topology import TARGET_LAN_IF, TARGET_WAN_IF
from _flowtable_connections import (healthy, peer)
from _flowtable_rig import (DPORT, TABLE, WAN_IP, command, read)
from _flowtable_tcp import software_tx


async def test_overflow_and_reuse(rig):
    r = rig
    # Thousands of independent peers share one UDP receiver. Give this socket
    # a bounded buffer without changing host-wide networking sysctls.
    receiver = r.echo.transport.get_extra_info("socket")
    receiver.setsockopt(socket.SOL_SOCKET, 33, 4 << 20)  # Linux SO_RCVBUFFORCE
    assert receiver.getsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF) >= 4 << 20
    assert socket_drops(receiver) == 0
    initial = await r.state()
    assert initial["max_entries"] == CAPACITY and initial["entries"] == 0, initial
    assert BASE + (CONNECTIONS + REPLACEMENTS) // 2 < 65536
    address = json.loads((await command(r.target, r.session, "ip", "-j", "-4", "addr",
                                       "show", "dev", TARGET_WAN_IF))["stdout"])[0]
    public = next(a["local"] for a in address["addr_info"] if a["family"] == "inet")
    specs = [{"id": i, "proto": "tcp" if i & 1 else "udp", "sport": BASE + i // 2,
              "remote": [public, BASE + i // 2]} for i in range(CONNECTIONS + REPLACEMENTS)]
    await enable_snapshots(r, lan=r.lan_ip, wan=WAN_IP, public=public,
                           lan_if=TARGET_LAN_IF, wan_if=TARGET_WAN_IF, dport=DPORT,
                           base=BASE, count=CONNECTIONS)
    full_ids = list(range(CONNECTIONS))
    extra_ids = list(range(CONNECTIONS, CONNECTIONS + REPLACEMENTS))
    retired_ids = full_ids[:REPLACEMENTS]
    survivors = full_ids[REPLACEMENTS:]
    cleanup = []
    r.record("capacity-lan-before", await lan_counters(r))
    limits = resource.getrlimit(resource.RLIMIT_NOFILE)
    required = 2 * len(specs) + 512
    assert required <= limits[1], (required, limits)
    resource.setrlimit(resource.RLIMIT_NOFILE, (max(limits[0], required), limits[1]))
    try:
        # Use the existing native MASQUERADE policy. The rig exempts only SPORT,
        # outside this workload. Longer idle timeouts allow deterministic bulk
        # setup; every admitted connection remains active during pressure.
        for proto in ("udp", "tcp"):
            name = f"net.netfilter.nf_flowtable_{proto}_timeout"
            value = (await read(r.target, r.session, "/proc/sys/" + name.replace(".", "/"))).strip()
            await command(r.target, r.session, "sysctl", "-w", f"{name}=120")
            cleanup.append(["sysctl", "-w", f"{name}={value}"])
            # This dedicated service tuple belongs entirely to the test.
            await command(r.target, r.session, "conntrack", "-D", "-p", proto,
                          "--orig-src", r.lan_ip, "--orig-dst", WAN_IP, "--dport", str(DPORT), check=False, quiet=True)
        await r.nft(f'''table inet {TABLE} {{
 flowtable fast {{ hook ingress priority 0; devices = {{ {TARGET_LAN_IF}, {TARGET_WAN_IF} }}; flags offload; }}
 chain forward {{ type filter hook forward priority 0; policy accept;
 ip saddr {r.lan_ip} ip daddr {WAN_IP} meta l4proto {{ tcp, udp }} th sport {BASE}-{BASE + len(specs)//2 - 1} th dport {DPORT} flow add @fast
 }}
}}''')
        memory_before = await read(r.target, r.session, "/proc/meminfo")
        async with peer(r, specs, initial_ids=[], lease=600, tcp_size=1024) as p:
            started = time.monotonic()
            warm_reports = {}
            # Bound the initial SYN/datagram burst, while previously opened
            # connections keep running. The admission cap itself is unchanged.
            for offset in range(0, len(full_ids), 256):
                group = full_ids[offset:offset + 256]
                await p.rpc("open", group)
                warm_reports.update(await batch(p, group, count=4, interval=0.025))
                await start(p, group)
            record_delivery(r, p, "capacity-warmup", warm_reports)
            await wait_entries(r, CAPACITY, p)
            full = await r.state()
            healthy(full)
            assert full["flow_count"] == CAPACITY
            assert full["installs"] - full["deletes"] == CAPACITY
            assert full["handle_refs"] == CAPACITY
            await check_rows(r, full)
            r.record("capacity-full", {"state": full, "fill_seconds": time.monotonic() - started,
                                      "memory_before": memory_before,
                                      "memory_full": await read(r.target, r.session, "/proc/meminfo")})

            steady = await hardware_window(r, full, "capacity-steady")
            assert socket_drops(receiver) == 0, "generator UDP receive queue overflow"

            await p.rpc("open", extra_ids)
            tx0 = await software_tx(r)
            overflow_reports = await batch(p, extra_ids, count=8, interval=0.25)
            tx1, overflow = await software_tx(r), await r.state()
            await unchanged(r, steady, overflow)
            assert overflow["entries"] == CAPACITY
            assert overflow["installs"] == steady["installs"] and overflow["deletes"] == steady["deletes"]
            assert overflow["rejects"] > steady["rejects"]
            tx = {dev: tx1[dev] - tx0[dev] for dev in tx0}
            assert all(n >= 8 * REPLACEMENTS for n in tx.values()), tx
            r.record("capacity-overflow", {"state": overflow, "software_tx": tx, "transfers": overflow_reports})

            record_delivery(r, p, "capacity-retired-transfers", await p.rpc("stop", retired_ids))
            await p.rpc("close", retired_ids)
            await delete_udp(r, [specs[ident]["sport"] for ident in retired_ids[::2]])
            assert not (await p.rpc("status", compact=True))["errors"]
            await wait_entries(r, CAPACITY - 2 * REPLACEMENTS, p)
            freed = await r.state()
            removed = await unchanged(r, full, freed, allow_missing=True)
            assert removed["missing_count"] == 2 * REPLACEMENTS and removed["unexpected_count"] == 0, removed
            assert freed["installs"] == full["installs"]
            assert freed["deletes"] == full["deletes"] + 2 * REPLACEMENTS

            # Same overflow sockets and conntracks retry native hardware
            # admission when their software flow approaches its refresh window.
            await start(p, extra_ids, interval=0.25)
            await wait_entries(r, CAPACITY, p, timeout=150)
            reused = await r.state()
            r.record("capacity-reuse", reused)
            await unchanged(r, full, reused, excluded_from=freed["snapshot"])
            await check_rows(r, reused, retire=retired_ids, admit=extra_ids)
            # A new generation can lose RTNL between directional admissions;
            # with no IPsec policy configured that only declines the offer, and
            # the software path offers it again. Anything retired here is a
            # generation the adapter retired for its own admission reason,
            # which it counts. Existing owners must survive unchanged; account
            # for this bounded, explicitly reported admission recovery.
            retries = reused["admission_invalidations"] - freed["admission_invalidations"]
            retired = reused["deletes"] - freed["deletes"]
            assert 0 <= retired <= 2 * retries, (retired, retries)
            assert reused["installs"] - freed["installs"] == 2 * REPLACEMENTS + retired
            after = await hardware_window(r, reused, "capacity-reused-steady")
            reports = await p.rpc("stop", survivors + extra_ids)
            record_delivery(r, p, "capacity-transfers", reports)
            # A committed route replacement affects every generation. This
            # exercises the full atomic dependency walk and worker retirement,
            # then repopulates the entire cap using the same live sockets. The
            # replacement changes only the advertised MSS, which forwarding
            # never reads: a smaller MTU would keep every UDP upload in Linux,
            # and the cap could not be filled again.
            started = time.monotonic()
            await command(r.target, r.session, "ip", "route", "replace", f"{WAN_IP}/32",
                          "dev", TARGET_WAN_IF, "advmss", "1300")
            route_seconds = time.monotonic() - started
            drained = await wait_entries(r, 0, p)
            assert drained["route_invalidations"] - after["route_invalidations"] == CONNECTIONS
            assert drained["installs"] == drained["deletes"]
            r.record("capacity-route-drain", {"state": drained, "command_seconds": route_seconds,
                                             "retirement_seconds": time.monotonic() - started})
            # Retirement goes in bounded batches, each behind one barrier, so
            # a reader waits for a batch rather than the whole table: one
            # walk under the transaction kept readers out for 13 s (A327).
            assert drained["longest_read_seconds"] < READER_BOUND_SECONDS, drained
            # Reuse the initial admission pacing after bulk retirement too.
            # This verifies full occupancy/recovery without conflating it with
            # an unpaced simultaneous connection-admission stress test.
            recovering = survivors + extra_ids
            warm_reports = {}
            for offset in range(0, len(recovering), 256):
                group = recovering[offset:offset + 256]
                warm_reports.update(await batch(p, group, count=4, interval=0.025))
                await start(p, group)
            record_delivery(r, p, "capacity-recovery-warmup", warm_reports)
            await wait_entries(r, CAPACITY, p)
            regenerated = await r.state()
            healthy(regenerated)
            await check_rows(r, regenerated, mtu=r.port_mtu)
            assert regenerated["installs"] - regenerated["deletes"] == CAPACITY
            r.record("capacity-regenerated", regenerated)
            await hardware_window(r, regenerated, "capacity-regenerated-steady")
            reports = await p.rpc("stop", survivors + extra_ids)
            record_delivery(r, p, "capacity-regenerated-transfers", reports)
            # Exercise whole-table detachment while every socket is still open.
            final = await r.delete_table(timeout=DETACH_SECONDS)
            detach = final["delete_seconds"]
            r.record("capacity-drain", {"state": final, "seconds": detach,
                                       "memory_drained": await read(r.target, r.session, "/proc/meminfo")})
            assert final["installs"] == final["deletes"]
            assert all(final[k] == 0 for k in ("entries", "handle_refs", "neighbour_refs", "quarantine"))
            assert final["errors"] == initial["errors"], (initial, final)
            assert socket_drops(receiver) == 0, "generator UDP receive queue overflow"
            # The retirement holds Netfilter's block lock throughout, so its
            # length is a property to bound, not only to wait out; CDX's
            # transaction it lets go between batches.
            assert detach < DETACH_BOUND_SECONDS, detach
            assert final["longest_read_seconds"] < READER_BOUND_SECONDS, final
    finally:
        try:
            records = {}
            for data, count in r.echo.received.items():
                ident, serial = struct.unpack("!IQ", data[:12])
                records.setdefault(ident, []).append([serial, count])
            r.record("capacity-receiver", {"drops": socket_drops(receiver), "records": records})
            r.record("capacity-lan-after", await lan_counters(r))
            r.record("capacity-final-state", await r.state())
            failures = []
            try:
                await r.delete_table(timeout=DETACH_SECONDS)
            except Exception as error:
                failures.append(str(error))
            for proto in ("udp", "tcp"):
                await command(r.target, r.session, "conntrack", "-D", "-p", proto,
                              "--orig-src", r.lan_ip, "--orig-dst", WAN_IP, "--dport", str(DPORT), check=False, quiet=True)
            for argv in reversed(cleanup):
                result = await command(r.target, r.session, *argv, check=False)
                if result["rc"]:
                    failures.append(result)
            assert not failures, failures
        finally:
            resource.setrlimit(resource.RLIMIT_NOFILE, limits)
