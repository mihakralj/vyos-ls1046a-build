"""Sequence numbers and anti-replay on offloaded SAs, across ciphers and feeders.

An SA's SEC queue has two feeders in each direction. Outbound, the classifier
enqueues offloaded flows and the CPU everything else. Inbound, the classifier
steers whole ESP frames to SEC by SPI, while an outer IP fragment cannot match
that entry: the CPU reassembles it, and xfrm_input() hands it to the same queue
(the software SEC submit, `tx todec`). Whichever feeder a frame takes, one
shared descriptor holds the SA's sequence state, so a sequence number seen
through one feeder must be refused through the other.

AEAD SAs take a different sharing policy from CBC+HMAC (SERIAL without
SAVECTX, cdx_ipsec_sh_desc_hdr_flags()), and each ICV length is its own
descriptor, so the traffic and shared-sequence proofs are repeated for them.

The replayed ESP is built on this host with the fixture's keys. The suite's
scapy has no cryptography backend, so the cipher runs in this host's kernel
through AF_ALG: the same implementation the peer's own SAs use.
"""
from __future__ import annotations

from _flowtable_service_ipsec_replay import GMAC_LOCAL, GMAC_PEER, LIMIT_LOCAL, LIMIT_PEER, TRUNC_LOCAL, TRUNC_PEER

from _flowtable_service_ipsec_replay import AEAD, CBC, GMAC, GMAC_REQID, GMAC_STATES, Inbound, LIVE_BLAST_SECONDS, LIVE_ROUNDS, READD_TIMEOUT_MS, REPLAY_CASES, SEC_REFUSAL_MIBS, SHA256_96, SHA256_DEFAULT, TRUNC_REQID, TRUNC_STATES, WIDTHS, aead_label, arrivals, delivered, dut_counters, getsa, lan_listener, offloaded, peer_errors, readd_outbound, sa_state, send_out, whole, xfrm_mib

import asyncio
from collections import Counter
from pathlib import Path
import re
import secrets
import time

import pytest

from _ipsec_helpers import endpoints_down, endpoints_up, iface_index, sa_add, sa_replay_state
from _topology import TARGET_WAN_IF
from _flowtable_connections import (peer)
from _flowtable_rig import (WAN_IP, command, read)
from _flowtable_selective_neighbour import (warm)
from _flowtable_service_ipsec import (INNER, REQIDS, flows_for, hardware, negative, plaintext_probe)
from _flowtable_service_ipsec import (ipsec_shared_sequence as shared_sequence)
from _ipsec_inbound_flow_offload import (AUTH, CIPHER)


@pytest.mark.parametrize("ipsec_service", list(AEAD.values()), ids=list(AEAD), indirect=True)
async def test_aead_traffic(ipsec_service):
    """AEAD SAs carry the tunnel in hardware both ways, as CBC+HMAC does, and
    the peer authenticates every frame SEC produced."""
    r, flows = ipsec_service, flows_for(ipsec_service)
    label = aead_label(r)
    await offloaded(r)
    before = xfrm_mib(Path("/proc/net/xfrm_stat").read_text())
    async with peer(r, flows, initial_ids=[0, 1, 2, 3, 5, 6], lease=400, listen_addresses=[INNER]) as p:
        await warm(r, p, [0, 1, 2, 3], label, flows[:4])
        await hardware(r, p, label + "-hardware", flows[:4])
        await plaintext_probe(r, p, label + "-plaintext")
        await negative(r, p)
        # A connection opened while the tunnel is already in hardware.
        await p.rpc("open", [4])
        await warm(r, p, [0, 1, 2, 3, 4], label + "-new", flows[:5])
        await hardware(r, p, label + "-new-hardware", flows[:5])
    refused = peer_errors(before)
    r.record(label + "-peer-errors", refused)
    assert not refused, f"the peer refused frames SEC encrypted: {refused}"


@pytest.mark.parametrize("ipsec_service", [AEAD["rfc4106-icv16"]], ids=["rfc4106-icv16"], indirect=True)
async def test_aead_shared_sequence(ipsec_service):
    """Both SEC feeders of one GCM SA draw from one sequence counter.

    GCM runs its shared descriptor SERIAL without SAVECTX, unlike CBC+HMAC,
    and it is where the reuse this guards against was first measured, as
    replay-window rejections at the peer."""
    await offloaded(ipsec_service)
    await shared_sequence(ipsec_service)


