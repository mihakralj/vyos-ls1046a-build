"""SA provenance across legitimate inbound and outbound rekey overlap."""
from __future__ import annotations

import asyncio
import secrets

import pytest

from _flowtable_connections import (by_key, healthy, peer)
from _flowtable_rig import (command)
from _flowtable_selective_neighbour import (warm)
from _flowtable_service_ipsec import (INNER, Transform, flows_for, hardware)
from _flowtable_service_ipsec_provenance import (COUNT, KEY, inject)
from _flowtable_service_ipsec_replay import (AEAD, sa_state)


@pytest.mark.parametrize("ipsec_service", [Transform(), AEAD["rfc4106-icv16"]],
                         ids=["cbc", "gcm"], indirect=True)
async def test_ipsec_inbound_rekey_collision(ipsec_service):
    """Both authorized SAs deliver the identical tuple; only the bound SA
    uses hardware, and the other must pass Linux's own policy check."""
    r, flows = ipsec_service, flows_for(ipsec_service)
    async with peer(r, flows, initial_ids=[0, 1, 2, 3], lease=300,
                    listen_addresses=[INNER]) as p:
        await warm(r, p, [0, 1, 2, 3], "overlap-baseline", flows[:4])
        await hardware(r, p, "overlap-baseline-hardware", flows[:4])
        await p.rpc("close", [1, 3])
        old_spi = r.ipsec.active["in"]
        new_spi = 0xB2800000 | secrets.randbits(16)
        await r.ipsec.install("in", new_spi)
        baseline = by_key(await r.state())[KEY]
        marker = b"ASK-SA-OVERLAP-" + secrets.token_bytes(8)
        old = await inject(r, p, old_spi, marker + b"old")
        assert old["wire"]["received"] == COUNT and old["software_forwarded"] == 0, old
        new = await inject(r, p, new_spi, marker + b"new")
        assert new["wire"]["received"] == COUNT, new
        assert new["software_forwarded"] == COUNT, new
        first, last = by_key(new["before"])[KEY], by_key(new["after"])[KEY]
        assert first["cookie"] == last["cookie"] == baseline["cookie"], new
        # FMan counts tuple hits before VLAN validation, including exceptions.
        # Linux's forwarding counter proves all new-SA frames took its check.
        healthy(new["after"])
        # Deleting the bound SA retires its flow, while the overlapping SA
        # remains able to deliver its next packet through the CPU.
        await command(r.target, r.session, "ip", "xfrm", "state", "delete",
                      *r.ipsec.state("in", old_spi))
        await r.wait(lambda s: KEY not in by_key(s))
        alive = await inject(r, p, new_spi, marker + b"survivor", seq=100000 + COUNT)
        assert alive["wire"]["received"] == COUNT, alive
        healthy(alive["after"])


@pytest.mark.parametrize("ipsec_service", [Transform(encap=(4500, 31000)),
                         Transform(AEAD["rfc4106-icv16"].algorithms, (4500, 31000))],
                         ids=["cbc", "gcm"], indirect=True)
async def test_ipsec_outbound_natt_rekey_root(ipsec_service):
    """Old and new SEC descriptors share one UDP output root. Retiring the
    first descriptor must keep that root's tag valid for the surviving SA."""
    r, flows = ipsec_service, flows_for(ipsec_service)
    async with peer(r, flows, initial_ids=[0, 1, 2, 3], lease=300,
                    listen_addresses=[INNER]) as p:
        await warm(r, p, [0, 1, 2, 3], "natt-overlap-baseline", flows[:4])
        await hardware(r, p, "natt-overlap-baseline-hardware", flows[:4])
        old_spi = r.ipsec.active["out"]
        new_spi = await r.ipsec.prepare_peer("out")
        await r.ipsec.install("out", new_spi)
        # Existing flows keep the old SA. A fresh socket resolves the new
        # one, so traffic exercises both descriptors while the root is shared.
        await p.rpc("open", [4])
        await warm(r, p, [0, 1, 2, 3, 4], "natt-overlap-both", flows[:5])
        before = {spi: await sa_state(r, spi) for spi in (old_spi, new_spi)}
        await p.batch([2, 3, 4], count=256, interval=0.03125)
        await asyncio.sleep(1.5)
        after = {spi: await sa_state(r, spi) for spi in (old_spi, new_spi)}
        r.record("natt-overlap-sas", {"before": before, "after": after, "state": await r.state()})
        assert all(after[spi]["packets"] > before[spi]["packets"] for spi in before), (before, after)
        await command(r.target, r.session, "ip", "xfrm", "state", "delete",
                      *r.ipsec.state("out", old_spi))
        # Wait for the old descriptor's deferred queue/tag release too.
        await asyncio.sleep(2)
        await warm(r, p, [0, 1, 2, 3, 4], "natt-overlap-survivor", flows[:5])
        await hardware(r, p, "natt-overlap-survivor-hardware", flows[:5])
        healthy(await r.state())
