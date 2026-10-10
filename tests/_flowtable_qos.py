"""Shared support for flowtable qos."""

from __future__ import annotations

import asyncio
import json
import os
import re
import shlex
import socket
import struct
import threading
import time

import pytest
import pytest_asyncio
from _flowtable_rig import (
    DPORT,
    TABLE,
    WAN_IP,
    artifact_dir,
    command,
    console_command,
    console_json,
    read,
)
from _topology import TARGET_LAN_IF, TARGET_WAN_IF, lan_run_python
from ask_orch.client import Agent
from ask_orch.uart import Console

# One port per flow, so a conntrack left behind by one case never feeds another.
# Above the gateway profiles' ranges (49100 and 49200). The devlink file takes
# PORT + 16 onwards; the NAT exemption covers the whole block.
PORT = int(os.environ.get("ASK_FLOWTABLE_QOS_PORT", "49300"))
PORT_SHAPED = PORT
PORT_HIGH, PORT_LOW = PORT + 1, PORT + 2
PORT_BULK, PORT_PROBE = PORT + 3, PORT + 4
PORT_EF, PORT_BE, PORT_EF_SOFTWARE = PORT + 5, PORT + 6, PORT + 7
PORT_POLICED = PORT + 8
PORT_EGRESS = PORT + 9
PORT_EF_BEFORE, PORT_EF_MOVED = PORT + 10, PORT + 11
PORT_SATURATE = PORT + 12
PORT_UNCLASSIFIED_SW, PORT_UNCLASSIFIED_HW = PORT + 13, PORT + 14
PORT_DEFAULT = PORT + 15
PORT_REMARK_HW, PORT_REMARK_SW = PORT + 18, PORT + 19
PORT_EF_REPLACED = PORT + 20
PORT_DECLINED = PORT + 21
PORT_WEIGHTED, PORT_WEIGHTED_BULK = PORT + 22, PORT + 23
PORTS_LAST = PORT + 23

# The shaped or policed rate. Below the roughly 9 Gbit/s the rig forwards
# unshaped and far above what the CPU can: see the module docstring.
CAP_MBIT = int(os.environ.get("ASK_FLOWTABLE_QOS_CAP_MBIT", "2000"))
OFFERED_MBIT = 3 * CAP_MBIT
DATAGRAM = 1400
UDP_HEADERS = 14 + 20 + 8
# Bytes a CEETM shaper charges each frame beyond the frame itself -- preamble,
# delimiter, FCS and inter-frame gap -- as the port's LNI is configured
# (CEETM_DEFA_OAL). A shaper held at the cap passes fewer payload bits than the
# cap by exactly this and the headers.
OAL = 24

# cdx_htb_cq_get() numbers the strict-priority class queues from the top, so a
# leaf of `prio N` holds class queue 7 - N, and a conntrack mark names that
# index. prio 0 is the queue that wins.
HIGH_PRIO, HIGH_CQ = 0, 7
LOW_PRIO, LOW_CQ = 1, 6
# A leaf that gives a quantum takes the first free queue of the weighted group,
# which starts at class queue 8.
WEIGHTED_CQ = 8

IPERF_SECONDS = 8
# Admission, and the burst a token bucket starts with, both happen in the first
# second or two of a transfer; rates are sampled after them.
SETTLE = 2.5
WINDOW = 4.0

# The WRED case reads queue occupancy as the extra round trip a probe waits
# behind it, so it shapes lower than the rest: at 500 Mbit/s a full 128-frame
# queue is 3 ms and a band a few tens of kilobytes wide a fraction of one, which
# a probe resolves cleanly. That is still several times what the CPU forwards,
# so the bulk flow's rate remains proof it was in hardware.
WRED_MBIT = int(os.environ.get("ASK_FLOWTABLE_QOS_WRED_MBIT", "500"))
# (min, max) in bytes: a narrow band, and a wider one wholly above it.
WRED_BANDS = {"narrow": (8000, 24000), "wide": (40000, 100000)}
# The tail-drop threshold under RED, far above either band: a curve that did
# not drop until its tail would show as a queue this deep.
WRED_LIMIT = 400000
WRED_PROBABILITY = "0.1"
# A leaf's class queue with no RED qdisc on it tail-drops at this many frames
# (CDX_HTB_CQ_DEPTH).
TAIL_FRAMES = 128
TCP_FRAME, TCP_PAYLOAD = 1514, 1448
# Scheduling noise in a round trip between two Python processes.
PROBE_SLACK = 0.15e-3

