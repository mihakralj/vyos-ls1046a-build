"""A multicast delete whose hardware barrier fails parks what it unlinked.

Withdrawing a group unlinks its classifier key and its listener chain from
tables the FMan microcode may be walking at that moment. Only a successful
host-command barrier proves the walk has left them; until one does, the memory
stays allocated in CDX's quarantine and /proc's `quarantine` counts it. These
cases fail that barrier on purpose while a control group stands, and require:

  - the parked entries to be exactly the withdrawn group's: its root plus one
    per listener for a delete, the displaced chain for a listener swap;
  - the control group and the survivors to keep replicating exactly, in
    hardware, while those entries are parked;
  - the next good barrier to release every one of them, including one the
    flowtable backend issues itself when no multicast delete is left to.

Any completed barrier on the PCD releases the backlog, so parked counts are
read straight after the failure. The windows between stay exact because the
cases that watch them run no unicast offload: no admission or unicast delete
can sync there. Nor any other multicast: a stream nobody on a snooping bridge
wants gets a discard entry of its own, whose add and age-out each end on a
barrier, so the bridged cases keep the segments' own multicast -- a bench
host's SSDP -- off the ports while they run.

The image is KASAN, and the splat window fails a case whose CPU touches memory
the quarantine should have kept. A walker in the microcode is not something
KASAN can see, which is why the accounting is exact rather than eventual.

Two knobs, because a group delete and a listener swap end on different
barriers. /proc/fm_ehash_hcsync_fail fails the one inside the classifier key's
own delete; /proc/cdx_mc_hcsync_fail fails the one dpa_control_mc.c issues
after unlinking a listener chain. Both read back how many failures remain
armed, and both are disarmed on the way out whatever happened.

The last two cases fail a delete before its unlink instead (ehash_fail_unlink),
which no barrier settles: the datapath stops, and CDX restarts it in the same
boot once the ports are idle (_flowtable_restart.py).
"""
from __future__ import annotations

from contextlib import asynccontextmanager
import time

import pytest

from _flowtable_restart import (HOLD, ROOT_FAULT, RUNNING, assert_restarted_cleanly, dmesg_count, knob,
                                log_marks, ports, require_knobs, restart_budget, restart_counts,
                                wait_restarted, wait_running, wait_stopped, write)
from _mcast_windows import (COUNT, bridge_settings, delivered, dut_console, in_hardware, in_software, learn, mcast_rows, mdb, members, moved, mroute_row, packets, quiet, stream, streamed, summary)
from _topology import (LAN_NIC, TARGET_LAN_IF, TARGET_WAN_IF, TopologyStack, dut_vlan_subif,
                       lan_vlan_subif)
from _flowtable_rig import (HEALTH_BASELINE, command, console_command, hardware_proof, read)
from _mcast_e2e import (wan_source_address)
from _mroute_capacity import (_daemon)

DELETE_BARRIER = "/proc/fm_ehash_hcsync_fail"
SPLICE_BARRIER = "/proc/cdx_mc_hcsync_fail"
# Withdrawn, and standing throughout.
GROUPS = {4: ("239.9.7.1", "239.9.7.2"), 6: ("ff1e::9:7:1", "ff1e::9:7:2")}
SWAP_GROUP = {4: "239.9.7.3", 6: "ff1e::9:7:3"}
SWAP_VID = 323


@asynccontextmanager
async def armed(r, knob, failures):
    present = await r.target.fs_read(r.session, knob)
    if present["errno"]:
        pytest.fail(f"{knob} is missing: this is not the fault-injection test image")
    result = await r.target.fs_write(r.session, knob, str(failures))
    assert result["errno"] == 0, (knob, result)
    try:
        yield
    finally:
        result = await r.target.fs_write(r.session, knob, "0")
        assert result["errno"] == 0, (knob, result)


async def remaining(r, knob):
    return (await read(r.target, r.session, knob)).split()[0]


def routed_groups(state):
    """Routed groups with somewhere to go. smcrouted adds a listener-less entry
    for whatever its WAN VIF hears -- the rig's segment carries an SSDP sender
    -- and the learner tracks and refuses it, so mroute_groups moves with
    traffic no case sends."""
    return sorted((row["src"], row["group"]) for row in state["mroute"]
                  if not (row["state"] == "refused-listener" and row["listeners"] == "-"))


