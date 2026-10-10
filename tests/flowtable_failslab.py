"""Real slab failures in binding and asynchronous admission, with service recovery."""
from __future__ import annotations

from _flowtable_failslab import same_service, slab_fault

import time

import pytest

from _flowtable_connections import (peer)
from _flowtable_rig import (console_command)
from _flowtable_selective_neighbour import (hardware, unchanged, warm)
from _flowtable_service import (FLOWS, blocked_probe, service_status, supervision_status, wait_service)


@pytest.mark.parametrize("target", ["binding", "callback"])
async def test_binding_recovery(service, target):
    r = service
    flows = [{**f, "lan": r.lan_ip} for f in FLOWS]
    before_service = await supervision_status(r)
    async with peer(r, flows, initial_ids=[0, 1, 3]) as p:
        await warm(r, p, [0, 1], target + "-baseline", flows[:2])
        before = await hardware(r, p, target + "-before", flows[:2])
        await blocked_probe(r, p)
        async with slab_fault(r, target, target) as fault:
            started = time.monotonic()
            # The actual service recreates the missing table. No apply or
            # restart occurs after the injected deletion/allocation failure.
            await console_command(r.service_console, "nft", "delete", "table", "inet", "ask_flowtable")
            hit = await fault.hit()
            await p.batch([0, 1], count=32, interval=0.01)
            await blocked_probe(r, p)
            samples = await wait_service(r, timeout=20, policy_hash=r.service_hash)
            ready_seconds = time.monotonic() - started
            assert ready_seconds < 20, (ready_seconds, samples)
            await warm(r, p, [0, 1], target + "-readmitted", flows[:2])
            after = await hardware(r, p, target + "-after", flows[:2])
            assert time.monotonic() - started < 40
            assert after["errors"] == before["errors"]
            await p.rpc("open", [2])
            await warm(r, p, [0, 1, 2], target + "-new-flow", flows[:3])
            await hardware(r, p, target + "-new-hardware", flows[:3])
            await blocked_probe(r, p)
            await same_service(r, before_service)
            r.record(target + "-recovery", {"controller_seconds": ready_seconds, "before": before,
                                           "after": after, "hit": hit, "samples": samples})


@pytest.mark.parametrize("target", ["work", "rule", "actions", "entry", "hardware"])
@pytest.mark.parametrize("protocol", ["udp", "tcp"])
async def test_admission_recovery(service, target, protocol):
    r = service
    label = target + "-" + protocol
    flows = [{**f, "lan": r.lan_ip} for f in FLOWS]
    flows[2]["proto"] = protocol
    before_service = await supervision_status(r)
    async with peer(r, flows, initial_ids=[0, 1, 3]) as p:
        await warm(r, p, [0, 1], label + "-baseline", flows[:2])
        before = await hardware(r, p, label + "-before", flows[:2])
        await blocked_probe(r, p)
        async with slab_fault(r, target, label, keep_alive=(p, [0, 1])) as fault:
            started = time.monotonic()
            await p.rpc("open", [2])
            await p.batch([2], count=32, interval=0.01)
            hit = await fault.hit()
            # The original new socket must recover by traffic alone. Keeping
            # controls active also proves selective retirement/clean unwind.
            admitted = await warm(r, p, [0, 1, 2], label + "-readmitted", flows[:3])
            ready_seconds = time.monotonic() - started
            assert ready_seconds < 20, (ready_seconds, admitted)
            after = await hardware(r, p, label + "-hardware", flows[:3])
            assert time.monotonic() - started < 40
            unchanged(before, after, [0, 1], flows)
            assert after["errors"] == before["errors"] and after["rearms"] == before["rearms"]
            assert (await service_status(r))["policy_hash"] == r.service_hash
            await blocked_probe(r, p)
            await same_service(r, before_service)
            r.record(label + "-recovery", {"admission_seconds": ready_seconds, "before": before,
                                         "after": after, "hit": hit})
