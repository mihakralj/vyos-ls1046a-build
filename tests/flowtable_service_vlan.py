"""Recover persistent connections when a routed VLAN disappears and returns."""
from __future__ import annotations

from _flowtable_service_vlan import ADDRESS

from _flowtable_service_vlan import (DUT_IF, NETNS, VID, attempts, balanced, create_vlan, denied, received, tagged, vlan_row)

import asyncio
import json
import time

import pytest

from _topology import TARGET_LAN_IF
from _flowtable_connections import (peer)
from _flowtable_rig import (console_command, read)
from _flowtable_selective_neighbour import (hardware, unchanged, warm)
from _flowtable_service import FIRST, service_status, supervision_status, wait_service


@pytest.mark.parametrize("target", ["dev-stats", "hardware"])
async def test_admission_failslab(vlan_service, target):
    """A tagged direction's admission takes a reference on its VLAN device's
    statistics record before the hardware entry exists, which no untagged flow
    does. A hardware failure after that has to hand the reference back, and the
    connection recovers by traffic alone. A failure creating the record itself
    is not an admission failure: that direction forwards in hardware and
    counts nowhere, which the record's references and counters both show.
    Untagged controls are undisturbed either way."""
    from _flowtable_failslab import (slab_fault)

    r = vlan_service
    vlan = {"lan": ADDRESS, "netns": NETNS}
    flows = [
        {"id": 0, "proto": "udp", "sport": FIRST, "lan": r.lan_ip},
        {"id": 1, "proto": "tcp", "sport": FIRST, "lan": r.lan_ip},
        {"id": 2, "proto": "udp", "sport": FIRST, **vlan},
    ]
    service = await supervision_status(r)
    async with peer(r, flows, initial_ids=[0, 1], lease=300) as p:
        await warm(r, p, [0, 1], "vlan-slab-baseline", flows[:2])
        before = await hardware(r, p, "vlan-slab-baseline-hardware", flows[:2])
        assert not tagged(before) and before["vlan_records"] == r.service_vlan_records_before, before
        async with slab_fault(r, target, "vlan-" + target, keep_alive=(p, [0, 1])) as fault:
            started = time.monotonic()
            await p.rpc("open", [2])
            await p.batch([2], count=32, interval=0.01)
            hit = await fault.hit()
            admitted = await warm(r, p, [0, 1, 2], "vlan-slab-readmitted", flows[:3])
            assert time.monotonic() - started < 20, admitted
        counted = vlan_row(await r.state())
        after = await hardware(r, p, "vlan-slab-hardware", flows[:3])
        unchanged(before, after, [0, 1], flows)
        balanced(after, before["errors"])
        assert tagged(after) and after["vlan_records"] == r.service_vlan_records_before + 1, after
        # One half of the record strips the tag and the other inserts it, and
        # the hardware burst moved each direction of the tagged flow by 256.
        row = vlan_row(after)
        assert row["slot"] == "yes", row
        moved = {half: int(row[half + "_packets"]) - int(counted[half + "_packets"])
                 for half in ("rx", "tx")}
        if target == "dev-stats":
            assert row["refs"] == "1" and sorted(moved.values()) == [0, 256], (counted, row)
        else:
            assert row["refs"] == "2" and moved == {"rx": 256, "tx": 256}, (counted, row)
        assert await supervision_status(r) == service
        r.record("vlan-" + target + "-recovery", {"before": before, "after": after, "hit": hit})


