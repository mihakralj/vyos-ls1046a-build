"""Live XFRM policy changes must revoke both cached forwarding paths.

Policy restoration is the configuration owner's action. Existing TCP/UDP
sockets must then return to hardware without restarting the flowtable service.
"""
from __future__ import annotations

import asyncio
import json
import time

import pytest

from _flowtable_connections import (by_key, consistent, peer)
from _flowtable_failslab import (same_service)
from _flowtable_rig import (DPORT, command, console_command)
from _flowtable_rig import (console_python, read)
from _flowtable_selective_neighbour import (keys, warm)
from _flowtable_service import (FIRST, supervision_status)
from _flowtable_service_ipsec import (INNER, LAN_INNER, REQIDS, TARGET_LAN_IF, TARGET_WAN_IF, WAN_IP, Wire, balanced, flows_for, hardware, negative, plaintext_probe)
from _flowtable_service_vlan import (attempts)

MARK_TABLE = "ask_recovery_xfrm_mark"


def selector(direction):
    if direction == "out":
        return ["src", LAN_INNER + "/32", "dst", INNER + "/32", "dir", "out"]
    return ["src", INNER + "/32", "dst", LAN_INNER + "/32", "dev", TARGET_LAN_IF, "dir", "fwd"]


def required(r, direction="fwd", *, reqid=None, offload=False):
    src, dst = (r.ipsec.outer, WAN_IP) if direction == "out" else (WAN_IP, r.ipsec.outer)
    return ["priority", "1000", "action", "allow", "tmpl", "src", src, "dst", dst,
            "proto", "esp", "mode", "tunnel", "reqid", reqid or REQIDS["out" if direction == "out" else "in"],
            "level", "required", *(["offload", "packet", "dev", TARGET_WAN_IF]
                                   if offload or direction == "out" else [])]


async def update(r, direction, options):
    await console_command(r.service_console, "ip", "xfrm", "policy", "update",
                          *selector(direction), *options)


async def recover(r, p, flows, initial, service, installed, label):
    started = time.monotonic()
    deadline = started + 25
    while True:
        await p.batch([0, 1], count=32, interval=0.01)
        progress = await p.rpc("status")
        assert not progress["errors"], progress
        if all(progress["received"][str(i)] > installed["received"][str(i)] for i in (2, 3)):
            break
        r.record(label + "-recovering", {"state": await r.state(), "progress": progress,
                 "wan_tcp": p.wan_tcp_info(), "elapsed": time.monotonic() - started,
                 "stopped": installed})
        assert time.monotonic() < deadline, progress
    reports = await p.rpc("stop", [2, 3])
    assert reports["2"]["lost"] > 0 and reports["3"]["lost"] == 0, reports
    await warm(r, p, [0, 1, 2, 3], label + "-readmitted", flows[:4])
    ready = time.monotonic() - started
    assert ready < 25
    after = await hardware(r, p, label + "-hardware", flows[:4])
    elapsed = time.monotonic() - started
    assert elapsed < 45
    assert after["rearms"] == initial["rearms"]
    balanced(after, initial["errors"])
    await same_service(r, service)
    r.record(label + "-recovery", {"ready_seconds": ready, "hardware_seconds": elapsed,
                                 "before": initial, "after": after, "reports": reports})


@pytest.mark.parametrize("change", ["forward-block", "forward-template", "outbound-block",
                                    "marked-forward", "offloaded-forward-template"])