# Expedited forwarding, DSCP 46, as it sits in the tos byte.
EF_DSCP = 46
EF_TOS = EF_DSCP << 2
COUNT = 256
# A class that carries a remark: the flag at bit 12, the codepoint in the six
# bits above it. The image's 0xf0 mask stops at the class queue, so the adapter
# is reloaded with the field widened from the same base -- a mark of 0x70 still
# names class queue 7 under it.
REMARK_MASK = 0x7ffff0
REMARK_CLASS = 1 << 12 | EF_DSCP << 13

# A UDP flow does not back off, so the policer's burst only shapes the first
# instant of the transfer; a megabyte keeps that instant short.
POLICE_BURST = "1m"

# Goodput a hardware TCP transfer has to hold with no tree in its way. The rig
# forwards about 9.4 Gbit/s; the CPU a small fraction of that.
UNSHAPED_GBPS = float(os.environ.get("ASK_FLOWTABLE_QOS_UNSHAPED_GBPS", "7"))

LAN_BASE = "/tmp/ask_flowtable_qos"
ECHO = f"{LAN_BASE}_echo.py"
ECHO_SOURCE = '''
import select, socket, sys
host, ports = sys.argv[1], [int(port) for port in sys.argv[2:]]
socks = []
for port in ports:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 1 << 21)
    s.bind((host, port))
    socks.append(s)
while True:
    for s in select.select(socks, [], [])[0]:
        data, peer = s.recvfrom(4096)
        s.sendto(data, peer)
'''


# ---- the LAN VM's side, detached ------------------------------------------

async def lan_start(r, *, iperf=(), echo=(), host=None, lifetime=240):
    """Start iperf3 servers and a UDP echo on the LAN VM, detached.

    Traffic is driven from this host while the servers run. Each process is its
    own session under `timeout`, so a run that dies without its teardown leaves
    nothing listening for longer than `lifetime`, and `lan_stop` kills each
    whole group. The servers speak JSON so a client asking for
    `--get-server-output` gets the receiver's intervals back.
    """
    host = host or r.lan_ip
    plan = [["iperf3", "-s", "-J", "-B", host, "-p", str(port)] for port in iperf]
    if echo:
        plan.append(["python3", ECHO, host, *map(str, echo)])
    script = f'''
import pathlib, subprocess, time
pathlib.Path({ECHO!r}).write_text({ECHO_SOURCE!r})
plan = {plan!r}
procs = []
with open({LAN_BASE + '.pids'!r}, 'a') as pids:
    for index, argv in enumerate(plan):
        log = open({LAN_BASE!r} + '_%d.err' % index, 'wb')
        proc = subprocess.Popen(['timeout', {str(lifetime)!r}] + argv, stdin=subprocess.DEVNULL,
                                stdout=subprocess.DEVNULL, stderr=log, start_new_session=True)
        pids.write('%d\\n' % proc.pid)
        procs.append(proc)
time.sleep(0.7)
for index, (argv, proc) in enumerate(zip(plan, procs)):
    assert proc.poll() is None, (argv, pathlib.Path({LAN_BASE!r} + '_%d.err' % index).read_text())
print('LAN-UP')
'''
    result = await lan_run_python(r.lan, script, label="flowtable_qos_start", timeout=30)
    assert result.rc == 0 and "LAN-UP" in result.stdout, result.stdout


async def lan_stop(r):
    script = f'''
import os, pathlib, signal
pids = pathlib.Path({LAN_BASE + '.pids'!r})
if pids.exists():
    for pid in pids.read_text().split():
        try:
            os.killpg(int(pid), signal.SIGTERM)
        except ProcessLookupError:
            pass
    pids.unlink()
print('LAN-DOWN')
'''
    result = await lan_run_python(r.lan, script, label="flowtable_qos_stop", timeout=30)
    assert result.rc == 0 and "LAN-DOWN" in result.stdout, result.stdout


# ---- the orchestrator's side ----------------------------------------------

