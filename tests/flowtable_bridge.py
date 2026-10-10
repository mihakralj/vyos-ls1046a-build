"""Bridge flowtable offload: the LAN sits behind br-ft, the WAN does not.

Three shapes, because a bridge changes the tag stack in three different ways.
"plain" is a bridge with VLAN filtering off, which adds nothing. "access" is a
vlan-aware bridge routed through br-ft.N over a port that is untagged for N --
the configuration OpenWrt actually ships, and the one where the wire carries
no tag at all even though a VLAN device is in the path. "tagged" is the same
bridge over a port that is tagged for N, where the tag survives to the wire.

Every case asserts the bridge and VID the adapter recorded, then sends a
second burst and requires the classifier's own packet counters to account for
all of it: the rule alone never proves the frame reached the wire.

The bridge FDB pins the egress port, so two further cases drive it directly --
a station that reappears on another bridge port, and an entry that ages out --
and require the flow to retire rather than keep forwarding to the old port.
"""
from __future__ import annotations

import asyncio
import json
import os

import pytest
import pytest_asyncio

from ask_orch.client import Agent
from _topology import (LAN_NIC, TARGET_LAN_IF, TARGET_WAN_IF, TopologyStack,
                       dut_vlan_subif, lan_run, lan_vlan_subif)
from _flowtable_rig import (DPORT, Echo, SPORT, WAN_IP, Rig, assert_undisturbed, command, read)
from _gated_tcp import GatedTcp

BRIDGE = "br-ft"
# Claimed in _topology.py's VLAN ID conventions block. 273 carries the access
# shape, 274 the tagged one, and 275 the second bridge port the roaming case
# makes a station reappear on.
ACCESS_VID = int(os.environ.get("ASK_FLOWTABLE_BRIDGE_VID", "273"))
TAGGED_VID = ACCESS_VID + 1
ROAM_VID = ACCESS_VID + 2
# Not derived from the VID: a VLAN id can exceed an octet, and a subnet built
# out of one silently aliases as soon as it does.
TAGGED_SUBNET, DUT_TAGGED_ADDR, LAN_TAGGED_ADDR = "172.29.73.0/24", "172.29.73.1", "172.29.73.2"
SNAT_ADDR = "172.29.73.9"
# The station's address on the second bridge port, and an address on that
# subnet that answers nothing: the ARP for it is the roaming stimulus.
ROAM_ADDR, ROAM_PROBE = "172.29.75.2", "172.29.75.1"
NAT_TABLE = "ask_bridge_nat"
# Centiseconds, as the kernel reads IFLA_BR_AGEING_TIME. Only the ageing case
# sets this: hardware forwarding never reaches the bridge, so under a live
# flow nothing refreshes the entry and a short ageing time would expire it
# under every other test as well.
AGEING_CS = 1000


class SourceEcho(Echo):
    """Echo that also records where each datagram came from. Under SNAT that is
    the translated endpoint, which is the wire-level proof of the rewrite."""

    def __init__(self):
        super().__init__()
        self.sources = set()

    def datagram_received(self, data, addr):
        self.sources.add((addr[0], addr[1]))
        super().datagram_received(data, addr)


async def _flows(r):
    return (await r.state())["flows"]


def _direction(flows, source, destination):
    """One installed direction, named by the endpoints of its match."""
    matching = [f for f in flows if f["src"].startswith(source + ":")
                and f["dst"].startswith(destination + ":")]
    assert len(matching) == 1, (source, destination, flows)
    return matching[0]


async def _fdb(r, mac):
    """Every bridge port this address is currently learned on."""
    entries = json.loads((await command(r.target, r.session, "bridge", "-j", "fdb",
                                        "show", "br", BRIDGE))["stdout"])
    return sorted(e["ifname"] for e in entries
                  if e["mac"] == mac and e.get("master") is not False
                  and "permanent" not in e.get("flags", []))


