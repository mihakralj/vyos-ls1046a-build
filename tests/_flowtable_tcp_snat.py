"""Shared support for flowtable tcp snat."""

import asyncio
import json

import pytest_asyncio
from _flowtable_policy import CONFIG, apply, stop
from _flowtable_rig import (
    DPORT,
    SPORT,
    TABLE,
    WAN_IP,
    artifact_dir,
    command,
    console_command,
)
from _topology import TARGET_LAN_IF, TARGET_WAN_IF
from ask_orch.uart import Console


@pytest_asyncio.fixture
async def tcp_snat(rig, request):
    r = rig
    addresses = json.loads((await command(r.target, r.session, "ip", "-j", "-4", "addr", "show",
                                         "dev", TARGET_WAN_IF))["stdout"])
    external = next(a["local"] for a in addresses[0]["addr_info"] if a["family"] == "inet")
    port = SPORT + 1024
    r.tcp_remote = (external, port)
    policy = {"version": 1, "enabled": True, "devices": [TARGET_LAN_IF, TARGET_WAN_IF],
              "scope": [{"protocol": "tcp", "source": r.lan_ip, "destination": WAN_IP,
                         "source_port": SPORT, "destination_port": DPORT}], "exclude": []}
    nat_table = "ask_tcp_snat_test"
    kind = getattr(request, "param", "snat")
    assert kind in {"snat", "masquerade"}
    translation = f"snat to {external}:{port}" if kind == "snat" else f"masquerade to :{port}"
    nat = (f"table ip {nat_table} {{ chain postrouting {{ type nat hook postrouting priority 90; "
           f"ip saddr {r.lan_ip} ip daddr {WAN_IP} tcp sport {SPORT} tcp dport {DPORT} "
           f"{translation}; }}; }}")
    table, delete_table, state = r.table, r.delete_table, r.state
    with Console.target(log_path=str(artifact_dir() / "tcp-snat-uart.log")) as con:
        await asyncio.to_thread(con.login, "root", None)
        existing = await command(r.target, r.session, "nft", "list", "table", "ip", nat_table, check=False)
        assert existing["rc"] != 0, existing
        await command(r.target, r.session, "nft", nat)

        async def create():
            # Flag counters remain at the forward hook. The production policy
            # owns the flowtable, including stop/reapply during the same socket.
            await r.nft(f"table inet {TABLE} {{ chain forward {{ type filter hook forward priority 0; policy accept; }}; }}")
            await apply(con, policy, r=r)

        async def remove():
            await stop(con)
            return await delete_table()

        # The adapter's error count is cumulative for the boot and never reset,
        # so this fixture's floor is whatever earlier tests already accounted
        # for. Only the three current-state fields are absolute.
        baseline = (await state())["errors"]

        async def checked_state():
            result = await state()
            assert result["fatal"] == result["quarantine"] == result["invalidated"] == 0, result
            assert result["errors"] == baseline, (result, baseline)
            local, remote, mapped = f"{r.lan_ip}:{SPORT}", f"{WAN_IP}:{DPORT}", f"{external}:{port}"
            expected = {TARGET_LAN_IF: (local, remote, mapped, remote, WAN_IP),
                        TARGET_WAN_IF: (remote, mapped, remote, local, r.lan_ip)}
            for row in result["flows"]:
                assert row["proto"] == "6" and int(row["mtu"]) == r.port_mtu, result
                assert tuple(row[k] for k in ("src", "dst", "new_src", "new_dst", "nexthop")) == expected[row["in"]], result
            return result

        r.table, r.delete_table, r.state = create, remove, checked_state
        try:
            yield r
        finally:
            r.table, r.delete_table, r.state = table, delete_table, state
            try:
                await stop(con)
            finally:
                try:
                    await command(r.target, r.session, "nft", "delete", "table", "ip", nat_table)
                finally:
                    await console_command(con, "rm", "-f", CONFIG)
