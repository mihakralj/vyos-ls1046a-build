"""Native DHCP renewal preserves mappings; an address change retires them."""

import asyncio
from functools import partial
import json
import os
from pathlib import Path
import signal
import subprocess
import tempfile

import pytest

from ask_orch.commands import console_command, console_python
from ask_orch.uart import Console
from _flowtable_connections import healthy, peer
from _flowtable_policy import CONFIG, apply, candidate, stop
from _flowtable_rig import DPORT
from _native_process import dut_process
from _topology import TARGET_LAN_IF, TARGET_WAN_IF, TopologyStack, dut_vlan_subif

pytestmark = pytest.mark.requires("udhcpc")
SERVER, FIRST, SECOND, ENDPOINT = "198.18.112.1", "198.18.112.10", "198.18.112.11", "198.18.113.2"
PORT = DPORT + 3120
GATEWAY_OPTIONS = ("--interface=eth3", "--bind-dynamic", "--dhcp-authoritative", "--port=0")


@pytest.mark.rfc("2131", section="4.4.5")
async def test_renewal_and_address_change(rig):
    r, con = rig, Console.target()
    stack = TopologyStack()
    namespace = f"ask-dhcp-{os.getpid()}"
    wan_vlan = os.environ["ASK_DHCP_WAN_IF"]

    async def local(*argv):
        result = await asyncio.to_thread(subprocess.run, argv, capture_output=True, text=True, timeout=20)
        assert result.returncode == 0, (argv, result.stdout, result.stderr)
        return result.stdout

    async def stop_process(process):
        if process.poll() is None:
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                await asyncio.to_thread(process.wait, 5)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                await asyncio.to_thread(process.wait, 5)

    with tempfile.TemporaryDirectory(prefix="ask-dhcp-") as directory:
        root = Path(directory)
        try:
            await local("ip", "netns", "add", namespace)
            stack.push(partial(local, "ip", "netns", "del", namespace))
            await local("ip", "link", "add", "link", wan_vlan, "name", "eth3",
                        "netns", namespace, "type", "macvlan", "mode", "bridge")
            for dev in ("lo", "eth3"):
                await local("ip", "-n", namespace, "link", "set", dev, "up")
            await local("ip", "-n", namespace, "addr", "add", SERVER + "/24", "dev", "eth3")
            await local("ip", "-n", namespace, "addr", "add", ENDPOINT + "/32", "dev", "lo")
            iface = await dut_vlan_subif(stack, r.target, r.session, parent=TARGET_WAN_IF, vid=3900)
            result = await console_command(con, "cat", f"/sys/class/net/{iface}/address")
            mac = result["stdout"].strip()
            hosts = root / "hosts"
            hosts.write_text(f"{mac},{FIRST}\n")
            def start_server():
                # Use the image's DHCP behavior on an isolated test subnet.
                with (root / "server.log").open("a") as log:
                    return subprocess.Popen(["ip", "netns", "exec", namespace, "timeout", "300", "/usr/sbin/dnsmasq",
                        "--keep-in-foreground", "--conf-file=/dev/null", *GATEWAY_OPTIONS, "--no-hosts", "--no-resolv",
                        "--user=root", "--group=root",
                        "--dhcp-range=198.18.112.0,static,255.255.255.0,2m",
                        f"--dhcp-hostsfile={hosts}", f"--dhcp-leasefile={root / 'leases'}",
                        f"--pid-file={root / 'server.pid'}",
                        f"--dhcp-option=3,{SERVER}", "--log-dhcp", "--log-facility=-"],
                        stdin=subprocess.DEVNULL, stdout=log, stderr=log, start_new_session=True)
            server = start_server()
            stack.push(partial(stop_process, server))
            echo = ("import socket\n"
                    f"s=socket.socket(socket.AF_INET,socket.SOCK_DGRAM); s.bind(({ENDPOINT!r},{PORT}))\n"
                    "while True:\n data,remote=s.recvfrom(2048)\n s.sendto(data,remote)\n")
            with (root / "echo.log").open("w") as log:
                process = subprocess.Popen(["ip", "netns", "exec", namespace, "timeout", "300", "python3", "-c", echo],
                    stdin=subprocess.DEVNULL, stdout=log, stderr=log, start_new_session=True)
            stack.push(partial(stop_process, process))
            hook = f'''#!/usr/bin/python3
import ipaddress,json,os,pathlib,subprocess,sys,time
iface = {iface!r}
assert os.environ['interface'] == iface
event = sys.argv[1]
def run(*args): subprocess.run(args, check=True, capture_output=True, text=True)
shown = json.loads(subprocess.check_output(['ip','-j','-4','addr','show','dev',iface], text=True))
old = [a['local'] for link in shown for a in link['addr_info'] if a['family'] == 'inet']
new = None
if event == 'deconfig':
    for address in old:
        assert address in ({FIRST!r},{SECOND!r}), address
        run('ip','addr','del',address+'/24','dev',iface)
elif event in ('bound','renew'):
    new = str(ipaddress.IPv4Address(os.environ['ip']))
    assert new in ({FIRST!r},{SECOND!r}) and os.environ['subnet'] == '255.255.255.0'
    assert os.environ['router'].split()[0] == {SERVER!r}
    if old != [new]:
        for address in old:
            assert address in ({FIRST!r},{SECOND!r}), address
            run('ip','addr','del',address+'/24','dev',iface)
        run('ip','addr','add',new+'/24','dev',iface)
        run('ip','route','replace',{ENDPOINT + '/32'!r},'via',{SERVER!r},'dev',iface)
with pathlib.Path({str(root / 'events')!r}).open('a') as log:
    log.write(json.dumps({{'event':event,'old':old,'new':new,'at':time.monotonic()}})+'\\n')
'''
            await console_python(con, f'''
from pathlib import Path
root = Path({str(root)!r}); root.mkdir()
hook = root / 'hook.py'; hook.write_text({hook!r}); hook.chmod(0o700)
''')
            stack.push(partial(console_command, con, "rm", "-rf", str(root)))
            await dut_process(stack, con, ["udhcpc", "-f", "-n", "-i", iface,
                "-s", str(root / "hook.py"), "-p", str(root / "client.pid"), "-t", "3", "-T", "2"],
                base=str(root / "client"))

            async def events(address, after=0, timeout=25):
                deadline = asyncio.get_running_loop().time() + timeout
                while True:
                    result = await console_python(con, f'''
import json,pathlib
path = pathlib.Path({str(root / 'events')!r})
print(json.dumps([json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []))
''')
                    records = json.loads(result["stdout"])
                    if any(row["new"] == address for row in records[after:]):
                        return records
                    assert server.poll() is None, (root / "server.log").read_text()
                    assert asyncio.get_running_loop().time() < deadline, records
                    await asyncio.sleep(0.3)

            async def renew():
                await console_python(con, f'''
import os,pathlib,signal
pid = int(pathlib.Path({str(root / 'client.pid')!r}).read_text())
args = pathlib.Path('/proc/%d/cmdline' % pid).read_bytes().split(b'\\0')
assert {str(root / 'hook.py').encode()!r} in args, 'DHCP PID changed owner'
os.kill(pid, signal.SIGUSR1)
''')

            records = await events(FIRST)
            stack.push(partial(console_command, con, "ip", "route", "del", ENDPOINT + "/32", "dev", iface))
            await r.delete_table()
            table = "ask_dhcp_nat"
            await console_command(con, "nft", f'''table ip {table} {{
 chain postrouting {{ type nat hook postrouting priority 90;
 ip saddr {r.lan_ip} ip daddr {ENDPOINT} udp dport {PORT} masquerade;
 }}
}}''')
            stack.push(partial(console_command, con, "nft", "delete", "table", "ip", table))
            policy = candidate(r)
            policy["scope"] = [{"source": r.lan_ip, "destination": ENDPOINT, "protocol": "udp", "destination_port": PORT}]
            stack.push(partial(console_command, con, "rm", "-f", CONFIG))
            stack.push(partial(stop, con))
            await apply(con, policy, r=r)
            flows = [{"id": 0, "proto": "udp", "sport": PORT,
                      "connect_ip": ENDPOINT, "connect_port": PORT}]

            async def mapping(p, address):
                for _ in range(12):
                    await p.batch([0], 32, 0.02)
                    state = await r.state()
                    if len(state["flows"]) == 2:
                        break
                healthy(state)
                assert len(state["flows"]) == 2, state
                forward, = [row for row in state["flows"] if row["in"] == TARGET_LAN_IF]
                assert forward["new_src"].rsplit(":", 1)[0] == address, forward
                before = {row["cookie"]: int(row["packets"]) for row in state["flows"]}
                await p.batch([0], 128, 0.02)
                state = await r.state()
                after = {row["cookie"]: int(row["packets"]) for row in state["flows"]}
                assert before.keys() == after.keys() and all(after[k] - before[k] >= 100 for k in before), state
                return set(after)

            async with peer(r, flows, lease=240) as p:
                original = await mapping(p, FIRST)
                # Unforced: the client renews by itself at T1, half the
                # two-minute lease (RFC 2131 §4.4.5).
                # Traffic runs throughout, or the idle flow would age out.
                previous = len(records)
                await p.rpc("start", [0], count=0, interval=0.02, allow_loss=True, udp_timeout=0.1)
                try:
                    records = await events(FIRST, previous, timeout=75)
                finally:
                    report = (await p.rpc("stop", [0]))["0"]
                assert [row["event"] for row in records[previous:]] == ["renew"], records
                assert report["received"] > 0 and report["lost"] == 0, report
                assert await mapping(p, FIRST) == original
                # A RAM-root reboot loses the server's lease database while
                # its clients keep their addresses. Renewal must not NAK them.
                await stop_process(server)
                (root / "leases").unlink()
                server = start_server()
                stack.push(partial(stop_process, server))
                previous = len(records)
                await renew()
                records = await events(FIRST, previous)
                assert all(row["event"] == "renew" for row in records[previous:]), records
                assert await mapping(p, FIRST) == original
                hosts.write_text(f"{mac},{SECOND}\n")
                pid = int((root / "server.pid").read_text())
                assert f"--dhcp-hostsfile={hosts}".encode() in Path(f"/proc/{pid}/cmdline").read_bytes().split(b"\0")
                os.kill(pid, signal.SIGHUP)
                await asyncio.sleep(0.2)
                previous = len(records)
                await renew()
                records = await events(SECOND, previous)
                replacement = await mapping(p, SECOND)
                assert original.isdisjoint(replacement), (original, replacement)
                assert any(row["event"] == "deconfig" for row in records[previous:]), records
                r.record("dhcp-lifecycle", {"events": records, "old_cookies": sorted(original),
                                            "new_cookies": sorted(replacement)})
        finally:
            try:
                for name in ("server.log", "echo.log"):
                    if (root / name).exists():
                        r.record("dhcp-" + name, {"text": (root / name).read_text()})
                result = await console_command(con, "cat", str(root / "client.log"), check=False)
                r.record("dhcp-client-log", result)
            finally:
                await stack.teardown("DHCP lifecycle")