class Routed:
    """smcroute's MFC: the WAN port in, the LAN port out, a routing hop."""
    kind, hops = "mroute", 63

    def __init__(self, r, ctl, family):
        self.r, self.ctl, self.family = r, ctl, family
        self.source = wan_source_address(family)
        self.observers = [(r.lan, {LAN_NIC: r.dut_lan_mac})]

    def row(self, state, group):
        return mroute_row(state, group, self.source)

    def installed(self, state, group):
        row = self.row(state, group)
        return bool(row) and row["state"] == "installed" and members(row, "listeners") == {f"{TARGET_LAN_IF}/0"}

    async def add(self, *groups):
        for group in groups:
            await self.ctl("add", TARGET_WAN_IF, self.source, group, TARGET_LAN_IF)
        # Carried once Linux has been seen forwarding each: frames first.
        return await learn(self.r, [stream(self.family, g, hops=63) for g in groups],
                           lambda s: all(self.installed(s, g) for g in groups),
                           f"routed {groups} installed")

    async def remove(self, group):
        await self.ctl("remove", TARGET_WAN_IF, self.source, group)

    def withdrawn(self, state, group):
        # Traffic for a withdrawn route can make smcrouted install a negative
        # entry. It has no listeners and stays in software.
        row = self.row(state, group)
        return row is None or (row["state"] == "refused-listener" and row["listeners"] == "-")


class Bridged:
    """A static membership on the snooping bridge; traffic supplies the key."""
    kind, hops = "mcast", 64

    def __init__(self, r, bridge, family):
        self.r, self.bridge, self.family = r, bridge, family
        self.source = wan_source_address(family)
        # The replica's source MAC belongs to the bridged-MAC work, which
        # asserts it on the wire; these cases count copies.
        self.observers = [(r.lan, {LAN_NIC: None})]

    def row(self, state, group):
        rows = mcast_rows(state, group)
        assert len(rows) <= 1, rows
        return rows[0] if rows else None

    def installed(self, state, group):
        row = self.row(state, group)
        return (bool(row) and row["state"] == "installed" and row["in"] == TARGET_WAN_IF and
                members(row, "ports") == {f"{TARGET_LAN_IF}/0"})

    async def add(self, *groups):
        for group in groups:
            await mdb(self.r, self.bridge, group, add=True)
        return await learn(self.r, [stream(self.family, g, hops=64) for g in groups],
                           lambda s: all(self.installed(s, g) for g in groups),
                           f"bridged {groups}")

    async def remove(self, group):
        await mdb(self.r, self.bridge, group, add=False)

    def withdrawn(self, state, group):
        return self.row(state, group) is None