async def iperf(r, port, *, seconds=IPERF_SECONDS, udp_mbit=None, streams=1, datagram=DATAGRAM):
    """One iperf3 run from here to the LAN VM, and its report.

    The receiver's intervals come back in `server_output_json`, which is where a
    steady-state rate is read: the sender's own figure for UDP is what it
    offered, not what arrived.
    """
    argv = ["iperf3", "-c", r.lan_ip, "-B", WAN_IP, "-p", str(port), "-t", str(seconds),
            "-J", "--get-server-output"]
    if udp_mbit:
        argv += ["-u", "-b", f"{udp_mbit}M", "-l", str(datagram)]
    if streams > 1:
        argv += ["-P", str(streams)]
    proc = await asyncio.create_subprocess_exec(*argv, stdout=asyncio.subprocess.PIPE,
                                                stderr=asyncio.subprocess.PIPE)
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), seconds + 30)
    finally:
        if proc.returncode is None:
            proc.kill()
            await proc.wait()
    report = json.loads(stdout) if stdout.strip() else {}
    assert proc.returncode == 0 and "error" not in report, (
        proc.returncode, report.get("error"), stderr.decode(errors="replace")[-400:])
    return report


def received_bps(report, after=SETTLE):
    """What the receiver counted per second once the transfer had settled."""
    intervals = [i["sum"] for i in report["server_output_json"]["intervals"]
                 if i["sum"]["start"] >= after - 0.01]
    assert intervals, report["server_output_json"]
    return sum(i["bytes"] for i in intervals) * 8 / sum(i["seconds"] for i in intervals)


def received_loss(report, after=SETTLE):
    """The fraction of a UDP transfer the receiver never saw, once settled."""
    intervals = [i["sum"] for i in report["server_output_json"]["intervals"]
                 if i["sum"]["start"] >= after - 0.01]
    packets = sum(i["packets"] for i in intervals)
    assert packets, report["server_output_json"]
    return sum(i["lost_packets"] for i in intervals) / packets


def lockstep(destination, port, count, *, tos=0, payload_size=256, timeout=1.0):
    """Echo `count` datagrams one at a time from this host, and count the replies.

    The source port is the destination port, so every call is the same flow and
    a flow admitted by one call is measured by the next.
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.setsockopt(socket.IPPROTO_IP, socket.IP_TOS, tos)
    sock.bind((WAN_IP, port))
    sock.settimeout(timeout)
    echoed = 0
    try:
        for n in range(count):
            payload = struct.pack("!Q", n) + b"ASK-qos".ljust(payload_size - 8, b".")
            sock.sendto(payload, (destination, port))
            try:
                while sock.recv(4096) != payload:
                    pass
                echoed += 1
            except TimeoutError:
                pass
    finally:
        sock.close()
    return echoed


def probe(destination, seconds, *, interval=0.01, timeout=0.25):
    """Round trips of a small datagram to the LAN VM's echo, one at a time.

    The probe's flow is marked into the same class as the traffic under test,
    so its request waits behind whatever that class queue holds, and the round
    trip it adds over an idle one is the queue's depth in time. A probe the
    queue dropped is counted, not waited for.
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind((WAN_IP, PORT_PROBE))
    rtts, lost, sent = [], 0, 0
    end = time.monotonic() + seconds
    try:
        while time.monotonic() < end:
            payload = struct.pack("!Q", sent) + b"ASK-qos-probe"
            sent += 1
            started = time.perf_counter()
            sock.sendto(payload, (destination, PORT_PROBE))
            while True:
                left = started + timeout - time.perf_counter()
                if left <= 0:
                    lost += 1
                    break
                sock.settimeout(left)
                try:
                    data = sock.recv(2048)
                except TimeoutError:
                    lost += 1
                    break
                if data == payload:
                    rtts.append(time.perf_counter() - started)
                    break
            pause = started + interval - time.perf_counter()
            if pause > 0:
                time.sleep(pause)
    finally:
        sock.close()
    return {"rtts": rtts, "lost": lost, "sent": sent}


# ---- reading the DUT ------------------------------------------------------

LEAF_COUNTER = re.compile(
    r"^\s*ceetm (dequeued frames|dequeued bytes|rejected frames) \[leaf (\d+)\]:\s*(\d+)\s*$", re.M)
