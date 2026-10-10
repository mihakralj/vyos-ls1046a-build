"""Shared support for mcast e2e."""

from __future__ import annotations

import asyncio
import json
import os
import re

import pytest
import pytest_asyncio
from _flowtable_rig import artifact_dir, console_command, stop_boot_daemon
from _mcast_cpu import cpu_frames, stream_cpu_counters
from _mcast_helpers import (
    arm_bridge_querier,
    kill_parallel_tcpdumps,
    multicast_on,
    read_pcap_count,
    spawn_parallel_tcpdumps,
)
from _topology import (
    LAN_NIC,
    TARGET_LAN_IF,
    TARGET_WAN_IF,
    TopologyStack,
    lan_run_python,
)
from ask_orch.uart import Console

# The serial console and its command wrapper, for the one step that cannot go
# over the agent: see mcast_bridge.


# The bridge the DUT puts its two test ports into. Not `br-lan`: the shipping
# configuration is not ours to disturb, and a test that reused it could not
# tell its own groups from the product's.
BRIDGE = "br_mcast_e2e"
# `fwd` and `lt` are nft keywords and a chain named either fails with a syntax
# error that reads like console corruption; the same caution is worth keeping
# for bridge names that shell out.

MCAST_PORT = int(os.environ.get("ASK_MCAST_E2E_PORT", "47300"))
# Above 1, always. See send_stream_from_vision() and the TTL section of
# docs/flowtable/multicast.md: the parser refuses to classify TTL 0 or 1.
MCAST_TTL = int(os.environ.get("ASK_MCAST_E2E_TTL", "64"))
# Distinct group per case so a stale entry from one cannot satisfy another.
GROUPS_V4 = {
    "v3_include": "239.8.1.1",
    "v2": "239.8.1.2",
    "vlan": "239.8.1.3",
    "rejoin": "239.8.1.4",
    "routed": "239.8.1.5",
    "routed_vlan": "239.8.1.6",
    "routed_bridge": "239.8.1.7",
    "routed_pair": "239.8.1.8",
}
GROUPS_V6 = {
    "mldv2_include": "ff1e::8:1:1",
    "mldv1": "ff1e::8:1:2",
    "routed": "ff1e::8:1:5",
}
# The ISP's IPTV VLAN in the deployment this increment exists for.
IPTV_VID = int(os.environ.get("ASK_MCAST_E2E_VID", "3999"))

# How long a case lets the stream run. Long enough that a software-forwarded
# stream would put thousands of frames through the DUT's CPU, which is what
# the negative oracle measures against.
STREAM_S = 3.0
STREAM_PPS = 500


# ---------------------------------------------------------------- fixtures

@pytest_asyncio.fixture
async def stream_cpu(aiohttp_session, target_agent):
    """The stream's own CPU counters (_mcast_cpu), installed before the case
    learns anything: a ruleset commit later would unconfirm a routed group."""
    async with stream_cpu_counters(target_agent, aiohttp_session, (MCAST_PORT,)):
        yield


