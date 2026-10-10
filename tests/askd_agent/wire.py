"""Checked, paced messages over a console shared with occasional kernel output.

Only fragments are retransmitted. Completed message IDs are never delivered
twice, so losing an acknowledgement cannot repeat a device operation.
"""

import base64
import json
import queue
import threading
import zlib

VERSION = 2
CHUNK = 384
MAX_MESSAGE = 64 << 20
MAX_COMPRESSED = 16 << 20


def frame(*fields):
    body = ("@ASK1 " + " ".join(map(str, fields))).encode("ascii")
    return body + f" {zlib.crc32(body):08x}\n".encode()


def parse(line):
    body, checksum = line.rstrip(b"\r\n").rsplit(b" ", 1)
    if not body.startswith(b"@ASK1 ") or zlib.crc32(body) != int(checksum, 16):
        raise ValueError("invalid serial frame")
    return body.decode("ascii").split()[1:]


class Channel:
    """One reader, serialized writers, and one acknowledged fragment in flight.

    `read` returns bytes (an empty value is an idle timeout) or raises EOFError.
    Keeping it independent of termios also lets a socket pair exercise the real
    protocol, including corruption and lost acknowledgements, on the host.
    """

    # Thirty seconds of retransmits before a peer is given up on: a full-table
    # route drain stalls the DUT's agent for ~14 s (ISSUES.md A327), and a
    # sender that gives up there takes the session, and every test after it,
    # down with it. Retransmitting is safe; the receiver drops duplicates.
    def __init__(self, read, write, *, ack_timeout=2, attempts=15):
        self.read, self.write = read, write
        self.ack_timeout, self.attempts = ack_timeout, attempts
        self.incoming = queue.Queue()
        self.error = None
        self.stopped = threading.Event()
        self.write_lock = threading.Lock()
        self.send_lock = threading.Lock()
        self.acks = {}
        self.pending = {}
        self.seen = set()
        self.reader = threading.Thread(target=self._receive, daemon=True)
        self.reader.start()

    def _write(self, data):
        with self.write_lock:
            self.write(data)

    def send(self, ident, value):
        data = json.dumps(value, separators=(",", ":"), ensure_ascii=True).encode()
        if len(data) > MAX_MESSAGE:
            raise ValueError("serial message exceeds decoded size limit")
        data = zlib.compress(data, 1)
        if len(data) > MAX_COMPRESSED:
            raise ValueError("serial message exceeds compressed size limit")
        count = (len(data) + CHUNK - 1) // CHUNK
        with self.send_lock:
            for index in range(count):
                if self.error or self.stopped.is_set():
                    raise ConnectionError("serial channel closed") from self.error
                ack = self.acks[ident, index] = threading.Event()
                packet = frame("d", ident, index, count,
                               base64.b64encode(data[index * CHUNK:(index + 1) * CHUNK]).decode())
                try:
                    for _ in range(self.attempts):
                        self._write(packet)
                        if ack.wait(self.ack_timeout):
                            break
                    else:
                        raise TimeoutError(f"serial acknowledgement lost for {ident}:{index}; outcome unknown")
                    if self.error:
                        raise ConnectionError("serial channel failed") from self.error
                finally:
                    self.acks.pop((ident, index), None)

    def _packet(self, line):
        fields = parse(line)
        if len(fields) == 3 and fields[0] == "a":
            ident, index = int(fields[1]), int(fields[2])
            ack = self.acks.get((ident, index))
            if ack:
                ack.set()
            return
        if len(fields) != 5 or fields[0] != "d":
            raise ValueError("unknown serial frame")
        ident, index, count = map(int, fields[1:4])
        chunk = base64.b64decode(fields[4], validate=True)
        if not (0 <= ident < 1 << 63 and 0 <= index < count <= MAX_COMPRESSED // CHUNK + 1
                and 0 < len(chunk) <= CHUNK):
            raise ValueError("invalid serial fragment bounds")
        if ident in self.seen:
            self._write(frame("a", ident, index))
            return
        # The sender holds its message lock through the last acknowledgement.
        # A bounded assembly is enough; requests execute concurrently afterward.
        if ident not in self.pending:
            if index != 0 or len(self.pending) >= 2:
                raise ValueError("unexpected serial fragment")
            self.pending[ident] = (count, [])
        wanted, parts = self.pending[ident]
        if count != wanted or index > len(parts):
            raise ValueError("out-of-order serial fragment")
        if index < len(parts):
            if parts[index] != chunk:
                raise ValueError("changed serial fragment")
        else:
            parts.append(chunk)
        if len(parts) == count:
            decoder = zlib.decompressobj()
            decoded = decoder.decompress(b"".join(parts), MAX_MESSAGE + 1)
            if len(decoded) > MAX_MESSAGE or not decoder.eof or decoder.unused_data:
                raise ValueError("invalid compressed serial message")
            value = json.loads(decoded)
            if not isinstance(value, dict):
                raise ValueError("serial message must be an object")
            if len(self.seen) >= 1_000_000:
                raise RuntimeError("serial session exhausted its message IDs")
            self.seen.add(ident)
            del self.pending[ident]
            self.incoming.put((ident, value))
        self._write(frame("a", ident, index))

    def _receive(self):
        buffer = bytearray()
        try:
            while not self.stopped.is_set():
                buffer.extend(self.read())
                while b"\n" in buffer:
                    line, _, rest = buffer.partition(b"\n")
                    buffer = bytearray(rest)
                    try:
                        self._packet(line)
                    except (ValueError, UnicodeError, zlib.error):
                        # Console output or damage: no ACK, so the sender retries
                        # this fragment. Nothing unvalidated reaches dispatch.
                        continue
                if len(buffer) > 4096:
                    buffer.clear()
        except BaseException as error:
            if not self.stopped.is_set():
                self.error = error
                self.incoming.put(error)
                for ack in list(self.acks.values()):
                    ack.set()

    def receive(self, timeout=None):
        value = self.incoming.get(timeout=timeout)
        if isinstance(value, BaseException):
            raise ConnectionError("serial reader stopped") from value
        return value

    def close(self):
        self.stopped.set()
        self.reader.join(timeout=1)