async def test_recovery(ipsec_service, change):
    r, flows = ipsec_service, flows_for(ipsec_service)
    direction = "out" if change == "outbound-block" else "fwd"
    marked = change == "marked-forward"
    offloaded = change == "offloaded-forward-template"
    # Ordinary and packet-offloaded FWD policies must enforce the same
    # templates, including when an arriving packet has no secpath at all.
    await update(r, "fwd", required(r, offload=offloaded))
    service, initial_attempts = await supervision_status(r), await attempts(r)
    async with peer(r, flows, initial_ids=[0, 1, 2, 3, 5, 6], lease=400, listen_addresses=[INNER]) as p:
        await warm(r, p, [0, 1, 2, 3], change + "-baseline", flows[:4])
        initial = await hardware(r, p, change + "-baseline-hardware", flows[:4])
        await plaintext_probe(r, p, change + "-plaintext-baseline")
        for cycle in range(2):
            label = f"policy-{change}-{cycle}"
            before = await r.state()
            await p.rpc("start", [2], count=0, interval=0.05, allow_loss=True)
            await p.rpc("start", [3], count=0, interval=0.05)
            started = time.monotonic()
            mark_policy, mark_table = False, False
            try:
                if marked:
                    await command(r.target, r.session, "ip", "xfrm", "policy", "add",
                                  *selector("fwd"), "mark", "0x44", "mask", "0xff", "priority", "1", "action", "block")
                    mark_policy = True
                    await command(r.target, r.session, "nft", "add", "table", "inet", MARK_TABLE)
                    mark_table = True
                    await command(r.target, r.session, "nft", "add", "chain", "inet", MARK_TABLE,
                                  "prerouting", "{ type filter hook prerouting priority -150; policy accept; }")
                    await command(r.target, r.session, "nft", "add", "rule", "inet", MARK_TABLE,
                                  "prerouting", "ip", "saddr", INNER, "ip", "daddr", LAN_INNER,
                                  "meta", "mark", "set", "0x44")
                elif "template" in change:
                    await update(r, direction, required(r, direction, reqid="49399", offload=offloaded))
                else:
                    await update(r, direction, ["priority", "1000", "action", "block"])
                retired = await r.wait(lambda s: consistent(s) and not (by_key(s).keys() & keys([2, 3], flows)),
                                       timeout=5)
                retire_seconds = time.monotonic() - started
                assert retire_seconds < 5
                assert retired["ipsec_policy_invalidations"] > before["ipsec_policy_invalidations"], (before, retired)
                # Let packets already in the path settle before proving the
                # revoked state stays closed, with the original sockets alive.
                await asyncio.sleep(0.2)
                stopped = await p.rpc("status")
                held = time.monotonic()
                async with Wire(r, label + "-blocked") as wire:
                    while time.monotonic() - held < 6:
                        await p.batch([0, 1], count=32, interval=0.01)
                        progress = await p.rpc("status")
                        assert not progress["errors"], progress
                        assert all(progress["received"][str(i)] == stopped["received"][str(i)] for i in (2, 3)), progress
                        state = await r.state()
                        assert not (by_key(state).keys() & keys([2, 3], flows)), state
                        if marked:
                            assert not state["entries"], "marked policy must exclude hardware admission globally"
                await plaintext_probe(r, p, label + "-plaintext-blocked")
                await negative(r, p)
                assert len(await r.ipsec.states()) == 2, "policy changes must not require SA recreation"
                r.record(label + "-blocked", {"state": retired, "seconds": retire_seconds, "wire": wire.check(), "progress": stopped, "wan_tcp": p.wan_tcp_info()})
            finally:
                if marked:
                    if mark_table:
                        await console_command(r.service_console, "nft", "delete", "table", "inet", MARK_TABLE)
                    if mark_policy:
                        await console_command(r.service_console, "ip", "xfrm", "policy", "delete",
                                              *selector("fwd"), "mark", "0x44", "mask", "0xff")
                else:
                    await update(r, direction, required(r, direction, offload=offloaded))
            await recover(r, p, flows, initial, service, stopped, label)
            assert await attempts(r) == initial_attempts
        await p.rpc("open", [4])
        await warm(r, p, [0, 1, 2, 3, 4], change + "-new", flows[:5])
        await hardware(r, p, change + "-new-hardware", flows[:5])
        await negative(r, p)


