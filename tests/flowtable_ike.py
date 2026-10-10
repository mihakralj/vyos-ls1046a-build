"""Negotiated IKEv2 and IKEv1 SAs survive child rekey and an owned peer restart."""

import asyncio
from functools import partial
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import tempfile

import pytest

from ask_orch.commands import console_command, console_python
from ask_orch.uart import Console
from _flowtable_connections import healthy, peer
from _flowtable_policy import CONFIG, apply, candidate, stop
from _flowtable_rig import DPORT
from _topology import TARGET_WAN_IF, TopologyStack
from _native_process import dut_process

pytestmark = pytest.mark.requires("swanctl")
OUTER, PEER, INNER = "198.18.110.1", "198.18.110.2", "198.18.111.2"
REQID, PORT = 50110, DPORT + 3110


def _connection(local, remote, local_ts, remote_ts, *, hardware, version=2):
    return f'''
connections {{
 ask-ike {{
  version = {version}
  local_addrs = {local}
  remote_addrs = {remote}
  proposals = aes128-sha256-modp2048
  mobike = no
  rekey_time = 0
  local {{ auth = psk
           id = {local} }}
  remote {{ auth = psk
            id = {remote} }}
  children {{
   ask-child {{
    local_ts = {local_ts}/32
    remote_ts = {remote_ts}/32
    esp_proposals = aes128-sha256
    reqid = {REQID}
    rekey_time = 0
    hw_offload = {'packet' if hardware else 'no'}
   }}
  }}
 }}
}}
secrets {{ ike-test {{ id-1 = {local}
                     id-2 = {remote}
                     secret = "ASK isolated interoperability test" }} }}
'''


