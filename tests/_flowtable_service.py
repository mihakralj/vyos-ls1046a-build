"""Shared flowtable service fixtures and scenarios."""

from __future__ import annotations

import asyncio
import ipaddress
import json
import re
import time
from contextlib import asynccontextmanager
from pathlib import Path

import pytest
import pytest_asyncio
from _flowtable_rig import (
    DPORT,
    SPORT,
    WAN_IP,
    artifact_dir,
    command,
    console_command,
    console_json,
    console_python,
    flowtable_json,
    read,
)
from _topology import TARGET_LAN_IF, TARGET_WAN_IF
from ask_orch.uart import Console

DAEMON = "/usr/sbin/ask-flowtable"

INIT = "/etc/init.d/ask-flowtable"

CONF = "/etc/ask/offload.conf"

FAULT_DIR = "/tmp/ask-flowtable-service-fault"

FIRST = SPORT + 512

OBSERVE_TABLE = "ask_recovery_observe"

FLOWS = [{"id": i, "proto": proto, "sport": FIRST + offset}
         for i, (proto, offset) in enumerate((("udp", 0), ("tcp", 0), ("tcp", 1), ("udp", 2)))]


async def service_status(r):
    return await flowtable_json(r.service_console, "status")


async def supervision_status(r):
    return await flowtable_json(r.service_console, "service-status")


async def software_forwarded(r):
    """Only slow-path packets from the test port pair, in either direction."""
    result = await command(r.target, r.session, "nft", "-j", "list", "counter", "inet", OBSERVE_TABLE, "slow_path")
    counters = [item["counter"] for item in json.loads(result["stdout"])["nftables"] if "counter" in item]
    assert len(counters) == 1, result
    return counters[0]["packets"]


async def wait_replacement(r, previous, timeout=8):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        state = await supervision_status(r)
        if state.get("worker_pid", 0) not in (0, int(previous)):
            return state
        await asyncio.sleep(0.1)
    pytest.fail(f"supervisor did not replace worker {previous}: {state}")


async def wait_service(r, ready=True, timeout=12, policy_hash=None):
    deadline = time.monotonic() + timeout
    samples = []
    while time.monotonic() < deadline:
        status = await service_status(r)
        samples.append({"time": time.monotonic(), "status": status})
        if status["admission_ready"] == ready and (policy_hash is None or status["policy_hash"] == policy_hash):
            return samples
        await asyncio.sleep(0.2)
    r.record("service-recovery-timeout", samples)
    pytest.fail(f"service failed to converge in {timeout}s: {samples}")


@pytest_asyncio.fixture
async def service(rig):
    async with managed_service(rig) as r:
        yield r