async def withdrawal(r, learner, family):
    """The shared body: a failed delete parks, a good one releases."""
    target, control = GROUPS[family]
    installed = learner.kind + "_installed"

    def window(groups, label):
        return r.window([stream(family, g, hops=learner.hops) for g in groups], learner.observers,
                        ingress=TARGET_WAN_IF, label=f"quarantine-{learner.kind}-v{family}-{label}")

    def row(group):
        return lambda state: learner.row(state, group)

    await learner.add(target, control)
    both = await window([target, control], "baseline")
    for group in (target, control):
        assert delivered(both, streamed(both, group), LAN_NIC), group
        assert moved(both, row(group)) == COUNT, (group, summary(both["after"]))
    in_hardware(both, streams=2)

    before = await r.proc()
    assert before["quarantine"] == 0, summary(before)
    async with armed(r, DELETE_BARRIER, 1):
        await learner.remove(target)
        # The count drops after the delete returns, and /proc reads under the
        # same transaction, so whatever the delete parked is visible by then.
        # A bridged group nobody wants any more is first swapped to a discard
        # and deleted at the first refresh that counts nothing, up to two
        # refreshes on, which is the delete that meets the barrier.
        # This group's own row, not the total alone: the WAN segment's own
        # multicast has flows whose entries come and go meanwhile.
        parked = await r.settle(lambda s: learner.withdrawn(s, target) and
                                s[installed] <= before[installed] - 1,
                                f"{target} withdrawn from hardware", timeout=30)
        assert await remaining(r, DELETE_BARRIER) == "armed=0", "the delete never reached its barrier"
    assert learner.withdrawn(parked, target), summary(parked)
    # The classifier key and its one listener entry: unlinked, not freed.
    assert parked["quarantine"] == 2, summary(parked)
    assert learner.installed(parked, control), summary(parked)

    # The survivor replicates exactly while the withdrawn group's entries sit
    # parked; the withdrawn stream now reaches the CPU and nothing else.
    after = await window([target, control], "parked")
    assert not delivered(after, streamed(after, target), LAN_NIC)
    assert delivered(after, streamed(after, control), LAN_NIC)
    assert moved(after, row(control)) == COUNT, summary(after["after"])
    in_software(after)
    assert after["stream_cpu"] < COUNT * 1.1, (after["stream_cpu"], after["cpu"], after["idle"])
    # No barrier ran in that window, so nothing may have been released.
    assert after["after"]["quarantine"] == 2, summary(after["after"])
    assert learner.withdrawn(after["after"], target), summary(after["after"])

    # The next good barrier is the next delete that reaches one. It releases
    # everything parked before it, not only its own entries.
    await learner.remove(control)
    released = await r.settle(lambda s: s[installed] == r.initial[installed],
                              f"{control} withdrawn from hardware")
    assert released["quarantine"] == 0, summary(released)
    assert learner.withdrawn(released, control), summary(released)

    # The key the failed delete gave up installs again and carries traffic.
    await learner.add(target)
    again = await window([target], "reinstalled")
    assert delivered(again, streamed(again, target), LAN_NIC)
    assert moved(again, row(target)) == COUNT, summary(again["after"])
    in_hardware(again)
    await learner.remove(target)
    final = await r.settle(lambda s: s[installed] == r.initial[installed], f"{target} withdrawn")
    assert final["quarantine"] == 0, summary(final)
    for counter in ("mcast_install_errors", "mroute_install_errors"):
        assert final[counter] == r.initial[counter], (counter, summary(final))


@pytest.mark.parametrize("family", [4, 6])
async def test_routed(multicast_rig, family):
    r = multicast_rig
    async with _daemon(r.target, r.session, [TARGET_WAN_IF, TARGET_LAN_IF]) as ctl:
        await withdrawal(r, Routed(r, ctl, family), family)


@pytest.mark.parametrize("family", [4, 6])
async def test_bridged(multicast_rig, mcast_bridge, family):
    r = multicast_rig
    # A querier that counts in both families, so a withdrawn group is no
    # longer flooded to the port it left; and no other multicast on the ports.
    async with bridge_settings(r, mcast_bridge), quiet(r, GROUPS[family]):
        await withdrawal(r, Bridged(r, mcast_bridge, family), family)


