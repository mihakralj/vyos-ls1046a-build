"""Recovery through the shipping daemon, without harness-issued repairs.

The base rig supplies endpoints and routing. This fixture then starts the real
boot service against its actual configuration file. Only setup/teardown and the
explicit maintenance test issue control commands; fault recovery must come from
the running daemon. The nft wrapper injects failed or hung operations and
otherwise execs the real nft with the inherited transaction lease intact.
"""
from __future__ import annotations

from _flowtable_service import (ADMISSION_GUARDS, CONF, DAEMON, FAULT_DIR, FIRST, FLOWS, INIT, blocked_probe, chain_rules, devices_follow, dpaa_ports, managed_service, operstate, service_status, set_link, supervision_status, wait_replacement, wait_service)

import asyncio
import json
import time

import pytest

from _topology import TARGET_LAN_IF, TARGET_WAN_IF
from _flowtable_connections import (by_key, peer)
from _flowtable_rig import (DPORT, WAN_IP, command, console_command, console_python, flowtable_json, read)
from _flowtable_selective_neighbour import (hardware, keys, warm)


@pytest.mark.parametrize("fault", ["missing-table", "invalidated-backend", "failed-apply",
                                 "hung-apply", "lost-reply"])
async def test_automatic_recovery(service, fault):
    r = service
    flows = [{**f, "lan": r.lan_ip} for f in FLOWS]
    async with peer(r, flows, initial_ids=[0, 1, 3]) as p:
        await warm(r, p, [0, 1], f"service-{fault}-warm", flows[:2])
        initial = await hardware(r, p, f"service-{fault}-before", flows[:2])
        await blocked_probe(r, p)
        started = time.monotonic()
        if fault == "invalidated-backend":
            # Rule events invalidate the backend but do not alter policy or
            # produce the LINK/ADDR notifications subscribed by the daemon.
            priority = "32007"
            rules = json.loads((await command(r.target, r.session, "ip", "-j", "rule", "show"))["stdout"])
            assert not any(rule.get("priority") == int(priority) for rule in rules), rules
            await command(r.target, r.session, "ip", "rule", "add", "pref", priority,
                          "from", "198.18.254.0/24", "table", "main")
            try:
                invalid = await r.wait(lambda s: s["invalidated"] == 1 or s["rearms"] > initial["rearms"], timeout=3)
                assert not invalid["fatal"], invalid
                r.record("service-rule-invalidation", invalid)
            finally:
                await command(r.target, r.session, "ip", "rule", "del", "pref", priority)
        else:
            if fault in ("failed-apply", "hung-apply", "lost-reply"):
                mode = "once" if fault == "failed-apply" else fault
                result = await r.target.fs_write(r.session, FAULT_DIR + "/armed", mode + "\n")
                assert result["errno"] == 0, result
            await console_command(r.service_console, "nft", "delete", "table", "inet", "ask_flowtable")
        # These are still the original sockets. The test issues no apply,
        # resume, restart, table recreation or reboot after fault injection.
        software = await p.batch([0, 1], count=32, interval=0.01)
        await blocked_probe(r, p)
        recovery_limit = 20 if fault in ("hung-apply", "lost-reply") else 12
        samples = await wait_service(r, timeout=recovery_limit)
        elapsed = time.monotonic() - started
        assert elapsed < recovery_limit, (elapsed, samples)
        if fault in ("failed-apply", "hung-apply", "lost-reply"):
            mode = "once" if fault == "failed-apply" else fault
            assert (await read(r.target, r.session, FAULT_DIR + "/consumed")).strip() == mode
            attempts = (await read(r.target, r.session, FAULT_DIR + "/attempts")).splitlines()
            # A committed transaction with a lost reply needs inspection,
            # while an uncommitted failure needs another install.
            assert len(attempts) == (2 if fault == "lost-reply" else 3), attempts
        if fault in ("hung-apply", "lost-reply"):
            pids = json.loads(await read(r.target, r.session, FAULT_DIR + "/pids"))
            await console_python(r.service_console, f"""
from pathlib import Path
for pid in {list(pids.values())!r}:
    assert not Path('/proc/' + str(pid)).exists(), pid
""")
            r.record(f"service-{fault}-reaped", pids)
        if fault == "lost-reply":
            await console_command(r.service_console, "test", "-f", FAULT_DIR + "/committed")
        await warm(r, p, [0, 1], f"service-{fault}-readmitted", flows[:2])
        recovered = await hardware(r, p, f"service-{fault}-after", flows[:2])
        hardware_seconds = time.monotonic() - started
        assert hardware_seconds < 35, hardware_seconds
        if fault == "invalidated-backend":
            assert recovered["rearms"] > initial["rearms"], (initial, recovered)
        assert recovered["errors"] == initial["errors"], (initial, recovered)
        await p.rpc("open", [2])
        await warm(r, p, [0, 1, 2], f"service-{fault}-new-flow", flows[:3])
        await hardware(r, p, f"service-{fault}-new-hardware", flows[:3])
        await blocked_probe(r, p)
        assert (await service_status(r))["policy_hash"] == r.service_hash
        assert (await read(r.target, r.session, "/run/ask-flowtable/worker.pid")).strip() == r.service_pid
        assert (await read(r.target, r.session, "/proc/sys/kernel/random/boot_id")).strip() == r.service_boot
        r.record(f"service-{fault}-recovery", {"controller_seconds": elapsed,
                 "hardware_proof_seconds": hardware_seconds, "samples": samples,
                 "transition_transfers": software, "pid": r.service_pid, "boot": r.service_boot})


