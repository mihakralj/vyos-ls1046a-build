"""Multicast wire oracle, staged unchanged on a listener host (stdlib only).

Records, per receiving interface, exactly what one run's frames looked like on
the wire: which datagrams arrived whole and in which size class, how many
arrived as fragments instead, and the Ethernet and hop-count framing each
carried. The test decides what to require of it; this only observes.

Sockets bind to individual interfaces, VLAN devices included, so VLAN
demultiplexing is itself the framing oracle for the tag: a copy on the wrong
VLAN never reaches the socket that expects it.

They take every frame (ETH_P_ALL) and pick this run's out by EtherType
themselves. A socket bound to one protocol sees nothing on a bridge port: the
bridge's receive handler takes the frame before protocol taps run, and only
the all-protocol taps come first. Those also see a tagged frame on its parent
before the VLAN device does, so a tagged frame some VLAN device here claims is
left to that device's socket, as the protocol tap would have left it; every
copy counted records the VLAN it arrived tagged with, 0 for none.
"""
from __future__ import annotations

import ipaddress
import json
import os
from pathlib import Path
import selectors
import signal
import socket
import struct
import sys
import time

MAGIC = b"ASKMCW1"
# Magic, token, size class, sequence.
HEADER = len(MAGIC) + 16 + 1 + 4
ETH_P_ALL = 0x0003
SOL_PACKET = 263
PACKET_ADD_MEMBERSHIP = 1
PACKET_AUXDATA = 8
TP_STATUS_VLAN_VALID = 1 << 4


def vlan_children(table: Path = Path("/proc/net/vlan/config")) -> set:
    """(parent, VLAN ID) of every 802.1Q device on this host."""
    try:
        lines = table.read_text().splitlines()[2:]
    except OSError:
        return set()             # no 8021q module, so no VLAN device
    children = set()
    for line in lines:
        fields = [field.strip() for field in line.split("|")]
        if len(fields) == 3 and fields[1].isdigit():
            children.add((fields[2], int(fields[1])))
    return children


def arrival_tag(ancillary) -> int:
    """The VLAN ID a frame arrived tagged with, from its PACKET_AUXDATA; 0 for
    an untagged or priority-tagged one."""
    for level, kind, data in ancillary:
        if level == SOL_PACKET and kind == PACKET_AUXDATA and len(data) >= 20:
            status, _, _, _, _, tci, _ = struct.unpack_from("=IIIHHHH", data)
            if status & TP_STATUS_VLAN_VALID:
                return tci & 0x0fff
    return 0


def multicast_mac(group: str) -> bytes:
    address = ipaddress.ip_address(group)
    if address.version == 6:
        return b"\x33\x33" + address.packed[-4:]
    return b"\x01\x00\x5e" + (int(address) & 0x7fffff).to_bytes(3, "big")


def payload(token: str, size_class: int, sequence: int, length: int) -> bytes:
    """A UDP payload of exactly `length` bytes naming its run, class and
    sequence, so a copy can be told apart from every other on the segment."""
    head = MAGIC + bytes.fromhex(token) + struct.pack("!BI", size_class, sequence)
    assert length >= len(head), length
    return head + b"." * (length - len(head))


def decode(frame: bytes, config: dict):
    """Classify one received frame of this run.

    Returns None for anything that is not this run's (S,G) at all,
    ("fragment", None) for a fragment of it, and ("whole", info) for a complete
    datagram, where info carries its class, sequence and framing. Raises
    AssertionError for a copy of this run that arrived corrupt.
    """
    family = config["family"]
    source = ipaddress.ip_address(config["source"]).packed
    group = ipaddress.ip_address(config["group"]).packed
    if family == 4:
        if len(frame) < 34 or frame[12:14] != b"\x08\x00":
            return None
        ip_start = 14
        header = (frame[ip_start] & 15) * 4
        if frame[26:30] != source or frame[30:34] != group:
            return None
        flags_offset = struct.unpack_from("!H", frame, ip_start + 6)[0]
        if flags_offset & 0x3fff:          # MF, or a non-zero offset
            return ("fragment", None)
        if frame[ip_start + 9] != 17:
            return None
        hops = frame[ip_start + 8]
        dont_fragment = bool(flags_offset & 0x4000)
        udp_start = ip_start + header
        total = struct.unpack_from("!H", frame, ip_start + 2)[0]
    else:
        if len(frame) < 54 or frame[12:14] != b"\x86\xdd":
            return None
        if frame[22:38] != source or frame[38:54] != group:
            return None
        if frame[20] == 44:                # a Fragment header follows
            return ("fragment", None)
        if frame[20] != 17:
            return None
        hops = frame[21]
        dont_fragment = True
        udp_start = 54
        total = 40 + struct.unpack_from("!H", frame, 18)[0]
    if len(frame) < udp_start + 8:
        return None
    sport, dport, length, _ = struct.unpack_from("!HHHH", frame, udp_start)
    if (sport, dport) != (config["port"], config["port"]):
        return None
    body = frame[udp_start + 8:udp_start + length]
    prefix = MAGIC + bytes.fromhex(config["token"])
    if not body.startswith(prefix):
        return None
    assert len(body) >= HEADER, "truncated replica"
    size_class, sequence = struct.unpack_from("!BI", body, len(prefix))
    assert body == payload(config["token"], size_class, sequence, len(body)), \
        "corrupt payload"
    return ("whole", {
        "class": size_class, "sequence": sequence, "ip_length": total,
        "hops": hops, "dont_fragment": dont_fragment,
        "destination": frame[0:6].hex(":"), "source": frame[6:12].hex(":"),
    })