@pytest.mark.parametrize("family", [4, 6])
async def test_listener_swap(multicast_rig, family):
    """One of two listeners goes and the swap's barrier fails.

    The group stays installed: the root entry now names the survivor's chain
    and has not left the table, so frames keep matching throughout. The
    displaced chain is what is parked, both of its entries, and the group's
    own delete is the good barrier that releases them."""
    r = multicast_rig
    group, source = SWAP_GROUP[family], wan_source_address(family)
    untagged, tagged = f"{TARGET_LAN_IF}/0", f"{TARGET_LAN_IF}/{SWAP_VID}"
    topology = TopologyStack()
    try:
        oif = await dut_vlan_subif(topology, r.target, r.session, parent=TARGET_LAN_IF, vid=SWAP_VID,
                                   ipv4="198.18.163.1/24", ipv6="fd00:163::1/64")
        peer = await lan_vlan_subif(topology, r.lan, parent=LAN_NIC, vid=SWAP_VID)
        observers = [(r.lan, {LAN_NIC: r.dut_lan_mac, peer: r.dut_lan_mac})]
        async with _daemon(r.target, r.session, [TARGET_WAN_IF, TARGET_LAN_IF, oif]) as ctl:
            await ctl("add", TARGET_WAN_IF, source, group, TARGET_LAN_IF, oif)

            def carried(state, listeners):
                row = mroute_row(state, group, source)
                return bool(row) and row["state"] == "installed" and members(row, "listeners") == listeners

            await learn(r, [stream(family, group, hops=63)],
                        lambda s: carried(s, {untagged, tagged}), "two listeners installed")
            both = await r.window([stream(family, group, hops=63)], observers,
                                  ingress=TARGET_WAN_IF, label=f"swap-{family}-both")
            # What the tail of the confirming burst left on the root.
            base = packets(mroute_row(both["before"], group, source))
            assert delivered(both, both["streams"][0], LAN_NIC)
            assert delivered(both, both["streams"][0], peer)
            in_hardware(both)

            before = await r.proc()
            assert before["quarantine"] == 0, summary(before)
            async with armed(r, SPLICE_BARRIER, 1):
                # The VIF goes with its device and the group is re-derived
                # onto the listener that is left: a chain swap, not a delete.
                await command(r.target, r.session, "ip", "link", "del", oif)
                swapped = await r.settle(lambda s: carried(s, {untagged}), "the swap to one listener")
                assert (await remaining(r, SPLICE_BARRIER)).startswith("armed=0"), \
                    "the swap never reached its barrier"
            assert swapped["mroute_installed"] == before["mroute_installed"], summary(swapped)
            # Never out of hardware on the way: a group taken out and put back
            # also ends on one listener with the count it had, but under an
            # entry added again, which its own row counts. The global refusal
            # count would not say so: it moves for any group, smcrouted's
            # listener-less entries for what the WAN VIF hears among them.
            now, then = mroute_row(swapped, group, source), mroute_row(before, group, source)
            assert now and then and now["adds"] == then["adds"], summary(swapped)
            assert swapped["mroute_install_errors"] == before["mroute_install_errors"], \
                summary(swapped)
            assert swapped["quarantine"] == 2, summary(swapped)

            survivor = await r.window([stream(family, group, hops=63)], observers,
                                      ingress=TARGET_WAN_IF, label=f"swap-{family}-survivor")
            assert delivered(survivor, survivor["streams"][0], LAN_NIC)
            assert not delivered(survivor, survivor["streams"][0], peer)
            in_hardware(survivor)
            # The same root counted both windows: it never left the table.
            assert packets(mroute_row(survivor["after"], group, source)) == base + 2 * COUNT, \
                summary(survivor["after"])
            assert survivor["after"]["quarantine"] == 2, summary(survivor["after"])

            await ctl("remove", TARGET_WAN_IF, source, group)
            released = await r.settle(lambda s: mroute_row(s, group, source) is None and
                                      s["mroute_installed"] == r.initial["mroute_installed"],
                                      "the group's own delete")
            assert released["quarantine"] == 0, summary(released)
    finally:
        await topology.teardown("listener swap quarantine")


