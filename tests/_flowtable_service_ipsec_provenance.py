"""Shared support for flowtable service ipsec provenance."""

from __future__ import annotations

import asyncio

from _flowtable_rig import DPORT, WAN_IP
from _flowtable_service import FIRST
from _flowtable_service_ipsec import INNER, LAN_INNER
from _flowtable_service_ipsec_replay import esp, sa_state
from _topology import LAN_NIC, TARGET_WAN_IF

SPI_B = 0xB2800001
COUNT = 32
KEY = (TARGET_WAN_IF, "17", f"{INNER}:{DPORT}", f"{LAN_INNER}:{FIRST}")


async def inject(r, p, spi, marker, *, sport=DPORT, size=256, seq=100000, source_mac=None,
                 tos=0, outer_tos=0, dropped=False):
    """Send COUNT ESP frames on the SA from the WAN host. `dropped` is for
    frames the SA itself discards after decrypting them, which SEC's per-SA
    counters do not reliably include."""
    from scapy.all import IP, UDP, Ether, Raw, sendp

    inner = bytes(IP(src=INNER, dst=LAN_INNER, tos=tos) /
                  UDP(sport=sport, dport=FIRST) /
                  Raw(marker.ljust(size - 28, b".")))
    frames = [Ether(src=source_mac or r.wan_mac, dst=r.dut_wan_mac) /
              IP(src=WAN_IP, dst=r.ipsec.outer, proto=50, tos=outer_tos) /
              Raw(esp(r.ipsec.transform.algorithms, spi, seq + i, inner))
              for i in range(COUNT)]
    before = await sa_state(r, spi, "in")
    state = await r.state()
    forwarded = await r.software_forwarded()
    await p.rpc("wire_probe", changes={"action": "start", "iface": LAN_NIC,
                                       "marker": marker.hex(), "samples": 2,
                                       "incoming_only": True})
    try:
        await asyncio.to_thread(sendp, frames, iface=r.wan_if, inter=0.01, verbose=False)
        await asyncio.sleep(2)
        wire = await p.rpc("wire_probe", changes={"action": "status"})
    finally:
        await p.rpc("wire_probe", changes={"action": "stop"})
    after = await sa_state(r, spi, "in")
    result = {"wire": wire, "sa_before": before, "sa_after": after,
              "before": state, "after": await r.state(),
              "software_forwarded": await r.software_forwarded() - forwarded}
    r.record(f"provenance-{spi:x}-{sport}-{size}-{seq}", result)
    assert after and before and (dropped or after["packets"] - before["packets"] >= COUNT), result
    return result


async def add_b(r):
    # A separate authenticated SA, with no forward policy authorizing A's tuple.
    await r.ipsec.add(r.target, "state", r.ipsec.state("in", SPI_B),
                     "mode", "tunnel", "reqid", "52801",
                     *r.ipsec.transform.algorithms, "replay-window", "32",
                     "offload", "packet", "dev", TARGET_WAN_IF, "dir", "in")
