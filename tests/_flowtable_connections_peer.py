"""Concurrent TCP/UDP peer; staged once through the LAN guest agent.

CONFIG is prepended by the controller. A local Unix socket accepts commands
from short guest-agent operations while traffic runs in this background process.
Every exchange carries its connection ID and a monotonically increasing serial.
"""
import asyncio
from contextlib import contextmanager
import errno
import json
import os
import resource
import socket
import struct
import time

TCP_SIZE = 16384
UDP_SIZE = 256
CONTROL_IDLE_TIMEOUT = 35
# linux/in.h: a socket's DF policy, for probes on a flow's own tuple.
IP_MTU_DISCOVER = 10
IP_PMTUDISC_PROBE = 3
IP_PMTUDISC_INTERFACE = 4


def payload(ident, serial, size):
    return struct.pack("!IQ", ident, serial) + bytes([ident % 251]) * (size - 12)


class WireProbe:
    """Count uniquely marked test frames reaching the LAN interface."""
    def __init__(self):
        self.sock = None
        self.received = 0
        self.sample_limit = 0
        self.samples = []

    def rpc(self, action, iface=None, marker=None, samples=0, promiscuous=False,
            incoming_only=False):
        if action == 'start':
            assert self.sock is None
            self.marker = bytes.fromhex(marker)
            assert len(self.marker) >= 16
            self.received = 0
            assert 0 <= samples <= 32
            self.sample_limit = samples
            self.samples = []
            self.sock = socket.socket(socket.AF_PACKET, socket.SOCK_RAW, socket.htons(3))
            self.sock.bind((iface, 0))
            if incoming_only:
                # PACKET_IGNORE_OUTGOING: ICMP errors can quote our marker;
                # a sender's reply is not another delivered test frame.
                self.sock.setsockopt(263, 23, 1)
            if samples:
                self.sock.setsockopt(263, 8, 1)  # PACKET_AUXDATA
            if promiscuous:
                self.sock.setsockopt(263, 1, struct.pack('IHH8s', socket.if_nametoindex(iface), 1, 0, b''))
            self.sock.setblocking(False)
            asyncio.get_running_loop().add_reader(self.sock.fileno(), self.drain)
        elif action == 'stop':
            self.close()
        else:
            assert action == 'status'
            self.drain()
        result = {'received': self.received}
        if self.sample_limit:
            result['samples'] = list(self.samples)
        return result

    def drain(self):
        while self.sock:
            try:
                frame, ancillary, _, _ = self.sock.recvmsg(65536, 256)
            except BlockingIOError:
                break
            if self.marker in frame:
                self.received += 1
                if len(self.samples) < self.sample_limit:
                    # Reconstitute a tag stripped by the receiver's VLAN
                    # offload so the sample describes Ethernet on the wire.
                    for level, kind, value in ancillary:
                        if (level, kind) == (263, 8):
                            status, _, _, _, _, tci, tpid = struct.unpack('IIIHHHH', value[:20])
                            if status & (1 << 4):
                                tpid = tpid if status & (1 << 6) else 0x8100
                                frame = frame[:12] + struct.pack('!HH', tpid, tci) + frame[12:]
                    self.samples.append({'length': len(frame), 'header': frame[:128].hex()})

    def close(self):
        if self.sock:
            self.drain()
            asyncio.get_running_loop().remove_reader(self.sock.fileno())
            self.sock.close()
            self.sock = None


@contextmanager
def namespace(spec):
    # No await is permitted in this context: other tasks share this thread.
    # Sockets retain their creation namespace after the thread returns.
    if "netns" not in spec:
        yield
        return
    with open('/proc/self/ns/net', 'rb') as original, open('/var/run/netns/' + spec["netns"], 'rb') as peer:
        os.setns(peer.fileno(), os.CLONE_NEWNET)
        try:
            yield
        finally:
            os.setns(original.fileno(), os.CLONE_NEWNET)