async def test_window(ipsec_service):
    """Inbound anti-replay in hardware, as the SA's window configures it.

    Whole frames reach SEC through the classifier and fragments through the
    CPU, and the two share one window. Every datagram is delivered to the LAN
    end exactly as often as its window allows, and every frame the window
    refuses is counted: once in the FMan microcode's refusal total, which
    /proc/cdx_flowtable carries, and once across the /proc/net/xfrm_stat
    counters the adapter folds the microcode's non-fault classes into, which
    is where the microcode files replays and late frames. Per SA it is counted
    nowhere; SEC keeps no such count."""
    r = ipsec_service
    marker = secrets.token_bytes(16)
    records, sas = [], []
    async with lan_listener(r, marker):
        initial = await dut_counters(r)
        rejected, accepted = 0, Counter()
        for name, algorithms, window, phases in REPLAY_CASES:
            sa = Inbound(r, marker, algorithms, window)
            await sa.install()
            sas.append(sa)
            accepted[sa.spi] = 0
            for step in phases:
                before, seen = await dut_counters(r), await delivered(r)
                await sa.send(step["frames"])
                got = await arrivals(r, sa.spi, seen, sum(step["delivered"].values()))
                after = await dut_counters(r)
                record = {"case": name, "phase": step["name"], "delivered": sorted(got.elements()),
                          "expected": sorted(step["delivered"].elements()),
                          "counters": {key: after[key] - before[key] for key in after}}
                records.append(record)
                r.record("ipsec-replay-window", records)
                assert got == step["delivered"], record
                assert (record["counters"]["todec"], record["counters"]["reasm"]) == (step["cpu"], step["cpu"]), (
                    "whole frames must reach SEC through the classifier and fragments through the CPU", record)
                rejected += step["rejected"]
                accepted[sa.spi] += sum(got.values())
        # The accounting pass publishes SEC's figures, and reads the
        # microcode's refusal count, once a second.
        await asyncio.sleep(1.5)
        figures = {sa.spi: await sa_state(r, sa.spi, "in") for sa in sas}
        for _ in range(8):
            final = await dut_counters(r)
            counted = {key: final[key] - initial[key] for key in ("refusals", "ipsec_sec_refused")}
            if min(counted.values()) >= rejected:
                break
            await asyncio.sleep(0.5)
        errors = [line for line in (await command(r.target, r.session, "dmesg"))["stdout"].splitlines()
                  if "IPsec SEC error" in line or "SEC could not process" in line][-64:]
        r.record("ipsec-replay-window-accounting", {
            "refused": rejected, "counted": counted, "sec_errors": errors,
            "classes": {key: final[key] - initial[key] for key in final
                        if key.startswith("ipsec_sec_refused_") or key.startswith("Xfrm")},
            "sas": {f"{spi:#x}": {"accepted": accepted[spi], **figures[spi]} for spi in accepted}})
        # SEC counts the frames it decrypted, whichever feeder brought them,
        # and not the ones its window refused: a replay must not age an SA.
        assert {spi: figures[spi]["packets"] for spi in accepted} == dict(accepted), (figures, accepted)
        # Every frame SEC refused, through either feeder, is counted once:
        # by the microcode, whose total /proc/cdx_flowtable carries, and
        # across the xfrm_stat counters, since the microcode files replays
        # and late frames in a class that is folded there (measured: its
        # catch-all, other_errs, never a fault class).
        assert counted["ipsec_sec_refused"] == rejected, (
            f"SEC refused {rejected} replayed or late frames and the microcode's count moved by "
            f"{counted['ipsec_sec_refused']}")
        assert counted["refusals"] == rejected, (
            f"SEC refused {rejected} replayed or late frames and {'+'.join(SEC_REFUSAL_MIBS)} moved by "
            f"{counted['refusals']}; a hardware refusal must be counted where Linux counts its own")


