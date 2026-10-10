"""Shared flowtable offload fixtures and scenarios."""

from __future__ import annotations

import asyncio
import json
import os
import re
import shlex
import struct
import time
from collections import Counter
from contextlib import asynccontextmanager

import pytest
import pytest_asyncio
from _mcast_helpers import MULTICAST_SWITCH
from _topology import LAN_NIC, TARGET_LAN_IF, TARGET_WAN_IF, lan_run_python
from ask_orch.artifacts import artifact_dir
from ask_orch.client import Agent
from ask_orch.commands import (
    CONSOLE_NOISE,
    command,
    console_command,
    console_json,
    console_python,
    flowtable_json,
    read,
)
from ask_orch.counters import kernel_tx_packets
from ask_orch.lifecycle import CleanupStack
from ask_orch.uart import Console

WAN_IP = os.environ.get("ASK_WAN_IPERF_IP", "")

SPORT = int(os.environ.get("ASK_FLOWTABLE_SPORT", "48270"))

DPORT = int(os.environ.get("ASK_FLOWTABLE_DPORT", "48271"))

TABLE = "ask_poc"

HEALTH_BASELINE = {"errors": 0}


async def ct_bytes(r):
    """Bytes conntrack has accounted to the original direction of the flow.

    The first bytes= in an extended listing is the original direction; the
    second is the reply. Both the software fast path and a hardware delta land
    here, which is what makes the two agreeing on units observable at all."""
    listing = await command(r.target, r.session, "conntrack", "-L", "-p", r.proto,
                            "--orig-src", r.lan_ip, "--orig-dst", WAN_IP,
                            "--sport", str(SPORT), "--dport", str(DPORT), "-o", "extended")
    counts = re.findall(r"bytes=(\d+)", listing["stdout"])
    assert counts, listing
    return int(counts[0])


async def upper_roundtrip(r, dev):
    """Exercise unsupported upper-device topology, restoring it through UART."""
    temporary = "askftupper"
    with Console.target(log_path=str(artifact_dir() / "upper-uart.log")) as con:
        await asyncio.to_thread(con.login, "root", None)
        links = json.loads((await console_command(con, "ip", "-j", "link", "show"))["stdout"])
        assert temporary not in {i["ifname"] for i in links}, links
        assert "master" not in next(i for i in links if i["ifname"] == dev), links
        await console_command(con, "ip", "link", "add", "name", temporary, "type", "bridge")
        try:
            return await console_command(con, "ip", "link", "set", "dev", dev, "master", temporary)
        finally:
            try:
                await console_command(con, "ip", "link", "set", "dev", dev, "nomaster")
            finally:
                await console_command(con, "ip", "link", "del", "dev", temporary)


STATUS_ROWS = {"flow": "flows", "session": "sessions", "vlan": "vlans",
               "tunnel": "tunnels", "mcast": "mcast", "mroute": "mroute"}


def status_text(text):
    state = {name: [] for name in STATUS_ROWS.values()}
    for line in text.splitlines():
        kind, _, rest = line.partition(" ")
        if kind in STATUS_ROWS:
            state[STATUS_ROWS[kind]].append(
                dict(item.split("=", 1) for item in rest.split()))
        else:
            parts = line.split()
            assert len(parts) == 2, f"unexpected /proc/cdx_flowtable line: {line!r}"
            key, value = parts
            state[key] = int(value) if value.isdecimal() else value
    return state


def assert_undisturbed(r, before, after, same=True, label="flow-disturbed"):
    """Nothing was readmitted between two adapter states, and no offer lost
    RTNL.

    A direction the adapter refused re-offers its flow about once a second
    while it carries traffic. An MTU refusal is decided before RTNL and an
    installed direction's offer is answered without it, so such a window
    should never take RTNL at all; busy moving names an offer that did. That
    is checked with the caller's own verdict on the rows -- `same`, typically
    that the cookies did not move -- and a failure is reported with every
    counter that could name its cause."""
    if after["busy"] == before["busy"] and same:
        return
    r.record(label, {"before": before, "after": after})
    pytest.fail("the measured flow was disturbed mid-measurement: " +
                " ".join(f"{k}={before.get(k)}->{after[k]}" for k in sorted(after)
                         if isinstance(after[k], int) and (
                             k.endswith("invalidations") or
                             k in ("invalidated", "invalidation_done", "rearms", "errors",
                                   "rejects", "busy", "installs", "deletes"))) +
                f"\nbefore={before['flows']}\nafter={after['flows']}")


