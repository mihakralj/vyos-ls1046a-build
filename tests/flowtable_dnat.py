"""WAN-initiated IPv4 TCP/UDP port forwarding through native DNAT."""

from _flowtable_dnat import dnat

import pytest


# Unlike every other flowtable test, these clients bind on the WAN host itself
# rather than on the LAN VM, so their source ports share a namespace with the sockets
# the rig fixture holds there: a UDP echo endpoint on (WAN_IP, DPORT) kept for
# the whole session and the TCP echo server on the same port. SPORT and
# DPORT are adjacent, so deriving the second
# parameter's port as SPORT + 1 landed on the echo socket and bound
# EADDRINUSE. Take them from an offset nothing else uses; +32, +96 and +128
# are spoken for by the connections, selective-neighbour and routes tests.


@pytest.mark.parametrize("zero_checksum", [False, True], ids=["checksum", "zero-checksum"])
async def test_flowtable_dnat(rig, zero_checksum, double_nat=False):
    await dnat(rig, zero_checksum, double_nat)
