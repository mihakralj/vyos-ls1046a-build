"""Exact multicast traffic windows for the flowtable multicast cases.

Every window sends numbered streams and counts each sequence at every observer
beneath the IP layer (_mroute_capture.py), so a copy that is missing, doubled or
still arriving after its listener left is a number rather than an impression.
Beside it, a counter on the ingress port's netdev hook says how much of each
stream the CPU carried: a replicated frame never reaches the host.

The rig traps that make a working offload read as dead are kept out of the
cases here rather than in each of them:

  - Captures and member hosts run on the LAN VM as background processes,
    coordinated through files across command calls.
  - An IPv4 join through ip_mreq names its interface by address and silently
    lands on the default route's device. Hosts join through the index-based
    MCAST_* calls instead (_multicast_member.py).
  - `ip -s mroute` lags the hardware by up to one fold interval. Every read of
    the kernel's counters here follows a read of /proc/cdx_flowtable, which is
    itself a fold.
  - Streams are injected on the orchestrator's physical port, so its own
    bridge's snooping cannot decide whether they reach the DUT at all.
"""
from __future__ import annotations
import asyncio
from contextlib import AsyncExitStack, asynccontextmanager
import inspect
import ipaddress
import json
import os
from pathlib import Path
import re
import time
import uuid
import pytest
import pytest_asyncio
from ask_orch.counters import kernel_rx_packets
from ask_orch.uart import Console
from _mcast_cpu import cpu_frames, stream_cpu_counters
from _topology import LAN_NIC, TARGET_LAN_IF, TARGET_WAN_IF, lan_run_python
from _mroute_capture import MAGIC, multicast_mac, payload
from _flowtable_rig import (artifact_dir, command, read, stop_boot_daemon)
from _mcast_e2e import (dut_mac, wan_source_address)
from _mroute_capacity import (_capture, _finish, _python)

COUNT, PPS, PORT = 256, 320, 47420
# A second stream of the same (S,G), for a case that needs two told apart by
# port. Counted at the CPU with the first.
OTHER_PORT = PORT + 1
MEMBER_SOURCE = Path(__file__).with_name("_multicast_member.py").read_text()
# The capture's own payload and group MAC, so a LAN-side sender cannot drift
# from the oracle that decodes what it sent.
WIRE_HELPERS = ("import ipaddress, struct\n" + f"MAGIC = {MAGIC!r}\n"
                + inspect.getsource(multicast_mac) + inspect.getsource(payload))


def wire_interface() -> str:
    """The orchestrator's physical port toward the DUT.

    Not the bridge above it: that bridge snoops too, and a stream sent into it
    reaches the DUT only while something on the DUT's side has joined -- which
    a case about leaving must not depend on."""
    configured = os.environ.get("ASK_WAN_WIRE_IF")
    if configured:
        return configured
    inject = os.environ.get("ASK_WAN_INJECT_IF", "")
    members = Path("/sys/class/net", inject, "brif")
    if not members.exists():
        return inject
    physical = [p.name for p in members.iterdir()
                if Path("/sys/class/net", p.name, "device").exists()]
    assert len(physical) == 1, ("set ASK_WAN_WIRE_IF to the DUT-facing physical port", physical)
    return physical[0]


def same(a: str, b: str) -> bool:
    return ipaddress.ip_address(a) == ipaddress.ip_address(b)


def mroute_row(state: dict, group: str, source: str) -> dict | None:
    rows = [row for row in state["mroute"] if same(row["group"], group) and same(row["src"], source)]
    assert len(rows) <= 1, ("duplicate routed rows", rows)
    return rows[0] if rows else None


def mcast_rows(state: dict, group: str) -> list[dict]:
    return [row for row in state["mcast"] if same(row["group"], group)]


def lan_groups(state: dict) -> set[tuple[str, str]]:
    """Every bridged group but those held only on the WAN port.

    The test bridges query their WAN port too, and every host on that live
    segment answers with memberships of its own -- SSDP, mDNS, DHCPv6 --
    that come and go on their own schedule, so the adapter's group count
    moves under a case that changed nothing. Their streams are the WAN's
    too: a flow in by the WAN port that no port wants is theirs as well."""
    def foreign(row):
        if row["ports"] in ("", "-"):
            return row["in"] == TARGET_WAN_IF
        return all(port.split("/")[0] == TARGET_WAN_IF for port in row["ports"].split(","))
    return {(row["group"], row["vid"]) for row in state["mcast"] if not foreign(row)}


