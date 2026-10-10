"""UART entry point. Shell/script operations exist only on the root console."""

import asyncio
import base64
import hashlib
import json
import os
from pathlib import Path
import queue
import signal
import sys
import tempfile
import termios
import time
import tty

from . import agent
from .wire import Channel, VERSION


async def execute(argv, timeout):
    started = time.monotonic()
    child = await asyncio.create_subprocess_exec(
        *argv, stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        start_new_session=True)
    try:
        stdout, stderr = await asyncio.wait_for(child.communicate(), timeout)
    except BaseException:
        try:
            os.killpg(child.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        await child.wait()
        raise
    ended = time.monotonic()
    return {"rc": child.returncode, "stdout": stdout.decode(errors="replace"),
            "stderr": stderr.decode(errors="replace"),
            "at": (started + ended) / 2, "span": ended - started}


class Runtime:
    def __init__(self, directory):
        self.root = Path(directory)
        self.state = {"captures": {}, "serial": True, "root": self.root}
        self.boot = Path("/proc/sys/kernel/random/boot_id").read_text().strip()
        self.artifacts = {}
        self.artifact_bytes = 0
        self.probes = {}
        self.script_bytes = 0

    def retain(self, value):
        data = json.dumps(value, separators=(",", ":")).encode()
        # Never silently evict evidence. The caller gets a failed operation if
        # this run exhausts its local diagnostic budget.
        if self.artifact_bytes + len(data) > 128 << 20:
            raise RuntimeError("DUT diagnostic budget exhausted; collect artifacts before continuing")
        ident = hashlib.sha256(data).hexdigest()
        if ident not in self.artifacts:
            path = self.root / ident
            path.write_bytes(data)
            self.artifacts[ident] = path
            self.artifact_bytes += len(data)
        return {"id": ident, "size": len(data), "sha256": ident}

    async def dispatch(self, operation, body):
        if operation == "shell":
            return await execute(["/bin/sh", "-c", body["command"]], body.get("timeout", 20))
        if operation == "python":
            ident = body["sha256"]
            if len(ident) != 64 or any(c not in "0123456789abcdef" for c in ident):
                raise ValueError("invalid script digest")
            path = self.root / (ident + ".py")
            if "source" in body:
                data = body["source"].encode()
                if len(data) > 1 << 20 or hashlib.sha256(data).hexdigest() != ident:
                    raise ValueError("script digest or size mismatch")
                if not path.exists():
                    if self.script_bytes + len(data) > 16 << 20:
                        raise ValueError("session script cache exhausted")
                    path.write_bytes(data)
                    self.script_bytes += len(data)
            if not path.exists():
                raise ValueError("script is not staged in this session")
            return await execute([sys.executable, str(path)], body.get("timeout", 20))
        if operation == "artifact/read":
            path = self.artifacts[body["id"]]
            offset = int(body.get("offset", 0))
            if not 0 <= offset <= path.stat().st_size:
                raise ValueError("invalid artifact offset")
            with path.open("rb") as stream:
                stream.seek(offset)
                data = stream.read(16384)
            return {"data": base64.b64encode(data).decode(), "size": path.stat().st_size}
        if operation == "artifact/release":
            path = self.artifacts.pop(body["id"])
            self.artifact_bytes -= path.stat().st_size
            path.unlink()
            return {"ok": True}
        if operation == "probe/start":
            # A traffic-test endpoint only: it accepts no commands or data.
            if self.probes:
                raise ValueError("a TCP probe is already running")
            def connected(reader, writer):
                writer.close()
            server = await asyncio.start_server(connected, body["address"], 0)
            port = server.sockets[0].getsockname()[1]
            self.probes[port] = server
            return {"port": port}
        if operation == "probe/stop":
            server = self.probes.pop(body["port"])
            server.close()
            await server.wait_closed()
            return {"ok": True}
        if operation == "observe/artifacts":
            store = self.state.get("snapshots")
            result = []
            if store:
                for ident in sorted(store.headers)[-2:]:
                    path = self.root / f"snapshot-{ident}.txt.gz"
                    size = path.stat().st_size
                    with path.open("rb") as stream:
                        digest = hashlib.file_digest(stream, "sha256").hexdigest()
                    if digest not in self.artifacts:
                        if self.artifact_bytes + size > 128 << 20:
                            raise ValueError("DUT diagnostic budget exhausted")
                        self.artifacts[digest] = path
                        self.artifact_bytes += size
                    result.append({"id": digest, "snapshot": ident, "size": size})
            return result
        if operation.startswith("observe/"):
            from .observe import observe
            return await observe(operation.removeprefix("observe/"), body, self.state)
        result = await agent.OPERATIONS[operation](body, self.state)
        if operation in {"capture-stop", "dmesg-delta"}:
            lines = result.pop("dmesg", result.pop("lines", []))
            result["records"] = len(lines)
            if lines:
                result["artifact"] = self.retain({"lines": lines})
        if operation == "health":
            result["serial_protocol"] = VERSION
        return result


async def serve(channel, runtime):
    tasks = set()
    failures = []
    last_request = time.monotonic()

    def completed(task):
        tasks.discard(task)
        if not task.cancelled() and task.exception():
            failures.append(task.exception())

    async def handle(ident, request):
        try:
            timeout = float(request.get("timeout", 30))
            if not 0 < timeout <= 7200:
                raise ValueError("invalid operation timeout")
            async with asyncio.timeout(timeout + 2):
                result = await runtime.dispatch(request["operation"], request.get("body", {}))
            response = {"result": result, "status": 200, "boot": runtime.boot}
        except agent.AgentError as error:
            response = {"result": error.detail, "status": error.status, "boot": runtime.boot}
        except Exception as error:
            response = {"result": {"error": f"{type(error).__name__}: {error}"},
                        "status": 500, "boot": runtime.boot}
        await asyncio.to_thread(channel.send, ident, response)

    try:
        while True:
            if failures:
                raise failures[0]
            try:
                ident, request = await asyncio.to_thread(channel.receive, 1)
            except queue.Empty:
                # Recover the shell after a runner disappears. Long operations
                # keep their explicit deadlines; idle sessions expire in 5 min.
                if not tasks and time.monotonic() - last_request > 300:
                    break
                continue
            last_request = time.monotonic()
            if request.get("operation") == "exit":
                if tasks:
                    await asyncio.gather(*tasks)
                await asyncio.to_thread(channel.send, ident,
                                        {"result": {"ok": True}, "status": 200, "boot": runtime.boot})
                break
            if len(tasks) >= 32:
                raise RuntimeError("too many concurrent UART operations")
            task = asyncio.create_task(handle(ident, request))
            tasks.add(task)
            task.add_done_callback(completed)
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await agent.close_captures(runtime.state)
        if "snapshots" in runtime.state:
            runtime.state["snapshots"].reset()
        for server in runtime.probes.values():
            server.close()
            await server.wait_closed()


def main():
    fd = sys.stdin.fileno()
    saved = termios.tcgetattr(fd) if os.isatty(fd) else None
    printk = Path("/proc/sys/kernel/printk")
    levels = None

    def read():
        import select
        if not select.select([fd], [], [], 0.2)[0]:
            return b""
        data = os.read(fd, 4096)
        if not data:
            raise EOFError("UART closed")
        return data

    def write(data):
        while data:
            data = data[os.write(sys.stdout.fileno(), data):]

    channel = None
    try:
        if saved:
            tty.setraw(fd)
            try:
                original = printk.read_text()
                printk.write_text("1 4 1 7\n")
                levels = original
            except OSError:
                pass
        write(f"@ASK-READY {VERSION}\n".encode())
        channel = Channel(read, write)
        with tempfile.TemporaryDirectory(prefix="ask-uart-") as directory:
            asyncio.run(serve(channel, Runtime(directory)))
    finally:
        if channel:
            channel.close()
        if levels is not None:
            printk.write_text(levels)
        if saved:
            termios.tcsetattr(fd, termios.TCSANOW, saved)
