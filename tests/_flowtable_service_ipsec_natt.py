"""Shared support for flowtable service ipsec natt."""

from __future__ import annotations

import struct
from collections import Counter
from pathlib import Path

from _flowtable_rig import WAN_IP, artifact_dir
from _flowtable_service_ipsec import Transform
from _flowtable_tunnel import Capture

# (DUT port, peer port). Neither is the other byte-swapped (0x1194 against
# 0x7918), so a swap of either kind lands on a port nothing listens on.
PORTS = (4500, 31000)
NATT = Transform(encap=PORTS)


class Encapsulated(Capture):
    """ESP between the DUT and the peer in both directions, bare or in UDP,
    taken below the WAN host's bridge."""
    snaplen = 64

    def __init__(self, r, label):
        self.path = artifact_dir() / (label + ".pcap")
        self.interface = r.ipsec_wire_if
        outer = r.ipsec.outer
        self.filter = (f"(udp or ip proto 50) and ((src host {outer} and dst host {WAN_IP}) or "
                       f"(src host {WAN_IP} and dst host {outer}))")


def encapsulations(path, spis):
    """How each SA's frames were carried: a count per (SPI, source MAC,
    encapsulation), where encapsulation is the UDP port pair or "esp" for a
    bare ESP frame. UDP whose payload does not start with one of `spis` is
    something else between the two hosts and is ignored."""
    seen, data, offset = Counter(), Path(path).read_bytes(), 24
    while offset + 16 <= len(data):
        length = struct.unpack_from("<I", data, offset + 8)[0]
        frame = data[offset + 16:offset + 16 + length]
        offset += 16 + length
        l3 = 18 if frame[12:14] == b"\x81\x00" else 14
        if len(frame) < l3 + 20:
            continue
        l4 = l3 + (frame[l3] & 0xF) * 4
        source = frame[6:12].hex(":")
        if frame[l3 + 9] == 50 and len(frame) >= l4 + 4:
            spi, how = struct.unpack_from("!I", frame, l4)[0], "esp"
        elif frame[l3 + 9] == 17 and len(frame) >= l4 + 12:
            spi, how = struct.unpack_from("!I", frame, l4 + 8)[0], struct.unpack_from("!HH", frame, l4)
        else:
            continue
        if spi in spis:
            seen[(spi, source, how)] += 1
    return seen


# A documentation-range pair of its own. Nothing is sent: the peer does not
# exist, and its neighbour entry is invented.
LOCAL, PEER = "198.18.104.1", "198.18.104.2"
REQID = "49306"


# Output marks for the update test. Nothing is sent over the SA; its peer
# still has to route by the WAN port with the mark, as it does on the bench.
MARK, OTHER_MARK = "0x10", "0x20"


def natt(sport, dport):
    return ["encap", "espinudp", str(sport), str(dport), "0.0.0.0"]