async def test_maintenance_stop(service):
    r = service
    flows = [{**f, "lan": r.lan_ip} for f in FLOWS]
    async with peer(r, flows, initial_ids=[0, 1, 3]) as p:
        await warm(r, p, [0, 1], "service-stop-warm", flows[:2])
        await console_command(r.service_console, DAEMON, "stop", timeout=45)
        # A crash while paused must replace the worker without rearming.
        await console_command(r.service_console, "kill", "-KILL", r.service_pid)
        replacement = await wait_replacement(r, r.service_pid)
        assert replacement["supervisor_pid"] == int(r.supervisor_pid), replacement
        # Longer than a health interval, then a real service process restart.
        await asyncio.sleep(6)
        assert not (await service_status(r))["admission_ready"]
        await console_command(r.service_console, INIT, "restart", timeout=45)
        await asyncio.sleep(6)
        restarted_pid = (await read(r.target, r.session, "/run/ask-flowtable/worker.pid")).strip()
        assert int(restarted_pid) != replacement["worker_pid"], (replacement, restarted_pid)
        restarted = await supervision_status(r)
        assert restarted["supervisor_pid"] != replacement["supervisor_pid"], (replacement, restarted)
        status = await service_status(r)
        assert status["reconciliation_paused"] and not status["policy_installed"], status
        await p.batch([0, 1], count=64, interval=0.01)
        await blocked_probe(r, p)
        await console_command(r.service_console, DAEMON, "resume")
        await wait_service(r)
        await warm(r, p, [0, 1], "service-stop-readmitted", flows[:2])
        await hardware(r, p, "service-stop-hardware", flows[:2])
        assert not (await service_status(r))["reconciliation_paused"]
        r.record("service-maintenance-restart", {"before_pid": r.service_pid,
                 "crash_replacement": replacement, "after_pid": restarted_pid,
                 "restarted": restarted, "paused_status": status})