@pytest.mark.parametrize("direction", ["fwd", "out"])
async def test_xfrm_default_recovery(service, direction):
    from _flowtable_service import (FLOWS, blocked_probe)
    from _flowtable_selective_neighbour import (hardware as plain_hardware)
    import re

    r = service
    flows = [{**f, "lan": r.lan_ip} for f in FLOWS]
    saved = (await command(r.target, r.session, "ip", "xfrm", "policy", "getdefault"))["stdout"]
    defaults = dict(re.findall(r"\b(in|fwd|out):?\s+(accept|block)\b", saved))
    assert defaults == {"in": "accept", "fwd": "accept", "out": "accept"}, saved
    supervision, initial_attempts = await supervision_status(r), await attempts(r)
    async with peer(r, flows, initial_ids=[0, 1, 3], lease=400) as p:
        await warm(r, p, [0, 1], "default-baseline", flows[:2])
        initial = await plain_hardware(r, p, "default-baseline-hardware", flows[:2])
        for cycle in range(2):
            label = f"default-{direction}-{cycle}"
            await p.rpc("start", [0], count=0, interval=0.05, allow_loss=True)
            await p.rpc("start", [1], count=0, interval=0.05)
            started = time.monotonic()
            try:
                await console_command(r.service_console, "ip", "xfrm", "policy", "setdefault", direction, "block")
                retired = await r.wait(lambda s: not s["entries"], timeout=5)
                assert time.monotonic() - started < 5
                await asyncio.sleep(0.2)
                try:
                    async with asyncio.timeout(5):
                        stopped = await p.rpc("status")
                except TimeoutError:
                    r.record(label + "-control-timeout", {
                        "policies": await command(r.target, r.session, "ip", "-s", "xfrm", "policy"),
                        "defaults": await command(r.target, r.session, "ip", "xfrm", "policy", "getdefault"),
                        "xfrm_stats": await read(r.target, r.session, "/proc/net/xfrm_stat"),
                        "conntrack": await read(r.target, r.session, "/proc/net/nf_conntrack"),
                    })
                    raise
                held = time.monotonic()
                while time.monotonic() - held < 6:
                    await asyncio.sleep(0.25)
                    progress = await p.rpc("status")
                    assert not progress["errors"], progress
                    assert all(progress["received"][str(i)] == stopped["received"][str(i)] for i in (0, 1)), progress
                    assert not (await r.state())["entries"]
                r.record(label + "-blocked", {"state": retired, "progress": progress})
            finally:
                await console_command(r.service_console, "ip", "xfrm", "policy", "setdefault", direction, defaults[direction])
            restored = time.monotonic()
            while True:
                await asyncio.sleep(0.1)
                progress = await p.rpc("status")
                assert not progress["errors"], progress
                if all(progress["received"][str(i)] > stopped["received"][str(i)] for i in (0, 1)):
                    break
                assert time.monotonic() - restored < 25, progress
            reports = await p.rpc("stop", [0, 1])
            assert reports["0"]["lost"] > 0 and reports["1"]["lost"] == 0, reports
            await warm(r, p, [0, 1], label + "-readmitted", flows[:2])
            ready = time.monotonic() - restored
            assert ready < 25
            after = await plain_hardware(r, p, label + "-hardware", flows[:2])
            assert time.monotonic() - restored < 45
            balanced(after, initial["errors"])
            assert after["rearms"] == initial["rearms"]
            await same_service(r, supervision)
            await blocked_probe(r, p)
            assert await attempts(r) == initial_attempts
            r.record(label + "-recovery", {"ready_seconds": ready, "reports": reports, "after": after})
        await p.rpc("open", [2])
        await warm(r, p, [0, 1, 2], "default-new", flows[:3])
        await plain_hardware(r, p, "default-new-hardware", flows[:3])
    assert (await command(r.target, r.session, "ip", "xfrm", "policy", "getdefault"))["stdout"] == saved


