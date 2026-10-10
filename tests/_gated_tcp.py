"""A TCP connection whose phases the test releases, so its flow can be read
while the connection is open and idle.

A FIN is punted to Linux, which marks the flow closing, and the flowtable's
garbage collection retires its hardware entries within about a second, so a
row read after the peer closed is a race. Here the LAN peer echoes a first
phase of blocks, long enough for admission, and waits. The test reads the rows
and counters, releases a second phase and waits for it to be echoed, reads
again -- the difference is the measurement -- and only then releases the
close.

Every block is echoed before the next is sent, so both directions carry data.
The peer runs wherever the caller's `run` puts it (a LAN VM namespace, the LAN
VM itself); the echo end is this process, on the caller's far address.
"""
from __future__ import annotations

import asyncio
import json
import socket

BLOCK = bytes(range(256)) * 16
# Long enough after the last echo for the peer's delayed ACK to cross, so a
# read sees an idle connection rather than its last frame in flight.
SETTLE = 0.5

PEER = '''
import json, socket, time
s = socket.socket({family}, socket.SOCK_STREAM)
s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
s.bind(({source!r}, {sport}))
s.settimeout(60)
s.connect(({peer!r}, {dport}))
port = s.getsockname()[1]
block = bytes(range(256)) * 16
def echo(count):
    for _ in range(count):
        s.sendall(block)
        remaining = len(block)
        while remaining:
            chunk = s.recv(remaining)
            assert chunk, 'the far end closed mid-transfer'
            remaining -= len(chunk)
        time.sleep(0.002)
def released():
    assert s.recv(1) == b'G', 'the far end closed without releasing the next phase'
echo({warm})
released()
echo({measured})
released()
s.close()
print(json.dumps({{'port': port, 'sent': {sent}}}))
'''


class GatedTcp:
    """`run(script, *, timeout, label)` runs the peer script and returns the
    console result; `source`/`sport` is where it binds (port 0 lets the kernel
    pick, which a connection left in TIME_WAIT never blocks), `peer`/`dport`
    where this process listens.

    Use as an async context manager: `await warmed()` once admission has had
    its phase, `await measure()` for the measured one. On the way out every
    phase is released, so the peer always finishes and frees its console.
    `peername` is the address the connection arrived from, after any
    translation; `report` is the peer's own account, once it has closed."""

    def __init__(self, run, *, source, peer, dport, label, sport=0, warm=32, measured=128,
                 timeout=60):
        self.run, self.source, self.sport = run, source, sport
        self.peer, self.dport, self.label = peer, dport, label
        self.warm, self.measured, self.timeout = warm, measured, timeout
        self._echoed = [asyncio.Event(), asyncio.Event()]
        self._released = [asyncio.Event(), asyncio.Event()]
        self.peername = self.report = self.result = None

    async def _serve(self, reader, writer):
        self.peername = tuple(writer.get_extra_info("peername")[:2])
        try:
            for count, echoed, released in zip((self.warm, self.measured),
                                               self._echoed, self._released):
                for _ in range(count):
                    writer.write(await reader.readexactly(len(BLOCK)))
                    await writer.drain()
                echoed.set()
                await released.wait()
                writer.write(b"G")
                await writer.drain()
            await reader.read()
        except (asyncio.IncompleteReadError, ConnectionError):
            pass
        finally:
            writer.close()

    async def __aenter__(self):
        family = socket.AF_INET6 if ":" in self.peer else socket.AF_INET
        self.server = await asyncio.start_server(self._serve, self.peer, self.dport,
                                                 family=family)
        script = PEER.format(family="socket.AF_INET6" if family == socket.AF_INET6
                             else "socket.AF_INET",
                             source=self.source, sport=self.sport, peer=self.peer,
                             dport=self.dport, warm=self.warm, measured=self.measured,
                             sent=(self.warm + self.measured) * len(BLOCK))
        self.task = asyncio.create_task(self.run(script, timeout=self.timeout + 60,
                                                 label=self.label))
        return self

    async def _phase(self, index):
        echoed = asyncio.ensure_future(self._echoed[index].wait())
        done, _ = await asyncio.wait({echoed, self.task}, timeout=self.timeout,
                                     return_when=asyncio.FIRST_COMPLETED)
        if echoed not in done:
            echoed.cancel()
            output = self.task.result().stdout if self.task.done() else "no answer"
            raise AssertionError(f"{self.label}: phase {index + 1} was not echoed: {output}")
        await asyncio.sleep(SETTLE)

    async def warmed(self):
        await self._phase(0)

    async def measure(self):
        self._released[0].set()
        await self._phase(1)

    async def __aexit__(self, kind, error, trace):
        for released in self._released:
            released.set()
        try:
            self.result = await self.task
        finally:
            self.server.close()
            await self.server.wait_closed()
        if kind is None:
            assert self.result.rc == 0, self.result.stdout
            self.report = json.loads(self.result.stdout.strip().splitlines()[-1])
        return False