@pytest.mark.parametrize("point,attempt_count", [("drain", 2), ("install", 3), ("commit", 2)])
async def test_controller_crash(service, point, attempt_count):
    r = service
    flows = [{**f, "lan": r.lan_ip} for f in FLOWS]
    async with peer(r, flows, initial_ids=[0, 1, 3]) as p:
        await warm(r, p, [0, 1], f"crash-{point}-warm", flows[:2])
        initial = await hardware(r, p, f"crash-{point}-before", flows[:2])
        await blocked_probe(r, p)
        result = await r.target.fs_write(r.session, FAULT_DIR + "/crash", point)
        assert result["errno"] == 0, result
        # Publish a valid changed policy to initiate a real replacement. The
        # extra exclusion is outside the traffic's scope, preserving sockets.
        candidate = (await read(r.target, r.session, CONF)) + "exclude tcp 9\n"
        candidate_path = CONF + ".recovery-test"
        assert (await r.target.fs_read(r.session, candidate_path))["errno"] != 0
        try:
            result = await r.target.fs_write(r.session, candidate_path, candidate)
            assert result["errno"] == 0, result
            started = time.monotonic()
            # Same-directory rename avoids injecting a second, unintended
            # fault where the daemon could read a truncated configuration.
            await console_command(r.service_console, "mv", candidate_path, CONF)
        finally:
            await console_command(r.service_console, "rm", "-f", candidate_path)
        expected_hash = (await flowtable_json(r.service_console, "check"))["policy_hash"]
        assert expected_hash != r.service_hash
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            hit_file = await r.target.fs_read(r.session, FAULT_DIR + "/crash-hit")
            if hit_file["errno"] == 0:
                break
            await asyncio.sleep(0.1)
        else:
            pytest.fail(f"crash injection never reached {point}")
        hit = json.loads(await read(r.target, r.session, FAULT_DIR + "/crash-hit"))
        assert hit["point"] == point and hit["controller"] == int(r.service_pid), hit
        # Observation and traffic only after the injected crash: no repair
        # command, signal to the supervisor, policy rewrite or reboot.
        software = await p.batch([0, 1], count=32, interval=0.01)
        await blocked_probe(r, p)
        replacement = await wait_replacement(r, r.service_pid)
        assert replacement["supervisor_pid"] == int(r.supervisor_pid), replacement
        assert replacement["restarts"] == 1, replacement
        samples = await wait_service(r, timeout=20, policy_hash=expected_hash)
        elapsed = time.monotonic() - started
        assert elapsed < 20, (elapsed, samples)
        await console_python(r.service_console, f"""
from pathlib import Path
for pid in {[hit[k] for k in ('controller', 'guardian', 'wrapper', 'real_nft') if hit[k]]!r}:
    assert not Path('/proc/' + str(pid)).exists(), pid
""")
        await warm(r, p, [0, 1], f"crash-{point}-readmitted", flows[:2])
        recovered = await hardware(r, p, f"crash-{point}-after", flows[:2])
        hardware_seconds = time.monotonic() - started
        assert hardware_seconds < 35, hardware_seconds
        assert recovered["errors"] == initial["errors"], (initial, recovered)
        await p.rpc("open", [2])
        await warm(r, p, [0, 1, 2], f"crash-{point}-new-flow", flows[:3])
        await hardware(r, p, f"crash-{point}-new-hardware", flows[:3])
        await blocked_probe(r, p)
        attempts = (await read(r.target, r.session, FAULT_DIR + "/attempts")).splitlines()
        assert len(attempts) == attempt_count, attempts
        assert (await read(r.target, r.session, "/proc/sys/kernel/random/boot_id")).strip() == r.service_boot
        assert not (await service_status(r))["reconciliation_paused"]
        r.record(f"service-crash-{point}-recovery", {"controller_seconds": elapsed,
                 "hardware_proof_seconds": hardware_seconds, "hit": hit, "replacement": replacement,
                 "samples": samples, "transition_transfers": software, "boot": r.service_boot})