async def test_released_without_multicast(multicast_rig, rig):
    """The only group is withdrawn and its barrier fails, so no multicast
    delete is left to release what it parked.

    The flowtable backend refuses every new unicast entry, and the adapter's
    load, while anything is parked, so it retries the barrier itself. A fresh
    unicast flow's admission releases the backlog and is admitted, with no
    multicast operation in between; parked a second time with nothing bound,
    the adapter's unload releases it and the adapter loads again. Both used to
    wait for an unrelated multicast or IPsec delete that might never come."""
    m, r = multicast_rig, rig
    group = GROUPS[4][0]
    async with _daemon(r.target, r.session, [TARGET_WAN_IF, TARGET_LAN_IF]) as ctl:
        learner = Routed(m, ctl, 4)

        async def park(label):
            await learner.add(group)
            before = await m.proc()
            assert before["quarantine"] == 0 and before["mroute_installed"] == 1, summary(before)
            async with armed(m, DELETE_BARRIER, 1):
                await learner.remove(group)
                parked = await m.settle(lambda s: s["mroute_installed"] == 0,
                                        f"{group} withdrawn from hardware before the {label}")
                assert await remaining(m, DELETE_BARRIER) == "armed=0", "the delete never reached its barrier"
            # The classifier key and its one listener entry, read before any
            # later barrier on the PCD could release them.
            assert learner.withdrawn(parked, group) and parked["quarantine"] == 2, summary(parked)
            return parked

        parked = await park("admission")
        await r.table()
        # Binding alone issues no barrier: the flow's admission has to.
        bound = await r.state()
        assert bound["quarantine"] == 2 and bound["entries"] == 0, bound
        # Admission retries at most once a second, so a flow it declined is
        # offered again on a later refresh: keep traffic flowing. An XFRM
        # policy anywhere in the namespace bypasses the software fast path and
        # defers that offer to the flow's 30-second expiry.
        deadline = time.monotonic() + 45
        while True:
            await r.exchange(32, promiscuous=False)
            admitted = await r.state()
            if admitted["entries"] == 2:
                break
            assert time.monotonic() < deadline, admitted
        assert admitted["quarantine"] == 0 and admitted["errors"] == parked["errors"], admitted
        for counter in ("mroute_installed", "mcast_groups", "mcast_installed",
                        "mroute_install_errors", "mcast_install_errors"):
            assert admitted[counter] == parked[counter], (counter, summary(parked), summary(admitted))
        assert routed_groups(admitted) == routed_groups(parked), (summary(parked), summary(admitted))
        admitted = await hardware_proof(r)
        drained = await r.delete_table()
        assert drained["quarantine"] == 0 and drained["errors"] == parked["errors"], drained

        await park("reload")
        # Nothing multicast is left installed for the unload to delete: that
        # delete's own barrier would release the backlog, and an unload that
        # never retries one would pass anyway.
        idle = await m.proc()
        assert idle["mcast_installed"] == idle["mroute_installed"] == 0, summary(idle)
        assert idle["quarantine"] == 2, summary(idle)
        console = await dut_console("quarantine-reload")
        try:
            await console_command(console, "rmmod", "ask_flowtable", timeout=30)
            try:
                # CDX's own read-back, the adapter's being gone with it.
                between = await console_command(console, "cat", "/proc/cdx_mc_hcsync_fail")
            finally:
                loaded = await console_command(console, "modprobe", "ask_flowtable", timeout=30, check=False)
        finally:
            console.close()
        assert "pending=0" in between["stdout"].split(), between
        assert loaded["rc"] == 0, loaded
        reloaded = await m.proc()
        assert reloaded["quarantine"] == reloaded["fatal"] == reloaded["bindings"] == 0, summary(reloaded)
        # The reload restarted the error count; health is measured from it.
        HEALTH_BASELINE["errors"] = reloaded["errors"]
        m.record("mcast-quarantine-released", {"parked": summary(parked), "admitted": summary(admitted),
                                               "unloaded": between["stdout"], "reloaded": summary(reloaded)})


