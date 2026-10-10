"""A classifier delete that rebuilds a crowded bucket while the allocator fails."""
from __future__ import annotations

import asyncio

from _ehash_bucket import crowded_ipv4
from _topology import TARGET_LAN_IF, TARGET_WAN_IF
from ask_orch.uart import Console
from _flowtable_connections import (healthy, peer)
from _flowtable_failslab import (slab_fault)
from _flowtable_rig import (artifact_dir, DPORT, TABLE, WAN_IP, Echo, command)

SERVERS = [DPORT, DPORT + 3, DPORT + 4, DPORT + 6]
COUNT = 256


def udp_rows(state):
    return {f"{f['in']} {f['src']} {f['dst']}": int(f["packets"]) for f in state["flows"] if f["proto"] == "17"}


async def in_hardware(r, p, ids, label):
    """Every direction counts every frame in the classifier."""
    before = udp_rows(await r.state())
    await p.batch(ids, count=COUNT, interval=0.01)
    after = udp_rows(await r.state())
    r.record(label, {"before": before, "after": after})
    assert before.keys() == after.keys() and len(after) == 2 * len(ids), (before, after)
    assert all(after[k] - before[k] == COUNT for k in after), (before, after)


async def test_rebuilds_under_allocation_failure(rig):
    """Four flows whose LAN-side keys share one classifier bucket, removed
    with every allocation a classifier delete makes failing.

    Deleting a key from a bucket that holds three or more rebuilds the
    bucket's node without it -- the one allocation a delete makes, atomic and
    free to fail under memory pressure. The rebuild takes the table's spare
    node instead, and the barrier after it hands back the node it replaced,
    so every key comes out: nothing abandoned, no terminal latch. Without the
    spare, the first such delete left its key linked and stopped the
    datapath until a reboot. The bucket then takes the same four again and
    the FMan forwards them."""
    r = rig
    pairs = crowded_ipv4(SERVERS)
    specs = [{"id": i, "proto": "udp", "sport": s, "connect_port": d} for i, (s, d) in enumerate(pairs)]
    ids = [spec["id"] for spec in specs]
    sports = ", ".join(str(s) for s, _ in pairs)
    dports = ", ".join(str(d) for d in SERVERS)
    table = f'''table inet {TABLE} {{
 flowtable fast {{ hook ingress priority 0; devices = {{ {TARGET_LAN_IF}, {TARGET_WAN_IF} }}; flags offload; }}
 chain forward {{ type filter hook forward priority 0; policy accept;
 ip saddr {r.lan_ip} ip daddr {WAN_IP} udp sport {{ {sports} }} udp dport {{ {dports} }} flow add @fast
 }}
}}'''
    initial = await r.state()
    loop = asyncio.get_running_loop()
    servers = [(await loop.create_datagram_endpoint(Echo, local_addr=(WAN_IP, port)))[0]
               for port in SERVERS[1:]]
    # The fault lease is launched and cancelled over the UART, which also
    # carries the rig's own cleanup should a delete stop the datapath.
    r.recovery_console = Console.target(log_path=str(artifact_dir() / "ehash-delete-uart.log"))
    await asyncio.to_thread(r.recovery_console.login, "root", None)

    async def forget():
        for s, d in pairs:
            await command(r.target, r.session, "conntrack", "-D", "-p", "udp", "--orig-src", r.lan_ip,
                          "--orig-dst", WAN_IP, "--sport", str(s), "--dport", str(d), check=False)
    try:
        await forget()
        await r.nft(table)
        async with peer(r, specs, initial_ids=[]) as p:
            await p.rpc("open", ids)
            await p.batch(ids, count=8, interval=0.02)
            healthy(await r.wait(lambda s: s["entries"] == 2 * len(ids), timeout=30))
            await in_hardware(r, p, ids, "ehash-delete-crowded")
            async with slab_fault(r, "ehash-delete", "ehash-delete", continuous=True,
                                  console=r.recovery_console) as fault:
                final = await r.delete_table(timeout=15)
            # A continuous lease writes its result once it is cancelled; it
            # asserts the allocator was failed at least once.
            hit = await fault.hit()
            r.record("ehash-delete", {"pairs": pairs, "state": final, "failed_allocations": hit["failures"]})
            assert all(final[k] == 0 for k in ("entries", "handle_refs", "neighbour_refs", "quarantine",
                                                "fatal")), final
            assert final["installs"] == final["deletes"], final
            assert final["errors"] == initial["errors"], (initial, final)
            assert "en_cumulative_entry" not in "".join(hit["kernel_records"]), hit["kernel_records"]

            await r.nft(table)
            await p.batch(ids, count=8, interval=0.02)
            healthy(await r.wait(lambda s: s["entries"] == 2 * len(ids), timeout=30))
            await in_hardware(r, p, ids, "ehash-delete-readmitted")
    finally:
        for server in servers:
            server.close()
        await r.delete_table(timeout=15)
        await forget()