class Echo(asyncio.DatagramProtocol):
    def __init__(self):
        self.received = Counter()
        self.record_payloads = True
        # A test that needs one direction alone clears this to receive
        # without answering.
        self.reply = True
        self.packets = 0

    def connection_made(self, transport):
        self.transport = transport

    def datagram_received(self, data, addr):
        self.packets += 1
        if self.record_payloads:
            self.received[data] += 1
        if self.reply:
            self.transport.sendto(data, addr)


class Rig:
    proto = "udp"

    async def state(self):
        if getattr(self, "bulk", False):
            summary = await self.target.observe(self.session, "summary")
            if not summary["entries"]:
                return summary
            return await self.target.observe(self.session, "snapshot")
        return await self.target.observe(self.session, "state")

    async def wait(self, predicate, timeout=10):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            state = await self.state()
            if predicate(state):
                return state
            await asyncio.sleep(0.1)
        pytest.fail(f"flowtable state did not converge: {state}")

    async def nft(self, text):
        return await command(self.target, self.session, "nft", text)

    def ruleset(self, hardware=True, counter=False, mark=None):
        # A mark is set in the same rule that offers the flow, so admission
        # sees it: it is how a test asks for a hardware decline that owes
        # nothing to fault injection.
        marking = f"ct mark set {mark:#x}" if mark else ""
        return f'''table inet {TABLE} {{
 flowtable fast {{ hook ingress priority 0; devices = {{ {TARGET_LAN_IF}, {TARGET_WAN_IF} }};
 {"flags offload;" if hardware else ""} {"counter;" if counter else ""} }}
 chain forward {{ type filter hook forward priority 0; policy accept;
 ip saddr {self.lan_ip} ip daddr {WAN_IP} {self.proto} sport {SPORT} {self.proto} dport {DPORT} {marking} flow add @fast
 }}
}}'''

    async def table(self, hardware=True, counter=False, mark=None):
        await self.nft(self.ruleset(hardware, counter, mark))
        if hardware:
            await self.wait(lambda s: s["bindings"] == 2)

    async def delete_table(self, timeout=10):
        # Unbinding retires every hardware entry one delete at a time before
        # nft returns, so a caller holding a full table passes a longer bound.
        if getattr(self, "bulk", False):
            return await self.target.observe(self.session, "delete_table", table=TABLE, timeout=max(timeout, 60))
        await command(self.target, self.session, "nft", "delete", "table", "inet", TABLE,
                      check=False, timeout_ms=max(15000, timeout * 1000))
        return await self.wait(lambda s: not s["bindings"] and not s["entries"], timeout=timeout)

    async def clear_ct(self):
        await command(self.target, self.session, "conntrack", "-D", "-p", self.proto,
                      "--orig-src", self.lan_ip, "--orig-dst", WAN_IP,
                      "--sport", str(SPORT), "--dport", str(DPORT), check=False)

    async def run_peer(self, script, **kwargs):
        netns = getattr(self, "peer_netns", None)
        if netns:
            script = ("import os\n" +
                      f"with open('/var/run/netns/' + {netns!r}, 'rb') as ns:\n" +
                      "    os.setns(ns.fileno(), os.CLONE_NEWNET)\n" + script)
        return await lan_run_python(self.lan, script, **kwargs)

    async def exchange(self, count=64, interval=0.003, payload_size=256, promiscuous=True,
                       sport=SPORT, ignore_pmtu=False):
        assert payload_size >= 8
        peer_if = getattr(self, "peer_if", LAN_NIC)
        # Driver-level counters belong to the physical NIC. A VLAN device has
        # no ethtool statistics at all, so a tagged peer has to name the port
        # underneath it for the link health check while still capturing frames
        # on the device that terminates the tag.
        link_if = getattr(self, "peer_link", peer_if)
        peer_mac = getattr(self, "peer_mac", self.lan_mac)
        gateway_mac = getattr(self, "peer_gateway_mac", self.dut_lan_mac)
        ttl = 64 - getattr(self, "forward_hops", 1)
        first = self.sequence
        self.sequence += count
        script = f'''
import json, socket, struct, subprocess, time
def link_stats():
    text = subprocess.check_output(['ethtool', '-S', {link_if!r}], text=True)
    return {{k.strip(): int(v.strip()) for line in text.splitlines() if ':' in line
            for k, v in [line.split(':', 1)] if v.strip().isdigit() and
            any(word in k for word in ('error', 'dropped', 'no_buffer', 'no_dma', 'timeout'))}}
link_before = link_stats()
s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
s.setsockopt(socket.SOL_IP, socket.IP_TTL, 64)
s.setsockopt(socket.SOL_IP, getattr(socket, "IP_RECVTTL", 12), 1)
if {ignore_pmtu!r}:
    s.setsockopt(socket.SOL_IP, getattr(socket, "IP_MTU_DISCOVER", 10), 3)  # IP_PMTUDISC_PROBE
s.settimeout(2)
s.bind(({self.lan_ip!r}, {sport}))
raw = socket.socket(socket.AF_PACKET, socket.SOCK_RAW, socket.htons(3))
raw.bind(({peer_if!r}, 0)); raw.settimeout(2)
if {promiscuous!r}:
    raw.setsockopt(263, 1, struct.pack('IHH8s', socket.if_nametoindex({peer_if!r}), 1, 0, b''))
received = []
for n in range({first}, {first + count}):
    payload = struct.pack('!Q', n) + b'ASK-flowtable'.ljust({payload_size - 8}, b'.')[:{payload_size - 8}]
    s.sendto(payload, ({WAN_IP!r}, {DPORT}))
    try:
        data, ancillary, flags, addr = s.recvmsg(2048, 128)
    except TimeoutError as error:
        raw.settimeout(0.05)
        frames = []
        try:
            while len(frames) < 32:
                frame = raw.recv(65535)
                frames.append(frame.hex())
        except TimeoutError:
            pass
        print(json.dumps({{'timeout_sequence': n, 'frames': frames,
                          'link_before': link_before, 'link_after': link_stats()}}), flush=True)
        raise AssertionError(('echo timeout', n)) from error
    assert data == payload and addr == ({WAN_IP!r}, {DPORT}), (n, data, addr)
    ttl = [struct.unpack('i', v)[0] for level, kind, v in ancillary if level == socket.SOL_IP and kind == socket.IP_TTL]
    assert ttl == [{ttl}], (n, ttl)
    while True:
        frame = raw.recv(65535)
        if len(frame) < 42 or frame[23] != socket.IPPROTO_UDP or frame[26:30] != socket.inet_aton({WAN_IP!r}):
            continue
        ihl = (frame[14] & 15) * 4
        start = 14 + ihl
        if struct.unpack('!HH', frame[start:start+4]) != ({DPORT}, {sport}):
            continue
        assert frame[start+8:start+8+len(payload)] == payload, (n, 'duplicate or unexpected frame', frame.hex())
        assert frame[:6] == bytes.fromhex({peer_mac.replace(':', '')!r})
        assert frame[6:12] == bytes.fromhex({gateway_mac.replace(':', '')!r})
        assert ihl == 20 and frame[22] == {ttl}
        break
    received.append(n)
    time.sleep({interval})
s.close()
raw.close()
print(json.dumps({{'first': received[0], 'last': received[-1], 'count': len(received)}}))
'''
        result = await self.run_peer(script, timeout=max(15, count * interval + 10), label="flowtable_echo")
        if result.rc:
            state = await self.state()
            received = [struct.unpack("!Q", p[:8])[0] for p in self.echo.received if len(p) >= 8]
            ct = await command(self.target, self.session, "conntrack", "-L", "-p", "udp", "-o", "extended")
            self.record("failed-exchange", {"state": state, "first": first, "count": count,
                                           "received": received, "stdout": result.stdout, "conntrack": ct})
            pytest.fail(f"{result.stdout}\nstate={state}\nlast received={received[-8:]}")
        report = json.loads(result.stdout.strip())
        assert report == {"first": first, "last": first + count - 1, "count": count}
        for n in range(first, first + count):
            data = struct.pack("!Q", n) + b"ASK-flowtable".ljust(payload_size - 8, b".")[:payload_size - 8]
            assert self.echo.received[data] == 1, (n, self.echo.received[data])
        return report

    def record(self, name, data):
        artifact_dir().mkdir(parents=True, exist_ok=True)
        (artifact_dir() / f"{name}.json").write_text(json.dumps(data, indent=2) + "\n")