def members(row: dict | None, field: str) -> set[str]:
    """A row's `listeners` or `ports` as a set; empty for no row at all."""
    if row is None or row[field] == "-":
        return set()
    return set(row[field].split(","))


def summary(state: dict) -> dict:
    """The multicast part of the adapter's state, for failure messages."""
    keys = [k for k in state if k.startswith(("mcast_", "mroute_"))] + ["quarantine", "fatal"]
    return {**{k: state[k] for k in keys}, "mcast": state["mcast"], "mroute": state["mroute"]}


def stream(family: int, group: str, *, hops: int, source: str | None = None,
           port: int = PORT) -> dict:
    """One numbered stream, in the shape _mroute_capture.py decodes. `port` is
    both UDP ports of its frames."""
    return {"family": family, "source": source or wan_source_address(family), "group": group,
            "port": port, "count": COUNT, "token": uuid.uuid4().hex, "hops": hops}


def stream_key(config: dict) -> str:
    """Where a window files a stream's deliveries: its group and source, and
    its port where that is not the usual one, so two ports of one (S,G) sent
    in one window are counted apart."""
    key = config["group"] + "/" + config["source"]
    return key if config["port"] == PORT else f"{key}/{config['port']}"


def frames(config: dict) -> list:
    from scapy.all import Ether, IP, IPv6, UDP, Raw
    # Explicit and above 1: the parser refuses to classify TTL 0 or 1.
    layer = (IP(src=config["source"], dst=config["group"], ttl=64) if config["family"] == 4 else
             IPv6(src=config["source"], dst=config["group"], hlim=64))
    return [Ether(dst=multicast_mac(config["group"]).hex(":")) / layer /
            UDP(sport=config["port"], dport=config["port"]) / Raw(payload(config["token"], i))
            for i in range(config["count"])]


def send_wire(configs: list[dict], iface: str) -> None:
    """Send every stream once from this host, interleaved frame by frame."""
    from scapy.all import sendp
    batches = [frames(config) for config in configs]
    interleaved = [f for batch in zip(*batches, strict=True) for f in batch]
    sendp(interleaved, iface=iface, inter=1 / (PPS * len(configs)), verbose=False)


async def send_lan(lan, configs: list[dict], iface: str = LAN_NIC) -> None:
    """The same streams, sent from the LAN VM: a source behind the LAN port."""
    script = WIRE_HELPERS + f"""
from scapy.all import Ether, IP, IPv6, UDP, Raw, sendp
configs = {configs!r}
batches = []
for c in configs:
    layer = (IP(src=c['source'], dst=c['group'], ttl=64) if c['family'] == 4 else
             IPv6(src=c['source'], dst=c['group'], hlim=64))
    batches.append([Ether(dst=multicast_mac(c['group']).hex(':')) / layer /
                    UDP(sport=c['port'], dport=c['port']) / Raw(payload(c['token'], i))
                    for i in range(c['count'])])
sendp([f for batch in zip(*batches) for f in batch], iface={iface!r},
      inter=1 / ({PPS} * len(configs)), verbose=False)
print('SENT')
"""
    result = await lan_run_python(lan, script, label="mcast_send", timeout=40)
    assert result.rc == 0 and "SENT" in result.stdout, result.stdout


