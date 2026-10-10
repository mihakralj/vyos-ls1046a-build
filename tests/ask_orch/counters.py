"""Software-path counters for the ASK SDK DPAA driver.

Netdev statistics include CDX's hardware counters. The driver's private
ethtool counters come from its per-CPU software statistics instead.
"""

import re


async def kernel_rx_packets(target_agent, session, iface: str) -> int:
    """Read SDK DPAA software RX packets; fail if that counter is unavailable.

    This can omit software flowtable forwarding: without GRO, an ingress hook
    consuming the skb can return before the driver increments RX. Use software
    TX counters and delivery/hardware evidence to distinguish that path.
    """
    return await _kernel_packets(target_agent, session, iface, "rx")


async def kernel_tx_packets(target_agent, session, iface: str) -> int:
    """Read software TX enqueues, including software flowtable forwarding.

    Successful endpoint delivery is still required: enqueue is not delivery.
    """
    return await _kernel_packets(target_agent, session, iface, "tx")


async def _kernel_packets(target_agent, session, iface: str, direction: str) -> int:
    r = await target_agent.exec_cmd(session, ["ethtool", "-S", iface])
    assert r.get("rc") == 0, f"ethtool -S {iface} failed: {r}"
    values = re.findall(
        rf"^\s*{direction} packets \[TOTAL\]:\s*(\d+)\s*$",
        r.get("stdout", ""), re.M,
    )
    assert len(values) == 1, (
        f"expected one SDK DPAA '{direction} packets [TOTAL]' counter on {iface}; "
        f"cannot determine software {direction.upper()} from: {r.get('stdout', '')!r}"
    )
    return int(values[0])