async def test_expiry(ipsec_service):
    r, flows = ipsec_service, flows_for(ipsec_service)
    await update(r, "fwd", required(r))
    supervision, initial_attempts = await supervision_status(r), await attempts(r)
    identity = ["src", INNER + "/32", "dst", LAN_INNER + "/32", "proto", "17",
                "sport", str(DPORT), "dport", str(FIRST), "dev", TARGET_LAN_IF, "dir", "fwd"]
    async with peer(r, flows, initial_ids=[0, 1, 2, 3, 5, 6], lease=400, listen_addresses=[INNER]) as p:
        await warm(r, p, [0, 1, 2, 3], "expiry-baseline", flows[:4])
        initial = await hardware(r, p, "expiry-baseline-hardware", flows[:4])
        await p.rpc("start", [2], count=0, interval=0.05, allow_loss=True)
        try:
            await command(r.target, r.session, "ip", "xfrm", "policy", "add", *identity,
                          "priority", "1", "action", "block", "limit", "time-hard", "12")
            retired = await r.wait(lambda s: consistent(s) and not (by_key(s).keys() & keys([2], flows)),
                                   timeout=5)
            await asyncio.sleep(0.2)
            stopped = await p.rpc("status")
            held = time.monotonic()
            while time.monotonic() - held < 4:
                await p.batch([0, 1, 3], count=32, interval=0.01)
                progress = await p.rpc("status")
                assert progress["received"]["2"] == stopped["received"]["2"], progress
                assert not (by_key(await r.state()).keys() & keys([2], flows))
            # No policy command, controller restart, or SA replacement causes
            # recovery here: the kernel's hard-expiry callback is the trigger.
            while True:
                await p.batch([0, 1, 3], count=32, interval=0.01)
                progress = await p.rpc("status")
                assert not progress["errors"], progress
                if progress["received"]["2"] > stopped["received"]["2"]:
                    break
                assert time.monotonic() - held < 30, progress
            reports = await p.rpc("stop", [2])
            assert reports["2"]["lost"] > 0
            await warm(r, p, [0, 1, 2, 3], "expiry-readmitted", flows[:4])
            after = await hardware(r, p, "expiry-hardware", flows[:4])
            assert after["ipsec_policy_invalidations"] > retired["ipsec_policy_invalidations"], (retired, after)
            assert after["rearms"] == initial["rearms"]
            assert await attempts(r) == initial_attempts
            await same_service(r, supervision)
            await plaintext_probe(r, p, "expiry-plaintext")
            await negative(r, p)
            r.record("expiry-recovery", {"blocked": retired, "after": after, "reports": reports})
        finally:
            await console_command(r.service_console, "ip", "xfrm", "policy", "delete", *identity, check=False)


async def test_receive_failslab(ipsec_service):
    from _flowtable_failslab import (slab_fault)

    r, flows = ipsec_service, flows_for(ipsec_service)
    supervision, initial_attempts = await supervision_status(r), await attempts(r)
    identity = [*selector("fwd"), "mark", "0x77", "mask", "0xff"]
    async with peer(r, flows, initial_ids=[0, 1, 2, 3, 5, 6], lease=400, listen_addresses=[INNER]) as p:
        await warm(r, p, [0, 1, 2, 3], "receive-slab-baseline", flows[:4])
        initial = await hardware(r, p, "receive-slab-baseline-hardware", flows[:4])
        try:
            # No test packet carries this mark. Its presence excludes cached
            # hardware admission, exercising native receive and policy checks.
            await command(r.target, r.session, "ip", "xfrm", "policy", "add", *identity,
                          "priority", "1", "action", "allow")
            await r.wait(lambda s: not s["entries"], timeout=5)
            await p.batch([0, 1, 2, 3], count=32, interval=0.02)
            async with slab_fault(r, "ipsec-receive", "ipsec-receive-slab") as fault:
                await p.rpc("start", [2], count=64, interval=0.02, allow_loss=True, udp_timeout=0.2)
                reports = await p.rpc("wait", [2])
                hit = await fault.hit()
                assert reports["2"]["lost"] >= 1 and reports["2"]["received"] > 32, reports
                await p.batch([0, 1, 2, 3], count=64, interval=0.02)
                assert len(await r.ipsec.states()) == 2
                r.record("ipsec-receive-slab-hit", {"reports": reports, "fault": hit})
            await plaintext_probe(r, p, "receive-slab-plaintext")
        finally:
            await console_command(r.service_console, "ip", "xfrm", "policy", "delete", *identity, check=False)
        await warm(r, p, [0, 1, 2, 3], "receive-slab-readmitted", flows[:4])
        after = await hardware(r, p, "receive-slab-hardware", flows[:4])
        balanced(after, initial["errors"])
        assert after["rearms"] == initial["rearms"]
        assert await attempts(r) == initial_attempts
        await same_service(r, supervision)
        await negative(r, p)