class MulticastRig:
    def __init__(self, target, session, lan):
        self.target, self.session, self.lan = target, session, lan
        # CDX's restart limit as a case found it, while the case has it
        # raised (_flowtable_restart.restart_budget()).
        self.restart_limit = None

    async def proc(self) -> dict:
        return await self.target.observe(self.session, "state")

    async def settle(self, predicate, what: str, timeout: float = 15) -> dict:
        deadline = time.monotonic() + timeout
        while True:
            state = await self.proc()
            if predicate(state):
                return state
            if time.monotonic() > deadline:
                pytest.fail(f"{what}: not reached in {timeout}s: {summary(state)}")
            await asyncio.sleep(0.1)

    async def window(self, streams: list[dict], observers: list, *, ingress: str,
                     label: str, sender: str = "wan", adapter: bool = True) -> dict:
        """Send `streams` once and count every sequence at every observer.

        observers is [(peer, {interface: expected source MAC or None})]; peer
        is the LAN guest agent, or None for a capture on this host. `cpu` is the
        ingress port's software receive delta across the send, `idle` what the
        same counter moved by over an idle interval just before, scaled to the
        send's own length: a busy segment's background traffic grows with the
        interval, and the send runs longer than its paced frames.
        `adapter` is False while the adapter is unloaded and has no /proc."""
        idle_start, idle_began = await kernel_rx_packets(self.target, self.session, ingress), time.monotonic()
        await asyncio.sleep(COUNT / PPS)
        idle = await kernel_rx_packets(self.target, self.session, ingress) - idle_start
        idle_seconds = time.monotonic() - idle_began
        captures = []
        async with AsyncExitStack() as stack:
            for config in streams:
                for peer, interfaces in observers:
                    captures.append((config, await stack.enter_async_context(
                        _capture(peer, {**config, "interfaces": interfaces}))))
            before = await self.proc() if adapter else None
            counted = await cpu_frames(self.target, self.session, ingress)
            rx, sent = await kernel_rx_packets(self.target, self.session, ingress), time.monotonic()
            if sender == "lan":
                await send_lan(self.lan, streams)
            else:
                await asyncio.to_thread(send_wire, streams, self.wire)
            cpu = await kernel_rx_packets(self.target, self.session, ingress) - rx
            idle = round(idle * (time.monotonic() - sent) / idle_seconds)
            await asyncio.sleep(0.4)  # drain receiver queues before asking
            counted = await cpu_frames(self.target, self.session, ingress) - counted
        received = {}
        for config, capture in captures:
            received.setdefault(stream_key(config), {}).update(await _finish(capture))
        after = await self.proc() if adapter else None  # also folds the routed counters
        result = {"streams": streams, "received": received, "stream_cpu": counted,
                  "cpu": cpu, "idle": idle,
                  "before": before and summary(before), "after": after and summary(after)}
        self.record(f"mcast-window-{label}", result)
        return {**result, "before": before, "after": after}

    def record(self, name: str, data) -> None:
        artifact_dir().mkdir(parents=True, exist_ok=True)
        (artifact_dir() / f"{name}.json").write_text(json.dumps(data, indent=2, default=str) + "\n")


def streamed(window: dict, group: str, source: str | None = None,
             port: int | None = None) -> dict:
    """The config a window sent for `group` (and `source`, and `port`, where
    it had two)."""
    configs = [c for c in window["streams"] if same(c["group"], group) and
               (source is None or same(c["source"], source)) and
               (port is None or c["port"] == port)]
    assert len(configs) == 1, (group, source, port, window["streams"])
    return configs[0]


def moved(window: dict, row) -> int:
    """How far a /proc row's classifier count moved across a window.

    `row` maps a state to the row, so a group installed or retired inside the
    window counts from or to zero."""
    return packets(row(window["after"])) - packets(row(window["before"]))


def seen(window: dict, config: dict, iface: str) -> list[int]:
    """The sequences one stream delivered at one interface; exact or it fails."""
    result = window["received"][stream_key(config)][iface]
    assert result["duplicates"] == 0 and not result["errors"], (iface, result)
    return result["seen"]


def delivered(window: dict, config: dict, iface: str) -> bool:
    """Every sequence exactly once (True) or none at all (False)."""
    sequences = seen(window, config, iface)
    assert sequences in ([], list(range(config["count"]))), (iface, "partial delivery", sequences)
    return bool(sequences)


def in_hardware(window: dict, streams: int = 1) -> None:
    """Next to none of the stream reached the CPU."""
    assert window["stream_cpu"] < COUNT * streams * 0.1, (window["stream_cpu"], window["cpu"], window["idle"])


def in_software(window: dict, streams: int = 1) -> None:
    """The whole stream reached the CPU: the classifier matched none of it."""
    assert window["stream_cpu"] >= COUNT * streams, (window["stream_cpu"], window["cpu"], window["idle"])


def packets(row: dict | None) -> int:
    return int(row["packets"]) if row else 0


