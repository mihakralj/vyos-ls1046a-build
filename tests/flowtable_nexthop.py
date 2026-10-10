"""Nexthop-object replacement cannot leave an obsolete hardware route live."""
from __future__ import annotations

import asyncio
import json
import time

from ask_orch.uart import Console
from _topology import TARGET_WAN_IF
from _flowtable_connections import (FLOWS, peer)
from _flowtable_module import (table)
from _flowtable_mtu import (table_identity)
from _flowtable_rig import (artifact_dir, HEALTH_BASELINE, WAN_IP, command, console_command, status_text)
from _flowtable_selective_neighbour import (hardware, warm)
from _flowtable_tcp import (software_tx)

NHID = "42135"


async def test_object_replacement(connections):
    r = connections
    flows = [{**flow, "lan": r.lan_ip} for flow in FLOWS[:2]]
    existing = json.loads((await command(r.target, r.session, "ip", "-j", "nexthop", "show"))["stdout"])
    assert not any(str(n["id"]) == NHID for n in existing), existing
    await r.delete_table()
    created = False
    with Console.target(log_path=str(artifact_dir() / "nexthop-uart.log")) as con:
        await asyncio.to_thread(con.login, "root", None)
        try:
            await command(r.target, r.session, "ip", "-4", "nexthop", "add", "id", NHID, "dev", TARGET_WAN_IF)
            created = True
            await command(r.target, r.session, "ip", "route", "replace", WAN_IP + "/32", "nhid", NHID)
            # Registration dumps existing objects; unregister also dumps DEL.
            # Neither may leave a stale invalidation on a fresh consumer.
            await console_command(con, "rmmod", "ask_flowtable")
            await console_command(con, "modprobe", "ask_flowtable")
            fresh = await r.state()
            assert all(fresh[k] == 0 for k in ("bindings", "entries", "invalidated", "fatal", "errors")), fresh
            # The reload restarted the error count; health is measured from it.
            HEALTH_BASELINE["errors"] = fresh["errors"]
            r.record("nexthop-existing-object-reload", fresh)
            await table(r)
            identity = await table_identity(r)
            async with peer(r, flows) as p:
                await warm(r, p, [0, 1], "nexthop-initial-admission", flows)
                before = await hardware(r, p, "nexthop-initial-hardware", flows)
                await p.rpc("start", [0], count=0, interval=0.01, allow_loss=True)
                try:
                    # TCP stays open and idle throughout the route change.
                    await console_command(con, "ip", "-4", "nexthop", "replace", "id", NHID, "blackhole")
                    deadline = time.monotonic() + 10
                    while True:
                        stopped = status_text((await console_command(con, "cat", "/proc/cdx_flowtable"))["stdout"])
                        if stopped["invalidation_done"] and not stopped["entries"]:
                            break
                        assert time.monotonic() < deadline, stopped
                        await asyncio.sleep(0.05)
                    assert stopped["invalidated"] == 1 and stopped["bindings"] == 2, stopped
                    assert all(stopped[k] == 0 for k in ("handle_refs", "neighbour_refs", "quarantine", "fatal", "errors"))
                    assert stopped["deletes"] == before["deletes"] + 4
                    assert stopped["installs"] == before["installs"]
                    await asyncio.sleep(0.2)
                    delivered = len(r.echo.received)
                    await asyncio.sleep(1)
                    assert len(r.echo.received) == delivered, "old hardware bypassed the blackhole object"
                    r.record("nexthop-blackhole", {"before": before, "stopped": stopped, "wan_deliveries": 0})
                finally:
                    await console_command(con, "ip", "-4", "nexthop", "replace", "id", NHID, "dev", TARGET_WAN_IF)
                transition = await p.rpc("stop", [0])
                assert transition["0"]["lost"] > 0, transition
                assert await table_identity(r) == identity
                tx_before = await software_tx(r)
                transfers = await p.batch([0, 1], 128, 0.01)
                tx_after = await software_tx(r)
                held = await r.state()
                assert held["invalidated"] == held["invalidation_done"] == 1 and held["entries"] == 0
                assert held["installs"] == before["installs"] and held["rearms"] == before["rearms"]
                delta = {dev: tx_after[dev] - tx_before[dev] for dev in tx_before}
                assert all(value >= 128 for value in delta.values()), delta
                r.record("nexthop-explicit-recovery-required", {"state": held, "transition": transition,
                                                                "transfers": transfers, "software_tx": delta})
                await r.delete_table()
                await table(r)
                await warm(r, p, [0, 1], "nexthop-readmitted", flows)
                recovered = await hardware(r, p, "nexthop-recovered-hardware", flows)
                assert recovered["rearms"] == before["rearms"] + 1
        finally:
            # Restore the fixture's directly connected host route before
            # deleting the nexthop object, including after any test failure.
            await console_command(con, "ip", "route", "replace", WAN_IP + "/32", "dev", TARGET_WAN_IF)
            await r.delete_table()
            if created:
                await command(r.target, r.session, "ip", "nexthop", "del", "id", NHID)
