"""Ordinary ARP on the direct-route flowtable topology."""
from __future__ import annotations

from _flowtable_arp import CHANGED_MAC, arp_environment, check_arp_trace, invalidated, lan_neighbour, neighbours, observe, recover, udp_hardware, wait_neighbour


import pytest

from _topology import LAN_NIC, TARGET_LAN_IF
from _flowtable_rig import (WAN_IP, command)
from _flowtable_tcp import (BLOCK, connection, hardware_transfer, installed, software_tx)


@pytest.mark.rfc("826")
async def test_udp(rig):
    r = rig
    original_mac = r.lan_mac
    async with arp_environment(r):
        await r.table()  # ARP is allowed to resolve after binding.
        before = await udp_hardware(r, count=1024, interval=0.01, label="ageing")
        learned = await neighbours(r)
        assert set(learned) == {r.lan_ip, WAN_IP}, learned
        assert all("PERMANENT" not in n["state"] for n in learned.values()), learned
        r.record("arp-udp-learned", learned)
        r.lan_mac = CHANGED_MAC  # Cleanup must restore even if the command fails.
        await lan_neighbour(r, mac=CHANGED_MAC)
        await invalidated(r, before, "mac-invalidated")
        await wait_neighbour(r, lambda ns: ns.get(r.lan_ip, {}).get("lladdr") == CHANGED_MAC)
        tx_before = await software_tx(r)
        await r.exchange(64, promiscuous=False)
        assert (await software_tx(r))[TARGET_LAN_IF] > tx_before[TARGET_LAN_IF]
        await recover(r)
        before = await udp_hardware(r, label="mac-recovered")
        failure = await lan_neighbour(r, arp_ignore=8)
        # Start from STALE to bound the fault window; the preceding sustained
        # phase proves natural ageing. No address or reachability is fabricated.
        await command(r.target, r.session, "ip", "neigh", "change", r.lan_ip,
                      "dev", TARGET_LAN_IF, "nud", "stale")
        await r.exchange(32, promiscuous=False)
        failed = await wait_neighbour(r, lambda ns: "FAILED" in ns.get(r.lan_ip, {}).get("state", []), timeout=6)
        await invalidated(r, before, "unreachable")
        restored = await lan_neighbour(r, arp_ignore=0)
        r.record("arp-udp-fault", {"start": failure, "restored": restored, "failed": failed})
        await r.exchange(64, promiscuous=False)  # Resolves through ARP in software.
        await recover(r)
        await udp_hardware(r, label="reachability-recovered")
    check_arp_trace(r, original_mac, failure["time"], restored["time"])


async def test_software_fallback(rig):
    r = rig
    r.arp_tag = "software"
    async with arp_environment(r):
        await r.exchange(32, promiscuous=False)  # Resolve both next hops first.
        await r.clear_ct()
        before = await r.state()
        # A hardware table that declines this flow, so the decline itself is
        # under test: a mark carrying a bit the adapter cannot honour. Taken
        # from the running mask rather than assumed, and the lowest such bit,
        # so the flow is refused whatever the boot configured.
        outside = ~int(before["qos_mark_mask"]) & 0xffffffff
        await r.table(mark=outside & -outside)
        await r.exchange(128, promiscuous=False)
        declined = await r.wait(lambda s: s["rejects"] > before["rejects"])
        assert declined["entries"] == declined["neighbour_refs"] == 0, declined
        r.lan_mac = CHANGED_MAC
        await lan_neighbour(r, mac=CHANGED_MAC)
        learned = await wait_neighbour(r, lambda ns: ns.get(r.lan_ip, {}).get("lladdr") == CHANGED_MAC)
        tx_before = await software_tx(r)
        await r.exchange(128, promiscuous=False)
        tx_after = await software_tx(r)
        state = await r.state()
        assert state["entries"] == state["neighbour_refs"] == 0, state
        assert tx_after[TARGET_LAN_IF] - tx_before[TARGET_LAN_IF] >= 128, (tx_before, tx_after)
        r.record("arp-software-fallback", {"declined": declined, "learned": learned,
                 "after": state, "software_tx_before": tx_before, "software_tx_after": tx_after})


@pytest.mark.parametrize("rig", ["tcp"], indirect=True)
async def test_tcp(rig):
    r = rig
    original_mac = r.lan_mac
    async with arp_environment(r):
        await r.table()
        async with connection(r) as conn:
            await installed(r, conn)
            await observe(r, hardware_transfer(r, conn, "upload", label="arp-tcp-ageing"), "ageing")
            before = await r.state()
            assert before["neighbour_refs"] == 2, before
            r.lan_mac = CHANGED_MAC
            # A reply on the measured TCP connection can readmit the flow
            # before retirement is observed. Change the MAC through QGA.
            await lan_neighbour(r, mac=CHANGED_MAC)
            await invalidated(r, before, "mac-invalidated")
            await wait_neighbour(r, lambda ns: ns.get(r.lan_ip, {}).get("lladdr") == CHANGED_MAC)
            await conn.transfer("download")  # Same connection, fresh MAC, software path.
            await recover(r)
            await installed(r, conn)
            await hardware_transfer(r, conn, "download", label="arp-tcp-mac-recovered")
            before = await r.state()
            failure = await conn.configure_neighbour(LAN_NIC, arp_ignore=8, restore_after=8)
            await command(r.target, r.session, "ip", "neigh", "change", r.lan_ip,
                          "dev", TARGET_LAN_IF, "nud", "stale")
            await conn.transfer("upload", size=len(BLOCK))
            failed = await wait_neighbour(r, lambda ns: "FAILED" in ns.get(r.lan_ip, {}).get("state", []), timeout=6)
            await invalidated(r, before, "unreachable")
            # The LAN lease restores ARP independently of this TCP connection.
            # Its queued command and transfer must survive the unreachable interval.
            report = await conn.transfer("upload")
            r.record("arp-tcp-fault", {"start": failure, "failed": failed, "resumed": report})
            await recover(r)
            await installed(r, conn)
            await hardware_transfer(r, conn, "upload", label="arp-tcp-reachability-recovered")
            await conn.close("fin")
            await r.wait(lambda s: s["entries"] == s["neighbour_refs"] == 0, timeout=3)
    check_arp_trace(r, original_mac, failure["time"], failure["time"] + 6)
