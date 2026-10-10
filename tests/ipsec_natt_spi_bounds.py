"""H5 regression — the NAT-T per-flow SPI array bound is `>=`, not `>`.

The H5 fix (613efa3) flipped the bounds check in
cdx_ipsec_process_udp_classification_table_entry (cdx/cdx_dpa_ipsec.c) from
`> MAX_SPI_PER_FLOW` to `>= MAX_SPI_PER_FLOW`, so an index equal to
MAX_SPI_PER_FLOW — one past the end of spi_param[16] — is refused instead of
written out of bounds.

Reaching that check used to be the hard part, and under xfrmdev_ops it stops
being hard at all. The bound lives inside `if (natt_sa && natt_sa->ct)`,
entered only when a *prior* same-flow NAT-T SA still holds a populated ct. An
FCI-installed SA could not: production resolved an SA's kernel state by handle
and a synthetic SA had none, so its push failed and its ct was torn down
again — which is why this test used to need the CDX_DEBUG_IPSEC_TEST_XFRM
by-SPI fallback to fabricate one. An offloaded SA is built *from* a real
xfrm_state, and cdx_ipsec_sa_add() installs its entry without looking any
kernel state up again. The ct survives because nothing can fail that lookup,
so the array accumulates on its own and the test hook is not part of this any
more.

Fill semantics, one SPI per SA:
  - the first SA of a flow finds no twin, so the entry is built and takes
    slot 0;
  - each later same-flow SA finds the populated ct, and the lowest free slot;
  - when the array is full the index comes back as MAX and the check refuses
    it, with no write past the end.
So MAX distinct same-flow SPIs install, and the next one is refused.

Same flow means the same family, addresses and UDP port pair — every SA here
shares all of those and differs only in SPI, which is exactly
M_ipsec_get_matched_natt_tunnel's predicate.

Run under KASAN (KASAN=1 kas build): a regressed `>` would let the index
through and write spi_param[MAX] out of bounds, which the refusal oracle alone
would miss if the write happened to succeed. splat_window is that oracle.
"""

from __future__ import annotations

import pytest
import pytest_asyncio

from _ipsec_helpers import (
    MAX_SPI_PER_FLOW,
    endpoints_down,
    endpoints_up,
    iface_index,
    sa_add,
    sa_del,
    sa_flush_range,
    sa_install_probe,
)
from _topology import TARGET_WAN_IF

# These SAs are inbound: the array being filled belongs to the entry that
# classifies arriving UDP-encapsulated ESP, so the SA's destination is the
# local endpoint and its source is the peer.
LOCAL = "198.18.89.1"
PEER = "198.18.89.2"
PEER_MAC = "02:00:00:00:89:02"
REQID = 0x8901
SPI_BASE = 0x0B000000
PROBE_SPI = SPI_BASE + 0x100

# One fixed UDP pair, shared by every SA, which is what makes them one flow.
NATT = (4500, 4500)


def _spi(index: int) -> int:
    return SPI_BASE + index


@pytest_asyncio.fixture
async def natt_flow(aiohttp_session, target_agent):
    """Fill one flow's SPI array to MAX_SPI_PER_FLOW and yield what it took.

    Yields (ifindex, filled) where `filled` is the list of SPIs the hardware
    accepted. Skips rather than fails when the DUT cannot host this at all —
    no address on the port, or no offload on this boot.
    """
    ifindex = await iface_index(target_agent, aiohttp_session, TARGET_WAN_IF)
    await endpoints_up(target_agent, aiohttp_session, iface=TARGET_WAN_IF,
                       local=LOCAL, peer=PEER, lladdr=PEER_MAC)
    # Every SPI this fixture or its tests may touch, including the one past
    # the end that the overflow case installs.
    touched = [PROBE_SPI] + [_spi(i) for i in range(MAX_SPI_PER_FLOW + 2)]
    try:
        await sa_flush_range(target_agent, aiohttp_session, dst=LOCAL,
                             spis=touched)
        reason = await sa_install_probe(
            target_agent, aiohttp_session, src=PEER, dst=LOCAL, spi=PROBE_SPI,
            reqid=REQID, ifindex=ifindex, inbound=True, natt=NATT)
        if reason:
            pytest.skip(reason)

        filled: list[int] = []
        refusals: list[tuple[int, object]] = []
        for index in range(MAX_SPI_PER_FLOW):
            reply = await sa_add(
                target_agent, aiohttp_session, src=PEER, dst=LOCAL,
                spi=_spi(index), reqid=REQID, ifindex=ifindex, inbound=True,
                natt=NATT)
            if reply.ok:
                filled.append(_spi(index))
            else:
                refusals.append((index, reply))
        assert not refusals, (
            f"a same-flow NAT-T SA was refused before the array was full: "
            f"{refusals!r}. The bound is {MAX_SPI_PER_FLOW}; a refusal at "
            f"slot {refusals[0][0]} means it is too tight, or the install "
            f"failed for a reason that has nothing to do with the array.")
        yield ifindex, filled
    finally:
        await sa_flush_range(target_agent, aiohttp_session, dst=LOCAL,
                             spis=touched)
        await endpoints_down(target_agent, aiohttp_session,
                             iface=TARGET_WAN_IF, local=LOCAL, peer=PEER)


async def test_at_max(natt_flow):
    """Exactly MAX_SPI_PER_FLOW same-flow SPIs install cleanly."""
    _ifindex, filled = natt_flow
    assert len(filled) == MAX_SPI_PER_FLOW, (
        f"the flow took {len(filled)} SPIs, expected {MAX_SPI_PER_FLOW}")


async def test_over_max(natt_flow, aiohttp_session,
                                       target_agent, splat_window):
    """With the array full, the next same-flow SA is refused rather than
    written past the end."""
    ifindex, filled = natt_flow
    assert len(filled) == MAX_SPI_PER_FLOW, (
        f"the flow is not full ({len(filled)}/{MAX_SPI_PER_FLOW}), so the "
        f"boundary cannot be reached — see test_at_max")

    over = _spi(MAX_SPI_PER_FLOW)
    reply = await sa_add(
        target_agent, aiohttp_session, src=PEER, dst=LOCAL, spi=over,
        reqid=REQID, ifindex=ifindex, inbound=True, natt=NATT)
    try:
        assert not reply.ok, (
            f"the SA past the array's end was accepted (error={reply.error!r}). "
            f"A {MAX_SPI_PER_FLOW + 1}th same-flow SPI found a slot, which "
            f"means the bound regressed from `>=` to `>` and spi_param"
            f"[{MAX_SPI_PER_FLOW}] was written out of bounds.")
    finally:
        await sa_del(target_agent, aiohttp_session, dst=LOCAL, spi=over)
    # splat_window independently asserts that no KASAN/UBSAN/BUG report fired
    # while the over-max install ran.