@pytest_asyncio.fixture
async def mcast_bridge(aiohttp_session, target_agent):
    """The DUT bridging its WAN and LAN ports, with IGMP/MLD snooping on and a
    querier of its own.

    The querier is not optional. Without one nothing on the segment elicits
    membership reports, the MDB stays empty, and the bridge floods every group
    to every port — which looks exactly like a working test and proves nothing.

    The bridge inherits the WAN address and MAC for its querier and local
    traffic. UART control remains available throughout the topology change.
    """
    await stop_boot_daemon()
    await multicast_on(target_agent, aiohttp_session)
    stack = TopologyStack()
    console = Console.target(log_path=str(artifact_dir() / "mcast-bridge-uart.log"))
    # A fresh boot leaves the console at a login prompt, and every command
    # below would be typed into it as a username. Logging in is idempotent on
    # a console that already has a shell.
    async def _exec(*argv: str):
        return await target_agent.exec_cmd(aiohttp_session, list(argv))

    try:
        await asyncio.to_thread(console.login, "root", None)
        await asyncio.to_thread(console.sync_prompt)
        # Where the management address is *now*. Normally the WAN port, but a
        # run interrupted between the move onto the bridge and its restore
        # leaves it on the bridge, and a fixture that only looked at the port
        # would fail on a tree that is merely untidy rather than tidying it.
        management = None
        for dev in (TARGET_WAN_IF, BRIDGE):
            info = json.loads((await _exec("ip", "-j", "-4", "addr", "show",
                                           "dev", dev))["stdout"] or "[]")
            if not info:
                continue
            management = next((f"{a['local']}/{a['prefixlen']}"
                               for a in info[0]["addr_info"]
                               if a["family"] == "inet"), None)
            if management:
                break
        assert management, (
            f"no IPv4 address on {TARGET_WAN_IF} or {BRIDGE} for the bridge querier; "
            f"this fixture moves that address and cannot start without it")
        routes = json.loads((await _exec("ip", "-j", "route", "show",
                                         "default"))["stdout"] or "[]")
        gateway = next((r["gateway"] for r in routes
                        if r.get("dev") in (TARGET_WAN_IF, BRIDGE)), None)
        # Through the file endpoint rather than `cat`, which the agent's argv
        # allowlist does not carry.
        r = await target_agent.fs_read(
            aiohttp_session, f"/sys/class/net/{TARGET_WAN_IF}/address")
        assert r.get("errno") == 0, f"reading {TARGET_WAN_IF}'s address: {r}"
        mac = bytes.fromhex(r["content_hex"]).decode().strip()

        # Restore a stale bridge before building the test topology.
        for argv in (["ip", "link", "del", BRIDGE],
                     ["ip", "addr", "replace", management, "dev", TARGET_WAN_IF],
                     ["ip", "link", "set", TARGET_WAN_IF, "up"],
                     ["ip", "link", "set", TARGET_LAN_IF, "up"]):
            await console_command(console, *argv, check=False, timeout=30)
        # IGMPv3 and MLDv2 from the first query: a host hears it on the LAN
        # port, and one IGMPv2 query keeps that host's interface in v2 mode
        # for minutes, where it neither reports sources nor sends a BLOCK.
        r = await _exec("ip", "link", "add", "name", BRIDGE, "type", "bridge",
                        "mcast_snooping", "1", "mcast_querier", "1",
                        "mcast_igmp_version", "3", "mcast_mld_version", "2")
        assert r["rc"] == 0, f"bridge add {BRIDGE}: {r}"
        # Register each restore before moving management. Each step has its
        # own deadline; one dead command cannot prevent address restoration.
        undo = [["ip", "link", "del", BRIDGE],
                ["ip", "addr", "replace", management, "dev", TARGET_WAN_IF],
                ["ip", "link", "set", TARGET_WAN_IF, "up"],
                ["ip", "link", "set", TARGET_LAN_IF, "up"]]
        if gateway:
            undo.append(["ip", "route", "replace", "default", "via", gateway,
                         "dev", TARGET_WAN_IF])
        for argv in reversed(undo):
            stack.push(lambda argv=argv: console_command(console, *argv, timeout=30))

        r = await _exec("ip", "link", "set", BRIDGE, "address", mac)
        assert r["rc"] == 0, f"bridge mac {mac}: {r}"
        r = await _exec("ip", "link", "set", BRIDGE, "up")
        assert r["rc"] == 0, f"bridge up: {r}"

        steps = [["ip", "addr", "del", management, "dev", TARGET_WAN_IF],
                 ["ip", "link", "set", TARGET_WAN_IF, "master", BRIDGE],
                 ["ip", "link", "set", TARGET_LAN_IF, "master", BRIDGE],
                 ["ip", "link", "set", TARGET_WAN_IF, "up"],
                 ["ip", "link", "set", TARGET_LAN_IF, "up"],
                 ["ip", "addr", "add", management, "dev", BRIDGE]]
        if gateway:
            steps.append(["ip", "route", "replace", "default", "via", gateway,
                          "dev", BRIDGE])
        for argv in steps:
            await console_command(console, *argv, timeout=30)

        # The querier's startup queries are spaced by
        # multicast_startup_query_interval; without waiting them out the first
        # join can land before snooping is querying and be missed.
        await asyncio.sleep(3.0)
        # And it has to count as a querier at all, in both families, or the
        # bridge floods every group and none is carried in hardware.
        await arm_bridge_querier(_exec, BRIDGE)
        yield BRIDGE
    finally:
        try:
            await stack.teardown("mcast_bridge")
        finally:
            console.close()