# The two queues traffic that names no leaf takes, after the sixteen slots:
# where unclassified traffic goes, and the top channel's control queue.
IMPLICIT_COUNTER = re.compile(
    r"^\s*ceetm (dequeued frames|dequeued bytes|rejected frames) \[(default|control)\]:\s*(\d+)\s*$",
    re.M)
SOFTWARE_TX = re.compile(r"^\s*tx packets \[TOTAL\]:\s*(\d+)\s*$", re.M)
LEAF_FIELDS = {"dequeued frames": "frames", "dequeued bytes": "bytes",
               "rejected frames": "rejected"}


async def egress(r, dev):
    """One `ethtool -S` read of a port: each leaf slot's dequeued frames and
    bytes and rejected frames, the same for the queues unclassified and
    control traffic take, the port's software transmit count, and when.

    The leaf counters are hardware totals read without clearing, so a delta
    between two reads is exact. The time is the middle of the call, which is
    what a rate over a window of a few seconds is taken against, and the call's
    length is kept: the counters were read somewhere inside it.
    """
    result = await command(r.target, r.session, "ethtool", "-S", dev)
    text = result["stdout"]
    leaves = {}
    for name, slot, value in LEAF_COUNTER.findall(text):
        leaves.setdefault(int(slot), {})[LEAF_FIELDS[name]] = int(value)
    for name, queue, value in IMPLICIT_COUNTER.findall(text):
        leaves.setdefault(queue, {})[LEAF_FIELDS[name]] = int(value)
    software = SOFTWARE_TX.findall(text)
    assert len(leaves) == 18 and len(software) == 1, text
    return {"at": result["at"], "span": result["span"], "leaves": leaves,
            "software_tx": int(software[0])}


def leaf_delta(before, after, slot):
    """What one leaf slot's queue -- or "default" or "control" -- moved by."""
    return {key: after["leaves"][slot][key] - before["leaves"][slot][key]
            for key in ("frames", "bytes", "rejected")}


def timing_slack(before, after):
    """How far a rate taken between two reads can be off only because neither
    read's instant is known better than its call's length. Small while the
    agent's own path is idle; it grows when that path shares a shaped queue."""
    return (before["span"] + after["span"]) / 2 / (after["at"] - before["at"])


def shaped_bps(before, after, *slots):
    """What the shaper let through the given leaves, in the bits it charges."""
    charged = 0
    for slot in slots:
        moved = leaf_delta(before, after, slot)
        charged += (moved["bytes"] + OAL * moved["frames"]) * 8
    return charged / (after["at"] - before["at"])


def police_counters(text):
    """What a hardware police action reports through `tc -s filter show`: the
    frames its meter saw and how many it dropped.

    The profile counts frames per colour and no bytes, so tc is handed frames
    and drops; a filter and its action each print a stats block, and the larger
    reading is the one carrying the driver's numbers.
    """
    metered = [int(n) for n in re.findall(r"Sent hardware \d+ bytes (\d+) pkt", text)]
    dropped = [int(n) for n in re.findall(r"\(dropped (\d+)", text)]
    return max(metered, default=0), max(dropped, default=0)


def directions(state, *, ingress, proto, src=None, dst=None):
    """The installed directions arriving on `ingress`, by the endpoints of their
    match. Either endpoint may be left open."""
    return [f for f in state["flows"] if f["in"] == ingress and f["proto"] == str(proto)
            and (src is None or f["src"] == src) and (dst is None or f["dst"] == dst)]


async def offload(r, *rules):
    """The flowtable, with the forward-chain rules that offer flows to it.

    Named as the rig's own table, so the rig's teardown is what drains it.
    """
    body = "\n ".join(rules)
    await r.nft(f'''table inet {TABLE} {{
 flowtable fast {{ hook ingress priority 0; devices = {{ {TARGET_LAN_IF}, {TARGET_WAN_IF} }}; flags offload; }}
 chain forward {{ type filter hook forward priority 0; policy accept;
 {body}
 }}
}}''')
    await r.wait(lambda s: s["bindings"] == 2)


def inbound(r, proto, port, mark=None):
    """A rule offering the orchestrator's flow to one LAN port, marked for a
    class. The mark is set on every packet of the flow and the offer comes after
    it in the same rule, so admission always sees it."""
    marking = f"ct mark set {mark:#x} " if mark else ""
    return f"ip saddr {WAN_IP} ip daddr {r.lan_ip} {proto} dport {port} {marking}flow add @fast"


