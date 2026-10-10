"""A multicast host on the LAN VM, staged unchanged and run in the background.

It holds one membership through the socket calls a real receiver makes, so the
kernel emits exactly the report its version and filter mode produce, and it
changes that membership only when the controller asks. Receiving is not its
job: _mroute_capture.py counts the replicas beneath the IP layer, which is what
also shows whether they still reach the port after the host has gone.

The controller writes a command file atomically; this process acknowledges
each command by its serial in the state file. stdlib only.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import signal
import socket
import struct
import sys
import time

# The protocol-independent calls, which name the interface by index. The IPv4
# ip_mreq forms name it by address, and 0.0.0.0 there means whichever device
# the route to the group resolves to -- never a VLAN device or a second host.
MCAST_JOIN_GROUP, MCAST_BLOCK_SOURCE, MCAST_LEAVE_GROUP = 42, 43, 45
MCAST_JOIN_SOURCE_GROUP, MCAST_LEAVE_SOURCE_GROUP = 46, 47


def sockaddr(family: int, address: str) -> bytes:
    if family == 4:
        raw = struct.pack("=HH4s", socket.AF_INET, 0, socket.inet_aton(address))
    else:
        raw = struct.pack("=HHI16sI", socket.AF_INET6, 0, 0,
                          socket.inet_pton(socket.AF_INET6, address), 0)
    return raw.ljust(128, b"\0")


def request(config: dict, with_source: bool) -> bytes:
    # struct group_req and group_source_req: a u32 interface index, then
    # sockaddr_storage members aligned to eight bytes.
    raw = struct.pack("=I4x", socket.if_nametoindex(config["iface"]))
    raw += sockaddr(config["family"], config["group"])
    if with_source:
        raw += sockaddr(config["family"], config["source"])
    return raw


def igmp_querier(iface: str) -> str:
    """The IGMP version `iface` answers queries in: /proc/net/igmp's Querier
    column, V1 or V2 while an older query was heard recently, else V3."""
    for line in Path("/proc/net/igmp").read_text().splitlines()[1:]:
        fields = line.split()
        if len(fields) >= 5 and fields[1] == iface and fields[2] == ":":
            return fields[4]
    raise AssertionError(f"{iface} is not in /proc/net/igmp")


def publish(path: Path, state: dict) -> None:
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(state))
    temporary.replace(path)


def run(config: dict) -> None:
    family = config["family"]
    level = socket.IPPROTO_IP if family == 4 else socket.IPPROTO_IPV6
    version = Path("/proc/sys/net/ipv4/conf" if family == 4 else "/proc/sys/net/ipv6/conf",
                   config["iface"], "force_igmp_version" if family == 4 else "force_mld_version")
    ssm = config["mode"] == "ssm"
    state_path, command_path = Path(config["state"]), Path(config["command"])
    running = True

    def stop(signum, frame):
        nonlocal running
        running = False

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    previous = version.read_text().strip()
    sock = socket.socket(socket.AF_INET if family == 4 else socket.AF_INET6, socket.SOCK_DGRAM)
    try:
        # Before the join, so the very first report is already the version
        # under test rather than whatever the querier last negotiated.
        version.write_text(str(config["version"]))
        if family == 4 and config["version"] == 3:
            # force_igmp_version=3 does not override an IGMPv2 query heard
            # in the last few minutes: the interface would report no sources
            # and send no BLOCK, and a source filter would test nothing.
            answering = igmp_querier(config["iface"])
            assert answering == "V3", f"{config['iface']} answers IGMP as {answering} after a v2 query"
        sock.setsockopt(level, MCAST_JOIN_SOURCE_GROUP if ssm else MCAST_JOIN_GROUP,
                        request(config, ssm))
        state = {"pid": os.getpid(), "done": [], "member": True}
        publish(state_path, state)
        while running:
            try:
                command = json.loads(command_path.read_text())
            except (FileNotFoundError, ValueError):
                command = None
            if command and command["serial"] not in state["done"]:
                action = command["action"]
                if action == "leave":
                    # ASM: IGMPv2 leave, IGMPv3 TO_IN({}), MLDv1 done.
                    # SSM: the last source goes, which is IGMPv3 BLOCK({S}).
                    sock.setsockopt(level, MCAST_LEAVE_SOURCE_GROUP if ssm else MCAST_LEAVE_GROUP,
                                    request(config, ssm))
                    state["member"] = False
                elif action == "block":
                    # An EXCLUDE-mode host refusing one source: BLOCK({S}).
                    assert not ssm, "an INCLUDE-mode host blocks by leaving"
                    sock.setsockopt(level, MCAST_BLOCK_SOURCE, request(config, True))
                else:
                    raise AssertionError(f"unknown action {action!r}")
                state["done"].append(command["serial"])
                publish(state_path, state)
            time.sleep(0.05)
    finally:
        sock.close()
        version.write_text(previous)


if __name__ == "__main__":
    run(json.loads(sys.argv[1]))
