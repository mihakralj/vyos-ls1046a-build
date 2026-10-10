"""H2 regression — an IPsec SA's cipher key is zeroed on free.

The H2 fix (613efa3) replaced kfree with kfree_sensitive on cipher_key /
auth_key / split_key in cdx_ipsec_sec_sa_context_free. A regression to plain
kfree leaves the key bytes sitting in the slab where the next consumer reads
them.

The SA arrives through xfrmdev_ops rather than through FCI, which is the only
thing that changed here: `ip xfrm state add ... offload packet` reaches
M_ipsec_sa_cache_create and the same SEC-context allocator, and deleting the
state reaches the same free. The subject is the allocator, not the door.

The oracle is a kernel-side debug probe at /proc/cdx/last_freed_key. The probe
(gated by CDX_DEBUG_KEY_ZEROING, on only in the meta-ask test image) snapshots
the cipher_key buffer immediately after kfree_sensitive returns. On a working
build the snapshot is mostly zero; under a plain-kfree regression the original
0xA5 bytes survive.

Note on assertion shape: the snapshot is NOT guaranteed all-zero even when H2
is correct — SLUB writes a freelist pointer into the freed slot before our
read. The cipher_key buffer is kzalloc(100), i.e. the kmalloc-128 cache, where
the hardened-SLUB free pointer sits at the slot's centre (offset ~64), outside
the sixteen-byte key at offset 0 — but the exact offset is layout-dependent,
so the test checks the cipher-key BYTE PATTERN instead: eight contiguous 0xA5
bytes must not appear and the total 0xA5 count must stay low. Under the fix
neither signal trips; under a plain-kfree regression at least one will, for
any freelist-pointer placement.

The probe's header carries a seq counter, bumped on every observed cipher_key
free. The test baselines seq right before its delete and polls for an advance,
so it cannot false-pass on a snapshot latched by an earlier free in the same
boot. If the sampled allocation happens to be a KFENCE object the probe cannot
read it post-free (captured=false, len=0); the test retries with a fresh SA.

False-pass caveats: active SLUB poisoning (CONFIG_SLUB_DEBUG_ON=y or a
slub_debug=P boot arg — plain CONFIG_SLUB_DEBUG=y is inert) fills freed slabs
with 0x6b, and CONFIG_INIT_ON_FREE_DEFAULT_ON (or init_on_free=1) zeroes them;
either hides a kfree regression from this test. The meta-ask kernel activates
neither. If that changes, this test must be revisited.
"""

from __future__ import annotations

import asyncio

import pytest

from _ipsec_helpers import (
    CIPHER_KEY,
    endpoints_down,
    endpoints_up,
    iface_index,
    sa_add,
    sa_del,
    sa_install_probe,
)
from _topology import TARGET_WAN_IF

# Endpoints from the RFC 2544 benchmarking block (198.18.0.0/15), which no
# real network routes, and distinct from every other IPsec file's so the
# tests can run in any order.
LOCAL = "198.18.87.1"
PEER = "198.18.87.2"
PEER_MAC = "02:00:00:00:87:02"
SPI = 0x12340014
REQID = 0x8701

PROBE_PATH = "/proc/cdx/last_freed_key"

# Deleting a state queues the hardware retirement on a workqueue, and the SEC
# context is then released on a 1-second cdx_timer: the queue into SEC out of
# service, SEC seen idle and SEC's output queue out of service in one fire,
# then a fire later the exception queue and the release. For a tunnel that
# never carried traffic this completes on the second fire; the budget is for a
# loaded bus, not for the expected case.
CAPTURE_POLL_BUDGET_S = 8.0
CAPTURE_POLL_INTERVAL_S = 0.25

# Retries for the case where the sampled allocation turns out to be a KFENCE
# object (captured=false). One retry virtually always suffices; three
# consecutive hits means the probe is lying.
KFENCE_RETRY_ATTEMPTS = 3


def _parse_probe(content_hex: str) -> tuple[dict, bytes]:
    """Decode /proc/cdx/last_freed_key into (header_fields, snapshot_bytes)."""
    raw = bytes.fromhex(content_hex).decode("ascii", errors="replace")
    lines = raw.split("\n")
    if len(lines) < 2:
        raise AssertionError(f"unexpected probe layout: {raw!r}")
    header = dict(p.split("=", 1) for p in lines[0].split())
    snapshot = bytes.fromhex(lines[1].strip()) if lines[1].strip() else b""
    return header, snapshot


