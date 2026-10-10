"""Failslab sweep over an offloaded SA install — M7-class regression net.

Drives cdx_ipsec_sec_sa_context_alloc (cdx/cdx_dpa_ipsec.c) into NULL with
fork-isolated fail-nth. The key and descriptor buffers are allocated there,
from M_ipsec_sa_cache_create, immediately after sa_alloc(); on NULL the
allocator frees what it has taken so far and the caller frees the SA struct.

The door is xfrmdev_ops rather than FCI, which is what re-pointing this test
meant: `XFRM_MSG_NEWSA` reaches xdo_dev_state_add(), then cdx_ipsec_sa_add(),
then the same allocator. What FCI spelled as five commands in sequence is one
message here, so one sweep covers the whole install instead of one step of it.

The window is the head of that install, and it is worth being exact about
what that means now rather than after a green run proves nothing. Faulting
the first N allocations covers xfrm's own state construction *and* the SA
cache, in that order, and the prefix is not short: `__xfrm_init_state` builds
the ESP transform through `crypto_alloc_aead("authenc(hmac(sha256),cbc(aes))")`
before `xfrm_dev_state_add()` ever reaches this driver. FCI had almost no
prefix; this one has tens of allocations. Nothing observable from the netlink
reply separates a fault in that prefix from one in the SA cache, so the
default sweep is long enough to cover both rather than proving it reached the
second. What keeps the result meaningful is the kmemleak filter: it names cdx
symbols only, so a leak it reports is cdx's regardless of which half of the
window faulted.

The tail — the descriptor build and the classifier entry — is
ipsec_dma_balance.py's window.

Oracles:
  - splat_window — no oops/lockdep/UBSAN/KASAN report during the sweep.
  - kmemleak, filtered to IPSEC_LEAK_FILTER — nothing leaked across the
    faulted iterations' unwind paths.

Every attempt uses a fresh SPI, so a faulted install cannot collide with the
next attempt's key in the SA hash table.
"""

from __future__ import annotations

import asyncio
import os

import pytest

from _ipsec_helpers import (
    IPSEC_LEAK_FILTER,
    SA_RELEASE_GRACE_S,
    endpoints_down,
    endpoints_up,
    iface_index,
    measure_install_allocations,
    sa_add,
    sa_del,
    sa_flush_range,
    sa_install_probe,
)
from _topology import TARGET_WAN_IF

LOCAL = "198.18.88.1"
PEER = "198.18.88.2"
PEER_MAC = "02:00:00:00:88:02"
REQID = 0x8801
SPI_BASE = 0xFA1AB000
# Distinct SPIs for the two probes: a deleted state's hardware teardown runs
# on a workqueue, so reusing one a round trip later can collide with an entry
# that has not gone yet and skip the whole file.
PROBE_SPI = SPI_BASE - 1
DEPTH_SPI = SPI_BASE - 2

NSWEEP = int(os.environ.get("ASK_IPSEC_FAILSLAB_SWEEP", "100"))


async def test_ipsec_install_failslab_sweep(
    aiohttp_session, target_agent, splat_window,
):
    ifindex = await iface_index(target_agent, aiohttp_session, TARGET_WAN_IF)
    await endpoints_up(target_agent, aiohttp_session, iface=TARGET_WAN_IF,
                       local=LOCAL, peer=PEER, lladdr=PEER_MAC)
    swept = [SPI_BASE + n for n in range(1, NSWEEP + 1)]
    try:
        reason = await sa_install_probe(
            target_agent, aiohttp_session, src=LOCAL, dst=PEER, spi=PROBE_SPI,
            reqid=REQID, ifindex=ifindex)
        if reason:
            pytest.skip(reason)

        # How deep the install goes, so a sweep that never reaches the
        # allocator is visible as a short window rather than as a clean run.
        depth = await measure_install_allocations(
            target_agent, aiohttp_session, src=LOCAL, dst=PEER, spi=DEPTH_SPI,
            reqid=REQID, ifindex=ifindex)

        await target_agent.kmemleak_clear(aiohttp_session)
        outcomes: list[tuple[int, int | None, bool]] = []
        refused: list[int] = []
        try:
            for n in range(1, NSWEEP + 1):
                reply = await sa_add(
                    target_agent, aiohttp_session, src=LOCAL, dst=PEER,
                    spi=SPI_BASE + n, reqid=REQID, ifindex=ifindex,
                    failslab_times=n)
                outcomes.append((n, reply.error, reply.lost))
                if reply.refused:
                    refused.append(n)
                # An install that survived its fault left a real SA with a SEC
                # context and frame queues; take it out now rather than hold a
                # sweep's worth at once.
                await sa_del(target_agent, aiohttp_session, dst=PEER,
                             spi=SPI_BASE + n)
        finally:
            # Idempotent safety net for an interrupted loop, so a surviving
            # kmemleak signal is a genuine unwind leak rather than an SA the
            # sweep forgot to remove.
            await sa_flush_range(target_agent, aiohttp_session, dst=PEER,
                                 spis=swept)

        # A refusal, not merely something having gone wrong: a lost reply says
        # the fault landed on the ACK, after the SA was already built, which
        # exercises none of the unwind this is a net for.
        assert refused, (
            f"a sweep of {NSWEEP} refused no install, so nothing on the path "
            f"that builds an SA was faulted. The install makes "
            f"{depth if depth is not None else 'an unmeasured number of'} "
            f"faultable allocations, so either the sweep is shorter than the "
            f"path — raise it with ASK_IPSEC_FAILSLAB_SWEEP — or fail-nth is "
            f"not arming at all. Outcomes: {outcomes}")

        await asyncio.sleep(SA_RELEASE_GRACE_S)
        report = await target_agent.kmemleak(
            aiohttp_session, filter_substrs=IPSEC_LEAK_FILTER)
        leak_count = report["leak_count"]
        assert not leak_count, (
            f"the failslab sweep (1..{NSWEEP}, install depth {depth!r}) leaked "
            f"{leak_count} ipsec-path object(s); {len(refused)} iteration(s) "
            f"were refused.\nOutcomes: {outcomes}\n\n"
            + report.get("report", "")[:4000])
    finally:
        await endpoints_down(target_agent, aiohttp_session,
                             iface=TARGET_WAN_IF, local=LOCAL, peer=PEER)
