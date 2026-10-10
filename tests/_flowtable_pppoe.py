"""Shared support for flowtable pppoe."""

from __future__ import annotations

import asyncio
import json
import os
import pathlib
import re
import socket
import subprocess
import time

import _flowtable_rig as ft
import pytest
import pytest_asyncio
from _flowtable_rig import (
    DPORT,
    SPORT,
    Echo,
    Rig,
    artifact_dir,
    assert_undisturbed,
    command,
    console_command,
    console_python,
    read,
)
from _gated_tcp import GatedTcp
from _topology import (
    DUT_IPV6_LAN,
    LAN_IPV6,
    LAN_NIC,
    PPPOE_IPV6_LOCAL,
    PPPOE_IPV6_REMOTE,
    TARGET_LAN_IF,
    TARGET_WAN_IF,
    VLAN_ID_PPPOE_WAN,
    TopologyStack,
    dut_vlan_subif,
    lan_run,
    lan_vlan_subif,
)
from ask_orch.client import Agent
from ask_orch.commands import console_json
from ask_orch.counters import kernel_tx_packets
from ask_orch.uart import Console

SERVER_IF = os.environ.get("ASK_PPPOE_SERVER_IF", "askwan3900")
WAN_VID = int(os.environ.get("ASK_FLOWTABLE_PPPOE_VID", str(VLAN_ID_PPPOE_WAN)))
# Claimed in _topology.py's VLAN ID conventions block.
LAN_VID = int(os.environ.get("ASK_FLOWTABLE_PPPOE_LAN_VID", "276"))

# The inner addresses the session negotiates. 10.98.0.x deliberately avoids
# 10.99.0.x, where the IPsec bench puts an address on the orchestrator's lo.
INNER_LOCAL = os.environ.get("ASK_PPPOE_INNER_LOCAL", "10.98.0.1")
INNER_REMOTE = os.environ.get("ASK_PPPOE_INNER_REMOTE", "10.98.0.2")
PPPOE_USER, PPPOE_SECRET = "ask-test", "ask-test-secret"

# The same two ends of the session in IPv6, claimed in _topology.py. IPV6CP
# negotiates interface identifiers and forms link-local addresses from them; a
# global address it does not assign, so the fixture puts one on each end.
INNER_LOCAL6 = PPPOE_IPV6_LOCAL
INNER_REMOTE6 = PPPOE_IPV6_REMOTE
# Distinct from the IPv4 cases' ports. The fixture's conntrack clear is
# IPv4-shaped by construction, so a v6 connection left behind by an earlier run
# would otherwise be readmitted into this one's measurement.
SPORT6 = int(os.environ.get("ASK_FLOWTABLE_PPPOE_SPORT6", "48280"))
DPORT6 = int(os.environ.get("ASK_FLOWTABLE_PPPOE_DPORT6", "48281"))

# Not derived from either VLAN id: an id can exceed an octet, and a subnet
# built out of one silently aliases as soon as it does.
TAGGED_SUBNET, DUT_TAGGED_ADDR, LAN_TAGGED_ADDR = "172.29.76.0/24", "172.29.76.1", "172.29.76.2"
# Reachable only because the orchestrator routes it down the session, so it
# cannot be confused with any address the bench carries of its own.
SNAT_ADDR = "172.29.76.9"
NAT_TABLE = "ask_pppoe_nat"

# The session's MTU: what both ends configure (mtu/mru below), the most a
# 1500-byte device carries under the eight-byte PPPoE header. pppd takes the
# smaller of it and the peer's MRU, and the concentrator clamps its MRU to its
# own device's MTU less eight, so a short server device lowers the session
# (_server_start() refuses one).
SESSION_MTU = 1492

# Seconds between the LCP echo requests each end of the session sends. Both
# ends are configured from this, and it bounds what the session exchanges on
# its own while a case measures around it.
LCP_ECHO_INTERVAL = 5

# The untagged flow the session-record case runs beside the session: the LAN
# VM to the WAN host's own address on the bare WAN port, crossing neither the
# session nor the tag it stands on. Ports of its own, so its conntrack is never
# the session flow's.
UNTAGGED_SPORT = int(os.environ.get("ASK_FLOWTABLE_PPPOE_UNTAGGED_SPORT", "48284"))
UNTAGGED_DPORT = UNTAGGED_SPORT + 1
UNTAGGED_COUNT = 10000
# That host's address, read at import: pppoe_rig rebinds the offload module's
# endpoint to the session's inner address for the length of each case.
WAN_ENDPOINT = ft.WAN_IP

# The QoS case's two flows: one marked into a class, which the port has to find
# again after the session scrubbed its conntrack, and one unmarked, which
# saturates the shaped channel beside it. The channel is slow enough that the
# CPU forwards several times its rate through the session; the class the mark
# names is the prio 1 leaf's, class queue 6.
PORT_QOS_VOICE = int(os.environ.get("ASK_FLOWTABLE_PPPOE_QOS_PORT", "48290"))
PORT_QOS_BULK = PORT_QOS_VOICE + 1
QOS_TABLE = "ask_pppoe_qos"
# The QoS case forwards everything in software -- no flowtable is bound --
# and on the test image (KASAN, lockdep) one core saturates near 6,000
# frames a second: at 20 and 60 it pegged, delaying the marked flow's echoes
# past their timeout although every one of them had left on its leaf. Three
# times the channel still fills the unclassified queue.
QOS_RATE_MBIT, QOS_BULK_MBIT = 5, 15
QOS_VOICE_PRIO, QOS_VOICE_CQ = 1, 6
QOS_DATAGRAM, QOS_COUNT = 1200, 200
QOS_BULK_SCRIPT = "/tmp/ask_pppoe_qos_bulk.py"
QOS_BULK_PID = "/tmp/ask_pppoe_qos_bulk.pid"

SERVER_OPTS = "/tmp/ask-flowtable-pppoe-server.opt"
SERVER_SECRETS = "/tmp/ask-flowtable-pppoe-secrets"
DUT_PEER = "/tmp/ask-flowtable-pppoe-peer"
DUT_SECRETS = "/tmp/ask-flowtable-pppoe-secrets"
DUT_LOG = "/tmp/ask-flowtable-pppoe-pppd.log"
DUT_PID = "/tmp/ask-flowtable-pppoe-pppd.pid"


class SourceEcho(Echo):
    """Echo that also records where each datagram came from. Under SNAT that is
    the translated endpoint, which is the wire-level proof of the rewrite."""

    def __init__(self):
        super().__init__()
        self.sources = set()

    def datagram_received(self, data, addr):
        self.sources.add((addr[0], addr[1]))
        super().datagram_received(data, addr)


def _session_row(state, identity):
    """The statistics record the adapter holds for one session.

    Keyed on the same `id@concentrator` string the flow rows carry, which is
    what lets the two be joined. A session always has a row once a direction
    naming it is admitted; whether it has a firmware record is what `slot`
    says, because the pool is four deep and shared and running out has to be
    visible rather than silent.
    """
    wanted = _session_text(identity)
    matching = [s for s in state["sessions"] if s["pppoe"] == wanted]
    assert len(matching) == 1, (wanted, state["sessions"])
    return matching[0]


