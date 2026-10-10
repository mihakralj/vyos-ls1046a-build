"""Take a bridge port, or one VLAN of it, out of forwarding and back without
repairing admission: revoked membership, a blocked port and a blocked VLAN."""
from __future__ import annotations

from _flowtable_service_bridge import ADDRESS, GUEST_VID, NETNS, TRUST_VID

from _flowtable_service_bridge import (MARKER, blockable, blocked_window, bridge_paths, crossed_nothing, guest_rows, membership, offloaded, pinned, port_state, topology, vlan_states)

import asyncio
import time

import pytest

from ask_orch.uart import Console
from _topology import LAN_NIC, TARGET_LAN_IF, TARGET_WAN_IF
from _flowtable_connections_peer import payload
from _flowtable_connections import (peer)
from _flowtable_rig import (artifact_dir, DPORT, SPORT, TABLE, WAN_IP, command, console_command, read)
from _flowtable_selective_neighbour import (hardware, warm)
from _flowtable_service import FIRST, service_status, supervision_status, wait_service
from _flowtable_service_vlan import (attempts, balanced, denied, received)


async def test_membership(bridge_service):
    r = bridge_service
    guest = {"lan": ADDRESS, "netns": NETNS}
    flows = [
        {"id": 0, "proto": "udp", "sport": FIRST, "lan": r.lan_ip},
        {"id": 1, "proto": "tcp", "sport": FIRST, "lan": r.lan_ip},
        {"id": 2, "proto": "udp", "sport": FIRST, **guest},
        {"id": 3, "proto": "tcp", "sport": FIRST, **guest},
        {"id": 4, "proto": "tcp", "sport": FIRST + 1, **guest},
        {"id": 5, "proto": "udp", "sport": FIRST + 2, "lan": r.lan_ip},
        {"id": 6, "proto": "udp", "sport": FIRST + 2, **guest},
    ]
    identity, service = await topology(r), await supervision_status(r)
    original_membership = await membership(r)
    assert TRUST_VID in original_membership and GUEST_VID in original_membership, original_membership
    # A reply on the existing guest UDP tuple must never reach its open
    # socket while membership is absent. The impossible serial fails the
    # receiver's payload checks if even one reverse-direction probe leaks.
    probe = payload(2, (1 << 63) - 1, 256)
    async with peer(r, flows, initial_ids=[0, 1, 2, 3, 5, 6], lease=300) as p:
        await warm(r, p, [0, 1, 2, 3], "service-bridge-baseline", flows[:4])
        initial = await hardware(r, p, "service-bridge-baseline-hardware", flows[:4])
        bridge_paths(initial, r)
        for ident in (5, 6):
            await denied(r, p, ident)
        for cycle in range(2):
            label = f"service-bridge-cycle-{cycle}"
            before, before_attempts = await r.state(), await attempts(r)
            await p.rpc("start", [2], count=0, interval=0.05, allow_loss=True)
            await p.rpc("start", [3], count=0, interval=0.05)
            samples, probes = [], 0
            try:
                await console_command(r.service_console, "bridge", "vlan", "del", "dev", TARGET_LAN_IF, "vid", str(GUEST_VID))
                removed_at = time.monotonic()
                await asyncio.sleep(0.2)  # in-flight traffic may finish
                absent_received = received(r, 2)
                retired = await r.wait(lambda s: not guest_rows(s), timeout=5)
                r.record(label + "-retired", retired)
                balanced(retired, initial["errors"])
                assert received(r, 2) == absent_received, "guest uplink survived membership removal"
                while time.monotonic() - removed_at < 6:
                    r.echo.transport.sendto(probe, (ADDRESS, FIRST))
                    probes += 1
                    await p.batch([0, 1], count=32, interval=0.01)
                    peer_state = await p.rpc("status")
                    assert not peer_state["errors"], ("guest downlink escaped membership filtering", peer_state)
                    state = await r.state()
                    samples.append({"seconds": time.monotonic() - removed_at, "state": state})
                    balanced(state, initial["errors"])
                    assert not guest_rows(state) and received(r, 2) == absent_received, state
                assert probes > 0
                held_membership = await membership(r)
                assert held_membership == {k: v for k, v in original_membership.items() if k != GUEST_VID}, held_membership
                assert await topology(r) == identity
                await denied(r, p, 5)
                r.record(label + "-absent", {"samples": samples, "membership": held_membership,
                    "reverse_probes": probes, "status": await service_status(r)})
            finally:
                # Inspect first so a lost deletion acknowledgement cannot
                # bypass restoration. Restore membership only, never policy.
                if GUEST_VID not in await membership(r):
                    await console_command(r.service_console, "bridge", "vlan", "add", "dev", TARGET_LAN_IF, "vid", str(GUEST_VID))
            restored_at = time.monotonic()
            reports = await p.rpc("stop", [2, 3])
            r.record(label + "-transfers", reports)
            assert reports["2"]["lost"] > 0 and reports["3"]["lost"] == 0, reports
            assert reports["3"]["count"] > 0, reports
            status = await wait_service(r, timeout=20, policy_hash=r.service_hash)
            await warm(r, p, [0, 1, 2, 3], label + "-readmitted", flows[:4])
            ready_seconds = time.monotonic() - restored_at
            assert ready_seconds < 20, (ready_seconds, status)
            # Converged: what it took to reinstall, counted before the
            # hardware proof, whose burst then covers a complete health-check
            # period in which the controller must not reinstall a healthy,
            # unchanged policy.
            after_attempts = await attempts(r)
            assert 1 <= after_attempts - before_attempts <= 4, (before_attempts, after_attempts)
            after = await hardware(r, p, label + "-hardware", flows[:4])
            hardware_seconds = time.monotonic() - restored_at
            assert hardware_seconds < 40, hardware_seconds
            balanced(after, initial["errors"])
            bridge_paths(after, r)
            assert after["rearms"] > before["rearms"], (before, after)
            assert all(after[k] == initial[k] for k in ("vlan_records", "vlan_slots")), (initial, after)
            assert await attempts(r) == after_attempts
            for ident in (5, 6):
                await denied(r, p, ident)
            assert await membership(r) == original_membership
            assert await topology(r) == identity
            assert await supervision_status(r) == service
            assert (await read(r.target, r.session, "/proc/sys/kernel/random/boot_id")).strip() == r.service_boot
            r.record(label + "-recovery", {"before": before, "after": after,
                "ready_seconds": ready_seconds, "hardware_seconds": hardware_seconds,
                "install_attempts": after_attempts - before_attempts, "transfers": reports})
        await p.rpc("open", [4])
        await warm(r, p, [0, 1, 2, 3, 4], "service-bridge-new-connection", flows[:5])
        final = await hardware(r, p, "service-bridge-new-hardware", flows[:5])
        bridge_paths(final, r)
        for ident in (5, 6):
            await denied(r, p, ident)


