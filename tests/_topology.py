"""Topology helpers shared across data-plane tests.

Bench port roles, VLAN ID claims, dell1 SSH scripting and composable
topology fixtures: VLAN subifs on either side and the IPv6 topology,
built on a finalizer stack so a partial setup tears down whatever did
come up.

By design: no new agent endpoints, no new exec_cmd allowlist
entries, no /pkt/inject, no pcap storage. Everything here runs on
existing primitives.
WAN VLAN 3900 is provided temporarily by the tagged_wan fixture on dell2;
it is shared by PPPoE, multicast and DHCP, not a permanent bridge device.
"""

from __future__ import annotations
import asyncio
import errno
import json
import os
import aiohttp
import pytest_asyncio
from ask_orch.lifecycle import CleanupStack, checked
from ask_orch.counters import kernel_rx_packets  # noqa: F401 (shared helper)


# ---- VLAN ID conventions -------------------------------------------------
#
# Every test that creates VLAN subinterfaces on the LAN segment claims its
# IDs here so they don't collide. pytest runs serially, but teardown races
# and shared-segment capture still make overlap worth tracking. Claims:
#   flowtable_vlan.py      271/272      (ASK_FLOWTABLE_VLAN_ID, +1 inner)
#   flowtable_bridge.py    273/274/275  (ASK_FLOWTABLE_BRIDGE_VID, +1, +2)
#   flowtable_pppoe.py     276          (ASK_FLOWTABLE_PPPOE_LAN_VID)
#   flowtable_service_vlan.py 284      (service VLAN recovery)
#   flowtable_service_bridge.py 285/286 (trusted/guest bridge membership,
#                                            port and VLAN forwarding state)
#   flowtable_service_multicast_bridge.py 287/288/289/290
#                               (LAN, WAN, IPTV, and the VLAN IPTV is routed into)
#   mcast_e2e.py           244          (VLAN_ID_MROUTE, routed oif)
#   mroute_capacity.py     311..319     (nine LAN listeners)
#   flowtable_service_multicast_leave.py      321/322 (routed via a snooping bridge)
#   flowtable_service_multicast_quarantine.py 323     (listener swap)
#   mcast_member_mtu.py    324          (VLAN_ID_MCAST_MTU, narrow oif)
#   flowtable_service_multicast_edges.py 325 (the oif a forward chain drops toward)
#   flowtable_service_multicast_xfrm.py  326 (the oif an XFRM policy governs)
#   flowtable_service_multicast_ports.py 327 (the oif a port rule drops toward)
#   flowtable_slaac.py     331          (isolated LAN router advertisements)
#   flowtable_jumbo.py     332          (VLAN_ID_JUMBO, tagged jumbo LAN)
#
VLAN_ID_MROUTE: int                   = 244
VLAN_IDS_MROUTE_LIMIT: tuple[int, ...] = tuple(range(311, 320))
VLAN_ID_MCAST_MTU: int                = 324
VLAN_ID_JUMBO: int                    = 332
VLAN_ID_PPPOE_WAN: int                = 3900

# Bench wiring: the DUT's eth3 faces dell1, eth4 faces dell2.
# Every test that needs a role-scoped DUT port
# imports these two symbols rather than hardcoding a netdev name, so a
# re-cable is a one-line change here (or an env override at run time).
TARGET_LAN_IF = os.environ.get("ASK_TARGET_LAN_IF", "eth3")
LAN_NIC       = os.environ.get("ASK_LAN_NIC",       "")

# The largest IPv4 packet an Ethernet port at or below a standard MTU delivers:
# each port's MAC receive limit follows its MTU but never drops below a full
# frame, and the sending host is never told the DUT's setting. A port given a
# jumbo MTU delivers up to that MTU instead. The flowtable adapter installs a
# non-TCP IPv4 direction only where its path carries the larger of the two from
# its ingress, so a UDP direction into any smaller path stays in Linux.
FULL_FRAME = 1500