async def _bridged_segment(r, stack, shape):
    """The LAN behind br-ft, on both sides of the wire.

    Only the LAN is bridged; the WAN keeps the untagged, unbridged address the
    rest of the suite uses, so the two directions of one connection differ in
    exactly the path stack and nothing else.
    """
    async def dut(*argv, check=True):
        return await command(r.target, r.session, *argv, check=check)

    # The DUT's LAN address moves onto the bridge, so the flow's logical
    # egress device is the bridge (or a VLAN on it) rather than the port.
    lan_cidr = json.loads((await dut("ip", "-j", "-4", "addr", "show",
                                     "dev", TARGET_LAN_IF))["stdout"])[0]
    dut_lan = next(f"{a['local']}/{a['prefixlen']}" for a in lan_cidr["addr_info"]
                   if a["family"] == "inet")
    await dut("ip", "link", "del", BRIDGE, check=False)
    await dut("ip", "link", "add", "name", BRIDGE, "type", "bridge")

    async def _drop_bridge():
        await dut("ip", "link", "del", BRIDGE, check=False)
        await dut("ip", "addr", "replace", dut_lan, "dev", TARGET_LAN_IF, check=False)
    stack.push(_drop_bridge)

    await dut("ip", "addr", "del", dut_lan, "dev", TARGET_LAN_IF)
    await dut("ip", "link", "set", TARGET_LAN_IF, "master", BRIDGE)
    await dut("ip", "link", "set", BRIDGE, "up")
    r.bridge_port = TARGET_LAN_IF

    if shape == "plain":
        await dut("ip", "addr", "add", dut_lan, "dev", BRIDGE)
        r.dut_lan_if, r.bridge_text = BRIDGE, BRIDGE
        r.peer_if, r.peer_link = LAN_NIC, LAN_NIC
        subnet = None
    else:
        vid = ACCESS_VID if shape == "access" else TAGGED_VID
        # vlan_default_pvid 0 first: the default of 1 would otherwise install
        # a PVID nothing here asked for, and the bridge resolves its FDB
        # lookup on whichever VLAN the frame ends up in.
        await dut("ip", "link", "set", BRIDGE, "type", "bridge",
                  "vlan_filtering", "1", "vlan_default_pvid", "0")
        port_flags = ["pvid", "untagged"] if shape == "access" else []
        await dut("bridge", "vlan", "add", "dev", TARGET_LAN_IF, "vid", str(vid), *port_flags)
        await dut("bridge", "vlan", "add", "dev", BRIDGE, "vid", str(vid), "self")
        dut_if = await dut_vlan_subif(stack, r.target, r.session, parent=BRIDGE, vid=vid,
                                      name=f"{BRIDGE}.{vid}")
        r.dut_lan_if, r.bridge_text = dut_if, f"{BRIDGE}.{vid}"
        if shape == "access":
            # The station stays untagged on the wire; the bridge puts the
            # frame into the VLAN and takes the tag off again on the way out.
            await dut("ip", "addr", "add", dut_lan, "dev", dut_if)
            r.peer_if, r.peer_link = LAN_NIC, LAN_NIC
            subnet = None
        else:
            await dut("ip", "addr", "add", f"{DUT_TAGGED_ADDR}/24", "dev", dut_if)
            lan_if = await lan_vlan_subif(
                stack, r.lan, parent=LAN_NIC, vid=vid, ipv4=f"{LAN_TAGGED_ADDR}/24",
                routes=[f"{WAN_IP}/32 via {DUT_TAGGED_ADDR} dev vlan{vid}"])
            r.peer_if, r.peer_link = lan_if, LAN_NIC
            subnet = TAGGED_SUBNET

    def lan_json(cmd):
        result = r.lan.run(cmd, timeout=10)
        assert result.rc == 0, result.stdout
        return json.loads(result.stdout.strip())

    r.lan_ip = next(a["local"] for a in lan_json(f"ip -j -4 addr show dev {r.peer_if}")[0]["addr_info"]
                    if a["family"] == "inet")
    r.peer_mac = r.lan_mac = lan_json(f"ip -j link show dev {r.peer_if}")[0]["address"]
    # The Ethernet source of a bridged neighbour-output flow is the physical
    # port's, and a bridge takes its lowest port's address, so the two agree.
    # That agreement is the eligibility rule, not an accident, so assert it.
    r.dut_lan_mac = (await read(r.target, r.session,
                                f"/sys/class/net/{TARGET_LAN_IF}/address")).strip()
    for device in {BRIDGE, r.dut_lan_if}:
        assert (await read(r.target, r.session,
                           f"/sys/class/net/{device}/address")).strip() == r.dut_lan_mac, device
    r.peer_gateway_mac = r.dut_lan_mac
    return subnet