async def test_intentional_stop(service):
    r = service
    flows = [{**f, "lan": r.lan_ip} for f in FLOWS]
    async with peer(r, flows, initial_ids=[0, 1, 3]) as p:
        await warm(r, p, [0, 1], "service-stop-supervisor-warm", flows[:2])
        await console_command(r.service_console, INIT, "stop", timeout=45)
        await asyncio.sleep(6)
        assert not (await supervision_status(r))["running"]
        for path in ("/run/ask-flowtable/worker.pid", "/run/ask-flowtable/supervisor.pid"):
            assert (await r.target.fs_read(r.session, path))["errno"] != 0, path
        status = await service_status(r)
        assert status["reconciliation_paused"] and not status["policy_installed"], status
        await p.batch([0, 1], count=64, interval=0.01)
        await blocked_probe(r, p)
        await console_command(r.service_console, INIT, "start")
        await asyncio.sleep(6)
        assert (await supervision_status(r))["running"]
        assert (await service_status(r))["reconciliation_paused"]
        assert not (await service_status(r))["policy_installed"]
        await console_command(r.service_console, DAEMON, "resume")
        await wait_service(r)
        await warm(r, p, [0, 1], "service-stop-supervisor-readmitted", flows[:2])
        await hardware(r, p, "service-stop-supervisor-hardware", flows[:2])
        r.record("service-intentional-stop", {"paused_status": status, "supervision": await supervision_status(r)})


# What render.c puts ahead of any policy selector, in its order: the conditions
# under which no configuration may offload a flow. The mark guard follows,
# derived from the adapter's mask. nft lists the protocol set by number.


async def test_rendered_admission(service):
    """The installed table is the one render.c describes. Its admission chain
    hooks forward at priority 10, behind every firewall chain at the standard
    filter priority, and its guards come before any policy selector.

    Priority is read from nft's JSON, which states it as a number; the text
    form names it relative to `filter`. Rules are read from the text form."""
    r = service
    objects = json.loads((await command(r.target, r.session, "nft", "-j", "list", "table", "inet",
                                        "ask_flowtable"))["stdout"])["nftables"]
    listing = (await command(r.target, r.session, "nft", "list", "table", "inet", "ask_flowtable"))["stdout"]
    r.record("service-rendered-admission", {"json": objects, "text": listing})
    table = next(item["table"] for item in objects if "table" in item)
    assert table.get("comment") == "ask-flowtable/v1:" + r.service_hash, table
    flowtables = [item["flowtable"] for item in objects if "flowtable" in item]
    assert [(f["name"], f["hook"], f["prio"]) for f in flowtables] == [("fast", "ingress", 0)], flowtables
    devices = flowtables[0]["dev"]
    assert sorted([devices] if isinstance(devices, str) else devices) == sorted([TARGET_LAN_IF, TARGET_WAN_IF])
    assert "flags offload" in listing, listing
    chains = [item["chain"] for item in objects if "chain" in item]
    assert [(c["name"], c.get("type"), c.get("hook"), c.get("prio"), c.get("policy")) for c in chains] == [
        ("admit", "filter", "forward", 10, "accept")], chains
    refused = ~(await r.state())["qos_mark_mask"] & 0xffffffff
    rules = chain_rules(listing, "admit")
    guards = [*ADMISSION_GUARDS, f"ct mark & {refused:#x} != 0x0 return"]
    assert rules[:len(guards)] == guards, rules
    # The fixture's policy is one scope line and no exclusion.
    policy = rules[len(guards):]
    assert len(policy) == 1 and policy[0].endswith(" flow add @fast"), rules
    assert f"ct original proto-src {FIRST}-{FIRST + 2}" in policy[0], policy
    assert f"ct original proto-dst {DPORT}" in policy[0], policy


async def test_foreign_table_beside(service):
    """Another offload flowtable binds beside the service's live one -- a
    consumer's own, or its check-mode probe -- which the adapter allows. The
    service counts more bindings than its table has devices and keeps its
    table and its flows through two health checks, rather than deleting the
    table into a drain the other one holds up; it carries on once the other
    table is gone."""
    r = service
    flows = [{**f, "lan": r.lan_ip} for f in FLOWS[:2]]
    status = await service_status(r)
    async with peer(r, flows, initial_ids=[0, 1]) as p:
        await warm(r, p, [0, 1], "service-beside-baseline", flows)
        before = await hardware(r, p, "service-beside-before", flows)
        ports = f"devices = {{ {TARGET_LAN_IF}, {TARGET_WAN_IF} }}; flags offload;"
        await command(r.target, r.session, "nft",
                      f"table inet ask_beside {{ flowtable beside {{ hook ingress priority 0; {ports} }}; }}")
        try:
            await r.wait(lambda s: s["bindings"] == 4)
            await asyncio.sleep(11)  # two health checks
            during = await hardware(r, p, "service-beside-during", flows, bindings=4)
            assert (during["installs"], during["deletes"]) == (before["installs"], before["deletes"]), (
                before, during)
            assert (await service_status(r))["policy_hash"] == status["policy_hash"]
        finally:
            await command(r.target, r.session, "nft", "delete", "table", "inet", "ask_beside", check=False)
        after = await r.wait(lambda s: s["bindings"] == 2)
        await asyncio.sleep(6)
        await hardware(r, p, "service-beside-after", flows)
        assert (await service_status(r))["admission_ready"]
        r.record("service-beside", {"before": before, "during": during, "after": after})


