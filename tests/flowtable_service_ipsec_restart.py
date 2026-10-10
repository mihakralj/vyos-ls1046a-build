"""An SA delete that cannot prove its classifier entry unlinked stops the
datapath, and CDX restarts it in the same boot.

The entry enqueues to the SA's TO_SEC FQID. While it may still be linked the
queues can go -- an out-of-service FQ rejects the enqueue -- but the FQIDs
cannot: a later SA or any other queue given them would be fed frames it was
never admitted for. So the delete latches the failure and stops the ports, and
the SA's release that follows holds its FQIDs. With the ports idle CDX settles
the entry, gives the FQIDs back and starts the ports again.

Nothing else is touched. The fixture's long-lived SA pair keeps its entries and
its SEC contexts across the restart, and its flows forward in hardware after
it with SEC counting; the deleted SA installs again with the same SPI and
addresses. Bare ESP keys the deleted SA's entry on the SPI, NAT-T on the UDP
pair with the SA picked by SPI inside it, so both are run.
"""
from __future__ import annotations

import asyncio

import pytest

from _flowtable_restart import (HOLD, ROOT_FAULT, RUNNING, assert_restarted_cleanly, knob, log_marks,
                                ports, require_knobs, restart_budget, restart_counts, wait_restarted,
                                wait_running, wait_stopped, write)
from _ipsec_helpers import endpoints_down, endpoints_up, iface_index, sa_add, sa_del
from _topology import TARGET_WAN_IF
from _flowtable_connections import (peer)
from _flowtable_rig import (command, console_command, read)
from _flowtable_selective_neighbour import (warm)
from _flowtable_service_ipsec import (INNER, Transform, flows_for, hardware)
from _flowtable_service_ipsec_natt import (NATT)
from _flowtable_service_ipsec_replay import (sa_state)

# The SA whose delete fails: inbound or outbound, on documentation-range endpoints of its
# own, beside the fixture's pair. Its peer does not exist and sends nothing.
LOCAL, PEER = "198.18.91.1", "198.18.91.2"
PEER_MAC = "02:00:00:00:91:02"
SPI = 0x4d6f6e70
REQID = 49101


