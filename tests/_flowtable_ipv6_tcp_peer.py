"""LAN peer staged by test_flowtable_ipv6; commands use the tested connection.

The controller prepends LAN_IPV6, WAN_IPV6, SPORT and DPORT before staging this
file with lan_run_python. Commands share the TCP connection being measured.
"""
import json
import socket


BLOCK = bytes(range(256)) * 16


def main():
    with socket.socket(socket.AF_INET6, socket.SOCK_STREAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        sock.settimeout(30)
        sock.bind((LAN_IPV6, SPORT))
        sock.connect((WAN_IPV6, DPORT))
        with sock.makefile("rb") as reader:
            sock.sendall(b'{"ready": true}\n')
            while True:
                line = reader.readline()
                if not line:
                    raise AssertionError("controller closed without a close command")
                command = json.loads(line)
                op = command["op"]
                if op == "close":
                    break
                blocks = command["blocks"]
                if op == "upload":
                    for _ in range(blocks):
                        sock.sendall(BLOCK)
                elif op == "download":
                    remaining = blocks * len(BLOCK)
                    while remaining:
                        chunk = reader.read(min(remaining, 65536))
                        assert chunk, ("controller stopped mid-download", remaining)
                        remaining -= len(chunk)
                else:
                    raise AssertionError(("unknown op", command))
                sock.sendall(json.dumps({"op": op, "bytes": blocks * len(BLOCK)}).encode() + b"\n")
    print(json.dumps({"closed": True}))


main()