async def test_firewall_revocation(service):
    """Revoke a cached flow across a service stop, the firewall-maintenance
    procedure policy.md prescribes: stop acceleration, change the firewall,
    then reload, which resumes the daemon and starts it.

    The stop drains every hardware flow. Resume clears the pause and nothing
    else; the running daemon reinstalls its unchanged policy on its next
    check. What must not return is the flow the new firewall forbids: it is
    dropped before the admission chain at priority 10 sees it, while a flow
    the firewall still permits is readmitted over the same connection."""
    r = service
    flows = [{**f, "lan": r.lan_ip} for f in FLOWS]
    deny = ["FORWARD", "-s", r.lan_ip, "-d", WAN_IP, "-p", "udp", "--sport", str(FIRST),
            "--dport", str(DPORT), "-j", "DROP"]
    async with peer(r, flows, initial_ids=[0, 1, 3]) as p:
        await warm(r, p, [0, 1], "revocation-warm", flows[:2])
        initial = await hardware(r, p, "revocation-before", flows[:2])
        await console_command(r.service_console, INIT, "stop", timeout=45)
        drained = await r.state()
        assert drained["entries"] == drained["bindings"] == drained["handle_refs"] == 0, drained
        assert drained["installs"] == initial["installs"], (initial, drained)
        assert drained["deletes"] == initial["deletes"] + 4, (initial, drained)
        await command(r.target, r.session, "iptables", "-I", *deny)
        try:
            await console_command(r.service_console, INIT, "reload", timeout=45)
            samples = await wait_service(r, policy_hash=r.service_hash)
            assert not samples[-1]["status"]["reconciliation_paused"], samples
            readmitted = await warm(r, p, [1], "revocation-permitted", flows[1:2])
            probe = await blocked_probe(r, p, 0)
            state = await r.state()
            r.record("revocation-firewall", {"drained": drained, "readmitted": readmitted,
                                             "probe": probe, "state": state})
            assert by_key(state).keys() == keys([0], flows[1:2]), state
            assert state["installs"] - state["deletes"] == state["entries"] == 2, state
            assert state["errors"] == initial["errors"], (initial, state)
        finally:
            await command(r.target, r.session, "iptables", "-D", *deny)
        # The same conntrack is admissible again once the firewall allows it,
        # so its absence above was the firewall's doing.
        await warm(r, p, [0, 1], "revocation-restored", flows[:2])
        await hardware(r, p, "revocation-restored-hardware", flows[:2])