async def stop_boot_daemon():
    """The offload service is default-on: a normal boot has already installed
    the catch-all policy, so the backend starts bound. Controlled tests own the
    policy themselves, so they stop the boot daemon first -- the init script
    kills it and removes its table, draining the hardware to an unbound state.
    Every rig does this itself rather than rely on an earlier test having done
    it, so a test run on its own after a boot sees the same start.

    The stop switches multicast acceleration off with the rest. A controlled
    test owns that switch as well, and starts from it on, as a boot leaves it."""
    with Console.target(log_path=str(artifact_dir() / "boot-daemon-stop.log")) as con:
        await asyncio.to_thread(con.login, "root", None)
        await console_command(con, "/etc/init.d/ask-flowtable", "stop", check=False, timeout=45)
        await console_command(con, "sh", "-c", f"echo Y > {MULTICAST_SWITCH}")


async def restore_restart_limit(r) -> list:
    """Put back the restart limit a case left changed: over the recovery
    console when there is one, otherwise over the agent. Only once CDX has no
    restart pending -- it checks the limit on every attempt, and the boot's
    own could find its budget spent and stop the ports for good -- and not at
    all once CDX has been unloaded with it. Returns what failed."""
    path = "/sys/module/cdx/parameters/flowtable_restart_limit"
    if r.recovery_console:
        from _flowtable_restart import latch_cleared, write
        con = r.recovery_console
        if (await console_command(con, "test", "-e", path, check=False))["rc"]:
            r.restart_limit = None
            return []
        if not await latch_cleared(con):
            return [("CDX's latch is still set; its restart limit is left at", r.restart_limit)]
        await write(con, path, r.restart_limit)
    else:
        if (await r.target.fs_read(r.session, path))["errno"]:
            r.restart_limit = None
            return []
        deadline = time.monotonic() + 10
        while True:
            try:
                state = await r.state()
            except Exception:
                state = None
            if state is not None and not state["fatal"]:
                break
            if time.monotonic() >= deadline:
                return [("CDX's latch is still set; its restart limit is left at", r.restart_limit, state)]
            await asyncio.sleep(0.25)
        result = await r.target.fs_write(r.session, path, r.restart_limit)
        if result["errno"]:
            return [result]
    r.restart_limit = None
    return []