async def kernel_mroute(r: MulticastRig, family: int, source: str, group: str, *,
                        offloaded: bool | None = None, fold: bool = True):
    """The kernel's MFC entry for (S,G): its `ip mroute` line and ip -s counters.

    `fold` reads /proc first, which folds the hardware counts in; the fold's
    own timer lags by up to five seconds. MFC_OFFLOAD is written just after
    /proc starts saying `installed` and just after it stops, so a caller
    that names the flag it expects waits a moment for it."""
    deadline = time.monotonic() + 5
    while True:
        if fold:
            await r.proc()
        out = (await command(r.target, r.session, "ip", "-s", *(["-6"] if family == 6 else []),
                             "mroute", "show"))["stdout"].splitlines()
        found = None, None, None
        for i, line in enumerate(out):
            match = re.match(r"\((\S+?),\s*(\S+?)\)", line.strip())
            if match and same(match[1], source) and same(match[2], group):
                counts = re.search(r"(\d+) packets, (\d+) bytes", out[i + 1] if i + 1 < len(out) else "")
                assert counts, (line, out[i + 1:i + 2])
                found = line, int(counts[1]), int(counts[2])
                break
        if offloaded is None or (found[0] and ("offload" in found[0]) == offloaded) or \
                time.monotonic() > deadline:
            return found
        await asyncio.sleep(0.2)


async def learn(r: MulticastRig, configs: list[dict], installed, what: str, *,
                sender: str = "wan") -> dict:
    """Short bursts until the learners have taken each stream.

    A (*,G) membership has no key until traffic supplies one, and a routed
    group is carried only once Linux has been seen forwarding a copy of it to
    every oif. A frame sent before either learner's hook is registered
    teaches it nothing, so this keeps offering until the groups are
    installed. A LAN `sender` offers them from behind the LAN port."""
    deadline = time.monotonic() + 15
    while True:
        primers = [{**config, "count": 8, "token": uuid.uuid4().hex} for config in configs]
        if sender == "lan":
            await send_lan(r.lan, primers)
        else:
            await asyncio.to_thread(send_wire, primers, r.wire)
        state = await r.proc()
        if installed(state):
            return state
        if time.monotonic() > deadline:
            pytest.fail(f"{what}: never installed from traffic: {summary(state)}")
        await asyncio.sleep(0.3)


async def trickle(r: MulticastRig, configs: list[dict], seconds: float) -> None:
    """Keep the streams' entries counting across a wait of `seconds`.

    An installed bridged flow whose entry counts nothing for the bridge's
    membership interval -- two refreshes at the least -- is a stream that
    stopped: it ages out and is learned again from its next frame, and a
    window across that counts frames in software. A few frames a second, each
    burst with a token of its own so no capture counts it, keep the entry
    live through a wait longer than that."""
    deadline = time.monotonic() + seconds
    while True:
        keep = [{**config, "count": 4, "token": uuid.uuid4().hex} for config in configs]
        await asyncio.to_thread(send_wire, keep, r.wire)
        left = deadline - time.monotonic()
        if left <= 0:
            return
        await asyncio.sleep(min(1.0, left))


async def mdb(r: MulticastRig, bridge: str, group: str, *, add: bool, vid: int | None = None,
              port: str = TARGET_LAN_IF) -> None:
    """A static membership: what an operator configures, never learned."""
    await command(r.target, r.session, "bridge", "mdb", "add" if add else "del", "dev", bridge,
                  "port", port, "grp", group, *(["vid", str(vid)] if vid else []),
                  *(["permanent"] if add else []))


async def mdb_ports(r: MulticastRig, bridge: str, group: str) -> set[str]:
    """Which ports the bridge itself says are members of `group`."""
    entries = json.loads((await command(r.target, r.session, "bridge", "-j", "mdb", "show",
                                        "dev", bridge))["stdout"] or "[]")
    ports = set()
    for entry in entries:
        for item in entry.get("mdb", []):
            if same(item["grp"], group):
                ports.add(item["port"])
    return ports


