"""Physical driver removal drains flows before the provider releases its pins."""
from __future__ import annotations

import asyncio
import json
import os
import time

import pytest

from ask_orch.uart import Console
from _topology import TARGET_LAN_IF
from _flowtable_rig import (artifact_dir, HEALTH_BASELINE, TABLE, command, console_command, console_python, read, terminal_stream)

pytestmark = pytest.mark.skipif(os.environ.get("ASK_FLOWTABLE_UNREGISTER") != "1",
                               reason="explicit physical driver removal; fresh boot required")


async def test_flowtable_physical_unregister(rig):
    r = rig
    r.recovery_console = Console.target(log_path=str(artifact_dir() / "unregister-uart.log"))
    con = r.recovery_console
    await asyncio.to_thread(con.login, "root", None)
    addresses = json.loads((await command(r.target, r.session, "ip", "-j", "-4", "addr",
                                         "show", "dev", TARGET_LAN_IF))["stdout"])[0]["addr_info"]
    await r.table()
    await r.exchange(128, promiscuous=False)
    initial = await r.wait(lambda s: s["entries"] == 2)
    baseline = len(r.echo.received)
    traffic = asyncio.create_task(terminal_stream(r, duration=24))
    device = None
    unloaded = False
    try:
        deadline = time.monotonic() + 5
        while len(r.echo.received) < baseline + 32:
            assert not traffic.done() and time.monotonic() < deadline
            await asyncio.sleep(0.05)
        # The unbind syscall waits for CDX's configuration/queue references.
        # A separate process lets this controller perform the required full
        # provider teardown while the old netdevice is still pinned safely.
        result = await console_python(con, f'''
import json, pathlib, subprocess, sys, tempfile
device = pathlib.Path('/sys/class/net/{TARGET_LAN_IF}/device').resolve(strict=True)
driver = (device / 'driver').resolve(strict=True)
assert driver.name == 'fsl_dpa', driver
result = pathlib.Path(tempfile.mkdtemp(prefix='ask_flowtable_unbind_')) / 'result.json'
code = """import json, pathlib, sys
try:
    pathlib.Path(sys.argv[1]).write_text(sys.argv[2])
    result = {{'ok': True}}
except Exception as error:
    result = {{'ok': False, 'error': repr(error)}}
target = pathlib.Path(sys.argv[3])
temporary = target.with_suffix('.tmp')
temporary.write_text(json.dumps(result))
temporary.replace(target)
"""
p = subprocess.Popen([sys.executable, '-c', code, str(driver / 'unbind'), device.name, str(result)],
                     stdin=subprocess.DEVNULL,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     start_new_session=True)
print(json.dumps({{'device': str(device), 'name': device.name, 'driver': str(driver),
                  'result': str(result), 'pid': p.pid}}))
''')
        device = json.loads(result["stdout"])
        deadline = time.monotonic() + 10
        while True:
            state = await r.state()
            gone = await console_command(con, "test", "-e", f"/sys/class/net/{TARGET_LAN_IF}", check=False)
            if gone["rc"] == 1 and state["entries"] == 0 and state["bindings"] <= 1:
                break
            assert time.monotonic() < deadline, state
            await asyncio.sleep(0.05)
        assert state["handle_refs"] == state["neighbour_refs"] == state["quarantine"] == 0, state
        assert state["fatal"] == 0 and state["errors"] == HEALTH_BASELINE["errors"], state
        assert state["installs"] == state["deletes"], state
        # Completion is deliberately held until the provider releases its
        # non-flow references; unloading only the adapter is insufficient.
        assert (await console_command(con, "test", "-e", device["result"], check=False))["rc"] == 1
        r.record("unregister-drained", {"initial": initial, "state": state, "device": device})
    finally:
        try:
            await console_command(con, "nft", "delete", "table", "inet", TABLE, check=False)
            for module in ("ask_flowtable", "cdx"):
                present = await console_command(con, "test", "-e", "/sys/module/" + module, check=False)
                if present["rc"] == 0:
                    await console_command(con, "rmmod", module, timeout=25)
            unloaded = True
            if device:
                # Rebinding is part of test restoration. A fresh CDX hardware
                # boot remains the supported offload recovery boundary.
                restored = await console_python(con, f'''
import json, pathlib, subprocess, time
device = {device!r}
result = pathlib.Path(device['result'])
deadline = time.monotonic() + 10
while not result.exists():
    assert time.monotonic() < deadline, device
    time.sleep(.05)
completion = json.loads(result.read_text())
assert completion['ok'], completion
assert not (pathlib.Path(device['device']) / 'driver').exists()
(pathlib.Path(device['driver']) / 'bind').write_text(device['name'])
assert pathlib.Path('/sys/class/net/{TARGET_LAN_IF}/device').resolve() == pathlib.Path(device['device'])
def run(*args): subprocess.run(args, check=True, capture_output=True, text=True)
run('ip', 'link', 'set', 'dev', {TARGET_LAN_IF!r}, 'address', {r.dut_lan_mac!r}, 'up')
for address in {addresses!r}:
    args = ['ip', 'addr', 'replace', address['local'] + '/' + str(address['prefixlen']), 'dev', {TARGET_LAN_IF!r}]
    if 'broadcast' in address: args += ['broadcast', address['broadcast']]
    run(*args)
run('ip', 'route', 'replace', {r.lan_ip + '/32'!r}, 'dev', {TARGET_LAN_IF!r})
run('ip', 'neigh', 'replace', {r.lan_ip!r}, 'dev', {TARGET_LAN_IF!r}, 'lladdr', {r.lan_mac!r}, 'nud', 'permanent')
result.unlink()
result.parent.rmdir()
print(json.dumps({{'unbound': completion, 'rebound': device['name']}}))
''')
                r.record("unregister-restored", json.loads(restored["stdout"]))
        finally:
            r.record("unregister-transition", await traffic)
    assert unloaded
    await r.clear_ct()
    await r.exchange(256, promiscuous=False)
    r.record("unregister-software", {"echoes": 256, "provider_absent": True,
                                     "boot_id": await read(r.target, r.session, "/proc/sys/kernel/random/boot_id")})
