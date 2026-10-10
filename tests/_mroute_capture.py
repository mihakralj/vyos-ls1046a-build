"""Routed multicast packet oracle, staged unchanged on a listener host (stdlib only)."""
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

MAGIC = b"ASK-A158"
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


def payload(token: str, sequence: int) -> bytes:
    return MAGIC + bytes.fromhex(token) + struct.pack("!I", sequence) + b"." * 128


def decode(frame: bytes, config: dict, source_mac: str) -> int | None:
    """Return this run's sequence; unrelated traffic is ignored, bad copies fail.

    Sockets bind to individual VLAN devices, so VLAN demultiplexing itself is
    the framing oracle. L2/L3 headers here are the frame after that demultiplex.
    They take every frame (ETH_P_ALL), because a socket bound to one protocol
    sees nothing on a bridge port -- the bridge's receive handler takes the
    frame before protocol taps run -- and capture() leaves a tagged frame some
    VLAN device claims to that device's socket, as the protocol tap did.
    """
    family = config["family"]
    ip_start, udp_start = 14, 34 if family == 4 else 54
    if len(frame) < udp_start + 8:
        return None
    if frame[12:14] != (b"\x08\x00" if family == 4 else b"\x86\xdd"):
        return None
    if family == 4:
        if frame[23] != 17:
            return None
        udp_start = ip_start + (frame[ip_start] & 15) * 4
        source, group, ttl = frame[26:30], frame[30:34], frame[22]
    else:
        if frame[20] != 17:
            return None
        source, group, ttl = frame[22:38], frame[38:54], frame[21]
    if (source, group) != (ipaddress.ip_address(config["source"]).packed,
                           ipaddress.ip_address(config["group"]).packed):
        return None
    if len(frame) < udp_start + 8:
        return None
    sport, dport, length, _ = struct.unpack_from("!HHHH", frame, udp_start)
    if (sport, dport) != (config["port"], config["port"]):
        return None
    body = frame[udp_start + 8:udp_start + length]
    prefix = MAGIC + bytes.fromhex(config["token"])
    if not body.startswith(prefix):
        return None
    assert len(body) == len(prefix) + 4 + 128, "truncated/oversized replica"
    sequence = struct.unpack_from("!I", body, len(prefix))[0]
    assert 0 <= sequence < config["count"], f"unexpected sequence {sequence}"
    assert body == payload(config["token"], sequence), "corrupt payload"
    # One routing hop by default. A bridged replica keeps the sender's 64.
    hops = config.get("hops", 63)
    assert ttl == hops, f"expected TTL/hop-limit {hops}, got {ttl}"
    assert frame[:6] == multicast_mac(config["group"]), "wrong multicast MAC"
    # None leaves the source MAC to a case that asserts it on its own.
    if source_mac is not None:
        assert frame[6:12] == bytes.fromhex(source_mac.replace(":", "")), "wrong source MAC"
    return sequence


def record(result: dict, sequence: int) -> None:
    if sequence in result["seen"]:
        result["duplicates"] += 1
    result["seen"].add(sequence)


def summary(results: dict) -> dict:
    return {iface: {**r, "seen": sorted(r["seen"])} for iface, r in results.items()}


def assert_results(results: dict, expected: list[str], count: int) -> None:
    assert set(expected) <= results.keys(), (expected, results.keys())
    for iface, result in results.items():
        want = list(range(count)) if iface in expected else []
        assert result["seen"] == want, f"{iface}: missing or unexpected replicas: {result}"
        assert result["duplicates"] == 0, f"{iface}: duplicate replicas: {result}"
        assert not result["errors"], f"{iface}: malformed replicas: {result}"


def capture(config: dict) -> None:
    results = {iface: {"seen": set(), "duplicates": 0, "errors": []}
               for iface in config["interfaces"]}
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
            for iface, mac in config["interfaces"].items():
                sock = socket.socket(socket.AF_PACKET, socket.SOCK_RAW, socket.htons(ETH_P_ALL))
                sockets.append(sock)
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 1024 * 1024)
                sock.bind((iface, ETH_P_ALL))
                sock.setsockopt(SOL_PACKET, PACKET_AUXDATA, 1)
                # Join only this Ethernet multicast address on this socket.
                # Closing the socket drops its membership; no persistent
                # promiscuity, sysctl or IP membership is changed.
                request = struct.pack("IHH8s", socket.if_nametoindex(iface), 0, 6,
                                      multicast_mac(config["group"]))
                sock.setsockopt(SOL_PACKET, PACKET_ADD_MEMBERSHIP, request)
                # And promiscuity, held by this socket alone: a VLAN device on
                # a snooping bridge (the orchestrator's WAN peer is one) only
                # sees a group the bridge knows its host joined, and a routed
                # replica's group is one nothing here joins at the IP layer.
                # A promiscuous upper makes the bridge deliver it anyway.
                request = struct.pack("IHH8s", socket.if_nametoindex(iface), 1, 0, b"")
                sock.setsockopt(SOL_PACKET, PACKET_ADD_MEMBERSHIP, request)  # PACKET_MR_PROMISC
                sock.setblocking(False)
                poll.register(sock, selectors.EVENT_READ, (iface, mac))
            Path(config["ready"]).write_text(str(os.getpid()))
            deadline = time.monotonic() + config.get("seconds", 30)
            while running and time.monotonic() < deadline:
                for key, _ in poll.select(0.1):
                    iface, mac = key.data
                    for _ in range(64):
                        try:
                            frame, ancillary, _, address = key.fileobj.recvmsg(
                                65536, socket.CMSG_SPACE(32))
                        except BlockingIOError:
                            break
                        if address[2] == socket.PACKET_OUTGOING:
                            continue
                        # A socket on a VLAN's parent also sees that VLAN's
                        # frames, still tagged; they are the child's copies
                        # to count, not ours.
                        if address[0] != iface:
                            continue
                        vlan = arrival_tag(ancillary)
                        if vlan and (iface, vlan) in children:
                            continue
                        try:
                            sequence = decode(frame, config, mac)
                            if sequence is not None:
                                record(results[iface], sequence)
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