def configured(state):
    """An `ip xfrm state` record less its anti-replay context, which moves
    with every frame the SA carries."""
    return "\n".join(line for line in state.splitlines()
                     if not line.strip().startswith("anti-replay context:"))


async def test_pool_recovery(ipsec_service):
    from _flowtable_failslab import (slab_fault)

    r, flows = ipsec_service, flows_for(ipsec_service)
    supervision, initial_attempts = await supervision_status(r), await attempts(r)
    # The BPID is allocated at runtime; cdx publishes the dedicated SEC pool's
    # as a read-only parameter. Not the boot log: a long same-boot run fills
    # the ring buffer and the registration line is gone. Then read BMan's
    # actual available-buffer count for it.
    result = await console_python(r.service_console, r'''
import json
from pathlib import Path
bpid = Path('/sys/module/cdx/parameters/ipsec_bpid').read_text().strip()
assert int(bpid) >= 0, bpid
paths = list(Path('/sys/bus/platform/devices').glob('*.bman/pool_count/' + bpid))
assert len(paths) == 1, paths
print(json.dumps({'path': str(paths[0]), 'bpid': int(bpid)}))
''')
    pool = json.loads(result["stdout"])

    async def available():
        return int(await read(r.target, r.session, pool['path']))

    identity = [*selector("fwd"), "mark", "0x77", "mask", "0xff"]
    async with peer(r, flows, initial_ids=[0, 1, 2, 3, 5, 6], lease=400, listen_addresses=[INNER]) as p:
        await warm(r, p, [0, 1, 2, 3], "pool-baseline", flows[:4])
        initial = await hardware(r, p, "pool-baseline-hardware", flows[:4])
        try:
            await command(r.target, r.session, "ip", "xfrm", "policy", "add", *identity,
                          "priority", "1", "action", "allow")
            await r.wait(lambda s: not s["entries"], timeout=5)
            await p.batch([0, 1, 2, 3], count=32, interval=0.01)
            initial_pool = await available()
            assert 480 <= initial_pool <= 512, initial_pool
            sas = await r.ipsec.states()
            depleted = (await r.state())["ipsec_sec_refused_buffer_depletion"]
            async with slab_fault(r, "ipsec-pool", "ipsec-pool-slab", continuous=True) as fault:
                # Outbound SEC output returns to BMan in hardware; each
                # software receive consumes one dedicated pool buffer.
                await p.rpc("start", [2], count=768, interval=0.001,
                            allow_loss=True, udp_timeout=0.005)
                reports = await p.rpc("wait", [2])
                remaining = await available()
                r.record("pool-exhaustion-window", {"reports": reports, "available": remaining})
                # The short timeout keeps exhaustion within the fault lease.
                # Validated late echoes still consumed a SEC receive buffer.
                delivered = reports["2"]["received"] + reports["2"]["late"]
                lost = reports["2"]["lost"] - reports["2"]["late"]
                assert delivered >= 400 and lost >= 64, reports
                assert remaining == 0
                assert (await read(r.target, r.session, "/sys/kernel/debug/failslab/probability")).strip() == "100"
                await p.batch([0, 1], count=32, interval=0.01)
                await negative(r, p)
                r.record("pool-exhausted", {"pool": pool, "before": initial_pool,
                                           "available": 0, "reports": reports})
            started = time.monotonic()
            hit = await fault.hit()
            # Leave protected traffic idle: refill must recover even when
            # an empty SEC pool cannot generate another receive callback.
            while await available() < 480:
                assert time.monotonic() - started < 5
                await asyncio.sleep(0.05)
            recovered = await available()
            refill_seconds = time.monotonic() - started
            # SEC had no buffer to write what the empty pool could not
            # supply. Those frames never reached Linux: the FMan microcode
            # counted and dropped them, and the adapter reports them as a
            # resource SEC ran out of, not as anything the traffic did.
            state = await r.wait(lambda s: s["ipsec_sec_refused_buffer_depletion"] > depleted, timeout=5)
            r.record("pool-depletion-refusals",
                     {key: value for key, value in state.items() if key.startswith("ipsec_sec_refused")})
            await p.batch([0, 1, 2, 3], count=64, interval=0.01)
            # The same SAs, not reinstalled ones. Their anti-replay context
            # follows SEC's numbering, which the traffic above advanced.
            assert [configured(s) for s in await r.ipsec.states()] == [configured(s) for s in sas]
            await plaintext_probe(r, p, "pool-plaintext")
            r.record("pool-refilled", {"pool": pool, "available": recovered,
                                      "seconds": refill_seconds, "fault": hit})
        finally:
            await console_command(r.service_console, "ip", "xfrm", "policy", "delete", *identity, check=False)
        await warm(r, p, [0, 1, 2, 3], "pool-readmitted", flows[:4])
        after = await hardware(r, p, "pool-hardware", flows[:4])
        balanced(after, initial["errors"])
        assert after["rearms"] == initial["rearms"]
        assert await attempts(r) == initial_attempts
        await same_service(r, supervision)
        await p.rpc("open", [4])
        await warm(r, p, [0, 1, 2, 3, 4], "pool-new", flows[:5])
        await hardware(r, p, "pool-new-hardware", flows[:5])
        await negative(r, p)


