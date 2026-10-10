"""Bulk, simultaneous TCP and small-packet UDP NAT with hardware evidence."""
import asyncio
import json
import math
import os
import subprocess
from pathlib import Path

import pytest
import pytest_asyncio

from ask_orch.client import Agent
from ask_orch.commands import console_python

from ask_orch.uart import Console
from _topology import LAN_NIC, TARGET_LAN_IF, TARGET_WAN_IF, lan_run_python
from _flowtable_connections import (by_key, healthy)
from _flowtable_rig import (artifact_dir, DPORT, HEALTH_BASELINE, WAN_IP, command, console_command)
from _flowtable_policy import (CONFIG, apply, candidate, stop)
from _flowtable_tcp import (cpu, cpu_delta, software_tx)

PORT, STREAMS = DPORT + 3000, 4
# Short enough for every suite run. The proof is structural -- every bulk
# stream on its hardware entry, software TX flat, cookies stable -- and the
# rate only needs a steady-state sample: iperf discards the ramp (-O), and the
# hardware window sits inside the measured interval.
IPERF_SECONDS, IPERF_OMIT, WINDOW_SECONDS = 5, 2, 3


def _udp_counters():
    rows = [line.split()[1:] for line in Path("/proc/net/snmp").read_text().splitlines()
            if line.startswith("Udp:")]
    return dict(zip(rows[0], map(int, rows[1])))


def _nic_counters(name):
    # A bridge has no driver statistics; retain its physical members too.
    names = [name, *sorted(path.name for path in (Path("/sys/class/net") / name / "brif").glob("*"))]
    reports = {}
    for interface in names:
        result = subprocess.run(["ethtool", "-S", interface], capture_output=True, text=True, timeout=5)
        reports[interface] = {"rc": result.returncode, "stdout": result.stdout, "stderr": result.stderr}
    return reports


async def _port_counters(con):
    # Offloaded receive drops bypass the netdev's ethtool counters.
    result = await console_python(con, f'''
import json
from pathlib import Path
print(json.dumps({{dev: {{name: (Path('/sys/class/net') / dev / name).read_text()
                         for name in ('mac_rx_stats', 'mac_tx_stats')}}
                  for dev in {(TARGET_LAN_IF, TARGET_WAN_IF)!r}}}))
''')
    return json.loads(result["stdout"])


@pytest_asyncio.fixture
async def rate_path(rig):
    r = rig
    addresses = json.loads((await command(r.target, r.session, "ip", "-j", "-4", "addr", "show",
                                         "dev", TARGET_WAN_IF))["stdout"])
    external = next(a["local"] for a in addresses[0]["addr_info"] if a["family"] == "inet")
    wan = Agent("wan", f"http://{os.environ.get('ASK_WAN_IP', '127.0.0.1')}:9110")
    existing = json.loads((await command(wan, r.session, "ip", "-j", "route", "show", "exact", external + "/32"))["stdout"])
    assert not existing, ("benchmark requires an unused endpoint host route", existing)
    # Prior MTU-exception proofs can leave endpoint PMTU caches at 1200.
    # A temporary explicit route provides fresh 1500-byte path metrics.
    await command(wan, r.session, "ip", "route", "add", external + "/32", "dev", r.wan_if, "mtu", "1500")
    try:
        yield r
    finally:
        await command(wan, r.session, "ip", "route", "del", external + "/32", "dev", r.wan_if)


@pytest.mark.parametrize("reverse", [False, True], ids=["forward", "reverse"])
async def test_flowtable_nat_throughput(rate_path, reverse):
    await _throughput(rate_path, reverse=reverse)


async def test_small_packets(rate_path):
    await _throughput(rate_path, udp=True)


async def test_simultaneous_tcp_directions(rate_path):
    """Both directions at once, unpaced. Each saturates its egress port, whose
    offloaded queue is bounded in time and shared out per flow, so neither
    direction's ACKs wait behind the other's data (A313): unbounded, the
    reverse direction got 1.3-2.2 Gbit/s behind a 9 ms queue."""
    await _throughput(rate_path, bidirectional=True)