async def test_stp(bridge_service):
    """A port that stops forwarding takes every flow bridged through it out of
    hardware at once, since the classifier has no idea of a port state and
    would go on bridging through a blocked port. Nothing is cached while it
    stays blocked, in either direction, and traffic alone readmits every
    connection once it forwards again."""
    r = bridge_service
    guest = {"lan": ADDRESS, "netns": NETNS}
    flows = [
        {"id": 0, "proto": "udp", "sport": FIRST, "lan": r.lan_ip},
        {"id": 1, "proto": "tcp", "sport": FIRST, "lan": r.lan_ip},
        {"id": 2, "proto": "udp", "sport": FIRST, **guest},
        {"id": 3, "proto": "tcp", "sport": FIRST, **guest},
    ]
    streams = {0: r.lan_ip, 2: ADDRESS}
    service = await supervision_status(r)
    async with pinned(r, (TRUST_VID, GUEST_VID)), blockable(r, r.service_console), \
            peer(r, flows, initial_ids=[0, 1, 2, 3], lease=300) as p:
        await warm(r, p, [0, 1, 2, 3], "service-bridge-stp-baseline", flows)
        initial = await hardware(r, p, "service-bridge-stp-baseline-hardware", flows)
        bridge_paths(initial, r)
        await p.rpc("wire_probe", changes={"action": "start", "iface": LAN_NIC, "marker": MARKER[:32].hex()})
        await p.rpc("start", list(streams), count=0, interval=0.05, allow_loss=True)
        try:
            await command(r.target, r.session, "bridge", "link", "set", "dev", TARGET_LAN_IF, "state", "4")
            # It has to hold: with STP off the kernel would already have put
            # the port back to forwarding.
            assert await port_state(r) == "blocking"
            retired = await r.wait(lambda s: not s["flows"], timeout=5)
            r.record("service-bridge-stp-retired", retired)
            balanced(retired, initial["errors"])
            # One retirement per connection: both directions share a handle.
            assert retired["stp_invalidations"] - initial["stp_invalidations"] == len(flows), (initial, retired)

            def nothing_admitted(state):
                balanced(state, initial["errors"])
                assert not state["flows"], state
            await blocked_window(r, streams, FIRST, "service-bridge-stp-blocked", check=nothing_admitted)
            assert await port_state(r) == "blocking"
        finally:
            if await port_state(r) != "forwarding":
                await command(r.target, r.session, "bridge", "link", "set", "dev", TARGET_LAN_IF, "state", "3")
        restored_at = time.monotonic()
        await crossed_nothing(r, p, streams, "service-bridge-stp-crossed")
        await warm(r, p, [0, 1, 2, 3], "service-bridge-stp-readmitted", flows)
        assert time.monotonic() - restored_at < 20
        after = await hardware(r, p, "service-bridge-stp-hardware", flows)
        balanced(after, initial["errors"])
        bridge_paths(after, r)
        assert after["stp_invalidations"] == retired["stp_invalidations"], (retired, after)
        assert all(after[k] == initial[k] for k in ("vlan_records", "vlan_slots")), (initial, after)
        assert await supervision_status(r) == service
        r.record("service-bridge-stp-recovery", {"initial": initial, "retired": retired, "after": after})