async def test_ipsec_replay_state_read_live(ipsec_service):
    """What xfrm hands a keying daemon of an offloaded SA's sequence space is
    SEC's at that moment, not the accounting pass's of up to a second before.

    strongSwan reads an SA with GETSA and GETAE and deletes it straight after
    when it moves it to a new address, and re-adds it from what it read. Read
    well inside a second of a burst's last frame, both must already cover it:
    the inbound top and bitmap every frame SEC took, the outbound number every
    one the peer received. The inbound rounds are several, so the pass
    happening to run between a burst and its read cannot pass them all."""
    r = ipsec_service
    inbound = Inbound(r, secrets.token_bytes(16), CBC, 32)
    await inbound.install()
    identity = r.ipsec.state("in", inbound.spi)
    records = []
    for n in range(LIVE_ROUNDS):
        await asyncio.sleep(1.1)
        base = 1000 * n + 1
        # A number SEC never sees in the middle of the burst, so the bitmap
        # has something to be wrong about.
        numbers = [s for s in range(base, base + 40) if s != base + 30]
        await inbound.send(whole(*numbers))
        sent = time.monotonic()
        await asyncio.sleep(0.05)
        read_sa = await getsa(r.target, r, identity)
        read_ae = await sa_replay_state(r.target, r.session, dst=r.ipsec.outer, spi=inbound.spi)
        record = {"round": n, "top": max(numbers), "getsa": read_sa, "getae": read_ae,
                  "read_after": time.monotonic() - sent}
        records.append(record)
        r.record("ipsec-replay-read-live-in", records)
        assert record["read_after"] < 1.0, record
        for read in (read_sa, read_ae):
            assert read["seq"] == max(numbers), record
            for k in range(32):
                assert bool(read["bitmap"] >> k & 1) == (max(numbers) - k in numbers), (k, record)

    out = r.ipsec.state("out", r.ipsec.active["out"])
    records = []
    for n in range(2):
        await send_out(r, seconds=LIVE_BLAST_SECONDS)
        # The device's oseq is published by a periodic accounting pass, so it
        # can trail the peer's received count by up to a pass right after a
        # blast; wait the pass out before comparing. A genuine shortfall -- the
        # device never reaching what the peer took -- still fails, after the
        # wait, with the same record.
        for _ in range(100):
            read_sa = await getsa(r.target, r, out)
            read_ae = await sa_replay_state(r.target, r.session, dst=WAN_IP, spi=r.ipsec.active["out"])
            # The peer's own SA: the highest number it has taken from the DUT.
            received = await getsa(r.ipsec.wan, r, out)
            if read_sa["oseq"] >= received["seq"] and read_ae["oseq"] >= received["seq"]:
                break
            await asyncio.sleep(0.1)
        record = {"round": n, "getsa": read_sa, "getae": read_ae, "peer": received}
        records.append(record)
        r.record("ipsec-replay-read-live-out", records)
        assert received["packets"] > 0, record
        assert read_sa["oseq"] >= received["seq"] and read_ae["oseq"] >= received["seq"], record


async def test_ipsec_readd_carries_replay_state(ipsec_service):
    """strongSwan moving a child SA to a new address, a MOBIKE update or a
    NAT's new mapping: GETSA and GETAE, DELSA, then NEWSA with the same SPI
    and keys and the replay state it read.

    Frames go on arriving and leaving between the read and the delete, and
    SEC goes on taking and numbering them until the old SA is out of the
    hardware. The new SA must still refuse every frame the old one accepted,
    and number past every frame the old one sent: the peer keeps its own SA,
    and drops the new one's frames as replays until they pass. Both hold
    only because the re-add is carried past where SEC left the old SA, which
    no reading taken before the delete can know."""
    r = ipsec_service
    marker = secrets.token_bytes(16)
    ifindex = await iface_index(r.target, r.session, TARGET_WAN_IF)
    keys = {"cipher_key": bytes.fromhex(CIPHER[2:]), "auth_key": bytes.fromhex(AUTH[2:])}
    inbound = Inbound(r, marker, CBC, 32)
    identity = r.ipsec.state("in", inbound.spi)
    async with lan_listener(r, marker):
        await inbound.install()
        seen = await delivered(r)
        await inbound.send(whole(*range(1, 21)))
        assert sum((await arrivals(r, inbound.spi, seen, 20)).values()) == 20
        read_sa = await getsa(r.target, r, identity)
        read_ae = await sa_replay_state(r.target, r.session, dst=r.ipsec.outer, spi=inbound.spi)
        # Taken after the read and before the delete, which the reading
        # cannot know of.
        seen = await delivered(r)
        await inbound.send(whole(*range(21, 25)))
        assert sum((await arrivals(r, inbound.spi, seen, 4)).values()) == 4
        await command(r.target, r.session, "ip", "xfrm", "state", "delete", *identity)
        # strongSwan takes GETSA's replay state where it has one.
        reply = await sa_add(r.target, r.session, src=WAN_IP, dst=r.ipsec.outer, spi=inbound.spi,
                             reqid=int(REQIDS["in"]), ifindex=ifindex, inbound=True, **keys,
                             replay_window=32, replay=(0, read_sa["seq"], read_sa["bitmap"]),
                             timeout_ms=READD_TIMEOUT_MS)
        assert reply.ok, reply.raw
        readded = await getsa(r.target, r, identity)
        before, seen = await dut_counters(r), await delivered(r)
        # One the reading had taken, one only SEC had: both refused.
        await inbound.send(whole(20, 23))
        replayed = await arrivals(r, inbound.spi, seen, 0)
        # And a number neither had seen is taken: the new SA works.
        await inbound.send(whole(25))
        fresh = await arrivals(r, inbound.spi, seen, 1)
        # The pass reads the microcode's count once a second.
        for _ in range(8):
            await asyncio.sleep(0.5)
            after = await dut_counters(r)
            if after["ipsec_sec_refused"] - before["ipsec_sec_refused"] >= 2:
                break
        record = {"read": {"getsa": read_sa, "getae": read_ae}, "readded": readded,
                  "replayed": sorted(replayed.elements()), "fresh": sorted(fresh.elements()),
                  "refused": after["ipsec_sec_refused"] - before["ipsec_sec_refused"]}
        r.record("ipsec-readd-in", record)
        assert (read_sa["seq"], read_ae["seq"]) == (20, 20), record
        assert not replayed, record
        assert fresh == Counter({25: 1}), record
        assert record["refused"] == 2, record

    # Outbound, first on a fresh SA that has sent nothing when it is read,
    # so the reading carries no rate to go ahead by; then on one that has
    # been busy until just before.
    spi = await r.ipsec.prepare_peer("out")
    await r.ipsec.remove("out")
    await r.ipsec.install("out", spi)
    await readd_outbound(r, ifindex, keys, "idle")
    await send_out(r, seconds=2)
    await readd_outbound(r, ifindex, keys, "busy")