# NAT-T on the suite's own ports: the orchestrator's IKE daemon holds 4500.
@pytest.mark.parametrize("ipsec_service", [Transform(), NATT], ids=["esp", "natt"], indirect=True)
@pytest.mark.parametrize("direction", ["in", "out"])
async def test_packet_offload_sa_unproven_delete_restarts(ipsec_service, direction):
    r, flows = ipsec_service, flows_for(ipsec_service)
    src, dst = (PEER, LOCAL) if direction == "in" else (LOCAL, PEER)
    inbound = direction == "in"
    await require_knobs(r.target, r.session, ROOT_FAULT)
    natt = r.ipsec.transform.encap
    con = r.service_console
    ifindex = await iface_index(r.target, r.session, TARGET_WAN_IF)
    await endpoints_up(r.target, r.session, iface=TARGET_WAN_IF, local=LOCAL, peer=PEER, lladdr=PEER_MAC)
    printk = (await read(r.target, r.session, "/proc/sys/kernel/printk")).split()
    try:
        # The restart budget is the case's own, and is let go of -- the hold
        # released, the fault disarmed, over the console -- before the peer's
        # teardown needs the agent.
        async with peer(r, flows, initial_ids=[0, 1, 2, 3], lease=400, listen_addresses=[INNER]) as p, \
                restart_budget(con, r, (ROOT_FAULT,)):
            await warm(r, p, [0, 1, 2, 3], "restart-baseline", flows[:4])
            baseline = await hardware(r, p, "restart-baseline-hardware", flows[:4])
            sas = dict(r.ipsec.active)
            sec_before = {direction: await sa_state(r, spi, direction) for direction, spi in sas.items()}
            reply = await sa_add(r.target, r.session, src=src, dst=dst, spi=SPI, reqid=REQID,
                                 ifindex=ifindex, inbound=inbound, natt=natt)
            assert reply.ok, reply
            installed = await r.state()
            assert installed["ipsec_sas"] == baseline["ipsec_sas"] + 1, installed
            sa_dirs = (await console_command(con, "ls", "/proc/fqid_stats/sa"))["stdout"].split()
            # The stop, the SA's release and the restart are all reported
            # from work items, after the delete returns, and a printk would
            # land inside whichever console read runs then.
            await command(r.target, r.session, "sysctl", "-w", "kernel.printk=1 4 1 7")
            marks = await log_marks(con)
            assert await ports(con) == RUNNING
            await write(con, HOLD, 1)
            # Armed over the agent while it still reaches the DUT; the delete
            # that consumes it goes over the console, since the ports may stop
            # before an agent reply could leave.
            result = await r.target.fs_write(r.session, ROOT_FAULT, "1")
            assert result["errno"] == 0, result
            await console_command(con, "ip", "xfrm", "state", "delete", "src", src, "dst", dst,
                                  "proto", "esp", "spi", hex(SPI))
            stopped, _ = await wait_stopped(con, installed)
            assert stopped["ipsec_sas"] == baseline["ipsec_sas"], stopped
            assert await knob(con, ROOT_FAULT) == "0"
            # The SA's release, a step per second on the CDX timer until its
            # queues are out of service and SEC is done with it, would give
            # its FQIDs back, and takes the SA's procfs entry with it. Held,
            # the restart waits until that release has run and held them, so
            # it is the restart that gives them back.
            for _ in range(90):
                left = (await console_command(con, "ls", "/proc/fqid_stats/sa"))["stdout"].split()
                if len(left) < len(sa_dirs):
                    break
                await asyncio.sleep(0.5)
            else:
                raise AssertionError(f"the deleted SA was not released: {sa_dirs} -> {left}")
            restarted = await wait_restarted(con, installed)
            assert await wait_running(con) == RUNNING
            line = await assert_restarted_cleanly(con, marks)
            resolved, released, _ = restart_counts(line)
            assert resolved == 1 and released == 1, line
            r.record("ipsec-restart", {"installed": installed, "stopped": stopped,
                                       "restarted": restarted, "log": line})
            # The pair's flows carry the tunnel in hardware again, SEC
            # counting both ways.
            await warm(r, p, [0, 1, 2, 3], "restart-readmitted", flows[:4])
            await hardware(r, p, "restart-hardware", flows[:4])
            # The accounting pass publishes SEC's per-SA counters once a second.
            await asyncio.sleep(1.5)
            for sa_dir, spi in sas.items():
                after = await sa_state(r, spi, sa_dir)
                assert after and after["packets"] - sec_before[sa_dir]["packets"] >= 256, (
                    sa_dir, sec_before[sa_dir], after)
        # The deleted SA installs again with the same SPI and addresses: the
        # restart settled its entry, so the key is free.
        reply = await sa_add(r.target, r.session, src=src, dst=dst, spi=SPI, reqid=REQID,
                             ifindex=ifindex, inbound=inbound, natt=natt)
        assert reply.ok, reply
        again = await r.state()
        assert again["ipsec_sas"] == baseline["ipsec_sas"] + 1, again
        assert again["fatal"] == 0 and again["restarts"] == installed["restarts"] + 1, again
        assert again["resume_failures"] == installed["resume_failures"], again
        reply = await sa_del(r.target, r.session, dst=dst, spi=SPI)
        assert reply.ok, reply
        final = await r.wait(lambda s: s["ipsec_sas"] == baseline["ipsec_sas"])
        assert final["fatal"] == 0, final
    finally:
        await sa_del(r.target, r.session, dst=dst, spi=SPI)
        await endpoints_down(r.target, r.session, iface=TARGET_WAN_IF, local=LOCAL, peer=PEER)
        await command(r.target, r.session, "sysctl", "-w", "kernel.printk=" + " ".join(printk[:4]))
