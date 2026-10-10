"""Shared flowtable connections fixtures and scenarios."""

from __future__ import annotations

import asyncio
import base64
import gzip
import json
import secrets
import socket
import struct
from contextlib import asynccontextmanager
from pathlib import Path

import pytest
import pytest_asyncio
from _flowtable_rig import DPORT, HEALTH_BASELINE, TABLE, WAN_IP, command, read
from _flowtable_rig import SPORT as BASE_SPORT
from ask_orch.commands import console_python
from ask_orch.uart import Console
from _flowtable_tcp import cpu, cpu_delta, software_tx
from _topology import LAN_NIC, TARGET_LAN_IF, TARGET_WAN_IF, lan_run_python
from _flowtable_connections_peer import TCP_SIZE, UDP_SIZE, payload

SPORT = BASE_SPORT + 32
HEARTBEAT_INTERVAL = 10

FLOWS = [{"id": i, "proto": "tcp" if i & 1 else "udp", "sport": SPORT + i // 2}
         for i in range(32)]

ALL = list(range(len(FLOWS)))


def by_key(state):
    return {(f["in"], f["proto"], f["src"], f["dst"]): f for f in state["flows"]}


def keys(r, ids):
    result = set()
    for ident in ids:
        spec = FLOWS[ident]
        proto = "6" if spec["proto"] == "tcp" else "17"
        src, dst = f"{r.lan_ip}:{spec['sport']}", f"{WAN_IP}:{DPORT}"
        result.update(((TARGET_LAN_IF, proto, src, dst), (TARGET_WAN_IF, proto, dst, src)))
    return result




# The adapter's error count is cumulative for the boot and deliberately never
# reset, and the rearm test asserts that its barrier adds exactly two. Health
# therefore means "no new errors", not "none ever": against an absolute zero
# every test collected after that one fails on its predecessor's bookkeeping.
# The other three are current state rather than counters and stay absolute.
# A second offload table bound beside the rig's own adds its devices to
# `bindings`; a caller that holds one passes the total it expects.
def healthy(state, bindings=2):
    assert state["bindings"] == bindings and state["max_entries"] == 32768, state
    assert consistent(state), state
    assert state["invalidated"] == state["fatal"] == state["quarantine"] == 0, state
    assert state["errors"] == HEALTH_BASELINE["errors"], (state, HEALTH_BASELINE)




# /proc/cdx_flowtable is read a page at a time, and each page is a fresh
# look: the counters at its head and the rows after them can straddle a
# delete. A wait for rows to go also waits for the counters to agree.
def consistent(state):
    rows = state["flow_count"] if "snapshot" in state else len(state["flows"])
    return state["entries"] == state["neighbour_refs"] == state["handle_refs"] == rows


def unchanged(r, before, after, ids):
    healthy(after)
    old, new = by_key(before), by_key(after)
    for key in keys(r, ids):
        assert new[key]["cookie"] == old[key]["cookie"], (key, before, after)
        assert int(new[key]["packets"]) >= int(old[key]["packets"]), (key, before, after)
        assert int(new[key]["bytes"]) >= int(old[key]["bytes"]), (key, before, after)


async def delete_connection(r, ident):
    spec = FLOWS[ident]
    return await command(r.target, r.session, "conntrack", "-D", "-p", spec["proto"],
                         "--orig-src", r.lan_ip, "--orig-dst", WAN_IP,
                         "--sport", str(spec["sport"]), "--dport", str(DPORT), check=False)


async def clear_connections(r):
    """Clear the fixture's tuples in one UART call, preserving other traffic."""
    tuples = [(FLOWS[i]["proto"], str(FLOWS[i]["sport"])) for i in ALL]
    await console_python(Console.target(), f'''
import subprocess
for proto, sport in {tuples!r}:
    result = subprocess.run(['conntrack', '-D', '-p', proto,
                             '--orig-src', {r.lan_ip!r}, '--orig-dst', {WAN_IP!r},
                             '--sport', sport, '--dport', {str(DPORT)!r}],
                            capture_output=True, text=True, timeout=15)
    assert result.returncode in (0, 1), (proto, sport, result.stderr)
print('cleared')
''', timeout=60)


class Peer:
    def __init__(self, lan, path, flows, tcp_size=TCP_SIZE):
        self.lan, self.path = lan, path
        self.tcp_size = tcp_size
        self.flows = {f["id"]: f for f in flows}
        self.lock = asyncio.Lock()

    async def rpc(self, op, ids=None, **kwargs):
        body = json.dumps({"op": op, "ids": ids or [], **kwargs})
        script = f"""
import socket
with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as control:
    control.settimeout(25)
    control.connect({self.path!r})
    control.sendall({(body + chr(10)).encode()!r})
    with control.makefile('rb') as stream:
        line = stream.readline(8 << 20)
    assert line.endswith(b'\\n'), 'peer response missing or oversized'
    print(line.decode(), end='')
"""
        async with self.lock:
            result = await lan_run_python(self.lan, script, timeout=30, label="peer_rpc")
        assert result.rc == 0, result.stdout
        response = json.loads(result.stdout)
        assert response["op"] == op, response
        return response["result"]

    async def batch(self, ids, count=32, interval=0.05):
        # Default warmup spans the kernel's one-second admission retry. A
        # deferred offer needs another packet; polling an idle flow cannot
        # admit it. Measurement windows pass their own interval explicitly.
        await self.rpc("start", ids, count=count, interval=interval)
        result = {int(k): v for k, v in (await self.rpc("wait", ids)).items()}
        assert set(result) == set(ids), result
        for ident, report in result.items():
            assert report["count"] == count, report
            size = self.tcp_size if self.flows[ident]["proto"] == "tcp" else UDP_SIZE
            assert report["bytes"] == count * size, report
        return result

    async def keep_alive(self):
        # DUT observations can outlast the peer's idle lease. QGA status calls
        # keep it alive without sending control traffic through the DUT.
        while True:
            await asyncio.sleep(HEARTBEAT_INTERVAL)
            status = await self.rpc("status", compact=True)
            assert not status["errors"], status


def peer_script(config):
    parts = ("_flowtable_neighbour_peer.py", "_flowtable_multicast_peer.py",
             "_flowtable_udp_wire.py", "_flowtable_echo_peer.py", "_flowtable_connections_peer.py")
    return f"CONFIG={config!r}\n" + "\n".join(Path(__file__).with_name(name).read_text() for name in parts)


@asynccontextmanager
async def peer(r, flows=FLOWS, *, initial_ids=None, servers=(), lease=180, tcp_size=TCP_SIZE,
               reconnect=False, listen_addresses=()):
    specs = {f["id"]: f for f in flows}
    tasks, writers, errors, tcp_counts = set(), set(), [], {}
    active_ids = set()
    listeners = []
    controller = None
    heartbeat = None
    path = "/tmp/ask-peer-" + secrets.token_hex(8)
    started = False
    # What the DUT's transforms had dropped when the peer started, so a lost
    # datagram's record says what dropped meanwhile, not since boot.
    try:
        xfrm_before = await read(r.target, r.session, "/proc/net/xfrm_stat")
    except Exception as error:
        xfrm_before = repr(error)

    async def echo(reader, writer):
        writers.add(writer)
        ident = None
        owns_id = False
        try:
            hello = json.loads(await asyncio.wait_for(reader.readline(), 10))
            ident = hello["id"]
            assert ident in specs and specs[ident]["proto"] == "tcp"
            assert writer.get_extra_info("peername")[:2] == tuple(specs[ident].get("remote", (specs[ident].get("lan", r.lan_ip), specs[ident]["sport"])))
            assert ident not in active_ids, (ident, "overlapping TCP generations")
            assert reconnect or ident not in tcp_counts
            assert hello["serial"] == tcp_counts.get(ident, 0), (ident, hello, tcp_counts.get(ident))
            tcp_counts.setdefault(ident, 0)
            active_ids.add(ident)
            owns_id = True
            while True:
                try:
                    data = await reader.readexactly(tcp_size)
                except asyncio.IncompleteReadError as error:
                    assert not error.partial, (ident, "partial TCP record", len(error.partial))
                    break
                assert data == payload(ident, tcp_counts[ident], tcp_size), (ident, tcp_counts[ident])
                tcp_counts[ident] += 1
                writer.write(data)
                await writer.drain()
        except ConnectionError as error:
            if ident not in specs or not specs[ident].get("abort"):
                errors.append((ident, repr(error)))
        except Exception as error:
            errors.append((ident, repr(error)))
        finally:
            writer.close()
            try:
                await asyncio.wait_for(writer.wait_closed(), 5)
            except (ConnectionError, TimeoutError):
                writer.transport.abort()
                if ident not in specs or not specs[ident].get("abort"):
                    errors.append((ident, "unexpected reset on close"))
            writers.discard(writer)
            if owns_id:
                active_ids.remove(ident)

    def accept(reader, writer):
        job = asyncio.create_task(echo(reader, writer))
        tasks.add(job)
        job.add_done_callback(tasks.discard)

    try:
        for address in dict.fromkeys((WAN_IP, *listen_addresses)):
            listeners.append(await asyncio.start_server(accept, address, DPORT))
        config = {"lan": r.lan_ip, "wan": WAN_IP, "dport": DPORT,
                  "control_path": path + ".sock", "servers": list(servers), "flows": flows,
                  "lease": lease, "tcp_size": tcp_size}
        script = peer_script(config)
        encoded = base64.b64encode(gzip.compress(script.encode())).decode()
        started = True  # Cleanup owns the staging paths before the first mutation.
        launched = await lan_run_python(r.lan, f"""
import base64, gzip, pathlib, subprocess, sys, time
path = {path!r}
pathlib.Path(path + '.py').write_bytes(gzip.decompress(base64.b64decode({encoded!r})))
with open(path + '.log', 'w') as log:
    child = subprocess.Popen([sys.executable, path + '.py'], stdin=subprocess.DEVNULL,
                             stdout=log, stderr=log, start_new_session=True)
pathlib.Path(path + '.pid').write_text(str(child.pid))
for _ in range(100):
    if pathlib.Path(path + '.sock').exists():
        print('READY')
        break
    assert child.poll() is None, pathlib.Path(path + '.log').read_text()
    time.sleep(0.1)
else:
    raise TimeoutError('LAN peer did not start')
""", timeout=15, label="peer_start")
        assert launched.rc == 0 and "READY" in launched.stdout.splitlines(), launched.stdout
        controller = Peer(r.lan, path + ".sock", flows, tcp_size)
        def tcp_info():
            snapshots = []
            for stream in writers:
                sock = stream.get_extra_info("socket")
                if sock is not None:
                    snapshots.append({"peer": stream.get_extra_info("peername"),
                        "tcp_info_hex": sock.getsockopt(socket.IPPROTO_TCP, socket.TCP_INFO, 104).hex()})
            return snapshots
        controller.wan_tcp_info = tcp_info
        await controller.rpc("open", list(specs) if initial_ids is None else initial_ids)
        heartbeat = asyncio.create_task(controller.keep_alive())
        yield controller
    finally:
        shutdown_error = None
        if heartbeat:
            heartbeat.cancel()
            try:
                await heartbeat
            except asyncio.CancelledError:
                pass
            except Exception as error:
                shutdown_error = error
        try:
            if controller:
                await controller.rpc("shutdown")
        except Exception as error:
            shutdown_error = error
        finally:
            for listener in listeners:
                listener.close()
            for writer in list(writers):
                # NAT lifecycle tests can destroy a mapping before its peer
                # closes. Cleanup must not wait for an unreachable TCP tuple.
                writer.transport.abort()
            try:
                await asyncio.wait_for(asyncio.gather(*list(tasks), return_exceptions=True), 10)
                # Python 3.13 also waits for accepted clients, aborted above.
                for listener in listeners:
                    await asyncio.wait_for(listener.wait_closed(), 10)
            except (Exception, pytest.fail.Exception) as error:
                shutdown_error = shutdown_error or error
            # Always stop this exact process, even if its last RPC failed.
            if started:
                result = await lan_run_python(r.lan, f"""
import os, pathlib, signal, time
path = {path!r}
pidfile = pathlib.Path(path + '.pid')
if pidfile.exists():
    pid = int(pidfile.read_text())
    for n in range(30):
        try:
            args = pathlib.Path('/proc/%d/cmdline' % pid).read_bytes().split(b'\\0')
            if (path + '.py').encode() not in args:
                break
            if n in (10, 20):
                os.killpg(pid, signal.SIGTERM if n == 10 else signal.SIGKILL)
        except (FileNotFoundError, ProcessLookupError):
            break
        time.sleep(0.1)
log = pathlib.Path(path + '.log')
print(log.read_text() if log.exists() else 'peer did not create its log', end='')
for suffix in ('.py', '.pid', '.log', '.sock'):
    pathlib.Path(path + suffix).unlink(missing_ok=True)
""", timeout=10, label="peer_finish")
                r.record("connections-peer", {"rc": result.rc, "stdout": result.stdout,
                                             "server_errors": errors, "tcp_records": tcp_counts})
                if shutdown_error is None:
                    report = json.loads(result.stdout.strip().splitlines()[-1])
                    assert report["closed"], report
                if result.rc != 0 or errors or shutdown_error:
                    # Which way a lost datagram died: the WAN echo's own record
                    # of what reached it, the newest serials of each flow. A
                    # serial the LAN never got back but the WAN saw was lost on
                    # the way back.
                    seen = {}
                    for data in getattr(getattr(r, "echo", None), "received", {}):
                        if len(data) >= 12:
                            ident, serial = struct.unpack("!IQ", data[:12])
                            seen.setdefault(ident, []).append(serial)
                    dut = {"/proc/net/xfrm_stat at start": xfrm_before}
                    # A frame corrupted on either cable is counted only by the
                    # receiving end: the DUT's ports for what arrives there.
                    for dev in (TARGET_LAN_IF, TARGET_WAN_IF):
                        for counter in ("rx_crc_errors", "rx_errors", "rx_missed_errors"):
                            path = f"/sys/class/net/{dev}/statistics/{counter}"
                            try:
                                dut[path] = (await read(r.target, r.session, path)).strip()
                            except Exception as error:
                                dut[path] = repr(error)
                    for path in ("/proc/net/xfrm_stat", "/proc/cdx_flowtable"):
                        try:
                            dut[path] = await read(r.target, r.session, path)
                        except Exception as error:
                            dut[path] = repr(error)
                    # A frame corrupted on the LAN medium is counted only by
                    # the endpoint that received it
                    # (docs/flowtable/udp-loss-investigation.md).
                    try:
                        lan_nic = (await lan_run_python(r.lan, f"""
import subprocess
stats = subprocess.run(['ethtool', '-S', {LAN_NIC!r}], capture_output=True, text=True).stdout
print(''.join(line + '\\n' for line in stats.splitlines()
              if any(word in line for word in ('err', 'drop', 'crc', 'miss'))), end='')
""", timeout=10, label="peer_nic_errors")).stdout
                    except Exception as error:
                        lan_nic = repr(error)
                    r.record("connections-wan-received",
                             {"wan": {ident: sorted(serials)[-32:] for ident, serials in seen.items()},
                              "dut": dut, "lan_nic_errors": lan_nic})
                assert result.rc == 0 and not errors, (result.stdout, errors)
        if shutdown_error:
            raise shutdown_error


@pytest_asyncio.fixture
async def connections(rig, request):
    r = rig
    cleanup = []
    try:
        initial = await r.state()
        assert not initial["observe"] and initial["max_entries"] == 32768, initial
        for proto in ("udp", "tcp"):
            nat = ["POSTROUTING", "-s", r.lan_ip, "-d", WAN_IP, "-p", proto,
                   "--sport", f"{SPORT}:{SPORT + 15}", "--dport", str(DPORT), "-j", "ACCEPT"]
            await command(r.target, r.session, "iptables", "-t", "nat", "-I", *nat)
            cleanup.append(["iptables", "-t", "nat", "-D", *nat])
            # Only expiry tests shorten the production idle timeout. Other
            # consumers may spend several seconds observing an idle flow.
            if hasattr(request, "param"):
                name = f"net.netfilter.nf_flowtable_{proto}_timeout"
                value = (await read(r.target, r.session, "/proc/sys/" + name.replace(".", "/"))).strip()
                await command(r.target, r.session, "sysctl", "-w", f"{name}={request.param}")
                cleanup.append(["sysctl", "-w", f"{name}={value}"])
        await clear_connections(r)
        await r.nft(f'''table inet {TABLE} {{
 flowtable fast {{ hook ingress priority 0; devices = {{ {TARGET_LAN_IF}, {TARGET_WAN_IF} }}; flags offload; }}
 chain forward {{ type filter hook forward priority 0; policy accept;
 ip saddr {r.lan_ip} ip daddr {WAN_IP} udp sport {SPORT}-{SPORT + 15} udp dport {DPORT} flow add @fast
 ip saddr {r.lan_ip} ip daddr {WAN_IP} tcp sport {SPORT}-{SPORT + 15} tcp dport {DPORT} flow add @fast
 }}
}}''')
        await r.wait(lambda s: s["bindings"] == 2)
        yield r
    finally:
        failures = []
        try:
            final = await r.delete_table()
            assert final["entries"] == final["neighbour_refs"] == final["quarantine"] == 0, final
            assert final["installs"] == final["deletes"], final
            assert final["errors"] == HEALTH_BASELINE["errors"], (final, HEALTH_BASELINE)
            r.record("connections-cleanup", final)
        except Exception as error:
            failures.append(str(error))
        try:
            await clear_connections(r)
        except Exception as error:
            failures.append(str(error))
        for argv in reversed(cleanup):
            try:
                await command(r.target, r.session, *argv)
            except Exception as error:
                failures.append(str(error))
        assert not failures, failures


async def hardware_batch(r, p, ids, label):
    before = await r.state()
    healthy(before)
    tx_before, cpu_before = await software_tx(r), await cpu(r)
    reports = await p.batch(ids, count=256, interval=0.03125)
    cpu_after, tx_after = await cpu(r), await software_tx(r)
    after = await r.state()
    unchanged(r, before, after, ids)
    assert by_key(after).keys() == keys(r, ids), after
    assert after["installs"] == before["installs"] and after["deletes"] == before["deletes"], (before, after)
    old, new = by_key(before), by_key(after)
    deltas = []
    for ident in ids:
        for key in keys(r, [ident]):
            packets = int(new[key]["packets"]) - int(old[key]["packets"])
            byte_count = int(new[key]["bytes"]) - int(old[key]["bytes"])
            if FLOWS[ident]["proto"] == "udp":
                assert packets == 256 and byte_count == 256 * (UDP_SIZE + 42), (key, packets, byte_count)
            else:
                assert packets >= reports[ident]["bytes"] // 1500, (key, packets)
            deltas.append({"key": key, "packets": packets, "bytes": byte_count})
    tx = {dev: tx_after[dev] - tx_before[dev] for dev in tx_before}
    assert 0 <= tx[TARGET_LAN_IF] <= 64 and 0 <= tx[TARGET_WAN_IF] <= 512, tx
    r.record(label, {"before": before, "after": after, "transfers": reports,
                     "hardware": deltas, "software_tx": tx, "cpu": cpu_delta(cpu_before, cpu_after),
                     "cpu_ticks_before": cpu_before, "cpu_ticks_after": cpu_after})
    return after
