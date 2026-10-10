"""LAN TCP/UDP echo endpoints staged alongside the flowtable control peer."""
import asyncio
import json
import socket
import struct


class EchoServers:
    def __init__(self, servers, flows, namespace, payload, validate_udp, capture_udp):
        self.config, self.flows = servers, {f["id"]: f for f in flows}
        self.namespace, self.payload, self.validate_udp = namespace, payload, validate_udp
        self.capture_udp = capture_udp
        self.listeners, self.sockets, self.tasks, self.writers = [], [], set(), set()
        self.counts, self.errors = {}, []

    def task(self, coroutine):
        task = asyncio.create_task(coroutine)
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)

    async def start(self):
        try:
            for spec in self.config:
                with self.namespace(spec):
                    tcp = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                    self.sockets.append(tcp)
                    udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                    self.sockets.append(udp)
                    raw = self.capture_udp(spec["port"])
                    self.sockets.append(raw)
                local = (spec["address"], spec["port"])
                tcp.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                tcp.bind(local)
                tcp.listen(16)
                tcp.setblocking(False)
                udp.bind(local)
                udp.setsockopt(socket.SOL_SOCKET, 11, int(spec.get("zero_checksum", False)))
                udp.setblocking(False)
                raw.bind((spec["iface"], 0))
                raw.setblocking(False)
                listener = await asyncio.start_server(lambda rd, wr: self.task(self.tcp(rd, wr)), sock=tcp)
                self.listeners.append(listener)
                self.task(self.udp(udp, raw))
        except BaseException:
            await self.close()
            raise

    async def tcp(self, reader, writer):
        ident = None
        self.writers.add(writer)
        try:
            ident = json.loads(await asyncio.wait_for(reader.readline(), 10))["id"]
            spec = self.flows[ident]
            assert spec["proto"] == "tcp" and ident not in self.counts, ident
            assert writer.get_extra_info("peername") == tuple(spec["server_peer"]), (spec, writer.get_extra_info("peername"))
            self.counts[ident] = 0
            while True:
                try:
                    data = await reader.readexactly(16384)
                except asyncio.IncompleteReadError as error:
                    assert not error.partial, (ident, "partial TCP record", len(error.partial))
                    break
                assert data == self.payload(ident, self.counts[ident], 16384), ident
                self.counts[ident] += 1
                writer.write(data)
                await writer.drain()
        except ConnectionError as error:
            if ident not in self.flows or not self.flows[ident].get("abort"):
                self.errors.append((ident, repr(error)))
        except asyncio.CancelledError:
            raise
        except Exception as error:
            self.errors.append((ident, repr(error)))
        finally:
            writer.close()
            try:
                await asyncio.wait_for(writer.wait_closed(), 5)
            except (ConnectionError, TimeoutError):
                writer.transport.abort()
            self.writers.discard(writer)

    async def udp(self, sock, raw):
        loop = asyncio.get_running_loop()
        try:
            while True:
                data, address = await loop.sock_recvfrom(sock, 65536)
                assert len(data) == 256, (address, len(data))
                ident = struct.unpack_from("!I", data)[0]
                spec = self.flows[ident]
                assert spec["proto"] == "udp" and address == tuple(spec["server_peer"]), (spec, address)
                serial = self.counts.get(ident, 0)
                assert data == self.payload(ident, serial, 256), (ident, serial)
                async with asyncio.timeout(5):
                    while True:
                        frame = await loop.sock_recv(raw, 65536)
                        packet = self.validate_udp(frame, **spec["server_wire"])
                        if packet is not None:
                            assert packet == data, (ident, serial, "wire payload")
                            break
                self.counts[ident] = serial + 1
                await loop.sock_sendto(sock, data, address)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            self.errors.append(("udp", repr(error)))

    def status(self):
        return {"counts": self.counts, "errors": self.errors}

    async def close(self):
        for server in self.listeners:
            server.close()
        for writer in list(self.writers):
            writer.transport.abort()
        for task in list(self.tasks):
            task.cancel()
        await asyncio.gather(*list(self.tasks), return_exceptions=True)
        for server in self.listeners:
            await server.wait_closed()
        for sock in self.sockets:
            sock.close()