async def test_vlan_state(bridge_service):
    """One VLAN of a port can stop forwarding while the port and its other
    VLAN go on: a per-VLAN STP state, which the kernel accepts with spanning
    tree off and keeps, unlike a port state. It reaches hardware as an event of
    its own, which nothing raised before. The blocked VLAN's connections leave
    hardware and are cached nowhere, in either direction; the other VLAN keeps
    forwarding through the same port and returns to hardware; and traffic alone
    readmits the blocked VLAN's connections once it forwards again."""
    r = bridge_service
    guest = {"lan": ADDRESS, "netns": NETNS}
    flows = [
        {"id": 0, "proto": "udp", "sport": FIRST, "lan": r.lan_ip},
        {"id": 1, "proto": "tcp", "sport": FIRST, "lan": r.lan_ip},
        {"id": 2, "proto": "udp", "sport": FIRST, **guest},
        {"id": 3, "proto": "tcp", "sport": FIRST, **guest},
    ]
    streams = {2: ADDRESS}
    service = await supervision_status(r)
    assert (await vlan_states(r))[GUEST_VID] == "forwarding"
    async with pinned(r, (TRUST_VID, GUEST_VID)), peer(r, flows, initial_ids=[0, 1, 2, 3], lease=300) as p:
        await warm(r, p, [0, 1, 2, 3], "service-bridge-vlan-state-baseline", flows)
        initial = await hardware(r, p, "service-bridge-vlan-state-baseline-hardware", flows)
        await p.rpc("wire_probe", changes={"action": "start", "iface": LAN_NIC, "marker": MARKER[:32].hex()})
        await p.rpc("start", list(streams), count=0, interval=0.05, allow_loss=True)
        try:
            await command(r.target, r.session, "bridge", "vlan", "set", "dev", TARGET_LAN_IF,
                          "vid", str(GUEST_VID), "state", "blocking")
            states = await vlan_states(r)
            assert states[GUEST_VID] == "blocking" and states[TRUST_VID] == "forwarding", states
            assert await port_state(r) == "forwarding"
            retired = await r.wait(lambda s: not guest_rows(s), timeout=5)
            r.record("service-bridge-vlan-state-retired", retired)
            balanced(retired, initial["errors"])
            # The whole port's connections: rules are not mapped onto VLANs.
            assert retired["stp_invalidations"] - initial["stp_invalidations"] == len(flows), (initial, retired)

            def guest_absent(state):
                balanced(state, initial["errors"])
                assert not guest_rows(state), state
            await blocked_window(r, streams, FIRST, "service-bridge-vlan-state-blocked", check=guest_absent)
            # The trusted VLAN forwards through the same port and returns to hardware
            # while the guest VLAN is still blocked.
            await p.batch([0, 1], count=64, interval=0.01)
            trusted = await r.wait(lambda s: len(s["flows"]) == 4 and not guest_rows(s), timeout=10)
            r.record("service-bridge-vlan-state-trusted", trusted)
            assert (await vlan_states(r))[GUEST_VID] == "blocking"
        finally:
            if (await vlan_states(r)).get(GUEST_VID) != "forwarding":
                await command(r.target, r.session, "bridge", "vlan", "set", "dev", TARGET_LAN_IF,
                              "vid", str(GUEST_VID), "state", "forwarding")
        restored_at = time.monotonic()
        await crossed_nothing(r, p, streams, "service-bridge-vlan-state-crossed")
        await warm(r, p, [0, 1, 2, 3], "service-bridge-vlan-state-readmitted", flows)
        assert time.monotonic() - restored_at < 20
        after = await hardware(r, p, "service-bridge-vlan-state-hardware", flows)
        balanced(after, initial["errors"])
        bridge_paths(after, r)
        assert all(after[k] == initial[k] for k in ("vlan_records", "vlan_slots")), (initial, after)
        assert await supervision_status(r) == service
        r.record("service-bridge-vlan-state-recovery", {"initial": initial, "retired": retired, "after": after})