async def test_devices_follow_ports(rig):
    """`devices auto` is resolved on every reconciliation, and a change in
    which ports are up is made to the live flowtable: a spare port gaining its
    link is added to it and losing the link deletes it again, with no restart,
    reload or policy edit in between. The policy is the configuration, not the
    ports it found up, so its hash never moves. Flows on the test ports stay
    in hardware throughout: the same entries, counting through both changes,
    with nothing installed or deleted, and their traffic loses nothing.

    This image configures only the LAN and WAN ports, so the spare fsl_dpa
    ports are administratively down; one with a cable is toggled. The flows
    are proven against a table of the two test ports alone, so a spare that
    is already up is taken down first and toggled instead. A port carrying an
    address is never touched."""
    r = rig
    flows = [{**f, "lan": r.lan_ip} for f in FLOWS[:2]]
    base = sorted([TARGET_LAN_IF, TARGET_WAN_IF])
    async with managed_service(r, devices=("auto",)):
        ports = await dpaa_ports(r)
        eligible = sorted(name for name, port in ports.items() if port["operstate"] == "up")
        assert set(base) <= set(eligible), ports
        baseline = await devices_follow(r, eligible)
        assert baseline["policy_hash"] == r.service_hash, baseline
        # `check` resolves no port at all, and names the same policy.
        assert (await flowtable_json(r.service_console, "check"))["policy_hash"] == r.service_hash
        links = json.loads((await command(r.target, r.session, "ip", "-j", "addr", "show"))["stdout"])
        addressed = {link["ifname"] for link in links
                     if any(a["family"] == "inet" or a.get("scope") == "global" for a in link.get("addr_info", []))}
        spare = [name for name in ports if name not in (*base, *addressed)]
        if set(eligible) - set(base) - set(spare):
            pytest.skip(f"a port carrying an address is up beside the test ports: {eligible}")
        port, restore = None, {}
        try:
            for name in sorted(set(eligible) - set(base)):
                await set_link(r, name, "down")
                restore[name] = "up"
                port = port or name
            for name in ([] if port else [name for name in spare if not ports[name]["up"]]):
                await set_link(r, name, "up")
                restore[name] = "down"
                # A 1G copper port negotiates for a few seconds.
                linked = await operstate(r, name, "up", timeout=8)
                await set_link(r, name, "down")
                if linked:
                    port = name
                    break
            if port is None:
                pytest.skip(f"no spare fsl_dpa port has a link to toggle: {ports}")
            await devices_follow(r, base)
            async with peer(r, flows) as p:
                await warm(r, p, [0, 1], "service-devices-warm", flows)
                samples = [await r.state()]
                forwarded = await r.software_forwarded()
                # The original sockets carry traffic through both changes.
                await p.rpc("start", [0, 1], count=0, interval=0.01)
                transitions = []
                for state, expected in (("up", sorted([*base, port])), ("down", base)):
                    await set_link(r, port, state)
                    if state == "up":
                        assert await operstate(r, port, "up", timeout=8), port
                    # Bounded well inside the LAN peer's 35 s wait for its
                    # next command, so a slow change fails here, not there.
                    status = await devices_follow(r, expected, timeout=15)
                    running = await p.rpc("status")
                    samples.append(await r.state())
                    transitions.append({"port": port, "link": state, "devices": expected,
                                        "status": status, "peer": running})
                    assert running["running"] == 2 and not running["errors"], transitions
                    assert status["policy_hash"] == r.service_hash, transitions
                transfers = await p.rpc("stop", [0, 1])
                slow_path = await r.software_forwarded() - forwarded
                r.record("service-devices-follow", {"ports": ports, "eligible": eligible, "samples": samples,
                                                    "transitions": transitions, "transfers": transfers,
                                                    "software_forwarded": slow_path})
                assert [s["bindings"] for s in samples] == [2, 3, 2], samples
                # Each sample against the one before it: the same hardware
                # entries, still counting, and nothing installed or deleted.
                for earlier, later in zip(samples, samples[1:]):
                    assert (later["installs"], later["deletes"]) == (earlier["installs"], earlier["deletes"]), samples
                    old, new = by_key(earlier), by_key(later)
                    for key in keys([0, 1], flows):
                        assert new[key]["cookie"] == old[key]["cookie"], (key, earlier, later)
                        assert int(new[key]["packets"]) > int(old[key]["packets"]), (key, earlier, later)
                for report in transfers.values():
                    assert report["count"] > 0 and report["received"] == report["count"], transfers
                assert 0 <= slow_path <= 64, slow_path
                after = await hardware(r, p, "service-devices-after", flows)
                assert ({key: row["cookie"] for key, row in by_key(after).items()}
                        == {key: row["cookie"] for key, row in by_key(samples[0]).items()}), (samples[0], after)
        finally:
            for name, state in restore.items():
                await set_link(r, name, state)
            await devices_follow(r, eligible)