def _direction(flows, source, destination):
    """One installed direction, named by the endpoints of its match."""
    matching = [f for f in flows if f["src"].startswith(source + ":")
                and f["dst"].startswith(destination + ":")]
    assert len(matching) == 1, (source, destination, flows)
    return matching[0]


# ---- the session ---------------------------------------------------------

def _server_start(ipv6=False, auth="pap"):
    """The access concentrator, on the orchestrator's standing tagged device.

    Run as a plain subprocess rather than through the WAN agent: pppoe-server
    is deliberately not in the agent's exec allowlist, and the orchestrator is
    the machine this test already runs on.

    `ipv6` enables IPV6CP on this end. It is enabled on both ends or neither:
    the peer that is not asked for it rejects the protocol, and the side that
    wanted it gives up rather than failing, so a session would come up carrying
    IPv4 only and the case that needs v6 would fail as a routing problem.
    """
    server = "/usr/sbin/pppoe-server"
    if not os.access(server, os.X_OK):
        pytest.skip(f"{server} not installed on the orchestrator")
    # The concentrator clamps its MRU to the device's MTU less the 8-byte
    # session header, and the DUT's link then takes the smaller value. A
    # short device would leave the session below SESSION_MTU, and every case
    # asserting that MTU would fail as an admission or offload problem. A
    # VLAN keeps a lowered MTU when its parent's MTU drops and comes back.
    server_mtu = int(pathlib.Path(f"/sys/class/net/{SERVER_IF}/mtu").read_text())
    assert server_mtu >= SESSION_MTU + 8, \
        f"{SERVER_IF} MTU {server_mtu} cannot carry a {SESSION_MTU}-byte session"
    _server_clear(server)
    pathlib.Path(SERVER_SECRETS).write_text(
        f'"{PPPOE_USER}"   *   "{PPPOE_SECRET}"   *\n')
    os.chmod(SERVER_SECRETS, 0o600)
    pathlib.Path(SERVER_OPTS).write_text(
        # require-pap/chap: the DUT authenticates to us, so a session that comes
        # up has completed discovery and authentication rather than merely LCP.
        f"require-{auth}\n"
        f"pap-secrets {SERVER_SECRETS}\n"
        f"chap-secrets {SERVER_SECRETS}\n"
        f"mtu {SESSION_MTU}\n"
        f"mru {SESSION_MTU}\n"
        "nodefaultroute\n"
        f"lcp-echo-interval {LCP_ECHO_INTERVAL}\n"
        "lcp-echo-failure 3\n"
        "noipdefault\n"
        + ("+ipv6\n" if ipv6 else ""))
    proc = subprocess.Popen(
        # -k: kernel-mode PPPoE. -F: stay in the foreground, so this handle is
        # the server rather than a wrapper that has already exited, which is
        # what makes teardown PID-targeted -- a pkill pattern broad enough to
        # find a daemonized one also matches the shell running it.
        [server, "-I", SERVER_IF, "-L", INNER_LOCAL, "-R", INNER_REMOTE,
         "-N", "4", "-O", SERVER_OPTS, "-k", "-F"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    return proc


def _server_clear(server):
    """Stop any concentrator a run that never reached its teardown left on
    SERVER_IF. Every one there answers the DUT's discovery, so the session
    could otherwise come up on a stale server started with other options --
    one without IPv6CP, say. Matched by exact argv from /proc, not a pkill
    pattern, which would also match the shell running it."""
    for entry in pathlib.Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        try:
            argv = (entry / "cmdline").read_bytes().split(b"\0")
        except OSError:
            continue
        argv = [a.decode(errors="replace") for a in argv if a]
        if argv[:1] == [server] and argv[1:3] == ["-I", SERVER_IF]:
            pid = int(entry.name)
            os.kill(pid, 15)
            deadline = time.monotonic() + 5
            while pathlib.Path(f"/proc/{pid}").exists() and time.monotonic() < deadline:
                time.sleep(0.1)
    _sessions_clear()


def _running(pid):
    """Alive and not a zombie: a session pppd whose server has not reaped it
    yet has already exited."""
    try:
        stat = pathlib.Path(f"/proc/{pid}/stat").read_text()
    except OSError:
        return False
    return stat.rsplit(")", 1)[1].split()[0] != "Z"


def _session_pids():
    """The concentrator's per-session pppd processes on SERVER_IF, by the
    argv pppoe-server execs them with: argv[0] is a bare "pppd", then the
    plugin and the interface it serves."""
    pids = []
    for entry in pathlib.Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        try:
            argv = [a.decode(errors="replace")
                    for a in (entry / "cmdline").read_bytes().split(b"\0") if a]
        except OSError:
            continue
        if (argv and os.path.basename(argv[0]) == "pppd" and argv[1:2] == ["plugin"]
                and f"nic-{SERVER_IF}" in argv and _running(int(entry.name))):
            pids.append(int(entry.name))
    return pids


def _sessions_clear():
    """Wait out the session pppds a stopped concentrator leaves behind.

    pppoe-server signals them on its way out but does not wait. One still
    exiting holds its session's kernel socket, so a new server that hands the
    DUT the same session id fails to connect its own (EALREADY), and the
    PADT the old one sends on exit then hangs up the DUT's new session."""
    pids = _session_pids()
    for sig, wait in ((15, 5), (9, 5)):
        for pid in pids:
            try:
                os.kill(pid, sig)
            except ProcessLookupError:
                pass
        deadline = time.monotonic() + wait
        while (pids := [p for p in pids if _running(p)]) and time.monotonic() < deadline:
            time.sleep(0.1)
        if not pids:
            return
    raise AssertionError(f"session pppd {pids} outlived SIGKILL")


def _server_stop(proc):
    """By PID. A bare pkill pattern matches the shell running it."""
    if proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)
    _sessions_clear()
    for path in (SERVER_OPTS, SERVER_SECRETS):
        try:
            os.unlink(path)
        except FileNotFoundError:
            pass


async def _dial(console, lower, ipv6=False):
    """Start pppd on the DUT over `lower` and wait for the session.

    pppd is not in the agent's exec allowlist, so the whole of it goes over the
    console. The config files are staged through console_python rather than a
    heredoc: the staging is checksum-verified, which a heredoc over a
    115200-baud line is not.
    """
    peer = (
        # plugin pppoe.so is the in-kernel PPPoE channel; the device named on
        # the next line is the one it dials over, which here is the tagged
        # device the concentrator sits behind.
        "plugin pppoe.so\n"
        f"{lower}\n"
        f"name {PPPOE_USER}\n"
        # noauth: we do not demand the concentrator authenticate back.
        # nodefaultroute: the DUT keeps reaching the agent over its own
        # default route, so the control channel never moves into the session.
        # nodetach: the process we start is the process we later signal.
        # No persist: a redial has to be this test's doing rather than pppd's,
        # or the dependency case races an automatic reconnection.
        "noauth\n"
        "nodefaultroute\n"
        "nodetach\n"
        "maxfail 3\n"
        "holdoff 2\n"
        f"mtu {SESSION_MTU}\n"
        f"mru {SESSION_MTU}\n"
        f"lcp-echo-interval {LCP_ECHO_INTERVAL}\n"
        "lcp-echo-failure 3\n"
        f"pap-secrets {DUT_SECRETS}\n"
        f"chap-secrets {DUT_SECRETS}\n"
        # IPV6CP, which brings up the v6 network protocol on the session and
        # forms a link-local address from the negotiated interface identifiers.
        # The global addresses are the fixture's to add; this is what makes the
        # device carry v6 at all.
        + ("+ipv6\n" if ipv6 else ""))
    secrets = f'"{PPPOE_USER}"   *   "{PPPOE_SECRET}"   *\n'
    # Built here and staged as literals rather than assembled on the DUT: the
    # script that carries them is checksum-verified over the line, which a
    # heredoc at 115200 baud is not.
    stage = (
        "import json, pathlib, subprocess\n"
        f"pathlib.Path({DUT_SECRETS!r}).write_text({secrets!r})\n"
        f"pathlib.Path({DUT_SECRETS!r}).chmod(0o600)\n"
        f"pathlib.Path({DUT_PEER!r}).write_text({peer!r})\n"
        f"pathlib.Path({DUT_LOG!r}).unlink(missing_ok=True)\n"
        f"log = open({DUT_LOG!r}, 'wb')\n"
        f"proc = subprocess.Popen(['pppd', 'file', {DUT_PEER!r}],\n"
        "                        stdin=subprocess.DEVNULL, stdout=log, stderr=log,\n"
        "                        start_new_session=True)\n"
        f"pathlib.Path({DUT_PID!r}).write_text(str(proc.pid))\n"
        "print(json.dumps({'pid': proc.pid}))\n")
    result = await console_python(console, stage, timeout=30)
    assert result["rc"] == 0, result["stdout"]
    reported = [line for line in result["stdout"].splitlines() if line.startswith("{")]
    assert reported, result["stdout"]
    pid = json.loads(reported[-1])["pid"]
    # One long shell loop rather than Python-side polling: a single console
    # round-trip is robust against kernel log noise arriving mid-stream, and
    # the ppp_generic lockdep warning fires on the first PPPIOCNEWUNIT of a
    # boot (ISSUES.md A13) right where a tight polling loop would read.
    poll = ("found=; for i in $(seq 1 60); do "
            "  iface=$(ip -br link show | awk '/^ppp[0-9]/ {print $1; exit}'); "
            "  if [ -n \"$iface\" ] && ip -4 addr show $iface | grep -q 'inet '; then "
            "    found=$iface; break; fi; sleep 0.5; done; "
            "if [ -n \"$found\" ]; then echo PPPIFACE=$found; else echo PPPIFACE_TIMEOUT; fi")
    probe = await asyncio.to_thread(console.run, poll, 45.0)
    match = re.search(r"PPPIFACE=(ppp\d+)", probe.stdout)
    if not match:
        log = await console_command(console, "sh", "-c", f"tail -60 {DUT_LOG}", check=False)
        await _hangup(console)
        pytest.fail("the PPPoE session did not come up within 30s. pppd log:\n"
                    + (log["stdout"] or "<empty: pppd never started>"))
    return match.group(1), pid


async def _hangup(console):
    """Stop pppd by the PID we started, and wait for the device to go.

    By PID, never by pattern: a pkill broad enough to find pppd also matches
    the shell that is running it.
    """
    await console_command(console, "sh", "-c",
                          f"[ -f {DUT_PID} ] && kill -TERM $(cat {DUT_PID}) 2>/dev/null; "
                          f"for i in 1 2 3 4 5 6 7 8 9 10; do "
                          f"  ip -br link show | grep -q '^ppp' || break; sleep 0.5; done; "
                          f"[ -f {DUT_PID} ] && kill -KILL $(cat {DUT_PID}) 2>/dev/null; "
                          f"rm -f {DUT_PID}; true", check=False, timeout=25)
    remaining = await console_command(console, "ip", "-j", "link", "show")
    assert not any(link["ifname"].startswith("ppp")
                   for link in console_json(remaining["stdout"])), remaining


async def _session_identity(r):
    """The session as the kernel itself describes it: (id, concentrator MAC).

    /proc/net/pppoe is the independent view. The adapter derives its own copy
    from the forwarding-path walk, so requiring the two to agree is what says
    the hardware carries the session that was actually negotiated -- and, after
    a redial, that it carries the new one rather than the old.
    """
    text = await read(r.target, r.session, "/proc/net/pppoe")
    rows = [line.split() for line in text.splitlines()[1:] if line.strip()]
    assert len(rows) == 1, (text, "expected exactly one PPPoE session")
    sid, mac, lower, ppp = rows[0][0], rows[0][1], rows[0][2], rows[0][3]
    # Which devices the session really stands on, rather than which ones the
    # fixture asked for: the tag under the session is half of what makes this
    # path spend both encapsulation slots.
    assert (lower, ppp) == (r.ppp_lower, r.ppp_if), (rows, r.ppp_lower, r.ppp_if)
    return int(sid, 16), mac.lower()


def _session_text(identity):
    """How the adapter prints one: the id in decimal and the concentrator."""
    return f"{identity[0]}@{identity[1]}"


async def _wait_reachable(r, attempts=25):
    """Prove the path across the session before measuring anything on it.

    The first packet after a bring-up can lose a race with the peer route and
    with the concentrator's own ARP for the LAN, and `exchange` treats a single
    lost datagram as a failure -- correctly, since that is what it is there to
    catch. Absorb the bring-up here instead of in whichever case happens to
    run first.
    """
    # Three probes per attempt, not one: the first datagram after a cold boot
    # is the one that resolves the neighbour rather than the one that crosses,
    # so a single-packet probe reports failure for a path that is one packet
    # away from working. Observed on a freshly booted DUT, where ten
    # single-packet attempts were not enough and every later case passed.
    for _ in range(attempts):
        probe = await lan_run(r.lan, f"ping -c 3 -W 2 -I {r.peer_if} {INNER_LOCAL} "
                                     f">/dev/null 2>&1; echo rc=$?", 20.0)
        if "rc=0" in probe.stdout:
            return
        await asyncio.sleep(1.0)
    await _unreachable(r, INNER_LOCAL, attempts, probe)


async def _unreachable(r, target, attempts, probe):
    """Fail with the state of every hop, not just the verdict.

    "Could not reach" names the symptom and nothing else, and the path has
    four places to break: the session itself, the DUT's route across it, the
    concentrator's route back to the LAN, and the LAN VM's route to the inner
    address. Report all four so the next failure is diagnosed from the log
    rather than from a re-run.
    """
    dut = await command(r.target, r.session, "ip", "-br", "addr", "show", "ppp0",
                        check=False)
    dut_route = await command(r.target, r.session, "ip", "route", "get", target,
                              check=False)
    lan_route = await lan_run(r.lan, f"ip route get {target} 2>&1", 10.0)
    wan_route = await command(r.wan, r.session, "ip", "route", "get", r.lan_ip,
                              check=False)
    pytest.fail(
        f"the LAN VM could not reach {target} across the session after "
        f"{attempts} attempts: {probe.stdout!r}\n"
        f"  DUT ppp0:        {dut.get('stdout', dut)!r}\n"
        f"  DUT route:       {dut_route.get('stdout', dut_route)!r}\n"
        f"  LAN route:       {lan_route.stdout!r}\n"
        f"  concentrator ->  {wan_route.get('stdout', wan_route)!r}")


async def _wait_reachable6(r, attempts=25):
    """The same bring-up absorption for the v6 path across the session.

    Sourced from the LAN address the offload rule matches on rather than from
    an interface: the LAN VM carries more than one v6 address, and a probe that
    left from a different one would prove a path the flow never takes.
    """
    for _ in range(attempts):
        probe = await lan_run(r.lan, f"ping -6 -c 3 -W 2 -I {LAN_IPV6} {INNER_LOCAL6} "
                                     f">/dev/null 2>&1; echo rc=$?", 20.0)
        if "rc=0" in probe.stdout:
            return
        await asyncio.sleep(1.0)
    pytest.fail(f"the LAN VM could not reach {INNER_LOCAL6} across the session after "
                f"{attempts} attempts: {probe.stdout!r}")


async def _offload_table6(r, proto="udp"):
    """The offload table for a v6 flow across the session.

    `Rig.table()` writes an IPv4 match by construction and the family is part
    of the adapter's key, so this direction needs a table of its own. The
    devices are the same two physical ports: a ppp device is never one, which
    is as true for v6 as for v4.
    """
    await r.nft(f'''table inet {ft.TABLE} {{
 flowtable fast {{ hook ingress priority 0; devices = {{ {TARGET_LAN_IF}, {TARGET_WAN_IF} }};
 flags offload; }}
 chain forward {{ type filter hook forward priority 0; policy accept;
 ip6 saddr {LAN_IPV6} {proto} sport {SPORT6} {proto} dport {DPORT6} flow add @fast
 }}
}}''')
    await r.wait(lambda s: s["bindings"] == 2)


async def _exchange6(r, count):
    """Echo `count` v6 datagrams from the LAN VM across the session.

    A reply from the wrong endpoint or with the wrong payload is fatal; a
    timeout is only counted, so the caller can tolerate loss while the flow is
    still being admitted and forbid it once it is installed.
    """
    script = f'''
import json, socket, struct, time
s = socket.socket(socket.AF_INET6, socket.SOCK_DGRAM)
s.settimeout(2)
s.bind(({LAN_IPV6!r}, {SPORT6}))
echoed = lost = 0
for n in range({count}):
    payload = struct.pack('!Q', n) + b'ASK-flowtable-pppoe-v6'.ljust(48, b'.')
    s.sendto(payload, ({INNER_LOCAL6!r}, {DPORT6}))
    try:
        data, addr = s.recvfrom(2048)
    except TimeoutError:
        lost += 1
        continue
    assert data == payload, (n, data)
    assert (addr[0], addr[1]) == ({INNER_LOCAL6!r}, {DPORT6}), (n, addr)
    echoed += 1
    time.sleep(0.01)
s.close()
print(json.dumps({{'echoed': echoed, 'lost': lost}}))
'''
    result = await r.run_peer(script, timeout=count * 0.3 + 40, label="flowtable_pppoe_v6")
    assert result.rc == 0, result.stdout
    return json.loads(result.stdout.strip().splitlines()[-1])


# ---- topology ------------------------------------------------------------

async def _lan_segment(r, stack, tagged):
    """The LAN side, tagged or not, and the LAN VM's route to the inner peer.

    The route is restated rather than created: the bench already carries it,
    which is why nothing here removes it.
    """
    if tagged:
        dut_if = await dut_vlan_subif(stack, r.target, r.session, parent=TARGET_LAN_IF,
                                      vid=LAN_VID, ipv4=f"{DUT_TAGGED_ADDR}/24")
        lan_if = await lan_vlan_subif(
            stack, r.lan, parent=LAN_NIC, vid=LAN_VID, ipv4=f"{LAN_TAGGED_ADDR}/24",
            routes=[f"{INNER_LOCAL}/32 via {DUT_TAGGED_ADDR} dev vlan{LAN_VID}"])
        r.lan_ip, reachable = LAN_TAGGED_ADDR, TAGGED_SUBNET
    else:
        dut_if, lan_if = TARGET_LAN_IF, LAN_NIC
        addr = json.loads((await lan_run(r.lan, f"ip -j -4 addr show dev {LAN_NIC}")).stdout)
        r.lan_ip = next(a["local"] for a in addr[0]["addr_info"] if a["family"] == "inet")
        reachable = f"{r.lan_ip}/32"
        gateway = json.loads((await command(r.target, r.session, "ip", "-j", "-4", "addr",
                                            "show", "dev", TARGET_LAN_IF))["stdout"])[0]
        via = next(a["local"] for a in gateway["addr_info"] if a["family"] == "inet")
        # Restating a route the bench already carries, which is why nothing
        # removes it again: the LAN VM reaches the inner peer through the DUT
        # exactly as it reaches everything else.
        await lan_run(r.lan, f"ip route replace {INNER_LOCAL}/32 via {via} dev {LAN_NIC}")
    r.dut_lan_if, r.peer_if, r.peer_link = dut_if, lan_if, LAN_NIC
    link = json.loads((await lan_run(r.lan, f"ip -j link show dev {lan_if}")).stdout)[0]
    r.peer_mac = r.lan_mac = link["address"]
    r.peer_gateway_mac = r.dut_lan_mac = (
        await read(r.target, r.session, f"/sys/class/net/{dut_if}/address")).strip()
    return reachable


async def _concentrator_if(r):
    """The concentrator's own ppp device, named by the address pppoe-server
    put on it rather than assumed to be the DUT's name: both ends are usually
    ppp0, which is a coincidence between two machines rather than a fact about
    either, and an earlier session still up on the concentrator breaks it."""
    addresses = json.loads((await command(r.wan, r.session, "ip", "-j", "-4", "addr"))["stdout"])
    return next(i["ifname"] for i in addresses
                if any(a.get("local") == INNER_LOCAL for a in i["addr_info"]))


async def _ipv6_session(r, stack, cleanup):
    """Global IPv6 on both ends of the session, and a LAN that can reach it.

    Three /64s meet here and only one of them is new. The LAN segment reuses
    the addresses the IPv6 offload tests already put on the DUT's LAN port and
    the LAN VM, so that half of the path is the one those tests proved. The
    session's own two endpoints get a /64 of their own, because they are on
    neither segment: they are the ends of the point-to-point link between the
    two ppp devices, and taking an address out of a segment's prefix would make
    a routing mistake look like a working path.

    What IPV6CP brings up is the protocol and a link-local address formed from
    the negotiated interface identifiers. A global address it does not assign,
    so each end gets one here -- as a /64 on a point-to-point device, which
    makes the far end on-link and needs no next hop, exactly as the IPv4 side
    of this session needs none.
    """
    async def target(*argv, check=True):
        return await command(r.target, r.session, *argv, check=check)

    r.server_ppp_if = await _concentrator_if(r)

    previous = (await target("sysctl", "-n", "net.ipv6.conf.all.forwarding"))["stdout"].strip()
    cleanup.append((r.target, ["sysctl", "-w",
                               f"net.ipv6.conf.all.forwarding={previous}"]))
    await target("sysctl", "-w", "net.ipv6.conf.all.forwarding=1")

    # nodad throughout: duplicate address detection leaves an address tentative
    # for about a second and a half, and the first flow would silently not come
    # up. A ppp device is NOARP and skips it anyway; the LAN port does not.
    for address, interface in ((INNER_REMOTE6, r.ppp_if), (DUT_IPV6_LAN, TARGET_LAN_IF)):
        await target("ip", "-6", "addr", "del", f"{address}/64", "dev", interface,
                     check=False)
        await target("ip", "-6", "addr", "add", f"{address}/64", "dev", interface, "nodad")
        cleanup.append((r.target, ["ip", "-6", "addr", "del", f"{address}/64",
                                   "dev", interface]))
    await target("ip", "-6", "neigh", "replace", LAN_IPV6, "lladdr", r.lan_mac,
                 "nud", "permanent", "dev", TARGET_LAN_IF)
    cleanup.append((r.target, ["ip", "-6", "neigh", "del", LAN_IPV6,
                               "dev", TARGET_LAN_IF]))

    await command(r.wan, r.session, "ip", "-6", "addr", "del", f"{INNER_LOCAL6}/64",
                  "dev", r.server_ppp_if, check=False)
    await command(r.wan, r.session, "ip", "-6", "addr", "add", f"{INNER_LOCAL6}/64",
                  "dev", r.server_ppp_if, "nodad")
    cleanup.append((r.wan, ["ip", "-6", "addr", "del", f"{INNER_LOCAL6}/64",
                            "dev", r.server_ppp_if]))
    # The concentrator has no route to the LAN at all, in either family. Point
    # to point, so no next hop: the session is the only way there.
    await command(r.wan, r.session, "ip", "-6", "route", "replace", f"{LAN_IPV6}/128",
                  "dev", r.server_ppp_if)
    cleanup.append((r.wan, ["ip", "-6", "route", "del", f"{LAN_IPV6}/128",
                            "dev", r.server_ppp_if]))

    await lan_run(r.lan, f"ip -6 addr del {LAN_IPV6}/64 dev {LAN_NIC} 2>/dev/null; true")
    result = await lan_run(r.lan, f"ip -6 addr add {LAN_IPV6}/64 dev {LAN_NIC} nodad")
    assert result.rc == 0, result.stdout
    await lan_run(r.lan, f"ip -6 neigh replace {DUT_IPV6_LAN} lladdr {r.dut_lan_mac} "
                         f"nud permanent dev {LAN_NIC}")
    # A host route rather than a default: the LAN VM keeps whatever v6 default
    # it already had, so nothing else it does moves onto this path.
    await lan_run(r.lan, f"ip -6 route replace {INNER_LOCAL6}/128 via {DUT_IPV6_LAN} "
                         f"dev {LAN_NIC}")

    async def _lan_restore():
        await lan_run(r.lan, f"ip -6 route del {INNER_LOCAL6}/128 dev {LAN_NIC} "
                             f"2>/dev/null; true")
        await lan_run(r.lan, f"ip -6 neigh del {DUT_IPV6_LAN} dev {LAN_NIC} "
                             f"2>/dev/null; true")
        await lan_run(r.lan, f"ip -6 addr del {LAN_IPV6}/64 dev {LAN_NIC} "
                             f"2>/dev/null; true")
    stack.push(_lan_restore)
    await _wait_reachable6(r)


@pytest_asyncio.fixture
async def pppoe_rig(target_agent, aiohttp_session, lan, splat_window, request, monkeypatch):
    """LAN VM -> DUT -> PPPoE session -> orchestrator.

    The parameter selects the shape: "udp" (default), "tcp", "tagged" for a
    tagged LAN behind the session, "tagged-tcp" for the same carrying TCP,
    "ipv6" for a session carrying v6 as well, or "chap" for the UDP session
    authenticated with CHAP rather than PAP. Teardown reverses only what came
    up.

    The far endpoint is the session's own inner address, not the orchestrator's
    ordinary WAN address. It has to be: a host route for the WAN address down
    the session would send the agent's own replies through the tunnel and take
    the control channel with it. Rig reads that endpoint from its own module,
    so it is rebound there for this fixture rather than restated in a fork of
    every Rig method; monkeypatch puts it back.
    """
    shape = getattr(request, "param", "udp")
    assert shape in {"udp", "tcp", "tagged", "tagged-tcp", "ipv6", "chap"}
    monkeypatch.setattr(ft, "WAN_IP", INNER_LOCAL)
    r = Rig()
    r.proto = "tcp" if shape.endswith("tcp") else "udp"
    # The v6 shape is v4 plus a second family on the same session, never
    # instead of it: the v4 path is what the bring-up waits on and what the
    # control channel and the session's own addressing already run over.
    r.session_ipv6 = shape == "ipv6"
    r.target, r.session, r.lan, r.sequence = target_agent, aiohttp_session, lan, 1
    r.recovery_console = None
    initial = await r.state()
    assert initial["entries"] == initial["bindings"] == initial["invalidated"] == 0, initial
    r.wan = Agent("wan", f"http://{os.environ.get('ASK_WAN_IP', '127.0.0.1')}:9110")
    stack = TopologyStack()
    cleanup = []
    transport = transport6 = server = None
    console = Console.target(log_path=str(artifact_dir() / "pppoe-uart.log"))
    try:
        await command(r.target, r.session, "modprobe", "xt_tcpudp")
        # Quiet the kernel's own console before driving it. Every command here
        # is framed by a marker the reader matches on, and a printk landing
        # mid-marker truncates it -- observed as "missing console output
        # boundary" with the leading bytes of the token gone, and as a dial
        # that silently did not happen, which then surfaces one step later as
        # an unreachable peer. Restored in teardown.
        printk = (await read(r.target, r.session, "/proc/sys/kernel/printk")).split()
        await command(r.target, r.session, "sysctl", "-w", "kernel.printk=1 4 1 7")
        cleanup.append((r.target, ["sysctl", "-w",
                                   "kernel.printk=" + " ".join(printk[:4])]))
        await asyncio.to_thread(console.login, "root", None)
        # Probe for pppd before anything is built: skipping here costs nothing,
        # where skipping after the concentrator is up costs a spin-up and a
        # teardown, and skipping after the dial costs a 30-second timeout.
        await console_command(console, "modprobe", "pppoe")
        probe = await console_command(console, "sh", "-c", "command -v pppd", check=False)
        if probe["rc"] != 0:
            pytest.skip("pppd is not in the DUT image; add `ppp` to IMAGE_INSTALL")
        # Every device the session stands on exists before the flowtable binds.
        # Adding an upper to a bound port is a configuration change the adapter
        # answers with full invalidation, which would retire flows for a reason
        # that has nothing to do with the session.
        r.ppp_lower = await dut_vlan_subif(stack, r.target, r.session,
                                           parent=TARGET_WAN_IF, vid=WAN_VID)
        server = _server_start(ipv6=r.session_ipv6, auth="chap" if shape == "chap" else "pap")
        # ~0.5s to bind. A bad interface or a port already in use exits fast;
        # catching that here beats a bring-up timeout half a minute later.
        await asyncio.sleep(0.5)
        if server.poll() is not None:
            _, err = server.communicate(timeout=2)
            pytest.fail(f"pppoe-server exited rc={server.returncode} on {SERVER_IF}: "
                        f"{err.decode('utf-8', 'replace')[:1000]!r}")
        r.ppp_if, r.ppp_pid = await _dial(console, r.ppp_lower, ipv6=r.session_ipv6)
        stack.push(lambda: _hangup(console))
        r.console = console
        r.reachable = await _lan_segment(r, stack, shape.startswith("tagged"))
        r.session_identity = await _session_identity(r)
        # pppd installs the peer host route itself; what it does not install is
        # the way back, and the concentrator has no route to the LAN at all.
        # Point-to-point, so no nexthop: the session is the only way there.
        server_ppp_if = await _concentrator_if(r)
        for prefix in (r.reachable, f"{SNAT_ADDR}/32"):
            await command(r.wan, r.session, "ip", "route", "replace", prefix,
                          "dev", server_ppp_if)
            cleanup.append((r.wan, ["ip", "route", "del", prefix]))
        # Only the LAN neighbour is pinned. There is deliberately none to pin
        # on the session: a ppp device is NOARP and carries no address, so the
        # concentrator is named by the session and by nothing else.
        await command(r.target, r.session, "ip", "neigh", "replace", r.lan_ip,
                      "lladdr", r.lan_mac, "nud", "permanent", "dev", r.dut_lan_if)
        cleanup.append((r.target, ["ip", "neigh", "del", r.lan_ip, "dev", r.dut_lan_if]))
        # Without this the image's own masquerade rule would translate the
        # routed case and it would silently stop being a routed case.
        accept = ["POSTROUTING", "-s", r.lan_ip, "-d", INNER_LOCAL, "-p", r.proto,
                  "--sport", str(SPORT), "--dport", str(DPORT), "-j", "ACCEPT"]
        await command(r.target, r.session, "iptables", "-t", "nat", "-I", *accept)
        cleanup.append((r.target, ["iptables", "-t", "nat", "-D", *accept]))
        old_acct = (await read(r.target, r.session,
                               "/proc/sys/net/netfilter/nf_conntrack_acct")).strip()
        await command(r.target, r.session, "sysctl", "-w", "net.netfilter.nf_conntrack_acct=1")
        cleanup.append((r.target, ["sysctl", "-w",
                                   f"net.netfilter.nf_conntrack_acct={old_acct}"]))
        await _wait_reachable(r)
        await r.clear_ct()
        if r.proto == "udp":
            transport, r.echo = await asyncio.get_running_loop().create_datagram_endpoint(
                SourceEcho, local_addr=(INNER_LOCAL, DPORT))
        if r.session_ipv6:
            # After the v4 path is proven, so a v6 failure is about v6 rather
            # than about a session that never came up.
            await _ipv6_session(r, stack, cleanup)
            transport6, r.echo6 = await asyncio.get_running_loop().create_datagram_endpoint(
                SourceEcho, local_addr=(INNER_LOCAL6, DPORT6), family=socket.AF_INET6)
        r.record("pppoe-fixture", {"lan": r.lan_ip, "inner": INNER_LOCAL, "shape": shape,
                                   "ppp": r.ppp_if, "lower": r.ppp_lower,
                                   "pppd_pid": r.ppp_pid, "reachable": r.reachable,
                                   "session": _session_text(r.session_identity),
                                   "dut_if": r.dut_lan_if, "lan_mac": r.lan_mac,
                                   "initial": initial})
        yield r
    finally:
        for endpoint in (transport, transport6):
            if endpoint:
                endpoint.close()
        failures = []
        steps = [r.delete_table] + ([r.clear_ct] if hasattr(r, "lan_ip") else [])
        for step in steps:
            try:
                await step()
            except Exception as error:
                failures.append(str(error))
        await command(r.target, r.session, "nft", "delete", "table", "ip", NAT_TABLE,
                      check=False)
        for agent, argv in reversed(cleanup):
            await command(agent, r.session, *argv, check=False)
        await stack.teardown("flowtable-pppoe")
        if server:
            _server_stop(server)
        try:
            await console_command(console, "sh", "-c",
                                  f"rm -f {DUT_PEER} {DUT_SECRETS} {DUT_LOG}; true",
                                  check=False)
        finally:
            console.close()
        assert not failures, failures


async def _both_directions(r, state=None):
    """Admission is directional, so one direction refused leaves the other
    accelerated and the difference is invisible in a throughput number. An
    ingress session is the likeliest half to be refused and the hardest to
    notice, because its rule looks exactly like an unencapsulated one. Reads
    the state unless handed one."""
    state = state or await r.state()
    if len(state["flows"]) != 2:
        conntrack = await command(r.target, r.session, "conntrack", "-L", "-o", "extended",
                                  check=False)
        r.record("pppoe-partial-admission", {"state": state, "conntrack": conntrack})
        pytest.fail(f"{len(state['flows'])} of 2 directions admitted: "
                    f"validated={state['validated']} rejects={state['rejects']} "
                    f"busy={state['busy']} errors={state['errors']}\n"
                    f"flows={state['flows']}\nconntrack={conntrack['stdout']}")
    return state["flows"]


async def _download_only(r, since):
    """The one direction of an IPv4 UDP connection hardware holds: the
    download, which arrives inside the session and leaves by the LAN port at
    its full MTU. The upload's path is the session's 1492 bytes and its LAN
    port can deliver 1500, so it is refused to Linux (see the module
    docstring) -- counted as a reject since the state `since` was read, and
    absent rather than merely late."""
    state = await r.state()
    flows = state["flows"]
    if (len(flows) != 1 or flows[0]["in"] != TARGET_WAN_IF
            or state["rejects"] <= since["rejects"]):
        conntrack = await command(r.target, r.session, "conntrack", "-L", "-o", "extended",
                                  check=False)
        r.record("pppoe-unexpected-admission", {"since": since, "state": state,
                                                "conntrack": conntrack})
        pytest.fail(f"expected the download alone, the upload refused: "
                    f"validated={state['validated']} rejects={state['rejects']} "
                    f"(from {since['rejects']}) busy={state['busy']} "
                    f"errors={state['errors']}\nflows={flows}\nconntrack={conntrack['stdout']}")
    return flows


async def _established(r, count=64):
    """Install the flow, then measure a second burst against the hardware.

    What is installed is the download alone; see _download_only. The counters
    are cumulative for the boot, so every measurement here is a delta against
    a baseline taken once the flow is already installed.
    """
    initial = await r.state()
    await r.table()
    await r.exchange(count=4)
    flows = await _download_only(r, initial)
    installed = await r.state()
    before = {f["cookie"]: int(f["packets"]) for f in flows}
    await r.exchange(count=count)
    state = await r.state()
    after = {f["cookie"]: int(f["packets"]) for f in state["flows"]}
    _assert_undisturbed(r, installed, state, set(before) == set(after))
    return flows, {c: after[c] - before[c] for c in before}


def _assert_undisturbed(r, before, after, same=True):
    assert_undisturbed(r, before, after, same, label="pppoe-readmitted")


def _assert_session(r, forward, reverse, session=None):
    """The session is named on the direction that carries it and nowhere else.

    Both directions still name the physical ports: a ppp device never becomes
    one, and neither does the tagged device the session stands on. `forward`
    is None where the upload is Linux's.
    """
    expected = _session_text(session or r.session_identity)
    assert reverse["in_ppp"] == expected and reverse["out_ppp"] == "-", reverse
    assert reverse["in"] == TARGET_WAN_IF and reverse["out"] == TARGET_LAN_IF, reverse
    # The session stands on a tag, so the WAN side of each direction carries
    # both -- which is every encapsulation slot a direction has.
    assert reverse["in_vlan"] == str(WAN_VID), reverse
    if forward is None:
        return
    assert forward["out_ppp"] == expected and forward["in_ppp"] == "-", forward
    assert forward["in"] == TARGET_LAN_IF and forward["out"] == TARGET_WAN_IF, forward
    assert forward["out_vlan"] == str(WAN_VID), forward


# What the session's record counts per frame and the ppp device does not. The
# device counts the payload alone; the insert counts the frame with the
# Ethernet and session headers on and no tag yet, the strip the frame as it
# arrived less the session header, so the tag the session runs over is in.
# The adapter restates by the same amounts (FT_PPP_TX_OVERHEAD and
# ft_ppp_rx_overhead in ask_flowtable_core.c).
PPP_TX_OVERHEAD = 14 + 8
PPP_RX_OVERHEAD = 14 + 4


def _session_halves(state, identity):
    row = _session_row(state, identity)
    return {k: int(row[k]) for k in ("rx_packets", "rx_bytes", "tx_packets", "tx_bytes")}


async def _tcp_carried(r, label):
    """One TCP connection from the LAN across the session, read back while it
    is open and idle (see _gated_tcp): a first phase admits it, a second is
    measured. Returns the installed flows, both states, and what moved over the
    measured phase -- the ppp device's record, its `ip -s link` counters and
    the WAN port's software transmit count -- with the endpoint the
    concentrator's end saw connect from.

    The concentrator advertises the MSS its ppp device allows, so every full
    data segment of the upload comes within its TCP options of the session's
    MTU. A size check that counted the session or tag header against that MTU
    would except each one to Linux, which would then send it out of the WAN
    port itself; the software count is what shows it did not."""
    async with GatedTcp(r.run_peer, source=r.lan_ip, sport=SPORT, peer=INNER_LOCAL,
                        dport=DPORT, label=label) as transfer:
        await transfer.warmed()
        # Admission is asynchronous (rtnl_trylock, deferred a second or two
        # under RTNL contention), so warmed()'s brief settle can miss a late
        # direction; wait both in before reading the baseline the record and
        # the direction check share.
        before = await r.wait(lambda s: len(s["flows"]) == 2)
        flows = await _both_directions(r, before)
        record = _session_halves(before, r.session_identity)
        link = await _ppp_link(r)
        sent = await kernel_tx_packets(r.target, r.session, TARGET_WAN_IF)
        await transfer.measure()
        sent = await kernel_tx_packets(r.target, r.session, TARGET_WAN_IF) - sent
        after = await r.state()
        record_after = _session_halves(after, r.session_identity)
        link_after = await _ppp_link(r)
    return {"flows": flows, "before": before, "after": after, "software_wan_tx": sent,
            "record": {k: record_after[k] - record[k] for k in record},
            "link": {k: link_after[k] - link[k] for k in link},
            "peer": transfer.peername, "report": transfer.report}


def _assert_carried(r, measured):
    """The measured phase was hardware's, and the ppp device says so in the
    units it counts itself.

    The same two entries carried it, each at least a hundred packets, with
    nothing installed, retired or declined meanwhile. The session record's
    halves are those same frames: the insert's the upload's, the strip's the
    download's. And `ip -s link` on the ppp device moved by the record,
    restated into payload bytes, plus only what the session itself exchanged
    in software meanwhile (LCP echoes), each at most one frame -- which is the
    transmit fold for frames the hardware inserted, the half a UDP upload can
    no longer show."""
    before, after = measured["before"], measured["after"]
    old = {f["cookie"]: f for f in measured["flows"]}
    new = {f["cookie"]: f for f in after["flows"]}
    _assert_undisturbed(r, before, after, new.keys() == old.keys()
                        and (after["installs"], after["deletes"]) == (before["installs"], before["deletes"]))
    moved = {c: int(new[c]["packets"]) - int(old[c]["packets"]) for c in old}
    upload = moved[next(c for c, f in old.items() if f["out_ppp"] != "-")]
    download = moved[next(c for c, f in old.items() if f["in_ppp"] != "-")]
    assert upload > 100 and download > 100, moved
    record, link = measured["record"], measured["link"]
    assert (record["tx_packets"], record["rx_packets"]) == (upload, download), (record, moved)
    for half, overhead in (("tx", PPP_TX_OVERHEAD), ("rx", PPP_RX_OVERHEAD)):
        stray = link[half + "_packets"] - record[half + "_packets"]
        assert 0 <= stray <= 8, (half, record, link)
        payload = record[half + "_bytes"] - overhead * record[half + "_packets"]
        assert payload <= link[half + "_bytes"] <= payload + stray * 1518, (half, record, link)
    # Only the handful of frames the reads above cost, and the session's own.
    assert measured["software_wan_tx"] < upload // 4, (measured["software_wan_tx"], upload)


async def _ppp_link(r):
    link = json.loads((await command(r.target, r.session, "ip", "-s", "-j", "link", "show",
                                     "dev", r.ppp_if))["stdout"])[0]["stats64"]
    return {"rx_packets": link["rx"]["packets"], "rx_bytes": link["rx"]["bytes"],
            "tx_packets": link["tx"]["packets"], "tx_bytes": link["tx"]["bytes"]}


async def _untagged_path(r, cleanup):
    """The WAN host's own address across the two bare ports, beside the
    session: the path test_flowtable_offload's rig builds, restated here
    because that fixture and this one cannot share a case -- each owns the
    offload module's endpoint.

    Built before anything is admitted, since a host route added under an
    installed flow is a routing change the adapter answers. The concentrator's
    route back to the LAN is left pointing into the session; the case moves it
    for its window alone. Returns what that move needs: the WAN host's device
    and the DUT's own address on the WAN port.
    """
    addresses = json.loads((await command(r.wan, r.session, "ip", "-j", "-4", "addr"))["stdout"])
    wan_if = next((i["ifname"] for i in addresses
                   if any(a.get("local") == WAN_ENDPOINT for a in i["addr_info"])), None)
    assert wan_if, (f"ASK_WAN_IPERF_IP={WAN_ENDPOINT} is none of the WAN host's addresses",
                    addresses)
    wan_mac = json.loads((await command(r.wan, r.session, "ip", "-j", "link", "show",
                                        "dev", wan_if))["stdout"])[0]["address"]
    dut = json.loads((await command(r.target, r.session, "ip", "-j", "-4", "addr", "show",
                                    "dev", TARGET_WAN_IF))["stdout"])[0]
    dut_wan_ip = next(a["local"] for a in dut["addr_info"] if a["family"] == "inet")
    # Pinned, as the rig pins it, and put back as it was found: a neighbour
    # resolving mid-measurement is a change the adapter would answer inside
    # the window.
    old = json.loads((await command(r.target, r.session, "ip", "-j", "neigh", "show", "to",
                                    WAN_ENDPOINT, "dev", TARGET_WAN_IF))["stdout"])
    restore = ["ip", "neigh", "del", WAN_ENDPOINT, "dev", TARGET_WAN_IF]
    if old and old[0].get("lladdr"):
        state = "permanent" if "PERMANENT" in old[0]["state"] else "stale"
        restore = ["ip", "neigh", "replace", WAN_ENDPOINT, "lladdr", old[0]["lladdr"],
                   "nud", state, "dev", TARGET_WAN_IF]
    await command(r.target, r.session, "ip", "neigh", "replace", WAN_ENDPOINT, "lladdr",
                  wan_mac, "nud", "permanent", "dev", TARGET_WAN_IF)
    cleanup.append((r.target, restore))
    routes = json.loads((await command(r.target, r.session, "ip", "-j", "route", "show",
                                       "exact", f"{WAN_ENDPOINT}/32"))["stdout"])
    assert not routes, ("a host route to the WAN host was left behind", routes)
    await command(r.target, r.session, "ip", "route", "add", f"{WAN_ENDPOINT}/32",
                  "dev", TARGET_WAN_IF)
    cleanup.append((r.target, ["ip", "route", "del", f"{WAN_ENDPOINT}/32",
                               "dev", TARGET_WAN_IF]))
    # Routed, not translated: the image's own masquerade would rewrite it.
    accept = ["POSTROUTING", "-s", r.lan_ip, "-d", WAN_ENDPOINT, "-p", "udp",
              "--sport", str(UNTAGGED_SPORT), "--dport", str(UNTAGGED_DPORT), "-j", "ACCEPT"]
    await command(r.target, r.session, "iptables", "-t", "nat", "-I", *accept)
    cleanup.append((r.target, ["iptables", "-t", "nat", "-D", *accept]))
    clear = ["conntrack", "-D", "-p", "udp", "--orig-src", r.lan_ip, "--orig-dst", WAN_ENDPOINT,
             "--sport", str(UNTAGGED_SPORT), "--dport", str(UNTAGGED_DPORT)]
    await command(r.target, r.session, *clear, check=False)
    cleanup.append((r.target, clear))
    return wan_if, dut_wan_ip


async def _untagged_exchange(r, count):
    """Echo `count` datagrams from the LAN VM to the WAN host over the bare
    ports, one at a time, so each is one frame each way and the entries'
    counters can be held to exactly `count`. A lost reply is counted, not
    fatal, so the caller decides what loss means."""
    script = f'''
import json, socket, struct
s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
s.settimeout(2)
s.bind(({r.lan_ip!r}, {UNTAGGED_SPORT}))
echoed = lost = 0
for n in range({count}):
    payload = struct.pack('!Q', n) + b'ASK-pppoe-untagged'.ljust(56, b'.')
    s.sendto(payload, ({WAN_ENDPOINT!r}, {UNTAGGED_DPORT}))
    try:
        while True:
            data, addr = s.recvfrom(2048)
            if data == payload:
                break
    except TimeoutError:
        lost += 1
        continue
    assert addr == ({WAN_ENDPOINT!r}, {UNTAGGED_DPORT}), (n, addr)
    echoed += 1
s.close()
print(json.dumps({{'echoed': echoed, 'lost': lost}}))
'''
    result = await r.run_peer(script, timeout=count * 0.01 + 40, label="flowtable_pppoe_untagged")
    assert result.rc == 0, result.stdout
    return json.loads(result.stdout.strip().splitlines()[-1])


def _untagged_directions(r, flows):
    """The untagged flow's two installed directions, forward first, or None
    until hardware holds both."""
    lan, wan = f"{r.lan_ip}:{UNTAGGED_SPORT}", f"{WAN_ENDPOINT}:{UNTAGGED_DPORT}"
    forward = [f for f in flows if (f["src"], f["dst"]) == (lan, wan)]
    reverse = [f for f in flows if (f["src"], f["dst"]) == (wan, lan)]
    if len(forward) == len(reverse) == 1:
        return forward[0], reverse[0]
    return None


async def _untagged_window(r, echo):
    """Admit the untagged flow, then run UNTAGGED_COUNT round trips over it
    between two readings of everything the session keeps: its record, its
    ppp device's counters, and its download's entry in the adapter state."""
    for _ in range(10):
        await _untagged_exchange(r, 4)
        state = await r.state()
        if _untagged_directions(r, state["flows"]):
            break
    else:
        pytest.fail(f"the untagged flow was not admitted beside the session: {state}")
    before = await r.state()
    assert _untagged_directions(r, before["flows"]), before["flows"]
    record = _session_halves(before, r.session_identity)
    opened = time.monotonic()
    link = await _ppp_link(r)
    answered = echo.packets
    report = await _untagged_exchange(r, UNTAGGED_COUNT)
    answered = echo.packets - answered
    link_after = await _ppp_link(r)
    window = time.monotonic() - opened
    after = await r.state()
    return {"before": before, "after": after, "report": report, "answered": answered,
            "window": window, "record": record,
            "record_after": _session_halves(after, r.session_identity),
            "link": link, "link_after": link_after}


def _snat_table(r):
    # nft rather than an iptables SNAT target, which this image has no module
    # for, and at priority 90 so it runs ahead of the fixture's own
    # priority-100 exemption rather than behind it.
    return (f"table ip {NAT_TABLE} {{ chain postrouting {{ "
            f"type nat hook postrouting priority 90; "
            f"ip saddr {r.lan_ip} ip daddr {INNER_LOCAL} "
            f"{r.proto} sport {SPORT} {r.proto} dport {DPORT} snat to {SNAT_ADDR}; }}; }}")


async def _qos_bulk(r, seconds):
    """Start the unmarked flow on the LAN VM, detached, paced at QOS_BULK_MBIT
    towards a port on the far end that nothing reads. It is its own session
    under `timeout`, so a run that dies without its teardown leaves nothing
    sending for longer than that."""
    blaster = f'''
import socket, time
s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
s.bind(({r.lan_ip!r}, {PORT_QOS_BULK}))
payload = b'ASK-pppoe-qos-bulk'.ljust({QOS_DATAGRAM}, b'.')
per_second = {QOS_BULK_MBIT} * 1e6 / 8 / {QOS_DATAGRAM}
start = time.monotonic()
sent = 0
while time.monotonic() - start < {seconds}:
    s.sendto(payload, ({INNER_LOCAL!r}, {PORT_QOS_BULK}))
    sent += 1
    ahead = sent / per_second - (time.monotonic() - start)
    if ahead > 0.002:
        time.sleep(ahead)
'''
    script = f'''
import pathlib, subprocess
pathlib.Path({QOS_BULK_SCRIPT!r}).write_text({blaster!r})
proc = subprocess.Popen(['timeout', {str(seconds + 30)!r}, 'python3', {QOS_BULK_SCRIPT!r}],
                        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL, start_new_session=True)
pathlib.Path({QOS_BULK_PID!r}).write_text(str(proc.pid))
print('BULK-UP')
'''
    result = await r.run_peer(script, label="pppoe_qos_bulk", timeout=30)
    assert result.rc == 0 and "BULK-UP" in result.stdout, result.stdout


async def _qos_bulk_stop(r):
    script = f'''
import os, pathlib, signal
pid = pathlib.Path({QOS_BULK_PID!r})
if pid.exists():
    try:
        os.killpg(int(pid.read_text()), signal.SIGTERM)
    except ProcessLookupError:
        pass
    pid.unlink()
print('BULK-DOWN')
'''
    result = await r.run_peer(script, label="pppoe_qos_bulk_stop", timeout=30)
    assert result.rc == 0 and "BULK-DOWN" in result.stdout, result.stdout
