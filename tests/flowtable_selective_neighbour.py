"""Retire one peer's TCP/UDP flows while another peer stays in hardware."""
from __future__ import annotations

from _flowtable_selective_neighbour import A, ALL, B

from _flowtable_selective_neighbour import (CHANGED_MAC, FLOWS, PEERS, hardware, retired, unchanged, warm)

import json


from _topology import TARGET_LAN_IF
from _flowtable_connections import peer
from _flowtable_rig import (command, read)


async def test_flowtable_selective_neighbour(selective):
    r = selective
    async with peer(r, FLOWS) as p:
        await warm(r, p, ALL, "selective-initial-admission")
        initial = await hardware(r, p, "selective-initial-hardware")
        await p.rpc("start", B, count=0, interval=0.01)
        # A is idle; B keeps sending on its original sockets throughout all
        # faults. All four flows share WAN resolution, but only A changes.
        await p.rpc("neighbour", ident=0, changes={"mac": CHANGED_MAC})
        await retired(r, initial, "selective-mac-retired")
        before = await warm(r, p, A, "selective-mac-readmitted")
        unchanged(initial, before, B)

        await p.rpc("start", [1], count=0, interval=0.01)
        failure = await p.rpc("neighbour", ident=0, changes={"arp_ignore": 8, "restore_after": 8})
        await command(r.target, r.session, "ip", "neigh", "change", PEERS[0]["lan"],
                      "dev", TARGET_LAN_IF, "nud", "stale")
        await retired(r, before, "selective-unreachable-retired")
        ns = await command(r.target, r.session, "ip", "-j", "neigh", "show", "to", PEERS[0]["lan"])
        # The same TCP socket can already be retrying ARP after FAILED, so the
        # next observable state may be INCOMPLETE. Neither has a usable MAC.
        assert set(json.loads(ns["stdout"])[0]["state"]) & {"FAILED", "INCOMPLETE"}, ns
        restored = await p.rpc("neighbour", ident=0, changes={"arp_ignore": 0})
        report = await p.rpc("stop", [1])
        r.record("selective-tcp-fault", {"failure": failure, "restored": restored,
                                        "neighbour": ns, "transfer": report})
        before = await warm(r, p, A, "selective-unreachable-readmitted")
        unchanged(initial, before, B)

        await command(r.target, r.session, "ip", "neigh", "del", PEERS[0]["lan"], "dev", TARGET_LAN_IF)
        await retired(r, before, "selective-object-retired")
        after = await warm(r, p, A, "selective-object-readmitted")
        unchanged(initial, after, B)
        reports = await p.rpc("stop", B)
        for report in reports.values():
            assert report["count"] > 128, reports
        r.record("selective-unaffected-transfers", reports)
        final = await hardware(r, p, "selective-final-hardware")
        unchanged(initial, final, B)
        assert final["rearms"] == initial["rearms"], (initial, final)
        assert final["neighbour_invalidations"] == initial["neighbour_invalidations"] + 6, (initial, final)


async def test_barrier(selective):
    """An unproven retirement barrier closes admission for every connection."""
    r = selective
    knob = "/proc/fm_ehash_hcsync_fail"
    try:
        async with peer(r, FLOWS) as p:
            before = await warm(r, p, ALL, "selective-barrier-admission")
            # One failed barrier, whichever retirement owes it: one failed
            # deletion, and recovery's own barrier proves the rest.
            r.selective_errors += 1
            result = await r.target.fs_write(r.session, knob, "1")
            assert result["errno"] == 0, result
            await command(r.target, r.session, "ip", "neigh", "del", PEERS[0]["lan"], "dev", TARGET_LAN_IF)
            state = await r.wait(lambda s: s["invalidation_done"] == 1 and s["entries"] == 0)
            assert state["invalidated"] == 1 and state["bindings"] == 2, state
            assert state["handle_refs"] == state["neighbour_refs"] == state["fatal"] == state["quarantine"] == 0, state
            assert state["errors"] == r.selective_errors, state
            assert state["deletes"] == before["deletes"] + 8 and state["installs"] == before["installs"], (before, state)
            assert (await read(r.target, r.session, knob)).strip() == "armed=0"
            reports = await p.batch(ALL)
            after = await r.state()
            assert after["entries"] == 0 and after["installs"] == before["installs"], after
            r.record("selective-barrier", {"before": before, "retired": state,
                                           "software": after, "transfers": reports})
    finally:
        result = await r.target.fs_write(r.session, knob, "0")
        assert result["errno"] == 0, result
        await r.delete_table()
        # Global recovery still requires a fresh table. Leave this boot usable
        # after the deliberately injected errors; the detailed proof is in rearm.
        state = await r.state()
        if state["invalidated"] and not state["fatal"]:
            await r.wait(lambda s: s["rearm_ready"] == 1)
            await r.table()
            await r.delete_table()