@pytest_asyncio.fixture
async def bridge_rig(target_agent, aiohttp_session, lan, splat_window, request):
    """LAN VM -> bridged segment -> DUT -> unbridged WAN host.

    The parameter selects the shape: "plain" (default), "access", "tagged", or
    "tcp" for an established connection over the access shape. Teardown
    reverses only what came up, so a partial setup leaves nothing for the next
    case to inherit -- including the LAN address, which moves onto the bridge.
    """
    shape = getattr(request, "param", "plain")
    assert shape in {"plain", "access", "tagged", "tcp"}
    r = Rig()
    r.proto = "tcp" if shape == "tcp" else "udp"
    r.target, r.session, r.lan, r.sequence = target_agent, aiohttp_session, lan, 1
    r.recovery_console = None
    initial = await r.state()
    assert initial["entries"] == initial["bindings"] == initial["invalidated"] == 0, initial
    r.wan = Agent("wan", f"http://{os.environ.get('ASK_WAN_IP', '127.0.0.1')}:9110")
    stack = TopologyStack()
    cleanup = []
    transport = None
    try:
        await command(r.target, r.session, "modprobe", "xt_tcpudp")
        subnet = await _bridged_segment(r, stack, "access" if shape == "tcp" else shape)
        r.dut_wan_mac = (await read(r.target, r.session,
                                    f"/sys/class/net/{TARGET_WAN_IF}/address")).strip()
        addresses = json.loads((await command(r.wan, r.session, "ip", "-j", "-4", "addr"))["stdout"])
        r.wan_if = next(i["ifname"] for i in addresses
                        if any(a.get("local") == WAN_IP for a in i["addr_info"]))
        r.wan_mac = json.loads((await command(r.wan, r.session, "ip", "-j", "link", "show",
                                              "dev", r.wan_if))["stdout"])[0]["address"]
        dut = json.loads((await command(r.target, r.session, "ip", "-j", "-4", "addr",
                                        "show", "dev", TARGET_WAN_IF))["stdout"])[0]
        r.dut_wan_ip = next(a["local"] for a in dut["addr_info"] if a["family"] == "inet")
        reachable = subnet or f"{r.lan_ip}/32"
        await command(r.wan, r.session, "ip", "route", "replace", reachable,
                      "via", r.dut_wan_ip, "dev", r.wan_if)
        cleanup.append((r.wan, ["ip", "route", "del", reachable, "via", r.dut_wan_ip,
                                "dev", r.wan_if]))
        # Pin both neighbours so admission never races ARP. The LAN one belongs
        # to the bridge, or to the VLAN device above it; the same address on
        # the port underneath would be a different neighbour and not be found.
        for ip, mac, dev in ((r.lan_ip, r.lan_mac, r.dut_lan_if),
                             (WAN_IP, r.wan_mac, TARGET_WAN_IF)):
            await command(r.target, r.session, "ip", "neigh", "replace", ip, "lladdr", mac,
                          "nud", "permanent", "dev", dev)
            cleanup.append((r.target, ["ip", "neigh", "del", ip, "dev", dev]))
        # Without this the image's own masquerade rule would translate the
        # routed case and it would silently stop being a routed case.
        accept = ["POSTROUTING", "-s", r.lan_ip, "-d", WAN_IP, "-p", r.proto,
                  "--sport", str(SPORT), "--dport", str(DPORT), "-j", "ACCEPT"]
        await command(r.target, r.session, "iptables", "-t", "nat", "-I", *accept)
        cleanup.append((r.target, ["iptables", "-t", "nat", "-D", *accept]))
        old_acct = (await read(r.target, r.session,
                               "/proc/sys/net/netfilter/nf_conntrack_acct")).strip()
        await command(r.target, r.session, "sysctl", "-w", "net.netfilter.nf_conntrack_acct=1")
        cleanup.append((r.target, ["sysctl", "-w",
                                   f"net.netfilter.nf_conntrack_acct={old_acct}"]))
        # The station's own frames teach the bridge which port it is on, and
        # nothing is admissible until they have: br_fill_forward_path() needs
        # the entry to resolve an egress port at all.
        await lan_run(r.lan, f"ping -c 2 -W 2 -I {r.peer_if} {WAN_IP} >/dev/null 2>&1; true", 12.0)
        await r.clear_ct()
        if r.proto == "udp":
            transport, r.echo = await asyncio.get_running_loop().create_datagram_endpoint(
                SourceEcho, local_addr=(WAN_IP, DPORT))
        r.record("bridge-fixture", {"lan": r.lan_ip, "wan": WAN_IP, "shape": shape,
                                    "bridge": r.bridge_text, "dut_if": r.dut_lan_if,
                                    "peer_if": r.peer_if, "lan_mac": r.lan_mac,
                                    "dut_lan_mac": r.dut_lan_mac, "initial": initial})
        yield r
    finally:
        if transport:
            transport.close()
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
        await stack.teardown("flowtable-bridge")
        # The LAN address is the one piece of state a later fixture cannot
        # recover on its own, so prove it came back rather than assume it.
        restored = json.loads((await command(r.target, r.session, "ip", "-j", "-4", "addr",
                                             "show", "dev", TARGET_LAN_IF))["stdout"])
        if not restored or not restored[0]["addr_info"]:
            failures.append(f"{TARGET_LAN_IF} left without an address")
        assert not failures, failures