async def tree(r, dev, rate_mbit, leaves):
    """The three levels CEETM has: the port as the root qdisc, one channel under
    it shaped at `rate_mbit`, and one class queue per leaf.

    rate and ceil are equal, so the channel has nothing to borrow and its
    committed rate is its ceiling. Leaves take Tx queue slots in the order they
    are created -- the first inherits the channel's own slot when
    TC_HTB_LEAF_TO_INNER turns the channel into an inner class -- so the slot
    `ethtool -S` reports a leaf under is its position in `leaves`.
    """
    rate = f"{rate_mbit}mbit"
    await r.tc("qdisc", "add", "dev", dev, "root", "handle", "1:", "htb", "offload")
    await r.tc("class", "add", "dev", dev, "parent", "1:", "classid", "1:1",
               "htb", "rate", rate, "ceil", rate)
    for classid, prio in leaves:
        await r.tc("class", "add", "dev", dev, "parent", "1:1", "classid", classid,
                   "htb", "rate", rate, "ceil", rate, "prio", str(prio))
    shown = (await r.tc("class", "show", "dev", dev))["stdout"]
    for classid in ("1:1", *(leaf for leaf, _ in leaves)):
        assert f"class htb {classid} " in shown, (classid, shown)


async def conntrack_clear(r):
    """Every connection between the two hosts, whichever side opened it.

    A mark is sampled once, at admission, so a connection left over from an
    earlier run would carry its old class into this one. Nothing between these
    two hosts outlives the test that opened it, the rig's own echo included."""
    for proto in ("tcp", "udp"):
        for src, dst in ((WAN_IP, r.lan_ip), (r.lan_ip, WAN_IP)):
            await command(r.target, r.session, "conntrack", "-D", "-p", proto,
                          "--orig-src", src, "--orig-dst", dst, check=False)


async def admit(r, port, *, tos=0, destination=None, timeout=20):
    """Echo short bursts to a LAN-side UDP port until both directions of the flow
    are installed, and return the two rows.

    A deadline rather than a round count: an admission that loses the adapter's
    RTNL trylock is declined, and the software path offers the flow again only
    about a second later, while traffic keeps it there, so every attempt sends
    before it looks.
    """
    destination = destination or r.lan_ip
    target = f"{destination}:{port}"
    deadline = time.monotonic() + timeout
    while True:
        await asyncio.to_thread(lockstep, destination, port, 8, tos=tos)
        state = await r.state()
        forward = directions(state, ingress=TARGET_WAN_IF, proto=17, dst=target)
        reverse = directions(state, ingress=TARGET_LAN_IF, proto=17, src=target)
        if forward and reverse:
            assert len(forward) == len(reverse) == 1, (forward, reverse)
            return forward[0], reverse[0]
        if time.monotonic() > deadline:
            pytest.fail(f"{target} was not admitted in both directions: {state}")
        await asyncio.sleep(0.5)


async def captured(r, send):
    """The rig's requests as they arrive on this host's WAN interface, captured
    while the coroutine `send()` makes runs."""
    from scapy.all import IP, UDP, AsyncSniffer

    ready = threading.Event()
    sniffer = AsyncSniffer(iface=r.wan_if, store=True, started_callback=ready.set,
                           filter=f"udp and src host {r.lan_ip} and dst port {DPORT}")
    sniffer.start()
    try:
        assert await asyncio.to_thread(ready.wait, 5), "the WAN capture did not start"
        await send()
    finally:
        packets = sniffer.stop()
    return [p for p in packets if IP in p and UDP in p]


async def reload_adapter(r, *parameters, idle=True):
    """Unload the flowtable adapter and load it again with `parameters`.

    The class decode is boot-immutable -- its parameters are 0444 -- so a case
    that needs a different mask reloads the module, and puts the image's own
    options back the same way. The adapter refuses to leave while anything is
    bound, so a caller that expects a clean reload checks that first.
    """
    if idle:
        state = await r.state()
        assert state["bindings"] == state["entries"] == 0, state
    await console_command(r.console, "rmmod", "ask_flowtable", check=idle, timeout=30)
    await console_command(r.console, "modprobe", "ask_flowtable", *parameters, timeout=30)