async def test_ipsec_replay_window_exact(aiohttp_session, target_agent, splat_window):
    """An inbound SA is offloaded only at a window SEC keeps exactly as wide.

    Linux drops a number replay_window or more behind the highest, so a width
    carried on SEC's next wider window took late frames the state's own check
    refuses. Every other width is refused with the reason, and nothing is
    installed: packet offload has no software fallback."""
    await endpoints_up(target_agent, aiohttp_session, iface=TARGET_WAN_IF, local=LIMIT_LOCAL,
                       peer=LIMIT_PEER, lladdr="02:00:00:00:05:02")
    results = []
    identities = []
    try:
        for n, (window, mode, refusal) in enumerate(WIDTHS):
            identity = ["src", LIMIT_PEER, "dst", LIMIT_LOCAL, "proto", "esp", "spi", hex(0x4D6F0100 + n)]
            identities.append(identity)
            added = await command(target_agent, aiohttp_session, "ip", "xfrm", "state", "add", *identity,
                                  "mode", mode, "reqid", "49305", *CBC, "replay-window", str(window),
                                  "offload", "packet", "dev", TARGET_WAN_IF, "dir", "in", check=False)
            shown = await command(target_agent, aiohttp_session, "ip", "xfrm", "state", "get", *identity,
                                  check=False)
            result = {"window": window, "mode": mode, "add": added, "get": shown}
            results.append(result)
            if added["rc"] == 0:
                await command(target_agent, aiohttp_session, "ip", "xfrm", "state", "delete", *identity)
            if refusal is None:
                assert added["rc"] == 0, result
                # Up to 32 in the legacy replay state, wider in the ESN-format
                # one, which ip shows as replay_window.
                assert re.search(rf"replay[-_]window {window}\b", shown["stdout"]), result
                assert re.search(rf"crypto offload parameters: dev {TARGET_WAN_IF} dir in mode packet",
                                 shown["stdout"]), result
            else:
                assert added["rc"] != 0, result
                assert refusal in added["stderr"], result
                assert shown["rc"] != 0, result
    finally:
        for identity in identities:
            await command(target_agent, aiohttp_session, "ip", "xfrm", "state", "delete", *identity,
                          check=False)
        await endpoints_down(target_agent, aiohttp_session, iface=TARGET_WAN_IF, local=LIMIT_LOCAL,
                             peer=LIMIT_PEER)