async def _both_directions(r):
    """Admission is directional, so one direction refused leaves the other
    accelerated and the difference is invisible in a throughput number. When
    the count is wrong, say what Linux and the adapter each thought."""
    # Admission is asynchronous and takes rtnl_trylock, so a direction can be
    # deferred a second or two when the bench's own ip commands hold RTNL; give
    # the deferred readmission that window before treating a missing direction
    # as a refusal. A genuine refusal still fails, just after the wait.
    state = await r.state()
    for _ in range(100):
        if len(state["flows"]) == 2:
            break
        await asyncio.sleep(0.1)
        state = await r.state()
    if len(state["flows"]) != 2:
        conntrack = await command(r.target, r.session, "conntrack", "-L", "-o", "extended",
                                  check=False)
        r.record("bridge-partial-admission", {"state": state, "conntrack": conntrack})
        pytest.fail(f"{len(state['flows'])} of 2 directions admitted: "
                    f"validated={state['validated']} rejects={state['rejects']} "
                    f"busy={state['busy']} errors={state['errors']}\n"
                    f"flows={state['flows']}\nconntrack={conntrack['stdout']}")
    return state["flows"]


async def _established(r, count=64):
    """Install the flow, then measure a second burst against the hardware."""
    await r.table()
    await r.exchange(count=4)
    flows = await _both_directions(r)
    before = {f["cookie"]: int(f["packets"]) for f in flows}
    await r.exchange(count=count)
    state = await r.state()
    after = {f["cookie"]: int(f["packets"]) for f in state["flows"]}
    if set(before) != set(after):
        # Which dependency moved is the whole diagnosis, and the cookies alone
        # never say. Report every invalidation counter rather than two sets.
        r.record("bridge-readmitted", {"before": before, "state": state})
        pytest.fail("a direction was readmitted mid-measurement: "
                    f"before={before} after={after}\n" +
                    " ".join(f"{k}={state[k]}" for k in sorted(state)
                             if k.endswith("invalidations") or
                             k in ("invalidated", "invalidation_done", "rearms",
                                   "errors", "rejects", "busy", "installs", "deletes")))
    return flows, {c: after[c] - before[c] for c in before}