# ---- composable topology primitives --------------------------------------

TopologyStack = CleanupStack


async def dut_vlan_subif(
    stack: TopologyStack,
    target_agent,
    session: aiohttp.ClientSession,
    *,
    parent: str,
    vid: int,
    name: str | None = None,
    ipv4: str | None = None,
    ipv6: str | None = None,
    master: str | None = None,
) -> str:
    """Create a VLAN subif on the DUT, push its cleanup onto `stack`,
    return the iface name.

    `name` defaults to `f"{parent}.{vid}"`. `ipv4`/`ipv6` are CIDR
    strings (e.g. "192.168.100.1/24"). `master` enslaves to a bridge.
    The subif is brought up at the end. Idempotent: a stale iface
    with the same name is deleted before re-add.
    """
    iface = name or f"{parent}.{vid}"

    async def _exec(*argv: str, check=True):
        result = await target_agent.exec_cmd(session, list(argv))
        return checked(result) if check else result

    await _exec("ip", "link", "del", iface, check=False)  # idempotent

    r = await _exec(
        "ip", "link", "add", "link", parent,
        "name", iface, "type", "vlan", "id", str(vid),
    )
    assert r["rc"] == 0, f"DUT vlan add {iface} (vid {vid}): {r}"

    async def _cleanup():
        result = await _exec("ip", "link", "del", iface, check=False)
        if result["rc"]:
            # Lifecycle tests may already have removed this device (or its
            # parent). Only absence makes a failed delete harmless.
            remaining = await target_agent.fs_read(session, f"/sys/class/net/{iface}/ifindex")
            if remaining["errno"] != errno.ENOENT:
                checked(result)
    stack.push(_cleanup)

    if ipv4:
        r = await _exec("ip", "addr", "add", ipv4, "dev", iface)
        assert r["rc"] == 0, f"DUT ipv4 {ipv4} on {iface}: {r}"
    if ipv6:
        r = await _exec("ip", "-6", "addr", "add", ipv6, "dev", iface)
        assert r["rc"] == 0, f"DUT ipv6 {ipv6} on {iface}: {r}"
    if master:
        r = await _exec("ip", "link", "set", iface, "master", master)
        assert r["rc"] == 0, f"DUT enslave {iface} → {master}: {r}"

    r = await _exec("ip", "link", "set", iface, "up")
    assert r["rc"] == 0, f"DUT vlan up {iface}: {r}"
    return iface


async def lan_vlan_subif(
    stack: TopologyStack,
    lan,
    *,
    parent: str,
    vid: int,
    name: str | None = None,
    ipv4: str | None = None,
    ipv6: str | None = None,
    routes: list[str] | None = None,
) -> str:
    """LAN-side counterpart to `dut_vlan_subif`. Same shape, driven via
    SSH. `routes` is a list of `ip route add ARGS` argument strings;
    each is added with a matching `ip route del FIRST_TOKEN` cleanup
    pushed onto `stack`.
    """
    iface = name or f"vlan{vid}"

    await lan_run(lan, f"ip link del {iface} 2>/dev/null", 5.0)

    r = await lan_run(
        lan,
        f"ip link add link {parent} name {iface} type vlan id {vid}",
        10.0,
    )
    assert r.rc == 0, f"LAN vlan add {iface} (vid {vid}): {r.stdout!r}"

    async def _cleanup():
        checked(await lan_run(lan, f"if [ -e /sys/class/net/{iface} ]; then ip link del {iface}; fi", 5.0))
    stack.push(_cleanup)

    if ipv4:
        r = await lan_run(lan, f"ip addr add {ipv4} dev {iface}", 5.0)
        assert r.rc == 0, f"LAN ipv4 {ipv4} on {iface}: {r.stdout!r}"
    if ipv6:
        r = await lan_run(lan, f"ip -6 addr add {ipv6} dev {iface}", 5.0)
        assert r.rc == 0, f"LAN ipv6 {ipv6} on {iface}: {r.stdout!r}"

    r = await lan_run(lan, f"ip link set {iface} up", 5.0)
    assert r.rc == 0, f"LAN vlan up {iface}: {r.stdout!r}"

    for spec in (routes or []):
        # `replace`, not `add`: a leftover route from a crashed prior run
        # (same prefix, stale nexthop) would make `add` fail "File exists"
        # and keep the stale route; replace keeps setup idempotent.
        checked(await lan_run(lan, f"ip route replace {spec}", 5.0))
        async def _del_route(_first=spec.split()[0]):
            checked(await lan_run(lan, f"ip route del {_first}", 5.0))
        stack.push(_del_route)

    return iface