@pytest_asyncio.fixture
async def rig(target_agent, aiohttp_session, lan, splat_window, request):
    r = Rig()
    r.proto = getattr(request, "param", "udp")
    assert r.proto in {"udp", "tcp"}
    r.target, r.session, r.lan, r.sequence = target_agent, aiohttp_session, lan, 1
    r.recovery_console = None
    # CDX's restart limit as a case found it, while the case has it raised
    # (_flowtable_restart.restart_budget()) or lowered.
    r.restart_limit = None
    await stop_boot_daemon()
    initial = await r.state()
    assert initial["entries"] == initial["bindings"] == initial["invalidated"] == 0, initial
    # The adapter's error count is cumulative for the boot and deliberately
    # never reset, so whatever earlier tests already accounted for is this
    # test's floor. Only errors raised from here are its own.
    HEALTH_BASELINE["errors"] = initial["errors"]
    wan = Agent("wan", f"http://{os.environ.get('ASK_WAN_IP', '127.0.0.1')}:9110")
    cleanup = []
    transport = None
    try:
        await command(r.target, r.session, "modprobe", "xt_tcpudp")
        # Hardware flows age on these, so a run cut short inside a fixture
        # that shortens them -- the lifetime test sets 5 s -- would have every
        # later test lose its flows on the first pause in traffic, and read
        # as flows expiring under load. Tests that need other values set and
        # restore them inside their own scope.
        for proto in ("tcp", "udp"):
            path = f"/proc/sys/net/netfilter/nf_flowtable_{proto}_timeout"
            value = (await read(r.target, r.session, path)).strip()
            assert value == "30", (f"fixture requires the default flowtable {proto} timeout; "
                                   f"a stopped run left {value}", path)
        def lan_json(cmd):
            result = lan.run(cmd, timeout=10)
            assert result.rc == 0, result.stdout
            return json.loads(result.stdout.strip())
        r.lan_ip = next(a["local"] for a in lan_json(f"ip -j -4 addr show dev {shlex.quote(LAN_NIC)}")[0]["addr_info"]
                        if a["family"] == "inet")
        lan_mac = lan_json(f"ip -j link show dev {shlex.quote(LAN_NIC)}")[0]["address"]
        r.lan_mac = lan_mac
        r.dut_lan_mac = (await read(r.target, r.session, f"/sys/class/net/{TARGET_LAN_IF}/address")).strip()
        r.dut_wan_mac = (await read(r.target, r.session, f"/sys/class/net/{TARGET_WAN_IF}/address")).strip()
        r.lan_gateway = lan_json(f"ip -j route get {shlex.quote(WAN_IP)}")[0]["gateway"]
        # Select the interface owning the endpoint address (route get would
        # report lo for an address belonging to the local WAN host).
        addresses = json.loads((await command(wan, r.session, "ip", "-j", "-4", "addr"))["stdout"])
        r.wan_if = next(i["ifname"] for i in addresses if any(a.get("local") == WAN_IP for a in i["addr_info"]))
        wan_mac = json.loads((await command(wan, r.session, "ip", "-j", "link", "show", "dev", r.wan_if))["stdout"])[0]["address"]
        r.wan_mac = wan_mac
        dut = json.loads((await command(r.target, r.session, "ip", "-j", "-4", "addr", "show", "dev", TARGET_WAN_IF))["stdout"])[0]
        dut_ip = next(a["local"] for a in dut["addr_info"] if a["family"] == "inet")
        existing = json.loads((await command(wan, r.session, "ip", "-j", "route", "show", "exact", f"{r.lan_ip}/32"))["stdout"])
        if existing:
            assert existing[0].get("gateway") == dut_ip and existing[0]["dev"] == r.wan_if, existing
        else:
            await command(wan, r.session, "ip", "route", "add", f"{r.lan_ip}/32", "via", dut_ip, "dev", r.wan_if)
            cleanup.append((wan, ["ip", "route", "del", f"{r.lan_ip}/32", "via", dut_ip, "dev", r.wan_if]))
        # Equal-MTU Ethernet: both ports share one MTU of at least a standard
        # frame, and the host routes below carry none, so every direction's
        # path MTU is its egress port's. The adapter installs a non-TCP IPv4
        # direction only when its path carries the largest packet its ingress
        # can deliver -- a full Ethernet frame, or the port's MTU where that is
        # larger -- so this is what admits the rig's UDP directions at all. A
        # test that needs a smaller path, or a jumbo one, builds it for its
        # own duration.
        mtus = {dev: int((await read(r.target, r.session, f"/sys/class/net/{dev}/mtu")).strip())
                for dev in (TARGET_LAN_IF, TARGET_WAN_IF)}
        assert len(set(mtus.values())) == 1 and mtus[TARGET_LAN_IF] >= 1500, \
            ("the rig needs both ports at one MTU of at least 1500", mtus)
        r.port_mtu = mtus[TARGET_LAN_IF]
        for ip, mac, dev in [(r.lan_ip, lan_mac, TARGET_LAN_IF), (WAN_IP, wan_mac, TARGET_WAN_IF)]:
            old = json.loads((await command(r.target, r.session, "ip", "-j", "neigh", "show", "to", ip, "dev", dev))["stdout"])
            restore = ["ip", "neigh", "del", ip, "dev", dev]
            if old and old[0].get("lladdr"):
                state = "permanent" if "PERMANENT" in old[0]["state"] else "stale"
                restore = ["ip", "neigh", "replace", ip, "lladdr", old[0]["lladdr"], "nud", state, "dev", dev]
            await command(r.target, r.session, "ip", "neigh", "replace", ip, "lladdr", mac, "nud", "permanent", "dev", dev)
            cleanup.append((r.target, restore))
            routes = json.loads((await command(r.target, r.session, "ip", "-j", "route", "show", "exact", f"{ip}/32"))["stdout"])
            assert not routes, ("fixture requires unused host routes", routes)
            await command(r.target, r.session, "ip", "route", "add", f"{ip}/32", "dev", dev)
            cleanup.append((r.target, ["ip", "route", "del", f"{ip}/32", "dev", dev]))
        nat = ["POSTROUTING", "-s", r.lan_ip, "-d", WAN_IP, "-p", r.proto, "--sport", str(SPORT), "--dport", str(DPORT), "-j", "ACCEPT"]
        await command(r.target, r.session, "iptables", "-t", "nat", "-I", *nat)
        cleanup.append((r.target, ["iptables", "-t", "nat", "-D", *nat]))
        await r.clear_ct()
        old_acct = (await read(r.target, r.session, "/proc/sys/net/netfilter/nf_conntrack_acct")).strip()
        await command(r.target, r.session, "sysctl", "-w", "net.netfilter.nf_conntrack_acct=1")
        cleanup.append((r.target, ["sysctl", "-w", f"net.netfilter.nf_conntrack_acct={old_acct}"]))
        if r.proto == "udp":
            transport, r.echo = await asyncio.get_running_loop().create_datagram_endpoint(Echo, local_addr=(WAN_IP, DPORT))
        r.record("fixture", {"lan": r.lan_ip, "wan": WAN_IP, "sport": SPORT, "dport": DPORT,
                             "lan_mac": lan_mac, "wan_mac": wan_mac, "initial": initial,
                             "protocol": r.proto,
                             "boot_id": await read(r.target, r.session, "/proc/sys/kernel/random/boot_id")})
        yield r
    finally:
        if transport:
            transport.close()
        failures = []
        try:
            if r.recovery_console:
                await console_command(r.recovery_console, "nft", "delete", "table", "inet", TABLE, check=False)
            else:
                await r.delete_table()
        except (Exception, pytest.fail.Exception) as error:
            failures.append(str(error))
        if hasattr(r, "lan_ip"):
            try:
                if r.recovery_console:
                    await console_command(r.recovery_console, "conntrack", "-D", "-p", r.proto,
                                          "--orig-src", r.lan_ip, "--orig-dst", WAN_IP,
                                          "--sport", str(SPORT), "--dport", str(DPORT), check=False)
                else:
                    await r.clear_ct()
            except Exception as error:
                failures.append(str(error))
        try:
            # A held restart is let go, and an armed delete fault cleared, so
            # a case that failed inside its window does not leave the ports
            # stopped. The knobs only the fault-injection image has are
            # cleared where they exist.
            debug_knobs = [("cdx", "flowtable_fail_unlink"), ("cdx", "ehash_fail_unlink"),
                           ("cdx", "flowtable_restart_hold")]
            if r.recovery_console:
                # Terminal cases remove CDX; restore only knobs still present.
                knobs = [("ask_flowtable", "flowtable_fail_stage"), *debug_knobs]
                await console_python(r.recovery_console, f"""
from pathlib import Path
for module, name in {knobs!r}:
    path = Path('/sys/module') / module / 'parameters' / name
    if path.exists():
        path.write_text('0')
""")
            else:
                result = await r.target.fs_write(r.session, "/sys/module/ask_flowtable/parameters/flowtable_fail_stage", "0")
                if result["errno"]:
                    failures.append(result)
                for module, name in debug_knobs:
                    path = f"/sys/module/{module}/parameters/{name}"
                    if not (await r.target.fs_read(r.session, path))["errno"]:
                        result = await r.target.fs_write(r.session, path, "0")
                        if result["errno"]:
                            failures.append(result)
            # A case that changed CDX's restart limit and could not put it
            # back itself has it put back here -- once no restart is pending,
            # which the boot's own limit could turn terminal -- unless CDX
            # has been unloaded with it.
            if r.restart_limit is not None:
                failures.extend(await restore_restart_limit(r))
        except Exception as error:
            failures.append(str(error))
        for agent, argv in reversed(cleanup):
            try:
                if r.recovery_console and agent is r.target:
                    result = await console_command(r.recovery_console, *argv, check=False)
                else:
                    result = await command(agent, r.session, *argv, check=False)
                if result["rc"]:
                    failures.append(result)
            except Exception as error:
                failures.append(str(error))
        if r.recovery_console:
            r.recovery_console.close()
        if getattr(r, "bulk", False):
            try:
                if failures or getattr(request.node, "_ask_failed", False):
                    for evidence in await r.target.observe(r.session, "artifacts"):
                        data = await r.target.artifact(r.session, evidence["id"])
                        (artifact_dir() / f"flow-snapshot-{evidence['snapshot']}.txt.gz").write_bytes(data)
                        await r.target.request(r.session, "artifact/release", {"id": evidence["id"]})
            except Exception as error:
                failures.append(f"collecting flow evidence: {error}")
            try:
                await r.target.observe(r.session, "reset")
            except Exception as error:
                failures.append(str(error))
        assert not failures, ("fixture restoration failed", failures)