# ---------------------------------------------------------------- oracles

async def mdb_reports_offload(target_agent, session, group: str) -> bool:
    """Whether the bridge says a driver took this group's port group on.

    br_switchdev_mdb_complete() sets MDB_PG_FLAGS_OFFLOAD only when the
    switchdev object was handled without error, and `bridge mdb show` prints
    it. Nothing else in the bridge consults that flag, so it is purely a
    report — which is what makes it a clean oracle rather than a mechanism the
    test could accidentally be driving.
    """
    r = await target_agent.exec_cmd(session, ["bridge", "mdb", "show"])
    for line in (r.get("stdout") or "").splitlines():
        if group in line:
            return "offload" in line
    return False


async def flowtable_proc(target_agent, session) -> str:
    """The adapter's diagnostic file, decoded.

    fs_read answers with `content_hex`, because the endpoint is binary-safe
    for the probes that need it. Reading `content` instead returns nothing at
    all, which makes every oracle built on this file quietly pass or quietly
    fail depending on its sense.
    """
    r = await target_agent.fs_read(session, "/proc/cdx_flowtable",
                                   max_bytes=16 << 20)
    assert r.get("errno") == 0, f"/proc/cdx_flowtable: {r}"
    return bytes.fromhex(r["content_hex"]).decode()


async def hardware_has_group(target_agent, session, group: str) -> bool:
    """Whether the adapter's own table holds the group."""
    return group in await flowtable_proc(target_agent, session)


# ------------------------------------------------------------ LAN VM side

def _join_script(group: str, port: int, source: str | None,
                 family: int, seconds: float, igmp_version: int | None,
                 iface: str) -> str:
    """A consumer that joins and then counts what arrives.

    Joining with a real socket rather than `ip maddr` is deliberate: the socket
    option decides which report the kernel emits, and that is the whole
    v2-versus-v3 distinction these cases turn on. IP_ADD_SOURCE_MEMBERSHIP
    produces an INCLUDE report naming the source; IP_ADD_MEMBERSHIP with
    force_igmp_version=2 produces a v2 report with none.

    A routed consumer joins for a different reason: the DUT does not snoop and
    the static MFC entry is what forwards, but the NIC still has to accept the
    group's multicast MAC, and nothing but a join puts it in the filter.
    """
    return f"""
import socket, struct, time, sys

GROUP = {group!r}
PORT = {port}
SOURCE = {source!r}
FAMILY = socket.AF_INET6 if {family} == 6 else socket.AF_INET
SECONDS = {seconds}

if {igmp_version!r} is not None:
    with open('/proc/sys/net/ipv4/conf/{iface}/force_igmp_version', 'w') as f:
        f.write(str({igmp_version!r}))

s = socket.socket(FAMILY, socket.SOCK_DGRAM)
s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
s.bind(('', PORT))

ifindex = socket.if_nametoindex({iface!r})
if FAMILY == socket.AF_INET6:
    mreq = socket.inet_pton(socket.AF_INET6, GROUP) + struct.pack('@I', ifindex)
    s.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_JOIN_GROUP, mreq)
elif SOURCE is not None:
    # IP_ADD_SOURCE_MEMBERSHIP: imr_multiaddr, imr_interface, imr_sourceaddr.
    mreq = (socket.inet_aton(GROUP) + socket.inet_aton('0.0.0.0')
            + socket.inet_aton(SOURCE))
    s.setsockopt(socket.IPPROTO_IP, 39, mreq)   # IP_ADD_SOURCE_MEMBERSHIP
else:
    # ip_mreqn, not ip_mreq. The shorter form names the interface by
    # *address*, and 0.0.0.0 there means "whichever one the route to the group
    # resolves to" -- the default route's, which is never a VLAN
    # sub-interface. A consumer asked to join on one would silently be joined
    # on its parent instead, receive nothing however correct the offload is,
    # and the frames would be on the wire the whole time.
    mreq = (socket.inet_aton(GROUP) + socket.inet_aton('0.0.0.0')
            + struct.pack('@i', ifindex))
    s.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)

print('JOINED', flush=True)
s.settimeout(0.5)
seen = 0
deadline = time.monotonic() + SECONDS
while time.monotonic() < deadline:
    try:
        s.recv(2048)
        seen += 1
    except socket.timeout:
        pass
print('RECEIVED', seen, flush=True)
"""


