"""One routed connection through a real LAN gateway, without NAT."""
from __future__ import annotations

from contextlib import asynccontextmanager
import json
import os

import pytest

from ask_orch.client import Agent
from _topology import LAN_NIC, TARGET_LAN_IF, TARGET_WAN_IF, lan_run_python
from _flowtable_rig import (DPORT, SPORT, WAN_IP, command)
from _flowtable_arp import (
    CHANGED_MAC,
    arp_environment,
    check_arp_trace,
    invalidated,
    lan_neighbour,
    observe,
    recover,
    udp_hardware,
    wait_neighbour,
)
from _flowtable_tcp import (BLOCK, connection, hardware_transfer, installed)

NETNS = "ask-ft-gateway"
ROUTER_IF, PEER_IF = "askftgw", "askftpeer"
ROUTER_IP, PEER_IP = "198.18.27.1", "198.18.27.2"
ALTERNATE_GW = "198.18.28.2"
ROUTER_MAC, PEER_MAC = "02:00:00:27:00:01", "02:00:00:27:00:02"


@asynccontextmanager
async def gateway_environment(r):
    original_ip = r.lan_ip
    saved = {}
    cleanup = []
    wan = Agent("wan", f"http://{os.environ.get('ASK_WAN_IP', '127.0.0.1')}:9110")
    # Refuse occupied names/addresses; cleanup only resources created here.
    for address in [PEER_IP, ALTERNATE_GW]:
        for agent in [r.target, wan]:
            routes = await command(agent, r.session, "ip", "-j", "route", "show", "exact", address + "/32")
            assert json.loads(routes["stdout"]) == [], routes
    setup = f'''
import json, pathlib, subprocess, sys

def run(*args):
    return subprocess.check_output(args, text=True)
assert not pathlib.Path('/var/run/netns/{NETNS}').exists()
assert not pathlib.Path('/sys/class/net/{ROUTER_IF}').exists()
assert not pathlib.Path('/sys/class/net/{PEER_IF}').exists()
addresses = json.loads(run('ip', '-j', '-4', 'addr'))
assert not any(a['local'] in { [ROUTER_IP, PEER_IP, ALTERNATE_GW]!r} for i in addresses for a in i['addr_info'])
forwarding = pathlib.Path('/proc/sys/net/ipv4/conf/{LAN_NIC}/forwarding')
old = forwarding.read_text().strip()
cleanup = []
try:
    run('ip', 'netns', 'add', {NETNS!r}); cleanup.append(['ip', 'netns', 'del', {NETNS!r}])
    run('ip', 'link', 'add', {ROUTER_IF!r}, 'type', 'veth', 'peer', 'name', {PEER_IF!r})
    cleanup.append(['ip', 'link', 'del', {ROUTER_IF!r}])
    run('ip', 'link', 'set', {PEER_IF!r}, 'netns', {NETNS!r})
    run('ip', 'link', 'set', {ROUTER_IF!r}, 'address', {ROUTER_MAC!r}, 'up')
    run('ip', 'addr', 'add', {ROUTER_IP + '/30'!r}, 'dev', {ROUTER_IF!r})
    run('ip', '-n', {NETNS!r}, 'link', 'set', 'lo', 'up')
    run('ip', '-n', {NETNS!r}, 'link', 'set', {PEER_IF!r}, 'address', {PEER_MAC!r}, 'up')
    run('ip', '-n', {NETNS!r}, 'addr', 'add', {PEER_IP + '/30'!r}, 'dev', {PEER_IF!r})
    run('ip', '-n', {NETNS!r}, 'route', 'add', 'default', 'via', {ROUTER_IP!r})
    run('ip', 'addr', 'add', {ALTERNATE_GW + '/32'!r}, 'dev', {LAN_NIC!r})
    cleanup.append(['ip', 'addr', 'del', {ALTERNATE_GW + '/32'!r}, 'dev', {LAN_NIC!r}])
    forwarding.write_text('1')
    pathlib.Path('/proc/sys/net/ipv4/conf/{ROUTER_IF}/forwarding').write_text('1')
except BaseException:
    forwarding.write_text(old)
    errors = []
    for args in reversed(cleanup):
        result = subprocess.run(args, text=True, capture_output=True)
        if result.returncode: errors.append([args, result.stderr])
    if errors: print('Gateway setup cleanup failed:', errors, file=sys.stderr)
    raise
print(json.dumps({{'forwarding': old}}))
'''
    result = await lan_run_python(r.lan, setup, label="flowtable_gateway_setup", timeout=20)
    assert result.rc == 0, result.stdout
    old_forwarding = json.loads(result.stdout.strip())["forwarding"]
    try:
        # The gateway remains in the LAN VM; only the remote endpoint is namespaced.
        # Its physical MAC and the endpoint's veth MAC are distinct.
        for key, value in {"lan_ip": PEER_IP, "peer_netns": NETNS, "peer_if": PEER_IF,
                           "peer_mac": PEER_MAC, "peer_gateway_mac": ROUTER_MAC,
                           "forward_hops": 2, "arp_address": original_ip,
                           "arp_tag": f"gateway-{r.proto}",
                           "arp_neighbours": [(original_ip, TARGET_LAN_IF), (WAN_IP, TARGET_WAN_IF)]}.items():
            saved[key] = (hasattr(r, key), getattr(r, key, None))
            setattr(r, key, value)
        dut_ip = next(a["local"] for i in json.loads((await command(r.target, r.session, "ip", "-j", "-4", "addr", "show", "dev", TARGET_WAN_IF))["stdout"])
                      for a in i["addr_info"] if a["family"] == "inet")
        for agent, args, undo in [
            (r.target, ["ip", "route", "add", ALTERNATE_GW + "/32", "dev", TARGET_LAN_IF],
                       ["ip", "route", "del", ALTERNATE_GW + "/32", "dev", TARGET_LAN_IF]),
            (r.target, ["ip", "route", "add", PEER_IP + "/32", "via", original_ip, "dev", TARGET_LAN_IF],
                       ["ip", "route", "del", PEER_IP + "/32", "dev", TARGET_LAN_IF]),
            (wan, ["ip", "route", "add", PEER_IP + "/32", "via", dut_ip, "dev", r.wan_if],
                  ["ip", "route", "del", PEER_IP + "/32", "via", dut_ip, "dev", r.wan_if]),
        ]:
            await command(agent, r.session, *args)
            cleanup.append((agent, undo))
        nat = ["POSTROUTING", "-s", PEER_IP, "-d", WAN_IP, "-p", r.proto,
               "--sport", str(SPORT), "--dport", str(DPORT), "-j", "ACCEPT"]
        await command(r.target, r.session, "iptables", "-t", "nat", "-I", *nat)
        cleanup.append((r.target, ["iptables", "-t", "nat", "-D", *nat]))
        await r.clear_ct()
        r.record(f"gateway-{r.proto}-fixture", {"endpoint": PEER_IP, "gateway": original_ip,
                 "alternate_gateway": ALTERNATE_GW, "gateway_mac": r.lan_mac,
                 "peer_mac": PEER_MAC, "router_mac": ROUTER_MAC,
                 "dut_route": await command(r.target, r.session, "ip", "-j", "route", "get", PEER_IP)})
        yield original_ip
    finally:
        failures = []

        async def attempt(operation):
            try:
                return await operation
            except Exception as error:
                failures.append(str(error))
                return None

        for operation in [r.delete_table, r.clear_ct]:
            await attempt(operation())
        for agent, args in reversed(cleanup):
            await attempt(command(agent, r.session, *args))
        # A diagnostic read failure must not skip independent LAN cleanup.
        remote_neigh = await attempt(command(r.target, r.session, "ip", "-j", "neigh", "show", "to", PEER_IP, "dev", TARGET_LAN_IF))
        if remote_neigh and json.loads(remote_neigh["stdout"]):
            failures.append(remote_neigh)
        await attempt(command(r.target, r.session, "ip", "neigh", "del", ALTERNATE_GW, "dev", TARGET_LAN_IF, check=False))
        teardown = f'''
import pathlib, subprocess
pathlib.Path('/proc/sys/net/ipv4/conf/{LAN_NIC}/forwarding').write_text({old_forwarding!r})
errors = []
for args in [['ip', 'addr', 'del', {ALTERNATE_GW + '/32'!r}, 'dev', {LAN_NIC!r}],
             ['ip', 'link', 'del', {ROUTER_IF!r}], ['ip', 'netns', 'del', {NETNS!r}]]:
    result = subprocess.run(args, text=True, capture_output=True)
    if result.returncode: errors.append([args, result.stderr])
assert not errors, errors
'''
        result = await attempt(lan_run_python(r.lan, teardown, label="flowtable_gateway_cleanup", timeout=15))
        if result and result.rc:
            failures.append(result.stdout)
        for key, (existed, value) in saved.items():
            if existed:
                setattr(r, key, value)
            else:
                delattr(r, key)
        assert not failures, failures