@asynccontextmanager
async def managed_service(r, addresses=None, *, extra_paths=(), devices=(TARGET_LAN_IF, TARGET_WAN_IF)):
    """Run the service for IPv4 endpoints and extra (source, destination) pairs.

    `devices` is the policy's device list; ("auto",) leaves it to the daemon."""
    addresses = tuple(addresses or (r.lan_ip,))
    paths = [(address, WAN_IP) for address in addresses] + list(extra_paths)
    old = await read(r.target, r.session, CONF)
    cleanup = []
    initial_errors = (await r.state())["errors"]
    with Console.target(log_path=str(artifact_dir() / "service-uart.log")) as con:
        await asyncio.to_thread(con.login, "root", None)
        r.service_console = con
        try:
            tables = json.loads((await command(r.target, r.session, "nft", "-j", "list", "tables"))["stdout"])
            assert not any(item.get("table", {}).get("name") == OBSERVE_TABLE for item in tables["nftables"]), tables
            await command(r.target, r.session, "nft", f'''table inet {OBSERVE_TABLE} {{
 counter slow_path {{ }}
 chain forward {{ type filter hook forward priority -10; policy accept;
 meta l4proto {{ tcp, udp }} ct original proto-src {FIRST}-{FIRST + 2} ct original proto-dst {DPORT} counter name slow_path
 }}
}}''')
            cleanup.append(["nft", "delete", "table", "inet", OBSERVE_TABLE])
            r.software_forwarded = lambda: software_forwarded(r)
            policy = f"enabled yes\ndevices {' '.join(devices)}\n"
            for address, destination in paths:
                version = ipaddress.ip_address(address).version
                assert ipaddress.ip_address(destination).version == version
                firewall = "ip6tables" if version == 6 else "iptables"
                for proto in ("tcp", "udp"):
                    nat = ["POSTROUTING", "-s", address, "-d", destination, "-p", proto,
                           "--sport", f"{FIRST}:{FIRST + 2}", "--dport", str(DPORT), "-j", "ACCEPT"]
                    # The routed IPv6 paths have no NAT; this image does not
                    # build the legacy IPv6 NAT table. IPv4 needs an exemption
                    # from the normal WAN masquerade policy.
                    if version == 4:
                        await command(r.target, r.session, firewall, "-t", "nat", "-I", *nat)
                        cleanup.append([firewall, "-t", "nat", "-D", *nat])
                    await command(r.target, r.session, "conntrack", "-D", "-f", f"ipv{version}", "-p", proto,
                                  "--orig-src", address, "--orig-dst", destination,
                                  "--dport", str(DPORT), check=False)
                deny = ["FORWARD", "-s", address, "-d", destination, "-p", "udp",
                        "--sport", str(FIRST + 2), "--dport", str(DPORT), "-j", "DROP"]
                if version == 6:
                    await console_command(con, firewall, "-I", *deny)
                else:
                    await command(r.target, r.session, firewall, "-I", *deny)
                cleanup.append([firewall, "-D", *deny])
                # Native address selectors are IPv4-only. Port selectors are
                # family-independent and keep IPv6 admission on our reserved
                # test ports; the assertions also check every admitted tuple.
                selector = f"saddr {address} daddr {destination} " if version == 4 else ""
                policy += (f"scope {selector}sport {FIRST}-{FIRST + 2} dport {DPORT}\n")
            result = await r.target.fs_write(r.session, CONF, policy)
            assert result["errno"] == 0, result
            # Resolve the real executable before adding the fault wrapper to
            # PATH. The wrapper is confined to this daemon's environment.
            wrapper = Path(__file__).with_name("_flowtable_service_nft.py").read_text()
            wrapper = wrapper.replace("__FAULT_ROOT__", repr(FAULT_DIR))
            await console_python(con, f'''
from pathlib import Path
import shutil
root = Path({FAULT_DIR!r})
assert not root.exists(), root
root.mkdir()
real = shutil.which('nft')
assert real
script = {wrapper!r}.replace('__REAL_NFT__', repr(real))
compile(script, str(root / 'nft'), 'exec')
(root / 'nft').write_text(script)
(root / 'nft').chmod(0o755)
''')
            await console_command(con, DAEMON, "resume")
            await console_command(con, "sh", "-c", f'PATH={FAULT_DIR}:"$PATH" {INIT} start')
            await wait_service(r)
            r.service_boot = (await read(r.target, r.session, "/proc/sys/kernel/random/boot_id")).strip()
            r.service_pid = (await read(r.target, r.session, "/run/ask-flowtable/worker.pid")).strip()
            r.service_hash = (await service_status(r))["policy_hash"]
            r.supervisor_pid = (await read(r.target, r.session, "/run/ask-flowtable/supervisor.pid")).strip()
            yield r
        finally:
            # Teardown occurs only after the recovery assertions (or failure
            # capture). It must never turn a failed observation into a pass.
            failures = []

            async def attempt(operation):
                try:
                    return await operation
                except Exception as error:
                    failures.append(repr(error))

            status = await attempt(service_status(r))
            r.record("service-final-status", status)
            await attempt(console_command(con, INIT, "stop", timeout=45))
            drained = await attempt(r.state())
            r.record("service-drained", drained)
            if drained and (any(drained[k] for k in ("bindings", "entries", "handle_refs", "neighbour_refs", "quarantine", "fatal"))
                            or drained["installs"] != drained["deletes"] or drained["errors"] != initial_errors):
                failures.append({"unbalanced": drained})
            # UART restoration stays available even if the management agent
            # is one of the things a failed recovery left unreachable.
            await attempt(console_python(con, f"from pathlib import Path\nPath({CONF!r}).write_text({old!r})\n"))
            for argv in reversed(cleanup):
                await attempt(console_command(con, *argv))
            for address, destination in paths:
                for proto in ("tcp", "udp"):
                    await attempt(console_command(con, "conntrack", "-D", "-f",
                                                  f"ipv{ipaddress.ip_address(address).version}", "-p", proto,
                                                  "--orig-src", address, "--orig-dst", destination,
                                                  "--dport", str(DPORT), check=False))
            await attempt(console_command(con, "rm", "-rf", FAULT_DIR))
            assert not failures, ("service fixture restoration failed", failures)