# Bridge multicast settings, by sysfs name; `ip link` takes them as mcast_<name>.
# Intervals are clock_t in both places.
@asynccontextmanager
async def bridge_settings(r: MulticastRig, bridge: str, **values):
    """Set a test bridge's querier and membership timers, restart its own
    querier under them, and put the old settings back.

    The restart is not optional. New intervals apply only from the next query,
    and a querier started with its bridge sent its first IPv6 query before the
    bridge had a usable link-local address; that failure counts it out until
    the next startup query, half a minute on. Until then the bridge floods an
    unregistered IPv6 group to every port -- which reads exactly like a
    listener that never left -- and the routed learner's bridge snapshot sees
    the flood set rather than the members. Restarted once the address is
    valid, it queries at once in both families and counts after one response
    interval."""
    values = {"query_response_interval": 100, **values}
    old = {}
    for name in values:
        old[name] = (await read(r.target, r.session,
                                f"/sys/class/net/{bridge}/bridge/multicast_{name}")).strip()

    async def apply(settings):
        argv = ["ip", "link", "set", "dev", bridge, "type", "bridge"]
        for name, value in settings.items():
            argv += [f"mcast_{name}", str(value)]
        await command(r.target, r.session, *argv)

    await apply(values)
    for querier in ("0", "1"):
        await command(r.target, r.session, "ip", "link", "set", "dev", bridge, "type", "bridge",
                      "mcast_querier", querier)
    await asyncio.sleep(int(values["query_response_interval"]) / 100 + 1)
    try:
        yield
    finally:
        # A case may have taken the bridge down itself, which restores
        # nothing but leaves nothing to restore either.
        present = await r.target.fs_read(r.session, f"/sys/class/net/{bridge}/bridge/multicast_querier")
        if present["errno"] == 0:
            await apply(old)


class Host:
    """One multicast listener on the LAN VM; see _multicast_member.py."""

    def __init__(self, lan, config: dict, path: str):
        self.lan, self.config, self.path, self.serial = lan, config, path, 0

    async def do(self, action: str) -> None:
        self.serial += 1
        out = await _python(self.lan, f"""
import json, pathlib, time
new = pathlib.Path({self.path + '.command-new'!r})
new.write_text(json.dumps({{'serial': {self.serial}, 'action': {action!r}}}))
new.replace({self.config['command']!r})
state = pathlib.Path({self.config['state']!r})
for _ in range(100):
    if {self.serial} in json.loads(state.read_text())['done']:
        print('DONE')
        break
    time.sleep(0.05)
else:
    print('TIMEOUT', state.read_text(), open({self.path + '.log'!r}).read())
""")
        assert "DONE" in out.splitlines(), out


@asynccontextmanager
async def host(lan, *, family: int, group: str, iface: str, mode: str, version: int,
               source: str | None = None):
    """A listener that joins on entry and is gone, membership and all, on exit."""
    path = f"/tmp/ask-mcast-member-{uuid.uuid4().hex}"
    config = {"family": family, "group": group, "source": source or wan_source_address(family),
              "iface": iface, "mode": mode, "version": version,
              "state": path + ".json", "command": path + ".command"}
    script = path + ".py"
    try:
        out = await _python(lan, f"""
import json, pathlib, subprocess, sys, time
pathlib.Path({script!r}).write_text({MEMBER_SOURCE!r})
with open({path + '.log'!r}, 'w') as log:
    child = subprocess.Popen([sys.executable, {script!r}, {json.dumps(config)!r}],
                             stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                             start_new_session=True)
pathlib.Path({path + '.pid'!r}).write_text(str(child.pid))
state = pathlib.Path({config['state']!r})
for _ in range(50):
    if state.exists() or child.poll() is not None:
        break
    time.sleep(0.1)
print('JOINED' if state.exists() else 'FAILED ' + open({path + '.log'!r}).read())
""")
        assert "JOINED" in out.splitlines(), out
        yield Host(lan, config, path)
    finally:
        # Only this host's process, identified by its script, never a
        # recycled PID. Its exit drops whatever membership is left.
        await _python(lan, f"""
import os, pathlib, signal, time
pidfile = pathlib.Path({path + '.pid'!r})
if pidfile.exists():
    pid = int(pidfile.read_text())
    try:
        if {script.encode()!r} in pathlib.Path('/proc/%d/cmdline' % pid).read_bytes().split(b'\\0'):
            os.kill(pid, signal.SIGTERM)
            for _ in range(50):
                if not pathlib.Path('/proc/%d' % pid).exists():
                    break
                time.sleep(0.1)
    except (ProcessLookupError, FileNotFoundError):
        pass
for suffix in ('.py', '.pid', '.json', '.tmp', '.command', '.command-new', '.log'):
    pathlib.Path({path!r} + suffix).unlink(missing_ok=True)
""")


QUIET_TABLE = "ask_mc_quiet"