@pytest_asyncio.fixture
async def qos(rig):
    """The rig, readied for multi-gigabit transfers and hardware queues.

    A transfer measured at line rate needs full-size frames. The rig's host
    routes carry the ports' own MTU already, but an earlier case that lowered
    a path can leave a smaller MTU cached against the LAN VM at the
    orchestrator, so that route is widened here explicitly. Flows in this
    file's port block are routed rather than masqueraded, so a row's endpoints
    are the hosts'.

    Teardown takes down every tree and clsact qdisc on both ports, then forwards
    an exchange through both: that is the proof each port still transmits once
    CEETM has been switched off again.
    """
    r = rig
    mask = int((await read(r.target, r.session,
                           "/sys/module/ask_flowtable/parameters/qos_mark_mask")).strip())
    if not mask:
        pytest.skip("classification is off in this boot; the QoS cases need "
                    "ask_flowtable.qos_mark_mask=0xf0, which the test image ships")
    shift = (mask & -mask).bit_length() - 1
    r.mark = lambda cq: cq << shift
    wan = Agent("wan", f"http://{os.environ.get('ASK_WAN_IP', '127.0.0.1')}:9110")
    console = Console.target(log_path=str(artifact_dir() / "qos-uart.log"))
    await asyncio.to_thread(console.login, "root", None)
    r.console = console

    async def tc(*argv, check=True):
        """`tc` is not in the agent's argv allowlist, so trees and filters are
        built on the console."""
        return await console_command(console, "tc", *argv, check=check, timeout=30)
    r.tc = tc
    cleanup = []
    printk = None
    try:
        # Every console command is framed by a marker, and a printk landing
        # in the middle of one breaks the framing. Restored last in teardown,
        # after the console's last use.
        printk = " ".join((await read(r.target, r.session, "/proc/sys/kernel/printk")).split()[:4])
        await command(r.target, r.session, "sysctl", "-w", "kernel.printk=1 4 1 7")
        route = json.loads((await command(wan, r.session, "ip", "-j", "route", "show",
                                          "exact", f"{r.lan_ip}/32"))["stdout"])[0]
        await command(wan, r.session, "ip", "route", "replace", f"{r.lan_ip}/32",
                      "via", route["gateway"], "dev", route["dev"], "mtu", "1500")
        cleanup.append((wan, ["ip", "route", "replace", f"{r.lan_ip}/32",
                              "via", route["gateway"], "dev", route["dev"]]))
        for proto in ("tcp", "udp"):
            nat = ["POSTROUTING", "-s", r.lan_ip, "-d", WAN_IP, "-p", proto,
                   "--dport", f"{PORT}:{PORTS_LAST}", "-j", "ACCEPT"]
            await command(r.target, r.session, "iptables", "-t", "nat", "-I", *nat)
            cleanup.append((r.target, ["iptables", "-t", "nat", "-D", *nat]))
        await conntrack_clear(r)
        yield r
    finally:
        failures = []

        async def attempt(step, label):
            try:
                await step
            except Exception as error:
                failures.append(f"{label}: {error!r}")

        await attempt(lan_stop(r), "LAN servers")
        for dev in (TARGET_LAN_IF, TARGET_WAN_IF):
            await attempt(tc("qdisc", "del", "dev", dev, "clsact", check=False), f"{dev} clsact")
            await attempt(tc("qdisc", "del", "dev", dev, "root", check=False), f"{dev} root")
        for agent, argv in reversed(cleanup):
            await attempt(command(agent, r.session, *argv, check=False), " ".join(argv))
        await attempt(conntrack_clear(r), "conntrack")
        try:
            for dev in (TARGET_LAN_IF, TARGET_WAN_IF):
                shown = (await tc("qdisc", "show", "dev", dev))["stdout"]
                assert "htb" not in shown and "clsact" not in shown, (dev, shown)
            # Both ports' egress, after CEETM: the request leaves by the WAN
            # port and the echo by the LAN port.
            await r.exchange(32)
        except (Exception, pytest.fail.Exception) as error:
            failures.append(f"egress after teardown: {error!r}")
        console.close()
        if printk:
            await attempt(command(r.target, r.session, "sysctl", "-w", f"kernel.printk={printk}",
                                  check=False), "printk")
        assert not failures, ("QoS fixture restoration failed", failures)


