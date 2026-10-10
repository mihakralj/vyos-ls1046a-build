"""Exact counts of a multicast test stream's frames that reach the DUT's CPU.

A frame the classifier replicates is never enqueued to the host, so whether a
stream is in hardware is the count of its frames the CPU saw. The port's own
receive counter cannot answer that on this rig: it also moves for everything
else on the segment, and the office LAN on the WAN side adds queries, reports
and discovery traffic in bursts larger than a hardware case's whole budget.
"""

from __future__ import annotations
from contextlib import asynccontextmanager
import json
from _topology import TARGET_LAN_IF, TARGET_WAN_IF
from _flowtable_rig import (command)

CPU_TABLE = "ask_mc_cpu"


@asynccontextmanager
async def stream_cpu_counters(target, session, ports: tuple[int, ...]):
    """Count the test streams' frames that reach the CPU, per port.

    A netdev ingress chain on each port counts UDP to the streams' own ports.
    The kernel has taken a VLAN tag off before this hook, and a frame the
    classifier replicates never gets here, so it counts the streams' CPU
    frames and nothing else; the port's own receive counter also moves for
    everything else on the segment, and a one-second querier draws a burst
    of reports from every host on it.

    Installed once, before a case learns anything, and only read around a
    window (cpu_frames()): the routed learner takes any ruleset commit as
    unconfirming every routed group, so a table written inside a window
    would itself send a routed stream to the CPU."""
    await command(target, session, "nft", "delete", "table", "netdev", CPU_TABLE, check=False)
    await command(target, session, "nft", "add", "table", "netdev", CPU_TABLE)
    try:
        for dev in (TARGET_LAN_IF, TARGET_WAN_IF):
            await command(target, session, "nft", "add", "chain", "netdev", CPU_TABLE, dev, "{", "type",
                          "filter", "hook", "ingress", "device", dev, "priority", "-500", ";",
                          "policy", "accept", ";", "}")
            await command(target, session, "nft", "add", "rule", "netdev", CPU_TABLE, dev, "udp", "dport",
                          "{", ", ".join(str(p) for p in ports), "}", "counter")
        yield
    finally:
        await command(target, session, "nft", "delete", "table", "netdev", CPU_TABLE, check=False)


async def cpu_frames(target, session, ingress: str) -> int:
    """The stream frames that have reached the CPU on `ingress` so far."""
    listed = json.loads((await command(target, session, "nft", "-j", "list", "chain", "netdev",
                                       CPU_TABLE, ingress))["stdout"])
    return sum(e["counter"]["packets"] for item in listed["nftables"] if "rule" in item
               for e in item["rule"]["expr"] if "counter" in e)