async def ct_listing(r):
    """The test flow's conntrack line as `conntrack -L -o id` prints it.

    The third column is the remaining timeout in seconds, the id names this
    conntrack rather than its tuple, and a flow hardware holds prints as
    [HW_OFFLOAD], which conntrack(8) shows in place of [OFFLOAD]."""
    result = await command(r.target, r.session, "conntrack", "-L", "-p", r.proto,
                           "--orig-src", r.lan_ip, "--orig-dst", WAN_IP,
                           "--sport", str(SPORT), "--dport", str(DPORT), "-o", "id")
    lines = [line for line in result["stdout"].splitlines() if line.startswith(r.proto + " ")]
    assert len(lines) == 1, result
    return lines[0]


async def latch_barrier_failure(r):
    """Invalidate with the table still bound: fail the retirement barrier the
    flowtable's own teardown owes once the connection is removed. Both
    directions' deletes may share it or not; either way one failed barrier is
    one failed deletion, and the worker's own barrier is not failed, so the
    invalidation drains completely."""
    knob = "/proc/fm_ehash_hcsync_fail"
    before = await r.state()
    result = await r.target.fs_write(r.session, knob, "1")
    assert result["errno"] == 0, result
    try:
        await r.clear_ct()
        latched = await r.wait(lambda s: s["invalidation_done"] == 1 and s["entries"] == 0)
        assert (await read(r.target, r.session, knob)).strip() == "armed=0"
    finally:
        result = await r.target.fs_write(r.session, knob, "0")
        assert result["errno"] == 0, result
    assert latched["invalidated"] == 1 and latched["errors"] - before["errors"] == 1, (before, latched)
    assert latched["fatal"] == latched["quarantine"] == 0, latched
    assert latched["bindings"] == before["bindings"] and latched["rearm_ready"] == 0, latched
    assert latched["handle_refs"] == latched["neighbour_refs"] == 0, latched
    return latched