# ---- 4. low-level helpers -----------------------------------------------

async def lan_run(lan, cmd: str, timeout: float = 10.0):
    """Run a dell1 command without blocking the event loop on SSH I/O."""
    return await asyncio.to_thread(lan.run, cmd, timeout)


async def has_address(agent, session, interface: str, address: str) -> bool:
    """Whether `interface` on the agent's host already holds IPv6 `address`.
    A fixture that finds one there, such as the address the image gives the
    DUT's WAN port, uses it and leaves it behind at teardown."""
    result = checked(await agent.exec_cmd(session, ["ip", "-j", "-6", "addr", "show",
                                                    "dev", interface]))
    return any(a.get("local") == address
               for link in json.loads(result["stdout"] or "[]") for a in link["addr_info"])


async def lan_run_python(
    lan,
    script: str,
    *,
    timeout: float = 30.0,
    label: str = "script",
):
    """Run a script through QGA; raw-console callers retain serial staging."""
    if hasattr(lan, "python"):
        return await asyncio.to_thread(lan.python, script, timeout)
    import base64
    import time

    path = f"/tmp/ask_lan_{label}_{os.getpid()}_{int(time.monotonic() * 1e6)}.py"
    b64 = base64.b64encode(script.encode()).decode()

    # Off the event loop like the run itself: a console another operation
    # holds would otherwise stall every task the test has in flight.
    stage = await lan_run(lan, f"echo {b64} | base64 -d > {path} && echo STAGED", 10)
    if stage.rc != 0 or "STAGED" not in stage.stdout:
        raise AssertionError(
            f"failed to stage Python script on LAN at {path}: "
            f"rc={stage.rc}, stdout={stage.stdout!r}"
        )
    try:
        return await lan_run(lan, f"python3 {path}", timeout)
    finally:
        checked(await lan_run(lan, f"rm -f {path}", 5))


async def lan_ipv6_default(stack, lan, gateway, device):
    """Set a temporary default route and restore iproute2's original snapshot."""
    snapshot = checked(await lan_run_python(lan, """
import base64, subprocess
saved = subprocess.run(["ip", "-6", "route", "save", "default"],
                       stdout=subprocess.PIPE, check=True, timeout=5).stdout
print(base64.b64encode(saved).decode())
""", label="save_ipv6_default"))
    saved = snapshot.stdout.strip()

    async def restore():
        cleanup = CleanupStack()
        # iproute2 seeks while restoring; a pipe cannot hold the snapshot.
        cleanup.push(lambda: lan_run_python(lan, f"""
import base64, subprocess, tempfile
with tempfile.TemporaryFile() as routes:
    routes.write(base64.b64decode({saved!r}))
    routes.seek(0)
    subprocess.run(["ip", "-6", "route", "restore"], stdin=routes,
                   check=True, timeout=5)
""", label="restore_ipv6_default"))
        cleanup.push(lambda: lan_run(lan, f"ip -6 route del default via {gateway} dev {device}", 5))
        await cleanup.teardown("LAN IPv6 default route")

    stack.push(restore)
    checked(await lan_run(lan, f"ip -6 route replace default via {gateway} dev {device}", 5))


