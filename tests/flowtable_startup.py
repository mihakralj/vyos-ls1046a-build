"""Boot and forward through CDX with the shipped offload service."""
import asyncio

import pytest

from ask_orch.uart import Console
from _flowtable_connections import (FLOWS, peer)
from _flowtable_module import (table)
from _flowtable_rig import (artifact_dir, console_command, console_json, flowtable_json, read)
from _flowtable_selective_neighbour import (hardware, warm)


async def test_flowtable_startup(connections):
    r = connections

    async def loaded():
        modules = await read(r.target, r.session, "/proc/modules")
        names = {line.split()[0] for line in modules.splitlines()}
        assert {"cdx", "ask_flowtable"} <= names, modules

    await loaded()
    await r.delete_table()
    with Console.target(log_path=str(artifact_dir() / "startup-independence-uart.log")) as con:
        await asyncio.to_thread(con.login, "root", None)
        # The shipped offload service is default-on and needs no configuration:
        # its shipped policy parses, and starting/resuming it after the
        # fixture's explicit maintenance stop binds every up CDX port. It is
        # stopped again afterwards because this test's own table needs the ports.
        shipped = await console_command(con, "/usr/sbin/ask-flowtable", "check",
                                        "--config", "/etc/ask/offload.conf")
        assert console_json(shipped["stdout"])["policy_hash"], shipped
        await console_command(con, "/etc/init.d/ask-flowtable", "start", check=False,
                              timeout=45)
        await console_command(con, "/usr/sbin/ask-flowtable", "resume")
        for _ in range(40):
            status = await flowtable_json(con, "status")
            if status["policy_installed"] and status["admission_ready"]:
                break
            await asyncio.sleep(0.5)
        else:
            pytest.fail(f"default-on service did not bind the backend: {status}")
        assert status["backend"]["bindings"] >= 2, status
        await loaded()
        await console_command(con, "/etc/init.d/ask-flowtable", "stop", check=False,
                              timeout=45)
    await r.wait(lambda s: not s["bindings"] and not s["entries"])
    await table(r)
    flows = [{**f, "lan": r.lan_ip} for f in FLOWS[:2]]
    async with peer(r, flows) as p:
        await warm(r, p, [0, 1], "startup-independent-admission", flows)
        state = await hardware(r, p, "startup-independent-hardware", flows)
        await loaded()
        r.record("startup-independent", {"default_service": status, "state": state})
