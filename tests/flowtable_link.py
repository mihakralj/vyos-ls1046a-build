"""Recover established flows across administrative outages of either real port."""
from __future__ import annotations

import asyncio
import json

from ask_orch.uart import Console
from _topology import TARGET_LAN_IF, TARGET_WAN_IF
from _flowtable_connections import (FLOWS, healthy, peer)
from _flowtable_mtu import (table_identity)
from _flowtable_rig import (artifact_dir, WAN_IP, console_command, read, status_text)
from _flowtable_selective_neighbour import (hardware, warm)


async def test_recovery(connections):
    r = connections
    flows = [{**f, "lan": r.lan_ip} for f in FLOWS[:2]]
    identity = await table_identity(r)
    boot_id = await read(r.target, r.session, "/proc/sys/kernel/random/boot_id")
    with Console.target(log_path=str(artifact_dir() / "link-uart.log")) as con:
        await asyncio.to_thread(con.login, "root", None)

        async def state():
            return status_text((await console_command(con, "cat", "/proc/cdx_flowtable"))["stdout"])

        async def restore(dev):
            await console_command(con, "ip", "link", "set", "dev", dev, "up")
            # Recreate only the fixture's routes/neighbours if the down event
            # discarded them. No table, conntrack or endpoint socket is reset.
            address, mac = (r.lan_ip, r.lan_mac) if dev == TARGET_LAN_IF else (WAN_IP, r.wan_mac)
            await console_command(con, "ip", "route", "replace", address + "/32", "dev", dev)
            await console_command(con, "ip", "neigh", "replace", address, "lladdr", mac,
                                  "nud", "permanent", "dev", dev)

        async with peer(r, flows) as p:
            await warm(r, p, [0, 1], "link-initial-admission", flows)
            initial = await hardware(r, p, "link-initial-hardware", flows)
            for cycle in range(2):
                for dev in (TARGET_LAN_IF, TARGET_WAN_IF):
                    before = await r.state()
                    await p.rpc("start", [0], count=0, interval=0.01, allow_loss=True)
                    await p.rpc("start", [1], count=0, interval=0.01)
                    try:
                        await console_command(con, "ip", "link", "set", "dev", dev, "down")
                        down = await state()
                        healthy(down)
                        assert down["entries"] == down["handle_refs"] == down["neighbour_refs"] == 0, down
                        assert down["installs"] == before["installs"] and down["deletes"] == before["deletes"] + 4
                        assert down["link_invalidations"] == before["link_invalidations"] + 2, (before, down)
                        links = json.loads((await console_command(con, "ip", "-j", "link", "show", "dev", dev))["stdout"])
                        assert "UP" not in links[0]["flags"], links
                        await asyncio.sleep(0.2)
                        received = sum(r.echo.received.values())
                        await asyncio.sleep(1)
                        assert sum(r.echo.received.values()) == received, "UDP passed a down port"
                        held = await state()
                        assert held["entries"] == 0 and held["installs"] == before["installs"], held
                    finally:
                        await restore(dev)
                    reports = await p.rpc("stop", [0, 1])
                    assert reports["0"]["lost"] > 0 and reports["1"]["lost"] == 0, reports
                    assert reports["1"]["count"] > 0, reports
                    after = await warm(r, p, [0, 1], f"link-{cycle}-{dev}-admission", flows)
                    # Traffic may briefly use the connected route between UP
                    # and restoration of the fixture's host route. Each such
                    # generation must also retire without leaking.
                    assert after["installs"] >= before["installs"] + 4, (before, after)
                    assert after["installs"] - before["installs"] == after["deletes"] - before["deletes"]
                    assert all(int(f["mtu"]) == r.port_mtu for f in after["flows"]), after
                    assert after["rearms"] == initial["rearms"] and not after["invalidation_done"], after
                    assert await table_identity(r) == identity
                    r.record(f"link-{cycle}-{dev}", {"before": before, "down": down, "held": held,
                                                   "after": after, "transfers": reports, "table": identity})
                    await hardware(r, p, f"link-{cycle}-{dev}-hardware", flows)
            assert await read(r.target, r.session, "/proc/sys/kernel/random/boot_id") == boot_id
        # The per-test kernel window checks and retains warnings locally.
        r.record("link-complete", {"state": await r.state(), "table": identity, "boot_id": boot_id})
