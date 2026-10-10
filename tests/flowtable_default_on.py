"""The offload service is default-on: a normal boot installs the catch-all
policy across the CDX physical ports with no configuration, the way CMM "just
worked". This is the parity guarantee of the C ask-flowtable daemon.

Order-independent: it (re)starts the boot service itself, so it does not depend
on running before the controlled tests that stop the daemon for their own
policies. It restarts the service at teardown so the image is left in its
normal default-on state.
"""
from __future__ import annotations

import asyncio

import pytest

from ask_orch.uart import Console
from _flowtable_rig import (artifact_dir, console_command, console_json, flowtable_json)

INIT = "/etc/init.d/ask-flowtable"
DAEMON = "/usr/sbin/ask-flowtable"
DEFAULT_CONF = "/etc/ask/offload.conf"


async def _status(con):
    return await flowtable_json(con, "status")


async def test_boot_service_offloads_by_default():
    with Console.target(log_path=str(artifact_dir() / "default-on-uart.log")) as con:
        await asyncio.to_thread(con.login, "root", None)

        # Zero-config: the shipped default parses and is the catch-all. Its
        # `check` hash resolves no port, and under "devices auto" neither does
        # the installed table's marker, so the two name the same policy.
        shipped = await console_command(con, DAEMON, "check", "--config", DEFAULT_CONF)
        expected = console_json(shipped["stdout"])["policy_hash"]
        assert expected, shipped

        # (Re)start the boot service — idempotent, and the point of the test is
        # that starting it is the *whole* configuration.
        await console_command(con, INIT, "restart", check=False, timeout=45)
        # Prior one-shot tests can retain manual ownership across restart.
        await console_command(con, DAEMON, "resume")

        # It installs its table and binds every up CDX physical port with no
        # further action. A table a previous manual apply left is replaced by
        # the configured one, so wait for that one.
        for _ in range(40):
            status = await _status(con)
            if (status["policy_installed"] and status["admission_ready"]
                    and status["policy_hash"] == expected):
                break
            await asyncio.sleep(0.5)
        else:
            pytest.fail(f"default-on service did not bind the backend: {status}")

        # The installed table carries our ownership marker, and every up CDX
        # physical port is bound — with no configuration.
        assert len(status["devices"]) >= 2, status
        assert status["backend"]["bindings"] == len(status["devices"]), status
        assert not status["backend"]["fatal"] and not status["backend"]["invalidated"], status
