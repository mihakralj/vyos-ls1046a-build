"""LAN peer staged by test_flowtable_tcp; commands use the tested connection.

The controller prepends LAN_IP, WAN_IP, SPORT and DPORT before staging this
file with lan_run_python. Commands share the TCP connection being measured.
"""
import hashlib
import json
import os
import socket
import struct
import time


BLOCK = bytes(range(256)) * 256


def retransmits(sock):
    # Linux UAPI struct tcp_info: eight initial u8 bytes, then 24 u32s.
    # tcpi_total_retrans is the last u32 in this stable 104-byte prefix.
    info = sock.getsockopt(socket.IPPROTO_TCP, socket.TCP_INFO, 104)
    assert len(info) == 104
    return struct.unpack_from("=I", info, 100)[0]


def peer_socket():
    # A socket retains the namespace where it was created. Return the process
    # to the LAN VM's namespace so gateway ARP controls remain available over the
    # same tested TCP connection, even when the endpoint sits behind the LAN VM.
    if PEER_NETNS is None:
        return socket.socket()
    with open('/proc/self/ns/net', 'rb') as original, open('/var/run/netns/' + PEER_NETNS, 'rb') as peer:
        os.setns(peer.fileno(), os.CLONE_NEWNET)
        try:
            return socket.socket()
        finally:
            os.setns(original.fileno(), os.CLONE_NEWNET)


def main():
    with peer_socket() as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        sock.settimeout(30)
        sock.bind((LAN_IP, SPORT))
        sock.connect((WAN_IP, DPORT))
        with sock.makefile("rb") as reader:
            sock.sendall(b'{"ready": true}\n')
            while True:
                line = reader.readline()
                if not line:
                    raise AssertionError("controller closed without a close command")
                command = json.loads(line)
                op = command["op"]
                if op == "neighbour":
                    changes = {key: value for key, value in command.items() if key != "op"}
                    changes.setdefault("address", LAN_IP)
                    report = configure_neighbour(**changes)
                    sock.sendall(json.dumps(report).encode() + b"\n")
                    continue
                if op == "fin":
                    sock.shutdown(socket.SHUT_WR)
                    assert reader.read() == b"", "unexpected data after FIN"
                    print(json.dumps({"closed": "fin"}), flush=True)
                    return
                if op == "rst":
                    sock.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER,
                                    struct.pack("ii", 1, 0))
                    print(json.dumps({"closed": "rst"}), flush=True)
                    return
                assert op in {"upload", "download"}, command
                size, rate = command["size"], command["rate"]
                assert size > 0 and size % len(BLOCK) == 0 and rate > 0
                before = retransmits(sock)
                digest = hashlib.sha256()
                start = time.monotonic()
                for offset in range(0, size, len(BLOCK)):
                    if op == "upload":
                        sock.sendall(BLOCK)
                        delay = (offset + len(BLOCK)) / rate - (time.monotonic() - start)
                        if delay > 0:
                            time.sleep(delay)
                    else:
                        data = reader.read(len(BLOCK))
                        assert data == BLOCK, ("download corruption", offset, len(data))
                        digest.update(data)
                report = {"op": op, "bytes": size,
                          "retransmits": retransmits(sock) - before}
                if op == "download":
                    report["sha256"] = digest.hexdigest()
                sock.sendall(json.dumps(report).encode() + b"\n")


main()