async def rearm_ready(r, timeout=10):
    """Whether a bind would rearm now, polled until the bound: r.wait() for a
    cleanup, which answers rather than fails. A fatal adapter never becomes
    ready, so it is not waited for."""
    deadline = time.monotonic() + timeout
    while True:
        state = await r.state()
        if state["rearm_ready"]:
            return True
        if state["fatal"] or time.monotonic() >= deadline:
            return False
        await asyncio.sleep(0.1)


async def hardware_proof(r, count=256):
    """The flow's both directions in hardware: exact per-entry hits, conntrack
    reporting it as hardware-offloaded, and next to nothing sent by the CPU."""
    installed = await r.state()
    assert installed["entries"] == 2, installed
    baseline = {f["cookie"]: int(f["packets"]) for f in installed["flows"]}
    tx_before = {d: await kernel_tx_packets(r.target, r.session, d) for d in (TARGET_LAN_IF, TARGET_WAN_IF)}
    await r.exchange(count, promiscuous=False)
    final = await r.state()
    tx_after = {d: await kernel_tx_packets(r.target, r.session, d) for d in tx_before}
    assert {f["cookie"]: int(f["packets"]) - baseline[f["cookie"]] for f in final["flows"]} == \
        {c: count for c in baseline}, (installed, final)
    for dev in tx_before:
        assert 0 <= tx_after[dev] - tx_before[dev] <= 64, (dev, tx_before, tx_after)
    listing = await ct_listing(r)
    assert "[HW_OFFLOAD]" in listing, listing
    return final


