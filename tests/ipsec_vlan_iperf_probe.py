"""Opt-in LAN-to-WAN throughput measurement for the SA provenance validation."""
import asyncio
import json
import os
from pathlib import Path

import pytest

from _topology import TARGET_LAN_IF, TARGET_WAN_IF, lan_run_python
from _flowtable_connections import (healthy)
from _flowtable_rig import (artifact_dir, command, console_command, read)
from _flowtable_service import (CONF, INIT)
from _flowtable_service_ipsec import (INNER, LAN_INNER, Transform)
from _flowtable_service_ipsec_replay import (AEAD, peer_errors, sa_state, xfrm_mib)
from _flowtable_tcp import (cpu, cpu_delta, software_tx)

pytestmark = pytest.mark.skipif(os.environ.get("ASK_IPSEC_IPERF") != "1",
                                reason="explicit lab throughput measurement")
PORT = 48993


@pytest.mark.parametrize("ipsec_service", [Transform(), AEAD["rfc4106-icv16"]],
                         ids=["cbc-sha256", "gcm-128"], indirect=True)
async def test_ipsec_vlan_iperf(ipsec_service):
    r = ipsec_service
    # Use the normal 1500-byte links instead of the fixture's deliberate
    # 1200/1400-byte exception-test routes. The fixture deletes these routes.
    for address, gateway, dev in ((INNER, os.environ["ASK_WAN_IP"], TARGET_WAN_IF),
                                  (LAN_INNER, r.lan_ip, TARGET_LAN_IF)):
        await command(r.target, r.session, "ip", "route", "replace", address + "/32",
                      "via", gateway, "dev", dev, "mtu", "1500")
    nat = ["POSTROUTING", "-s", LAN_INNER, "-d", INNER, "-p", "tcp",
           "--dport", str(PORT), "-j", "ACCEPT"]
    await command(r.target, r.session, "iptables", "-t", "nat", "-I", *nat)
    try:
        config = await read(r.target, r.session, CONF)
        result = await r.target.fs_write(r.session, CONF, config +
                                         f"scope saddr {LAN_INNER} daddr {INNER} dport {PORT}\n")
        assert result["errno"] == 0, result
        await console_command(r.service_console, INIT, "reload", timeout=45)
        for streams in (1, 4):
            server = await asyncio.create_subprocess_exec(
                "iperf3", "-s", "-1", "-B", INNER, "-p", str(PORT), "-J",
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
            task = None
            capture = None
            try:
                await asyncio.sleep(0.2)
                assert server.returncode is None
                if os.environ.get("ASK_IPSEC_CAPTURE") == "1" and streams == 4:
                    capture = await asyncio.create_subprocess_exec(
                        "tcpdump", "-i", r.ipsec_wire_if, "-n", "-s", "0", "-c", "20000",
                        "-w", str(artifact_dir() / "iperf-4-esp.pcap"),
                        f"src host {r.ipsec.outer} and ip proto 50",
                        stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
                argv = ["iperf3", "-c", INNER, "-B", LAN_INNER, "-p", str(PORT),
                        "-P", str(streams), "-t", "15", "-O", "3", "-Z", "-J"]
                # An optional aggregate rate lets the DUT be checked below
                # the software peer's crypto-queue saturation point. Iperf's
                # TCP -b value applies to each parallel stream separately.
                rate = int(os.environ.get("ASK_IPSEC_IPERF_BPS", "0"))
                if rate:
                    argv += ["-b", str(rate // streams)]
                script = f"""
import subprocess
result = subprocess.run({argv!r}, capture_output=True, text=True, timeout=40)
print(result.stdout, flush=True)
assert result.returncode == 0, result.stderr
"""
                peer_before = xfrm_mib(Path("/proc/net/xfrm_stat").read_text())
                task = asyncio.create_task(lan_run_python(r.lan, script, timeout=50,
                                                         label=f"ipsec_iperf_{streams}"))
                before = await r.wait(lambda s: sum(
                    f["in"] == TARGET_LAN_IF and f["dst"] == f"{INNER}:{PORT}"
                    and int(f["bytes"]) > 1_000_000 for f in s["flows"]) == streams,
                    timeout=12)
                tx_before, cpu_before = await software_tx(r), await cpu(r)
                await asyncio.sleep(5)
                cpu_after, tx_after = await cpu(r), await software_tx(r)
                after = await r.state()
                old = {f["cookie"]: f for f in before["flows"]}
                deltas = [{"in": f["in"], "sa": f["sa"], "in_sa": f["in_sa"],
                           "packets": int(f["packets"]) - int(old[f["cookie"]]["packets"]),
                           "bytes": int(f["bytes"]) - int(old[f["cookie"]]["bytes"])}
                          for f in after["flows"] if f["cookie"] in old]
                result = await task
                r.record(f"iperf-{streams}-console", {"rc": result.rc, "stdout": result.stdout})
                assert result.rc == 0, result.stdout
                client = json.loads(result.stdout.strip())
                stdout, stderr = await asyncio.wait_for(server.communicate(), 10)
                received = json.loads(stdout)
                record = {"argv": argv, "client": client, "server": received,
                          "server_stderr": stderr.decode(), "hardware": deltas,
                          "software_tx": {dev: tx_after[dev] - tx_before[dev] for dev in tx_before},
                          "cpu": cpu_delta(cpu_before, cpu_after),
                          "peer_errors": peer_errors(peer_before),
                          "peer_sa": (await command(r.ipsec.wan, r.session, "ip", "-s", "xfrm",
                                       "state", "get", *r.ipsec.state("out", r.ipsec.active["out"])))['stdout'],
                          "sa": {d: await sa_state(r, spi, d) for d, spi in r.ipsec.active.items()},
                          "before": before, "after": after}
                r.record(f"iperf-{streams}", record)
                assert server.returncode == 0 and "error" not in received, record
                assert received["end"]["sum_received"]["bytes"] > 0, record
                assert sum(d["in"] == TARGET_LAN_IF and d["sa"] != "0"
                           and d["packets"] > 1000 for d in deltas) == streams, deltas
                assert not record["peer_errors"], record["peer_errors"]
                healthy(after)
            finally:
                if task:
                    result = await task  # finish traffic before fixture cleanup
                    r.record(f"iperf-{streams}-console", {"rc": result.rc, "stdout": result.stdout})
                if capture and capture.returncode is None:
                    capture.terminate()
                    await capture.wait()
                if server.returncode is None:
                    server.terminate()
                    await asyncio.wait_for(server.communicate(), 5)
                await command(r.target, r.session, "conntrack", "-D", "-p", "tcp",
                              "--orig-src", LAN_INNER, "--orig-dst", INNER,
                              "--dport", str(PORT), check=False)
    finally:
        await command(r.target, r.session, "iptables", "-t", "nat", "-D", *nat)
