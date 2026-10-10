"""Shared support for mroute capacity."""

from __future__ import annotations

import asyncio
import json
import os
import re
import sys
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from _flowtable_rig import artifact_dir, stop_boot_daemon
from _mcast_cpu import cpu_frames
from _mcast_e2e import (
    _exec,
    flowtable_proc,
    mroute_line,
    mroute_proc_row,
    wan_source_address,
)
from _mcast_helpers import multicast_on
from _topology import TARGET_WAN_IF, lan_run_python
from ask_orch.counters import kernel_rx_packets
from _mroute_capture import assert_results, multicast_mac, payload

COUNT, PPS, PORT = 256, 200, 47358
CAPTURE_SOURCE = Path(__file__).with_name("_mroute_capture.py").read_text()


async def _python(lan, script):
    if lan is not None:
        r = await lan_run_python(lan, script, label="_mroute_capture", timeout=15)
        assert r.rc == 0, r.stdout
        return r.stdout
    proc = await asyncio.create_subprocess_exec(
        "sudo", "-n", sys.executable, "-c", script,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    out, err = await asyncio.wait_for(proc.communicate(), 15)
    assert proc.returncode == 0, err.decode()
    return out.decode()


@asynccontextmanager
async def _capture(lan, config):
    path = f"/tmp/ask-mroute-capture-{uuid.uuid4().hex}"
    config = {**config, "ready": path + ".ready", "result": path + ".json"}
    script = path + ".py"
    try:
        await _python(lan, f"""
import pathlib, subprocess, sys
pathlib.Path({script!r}).write_text({CAPTURE_SOURCE!r})
with open({path + '.log'!r}, 'w') as log:
    child = subprocess.Popen([sys.executable, {script!r}, {json.dumps(config)!r}],
                             stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                             start_new_session=True)
pathlib.Path({path + '.pid'!r}).write_text(str(child.pid))
""")
        for _ in range(30):
            out = await _python(lan, f"""
import pathlib
ready = pathlib.Path({config['ready']!r})
print('READY' if ready.exists() else 'WAIT')
""")
            if "READY" in out.splitlines():
                break
            await asyncio.sleep(0.1)
        else:
            log = await _python(lan, f"print(open({path + '.log'!r}).read())")
            raise AssertionError(f"multicast capture did not become ready: {log}")
        yield (lan, config, path)
    finally:
        # Verify process identity before signalling; a capture may have hit its
        # own deadline. Never signal a recycled PID or another test's capture.
        await _python(lan, f"""
import os, pathlib, signal, time
pidfile = pathlib.Path({path + '.pid'!r})
if pidfile.exists():
    pid = int(pidfile.read_text())
    try:
        args = pathlib.Path('/proc/%d/cmdline' % pid).read_bytes().split(b'\\0')
        if {script.encode()!r} in args:
            os.kill(pid, signal.SIGTERM)
    except (ProcessLookupError, FileNotFoundError):
        pass
for _ in range(50):
    if pathlib.Path({config['result']!r}).exists():
        break
    time.sleep(0.1)
""")


async def _finish(capture):
    lan, config, path = capture
    result = json.loads((await _python(lan, f"print(open({config['result']!r}).read())")).strip())
    await _python(lan, f"""
import pathlib
for suffix in ('.pid', '.ready', '.json', '.log', '.py'):
    pathlib.Path({path!r} + suffix).unlink(missing_ok=True)
""")
    return result


@asynccontextmanager
async def _daemon(target, session, interfaces):
    await multicast_on(target, session)
    name = "ask-smcroute-" + uuid.uuid4().hex[:8]
    config = f"/tmp/{name}.conf"
    text = "".join(f"phyint {dev} enable\n" for dev in interfaces)
    r = await target.fs_write(session, config, text)
    assert r.get("errno", 0) == 0, r

    async def command(*args, check=True):
        return await _exec(target, session, "smcroutectl", "-i", name, *args, check=check)

    try:
        # A different daemon owning the default MRT table makes startup fail;
        # it is not stopped or replaced by this test.
        await _exec(target, session, "smcrouted", "-N", "-i", name,
                    "-f", config, "-l", "notice")
        for _ in range(30):
            if (await command("show", "routes", check=False))["rc"] == 0:
                break
            await asyncio.sleep(0.1)
        else:
            raise AssertionError("smcrouted failed to own the default routing table")
        yield command
    finally:
        await command("kill", check=False)
        await target.fs_write(session, config, "")


async def _state(target, session, group, state, listeners=(), *, family=None):
    """Wait for the group's row to say `state` with exactly `listeners`.

    With `family`, a few frames of the stream go out between looks: a routed
    group is carried only once Linux has been seen forwarding a copy of it to
    every oif, so a route nothing has used yet waits as pending-confirm."""
    last = ""
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        last = await mroute_proc_row(target, session, group)
        names = re.search(r"listeners=(\S+)", last)
        actual = set(names[1].split(",")) if names and names[1] != "-" else set()
        if f"state={state} " in last and actual == set(listeners):
            return last
        if family is not None:
            await asyncio.to_thread(_send, {"family": family, "source": wan_source_address(family),
                                            "group": group, "token": uuid.uuid4().hex}, 8)
        await asyncio.sleep(0.1)
    raise AssertionError(f"{group}: expected {state}, {list(listeners)}, got {last!r}")


async def _absent(target, session, group):
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        if not await mroute_proc_row(target, session, group):
            return
        await asyncio.sleep(0.1)
    raise AssertionError(f"{group}: multicast row survived route removal")


def _send(config, count=COUNT):
    from scapy.all import IP, UDP, Ether, IPv6, Raw, sendp
    layer = (IP(src=config["source"], dst=config["group"], ttl=64)
             if config["family"] == 4 else
             IPv6(src=config["source"], dst=config["group"], hlim=64))
    frames = [Ether(dst=multicast_mac(config["group"])) / layer /
              UDP(sport=PORT, dport=PORT) / Raw(payload(config["token"], i))
              for i in range(count)]
    sendp(frames, iface=os.environ.get("ASK_WAN_INJECT_IF", ""),
          inter=1 / PPS, verbose=False)


async def _window(target, session, *, family, group, observers,
                  expected, hardware, label):
    """observers is [(peer, {interface: expected source MAC}), ...]."""
    from contextlib import AsyncExitStack
    config = {"family": family, "source": wan_source_address(family), "group": group,
              "port": PORT, "count": COUNT, "token": uuid.uuid4().hex}
    captures = []
    async with AsyncExitStack() as stack:
        for peer, interfaces in observers:
            captures.append(await stack.enter_async_context(
                _capture(peer, {**config, "interfaces": interfaces})))
        before_row = await mroute_proc_row(target, session, group)
        counted = await cpu_frames(target, session, TARGET_WAN_IF)
        rx = await kernel_rx_packets(target, session, TARGET_WAN_IF)
        await asyncio.to_thread(_send, config)
        await asyncio.sleep(0.4)  # drain receiver queues before requesting output
        cpu = await cpu_frames(target, session, TARGET_WAN_IF) - counted
        rx = await kernel_rx_packets(target, session, TARGET_WAN_IF) - rx
    results = {}
    for capture in captures:
        results.update(await _finish(capture))
    row = await mroute_proc_row(target, session, group)  # also folds MFC counters
    route, _ = await mroute_line(target, session, family, config["source"], group)
    # The port's receive count is for the record only: the WAN segment's own
    # traffic moves it too (see _mcast_cpu), and the stream's count does not.
    artifact = {"config": config, "results": results, "before": before_row,
                "after": row, "mroute": route, "stream_cpu": cpu, "port_rx": rx}
    artifact_dir().mkdir(parents=True, exist_ok=True)
    (artifact_dir() / f"mroute-capacity-{label}-v{family}.json").write_text(json.dumps(artifact, indent=2))
    assert_results(results, expected, COUNT)
    assert ("offload" in route) == hardware, route
    if hardware:
        assert "state=installed" in row, row
        assert cpu < COUNT * 0.1, artifact
        packets = lambda text: int(re.search(r"packets=(\d+)", text)[1])
        assert packets(row) - packets(before_row) >= COUNT * 0.95, artifact
    else:
        assert "state=refused-listener" in row and "listeners=- " in row, row
        assert cpu >= COUNT, artifact


async def _preflight(target, session):
    # The boot's service commits the ruleset when an interface goes, and a
    # commit takes back every routed group's confirmation: a case that deletes
    # an oif and checks the group without sending again would fail after a
    # fresh boot and pass after a test that had stopped it.
    await stop_boot_daemon()
    assert "mroute_groups 0\n" in await flowtable_proc(target, session), \
        "existing multicast routes belong to another workload"