async def ct_counts(r):
    """(packets, bytes) conntrack has accounted to each direction of the flow,
    original first."""
    listing = await command(r.target, r.session, "conntrack", "-L", "-p", r.proto,
                            "--orig-src", r.lan_ip, "--orig-dst", WAN_IP,
                            "--sport", str(SPORT), "--dport", str(DPORT), "-o", "extended")
    counts = [(int(p), int(b)) for p, b in re.findall(r"packets=(\d+) bytes=(\d+)", listing["stdout"])]
    assert len(counts) == 2, listing
    return counts


async def terminal_stream(r, duration=12):
    """Keep sending across an intentional datapath stop; validate every echo.

    A terminal operation may interrupt delivery. Normal exchange() retains its
    zero-loss requirement and separately checks forwarding after healthy unload.
    """
    script = f'''
import json, socket, struct, time
s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
s.bind(({r.lan_ip!r}, {SPORT})); s.settimeout(0.02)
first = 1 << 63
sent = first
received = set()
send_timeouts = 0
start = time.monotonic()
while time.monotonic() - start < {duration}:
    payload = struct.pack('!Q', sent) + b'ASK-terminal'.ljust(248, b'.')
    try:
        s.sendto(payload, ({WAN_IP!r}, {DPORT}))
    except TimeoutError:
        # Stopped classifier ports can apply backpressure to the peer.
        # Keep attempting sends until the observation window ends.
        send_timeouts += 1
        time.sleep(0.005)
        continue
    sent += 1
    try:
        data, addr = s.recvfrom(2048)
    except TimeoutError:
        pass
    else:
        assert addr == ({WAN_IP!r}, {DPORT}) and len(data) == 256
        sequence = struct.unpack('!Q', data[:8])[0]
        assert first <= sequence < sent and sequence not in received
        assert data[8:] == b'ASK-terminal'.ljust(248, b'.')
        received.add(sequence)
    time.sleep(0.005)
s.close()
print(json.dumps({{'sent': sent-first, 'received': len(received),
                  'send_timeouts': send_timeouts,
                  'duration': time.monotonic()-start}}))
'''
    result = await lan_run_python(r.lan, script, timeout=duration + 10, label="flowtable_terminal")
    assert result.rc == 0, result.stdout
    return json.loads(result.stdout.strip())