async def restart_after_withdrawal(r, learner, family):
    """A group delete that cannot prove its unlink stops the datapath, and CDX
    restarts it in the same boot.

    The cases above fail the barrier after the key has left the table, so its
    entries can be parked. This one fails the delete before the unlink
    (ehash_fail_unlink): the root may still resolve and replicate through a
    listener chain nothing owns any more -- the state a unicast -EIO latches --
    so the ports stop rather than a root replicating to a revoked listener
    while /proc calls the group gone. With the ports idle CDX settles the root
    and its listeners and starts them again. The group standing beside it then
    replicates exactly in hardware, and the withdrawn one joins again and does
    too -- the settled key left nothing behind to collide with.

    The stopped ports take the management path with them, so the window is
    read over the UART; everything after the restart goes over the agent."""
    target, control = GROUPS[family]
    installed = learner.kind + "_installed"

    def window(groups, label):
        return r.window([stream(family, g, hops=learner.hops) for g in groups], learner.observers,
                        ingress=TARGET_WAN_IF, label=f"restart-{learner.kind}-v{family}-{label}")

    def row(group):
        return lambda state: learner.row(state, group)

    await require_knobs(r.target, r.session, ROOT_FAULT)
    await learner.add(target, control)
    both = await window([target, control], "baseline")
    for group in (target, control):
        assert delivered(both, streamed(both, group), LAN_NIC), group
        assert moved(both, row(group)) == COUNT, (group, summary(both["after"]))
    in_hardware(both, streams=2)
    live = await r.proc()
    assert live["fatal"] == 0, summary(live)
    # The stop and the restart are reported from a work item, after the
    # withdrawal returns, and a printk would land inside whichever console read
    # runs then. dmesg keeps every line for the assertions below.
    printk = (await read(r.target, r.session, "/proc/sys/kernel/printk")).split()
    await command(r.target, r.session, "sysctl", "-w", "kernel.printk=1 4 1 7")
    try:
        console = await dut_console(f"mcast-restart-{learner.kind}")
        try:
            # Its own restart budget, and on the way out, over this console
            # while it is still open, the hold released and the fault
            # disarmed: a case that fails inside the window must not leave
            # the ports stopped for the agent's teardown to fail against.
            async with restart_budget(console, r, (ROOT_FAULT,)):
                marks = await log_marks(console)
                failed = await dmesg_count(console, "classifier delete failed pre-unlink")
                assert await ports(console) == RUNNING
                await write(console, HOLD, 1)
                # Armed over the agent while it still reaches the DUT; the
                # withdrawal that consumes it is the last command the
                # management path carries. A bridged group nobody wants is
                # first swapped to a discard and deleted a refresh or two
                # later, which is the delete that fails.
                result = await r.target.fs_write(r.session, ROOT_FAULT, "1")
                assert result["errno"] == 0, result
                await learner.remove(target)
                stopped, _ = await wait_stopped(console, live, timeout=30)
                # The adapter let the withdrawn group go; the hardware may
                # not have, which is why the stopped ports and not /proc are
                # the proof.
                assert learner.withdrawn(stopped, target), summary(stopped)
                assert await knob(console, ROOT_FAULT) == "0"
                assert await dmesg_count(console, "classifier delete failed pre-unlink") == failed + 1
                r.record(f"mcast-restart-{learner.kind}-stopped", {"live": summary(live),
                                                                   "stopped": summary(stopped)})
                restarted = await wait_restarted(console, live)
                assert await wait_running(console) == RUNNING
                line = await assert_restarted_cleanly(console, marks)
                resolved, released, _ = restart_counts(line)
                assert resolved == 1 and released == 0, line
        finally:
            console.close()
        # The survivor is carried and replicates exactly, in hardware. Whether
        # its entry stood through the window or aged out while nothing reached
        # it, the learner has it installed again once traffic is offered.
        await learn(r, [stream(family, control, hops=learner.hops)],
                    lambda s: learner.installed(s, control) and learner.withdrawn(s, target),
                    f"{control} carried after the restart")
        survivor = await window([control], "survivor")
        assert delivered(survivor, streamed(survivor, control), LAN_NIC)
        assert moved(survivor, row(control)) == COUNT, summary(survivor["after"])
        in_hardware(survivor)
        assert survivor["after"]["quarantine"] == 0, summary(survivor["after"])
        # The withdrawn group joins again and replicates in hardware beside it.
        await learner.add(target)
        again = await window([target, control], "rejoined")
        for group in (target, control):
            assert delivered(again, streamed(again, group), LAN_NIC), group
            assert moved(again, row(group)) == COUNT, (group, summary(again["after"]))
        in_hardware(again, streams=2)
        assert again["after"][installed] == live[installed], summary(again["after"])
        await learner.remove(target)
        await learner.remove(control)
        final = await r.settle(lambda s: s[installed] == r.initial[installed] and not s["quarantine"],
                               "both groups withdrawn after the restart", timeout=30)
        assert final["fatal"] == 0 and final["restarts"] == live["restarts"] + 1, summary(final)
        assert final["resume_failures"] == live["resume_failures"], summary(final)
        r.record(f"mcast-restart-{learner.kind}", {"restarted": summary(restarted), "log": line,
                                                   "final": summary(final)})
    finally:
        await command(r.target, r.session, "sysctl", "-w", "kernel.printk=" + " ".join(printk[:4]))


async def test_unproven_delete_restarts_routed(multicast_rig):
    r = multicast_rig
    async with _daemon(r.target, r.session, [TARGET_WAN_IF, TARGET_LAN_IF]) as ctl:
        await restart_after_withdrawal(r, Routed(r, ctl, 4), 4)


async def test_unproven_delete_restarts_bridged(multicast_rig, mcast_bridge):
    r = multicast_rig
    async with bridge_settings(r, mcast_bridge), quiet(r, GROUPS[4]):
        await restart_after_withdrawal(r, Bridged(r, mcast_bridge, 4), 4)
