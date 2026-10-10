"""Staging the multicast wire oracle on a listener, and feeding it frames.

The oracle itself is _mcast_wire_capture.py, which runs unchanged on the host
that receives the replicas; this module stages it there as a backgrounded
process, collects its verdict, and builds the frames the orchestrator injects.
The LAN VM is reachable over its console alone, so every step on it goes
through lan_run_python(); a `lan` of None means this host, for a replica the
orchestrator receives itself.
"""
from __future__ import annotations
import asyncio
from contextlib import asynccontextmanager
import json
import os
from pathlib import Path
import sys
import uuid
from _topology import lan_run_python
from _mcast_wire_capture import multicast_mac, payload

CAPTURE_SOURCE = Path(__file__).with_name("_mcast_wire_capture.py").read_text()


async def run_python(lan, script: str, label: str = "mcast_wire") -> str:
    if lan is not None:
        result = await lan_run_python(lan, script, label=label, timeout=15)
        assert result.rc == 0, result.stdout
        return result.stdout
    proc = await asyncio.create_subprocess_exec(
        "sudo", "-n", sys.executable, "-c", script,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    out, err = await asyncio.wait_for(proc.communicate(), 15)
    assert proc.returncode == 0, err.decode()
    return out.decode()


def new_config(family: int, source: str, group: str, port: int,
               interfaces: list[str], seconds: float = 30) -> dict:
    return {"family": family, "source": source, "group": group, "port": port,
            "token": uuid.uuid4().hex, "interfaces": interfaces, "seconds": seconds}


@asynccontextmanager
async def capture(lan, config: dict):
    """Run the oracle on `lan` for the duration of the block.

    The process is identified by its own script path before it is signalled,
    so a capture that already hit its deadline never takes a recycled PID or
    another test's capture with it.
    """
    path = f"/tmp/ask-mcast-wire-{uuid.uuid4().hex}"
    config = {**config, "ready": path + ".ready", "result": path + ".json"}
    script = path + ".py"
    handle = {"lan": lan, "config": config, "path": path, "result": None}
    try:
        await run_python(lan, f"""
import pathlib, subprocess, sys
pathlib.Path({script!r}).write_text({CAPTURE_SOURCE!r})
with open({path + '.log'!r}, 'w') as log:
    child = subprocess.Popen([sys.executable, {script!r}, {json.dumps(config)!r}],
                             stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                             start_new_session=True)
pathlib.Path({path + '.pid'!r}).write_text(str(child.pid))
""")
        for _ in range(50):
            out = await run_python(lan, f"""
import pathlib
print('READY' if pathlib.Path({config['ready']!r}).exists() else 'WAIT')
""")
            if "READY" in out.splitlines():
                break
            await asyncio.sleep(0.1)
        else:
            log = await run_python(lan, f"print(open({path + '.log'!r}).read())")
            raise AssertionError(f"multicast capture did not become ready: {log}")
        yield handle
    finally:
        await run_python(lan, f"""
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
        text = await run_python(lan, f"""
import pathlib
result = pathlib.Path({config['result']!r})
print(result.read_text() if result.exists() else '{{}}')
for suffix in ('.pid', '.ready', '.json', '.log', '.py'):
    pathlib.Path({path!r} + suffix).unlink(missing_ok=True)
""")
        handle["result"] = json.loads(text.strip().splitlines()[-1])


def frames(config: dict, sizes: dict[int, int], count: int, *, hops: int = 64,
           dont_fragment: bool = True, source_mac: str | None = None,
           vlan: int | None = None) -> list:
    """`count` frames per size class, interleaved, each an exact IP length.

    `sizes` maps a class to the IP packet length its datagrams carry, which is
    the length an MTU is compared against.
    """
    from scapy.all import Ether, Dot1Q, IP, IPv6, UDP, Raw
    overhead = (20 if config["family"] == 4 else 40) + 8
    out = []
    for sequence in range(count):
        for size_class, length in sizes.items():
            if config["family"] == 4:
                layer = IP(src=config["source"], dst=config["group"], ttl=hops,
                           flags="DF" if dont_fragment else 0)
            else:
                layer = IPv6(src=config["source"], dst=config["group"], hlim=hops)
            ethernet = Ether(dst=multicast_mac(config["group"]).hex(":"),
                             **({"src": source_mac} if source_mac else {}))
            if vlan is not None:
                ethernet = ethernet / Dot1Q(vlan=vlan)
            out.append(ethernet / layer / UDP(sport=config["port"], dport=config["port"])
                       / Raw(payload(config["token"], size_class, sequence,
                                     length - overhead)))
    return out


def send(frames_to_send: list, iface: str | None = None, pps: int = 200) -> None:
    """Inject on the orchestrator's DUT-facing wire, at a rate the software
    path keeps up with so a loss is never the CPU's."""
    from scapy.all import conf
    sock = conf.L2socket(iface=iface or os.environ.get("ASK_WAN_INJECT_IF", ""))
    try:
        import time
        for frame in frames_to_send:
            sock.send(frame)
            time.sleep(1 / pps)
    finally:
        sock.close()
