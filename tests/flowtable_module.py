"""Prove adapter unload/reload without replacing its CDX hardware provider."""
from __future__ import annotations

from _flowtable_module import (table)

import asyncio
import errno

from ask_orch.uart import Console
from _ioctl import CDX_CTRL_DPA_SET_PARAMS, SIZEOF_CDX_CTRL_SET_DPA_PARAMS
from _topology import TARGET_LAN_IF, TARGET_WAN_IF
from _flowtable_connections import (FLOWS, peer)
from _flowtable_rig import (artifact_dir, HEALTH_BASELINE, TABLE, console_command, read)
from _flowtable_selective_neighbour import (hardware, warm)
from _flowtable_tcp import (software_tx)


async def test_lifecycle(connections):
    r = connections
    flows = [{**f, "lan": r.lan_ip} for f in FLOWS[:2]]
    ids = [0, 1]
    con = Console.target(log_path=str(artifact_dir() / "module-uart.log"))
    await asyncio.to_thread(con.login, "root", None)
    try:
        await console_command(con, "test", "-e", "/sys/module/cdx/holders/ask_flowtable")
        pinned = await console_command(con, "rmmod", "cdx", check=False)
        assert pinned["rc"] != 0 and "in use" in pinned["stdout"], pinned
        await console_command(con, "rmmod", "ask_flowtable")
        await r.nft(f"delete table inet {TABLE}")
        provider = (await read(r.target, r.session, "/sys/module/cdx/refcnt")).strip()

        async def absent():
            for path in ("/sys/module/ask_flowtable", "/proc/cdx_flowtable",
                         "/sys/module/cdx/holders/ask_flowtable"):
                assert (await console_command(con, "test", "-e", path, check=False))["rc"] == 1, path
            assert (await read(r.target, r.session, "/sys/module/cdx/refcnt")).strip() == provider
            reply = await r.target.ioctl_send(r.session, "/dev/cdx_ctrl", CDX_CTRL_DPA_SET_PARAMS,
                                             bytes(SIZEOF_CDX_CTRL_SET_DPA_PARAMS))
            assert reply["errno"] == errno.ENOTTY, reply

        failures = []
        for stage in range(1, 7):
            result = await console_command(con, "modprobe", "ask_flowtable", f"init_fail_stage={stage}", check=False)
            assert result["rc"] != 0, result
            await absent()
            # Successful reclaim proves no claim, live direction or quarantine
            # escaped the failed initialization. Then exercise normal exit.
            await console_command(con, "modprobe", "ask_flowtable")
            state = await r.state()
            assert state["bindings"] == state["entries"] == state["fatal"] == state["errors"] == 0, state
            await console_command(con, "rmmod", "ask_flowtable")
            await absent()
            failures.append({"stage": stage, "failure": result, "reclaimed": state})
        r.record("module-failed-loads", {"provider_refcount": provider, "pinned": pinned, "stages": failures})
        await console_command(con, "modprobe", "ask_flowtable")
        # A fresh adapter counts errors from zero, so the boot's baseline no
        # longer applies; health from here on means none since this load.
        HEALTH_BASELINE["errors"] = (await r.state())["errors"]
        await table(r)
        async with peer(r, flows) as p:
            await warm(r, p, ids, "module-initial-admission", flows)
            await hardware(r, p, "module-initial-hardware", flows)
            for barrier in (False, True):
                label = "module-barrier" if barrier else "module-healthy"
                before = await r.state()
                if barrier:
                    # This hook fails a deletion's barrier only; recovery
                    # syncs are real. The unload retires the four directional
                    # deletions in batches, each behind one barrier: fail the
                    # first, which leaves its batch for unload's recovery.
                    reply = await r.target.fs_write(r.session, "/proc/fm_ehash_hcsync_fail", "1")
                    assert reply["errno"] == 0, reply
                await p.rpc("start", ids, count=0, interval=0.01)
                await console_command(con, "rmmod", "ask_flowtable", timeout=25)
                await absent()
                if barrier:
                    knob = await read(r.target, r.session, "/proc/fm_ehash_hcsync_fail")
                    assert knob.strip() == "armed=0", knob
                tx_before = await software_tx(r)
                await asyncio.sleep(2)
                tx_after = await software_tx(r)
                tx = {dev: tx_after[dev] - tx_before[dev] for dev in tx_before}
                assert tx[TARGET_LAN_IF] >= 64 and tx[TARGET_WAN_IF] >= 64, tx
                reports = await p.rpc("stop", ids)
                assert all(v["count"] >= 64 for v in reports.values()), reports
                await console_command(con, "modprobe", "ask_flowtable")
                # Linux does not replay device-specific FT bindings on module
                # registration. The old table stays in software until recreated.
                detached = await r.state()
                assert detached["bindings"] == detached["entries"] == detached["fatal"] == 0, detached
                HEALTH_BASELINE["errors"] = detached["errors"]
                await p.batch(ids, count=64)
                assert (await r.state())["entries"] == 0
                await r.delete_table()
                await table(r)
                await warm(r, p, ids, label + "-readmitted", flows)
                after = await hardware(r, p, label + "-hardware", flows)
                r.record(label, {"before": before, "software_tx": tx, "continuous_transfers": reports,
                                 "detached": detached, "after": after})
            # Exercise normal UNBIND repeatedly with native statistics work
            # and live TCP/UDP. Moving a callback needs the same write lock as
            # freeing it; a worker must never traverse the temporary list.
            await p.rpc("start", ids, count=0, interval=0.01)
            retired = []
            for _ in range(8):
                await r.wait(lambda s: s["entries"] == 4)
                await asyncio.sleep(1.05)
                state = await r.delete_table()
                assert state["handle_refs"] == state["neighbour_refs"] == state["quarantine"] == 0, state
                assert state["installs"] == state["deletes"] and not state["errors"], state
                retired.append(state)
                await table(r)
            reports = await p.rpc("stop", ids)
            await warm(r, p, ids, "module-unbind-readmitted", flows)
            await hardware(r, p, "module-unbind-hardware", flows)
            r.record("module-unbind-stress", {"retired": retired, "continuous_transfers": reports})
    finally:
        try:
            reply = await r.target.fs_write(r.session, "/proc/fm_ehash_hcsync_fail", "0")
            assert reply["errno"] == 0, reply
            await console_command(con, "modprobe", "ask_flowtable")
        finally:
            con.close()