async def lan_join_and_count(lan, *, group: str, source: str | None,
                             family: int, seconds: float,
                             igmp_version: int | None, label: str,
                             iface: str = LAN_NIC) -> int:
    script = _join_script(group, MCAST_PORT, source, family, seconds,
                          igmp_version, iface)
    r = await lan_run_python(lan, script, label=label, timeout=seconds + 30)
    assert "JOINED" in r.stdout, f"LAN join failed: {r.stdout!r}"
    for line in r.stdout.splitlines():
        if line.startswith("RECEIVED"):
            return int(line.split()[1])
    raise AssertionError(f"no RECEIVED line from LAN: {r.stdout!r}")


# ------------------------------------------------------------- WAN source

def send_stream_from_vision(group: str, family: int, seconds: float,
                            pps: int, ttl: int = MCAST_TTL) -> None:
    """Send a multicast UDP stream from this host onto the DUT-facing wire.

    L2 sendp with an explicit iface, because a multicast destination has no
    host route and scapy's L3 send() would pick whatever default outbound NIC
    the kernel finds — which on this orchestrator is not the DUT-facing one.
    Pinning the source address matters just as much: the classifier key is an
    exact (S,G), so a source scapy chose for itself would miss the entry and
    the frame would punt unreplicated. That cost a debugging session once
    already; see commit 73144ff.
    """
    import time

    from scapy.all import (  # type: ignore[import]
        IP,
        UDP,
        Ether,
        IPv6,
        Raw,
        sendp,
    )

    iface = os.environ.get("ASK_WAN_INJECT_IF", "")
    src = wan_source_address(family)
    # Explicitly, and never inherited. The FMC soft parser ends the parse
    # before classification for TTL 0 or 1, so a TTL-1 stream is never matched
    # in hardware however correct the entry is -- and a plain UDP multicast
    # socket sends TTL 1 by default. Getting this wrong makes a working
    # offload look completely dead; it cost a rig session once.
    if family == 6:
        # IPv6 multicast MAC: 33:33:<low 32 bits of the group>.
        import socket as _socket
        raw = _socket.inet_pton(_socket.AF_INET6, group)
        mac = "33:33:" + ":".join(f"{b:02x}" for b in raw[12:16])
        layer = IPv6(src=src, dst=group, hlim=ttl)
    else:
        o = [int(b) for b in group.split(".")]
        mac = "01:00:5e:%02x:%02x:%02x" % (o[1] & 0x7F, o[2], o[3])
        layer = IP(src=src, dst=group, ttl=ttl)

    frame = (Ether(dst=mac) / layer / UDP(sport=MCAST_PORT, dport=MCAST_PORT)
             / Raw(b"x" * 512))
    total = int(seconds * pps)
    sendp(frame, iface=iface, count=total, inter=1.0 / pps, verbose=False)


def wan_source_address(family: int) -> str:
    if family == 6:
        return os.environ.get("ASK_WAN_IPV6", "fc00:beef::99")
    # Derived from br0 on THIS host; the conftest default is stale.
    return os.environ.get("ASK_WAN_IPERF_IP", "")


# ------------------------------------------------------------------- cases