def _assert_ports(r, forward, reverse):
    """Both directions name the physical ports; a bridge never becomes one."""
    assert forward["in"] == TARGET_LAN_IF and forward["out"] == TARGET_WAN_IF, forward
    assert reverse["in"] == TARGET_WAN_IF and reverse["out"] == TARGET_LAN_IF, reverse
    # The bridge is named on the direction that crosses it, and only there.
    assert forward["in_br"] == r.bridge_text and forward["out_br"] == "-", forward
    assert reverse["out_br"] == r.bridge_text and reverse["in_br"] == "-", reverse


async def test_routed(bridge_rig):
    """A bridged LAN and an unbridged WAN, routed, with no translation."""
    r = bridge_rig
    flows, delta = await _established(r)
    forward = _direction(flows, r.lan_ip, WAN_IP)
    reverse = _direction(flows, WAN_IP, r.lan_ip)
    _assert_ports(r, forward, reverse)
    # A bridge with VLAN filtering off adds no tag and keys its FDB on zero,
    # so the bridge is named without one.
    assert forward["in_vlan"] == "-" and forward["out_vlan"] == "-", forward
    assert reverse["in_vlan"] == "-" and reverse["out_vlan"] == "-", reverse
    assert all(d == 64 for d in delta.values()), delta
    # The hardware carries the traffic, so it never reaches the bridge, and
    # the station stays learned on exactly the port the entry was pinned to.
    assert await _fdb(r, r.lan_mac) == [TARGET_LAN_IF]
    r.record("bridge-routed", {"flows": flows, "delta": delta})


async def test_snat(bridge_rig):
    """Source NAT across the bridge, proved at the far endpoint.

    The WAN host observing the translated source is what separates a rewrite
    that reached the wire from one that only reached the rule.
    """
    r = bridge_rig
    nat = (f"table ip {NAT_TABLE} {{ chain postrouting {{ "
           f"type nat hook postrouting priority 90; "
           f"ip saddr {r.lan_ip} ip daddr {WAN_IP} udp sport {SPORT} udp dport {DPORT} "
           f"snat to {SNAT_ADDR}; }}; }}")
    await command(r.target, r.session, "nft", nat)
    await command(r.wan, r.session, "ip", "route", "replace", f"{SNAT_ADDR}/32",
                  "via", r.dut_wan_ip, "dev", r.wan_if)
    try:
        flows, delta = await _established(r)
        forward = _direction(flows, r.lan_ip, WAN_IP)
        assert forward["new_src"].startswith(SNAT_ADDR + ":"), forward
        reverse = _direction(flows, WAN_IP, SNAT_ADDR)
        assert reverse["new_dst"].startswith(r.lan_ip + ":"), reverse
        _assert_ports(r, forward, reverse)
        assert all(d == 64 for d in delta.values()), delta
        # What the wire carried, not what the rule said it would.
        assert r.echo.sources == {(SNAT_ADDR, SPORT)}, r.echo.sources
        r.record("bridge-snat", {"flows": flows, "delta": delta,
                                 "observed": sorted(r.echo.sources)})
    finally:
        await command(r.target, r.session, "nft", "delete", "table", "ip", NAT_TABLE,
                      check=False)
        await command(r.wan, r.session, "ip", "route", "del", f"{SNAT_ADDR}/32", check=False)


@pytest.mark.parametrize("bridge_rig", ["access"], indirect=True)
async def test_vlan_access(bridge_rig):
    """The configuration that actually ships: br-ft.N over an untagged port.

    A VLAN device is in the path and the bridge resolves its FDB inside that
    VLAN, but the egress port is untagged for it, so the wire carries no tag
    at all. A derivation that stopped at the netdevs would push one, and the
    station would receive a frame it cannot parse.
    """
    r = bridge_rig
    flows, delta = await _established(r)
    forward = _direction(flows, r.lan_ip, WAN_IP)
    reverse = _direction(flows, WAN_IP, r.lan_ip)
    _assert_ports(r, forward, reverse)
    # The VID the bridge keyed its lookup on is recorded even though it is on
    # neither wire, which is exactly what distinguishes this from "plain".
    assert forward["in_br"] == f"{BRIDGE}.{ACCESS_VID}", forward
    assert forward["in_vlan"] == "-" and reverse["out_vlan"] == "-", (forward, reverse)
    assert all(d == 64 for d in delta.values()), delta
    r.record("bridge-vlan-access", {"flows": flows, "delta": delta})