async def qdisc_shown(r, dev, handle):
    """One qdisc as `tc -j qdisc show` reports it, found by handle."""
    shown = console_json((await r.tc("-j", "qdisc", "show", "dev", dev))["stdout"])
    found = [q for q in shown if q.get("handle") == handle]
    assert len(found) == 1, (handle, shown)
    return found[0]


async def logged(r, text):
    """How many kernel log lines contain `text`. A count, so a case compares
    two reads and a rerun on the same boot starts from wherever the last left."""
    result = await console_command(r.console, "sh", "-c",
                                   f"dmesg | grep -cF {shlex.quote(text)}", check=False)
    return int(result["stdout"].split()[-1])


async def ef_filter(r, dev, verb="add"):
    """The EF filter every DSCP case here uses: codepoint 46 to class 1:11."""
    if verb == "del":
        await r.tc("filter", "del", "dev", dev, "egress", "pref", "1")
        return
    await r.tc("filter", verb, "dev", dev, "egress", "protocol", "ip", "pref", "1",
               "flower", "skip_sw", "ip_tos", f"{EF_TOS:#x}/0xfc",
               "action", "skbedit", "priority", "1:11")


async def readmitted(r, port, old_cookie, *, tos=0, timeout=20):
    """Echo to a LAN-side port until its forward direction is back on an entry
    other than `old_cookie`, and return that row."""
    target = f"{r.lan_ip}:{port}"
    deadline = time.monotonic() + timeout
    while True:
        await asyncio.to_thread(lockstep, r.lan_ip, port, 8, tos=tos)
        rows = directions(await r.state(), ingress=TARGET_WAN_IF, proto=17, dst=target)
        if rows and rows[0]["cookie"] != old_cookie:
            assert len(rows) == 1, rows
            return rows[0]
        if time.monotonic() > deadline:
            pytest.fail(f"{target} was not readmitted after {old_cookie}")
        await asyncio.sleep(0.5)


# ---- traffic that names no leaf ---------------------------------------------

async def dut_ping(r, address, count, interval="0.01"):
    """Ping from the DUT itself, over its console: the gateway's own frames,
    which no flow and no mark describes. Returns (sent, received)."""
    result = await console_command(r.console, "ping", "-c", str(count), "-i", interval,
                                   "-W", "1", address, check=False,
                                   timeout=int(count * float(interval)) + 30)
    match = re.search(r"(\d+) packets transmitted, (\d+) (?:packets )?received",
                      result["stdout"])
    assert match, result["stdout"]
    return int(match.group(1)), int(match.group(2))


async def handshakes(host, port, count, timeout=1.0):
    """Open `count` fresh TCP connections from here and count the ones that
    completed their handshake within `timeout`."""
    completed = 0
    for _ in range(count):
        try:
            _, writer = await asyncio.wait_for(asyncio.open_connection(host, port), timeout)
        except (OSError, asyncio.TimeoutError):
            continue
        completed += 1
        writer.close()
        try:
            await writer.wait_closed()
        except OSError:
            pass
    return completed


async def offered(r):
    """Packets the forward chain's one counting rule has seen. A frame the
    flowtable forwards never reaches the chain, so a count that does not move
    across a burst says the flowtable carried all of it."""
    result = await command(r.target, r.session, "nft", "-j", "list", "chain", "inet",
                           TABLE, "forward")
    counts = [expr["counter"]["packets"]
              for obj in json.loads(result["stdout"])["nftables"] if "rule" in obj
              for expr in obj["rule"]["expr"] if "counter" in expr]
    assert len(counts) == 1, result["stdout"]
    return counts[0]


# ---- the egress a flow was installed with ----------------------------------

async def read_intervals(stream, into):
    """Every interval a `--json-stream` iperf3 reports, with when it arrived
    here. An interval is reported as it ends, so it covers the second before."""
    while line := await stream.readline():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if event.get("event") == "interval":
            into.append((time.monotonic(), event["data"]["sum"]["bits_per_second"]))


async def conntrack_ids(r, port):
    """The ids of the LAN VM's TCP connections to one port on this host."""
    listing = await command(r.target, r.session, "conntrack", "-L", "-p", "tcp",
                            "--orig-src", r.lan_ip, "--orig-dst", WAN_IP,
                            "--dport", str(port), "-o", "id")
    return set(re.findall(r"\bid=(\d+)", listing["stdout"]))
