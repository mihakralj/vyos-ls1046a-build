"""Prove independent connection lifetimes within the admission budget."""
from __future__ import annotations

from _flowtable_connections import (ALL, by_key, delete_connection, hardware_batch, healthy, keys, peer, unchanged)

import asyncio
import pytest


from _flowtable_tcp import cpu, cpu_delta

@pytest.mark.parametrize("connections", [5], indirect=True, ids=["expiry-5s"])
async def test_independent_lifetimes(connections):
    r = connections
    assert (await r.state())["entries"] == 0
    cpu_before = await cpu(r)
    await asyncio.sleep(4)
    cpu_after = await cpu(r)
    assert (await r.state())["entries"] == 0
    r.record("connections-idle", {"cpu": cpu_delta(cpu_before, cpu_after),
                                  "cpu_ticks_before": cpu_before, "cpu_ticks_after": cpu_after})
    async with peer(r) as p:
        await p.batch(ALL)
        installed = await r.wait(lambda s: s["entries"] == 64)
        healthy(installed)
        assert by_key(installed).keys() == keys(r, ALL), installed
        baseline = await hardware_batch(r, p, ALL, "connections-full")

        # Keep every other connection active while deleting exactly one UDP CT.
        live = ALL[1:]
        await p.rpc("start", live, count=0, interval=0.01)
        deleted = await delete_connection(r, 0)
        assert deleted["rc"] == 0, deleted
        removed = await r.wait(lambda s: s["entries"] == 62)
        assert by_key(removed).keys() == keys(r, live), removed
        unchanged(r, baseline, removed, live)
        assert removed["installs"] == baseline["installs"] and removed["deletes"] == baseline["deletes"] + 2
        r.record("connections-delete", {"before": baseline, "after": removed, "conntrack": deleted})

        # A normal TCP close must retire just its two directions too.
        await p.rpc("stop", [1])
        await p.rpc("close", [1])
        live.remove(1)
        closed = await r.wait(lambda s: s["entries"] == 60)
        assert by_key(closed).keys() == keys(r, live), closed
        unchanged(r, baseline, closed, live)
        assert closed["installs"] == baseline["installs"] and closed["deletes"] == baseline["deletes"] + 4
        r.record("connections-fin", closed)

        # Shared-neighbour activity must not refresh an idle connection.
        await p.rpc("stop", [2])
        live.remove(2)
        idle = await r.wait(lambda s: s["entries"] == 58, timeout=15)
        assert by_key(idle).keys() == keys(r, live), idle
        unchanged(r, baseline, idle, live)
        assert idle["installs"] == baseline["installs"] and idle["deletes"] == baseline["deletes"] + 6
        for key in keys(r, live):
            assert int(by_key(idle)[key]["packets"]) > int(by_key(removed)[key]["packets"]), (key, removed, idle)
        r.record("connections-expiry", idle)

        # Reuse the deleted UDP tuple and refresh the still-open idle one while
        # all surviving connections keep their original hardware ownership.
        await p.batch([0, 2])
        restored = await r.wait(lambda s: s["entries"] == 62)
        unchanged(r, baseline, restored, live)
        assert by_key(restored).keys() == keys(r, [0, 2] + live), restored
        assert restored["installs"] == baseline["installs"] + 4 and restored["deletes"] == idle["deletes"], restored
        for key in keys(r, [0, 2]):
            assert int(by_key(restored)[key]["packets"]) <= 32, (key, restored)
        r.record("connections-reuse", restored)
        background = await p.rpc("stop", live)
        assert all(report["count"] > 0 for report in background.values()), background
        r.record("connections-background", background)
        await hardware_batch(r, p, [0, 2] + live, "connections-survivors")

        # Every UDP record is unique across warmup, steady traffic and reuse.
        assert r.echo.received and all(n == 1 for n in r.echo.received.values())
        r.record("connections-udp", {"unique_records": len(r.echo.received),
                                      "duplicates": sum(n - 1 for n in r.echo.received.values())})