@asynccontextmanager
async def quiet(r: MulticastRig, groups):
    """Only the case's own groups, and link-local control, reach the bridge
    from either port: everything else multicast is dropped at ingress, before
    the bridge can learn it and the bridged learner discard it in hardware.
    A group may be a prefix, for a case with more groups than it names."""
    v4 = ", ".join(["224.0.0.0/24"] + [g for g in groups if ":" not in g])
    v6 = ", ".join(["ff02::/16"] + [g for g in groups if ":" in g])
    chains = "\n".join(f''' chain {dev} {{ type filter hook ingress device "{dev}" priority -400; policy accept;
  ip daddr 224.0.0.0/4 ip daddr != {{ {v4} }} drop
  ip6 daddr ff00::/8 ip6 daddr != {{ {v6} }} drop
 }}''' for dev in (TARGET_WAN_IF, TARGET_LAN_IF))
    await command(r.target, r.session, "nft", "delete", "table", "netdev", QUIET_TABLE, check=False)
    await command(r.target, r.session, "nft", f"table netdev {QUIET_TABLE} {{\n{chains}\n}}")
    try:
        yield
    finally:
        await command(r.target, r.session, "nft", "delete", "table", "netdev", QUIET_TABLE,
                      check=False)


@asynccontextmanager
async def silenced(lan, iface: str):
    """The host behind `iface` stops answering queries, without leaving.

    Its membership stands in its own stack; only the reports that would
    refresh it on the bridge are dropped, which is how a set-top box that
    lost power looks from the querier's side."""
    table = "ask_mcast_silence_" + uuid.uuid4().hex[:8]
    rules = f'''table inet {table} {{
 chain output {{ type filter hook output priority -300; policy accept;
  oifname "{iface}" ip protocol igmp drop
  oifname "{iface}" meta l4proto ipv6-icmp icmpv6 type {{ 130, 131, 132, 143 }} drop
 }}
}}'''
    await _python(lan, f"import subprocess\nsubprocess.run(['nft', '-f', '-'], input={rules!r}, text=True, check=True)\n")
    try:
        yield
    finally:
        await _python(lan, f"import subprocess\nsubprocess.run(['nft', 'delete', 'table', 'inet', {table!r}], check=True)\n")


async def dut_console(label: str) -> Console:
    console = Console.target(log_path=str(artifact_dir() / f"{label}-uart.log"))
    try:
        await asyncio.to_thread(console.login, "root", None)
    except BaseException:
        console.close()
        raise
    return console


@pytest_asyncio.fixture
async def multicast_rig(target_agent, aiohttp_session, lan, splat_window):
    """The DUT with no multicast workload of its own and no unicast policy.

    The boot service is stopped the way every controlled rig stops it: its
    offloaded flows would put unrelated hardware deletions -- and their
    barriers -- inside windows these cases count. On the way out, every group,
    installed entry and parked table entry a case created must be gone."""
    await stop_boot_daemon()
    r = MulticastRig(target_agent, aiohttp_session, lan)
    r.initial = await r.proc()
    assert r.initial["mcast_groups"] == r.initial["mroute_groups"] == 0, \
        ("existing multicast groups belong to another workload", summary(r.initial))
    assert r.initial["quarantine"] == r.initial["fatal"] == 0, summary(r.initial)
    r.wire = wire_interface()
    r.dut_lan_mac = await dut_mac(target_agent, aiohttp_session, TARGET_LAN_IF)
    r.dut_wan_mac = await dut_mac(target_agent, aiohttp_session, TARGET_WAN_IF)
    async with stream_cpu_counters(target_agent, aiohttp_session, (PORT, OTHER_PORT)):
        try:
            yield r
        finally:
            # A case whose console could not put CDX's restart limit back
            # leaves it here, to go back once no restart is pending: CDX
            # checks the limit on every attempt, and the boot's own could find
            # its budget spent and stop the ports for good.
            restored = None
            if r.restart_limit is not None:
                cleared = await r.settle(lambda s: not s["fatal"], "CDX's latch clear", timeout=10)
                restored = await target_agent.fs_write(
                    aiohttp_session, "/sys/module/cdx/parameters/flowtable_restart_limit", r.restart_limit)
                r.restart_limit = None
                r.record("mcast-restart-limit-restored", summary(cleared))
            drained = await r.settle(
                lambda s: s["mcast_groups"] == s["mroute_groups"] == 0 and
                s["mcast_installed"] == s["mroute_installed"] == 0 and s["quarantine"] == 0,
                "multicast state drained after the case", timeout=15)
            r.record("mcast-drained", summary(drained))
            assert restored is None or not restored["errno"], ("CDX's restart limit not restored", restored)