@pytest.mark.parametrize("bridge_rig", ["tagged"], indirect=True)
async def test_vlan_tagged(bridge_rig):
    """br-ft.N over a port that is tagged for N: the tag reaches the wire.

    Same devices as the access shape and a different bridge VLAN membership,
    so the only thing that can produce the difference is the membership.
    """
    r = bridge_rig
    flows, delta = await _established(r)
    forward = _direction(flows, r.lan_ip, WAN_IP)
    reverse = _direction(flows, WAN_IP, r.lan_ip)
    _assert_ports(r, forward, reverse)
    assert forward["in_br"] == f"{BRIDGE}.{TAGGED_VID}", forward
    assert forward["in_vlan"] == str(TAGGED_VID) and forward["out_vlan"] == "-", forward
    assert reverse["out_vlan"] == str(TAGGED_VID) and reverse["in_vlan"] == "-", reverse
    assert all(d == 64 for d in delta.values()), delta
    r.record("bridge-vlan-tagged", {"flows": flows, "delta": delta})


async def test_fdb_roaming(bridge_rig):
    """A station that reappears on another bridge port retires its flow.

    br_fill_forward_path() chose this flow's egress port from the FDB, so the
    hardware entry is pinned to it. Nothing in Linux retires a flow when that
    entry moves. The station is made to roam for real -- a second bridge port
    is added on the same wire and the LAN VM sends from its own address
    through it, so the bridge relearns rather than being told to.
    """
    r = bridge_rig
    stack = TopologyStack()
    lan_if = f"vlan{ROAM_VID}"
    try:
        # Both pseudo-ports exist before the flowtable binds. Adding an upper
        # to a bound port is a configuration change the adapter answers with
        # full invalidation, which would retire the flow for a reason that has
        # nothing to do with the FDB.
        roam_port = await dut_vlan_subif(stack, r.target, r.session, parent=TARGET_LAN_IF,
                                         vid=ROAM_VID, master=BRIDGE)
        # The station's side is created down and stays down: an interface
        # brought up with an address immediately emits its own multicast
        # listener and duplicate-address traffic, which is itself a frame from
        # the station's MAC and would move the entry before the measurement.
        await lan_run(r.lan, f"ip link del {lan_if} 2>/dev/null; "
                             f"ip link add link {LAN_NIC} name {lan_if} "
                             f"type vlan id {ROAM_VID}", 10.0)

        async def _drop_roam_if():
            await lan_run(r.lan, f"ip link del {lan_if} 2>/dev/null", 5.0)
        stack.push(_drop_roam_if)

        flows, delta = await _established(r)
        assert all(d == 64 for d in delta.values()), delta
        assert await _fdb(r, r.lan_mac) == [TARGET_LAN_IF]
        before = await r.state()
        # One frame from the station's own address, arriving on the other port.
        # A VLAN device inherits its parent's address, so the ARP the station
        # broadcasts for a host that does not exist carries exactly the MAC the
        # bridge has learned on the first port, tagged onto the second.
        await lan_run(r.lan, f"ip link set {lan_if} up; "
                             f"ip addr add {ROAM_ADDR}/24 dev {lan_if}", 20.0)
        # The roam port and the parent carry the same MAC, so the parent's own
        # background traffic can relearn the entry back onto it between reads. A
        # single snapshot after the first probe therefore races. Re-send the
        # tagged probe and poll until the bridge both shows the address on the
        # roam port and has told the adapter to invalidate; seeing the roam port
        # once is proof the relearn happened, and the retirement below is the
        # invariant the test is really asserting.
        loop = asyncio.get_running_loop()
        deadline = loop.time() + 20
        moved = None
        while moved is None:
            await lan_run(r.lan, f"ping -c 1 -W 1 -I {lan_if} {ROAM_PROBE} "
                                 f">/dev/null 2>&1; true", 12.0)
            s = await r.state()
            if (s["fdb_invalidations"] >= before["fdb_invalidations"] + 1
                    and await _fdb(r, r.lan_mac) == [roam_port]):
                moved = s
            elif loop.time() >= deadline:
                pytest.fail(f"station did not roam to {roam_port}: "
                            f"fdb={await _fdb(r, r.lan_mac)} state={s}")
            else:
                await asyncio.sleep(0.5)
        retired = await r.wait(lambda s: not s["entries"], timeout=15)
        assert retired["errors"] == before["errors"], retired
        r.record("bridge-fdb-roaming", {"flows": flows, "before": before,
                                        "moved": moved, "retired": retired})
        # Ordinary traffic teaches the bridge the station is back on the first
        # port, and the flow is admitted again against it. Take the station's
        # other interface down first, so its own background traffic cannot
        # move the entry back underneath the readmission.
        await lan_run(r.lan, f"ip link set {lan_if} down", 10.0)
        await r.exchange(count=4)
        assert await _fdb(r, r.lan_mac) == [TARGET_LAN_IF]
        readmitted = await _both_directions(r)
        assert all(f["out_br"] in (r.bridge_text, "-") for f in readmitted), readmitted
        after = {f["cookie"]: int(f["packets"]) for f in readmitted}
        await r.exchange(count=32)
        final = {f["cookie"]: int(f["packets"]) for f in await _flows(r)}
        assert all(final[c] - after[c] == 32 for c in after), (after, final)
    finally:
        # Removing an upper from a bound port invalidates everything, so drain
        # the binding first; the fixture's own teardown is then a no-op rather
        # than something the next test inherits an invalidation from.
        await r.delete_table()
        await stack.teardown("flowtable-bridge-roaming")