class Flow:
    def __init__(self, spec, validate_udp=None, capture_udp=None):
        self.spec = spec
        self.validate_udp = validate_udp
        self.capture_udp = capture_udp
        self.serial = 0
        # Lifetime count of validated records, including late UDP replies.
        # Fault tests inspect it while a transfer is still running.
        self.received = 0
        self.reader = self.writer = self.sock = None
        self.wire = None
        self.stop = asyncio.Event()

    async def open(self, config):
        local = (self.spec.get("lan", config["lan"]), self.spec["sport"])
        remote = (self.spec.get("connect_ip", config["wan"]),
                  self.spec.get("connect_port", config["dport"]))
        family = socket.AF_INET6 if ":" in local[0] else socket.AF_INET
        if self.spec["proto"] == "tcp":
            with namespace(self.spec):
                sock = socket.socket(family, socket.SOCK_STREAM)
            try:
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                sock.setblocking(False)
                sock.bind(local)
                await asyncio.get_running_loop().sock_connect(sock, remote)
                self.reader, self.writer = await asyncio.open_connection(sock=sock)
            except BaseException:
                sock.close()
                raise
            self.writer.write(json.dumps({"id": self.spec["id"], "serial": self.serial}).encode() + b"\n")
            await self.writer.drain()
        else:
            with namespace(self.spec):
                self.sock = socket.socket(family, socket.SOCK_DGRAM)
            self.sock.setblocking(False)
            if "tos" in self.spec:
                # The whole traffic-class byte: DSCP and ECN as given.
                if family == socket.AF_INET6:
                    self.sock.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_TCLASS, self.spec["tos"])
                else:
                    self.sock.setsockopt(socket.IPPROTO_IP, socket.IP_TOS, self.spec["tos"])
            self.sock.bind(local)
            self.sock.connect(remote)
            if "wire" in self.spec:
                with namespace(self.spec):
                    self.wire = self.capture_udp(self.spec["sport"])
                self.wire.bind((self.spec["iface"], 0))
                self.wire.setblocking(False)
                if self.spec["wire"].get("zero_checksum"):
                    self.sock.setsockopt(socket.SOL_SOCKET, 11, 1)  # Linux SO_NO_CHECK

    async def probe_after_loss(self, size):
        """Whether a lost datagram was one packet or the flow going dark: five
        more probes a second apart, and what came back. Only on the failure
        path, so it spends serials nothing counts any more."""
        loop = asyncio.get_running_loop()
        results = []
        for n in range(1, 6):
            data = payload(self.spec["id"], self.serial + n, size)
            try:
                await loop.sock_sendall(self.sock, data)
                async with asyncio.timeout(1):
                    while await loop.sock_recv(self.sock, size + 1) != data:
                        pass
                results.append("ok")
            except (TimeoutError, OSError) as error:
                results.append(type(error).__name__)
        return f"after the loss, {time.monotonic():.3f}: " + " ".join(results)

    async def df_probe(self, data, df):
        """One datagram of `data` on this UDP flow's own tuple, with DF or
        without, and every Fragmentation Needed quoting it.

        PROBE sets DF and INTERFACE leaves it clear; both size the datagram by
        the interface alone, so the PMTU an earlier probe taught this host
        neither refuses nor fragments this one. The ICMP marks the connected
        socket with EMSGSIZE, consumed here; so is an echo that comes back
        within the listening window, and a caller that needs none at all
        silences the far end instead."""
        assert self.sock and self.sock.family == socket.AF_INET, self.spec
        loop = asyncio.get_running_loop()
        local, remote = self.sock.getsockname()[:2], self.sock.getpeername()[:2]
        mode = self.sock.getsockopt(socket.IPPROTO_IP, IP_MTU_DISCOVER)
        answers = []
        # Where the flow's own socket lives, or the answer is never seen.
        with namespace(self.spec):
            icmp = socket.socket(socket.AF_INET, socket.SOCK_RAW, socket.IPPROTO_ICMP)
        with icmp:
            icmp.setblocking(False)

            async def frag_needed():
                while True:
                    packet = await loop.sock_recv(icmp, 65536)
                    offset = (packet[0] & 15) * 4
                    quoted = packet[offset + 8:]
                    if len(packet) < offset + 8 or packet[offset:offset + 2] != b"\x03\x04":
                        continue
                    header = (quoted[0] & 15) * 4 if quoted else 0
                    if len(quoted) < max(header, 20) + 4 or quoted[9] != socket.IPPROTO_UDP:
                        continue
                    sport, dport = struct.unpack_from("!HH", quoted, header)
                    if ((socket.inet_ntoa(quoted[12:16]), sport) == local
                            and (socket.inet_ntoa(quoted[16:20]), dport) == remote):
                        answers.append({"mtu": struct.unpack_from("!H", packet, offset + 6)[0],
                                        "length": struct.unpack_from("!H", quoted, 2)[0]})

            async def echo():
                while True:
                    try:
                        if await loop.sock_recv(self.sock, len(data) + 1) == data:
                            return True
                    except OSError as error:
                        if error.errno != errno.EMSGSIZE:
                            raise

            self.sock.setsockopt(socket.IPPROTO_IP, IP_MTU_DISCOVER,
                                 IP_PMTUDISC_PROBE if df else IP_PMTUDISC_INTERFACE)
            try:
                await loop.sock_sendall(self.sock, data)
                echoing = asyncio.ensure_future(echo())
                try:
                    # Two seconds for Linux's answer, as the LAN end has
                    # always listened; frag_needed() returns only by timing out.
                    async with asyncio.timeout(2):
                        await frag_needed()
                except TimeoutError:
                    pass
                finally:
                    echoing.cancel()
                    # A cancelled reader yields CancelledError, which is not an
                    # Exception; a real receive error is, and is raised.
                    echoed, = await asyncio.gather(echoing, return_exceptions=True)
                if isinstance(echoed, Exception):
                    raise echoed
            finally:
                self.sock.setsockopt(socket.IPPROTO_IP, IP_MTU_DISCOVER, mode)
                self.sock.getsockopt(socket.SOL_SOCKET, socket.SO_ERROR)
        return {"length": 28 + len(data), "frag_needed": answers, "echoed": echoed is True}

    async def run(self, count, interval, allow_loss=False, udp_timeout=None):
        # Loss-tolerant UDP windows validate every received payload. Their
        # controller supplies the loss budget; TCP always requires delivery.
        assert not (allow_loss and self.writer)
        deadline = self.spec.get("tcp_timeout", 20) if self.writer else (
            udp_timeout if udp_timeout is not None else 0.1 if allow_loss else 5)
        assert deadline > 0
        first = self.serial
        received = lost = late = 0
        size = TCP_SIZE if self.writer else UDP_SIZE
        loop = asyncio.get_running_loop()
        # Spread the streams within each pacing interval.
        await asyncio.sleep((self.spec["id"] % 256) * interval / 256)
        started = time.monotonic()
        while not self.stop.is_set() and (not count or self.serial - first < count):
            data = payload(self.spec["id"], self.serial, size)
            try:
                async with asyncio.timeout(deadline):
                    if self.writer:
                        self.writer.write(data)
                        await self.writer.drain()
                        reply = await self.reader.readexactly(size)
                    else:
                        await loop.sock_sendall(self.sock, data)
                        while True:
                            reply = await loop.sock_recv(self.sock, size + 1)
                            if not allow_loss or reply == data:
                                break
                            assert len(reply) == size, (self.spec, reply)
                            ident, serial = struct.unpack('!IQ', reply[:12])
                            assert ident == self.spec['id'] and first <= serial < self.serial
                            assert reply == payload(ident, serial, size)
                            late += 1
                            self.received += 1
                        if self.wire:
                            while True:
                                frame = await loop.sock_recv(self.wire, 65536)
                                wire_payload = self.validate_udp(frame, **self.spec["wire"])
                                if wire_payload is not None:
                                    assert wire_payload == data, (self.spec, self.serial, frame.hex())
                                    break
                assert reply == data, (self.spec, self.serial, "corrupt, duplicate or misdirected echo")
                received += 1
                self.received += 1
            except (TimeoutError, OSError) as error:
                if not allow_loss or (isinstance(error, OSError) and not isinstance(error, TimeoutError)
                                      and error.errno not in {errno.EHOSTUNREACH, errno.ENETUNREACH,
                                                              errno.ECONNREFUSED}):
                    error.add_note(f"flow={self.spec} serial={self.serial} first={first}")
                    if not self.writer and isinstance(error, TimeoutError):
                        error.add_note(await self.probe_after_loss(size))
                    if self.wire:
                        packets, drops = struct.unpack("II", self.wire.getsockopt(263, 6, 8))
                        error.add_note(f"receive capture: {packets} packets, {drops} socket drops; serial={self.serial}")
                    raise
                lost += 1
            self.serial += 1
            delay = (self.serial - first) * interval - (time.monotonic() - started)
            if delay > 0:
                await asyncio.sleep(delay)
        return {"first": first, "count": self.serial - first,
                "bytes": received * size, "received": received, "lost": lost, "late": late,
                "seconds": time.monotonic() - started}

    def tcp_info(self):
        if not self.writer:
            return None
        sock = self.writer.get_extra_info("socket")
        info = sock.getsockopt(socket.IPPROTO_TCP, socket.TCP_INFO, 104)
        header = struct.unpack_from("8B", info)
        values = struct.unpack_from("24I", info, 8)
        return {"state": header[0], "backoff": header[4], "rto_us": values[0],
                "unacked": values[4], "lost": values[6], "retrans": values[7],
                "rtt_us": values[15], "total_retrans": values[23]}

    async def close(self):
        if self.writer:
            if self.spec.get("abort"):
                self.writer.get_extra_info("socket").setsockopt(socket.SOL_SOCKET, socket.SO_LINGER,
                                                               struct.pack("ii", 1, 0))
                self.writer.transport.abort()
            else:
                self.writer.write_eof()
                assert await asyncio.wait_for(self.reader.read(), 5) == b""
            self.writer.close()
            await self.writer.wait_closed()
            self.writer = None
        if self.sock:
            self.sock.close()
            self.sock = None
        if self.wire:
            self.wire.close()
            self.wire = None