# DUT and LAN IPv6 addresses for the IPv6 tests. Two ULA /64s
# (fc00::/7 documentation/private space) — keeps routing self-contained
# without needing real upstream IPv6 connectivity. ASK_WAN_IPV6 should
# point into the WAN /64; a destination there has no listener, but the
# DUT's IPv6 input path (TTL/HBH/PTB checks, classifier) runs before
# the next-hop ND attempt, which is what these tests exercise.
DUT_IPV6_LAN  = "fc00:dead::1"
DUT_IPV6_WAN  = "fc00:beef::1"
LAN_IPV6      = "fc00:dead::2"
# The offload tests give the WAN host this address for real bidirectional
# traffic, so their endpoint answers rather than only provoking an ICMPv6
# error. VIRT_IPV6 is an unassigned address in the same /64, used as the
# pre-translation destination of a DNAT flow.
WAN_IPV6      = os.environ.get("ASK_WAN_IPV6", "fc00:beef::99")
VIRT_IPV6     = os.environ.get("ASK_VIRT_IPV6", "fc00:beef::dd")
TARGET_WAN_IF = os.environ.get("ASK_TARGET_WAN_IF", "eth4")

# A third ULA /64, claimed by flowtable_pppoe.py for the addresses a PPPoE
# session carries inside itself. It is deliberately neither of the two above:
# the session's endpoints are not on the LAN or the WAN segment, they are on
# the point-to-point link between the two ppp devices, and giving them an
# address out of a segment /64 would make a routing mistake look like a
# working path. The concentrator takes ::1 and the DUT ::2, matching the
# INNER_LOCAL/INNER_REMOTE convention the IPv4 side of that session uses.
# ("babe" rather than a spelling like "ppp" because p is not a hex digit and
# the address would not parse.)
#
# Only the session's own /64 is new. Its LAN side reuses DUT_IPV6_LAN and
# LAN_IPV6 above, which it configures itself rather than through
# ipv6_topology -- that fixture also addresses the WAN port, which is where
# the session stands. Sharing those two with flowtable_ipv6.py is safe
# only because pytest runs serially and both tear down in finalizers, the same
# basis as the VLAN id overlaps recorded above.
PPPOE_IPV6_LOCAL  = os.environ.get("ASK_PPPOE_INNER_LOCAL6", "fc00:babe::1")
PPPOE_IPV6_REMOTE = os.environ.get("ASK_PPPOE_INNER_REMOTE6", "fc00:babe::2")