async def assert_gateway(r, address):
    state = await r.state()
    assert state["entries"] == state["neighbour_refs"] == 2, state
    flows = {f["out"]: f for f in state["flows"]}
    assert flows[TARGET_LAN_IF]["dst"] == f"{PEER_IP}:{SPORT}", flows
    assert flows[TARGET_LAN_IF]["nexthop"] == address, flows
    assert flows[TARGET_WAN_IF]["nexthop"] == WAN_IP, flows
    endpoint = await command(r.target, r.session, "ip", "-j", "neigh", "show", "to", PEER_IP)
    assert json.loads(endpoint["stdout"]) == [], endpoint
    return state


async def switch_gateway(r, before):
    await command(r.target, r.session, "ip", "route", "replace", PEER_IP + "/32",
                  "via", ALTERNATE_GW, "dev", TARGET_LAN_IF)
    await invalidated(r, before, "gateway-route-invalidated", counter="route_invalidations")
    r.arp_address = ALTERNATE_GW
    r.arp_neighbours.append((ALTERNATE_GW, TARGET_LAN_IF))
    r.record(f"gateway-{r.proto}-route-changed", await command(r.target, r.session, "ip", "-j", "route", "get", PEER_IP))


async def test_udp(rig):
    r = rig
    original_mac = r.lan_mac
    async with gateway_environment(r) as gateway:
        async with arp_environment(r):
            await r.table()
            await udp_hardware(r, count=1024, interval=0.01, label="gateway-ageing")
            before = await assert_gateway(r, gateway)
            r.lan_mac = CHANGED_MAC
            await lan_neighbour(r, mac=CHANGED_MAC)
            await invalidated(r, before, "gateway-mac-invalidated")
            await wait_neighbour(r, lambda ns: ns.get(gateway, {}).get("lladdr") == CHANGED_MAC)
            await r.exchange(64, promiscuous=False)
            await recover(r)
            await udp_hardware(r, label="gateway-mac-recovered")
            before = await assert_gateway(r, gateway)
            await switch_gateway(r, before)
            await r.exchange(64, promiscuous=False)
            await recover(r)
            await udp_hardware(r, label="gateway-route-recovered")
            before = await assert_gateway(r, ALTERNATE_GW)
            failure = await lan_neighbour(r, arp_ignore=8)
            await command(r.target, r.session, "ip", "neigh", "change", ALTERNATE_GW,
                          "dev", TARGET_LAN_IF, "nud", "stale")
            await r.exchange(32, promiscuous=False)
            failed = await wait_neighbour(r, lambda ns: "FAILED" in ns.get(ALTERNATE_GW, {}).get("state", []), timeout=6)
            await invalidated(r, before, "gateway-unreachable")
            restored = await lan_neighbour(r, arp_ignore=0)
            r.record("gateway-udp-fault", {"start": failure, "restored": restored, "failed": failed})
            await r.exchange(64, promiscuous=False)
            await recover(r)
            await udp_hardware(r, label="gateway-reachability-recovered")
            await assert_gateway(r, ALTERNATE_GW)
        check_arp_trace(r, original_mac, failure["time"], restored["time"])


