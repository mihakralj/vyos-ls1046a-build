"""Policy replacement must revoke existing hardware without closing sockets."""
from __future__ import annotations

from _flowtable_policy import CONFIG, DRAIN_FIELDS, apply, candidate, expected_hash, installed, policy_table_handle, stop

import asyncio
import copy
import json

from ask_orch.uart import Console
from _topology import TARGET_LAN_IF, TARGET_WAN_IF
from _flowtable_connections import (FLOWS, SPORT, by_key, peer)
from _flowtable_rig import (artifact_dir, console_command)
from _flowtable_selective_neighbour import (hardware, keys, warm)
from _flowtable_tcp import (software_tx)


async def test_revokes_live_connections(connections):
    r = connections
    flows = [{**flow, "lan": r.lan_ip} for flow in FLOWS[:2]]
    policy = candidate(r)
    await r.delete_table()
    with Console.target(log_path=str(artifact_dir() / "policy-uart.log")) as con:
        await asyncio.to_thread(con.login, "root", None)
        try:
            await apply(con, policy, r=r)
            async with peer(r, flows) as p:
                await warm(r, p, [0, 1], "policy-initial-admission", flows)
                initial = await hardware(r, p, "policy-initial-hardware", flows)
                excluded = copy.deepcopy(policy)
                # 'port' matches either direction's tuple endpoints.
                excluded["exclude"] = [{"protocol": "udp", "port": SPORT}]
                result = await apply(con, excluded, r=r)
                assert result["drained"]["deletes"] == initial["deletes"] + 4, result
                for _ in range(8):
                    await p.batch([0, 1], 128, 0.01)
                    state = await r.state()
                    if state["entries"] == 2:
                        break
                assert by_key(state).keys() == keys([1], flows), state
                # Count software forwarding independently of the TCP stream.
                before_tx = await software_tx(r)
                udp = await p.batch([0], 256, 0.005)
                after_tx = await software_tx(r)
                tx = {d: after_tx[d] - before_tx[d] for d in before_tx}
                assert all(value >= 256 for value in tx.values()), tx
                state = await r.state()
                assert by_key(state).keys() == keys([1], flows), state
                before_tcp = await software_tx(r)
                tcp = await p.batch([1], 256, 0.015625)
                after_tcp, accelerated = await software_tx(r), await r.state()
                generation = await policy_table_handle(r)
                assert generation is not None, "the controller's table is missing"
                tcp_tx = {d: after_tcp[d] - before_tcp[d] for d in before_tcp}
                assert tcp_tx[TARGET_LAN_IF] <= 64 and tcp_tx[TARGET_WAN_IF] <= 512, tcp_tx
                for key in keys([1], flows):
                    old, new = by_key(state)[key], by_key(accelerated)[key]
                    assert old["cookie"] == new["cookie"]
                    assert int(new["packets"]) - int(old["packets"]) >= tcp[1]["bytes"] // 1500
                assert (await installed(con))["policy_hash"] == await expected_hash(con, excluded)
                r.record("policy-exclusion-revoked-existing-udp", {"apply": result, "state": accelerated,
                         "udp": udp, "tcp": tcp, "udp_software_tx": tx, "tcp_software_tx": tcp_tx})

                # Schema errors leave the installed generation untouched: the
                # same table, still bound, never drained or rearmed, from the
                # snapshot above through the status/check reads and the
                # rejected apply. Install and delete counts are not that proof
                # -- the fixture ages flows out after 5 s idle, and the TCP
                # connection is idle here, so a legitimate expiry can retire
                # its entries meanwhile. Nothing can install without traffic.
                before = accelerated
                rejected = await apply(con, {**policy, "enabled": "maybe"}, check=False, r=r)
                assert rejected["rc"] != 0 and "yes or no" in rejected["stdout"], rejected
                held = await r.state()
                moved = {k: (before[k], held[k]) for k in held if k != "flows" and before.get(k) != held[k]}
                assert await policy_table_handle(r) == generation, moved
                assert held["bindings"] == before["bindings"] and held["installs"] == before["installs"], moved
                assert all(held[k] == before[k] for k in held
                           if k in ("rearms", "invalidated", "fatal") or k.endswith("_invalidations")), moved
                assert held["deletes"] - before["deletes"] <= before["entries"], moved
                assert (await installed(con))["policy_hash"] == await expected_hash(con, excluded)
                await apply(con, policy, r=r)
                await warm(r, p, [0, 1], "policy-exclusion-removed", flows)
                await hardware(r, p, "policy-restored-hardware", flows)

                # A valid configuration with unsupported hardware ports is
                # rejected after retiring the old policy. Both sockets survive.
                unsupported = {**policy, "devices": [TARGET_LAN_IF, "lo"]}
                rejected = await apply(con, unsupported, check=False, r=r)
                assert rejected["rc"] != 0, rejected
                status = await installed(con)
                assert not status["policy_installed"] and not status["admission_ready"], status
                assert all(status["backend"][k] == 0 for k in DRAIN_FIELDS), status
                before_tx = await software_tx(r)
                reports = await p.batch([0, 1], 128, 0.01)
                after_tx = await software_tx(r)
                tx = {d: after_tx[d] - before_tx[d] for d in before_tx}
                assert all(value >= 128 for value in tx.values()), tx
                r.record("policy-failed-candidate-software", {"rejection": rejected, "status": status,
                                                            "transfers": reports, "software_tx": tx})
        finally:
            await stop(con)
            await console_command(con, "rm", "-f", CONFIG)


async def test_preserves_foreign_table(connections):
    r = connections
    with Console.target(log_path=str(artifact_dir() / "policy-foreign-uart.log")) as con:
        await asyncio.to_thread(con.login, "root", None)
        try:
            # The fixture's differently named flowtable owns the backend.
            before = await r.state()
            result = await apply(con, candidate(r), check=False, r=r)
            assert result["rc"] != 0 and "another flowtable" in result["stdout"], result
            after = await r.state()
            assert after["bindings"] == 2 and after["rearms"] == before["rearms"]
            await r.delete_table()
            await r.nft("table inet ask_flowtable { comment \"foreign\"; }")
            try:
                result = await apply(con, candidate(r), check=False, r=r)
                assert result["rc"] != 0 and "ownership marker" in result["stdout"], result
                listed = await console_command(con, "nft", "-j", "list", "table", "inet", "ask_flowtable")
                assert any(t.get("table", {}).get("comment") == "foreign" for t in json.loads(listed["stdout"])["nftables"])
                r.record("policy-foreign-ownership-preserved", result)
            finally:
                await console_command(con, "nft", "delete", "table", "inet", "ask_flowtable")
        finally:
            await console_command(con, "rm", "-f", CONFIG)