async def test_recreation(vlan_service):
    r = vlan_service
    vlan = {"lan": ADDRESS, "netns": NETNS}
    flows = [
        {"id": 0, "proto": "udp", "sport": FIRST, "lan": r.lan_ip},
        {"id": 1, "proto": "tcp", "sport": FIRST, "lan": r.lan_ip},
        {"id": 2, "proto": "udp", "sport": FIRST, **vlan},
        {"id": 3, "proto": "tcp", "sport": FIRST, **vlan},
        {"id": 4, "proto": "tcp", "sport": FIRST + 1, **vlan},
        {"id": 5, "proto": "udp", "sport": FIRST + 2, "lan": r.lan_ip},
        {"id": 6, "proto": "udp", "sport": FIRST + 2, **vlan},
    ]
    service = await supervision_status(r)
    async with peer(r, flows, initial_ids=[0, 1, 2, 3, 5, 6], lease=300) as p:
        await warm(r, p, [0, 1, 2, 3], "service-vlan-baseline", flows[:4])
        initial = await hardware(r, p, "service-vlan-baseline-hardware", flows[:4])
        for ident in (5, 6):
            await denied(r, p, ident)
        for cycle in range(2):
            label = f"service-vlan-cycle-{cycle}"
            before = await r.state()
            old_index = (await read(r.target, r.session, f"/sys/class/net/{DUT_IF}/ifindex")).strip()
            before_attempts = await attempts(r)
            await p.rpc("start", [2], count=0, interval=0.05, allow_loss=True)
            await p.rpc("start", [3], count=0, interval=0.05)
            samples = []
            try:
                await console_command(r.service_console, "ip", "link", "del", DUT_IF)
                r.service_vlan_created = False
                removed_at = time.monotonic()
                retired = await r.wait(lambda s: not tagged(s) and s["vlan_records"] == r.service_vlan_records_before,
                                       timeout=5)
                r.record(label + "-retired", retired)
                balanced(retired, initial["errors"])
                # Allow in-flight packets to drain, then require zero VLAN
                # delivery while untagged control traffic continues to work.
                await asyncio.sleep(0.2)
                absent_received = received(r, 2)
                while time.monotonic() - removed_at < 6:
                    await p.batch([0, 1], count=32, interval=0.01)
                    state = await r.state()
                    samples.append({"seconds": time.monotonic() - removed_at, "state": state})
                    balanced(state, initial["errors"])
                    assert not tagged(state) and received(r, 2) == absent_received, state
                missing = await r.target.fs_read(r.session, f"/sys/class/net/{DUT_IF}/ifindex")
                assert missing["errno"] == 2, "controller recreated the missing VLAN"
                await denied(r, p, 5)
                r.record(label + "-absent", {"samples": samples, "status": await service_status(r)})
            finally:
                # Restore only the network prerequisite, through independent
                # UART. All admission repair belongs to the running service.
                # Inspect actual state even if deletion lost its acknowledgement.
                links = json.loads((await console_command(r.service_console, "ip", "-j", "link", "show"))["stdout"])
                r.service_vlan_created = DUT_IF in {link["ifname"] for link in links}
                if not r.service_vlan_created:
                    await create_vlan(r, console=True)
            restored_at = time.monotonic()
            reports = await p.rpc("stop", [2, 3])
            r.record(label + "-transfers", reports)
            assert reports["2"]["lost"] > 0 and reports["3"]["lost"] == 0, reports
            assert reports["3"]["count"] > 0, reports
            new_index = (await read(r.target, r.session, f"/sys/class/net/{DUT_IF}/ifindex")).strip()
            assert new_index != old_index, (old_index, new_index)
            status = await wait_service(r, timeout=20, policy_hash=r.service_hash)
            ready = await warm(r, p, [0, 1, 2, 3], label + "-readmitted", flows[:4])
            ready_seconds = time.monotonic() - restored_at
            assert ready_seconds < 20, (ready_seconds, status, ready)
            # Converged: what it took to reinstall, counted before the
            # hardware proof, whose burst then covers a complete health-check
            # period in which the controller must not reinstall a healthy,
            # unchanged policy.
            after_attempts = await attempts(r)
            assert 0 <= after_attempts - before_attempts <= 4, (before_attempts, after_attempts)
            after = await hardware(r, p, label + "-hardware", flows[:4])
            hardware_seconds = time.monotonic() - restored_at
            assert hardware_seconds < 40, hardware_seconds
            balanced(after, initial["errors"])
            assert after["vlan_records"] == initial["vlan_records"], (initial, after)
            for flow in tagged(after):
                tags = (flow["in_vlan"], flow["out_vlan"])
                assert tags == ((str(VID), "-") if flow["in"] == TARGET_LAN_IF else ("-", str(VID))), flow
            assert await attempts(r) == after_attempts
            for ident in (5, 6):
                await denied(r, p, ident)
            assert await supervision_status(r) == service
            assert (await read(r.target, r.session, "/proc/sys/kernel/random/boot_id")).strip() == r.service_boot
            r.record(label + "-recovery", {"before": before, "after": after,
                "old_ifindex": old_index, "new_ifindex": new_index, "ready_seconds": ready_seconds,
                "hardware_seconds": hardware_seconds, "install_attempts": after_attempts - before_attempts,
                "status": status, "transfers": reports})
        await p.rpc("open", [4])
        await warm(r, p, [0, 1, 2, 3, 4], "service-vlan-new-connection", flows[:5])
        await hardware(r, p, "service-vlan-new-hardware", flows[:5])
        for ident in (5, 6):
            await denied(r, p, ident)