async def test_fdb_ageing(bridge_rig):
    """An FDB entry that ages out under the flow retires it too.

    Hardware forwarding never reaches the bridge, so nothing refreshes the
    entry while the flow is carrying traffic, and a pinned neighbour means no
    ARP crosses the bridge either. The entry therefore ages out under a live
    flow, which is the case that would silently misforward if the delete were
    not watched. A dynamic neighbour behaves differently and deliberately so:
    the adapter solicits ARP on hardware activity and the reply keeps the
    entry warm -- that is a property of the neighbour watch, not of this one.
    """
    r = bridge_rig
    flows, delta = await _established(r)
    assert all(d == 64 for d in delta.values()), delta
    before = await r.state()
    assert await _fdb(r, r.lan_mac) == [TARGET_LAN_IF]
    # Shorten the ageing time only now: setting it in the fixture would expire
    # the station under every other case in this file too, because none of
    # them send anything the bridge itself sees once the flow is installed.
    await command(r.target, r.session, "ip", "link", "set", BRIDGE, "type", "bridge",
                  "ageing_time", str(AGEING_CS))
    aged = await r.wait(
        lambda s: s["fdb_invalidations"] >= before["fdb_invalidations"] + 1,
        timeout=AGEING_CS / 100 * 3 + 10)
    assert await _fdb(r, r.lan_mac) == []
    retired = await r.wait(lambda s: not s["entries"], timeout=15)
    assert retired["errors"] == before["errors"], retired
    r.record("bridge-fdb-ageing", {"flows": flows, "before": before,
                                   "aged": aged, "retired": retired})
    # Put the ageing time back before readmitting. Leaving it short would have
    # the entry expire again between the traffic that relearns it and the
    # admission that depends on it, which is a race against the harness rather
    # than anything about the adapter.
    await command(r.target, r.session, "ip", "link", "set", BRIDGE, "type", "bridge",
                  "ageing_time", "30000")
    # Traffic relearns the station and readmits the flow against it. Each
    # attempt sends before it looks: readmission needs both a bridge that has
    # seen the station again and a Linux flow that has finished retiring, and
    # nothing re-offers a retired flow on its own.
    for _ in range(10):
        await r.exchange(count=4)
        state = await r.state()
        if state["entries"] == 2:
            break
    else:
        pytest.fail(f"flow was not readmitted after the entry aged out: {state}")
    assert await _fdb(r, r.lan_mac) == [TARGET_LAN_IF]