async def run_bridged_case(aiohttp_session, target_agent, lan, *, group: str,
                           family: int, source: str | None,
                           igmp_version: int | None, label: str):
    """One bridged case, end to end, with all three oracles."""
    # A pre-test drain: background multicast on a shared segment must not be
    # counted as this group's.
    await asyncio.sleep(2.0)

    baseline_hw = await hardware_has_group(target_agent, aiohttp_session, group)
    assert not baseline_hw, (
        f"{group} already in the hardware table before the test joined it — "
        f"a stale entry from an earlier run would satisfy every oracle here"
    )

    joiner = asyncio.create_task(lan_join_and_count(
        lan, group=group, source=source, family=family,
        seconds=STREAM_S + 4.0, igmp_version=igmp_version, label=label,
    ))
    # Let the report reach the bridge and the learner act on it.
    await asyncio.sleep(2.0)

    # On the ingress port, not on the bridge device: the port's hook sees a
    # frame before the bridge does, and a replicated one not at all.
    counted = await cpu_frames(target_agent, aiohttp_session, TARGET_WAN_IF)
    await asyncio.to_thread(
        send_stream_from_vision, group, family, STREAM_S, STREAM_PPS,
    )

    received = await joiner
    cpu_seen = await cpu_frames(target_agent, aiohttp_session, TARGET_WAN_IF) - counted
    offloaded = await mdb_reports_offload(target_agent, aiohttp_session, group)
    in_hw = await hardware_has_group(target_agent, aiohttp_session, group)

    sent = int(STREAM_S * STREAM_PPS)
    # Forwarding first: without it the rest is describing a broken path.
    assert received > sent * 0.5, (
        f"{group}: consumer received {received} of {sent} — the stream is not "
        f"reaching the LAN VM at all, so nothing below is meaningful"
    )
    assert in_hw, f"{group}: forwarded, but no hardware entry — software path"
    assert offloaded, (
        f"{group}: installed in hardware, but `bridge mdb show` does not "
        f"report offload. The flag is set when the adapter accepts the "
        f"switchdev object, which is strictly earlier than the install, so "
        f"an entry present without it means the group reached hardware by "
        f"some path other than the membership it was supposed to come from"
    )
    # The discriminating one.
    assert cpu_seen < sent * 0.05, (
        f"{group}: {cpu_seen} of the {sent} frames sent reached the DUT's CPU "
        f"on {TARGET_WAN_IF}. A hardware-replicated frame never reaches the "
        f"CPU, so the bridge is still flooding this group in software "
        f"whatever the table says"
    )


# IPTV tagged on the way in is test_flowtable_service_multicast_bridge's
# tagged_ingress case, and a consumer leaving while another keeps watching
# is test_flowtable_service_multicast_leave's bridged_leave.


# ------------------------------------------------------- routed multicast
#
# The second learner, against the same encoder. ipmr's MFC already carries an
# (S,G), an iif and a replication list, so nothing here is learned from
# traffic: smcroute writes the entry and the group is either in hardware a
# moment later or /proc says why not. See docs/flowtable/multicast-routed.md.
#
# The DUT routes rather than bridges in these cases, which is its shipping
# configuration -- eth4 is the WAN and eth3 the LAN at
# 192.168.1.1/24, both up with forwarding on, from S40gateway-setup. Nothing
# has to be built for the plain case but the MFC entry itself.
#
# A fourth oracle joins the three above, and it is the one only routing can
# satisfy: the replicas arriving at the LAN VM carry the DUT's own egress MAC
# as their source and a TTL one lower than the sender's. A bridge would have
# forwarded the frame with the sender's MAC and TTL untouched, so this is what
# tells "the FMAN routed it" from "the FMAN bridged it".

SMCROUTE_CONF = "/tmp/ask_mroute.conf"
# The bridge for the case whose oif is one. Not br-lan, and not the bridge the
# bridged cases build: those enslave the WAN port too, which would make the
# ingress a bridge port and the whole group ineligible.
MROUTE_BRIDGE = "br_mroute_e2e"
# An address of the bridge's own, locally administered, so a copy sent with
# it cannot be mistaken for one sent with its port's.
MROUTE_BRIDGE_MAC = "02:a5:19:10:02:90"


async def _exec(target_agent, session, *argv, check=True, timeout_ms=10000):
    r = await target_agent.exec_cmd(session, list(argv), timeout_ms=timeout_ms)
    if check:
        assert r["rc"] == 0, f"{' '.join(argv)}: {r}"
    return r


async def dut_mac(target_agent, session, iface: str) -> str:
    r = await _exec(target_agent, session, "ip", "-br", "link", "show", iface)
    fields = (r.get("stdout") or "").split()
    assert len(fields) >= 3, f"no MAC for {iface}: {r}"
    return fields[2].lower()


async def mroute_line(target_agent, session, family: int, source: str,
                      group: str) -> tuple[str, int]:
    """The kernel's own view of one MFC entry: its `ip -s mroute` line and the
    packet count on the line below it.

    Both are standard-tool surfaces rather than anything ASK-private, which is
    the point of the fold: `offload` appears because the learner set
    MFC_OFFLOAD, and the counters are the classifier's, restated into the units
    ip_mr_forward() would have counted in.
    """
    argv = ["ip", "-s"] + (["-6"] if family == 6 else []) + ["mroute", "show"]
    r = await _exec(target_agent, session, *argv)
    lines = (r.get("stdout") or "").splitlines()
    for i, line in enumerate(lines):
        if source in line and group in line:
            packets = 0
            if i + 1 < len(lines):
                m = re.search(r"(\d+)\s+packets", lines[i + 1])
                if m:
                    packets = int(m.group(1))
            return line, packets
    return "", 0