async def test_provider_lifetime(ipsec_service):
    from _flowtable_service import (DAEMON, wait_service)
    from _ipsec_inbound_flow_offload import (sec_counter)

    r = ipsec_service
    await console_command(r.service_console, DAEMON, "stop", timeout=45)
    await r.wait(lambda state: not state["bindings"] and not state["entries"])

    async def refs():
        return int(await read(r.target, r.session, "/sys/module/ask_flowtable/refcnt"))

    async def busy(label, expected):
        count = await refs()
        assert count == expected, (label, count, expected)
        result = await console_command(r.service_console, "rmmod", "ask_flowtable", check=False)
        assert result["rc"] != 0 and await refs() == count, result
        r.record(label, {"references": count, "unload": result})

    try:
        # Exercise software SEC submission before deleting the states. Its
        # last completed input must release its secpath without a later
        # packet reusing the buffer. Admission stays stopped throughout.
        counters = ("tx toenc", "tx todec")
        before = {name: await sec_counter(r.session, r.target, TARGET_WAN_IF, name)
                  for name in counters}
        async with peer(r, flows_for(r), initial_ids=[2], lease=60, listen_addresses=[INNER]) as p:
            await p.batch([2], count=16, interval=0.01)
        submitted = {name: await sec_counter(r.session, r.target, TARGET_WAN_IF, name) - before[name]
                     for name in counters}
        # Outbound native XFRM submits skb-backed input to SEC. Inbound ESP
        # can reach SEC directly through its classifier without CPU submit.
        assert submitted["tx toenc"] >= 16, submitted
        r.record("provider-software-sec", {"submitted": submitted, "backend": await r.state()})
        # Admission is drained, so only the two states and the two packet
        # policies retain the provider. Ordinary FWD policy has no callback.
        await busy("provider-live-states-and-policies", 4)
        for direction in ("out", "in"):
            await r.ipsec.remove(direction)
        deadline = time.monotonic() + 5
        while await refs() != 2:
            assert time.monotonic() < deadline
            await asyncio.sleep(0.05)
        await busy("provider-live-policies", 2)
        for agent, argv in list(r.ipsec.cleanup):
            if agent is r.target and argv[:4] == ["ip", "xfrm", "policy", "delete"]:
                await console_command(r.service_console, *argv)
                r.ipsec.cleanup.remove((agent, argv))
        deadline = time.monotonic() + 5
        while await refs():
            assert time.monotonic() < deadline
            await asyncio.sleep(0.05)
        assert not await r.ipsec.states() and not await r.ipsec.policies()
        r.record("provider-released", {"references": 0, "backend": await r.state()})
    finally:
        await console_command(r.service_console, DAEMON, "resume")
        await wait_service(r)
