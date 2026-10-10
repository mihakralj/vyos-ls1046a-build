"""Session-owned framed agent channels over SSH stdio or a recovery UART."""

import hashlib
import itertools
import os
import queue
import select
import struct
import subprocess
import sys
import threading
import time

from askd_agent.wire import Channel, VERSION
from .uart import RunResult


class SerialSession:
    def __init__(self, console, *, launch=True):
        self.console = console
        self.sequence = itertools.count(1)
        self.pending = {}
        self.scripts = set()
        self.boot = None
        self.error = None
        self.stopped = threading.Event()
        if launch:
            console.login("root", None)
            result = console.run("python3 -c 'from askd_agent.wire import VERSION; print(VERSION)'", timeout=10)
            if result.rc or str(VERSION) not in result.stdout.splitlines():
                raise RuntimeError("DUT needs the UART test agent; rebuild and boot the current image: " + result.stdout)
            console.send("python3 -m askd_agent --stdio\n")
            console.ser.timeout = 0.2
            deadline = time.monotonic() + 15
            banner = bytearray()
            while f"@ASK-READY {VERSION}\n".encode() not in banner:
                if time.monotonic() >= deadline:
                    raise TimeoutError("DUT UART agent did not become ready")
                data = console.ser.read(max(1, min(4096, console.ser.in_waiting)))
                banner.extend(data)
                if console.log_fp and data:
                    console.log_fp.write(b"<RECV>" + data + b"</RECV>\n")
        console.ser.timeout = 0.2

        def read():
            data = console.ser.read(max(1, min(4096, console.ser.in_waiting)))
            if data and console.log_fp:
                console.log_fp.write(b"<RECV>" + data + b"</RECV>\n")
            return data

        self.channel = Channel(read, console.send)
        self.reader = threading.Thread(target=self._responses, daemon=True)
        self.reader.start()

    def _responses(self):
        try:
            while not self.stopped.is_set():
                try:
                    ident, response = self.channel.receive(timeout=0.2)
                except queue.Empty:
                    continue
                pending = self.pending.get(ident)
                if pending:
                    pending.put(response)
        except BaseException as error:
            self.error = error
            for pending in list(self.pending.values()):
                pending.put(error)

    def request(self, operation, body=None, timeout=30, check=True):
        if self.error:
            raise ConnectionError("DUT UART reader failed") from self.error
        ident = next(self.sequence)
        pending = self.pending[ident] = queue.Queue()
        try:
            self.channel.send(ident, {"operation": operation, "body": body or {}, "timeout": timeout})
            try:
                response = pending.get(timeout=timeout + 15)
            except queue.Empty as error:
                raise TimeoutError(f"DUT {operation}: result missing; operation outcome unknown") from error
            if isinstance(response, BaseException):
                raise ConnectionError("DUT UART failed") from response
            if self.boot is None:
                self.boot = response["boot"]
            if self.boot != response["boot"]:
                raise RuntimeError("DUT rebooted during the UART session")
            if check and response["status"] != 200:
                raise RuntimeError(f"DUT {operation}: {response['result']}")
            return response["result"]
        finally:
            self.pending.pop(ident, None)

    def run(self, command, timeout=20):
        result = self.request("shell", {"command": command, "timeout": timeout}, timeout + 2)
        return RunResult(command, result["stdout"] + result["stderr"], result["rc"])

    def python(self, source, timeout=20):
        ident = hashlib.sha256(source.encode()).hexdigest()
        body = {"sha256": ident, "timeout": timeout}
        if ident not in self.scripts:
            body["source"] = source
        result = self.request("python", body, timeout + 2)
        self.scripts.add(ident)
        return {**result, "stdout": result["stdout"] + result["stderr"]}

    def close(self):
        try:
            self.request("exit", timeout=10)
        finally:
            self.stopped.set()
            self.channel.close()
            self.reader.join(timeout=1)


class SSHSession(SerialSession):
    def __init__(self, endpoint, *, local=False, log_path=None):
        from .lab import SUITE_ROOT, source_archive

        bootstrap = '''
import io, os, struct, sys, tempfile, zipfile
size = struct.unpack("!I", sys.stdin.buffer.read(4))[0]
if size > 16 << 20:
    raise ValueError("agent archive too large")
payload = sys.stdin.buffer.read(size)
with tempfile.TemporaryDirectory(prefix="ask-tests-agent-") as root:
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        archive.extractall(root)
    sys.path.insert(0, root)
    os.environ["PYTHONPATH"] = root
    sys.argv = ["askd_agent", "--stdio"]
    from askd_agent.agent import main
    main()
'''
        payload = source_archive(SUITE_ROOT / "askd_agent", prefix="askd_agent")
        argv = ([sys.executable, "-u", "-c", bootstrap] if local else
                endpoint.command(["python3", "-u", "-c", bootstrap]))
        self.log_fp = open(log_path, "ab", buffering=0) if log_path else None
        self.process = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                        stderr=self.log_fp or subprocess.DEVNULL, bufsize=0)

        def write(data):
            remaining = memoryview(data)
            while remaining:
                written = self.process.stdin.write(remaining)
                if not written:
                    raise EOFError("SSH agent stdin closed")
                remaining = remaining[written:]

        def read():
            ready, _, _ = select.select([self.process.stdout], [], [], 0.2)
            if not ready:
                return b""
            data = os.read(self.process.stdout.fileno(), 4096)
            if not data:
                raise EOFError("SSH agent stdout closed")
            return data

        try:
            write(struct.pack("!I", len(payload)) + payload)
            self.sequence = itertools.count(1)
            self.pending = {}
            self.scripts = set()
            self.boot = None
            self.error = None
            self.stopped = threading.Event()
            self.channel = Channel(read, write)
            self.reader = threading.Thread(target=self._responses, daemon=True)
            self.reader.start()
        except BaseException:
            self._close_process()
            raise

    def _close_process(self):
        self.process.stdin.close()
        try:
            self.process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=5)
        finally:
            self.process.stdout.close()
            if self.log_fp:
                self.log_fp.close()

    def close(self):
        try:
            super().close()
        finally:
            self._close_process()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


class AgentConsole:
    """Borrow the existing session; closing a test's handle keeps it alive."""

    def __init__(self, session):
        self.session = session

    def login(self, *args, **kwargs):
        pass

    def sync_prompt(self, *args, **kwargs):
        pass

    def run(self, command, timeout=20):
        return self.session.run(command, timeout)

    def python(self, source, timeout=20):
        return self.session.python(source, timeout)

    def close(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