async def _throughput(rate_path, *, udp=False, bidirectional=False, reverse=False):
    r = rate_path
    protocol, number = ("udp", "17") if udp else ("tcp", "6")
    seconds = 10 if udp or bidirectional else IPERF_SECONDS
    streams = 1 if udp else STREAMS
    for address, dev in ((r.lan_ip, TARGET_LAN_IF), (WAN_IP, TARGET_WAN_IF)):
        await command(r.target, r.session, "ip", "route", "replace", address + "/32", "dev", dev, "mtu", "1500")
    addresses = json.loads((await command(r.target, r.session, "ip", "-j", "-4", "addr", "show",
                                         "dev", TARGET_WAN_IF))["stdout"])
    external = next(a["local"] for a in addresses[0]["addr_info"] if a["family"] == "inet")
    policy = candidate(r)
    policy["scope"] = [{"source": r.lan_ip, "destination": WAN_IP, "protocol": protocol, "destination_port": PORT}]
    nat_table = "ask_nat_rate_test"
    nat = (f"table ip {nat_table} {{ chain postrouting {{ type nat hook postrouting priority 90; "
           f"ip saddr {r.lan_ip} ip daddr {WAN_IP} {protocol} dport {PORT} masquerade; }}; }}")
    server = lan_task = None
    with Console.target(log_path=str(artifact_dir() / "nat-rate-uart.log")) as con:
        await asyncio.to_thread(con.login, "root", None)
        assert (await command(r.target, r.session, "nft", "list", "table", "ip", nat_table, check=False))["rc"] != 0
        await command(r.target, r.session, "nft", nat)
        try:
            await apply(con, policy, r=r)
            server = await asyncio.create_subprocess_exec("iperf3", "-s", "-1", "-B", WAN_IP, "-p", str(PORT), "-J",
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
            await asyncio.sleep(0.2)
            assert server.returncode is None, "dedicated iperf server failed to start"
            script = f'''
import json, subprocess
link = subprocess.check_output(['ethtool', {LAN_NIC!r}], text=True)
stats_before = subprocess.check_output(['ethtool', '-S', {LAN_NIC!r}], text=True)
assert 'Speed: 10000Mb/s' in link and 'Duplex: Full' in link and 'Link detected: yes' in link, link
argv = ['iperf3', '-c', {WAN_IP!r}, '-B', {r.lan_ip!r}, '-p', {str(PORT)!r},
        '-P', {str(streams)!r}, '-t', {str(seconds)!r}, '-O', {str(IPERF_OMIT)!r}, '-J']
argv += {['-u', '-l', '64', '-b', '25M'] if udp else ['-Z', '--bidir'] if bidirectional else ['-Z', '-R'] if reverse else ['-Z']!r}
existing = json.loads(subprocess.check_output(['ip', '-j', 'route', 'show', 'exact', {WAN_IP + '/32'!r}], text=True))
assert not existing, existing
subprocess.run(['ip', 'route', 'add', {WAN_IP + '/32'!r}, 'via', {r.lan_gateway!r}, 'dev', {LAN_NIC!r}, 'mtu', '1500'], check=True)
try:
    result = subprocess.run(argv, capture_output=True, text=True, timeout=40)
finally:
    subprocess.run(['ip', 'route', 'del', {WAN_IP + '/32'!r}, 'dev', {LAN_NIC!r}], check=True)
print(json.dumps({{'link': link, 'argv': argv, 'rc': result.returncode,
                  'stdout': result.stdout, 'stderr': result.stderr,
                  'nic_before': stats_before,
                  'nic_after': subprocess.check_output(['ethtool', '-S', {LAN_NIC!r}], text=True)}}), flush=True)
assert result.returncode == 0
'''
            snmp_before = _udp_counters()
            nic = os.environ.get("ASK_WAN_INJECT_IF", r.wan_if)
            host_nic_before = _nic_counters(nic)
            r.record("nat-rate-dut-ports-before", await _port_counters(con))
            lan_task = asyncio.create_task(lan_run_python(r.lan, script, label="flowtable_nat_rate", timeout=55))
            # Measure the sender's established original direction. A reverse-only
            # iperf UDP socket never qualifies for this service's admission policy.
            deadline = asyncio.get_running_loop().time() + 8
            while True:
                before = await r.state()
                bulk = [f for f in before["flows"] if f["proto"] == number
                        and f"{WAN_IP}:{PORT}" in (f["src"], f["dst"])
                        and (bidirectional or f["in"] == (TARGET_WAN_IF if reverse else TARGET_LAN_IF))
                        and (int(f["packets"]) > 1000 if udp else
                             int(f["bytes"]) > max(1_000_000, 512 * int(f["packets"])))]
                if len(bulk) == streams * (2 if bidirectional else 1):
                    break
                assert not lan_task.done(), "iperf ended before hardware admission"
                assert asyncio.get_running_loop().time() < deadline, before
                await asyncio.sleep(0.25)
            healthy(before)
            keys = set()
            for row in bulk:
                assert row["mtu"] == "1500", row
                if row["in"] == TARGET_LAN_IF:
                    assert row["new_src"].rsplit(":", 1)[0] == external and row["new_dst"] == row["dst"], row
                    assert row["src"].rsplit(":", 1)[0] == r.lan_ip, row
                else:
                    assert (bidirectional or reverse) and row["in"] == TARGET_WAN_IF, row
                    assert row["new_dst"].rsplit(":", 1)[0] == r.lan_ip and row["new_src"] == row["src"], row
                    assert row["dst"].rsplit(":", 1)[0] == external, row
                keys.add((row["in"], number, row["src"], row["dst"]))
                if not udp:
                    opposite = TARGET_WAN_IF if row["in"] == TARGET_LAN_IF else TARGET_LAN_IF
                    keys.add((opposite, number, row["new_dst"], row["new_src"]))
            old = by_key(before)
            assert keys <= old.keys(), before
            if not udp:
                for row in bulk:
                    opposite = TARGET_WAN_IF if row["in"] == TARGET_LAN_IF else TARGET_LAN_IF
                    reply = old[(opposite, number, row["new_dst"], row["new_src"])]
                    assert (reply["new_src"], reply["new_dst"]) == (row["dst"], row["src"]), reply
            tx_before, cpu_before = await software_tx(r), await cpu(r)
            await asyncio.sleep(WINDOW_SECONDS)
            cpu_after, tx_after = await cpu(r), await software_tx(r)
            after = await r.state()
            healthy(after)
            new = by_key(after)
            deltas = []
            for key in keys:
                assert new[key]["cookie"] == old[key]["cookie"], (before, after)
                packets = int(new[key]["packets"]) - int(old[key]["packets"])
                size = int(new[key]["bytes"]) - int(old[key]["bytes"])
                assert packets > 1000, (key, packets)
                deltas.append({"key": key, "packets": packets, "bytes": size})
            tx = {dev: tx_after[dev] - tx_before[dev] for dev in tx_before}
            measurement = {"before": before, "after": after, "hardware": deltas, "software_tx": tx,
                           "cpu": cpu_delta(cpu_before, cpu_after)}
            r.record("nat-rate-hardware", measurement)
            assert all(0 <= count <= 512 for count in tx.values()), tx
            result = await lan_task
            r.record("nat-rate-client-console", {"rc": result.rc, "stdout": result.stdout})
            assert result.rc == 0, result.stdout
            client = json.loads(result.stdout.strip())
            client_json = json.loads(client["stdout"])
            stdout, stderr = await asyncio.wait_for(server.communicate(), 10)
            server_json = json.loads(stdout)
            r.record("nat-rate-iperf", {"client": client_json, "server": server_json,
                                        "stderr": stderr.decode(), "lan_link": client["link"],
                                        "lan_nic": {key: client[key] for key in ("nic_before", "nic_after")},
                                        "host_nic": {"before": host_nic_before, "after": _nic_counters(nic)},
                                        "host_udp_delta": {key: value - snmp_before[key]
                                                           for key, value in _udp_counters().items()}})
            assert client["rc"] == server.returncode == 0 and "error" not in client_json and "error" not in server_json
            if udp:
                received = client_json["end"]["sum_received"]
                assert received["bytes"] > 0 and received["seconds"] >= seconds - 0.1, received
                assert received["bits_per_second"] >= 25e6 * 0.95, received
                assert 0 <= received["lost_percent"] <= 1, received
                assert math.isfinite(received["jitter_ms"]) and received["jitter_ms"] >= 0, received
            else:
                received = (client_json if reverse else server_json)["end"]["sum_received"]
                assert received["bytes"] > 0 and received["seconds"] >= seconds - 0.1, received
                minimum = 8e9 if bidirectional else float(os.environ.get("ASK_FLOWTABLE_MIN_GBPS", "9")) * 1e9
                assert received["bits_per_second"] >= minimum, received
                if bidirectional:
                    # The forward data and the reverse ACKs share the WAN
                    # port, and both directions' tail drops land somewhere.
                    # Eleven runs measured 5.5-7.9 Gbit/s (mean 6.9), the
                    # low end with the hosts' senders, not the DUT, short
                    # of work; the unbounded queue this guards against
                    # (A313) gave 1.3-2.2.
                    reverse_result = client_json["end"]["sum_received_bidir_reverse"]
                    assert reverse_result["bits_per_second"] >= 5e9, reverse_result
        finally:
            try:
                if lan_task:
                    result = await lan_task
                    r.record("nat-rate-client-console", {"rc": result.rc, "stdout": result.stdout})
            finally:
                try:
                    if server and server.returncode is None:
                        server.terminate()
                        await asyncio.wait_for(server.communicate(), 5)
                finally:
                    try:
                        try:
                            r.record("nat-rate-dut-ports-after", await _port_counters(con))
                        finally:
                            await stop(con)
                    finally:
                        await command(r.target, r.session, "nft", "delete", "table", "ip", nat_table)
                        await console_command(con, "rm", "-f", CONFIG)
                        await command(r.target, r.session, "conntrack", "-D", "-p", protocol, "--orig-src", r.lan_ip,
                            "--orig-dst", WAN_IP, "--dport", str(PORT), check=False)
            final = await r.state()
            assert final["installs"] == final["deletes"] and final["fatal"] == final["quarantine"] == 0, final
            assert final["errors"] == HEALTH_BASELINE["errors"], (final, HEALTH_BASELINE)
            r.record("nat-rate-cleanup", final)