@pytest.mark.parametrize("offload", [False, True], ids=["software", "hardware"])
async def test_flowtable_bridge_stp_fastpath(bridge_software, offload):
    """The same block against a flowtable that offers a flow on any
    established packet, in either direction, as fw4's does. That is what lets
    a reply from the WAN cache a flow through the blocked port, and the
    flowtable's hook on the port runs before br_handle_frame(), so such a flow
    would carry the port's received frames past the block. Without hardware
    offload the bridge hop is also a DIRECT transmit, which goes out of the
    port past br_forward(); and nothing but the adapter's sweep takes away a
    software flow cached before the block. With it, the flow is in hardware
    and the adapter's own retirement takes it."""
    r = bridge_software
    flows = [{"id": 0, "proto": "udp", "sport": SPORT, "lan": r.lan_ip}]
    streams = {0: r.lan_ip}
    label = "bridge-stp-" + ("hardware" if offload else "software")
    await r.nft(f'''table inet {TABLE} {{
 flowtable fast {{ hook ingress priority 0; devices = {{ {TARGET_LAN_IF}, {TARGET_WAN_IF} }}; {"flags offload;" if offload else ""} }}
 chain forward {{ type filter hook forward priority 0; policy accept;
 meta l4proto udp ct original ip saddr {r.lan_ip} ct original ip daddr {WAN_IP} ct original proto-src {SPORT} ct original proto-dst {DPORT} ct state established flow add @fast
 }}
}}''')
    bound = await r.wait(lambda s: s["bindings"] == (2 if offload else 0))
    with Console.target(log_path=str(artifact_dir() / "bridge-stp-fastpath-uart.log")) as con:
        await asyncio.to_thread(con.login, "root", None)
        async with pinned(r, (TRUST_VID,)), blockable(r, con), peer(r, flows, lease=180) as p:
            await p.batch([0], count=64, interval=0.01)
            assert await offloaded(r, r.lan_ip, SPORT), "the flowtable never took the connection"
            if offload:
                admitted = await r.wait(lambda s: s["entries"] == 2)
                assert admitted["installs"] > bound["installs"], (bound, admitted)
            await p.rpc("wire_probe", changes={"action": "start", "iface": LAN_NIC, "marker": MARKER[:32].hex()})
            await p.rpc("start", list(streams), count=0, interval=0.05, allow_loss=True)
            try:
                await command(r.target, r.session, "bridge", "link", "set", "dev", TARGET_LAN_IF, "state", "4")
                assert await port_state(r) == "blocking"

                def nothing_admitted(state):
                    assert not state["flows"], state
                await blocked_window(r, streams, SPORT, label + "-blocked", check=nothing_admitted)
                assert await port_state(r) == "blocking"
            finally:
                if await port_state(r) != "forwarding":
                    await command(r.target, r.session, "bridge", "link", "set", "dev", TARGET_LAN_IF, "state", "3")
            await crossed_nothing(r, p, streams, label + "-crossed")
            # Traffic alone caches it again.
            await p.batch([0], count=64, interval=0.01)
            assert await offloaded(r, r.lan_ip, SPORT), "the connection was not cached again"
            if offload:
                await r.wait(lambda s: s["entries"] == 2)