async def test_ipsec_gmac_refused(aiohttp_session, target_agent, splat_window):
    """AES-GMAC is refused for packet offload in both directions, with the
    reason, and the same state installs in software.

    SEC runs GMAC as GCM with the payload left unencrypted, so its ICV covers
    the ESP header and payload but not the IV, which RFC 4543 and every
    software peer authenticate. Offloaded, every frame the SA sent failed the
    peer's check and every compliant frame it received was dropped."""
    await endpoints_up(target_agent, aiohttp_session, iface=TARGET_WAN_IF, local=GMAC_LOCAL,
                       peer=GMAC_PEER, lladdr="02:00:00:00:08:02")
    try:
        for direction, identity in GMAC_STATES.items():
            added = await command(target_agent, aiohttp_session, "ip", "xfrm", "state", "add", *identity,
                                  "mode", "tunnel", "reqid", GMAC_REQID, *GMAC,
                                  "offload", "packet", "dev", TARGET_WAN_IF, "dir", direction, check=False)
            shown = await command(target_agent, aiohttp_session, "ip", "xfrm", "state", "get", *identity,
                                  check=False)
            result = {"direction": direction, "add": added, "get": shown}
            assert added["rc"] != 0, result
            assert "cdx: SEC's AES-GMAC leaves the IV out of the ICV" in added["stderr"], result
            # Packet offload has no software fallback: nothing was installed.
            assert shown["rc"] != 0, result
        # Asked for without offload, it is software's, which follows the RFC.
        identity = GMAC_STATES["out"]
        added = await command(target_agent, aiohttp_session, "ip", "xfrm", "state", "add", *identity,
                              "mode", "tunnel", "reqid", GMAC_REQID, *GMAC, check=False)
        shown = await command(target_agent, aiohttp_session, "ip", "xfrm", "state", "get", *identity,
                              check=False)
        result = {"add": added, "get": shown}
        assert added["rc"] == 0 and shown["rc"] == 0, result
        assert GMAC[1] in shown["stdout"], result
        assert "crypto offload parameters" not in shown["stdout"], result
    finally:
        for identity in GMAC_STATES.values():
            await command(target_agent, aiohttp_session, "ip", "xfrm", "state", "delete", *identity,
                          check=False)
        await endpoints_down(target_agent, aiohttp_session, iface=TARGET_WAN_IF, local=GMAC_LOCAL,
                             peer=GMAC_PEER)


async def test_ipsec_auth_truncation_refused(aiohttp_session, target_agent, splat_window):
    """An HMAC truncated to a length SEC has no operation for is refused for
    packet offload in both directions, with the reason, and the same state
    installs in software.

    SEC fixes the ICV in its protocol operation, and SHA-256 is only ever 128
    bits there. Offloaded at 96, every frame the SA sent ended in a 16-byte
    ICV where the peer expected 12 and failed its check, and every frame it
    received failed SEC's."""
    await endpoints_up(target_agent, aiohttp_session, iface=TARGET_WAN_IF, local=TRUNC_LOCAL,
                       peer=TRUNC_PEER, lladdr="02:00:00:00:09:02")
    attempts = [("out", SHA256_96), ("in", SHA256_96), ("out", SHA256_DEFAULT)]
    try:
        for direction, algorithms in attempts:
            identity = TRUNC_STATES[direction]
            added = await command(target_agent, aiohttp_session, "ip", "xfrm", "state", "add", *identity,
                                  "mode", "tunnel", "reqid", TRUNC_REQID, *algorithms,
                                  "offload", "packet", "dev", TARGET_WAN_IF, "dir", direction, check=False)
            shown = await command(target_agent, aiohttp_session, "ip", "xfrm", "state", "get", *identity,
                                  check=False)
            result = {"direction": direction, "algorithms": algorithms, "add": added, "get": shown}
            assert added["rc"] != 0, result
            assert "cdx: SEC cannot produce this authenticator at this ICV length" in added["stderr"], result
            # Packet offload has no software fallback: nothing was installed.
            assert shown["rc"] != 0, result
        # Asked for without offload, it is software's.
        identity = TRUNC_STATES["out"]
        added = await command(target_agent, aiohttp_session, "ip", "xfrm", "state", "add", *identity,
                              "mode", "tunnel", "reqid", TRUNC_REQID, *SHA256_96, check=False)
        shown = await command(target_agent, aiohttp_session, "ip", "xfrm", "state", "get", *identity,
                              check=False)
        result = {"add": added, "get": shown}
        assert added["rc"] == 0 and shown["rc"] == 0, result
        assert re.search(r"auth-trunc hmac\(sha256\) \S+ 96$", shown["stdout"], re.M), result
        assert "crypto offload parameters" not in shown["stdout"], result
    finally:
        for identity in TRUNC_STATES.values():
            await command(target_agent, aiohttp_session, "ip", "xfrm", "state", "delete", *identity,
                          check=False)
        await endpoints_down(target_agent, aiohttp_session, iface=TARGET_WAN_IF, local=TRUNC_LOCAL,
                             peer=TRUNC_PEER)