async def mroute_proc_row(target_agent, session, group: str) -> str:
    for line in (await flowtable_proc(target_agent, session)).splitlines():
        if line.startswith("mroute ") and f"group={group} " in line:
            return line
    return ""


def proc_field(row: str, field: str) -> str:
    """One `field=value` of a /proc/cdx_flowtable row."""
    m = re.search(rf"(?:^| ){field}=(\S+)", row)
    assert m, f"no {field}= in {row!r}"
    return m.group(1)


@pytest_asyncio.fixture
async def smcrouted(aiohttp_session, target_agent):
    """A running smcrouted with a VIF on exactly the interfaces a case names.

    `-N` rather than the default, which enables every multicast-capable
    interface it can find: a VIF set that depends on what else the image
    happened to bring up is not a fixture, and a stray VIF changes the index
    every threshold in the MFC is expressed against.

    Yields a callable that (re)starts the daemon over a given interface list;
    a case that builds a VLAN device or a bridge has to do so before the
    daemon starts, because smcroute matches a phyint by name at startup.
    """
    started: list[bool] = []
    await stop_boot_daemon()
    await multicast_on(target_agent, aiohttp_session)

    async def start(ifaces: list[str]):
        if started:
            await _exec(target_agent, aiohttp_session, "killall", "smcrouted",
                        check=False)
            await asyncio.sleep(1.0)
            started.clear()
        conf = "".join(f"phyint {i} enable\n" for i in ifaces)
        w = await target_agent.fs_write(aiohttp_session, SMCROUTE_CONF, conf)
        assert w.get("errno", 0) == 0, f"writing {SMCROUTE_CONF}: {w}"
        await _exec(target_agent, aiohttp_session, "smcrouted", "-N",
                    "-f", SMCROUTE_CONF, "-l", "notice")
        started.append(True)
        # MRT_INIT, the VIF adds and the registration dump all happen inside
        # the daemon's first second; the adapter's own worker follows them.
        await asyncio.sleep(2.0)

    try:
        yield start
    finally:
        await _exec(target_agent, aiohttp_session, "killall", "smcrouted",
                    check=False)
        await asyncio.sleep(1.0)


@pytest_asyncio.fixture
async def mroute_lan_bridge(aiohttp_session, target_agent):
    """A bridge over the DUT's LAN port alone, carrying its address.

    The oif is then a bridge with one CDX port and snooping off, which is the
    br_flood() arm of the listener walk. The WAN port stays out of it: an
    ingress that is a bridge port is refused, and rightly -- its frames go to
    the bridge's rx handler and never reach a VIF above it.
    """
    stack = TopologyStack()
    try:
        await _exec(target_agent, aiohttp_session, "ip", "link", "del",
                    MROUTE_BRIDGE, check=False)
        await _exec(target_agent, aiohttp_session, "ip", "link", "add", "name",
                    MROUTE_BRIDGE, "type", "bridge", "mcast_snooping", "0")

        # Separate callbacks keep address restoration independent of a
        # failed detach/delete. The stack executes these in reverse order.
        for argv in reversed([
            ["ip", "link", "set", TARGET_LAN_IF, "nomaster"],
            ["ip", "link", "del", MROUTE_BRIDGE],
            ["ip", "addr", "replace", "192.168.1.1/24", "dev", TARGET_LAN_IF],
            ["ip", "link", "set", TARGET_LAN_IF, "up"],
        ]):
            stack.push(lambda args=argv: _exec(target_agent, aiohttp_session, *args))

        await _exec(target_agent, aiohttp_session, "ip", "addr", "del",
                    "192.168.1.1/24", "dev", TARGET_LAN_IF)
        await _exec(target_agent, aiohttp_session, "ip", "link", "set",
                    TARGET_LAN_IF, "master", MROUTE_BRIDGE)
        await _exec(target_agent, aiohttp_session, "ip", "addr", "replace",
                    "192.168.1.1/24", "dev", MROUTE_BRIDGE)
        await _exec(target_agent, aiohttp_session, "ip", "link", "set",
                    MROUTE_BRIDGE, "up")
        await asyncio.sleep(1.0)
        yield MROUTE_BRIDGE
    finally:
        await stack.teardown("mroute_lan_bridge")


