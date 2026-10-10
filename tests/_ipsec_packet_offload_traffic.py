"""Shared support for ipsec packet offload traffic."""

from __future__ import annotations

# Inner endpoints, carried inside the tunnel, on lo at each end.
DUT_INNER = "198.18.87.1"
LAN_INNER = "198.18.87.2"
PORT = 48701
REQID = 48701
COUNT = 32
CIPHER = b"\xa5" * 16
AUTH = b"\x5a" * 32


async def toenc(session, agent, iface):
    """Total `tx toenc` across CPUs: frames this port handed to SEC."""
    result = await agent.exec_cmd(session, ["ethtool", "-S", iface])
    assert result["rc"] == 0, result
    return sum(int(line.split(":")[1])
               for line in result["stdout"].splitlines()
               if line.strip().startswith("tx toenc [CPU"))


def decrypted_packets(state_output, spi):
    """Packets the SA for `spi` processed, from `ip -s xfrm state` output.

    Anchored on the "lifetime current:" heading rather than on the shape of a
    counter line, because the "limit:" lines above it have the same shape and
    carry (INF) where a number would be. `ip` also prints the SPI zero-padded
    to eight digits, which is not what hex() gives for a value whose top
    nibble is zero, so the needle is formatted rather than converted.
    """
    needle = "spi 0x%08x" % spi
    lines = [line.strip() for line in state_output.splitlines()]
    for index, line in enumerate(lines):
        if needle not in line:
            continue
        for offset, probe in enumerate(lines[index:index + 24]):
            if probe.startswith("lifetime current"):
                current = lines[index + offset + 1]
                return int(current.split(",")[1].split("(")[0])
    return 0