@pytest_asyncio.fixture
async def ipv6_topology(aiohttp_session, target_agent, lan):
    """Bring up a minimal IPv6 LAN→DUT→WAN topology for the IPv6
    tests. Tears down all assigned addresses + forwarding flags + routes
    on exit, in reverse order of setup, so partial-setup failures clean
    only what came up.

    DUT eth3  ULA  fc00:dead::1/64  (LAN-facing, TARGET_LAN_IF)
    DUT eth4  ULA  fc00:beef::1/64  (WAN-facing, TARGET_WAN_IF)
    LAN NIC   ULA  fc00:dead::2/64
    LAN default v6 route via fc00:dead::1

    ASK_WAN_IPV6 (default fc00:beef::99) lives in the WAN /64 — the
    DUT routes to it but no listener exists; ND for the next-hop fails.
    That's expected: 2b/2c assert ICMPv6 errors emitted *before* the
    next-hop attempt, and 2a/2d/2e tripwire on counter deltas
    irrespective of forward outcome.
    """
    cleanup = CleanupStack()

    async def _exec(*argv: str, check=True):
        result = await target_agent.exec_cmd(aiohttp_session, list(argv))
        return checked(result) if check else result

    async def _lan(cmd: str, timeout_s: float = 5.0, check=True):
        result = await lan_run(lan, cmd, timeout_s)
        return checked(result) if check else result

    try:
        # ---- DUT sysctl: enable IPv6 forwarding ----
        # Save current values so teardown restores them.
        r = await _exec("sysctl", "-n", "net.ipv6.conf.all.forwarding")
        prev_all_fwd = r.get("stdout", "0").strip() or "0"

        async def _restore_all_fwd(v=prev_all_fwd):
            await _exec("sysctl", "-w", f"net.ipv6.conf.all.forwarding={v}")
        cleanup.push(_restore_all_fwd)

        r = await _exec("sysctl", "-w", "net.ipv6.conf.all.forwarding=1")
        assert r["rc"] == 0, f"enable v6 forwarding: {r}"

        # ---- DUT addresses ----
        # Idempotent: del before add so a re-run after a botched teardown
        # doesn't trip "already exists".
        await _exec("ip", "-6", "addr", "del",
                    f"{DUT_IPV6_LAN}/64", "dev", TARGET_LAN_IF, check=False)
        r = await _exec("ip", "-6", "addr", "add",
                        f"{DUT_IPV6_LAN}/64", "dev", TARGET_LAN_IF, "nodad")
        assert r["rc"] == 0, f"DUT {TARGET_LAN_IF} v6 addr: {r}"

        async def _del_dut_lan():
            await _exec("ip", "-6", "addr", "del",
                        f"{DUT_IPV6_LAN}/64", "dev", TARGET_LAN_IF)
        cleanup.push(_del_dut_lan)

        # The image addresses the WAN port itself; that address stays.
        if not await has_address(target_agent, aiohttp_session, TARGET_WAN_IF, DUT_IPV6_WAN):
            r = await _exec("ip", "-6", "addr", "add",
                            f"{DUT_IPV6_WAN}/64", "dev", TARGET_WAN_IF, "nodad")
            assert r["rc"] == 0, f"DUT {TARGET_WAN_IF} v6 addr: {r}"

            async def _del_dut_wan():
                await _exec("ip", "-6", "addr", "del",
                            f"{DUT_IPV6_WAN}/64", "dev", TARGET_WAN_IF)
            cleanup.push(_del_dut_wan)

        # ---- LAN address + default route ----
        await _lan(f"ip -6 addr del {LAN_IPV6}/64 dev {LAN_NIC} 2>/dev/null", check=False)
        # nodad: static ULA on a point-to-point test segment — DAD would
        # leave the address tentative ~1.5 s and the first test flow of
        # the session silently fails to come up.
        r = await _lan(f"ip -6 addr add {LAN_IPV6}/64 dev {LAN_NIC} nodad")
        assert r.rc == 0, f"LAN v6 addr: {r.stdout!r}"

        async def _del_lan_addr():
            await _lan(f"ip -6 addr del {LAN_IPV6}/64 dev {LAN_NIC} 2>/dev/null")
        cleanup.push(_del_lan_addr)

        await lan_ipv6_default(cleanup, lan, DUT_IPV6_LAN, LAN_NIC)

        # Populate the LAN's IPv6 neighbor cache for the DUT. Without
        # this, scapy's first send falls back to broadcast L2 MAC
        # ("MAC address to reach destination not found") which the
        # DUT may drop at L2 input, and the entire IPv6 test path
        # turns into a vacuous "no signal" pass. ping6 -c 1 forces
        # the LAN kernel to do ND once; subsequent scapy sends in
        # the same test see the cached neighbor.
        await _lan(
            f"ping -6 -c 1 -W 2 {DUT_IPV6_LAN} > /dev/null 2>&1 || true",
            10.0,
        )
        await asyncio.sleep(0.5)
        yield {
            "dut_lan_v6": DUT_IPV6_LAN,
            "dut_wan_v6": DUT_IPV6_WAN,
            "lan_v6":     LAN_IPV6,
        }
    finally:
        await cleanup.teardown("ipv6_topology")