async def main(config):
    global TCP_SIZE
    TCP_SIZE = config["tcp_size"]
    flows = {}
    running = {}
    control = control_server = None
    accepted = asyncio.Queue()
    reports = []
    servers = None
    multicast = MulticastListeners()
    wire_probe = WireProbe()

    async def finish(ids, stop=False):
        if stop:
            for ident in ids:
                flows[ident].stop.set()
        result = {}
        for ident in ids:
            result[ident] = await running.pop(ident)
        reports.append(result)
        return result

    try:
        # A local lease also ends traffic if the controller disappears.
        async with asyncio.timeout(config["lease"]):
            control_server = await asyncio.start_unix_server(
                lambda reader, writer: accepted.put_nowait((reader, writer)),
                config["control_path"], limit=8 << 20)
            specs = config["flows"]
            soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
            needed = len(specs) * 2 + 256
            assert needed <= hard, (needed, hard)
            resource.setrlimit(resource.RLIMIT_NOFILE, (max(soft, needed), hard))
            flows = {spec["id"]: Flow(spec, udp_wire_payload, udp_capture_socket) for spec in specs}
            servers = EchoServers(config.get("servers", []), specs, namespace, payload,
                                  udp_wire_payload, udp_capture_socket)
            await servers.start()
            opening = asyncio.Semaphore(32)

            async def open_one(ident):
                async with opening:
                    await flows[ident].open(config)

            while True:
                reader, control = await asyncio.wait_for(accepted.get(), CONTROL_IDLE_TIMEOUT)
                line = await asyncio.wait_for(reader.readline(), 5)
                command = json.loads(line)
                op, ids = command["op"], command.get("ids", [])
                result = {}
                if op == "open":
                    async with asyncio.TaskGroup() as group:
                        for ident in ids:
                            group.create_task(open_one(ident))
                elif op == "status":
                    result = {"running": len(running), "errors": {
                        ident: repr(task.exception()) for ident, task in running.items()
                        if task.done() and not task.cancelled() and task.exception()},
                        "received": {} if command.get("compact") else {ident: flow.received for ident, flow in flows.items()},
                        "tcp_info": {} if command.get("compact") else {ident: flow.tcp_info() for ident, flow in flows.items() if flow.writer}}
                elif op == "neighbour":
                    flow = flows[command["ident"]]
                    with namespace(flow.spec):
                        result = configure_neighbour(flow.spec["iface"], flow.spec["lan"], **command["changes"])
                elif op == "ndp":
                    flow = flows[command["ident"]]
                    with namespace(flow.spec):
                        result = configure_ndp(flow.spec["iface"], **command["changes"])
                elif op == "servers":
                    result = servers.status()
                elif op == "multicast":
                    result = multicast.rpc(**command["changes"])
                elif op == "wire_probe":
                    result = wire_probe.rpc(**command["changes"])
                elif op == "df_probe":
                    assert command["ident"] not in running, command
                    result = await flows[command["ident"]].df_probe(bytes.fromhex(command["data"]),
                                                                    command["df"])
                elif op == "start":
                    for ident in ids:
                        assert ident not in running
                        flow = flows[ident]
                        flow.stop.clear()
                        running[ident] = asyncio.create_task(flow.run(command["count"], command["interval"],
                                                                     command.get("allow_loss", False),
                                                                     command.get("udp_timeout")))
                elif op in {"wait", "stop"}:
                    result = await finish(ids, stop=op == "stop")
                elif op == "close":
                    assert all(i not in running for i in ids)
                    await asyncio.gather(*(flows[i].close() for i in ids))
                elif op == "shutdown":
                    result = await finish(list(running), stop=True)
                    await asyncio.gather(*(flow.close() for flow in flows.values()))
                else:
                    raise AssertionError(command)
                control.write(json.dumps({"op": op, "result": result}).encode() + b"\n")
                await control.drain()
                control.close()
                await control.wait_closed()
                control = None
                if op == "shutdown":
                    assert not servers.errors, servers.errors
                    print(json.dumps({"reports": reports if len(flows) <= 64 else None,
                                      "report_groups": len(reports), "flows": len(flows),
                                      "closed": True, "servers": servers.status()}), flush=True)
                    return
    finally:
        if control_server:
            control_server.close()
            # Python 3.13 also waits for accepted clients. Close them before
            # waiting, including the caller whose command just failed.
            if hasattr(control_server, "close_clients"):
                control_server.close_clients()
            else:  # LAN guests may still use Python 3.12.
                if control:
                    control.close()
                while not accepted.empty():
                    _, pending = accepted.get_nowait()
                    pending.close()
            await control_server.wait_closed()
        from pathlib import Path
        Path(config["control_path"]).unlink(missing_ok=True)
        wire_probe.close()
        multicast.close()
        tasks = list(running.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        for flow in flows.values():
            if flow.writer:
                flow.writer.close()
            if flow.sock:
                flow.sock.close()
            if flow.wire:
                flow.wire.close()
        if servers:
            await servers.close()
        if control:
            control.close()
            await control.wait_closed()


if __name__ == "__main__":
    asyncio.run(main(CONFIG))