# Whether each receive port is enabled, read from its BMI configuration
# register (bit 31, BMI_PORT_CFG_EN) in the SDK's register dump: the hardware's
# own answer, whether or not cdx or the adapter is loaded; a stopped datapath
# keeps carrier. The SDK names a 1G receive port fm0-port-rx<cell-index - 0x08>
# and a 10G one fm0-port-rx<cell-index - 0x10 + 6>. Find the dumps once, then
# read them as often as needed.
RX_PORTS_CODE = '''
import os, re
def rx_port_dumps():
    paths = {}
    for directory, _, files in os.walk('/sys/devices'):
        if 'fm_port_bmi_regs' not in files:
            continue
        node = os.path.join(directory, 'of_node')
        compatible = open(os.path.join(node, 'compatible'), 'rb').read()
        index = int.from_bytes(open(os.path.join(node, 'cell-index'), 'rb').read()[:4], 'big')
        if b'fman-port-10g-rx' in compatible:
            paths[str(index - 0x10 + 6)] = os.path.join(directory, 'fm_port_bmi_regs')
        elif b'fman-port-1g-rx' in compatible:
            paths[str(index - 0x08)] = os.path.join(directory, 'fm_port_bmi_regs')
    return paths
def rx_ports_enabled(paths, ports=('6', '7')):
    states = {}
    for port in ports:
        match = re.search(r'0x([0-9a-fA-F]{8})\\s+fmbm_rcfg$', open(paths[port]).read(), re.M)
        states[port] = int(match[1], 16) >> 31
    return states
'''
RX_PORTS_SCRIPT = RX_PORTS_CODE + '''
import json
print(json.dumps(rx_ports_enabled(rx_port_dumps())))
'''


@asynccontextmanager
async def links_restored(r, con):
    """Bring the rig's ports up again however a case ends, with the /32 routes
    and permanent neighbours of the fixture's that taking them down can
    discard: the management path and every later test depend on them. Up is
    refused while CDX's latch holds, so this is left after the restart budget,
    whose exit waits for the latch to clear."""
    try:
        yield
    finally:
        cleanup = CleanupStack()
        commands = [["ip", "link", "set", "dev", dev, "up"]
                    for dev in (TARGET_LAN_IF, TARGET_WAN_IF)]
        for address, mac, dev in ((r.lan_ip, r.lan_mac, TARGET_LAN_IF),
                                  (WAN_IP, r.wan_mac, TARGET_WAN_IF)):
            commands.extend([
                ["ip", "route", "replace", address + "/32", "dev", dev],
                ["ip", "neigh", "replace", address, "lladdr", mac,
                 "nud", "permanent", "dev", dev],
            ])
        for argv in reversed(commands):
            cleanup.push(lambda argv=argv: console_command(con, *argv))
        await cleanup.teardown("rig links")