def empty() -> dict:
    return {"seen": {}, "duplicates": 0, "fragments": 0, "hops": set(),
            "sources": set(), "destinations": set(), "lengths": set(),
            "vlans": set(), "errors": []}


def record(result: dict, verdict, vlan: int = 0) -> None:
    if verdict is None:
        return
    kind, info = verdict
    if kind == "fragment":
        result["fragments"] += 1
        return
    result["vlans"].add(vlan)
    seen = result["seen"].setdefault(str(info["class"]), set())
    if info["sequence"] in seen:
        result["duplicates"] += 1
    seen.add(info["sequence"])
    result["hops"].add(info["hops"])
    result["sources"].add(info["source"])
    result["destinations"].add(info["destination"])
    result["lengths"].add(info["ip_length"])


def summary(results: dict) -> dict:
    return {iface: {**r, "seen": {k: sorted(v) for k, v in r["seen"].items()},
                    "hops": sorted(r["hops"]), "sources": sorted(r["sources"]),
                    "destinations": sorted(r["destinations"]),
                    "lengths": sorted(r["lengths"]), "vlans": sorted(r["vlans"])}
            for iface, r in results.items()}


def capture(config: dict) -> None:
    results = {iface: empty() for iface in config["interfaces"]}
    sockets = []
    running = True

    def stop(signum, frame):
        nonlocal running
        running = False

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    children = vlan_children()
    try:
        with selectors.DefaultSelector() as poll:
            for iface in config["interfaces"]:
                sock = socket.socket(socket.AF_PACKET, socket.SOCK_RAW,
                                     socket.htons(ETH_P_ALL))
                sockets.append(sock)
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 4 * 1024 * 1024)
                sock.bind((iface, ETH_P_ALL))
                sock.setsockopt(SOL_PACKET, PACKET_AUXDATA, 1)
                # Only this group's Ethernet address, on this socket alone.
                # Closing the socket drops the membership; nothing persistent
                # (promiscuity, sysctls, IP memberships) is changed.
                request = struct.pack("IHH8s", socket.if_nametoindex(iface), 0, 6,
                                      multicast_mac(config["group"]))
                sock.setsockopt(SOL_PACKET, PACKET_ADD_MEMBERSHIP, request)
                sock.setblocking(False)
                poll.register(sock, selectors.EVENT_READ, iface)
            Path(config["ready"]).write_text(str(os.getpid()))
            deadline = time.monotonic() + config.get("seconds", 30)
            while running and time.monotonic() < deadline:
                for key, _ in poll.select(0.1):
                    iface = key.data
                    for _ in range(64):
                        try:
                            frame, ancillary, _, address = key.fileobj.recvmsg(
                                65536, socket.CMSG_SPACE(32))
                        except BlockingIOError:
                            break
                        if address[2] == socket.PACKET_OUTGOING:
                            continue
                        vlan = arrival_tag(ancillary)
                        if vlan and (iface, vlan) in children:
                            continue     # that VLAN device's copy, not this one's
                        try:
                            record(results[iface], decode(frame, config), vlan)
                        except AssertionError as exc:
                            results[iface]["errors"].append(str(exc))
    finally:
        for sock in sockets:
            sock.close()
        destination = Path(config["result"])
        temporary = destination.with_suffix(".tmp")
        temporary.write_text(json.dumps(summary(results)))
        temporary.replace(destination)


if __name__ == "__main__":
    capture(json.loads(sys.argv[1]))
