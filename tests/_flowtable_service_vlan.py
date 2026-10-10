"""Shared support for flowtable service vlan."""

from __future__ import annotations

import json
import os
import struct

import pytest_asyncio
from _flowtable_rig import command, console_command, read
from _flowtable_service import FAULT_DIR, managed_service
from _topology import LAN_NIC, TARGET_LAN_IF, TARGET_WAN_IF, lan_run_python
from ask_orch.client import Agent

VID = 284
DUT_IF = f"{TARGET_LAN_IF}.{VID}"
LAN_IF = "askftvlan"
NETNS = "ask-ft-service-vlan"
GATEWAY, ADDRESS = "172.29.84.1", "172.29.84.2"


async def create_vlan(r, *, console=False):
    async def run(*args):
        if console:
            return await console_command(r.service_console, *args)
        return await command(r.target, r.session, *args)

    await run("ip", "link", "add", "link", TARGET_LAN_IF, "name", DUT_IF,
              "type", "vlan", "id", str(VID))
    r.service_vlan_created = True
    await run("ip", "addr", "add", GATEWAY + "/24", "dev", DUT_IF)
    await run("ip", "link", "set", "dev", DUT_IF, "up")
    await run("ip", "route", "add", ADDRESS + "/32", "dev", DUT_IF)
    await run("ip", "neigh", "replace", ADDRESS, "lladdr", r.lan_mac,
              "nud", "permanent", "dev", DUT_IF)


@pytest_asyncio.fixture
async def vlan_service(rig):
    r = rig
    r.service_vlan_created = False
    r.service_vlan_records_before = (await r.state())["vlan_records"]
    wan = Agent("wan", f"http://{os.environ.get('ASK_WAN_IP', '127.0.0.1')}:9110")
    lan_created = wan_route = False
    links = json.loads((await command(r.target, r.session, "ip", "-j", "link", "show"))["stdout"])
    assert DUT_IF not in {link["ifname"] for link in links}, links
    routes = json.loads((await command(wan, r.session, "ip", "-j", "route", "show", "exact", ADDRESS + "/32"))["stdout"])
    assert not routes, routes
    dut_wan = json.loads((await command(r.target, r.session, "ip", "-j", "-4", "addr", "show", "dev", TARGET_WAN_IF))["stdout"])
    dut_ip = next(a["local"] for a in dut_wan[0]["addr_info"] if a["family"] == "inet")
    try:
        await create_vlan(r)
        # The peer namespace routes its sockets over the VLAN while the
        # unaffected sockets keep the normal LAN path.
        setup = f'''
from pathlib import Path
import subprocess
def run(*args): subprocess.run(args, check=True, capture_output=True, text=True)
assert not Path('/var/run/netns/' + {NETNS!r}).exists()
assert not Path('/sys/class/net/' + {LAN_IF!r}).exists()
run('ip', 'netns', 'add', {NETNS!r})
try:
    run('ip', 'link', 'add', 'link', {LAN_NIC!r}, 'name', {LAN_IF!r},
        'netns', {NETNS!r}, 'type', 'vlan', 'id', {str(VID)!r})
    run('ip', '-n', {NETNS!r}, 'link', 'set', 'lo', 'up')
    run('ip', '-n', {NETNS!r}, 'addr', 'add', {ADDRESS + '/24'!r}, 'dev', {LAN_IF!r})
    run('ip', '-n', {NETNS!r}, 'link', 'set', {LAN_IF!r}, 'up')
    run('ip', '-n', {NETNS!r}, 'route', 'add', 'default', 'via', {GATEWAY!r})
    run('ip', '-n', {NETNS!r}, 'neigh', 'replace', {GATEWAY!r}, 'lladdr',
        {r.dut_lan_mac!r}, 'nud', 'permanent', 'dev', {LAN_IF!r})
except BaseException:
    run('ip', 'netns', 'del', {NETNS!r})
    raise
'''
        result = await lan_run_python(r.lan, setup, label="service_vlan_setup", timeout=20)
        assert result.rc == 0, result.stdout
        lan_created = True
        await command(wan, r.session, "ip", "route", "add", ADDRESS + "/32", "via", dut_ip, "dev", r.wan_if)
        wan_route = True
        async with managed_service(r, addresses=(r.lan_ip, ADDRESS)):
            yield r
    finally:
        failures = []

        async def attempt(operation):
            try:
                return await operation
            except Exception as error:
                failures.append(repr(error))

        if wan_route:
            await attempt(command(wan, r.session, "ip", "route", "del", ADDRESS + "/32", "via", dut_ip, "dev", r.wan_if))
        if r.service_vlan_created:
            await attempt(command(r.target, r.session, "ip", "link", "del", DUT_IF))
        if lan_created:
            result = await attempt(lan_run_python(r.lan,
                f"import subprocess\nsubprocess.run(['ip', 'netns', 'del', {NETNS!r}], check=True)\n",
                label="service_vlan_cleanup", timeout=15))
            if result and result.rc:
                failures.append(result.stdout)
        final = await attempt(r.state())
        r.record("service-vlan-cleanup", final)
        if final and final["vlan_records"] != r.service_vlan_records_before:
            failures.append({"vlan_records": final})
        assert not failures, failures


def received(r, ident):
    return sum(count for data, count in r.echo.received.items()
               if len(data) >= 4 and struct.unpack("!I", data[:4])[0] == ident)


async def denied(r, p, ident):
    before = received(r, ident)
    await p.rpc("start", [ident], count=4, interval=0.01, allow_loss=True, udp_timeout=0.1)
    result = (await p.rpc("wait", [ident]))[str(ident)]
    assert result["received"] == 0 and result["lost"] == 4, result
    assert received(r, ident) == before, "forbidden UDP reached WAN"
    return result


async def attempts(r):
    return len((await read(r.target, r.session, FAULT_DIR + "/attempts")).splitlines())


def tagged(state):
    return [f for f in state["flows"]
            if f["src"].startswith(ADDRESS + ":") or f["dst"].startswith(ADDRESS + ":")]


def balanced(state, errors):
    assert state["errors"] == errors and state["fatal"] == state["quarantine"] == 0, state
    assert state["installs"] - state["deletes"] == state["entries"], state
    assert state["entries"] == state["handle_refs"] == state["neighbour_refs"] == len(state["flows"]), state


def vlan_row(state):
    rows = [v for v in state["vlans"] if v["dev"] == DUT_IF]
    assert len(rows) == 1, (DUT_IF, state["vlans"])
    return rows[0]
