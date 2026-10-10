"""Advertised IPv6 renumbering preserves old sockets and changes new sources."""

import asyncio
from functools import partial
import ipaddress
import json
import os
import socket
import struct
from types import SimpleNamespace

import pytest

from ask_orch.commands import command, console_command, console_python
from ask_orch.uart import Console
from _flowtable_connections import peer
from _flowtable_policy import CONFIG, apply, stop
from _flowtable_rig import DPORT, Echo
from _native_process import dut_process
from _topology import (DUT_IPV6_WAN, LAN_NIC, TARGET_LAN_IF, TARGET_WAN_IF, WAN_IPV6,
                       TopologyStack, dut_vlan_subif, lan_run_python)

pytestmark = pytest.mark.requires("dnsmasq")
VID, PORT = 331, DPORT + 3130
OLD, NEW = "fd73:6173:6b00:331::/64", "fd73:6173:6b00:332::/64"


@pytest.mark.rfc("4862")
async def test_renumbering_uses_native_source_selection(ipv6_rig):
    r, con = ipv6_rig, Console.target()
    stack = TopologyStack()
    namespace, lan_if = f"ask-slaac-{os.getpid()}", "slaac-peer"
    root = f"/tmp/ask-slaac-{os.getpid()}"
    old_gateway, new_gateway = [str(ipaddress.IPv6Network(prefix).network_address + 1) for prefix in (OLD, NEW)]

    class Sources(Echo):
        def __init__(self):
            super().__init__()
            self.sources = {}
            self.errors = []

        def datagram_received(self, data, address):
            ident, = struct.unpack_from("!I", data)
            self.sources.setdefault(ident, set()).add(address[0])
            super().datagram_received(data, address)

        def error_received(self, error):
            self.errors.append(repr(error))

    echo = Sources()
    transport, _ = await asyncio.get_running_loop().create_datagram_endpoint(
        lambda: echo, local_addr=(WAN_IPV6, PORT), family=socket.AF_INET6)

    async def lan(script):
        result = await lan_run_python(r.lan, script, timeout=30, label="slaac")
        assert result.rc == 0, result.stdout
        return result.stdout

    async def drop_client():
        await lan(f'''
import os,signal,subprocess
for pid in subprocess.check_output(['ip','netns','pids',{namespace!r}],text=True).split():
    try: os.kill(int(pid),signal.SIGTERM)
    except ProcessLookupError: pass
subprocess.run(['ip','netns','del',{namespace!r}],check=True)
''')

    async def addresses():
        return json.loads(await lan(f'''
import subprocess
print(subprocess.check_output(['ip','-n',{namespace!r},'-j','-6','addr','show','dev',{lan_if!r}],text=True))
'''))[0]["addr_info"]

    async def reachable(label):
        # Resolve native ND before a UDP tuple caches its VLAN forwarding path.
        result = await lan(f'''
import subprocess
subprocess.run(['ip','netns','exec',{namespace!r},'ping','-6','-n','-c','1','-W','2',{WAN_IPV6!r}],check=True)
''')
        r.record(label, result)

    async def ready(prefix, *, deprecated=False):
        network = ipaddress.IPv6Network(prefix)
        deadline = asyncio.get_running_loop().time() + 35
        while True:
            rows = await addresses()
            found = [row for row in rows if ipaddress.IPv6Address(row["local"]) in network
                     and not row.get("tentative") and not row.get("dadfailed")
                     and (row.get("preferred_life_time") == 0) == deprecated]
            if found:
                assert len(found) == 1 and found[0]["valid_life_time"] > 0, found
                return found[0]
            assert asyncio.get_running_loop().time() < deadline, rows
            await asyncio.sleep(0.3)

    try:
        iface = await dut_vlan_subif(stack, r.target, r.session, parent=TARGET_LAN_IF,
                                    vid=VID, ipv6=old_gateway + "/64")
        for prefix in (OLD, NEW):
            await command(r.wan, r.session, "ip", "-6", "route", "add", prefix,
                          "via", DUT_IPV6_WAN, "dev", r.wan_if)
            stack.push(partial(command, r.wan, r.session, "ip", "-6", "route", "del", prefix,
                               "via", DUT_IPV6_WAN, "dev", r.wan_if))
        await lan(f'''
import subprocess
subprocess.run(['ip','netns','add',{namespace!r}],check=True)
''')
        stack.push(drop_client)
        await lan(f'''
import subprocess
def run(*args): subprocess.run(args,check=True,capture_output=True,text=True)
run('ip','link','add','link',{LAN_NIC!r},'name',{lan_if!r},'netns',{namespace!r},
    'type','vlan','id',{str(VID)!r})
run('ip','-n',{namespace!r},'link','set','lo','up')
for setting in ('accept_ra=2','autoconf=1','use_tempaddr=0'):
    run('ip','netns','exec',{namespace!r},'sysctl','-qw','net.ipv6.conf.'+{lan_if!r}+'.'+setting)
run('ip','-n',{namespace!r},'link','set',{lan_if!r},'up')
''')
        await console_command(con, "mkdir", root)
        stack.push(partial(console_command, con, "rm", "-rf", root))
        await dut_process(stack, con, ["dnsmasq", "--keep-in-foreground", "--conf-file=/dev/null",
            "--port=0", "--no-hosts", "--no-resolv", "--user=root", "--group=root",
            "--bind-interfaces", f"--interface={iface}", "--enable-ra",
            f"--dhcp-range=::,constructor:{iface},ra-only,64,2m", f"--ra-param={iface},3,30",
            f"--pid-file={root}/dnsmasq.pid", "--log-facility=-"], base=root + "/ra")
        old = await ready(OLD)
        await reachable("slaac-initial-connectivity")
        policy = {"version": 1, "enabled": True, "devices": [TARGET_LAN_IF, TARGET_WAN_IF],
                  "scope": [{"protocol": "udp", "destination_port": PORT}],
                  "exclude": []}
        stack.push(partial(console_command, con, "rm", "-f", CONFIG))
        stack.push(partial(stop, con))
        await apply(con, policy, r=r)
        proxy = SimpleNamespace(lan=r.lan, lan_ip="::", echo=echo, record=r.record,
                                target=r.target, session=r.session)
        flows = [{"id": ident, "proto": "udp", "sport": PORT + ident, "lan": "::",
                  "netns": namespace, "connect_ip": WAN_IPV6, "connect_port": PORT}
                 for ident in (0, 1)]
        async with peer(proxy, flows, initial_ids=[0], lease=240) as p:
            for _ in range(8):
                await p.batch([0], 32, 0.02)
                initial = await r.state()
                if initial["entries"] == 2:
                    break
            r.record("slaac-initial", initial)
            assert initial["entries"] == 2 and initial["errors"] == r.errors, initial
            assert echo.sources[0] == {old["local"]}, echo.sources
            await p.rpc("start", [0], count=0, interval=0.02)
            try:
                await console_command(con, "ip", "-6", "addr", "add", new_gateway + "/64", "dev", iface)
                await console_command(con, "ip", "-6", "addr", "change", old_gateway + "/64",
                                      "dev", iface, "preferred_lft", "0", "valid_lft", "3600")
                new = await ready(NEW)
                deprecated = await ready(OLD, deprecated=True)
                routes = json.loads((await console_command(con, "ip", "-j", "-6", "route", "show",
                                                          "exact", OLD, "dev", iface))["stdout"])
                r.record("slaac-deprecated-prefix-route", routes)
                assert len(routes) == 1 and 3500 <= routes[0].get("expires", 0) <= 3600, routes
            finally:
                overlap = (await p.rpc("stop", [0]))["0"]
                r.record("slaac-overlap-traffic", overlap)
            assert overlap["lost"] == 0 and overlap["received"] > 0, overlap
            assert echo.sources[0] == {old["local"]}, echo.sources
            await reachable("slaac-new-connectivity")
            await p.rpc("open", [1])
            for _ in range(8):
                await p.batch([0, 1], 32, 0.02)
                before = await r.state()
                if before["entries"] == 4:
                    break
            assert echo.sources == {0: {old["local"]}, 1: {new["local"]}}, echo.sources
            assert len(before["flows"]) == 4 and before["errors"] == r.errors, before
            await p.batch([0, 1], 128, 0.02)
            after = await r.state()
            previous = {row["cookie"]: int(row["packets"]) for row in before["flows"]}
            current = {row["cookie"]: int(row["packets"]) for row in after["flows"]}
            assert previous.keys() == current.keys() and all(current[k] - previous[k] >= 100 for k in previous), (before, after)
            assert after["errors"] == r.errors and after["fatal"] == after["quarantine"] == 0, after
            r.record("slaac-renumbering", {"old": old, "deprecated": deprecated, "new": new,
                                         "sources": {key: sorted(value) for key, value in echo.sources.items()},
                                         "before": before, "after": after})
            await stop(con)
            await p.batch([0, 1], 32, 0.02)
    except Exception:
        # Preserve native routing/ND evidence before topology cleanup.
        if "old" in locals():
            result = await console_python(con, f'''
import json,subprocess
commands = {{
 'addresses': ['ip','-j','-6','addr','show','dev',{iface!r}],
 'neighbours': ['ip','-j','-6','neigh','show','dev',{iface!r}],
 'return_route': ['ip','-j','-6','route','get',{old["local"]!r},'from',{WAN_IPV6!r},'iif',{TARGET_WAN_IF!r}],
 'routes': ['ip','-j','-6','route','show','dev',{iface!r}],
}}
reports = {{}}
for label,argv in commands.items():
 result = subprocess.run(argv,capture_output=True,text=True)
 reports[label] = {{'rc':result.returncode,'stdout':result.stdout,'stderr':result.stderr}}
print(json.dumps(reports))
''')
            client = await lan(f'''
import json,subprocess
print(json.dumps({{part:subprocess.check_output(['ip','-n',{namespace!r},'-j','-6',part,'show'],text=True)
                  for part in ('addr','route','neigh')}}))
''')
            route = await command(r.wan, r.session, "ip", "-j", "-6", "route", "get", old["local"],
                                  "from", WAN_IPV6)
            r.record("slaac-failure", {"dut": json.loads(result["stdout"]),
                      "client": json.loads(client), "state": await r.state(),
                      "wan_route": route, "echo_errors": echo.errors,
                      "sources": {key: sorted(value) for key,value in echo.sources.items()}})
        raise
    finally:
        try:
            result = await console_command(con, "cat", root + "/ra.log", check=False)
            r.record("slaac-ra-log", result)
        finally:
            transport.close()
            await stack.teardown("SLAAC renumbering")