@pytest.mark.parametrize("rig", ["tcp"], indirect=True)
async def test_tcp(rig):
    r = rig
    original_mac = r.lan_mac
    async with gateway_environment(r) as gateway:
        async with arp_environment(r):
            await r.table()
            async with connection(r) as conn:
                await installed(r, conn)
                before = await assert_gateway(r, gateway)
                await observe(r, hardware_transfer(r, conn, "upload", label="gateway-tcp-ageing"), "gateway-ageing")
                r.lan_mac = CHANGED_MAC
                # Keep control replies off the flow whose retirement we check.
                await lan_neighbour(r, mac=CHANGED_MAC)
                await invalidated(r, before, "gateway-mac-invalidated")
                await conn.transfer("download")
                await recover(r)
                await installed(r, conn)
                await hardware_transfer(r, conn, "download", label="gateway-tcp-mac-recovered")
                before = await assert_gateway(r, gateway)
                await switch_gateway(r, before)
                await conn.transfer("upload")
                await recover(r)
                await installed(r, conn)
                before = await assert_gateway(r, ALTERNATE_GW)
                failure = await conn.configure_neighbour(LAN_NIC, address=ALTERNATE_GW, arp_ignore=8, restore_after=8)
                await command(r.target, r.session, "ip", "neigh", "change", ALTERNATE_GW,
                              "dev", TARGET_LAN_IF, "nud", "stale")
                await conn.transfer("upload", size=len(BLOCK))
                failed = await wait_neighbour(r, lambda ns: "FAILED" in ns.get(ALTERNATE_GW, {}).get("state", []), timeout=6)
                await invalidated(r, before, "gateway-unreachable")
                report = await conn.transfer("upload")
                r.record("gateway-tcp-fault", {"start": failure, "failed": failed, "resumed": report})
                await recover(r)
                await installed(r, conn)
                await hardware_transfer(r, conn, "upload", label="gateway-tcp-reachability-recovered")
                await assert_gateway(r, ALTERNATE_GW)
                await conn.close("fin")
                await r.wait(lambda s: s["entries"] == s["neighbour_refs"] == 0, timeout=3)
        check_arp_trace(r, original_mac, failure["time"], failure["time"] + 6)