async def test_device_retires(bridge_rig):
    """The bridge carries its own MTU and link state, and the flow depends on both.

    Each direction carries the MTU of the interface it leaves by, so lowering
    the bridge touches only the direction leaving by it. Both directions share
    one invalidation handle, so retiring the connection is a single increment
    rather than two.

    Lowered below a full frame, the bridge is a path the reverse's datagrams
    -- arriving on the WAN port, which can deliver 1500 bytes whatever its
    MTU -- no longer fit, and the microcode would have to fragment them. So
    the flow comes back with the forward direction alone in hardware and the
    reverse refused to Linux, which fragments correctly.
    """
    r = bridge_rig
    await r.table()

    async def settled(expected, since=None):
        """`expected` maps the egress port of each direction hardware should
        hold to the MTU it should describe; `since`, a state the directions
        must have been installed after. Readmission needs traffic, so each
        attempt sends before it looks; nothing re-offers a retired flow on its
        own."""
        for _ in range(10):
            await r.exchange(count=4)
            state = await r.state()
            if (sorted(f["out"] for f in state["flows"]) == sorted(expected)
                    and all(int(f["mtu"]) == expected[f["out"]] for f in state["flows"])
                    and (since is None or state["installs"] > since["installs"])):
                return state
        pytest.fail(f"flow did not settle at {expected}: {state}")

    before = await settled({TARGET_LAN_IF: 1500, TARGET_WAN_IF: 1500})
    await command(r.target, r.session, "ip", "link", "set", BRIDGE, "mtu", "1400")
    try:
        invalidated = await r.wait(
            lambda s: s["mtu_invalidations"] >= before["mtu_invalidations"] + 1)
        # Installed since, so a state caught mid-retirement cannot pass for
        # the readmitted one.
        reduced = await settled({TARGET_WAN_IF: 1500}, since=before)
        assert reduced["errors"] == before["errors"], reduced
        assert reduced["rejects"] > invalidated["rejects"], (invalidated, reduced)
        r.record("bridge-mtu", {"before": before, "invalidated": invalidated,
                                "reduced": reduced})
    finally:
        await command(r.target, r.session, "ip", "link", "set", BRIDGE, "mtu", "1500",
                      check=False)


@pytest.mark.parametrize("bridge_rig", ["tcp"], indirect=True)
async def test_tcp(bridge_rig):
    """An established TCP connection over the shipping bridged topology.

    The classifier punts SYN, FIN and RST before its own lookup, so what the
    hardware actually carries is the bulk transfer in the middle. The cookies
    staying put is what proves the connection was never readmitted underneath
    it, and the FDB entry staying put is what proves the pinning held.
    """
    r = bridge_rig
    await r.table()
    async with GatedTcp(r.run_peer, source=r.lan_ip, sport=SPORT, peer=WAN_IP,
                        dport=DPORT, label="flowtable_bridge_tcp") as transfer:
        await transfer.warmed()
        before = await r.wait(lambda s: len(s["flows"]) == 2)
        await transfer.measure()
        after = await r.state()
    r.record("bridge-tcp", {"before": before, "after": after, "report": transfer.report})
    forward = _direction(before["flows"], r.lan_ip, WAN_IP)
    reverse = _direction(before["flows"], WAN_IP, r.lan_ip)
    _assert_ports(r, forward, reverse)
    assert forward["in_vlan"] == "-" and reverse["out_vlan"] == "-", (forward, reverse)
    old = {f["cookie"]: int(f["packets"]) for f in before["flows"]}
    new = {f["cookie"]: int(f["packets"]) for f in after["flows"]}
    assert_undisturbed(r, before, after, old.keys() == new.keys()
                       and (before["installs"], before["deletes"]) == (after["installs"], after["deletes"]))
    assert all(new[cookie] - count > 100 for cookie, count in old.items()), (old, new)