@pytest.mark.rfc("7296")
@pytest.mark.rfc("2409")
@pytest.mark.parametrize("version", [2, 1], ids=["ikev2", "ikev1"])
async def test_rekey_and_peer_restart(rig, version):
    r = rig
    con = Console.target()
    stack = TopologyStack()
    namespace = f"ask-ike-{os.getpid()}"

    async def local(*argv, timeout=30):
        result = await asyncio.to_thread(subprocess.run, argv, capture_output=True,
                                         text=True, timeout=timeout)
        assert result.returncode == 0, (argv, result.stdout, result.stderr)
        return result.stdout

    async def local_add(argv, undo):
        await local(*argv)
        stack.push(partial(local, *undo))

    async def dut_add(argv, undo):
        await console_command(con, *argv)
        stack.push(partial(console_command, con, *undo))

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

    async def states():
        # XFRM's text contains key material. Return only this test's SPI and
        # offload identities, never the full listing.
        result = await console_python(con, rf'''
import json,re,subprocess
text = subprocess.check_output(['ip','xfrm','state'], text=True)
rows = []
for row in re.split(r'(?m)(?=^src )', text):
    if re.search(r'\breqid {REQID}\b', row):
        spi = re.search(r'\bspi (0x[0-9a-f]+)', row)
        direction = re.search(r'\bdev {TARGET_WAN_IF} dir (in|out) mode packet\b', row)
        rows.append({{'spi': spi[1] if spi else None,
                      'direction': direction[1] if direction else None}})
print(json.dumps(rows))
''')
        return json.loads(result["stdout"])

    async def established(previous=frozenset()):
        # IKEv1 keeps a rekeyed SA until it expires (strongSwan's
        # delete_rekeyed defaults to no), so earlier SAs may linger.
        deadline = asyncio.get_running_loop().time() + 20
        while True:
            rows = [row for row in await states() if row["spi"] not in previous]
            spis = {row["spi"] for row in rows}
            if len(rows) == 2:
                assert {row["direction"] for row in rows} == {"in", "out"}, rows
                return previous | spis
            assert asyncio.get_running_loop().time() < deadline, rows
            await asyncio.sleep(0.3)

    async def hardware(p, label):
        for _ in range(12):
            await p.batch([0], 32, 0.02)
            before = await r.state()
            if len(before["flows"]) == 2:
                break
        healthy(before)
        assert len(before["flows"]) == 2, before
        await p.batch([0], 128, 0.02)
        after = await r.state()
        healthy(after)
        old = {row["cookie"]: int(row["packets"]) for row in before["flows"]}
        new = {row["cookie"]: int(row["packets"]) for row in after["flows"]}
        assert old.keys() == new.keys() and all(new[key] - old[key] >= 100 for key in old), (before, after)
        r.record(label, {"before": before, "after": after, "sas": await states()})

    # The host's native swanctl AppArmor profile reads configs under /etc/swanctl.
    # A private subdirectory stays outside the standing daemon's conf.d includes.
    with tempfile.TemporaryDirectory(prefix="ask-ike-") as directory, \
            tempfile.TemporaryDirectory(prefix="ask-ike-", dir="/etc/swanctl") as configs:
        root = Path(directory)
        host_config = Path(configs) / "swanctl.conf"
        uri = f"unix://{root}/charon.vici"
        daemon = f'''charon {{
 load_modular = no
 filelog {{
  stdout {{
   default = 1
   flush_line = yes
  }}
 }}
 plugins {{
  vici {{ socket = {uri} }}
  kernel-netlink {{ install_routes = no }}
 }}
}}
'''
        (root / "strongswan.conf").write_text(daemon)
        host_config.write_text(_connection(PEER, OUTER, INNER, r.lan_ip, hardware=False, version=version))

        async def swan(side, *args):
            argv = ("swanctl", *args, "--uri", uri)
            if side == "dut":
                result = await console_command(con, *argv, timeout=30)
                return result["stdout"]
            return await local("ip", "netns", "exec", namespace, *argv)

        async def load(side):
            for command in ("--load-conns", "--load-creds"):
                options = ["--noprompt"] if command == "--load-creds" else []
                path = root / "swanctl.conf" if side == "dut" else host_config
                loaded = await swan(side, command, "--file", str(path), *options)
                r.record("ike-" + side + command, {"stdout": loaded})
                if command == "--load-conns":
                    assert "loaded connection 'ask-ike'" in loaded, loaded

        async def start_peer():
            with (root / "peer.log").open("ab") as log:
                process = subprocess.Popen(
                    ["ip", "netns", "exec", namespace, "timeout", "300", "/usr/sbin/charon-systemd"],
                    stdin=subprocess.DEVNULL, stdout=log, stderr=log, start_new_session=True,
                    env={**os.environ, "STRONGSWAN_CONF": str(root / "strongswan.conf")})
            stack.push(partial(stop_process, process))
            for _ in range(100):
                assert process.poll() is None, (root / "peer.log").read_text()
                try:
                    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as probe:
                        probe.connect(str(root / "charon.vici"))
                except (FileNotFoundError, ConnectionRefusedError):
                    pass
                else:
                    await load("peer")
                    return process
                await asyncio.sleep(0.1)
            pytest.fail("private peer IKE daemon did not create its VICI socket")

        try:
            await local_add(["ip", "netns", "add", namespace], ["ip", "netns", "del", namespace])
            # A fixed address: the DUT's neighbour for PEER outlives one case,
            # and a fresh random one would leave it REACHABLE at a stale MAC.
            await local("ip", "link", "add", "link", r.wan_if, "name", "ike-peer",
                        "address", "02:00:00:00:11:02",
                        "netns", namespace, "type", "macvlan", "mode", "bridge")
            for dev in ("lo", "ike-peer"):
                await local("ip", "-n", namespace, "link", "set", dev, "up")
            await local("ip", "-n", namespace, "addr", "add", PEER + "/24", "dev", "ike-peer")
            await local("ip", "-n", namespace, "addr", "add", INNER + "/32", "dev", "lo")
            await local("ip", "-n", namespace, "route", "add", r.lan_ip + "/32", "via", OUTER)
            await dut_add(["ip", "addr", "add", OUTER + "/24", "dev", TARGET_WAN_IF],
                          ["ip", "addr", "del", OUTER + "/24", "dev", TARGET_WAN_IF])
            await dut_add(["ip", "route", "add", INNER + "/32", "via", PEER, "dev", TARGET_WAN_IF],
                          ["ip", "route", "del", INNER + "/32", "via", PEER])
            exemption = ["POSTROUTING", "-s", r.lan_ip, "-d", INNER, "-j", "ACCEPT"]
            await dut_add(["iptables", "-t", "nat", "-I", exemption[0], "1", *exemption[1:]],
                          ["iptables", "-t", "nat", "-D", *exemption])
            await console_python(con, f'''
import pathlib
assert not pathlib.Path('/run/charon.pid').exists(), 'DUT already has an IKE daemon'
root = pathlib.Path({str(root)!r})
root.mkdir()
(root / 'strongswan.conf').write_text({daemon!r})
(root / 'swanctl.conf').write_text({_connection(OUTER, PEER, r.lan_ip, INNER, hardware=True, version=version)!r})
''')
            stack.push(partial(console_command, con, "rm", "-rf", str(root)))
            await dut_process(stack, con, ["/usr/libexec/ipsec/charon"],
                              base=str(root / "dut"),
                              env={"STRONGSWAN_CONF": str(root / "strongswan.conf")})
            await console_python(con, f'''
import pathlib,time
root = pathlib.Path({str(root)!r})
for _ in range(100):
    if (root / 'charon.vici').exists(): break
    time.sleep(0.1)
else: raise TimeoutError('DUT IKE daemon did not start')
print('ready')
''', timeout=15)
            await load("dut")
            remote = await start_peer()
            echo = ("import socket\n"
                    f"s=socket.socket(socket.AF_INET,socket.SOCK_DGRAM); s.bind(({INNER!r},{PORT}))\n"
                    "while True:\n data,peer=s.recvfrom(2048)\n s.sendto(data,peer)\n")
            with (root / "echo.log").open("w") as log:
                process = subprocess.Popen(["ip", "netns", "exec", namespace, "timeout", "300", "python3", "-c", echo],
                    stdin=subprocess.DEVNULL, stdout=log, stderr=log, start_new_session=True)
            stack.push(partial(stop_process, process))
            await swan("dut", "--initiate", "--child", "ask-child", "--timeout", "20")
            current = await established()
            await r.delete_table()
            policy = candidate(r)
            policy["scope"] = [{"source": r.lan_ip, "destination": INNER, "protocol": "udp", "destination_port": PORT}]
            stack.push(partial(console_command, con, "rm", "-f", CONFIG))
            stack.push(partial(stop, con))
            await apply(con, policy, r=r)
            flows = [{"id": 0, "proto": "udp", "sport": PORT,
                      "connect_ip": INNER, "connect_port": PORT}]
            async with peer(r, flows, lease=240) as p:
                await hardware(p, "ike-initial")
                for transition in ("rekey", "peer-restart"):
                    await p.rpc("start", [0], count=0, interval=0.02, allow_loss=True, udp_timeout=0.1)
                    try:
                        if transition == "rekey":
                            await swan("dut", "--rekey", "--child", "ask-child")
                        else:
                            await stop_process(remote)
                            remote = await start_peer()
                            await swan("dut", "--initiate", "--child", "ask-child", "--timeout", "20")
                        current = await established(current)
                        await asyncio.sleep(2)
                    finally:
                        report = (await p.rpc("stop", [0]))["0"]
                        r.record("ike-" + transition, report)
                    assert report["received"] > 0 and report["lost"] <= 200, report
                    await hardware(p, "ike-after-" + transition)
        finally:
            try:
                # The tails: a record is cut short, and the daemons open with
                # pages of plugin loading.
                for name in ("peer.log", "echo.log"):
                    if (root / name).exists():
                        r.record("ike-" + name, {"text": (root / name).read_text()[-3500:]})
                result = await console_command(con, "tail", "-c", "3500", str(root / "dut.log"), check=False)
                r.record("ike-dut-log", result)
            finally:
                await stack.teardown("IKE interoperability")