async def installed_by_traffic(target_agent, aiohttp_session, group: str,
                               family: int, timeout: float = 15.0) -> str:
    """Short bursts of the stream until the routed row says installed, and
    that row.

    A routed group is carried only once Linux has been seen forwarding a copy
    of it to every oif, so a route with no traffic yet waits in software as
    pending-confirm. The entry also appears a moment after the route does --
    the FIB chain fires under RTNL and the adapter's worker installs outside
    it -- which the retries cover as well."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while True:
        row = await mroute_proc_row(target_agent, aiohttp_session, group)
        if "state=installed" in row or loop.time() > deadline:
            return row
        await asyncio.to_thread(send_stream_from_vision, group, family, 0.2, 50)
        await asyncio.sleep(0.3)


async def run_routed_case(aiohttp_session, target_agent, lan, *, group: str,
                          family: int, oif: str, lan_iface: str, label: str,
                          smcrouted, expire: bool = False):
    """One routed case, end to end.

    Six oracles: `ip mroute show` reports offload, /proc says installed, the
    DUT's CPU never sees the stream, the consumer receives it, the replicas
    carry the router's own framing, and `ip -s mroute` shows the hardware's
    count where the software counter is zero. The last two are what a bridged
    case cannot produce. The router's framing is the oif's: ipmr builds each
    copy's header on the device it sends it through, and a VLAN device or a
    bridge passes it on, so the source is the oif's own address -- which the
    hardware has to write too, whatever port the copy leaves by.
    """
    source = wan_source_address(family)
    sent = int(STREAM_S * STREAM_PPS)
    capfile = f"/tmp/ask_mroute_{label}.pcap"

    await smcrouted([TARGET_WAN_IF, oif])

    assert not await mroute_proc_row(target_agent, aiohttp_session, group), (
        f"{group} already has a routed row before this case programmed it — "
        f"a stale entry from an earlier run would satisfy every oracle here"
    )

    await _exec(target_agent, aiohttp_session,
                "smcroutectl", "add", TARGET_WAN_IF, source, group, oif)
    row = await installed_by_traffic(target_agent, aiohttp_session, group, family)
    assert "state=installed" in row, (
        f"{group}: the routed learner did not install it. /proc row: "
        f"{row or '(absent)'}. A refused-* state names which clause of the "
        f"contract turned it down; an absent row means the MFC event never "
        f"reached ft_mr_fib_event() at all"
    )

    _, before = await mroute_line(target_agent, aiohttp_session, family,
                                  source, group)

    # Two streams, one oracle each, because the LAN VM's console is a single
    # channel and the two LAN-side observers cannot share it. The socket
    # consumer runs a staged Python script over that console for the whole
    # window; spawning a capture in the middle of it writes into the same
    # line discipline, and what the consumer counts afterwards is no longer
    # about this group. Measured rather than reasoned: the same case with the
    # capture removed goes from 0 of 1500 delivered to 1500 of 1500, with the
    # classifier counting both runs.
    joiner = asyncio.create_task(lan_join_and_count(
        lan, group=group, source=None, family=family,
        seconds=STREAM_S + 4.0, igmp_version=None, label=label,
        iface=lan_iface,
    ))
    await asyncio.sleep(2.0)
    counted = await cpu_frames(target_agent, aiohttp_session, TARGET_WAN_IF)
    await asyncio.to_thread(
        send_stream_from_vision, group, family, STREAM_S, STREAM_PPS,
    )
    received = await joiner
    cpu_seen = await cpu_frames(target_agent, aiohttp_session, TARGET_WAN_IF) - counted

    # Second window: the console is idle now, so the capture has it to itself.
    # This is the one that answers what the replicas look like on the wire.
    spawn_parallel_tcpdumps(lan, [lan_iface], [capfile],
                            f"udp port {MCAST_PORT}")
    await asyncio.to_thread(
        send_stream_from_vision, group, family, STREAM_S, STREAM_PPS,
    )
    kill_parallel_tcpdumps(lan, [lan_iface])
    await asyncio.sleep(0.2)

    # The /proc read first, and not incidentally: the counter fold runs on a
    # five-second timer *and* on every read of that file, so reading it is what
    # makes `ip -s mroute` exact rather than up to one interval stale. Asking
    # the kernel first reports whatever the last timer happened to catch, which
    # on a three-second stream is most of it and not all.
    row = await mroute_proc_row(target_agent, aiohttp_session, group)
    line, packets = await mroute_line(target_agent, aiohttp_session, family,
                                      source, group)
    captured = read_pcap_count(lan, capfile)
    headers = lan.run(f"tcpdump -r {capfile} -nn -e -v -c 2 2>&1",
                      timeout=15).stdout
    mac = await dut_mac(target_agent, aiohttp_session, oif)
    lan.run(f"rm -f {capfile}", timeout=5)

    # Forwarding first: without it the rest describes a broken path.
    assert received > sent * 0.95, (
        f"{group}: consumer received {received} of {sent} — a routed group is "
        f"forwarded by the kernel until the offload takes it, so anything "
        f"below 95% is loss rather than a learning window"
    )
    assert "state=installed" in row, (
        f"{group}: installed before the stream and {row!r} after it — the "
        f"group was retired while its own traffic was running")
    assert "offload" in line, (
        f"{group}: /proc says installed but `ip -s mroute` does not report "
        f"offload: {line!r}. MFC_OFFLOAD is set by the learner after a "
        f"successful install, so the two disagreeing means the flag was "
        f"never written or was cleared behind the entry's back")
    assert cpu_seen < sent * 0.05, (
        f"{group}: {cpu_seen} of the {sent} frames sent reached the DUT's CPU "
        f"on {TARGET_WAN_IF}. A hardware-replicated frame never reaches the "
        f"CPU, so ipmr is still forwarding this group in software whatever "
        f"the table says")
    assert packets - before > 2 * sent * 0.9, (
        f"{group}: `ip -s mroute` moved by {packets - before} over two "
        f"streams of {sent}. The software counter is zero for an offloaded "
        f"entry, so this is the classifier's own count folded into the "
        f"kernel's -- a flat counter with the stream delivered means the fold "
        f"never ran")
    assert captured > sent * 0.95, (
        f"{group}: the LAN capture saw {captured} of {sent}")
    # The oracle only routing can satisfy.
    ttl = "hlim 63" if family == 6 else "ttl 63"
    assert ttl in headers, (
        f"{group}: replicas do not carry {ttl}, so nothing decremented the "
        f"header — the frame was bridged rather than routed. Headers: "
        f"{headers[:400]!r}")
    assert mac in headers.lower(), (
        f"{group}: replicas do not carry the oif {oif}'s address {mac} as "
        f"their Ethernet source, which is what ipmr sends them with, so the "
        f"listener entry did not rebuild the L2 header as Linux would. "
        f"Headers: {headers[:400]!r}")

    if expire:
        # RFC 1812 §5.3.1: a datagram that would leave with TTL 0 is not
        # forwarded. The soft parser hands TTL <= 1 to Linux unclassified, and
        # ipmr's threshold of 1 drops it -- so nothing reaches the listener.
        joiner = asyncio.create_task(lan_join_and_count(
            lan, group=group, source=None, family=family,
            seconds=STREAM_S + 4.0, igmp_version=None, label=label + "_ttl1",
            iface=lan_iface,
        ))
        await asyncio.sleep(2.0)
        await asyncio.to_thread(send_stream_from_vision, group, family,
                                STREAM_S, STREAM_PPS, 1)
        expired = await joiner
        assert expired == 0, f"{group}: {expired} TTL-1 frames were routed to the listener"

    # Teardown is an assertion too: the entry going has to take the hardware
    # group and the kernel's flag with it.
    await _exec(target_agent, aiohttp_session,
                "smcroutectl", "remove", TARGET_WAN_IF, source, group)
    await asyncio.sleep(2.0)
    assert not await mroute_proc_row(target_agent, aiohttp_session, group), (
        f"{group}: the routed row outlived its MFC entry")
    line, _ = await mroute_line(target_agent, aiohttp_session, family, source,
                                group)
    assert not line, f"{group}: the MFC entry outlived its removal: {line!r}"
