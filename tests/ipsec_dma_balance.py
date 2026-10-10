"""H3 regression — the classifier-entry build unwinds cleanly under failslab.

The H3 fix (613efa3) hardened the tail of an SA install, the part that turns a
described SA into a hardware one:

  - the info / sa->ct kzalloc unwind in
    cdx_ipsec_add_classification_table_entry (cdx/cdx_dpa_ipsec.c), whose
    err_ret frees both and rolls back SA_SH_DESC_BUILT, and
  - cdx_ipsec_create_shareddescriptor with its err_unmap_crypto /
    err_unmap_auth DMA-map unwind.

Under xfrmdev_ops that tail is reached by the same message as everything else:
`XFRM_MSG_NEWSA` → xdo_dev_state_add() → cdx_ipsec_sa_add() →
ipsec_install_fp_entry(). What used to need CMD_IPSEC_SA_SET_STATE as a
separate step, and needed the SA's daddr to match a DUT-local address before
the push would even start, now needs only that the SA installs — the local
endpoint being an address on the port is part of installing one.

So this file and ipsec_failslab.py sweep the same call, and the
difference between them is *where in it* the fault lands. The install's
faultable-allocation count is measured first, and this sweep takes the last
stretch of it: the SA cache and the key buffers are allocated early, the
descriptor maps and the table entry late. Sweeping the head instead would
fault the allocator over and over and never reach the maps this test is a
tripwire for.

Oracles: kmemleak filtered to the IPsec path for the info / sa->ct unwind,
and splat_window for a use-after-unmap or an overflow on an unwound buffer.
Only the second covers the DMA maps -- an unbalanced dma_map_single is not an
allocation kmemleak tracks, and the helpers that take one are a macro and a
static inline, so no filter could name them either. So run this under KASAN
(KASAN=1 kas build); without it half the point is gone.
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

LOCAL = "198.18.90.1"
PEER = "198.18.90.2"
PEER_MAC = "02:00:00:00:90:02"
REQID = 0x9001
SPI_BASE = 0xD3A00000
# Distinct SPIs for the two probes. Deleting a state queues the hardware
# teardown on a workqueue, so the same SPI reinstalled a round trip later can
# still collide with an entry that has not gone yet -- which would look like a
# refusal and skip the whole file.
PROBE_SPI = SPI_BASE - 1
DEPTH_SPI = SPI_BASE - 2

# How many of the install's last allocations to fault, one per iteration. The
# last stretch of the measured depth is the netlink reply rather than the
# install, so the window has to be long enough to reach past it -- which is
# what the refusal assertion below checks rather than assumes.
NSWEEP = int(os.environ.get("ASK_IPSEC_DMA_SWEEP", "80"))


async def test_ipsec_entry_build_dma_balance(
    aiohttp_session, target_agent, splat_window,
):
    ifindex = await iface_index(target_agent, aiohttp_session, TARGET_WAN_IF)
    await endpoints_up(target_agent, aiohttp_session, iface=TARGET_WAN_IF,
                       local=LOCAL, peer=PEER, lladdr=PEER_MAC)
    try:
        reason = await sa_install_probe(
            target_agent, aiohttp_session, src=LOCAL, dst=PEER, spi=PROBE_SPI,
            reqid=REQID, ifindex=ifindex)
        if reason:
            pytest.skip(reason)

        depth = await measure_install_allocations(
            target_agent, aiohttp_session, src=LOCAL, dst=PEER, spi=DEPTH_SPI,
            reqid=REQID, ifindex=ifindex)
        if depth is None:
            pytest.skip(
                "could not measure the install's allocation depth (the probe "
                "returned no fail-nth residue), so this sweep cannot aim at "
                "the entry build rather than at the allocator before it")
        # The window: the last NSWEEP allocations of the install, which is
        # where the descriptor maps and the table entry are taken. Never below
        # one, and never longer than the install itself.
        first = max(1, depth - NSWEEP + 1)
        window = list(range(first, depth + 1))
        assert window, f"empty sweep window for depth={depth}"

        await target_agent.kmemleak_clear(aiohttp_session)
        outcomes: list[tuple[int, int | None, bool]] = []
        refused: list[int] = []
        swept = [SPI_BASE + n for n in window]
        try:
            for n in window:
                reply = await sa_add(
                    target_agent, aiohttp_session, src=LOCAL, dst=PEER,
                    spi=SPI_BASE + n, reqid=REQID, ifindex=ifindex,
                    failslab_times=n)
                outcomes.append((n, reply.error, reply.lost))
                if reply.refused:
                    refused.append(n)
                await sa_del(target_agent, aiohttp_session, dst=PEER,
                             spi=SPI_BASE + n)
        finally:
            await sa_flush_range(target_agent, aiohttp_session, dst=PEER,
                                 spis=swept)

        # Non-vacuity, and the strict form of it. A fault on the path that
        # builds the SA comes back as an ACK carrying an error; a fault on
        # the reply's own allocation comes back as no ACK at all, and by then
        # the SA is already installed. Counting the second as progress is how
        # this sweep could sit entirely in the netlink reply, exercise none of
        # the entry build, and still pass -- so only a refusal counts.
        assert refused, (
            f"faulting allocations {window[0]}..{window[-1]} of a "
            f"{depth}-allocation install refused nothing, so nothing on the "
            f"path that builds the SA was faulted and no unwind ran. The "
            f"window is probably entirely inside the netlink reply that "
            f"follows the install: raise ASK_IPSEC_DMA_SWEEP. Outcomes: "
            f"{outcomes}")

        await asyncio.sleep(SA_RELEASE_GRACE_S)
        report = await target_agent.kmemleak(
            aiohttp_session, filter_substrs=IPSEC_LEAK_FILTER)
        leak_count = report["leak_count"]
        assert not leak_count, (
            f"faulting the last {len(window)} of {depth} install allocations "
            f"leaked {leak_count} ipsec object(s) on the unwind; "
            f"{len(refused)} iteration(s) were refused.\n"
            f"Outcomes: {outcomes}\n\n" + report.get("report", "")[:4000])
        # splat_window independently asserts that no KASAN/UBSAN/BUG report
        # fired during the sweep. That is the whole DMA oracle: an unbalanced
        # dma_map_single is not an allocation kmemleak can see, so only KASAN
        # catches a use-after-unmap here.
    finally:
        await endpoints_down(target_agent, aiohttp_session,
                             iface=TARGET_WAN_IF, local=LOCAL, peer=PEER)