async def test_after_free(
    aiohttp_session, target_agent, splat_window,
):
    pre = await target_agent.fs_read(aiohttp_session, PROBE_PATH)
    if pre.get("errno", 0) != 0:
        pytest.skip(
            f"H2 probe absent at {PROBE_PATH} (errno={pre.get('errno')!r}); "
            "kernel build does not define CDX_DEBUG_KEY_ZEROING."
        )

    ifindex = await iface_index(target_agent, aiohttp_session, TARGET_WAN_IF)
    await endpoints_up(target_agent, aiohttp_session, iface=TARGET_WAN_IF,
                       local=LOCAL, peer=PEER, lladdr=PEER_MAC)
    header: dict = {}
    snapshot = b""
    try:
        reason = await sa_install_probe(
            target_agent, aiohttp_session, src=LOCAL, dst=PEER, spi=SPI,
            reqid=REQID, ifindex=ifindex)
        if reason:
            pytest.skip(reason)

        for _attempt in range(KFENCE_RETRY_ATTEMPTS):
            await sa_del(target_agent, aiohttp_session, dst=PEER, spi=SPI)
            # A delete from a previous attempt frees on the same 1-second
            # timer and would advance seq after the baseline below, latching a
            # snapshot that is not ours. Let it land first.
            await asyncio.sleep(1.5)

            reply = await sa_add(
                target_agent, aiohttp_session, src=LOCAL, dst=PEER, spi=SPI,
                reqid=REQID, ifindex=ifindex, cipher_key=CIPHER_KEY)
            assert reply.ok, (
                f"the SA the probe needs was refused: {reply!r}. Without an "
                f"installed SA there is no cipher key to free, so the test "
                f"cannot tell H2-OK from H2-broken.")

            # Baseline seq immediately before the delete, so an advance can
            # only come from a free triggered after this point. Tests run
            # sequentially, so the advancing free is ours.
            base = await target_agent.fs_read(aiohttp_session, PROBE_PATH)
            assert base.get("errno", 0) == 0, (
                f"probe read failed pre-delete: {base!r}")
            base_header, _ = _parse_probe(base["content_hex"])
            base_seq = int(base_header.get("seq", 0))

            await sa_del(target_agent, aiohttp_session, dst=PEER, spi=SPI)

            deadline = asyncio.get_event_loop().time() + CAPTURE_POLL_BUDGET_S
            header = {}
            snapshot = b""
            while asyncio.get_event_loop().time() < deadline:
                post = await target_agent.fs_read(aiohttp_session, PROBE_PATH)
                assert post.get("errno", 0) == 0, (
                    f"probe read failed mid-test: {post!r}")
                header, snapshot = _parse_probe(post["content_hex"])
                if int(header.get("seq", 0)) > base_seq:
                    break
                await asyncio.sleep(CAPTURE_POLL_INTERVAL_S)

            assert int(header.get("seq", 0)) > base_seq, (
                f"the probe never observed a cipher_key free in "
                f"{CAPTURE_POLL_BUDGET_S}s after the state was deleted (seq "
                f"stuck at {base_seq}) — final header={header!r}. Either "
                f"cdx_ipsec_sec_sa_context_free was not reached (the retirement "
                f"work never ran, or the SA outlived its state) or the release "
                f"timer did not fire. Check dmesg for cdx errors.")

            if header.get("captured") == "true":
                break
            # seq advanced but captured=false: the allocation was
            # KFENCE-sampled and the probe skipped the unreadable page. Retry
            # with a fresh SA, which is a new allocation.
        else:
            pytest.fail(
                f"{KFENCE_RETRY_ATTEMPTS} consecutive cipher_key allocations "
                f"reported KFENCE-sampled (captured=false) — statistically "
                f"implausible; probe state suspect: {header!r}")
    finally:
        await sa_del(target_agent, aiohttp_session, dst=PEER, spi=SPI)
        await endpoints_down(target_agent, aiohttp_session,
                             iface=TARGET_WAN_IF, local=LOCAL, peer=PEER)

    n = int(header["len"])
    assert n > 0, f"captured len=0 — probe state corrupt: {header!r}"
    assert len(snapshot) == n, (
        f"body length {len(snapshot)} != header len={n}: {header!r}")

    contiguous_a5_8 = b"\xA5" * 8 in snapshot
    a5_count = snapshot.count(0xA5)

    assert not contiguous_a5_8 and a5_count < 8, (
        f"H2 REGRESSION: cipher key bytes survived kfree_sensitive. The key "
        f"was sixteen 0xA5 bytes; after a working kfree_sensitive the slab "
        f"should be mostly zero, with at most an eight-byte SLUB freelist "
        f"pointer. Found contiguous-8x0xA5={contiguous_a5_8}, total 0xA5 byte "
        f"count={a5_count}. This usually means cdx_ipsec_sec_sa_context_free "
        f"was reverted to plain kfree on cipher_key — re-check the H2 fix in "
        f"cdx/cdx_dpa_ipsec.c. Snapshot ({n} B): {snapshot.hex()}")