async def blocked_probe(r, p, ident=3):
    received = r.echo.packets
    await p.rpc("start", [ident], count=4, interval=0.01, allow_loss=True, udp_timeout=0.1)
    result = await p.rpc("wait", [ident])
    assert result[str(ident)]["received"] == 0 and result[str(ident)]["lost"] == 4, result
    assert r.echo.packets == received, "forbidden UDP reached WAN"
    return result


ADMISSION_GUARDS = ("meta nfproto != { ipv4, ipv6 } return",
                    "meta l4proto != { 6, 17 } return",
                    "ct direction != original return",
                    "ct state != established return")


def chain_rules(listing, chain):
    """One chain's rule lines from `nft list` text. nft prints a mark padded to
    eight hex digits and the renderer does not, so hex constants are compared
    by value."""
    rules, inside = [], False
    for line in (text.strip() for text in listing.splitlines()):
        if line == f"chain {chain} {{":
            inside = True
        elif inside and line == "}":
            return rules
        elif inside and line and not line.startswith("type "):
            rules.append(re.sub(r"0x[0-9a-f]+", lambda m: hex(int(m[0], 16)), line))
    raise AssertionError((chain, listing))


async def dpaa_ports(r):
    """Every fsl_dpa port with its administrative and operational state, read
    from sysfs by the same test `devices auto` resolution applies."""
    result = await console_python(r.service_console, """
import json, os
ports = {}
for name in sorted(os.listdir('/sys/class/net')):
    driver = '/sys/class/net/%s/device/driver' % name
    if os.path.islink(driver) and os.path.basename(os.readlink(driver)) == 'fsl_dpa':
        with open('/sys/class/net/%s/flags' % name) as flags, open('/sys/class/net/%s/operstate' % name) as state:
            ports[name] = {'up': bool(int(flags.read(), 16) & 1), 'operstate': state.read().strip()}
print(json.dumps(ports))
""")
    return console_json(result["stdout"])


async def rendered_devices(r):
    """The devices the installed flowtable names, or None while no table exists."""
    result = await command(r.target, r.session, "nft", "-j", "list", "flowtable", "inet",
                           "ask_flowtable", "fast", check=False)
    if result["rc"]:
        return None
    flowtable = next(item["flowtable"] for item in json.loads(result["stdout"])["nftables"] if "flowtable" in item)
    devices = flowtable.get("dev", [])
    return sorted([devices] if isinstance(devices, str) else devices)


async def devices_follow(r, expected, timeout=20):
    """Wait until the installed flowtable names exactly `expected`, each of
    them bound, with the service healthy and reporting the same devices."""
    deadline, samples = time.monotonic() + timeout, []
    while time.monotonic() < deadline:
        devices, status = await rendered_devices(r), await service_status(r)
        samples.append({"devices": devices, "status": status})
        if (devices == expected and sorted(status["devices"] or []) == expected
                and status["admission_ready"] and status["backend"]["bindings"] == len(expected)):
            return status
        await asyncio.sleep(0.5)
    r.record("service-devices-timeout", samples)
    pytest.fail(f"flowtable devices did not follow {expected}: {samples[-3:]}")


async def operstate(r, port, wanted, timeout):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if (await read(r.target, r.session, f"/sys/class/net/{port}/operstate")).strip() == wanted:
            return True
        await asyncio.sleep(0.5)
    return False


async def set_link(r, port, state):
    await command(r.target, r.session, "ip", "link", "set", "dev", port, state)
