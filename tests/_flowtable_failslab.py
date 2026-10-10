"""Shared support for flowtable failslab."""

from __future__ import annotations

import asyncio
import gzip
import json
import time
from contextlib import AsyncExitStack, asynccontextmanager
from pathlib import Path

import pytest
from _flowtable_rig import console_command, console_python, read
from _flowtable_service import FAULT_DIR, service_status, supervision_status

# Where a fault lease lives for a caller without the service fixture.
SLAB_FAULT_DIR = "/tmp/ask-flowtable-slab-fault"


async def wait_json(r, path, timeout=25):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = await r.target.fs_read(r.session, path)
        if result["errno"] == 0:
            return json.loads(bytes.fromhex(result["content_hex"]))
        assert result["errno"] == 2, result
        await asyncio.sleep(0.05)
    pytest.fail(f"failslab guard did not publish {path}")


class Fault:
    def __init__(self, r, target, label, root, continuous=False):
        self.r, self.target, self.label, self.root = r, target, label, root
        self.result = None
        self.continuous = continuous

    async def hit(self):
        if self.result is None:
            self.result = await wait_json(self.r, self.root + "/result.json")
            self.r.record(self.label + "-injection", self.result)
        result = self.result
        assert result["consumed"] and "error" not in result, result
        assert not result["restore_errors"] and result["restored"] == result["original"], result
        if self.continuous:
            # __GFP_NOWARN sites suppress even failslab's stack diagnostic.
            # The scoped injector's finite budget records every consumed hit.
            assert result["failures"] > 0, result
            return result
        log = "".join(result["kernel_records"])
        assert "FAULT_INJECTION: forcing a failure" in log and "name failslab" in log, log
        # times=1 is a limit, not an atomic reservation across CPUs. Prove
        # that this run consumed exactly one failure in the selected path.
        failures = log.count("name failslab,")
        assert failures >= 1, log
        assert failures == 1, log
        assert result["selected"]["name"] + "+" in log, log
        if self.target == "callback":
            assert "ft_block_setup" in log, "fault missed the flowtable binding path"
        if self.target == "binding":
            assert "flow_block_cb_alloc" not in log, "fault hit the callback allocation, not the binding's own"
        if self.target == "actions":
            assert "flow_offload_work" in log, "fault missed native flow admission"
        return result


@asynccontextmanager
async def _streaming(keep_alive):
    """Traffic on established flows while the guard is staged and armed.

    Staging the guard over the UART takes most of the flowtable's 30 s idle
    timeout, and a hardware flow's timeout moves only with its own packets.
    A caller that later proves those flows untouched needs them to stay alive
    across the setup; otherwise Linux expires them, correctly, and they return
    under new cookies before the fault has done anything."""
    if not keep_alive:
        yield
        return
    peer, ids = keep_alive
    await peer.rpc("start", ids, count=0, interval=1.0)
    try:
        yield
    finally:
        await peer.rpc("stop", ids)


@asynccontextmanager
async def slab_fault(r, target, label, *, continuous=False, lease=20, console=None,
                     keep_alive=None):
    # The service fixture makes and removes FAULT_DIR, and checks that it starts
    # without one. Any other caller's lease lives under a directory of its
    # own, made here and removed whole once the result is read. keep_alive is
    # (peer, ids): flows to keep sending on until the fault is armed.
    base = FAULT_DIR if console is None else SLAB_FAULT_DIR
    console = console or r.service_console
    config = await r.target.fs_read(r.session, "/proc/config.gz")
    assert config["errno"] == 0, config
    config = gzip.decompress(bytes.fromhex(config["content_hex"])).decode()
    for option in ("CONFIG_KASAN=y", "CONFIG_FAILSLAB=y", "CONFIG_FAULT_INJECTION_STACKTRACE_FILTER=y"):
        assert option in config.splitlines(), f"rebuild/stage a KASAN image with {option}"
    script = Path(__file__).with_name("_flowtable_failslab_guard.py").read_text()
    root = base + "/failslab-" + target
    keep = AsyncExitStack()
    await keep.enter_async_context(_streaming(keep_alive))
    try:
        # Stage and verify the guard before entering the measured window.
        if base != FAULT_DIR:
            await console_command(console, "mkdir", "-p", base)
        await console_command(console, "mkdir", root)
        staged = await r.target.fs_write(r.session, root + "/guard.py", script)
        assert staged["errno"] == 0, staged
        assert await read(r.target, r.session, root + "/guard.py") == script
    except BaseException:
        await keep.aclose()
        raise
    fault = Fault(r, target, label, root, continuous)
    try:
        # Launch can succeed even when its UART acknowledgement is lost.
        # Keep cancellation protected from the moment the child may exist.
        await console_python(console, f'''
from pathlib import Path
import subprocess
root = Path({root!r})
script = root / 'guard.py'
with (root / 'guard.log').open('w') as log:
    child = subprocess.Popen(['/usr/bin/python3', str(script), str(root), {target!r},
                              {'continuous' if continuous else 'once'!r}, {str(lease)!r}],
                             stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                             close_fds=True, start_new_session=True)
(root / 'pid').write_text(str(child.pid))
''')
        armed = await wait_json(r, root + "/armed.json", timeout=5)
        r.record(label + "-armed", armed)
        # Armed: the measured window starts with every kept flow fresh.
        await keep.aclose()
        yield fault
    except BaseException:
        await keep.aclose()
        raise
    finally:
        # Only remove our fault. A missing result is a failed guard, never
        # permission to repair flowtables or discard the failed observation.
        try:
            cancelled = await r.target.fs_write(r.session, root + "/cancel", "")
            assert cancelled["errno"] == 0, cancelled
        except Exception:
            # A failed file operation still permits the shell cleanup command.
            await console_command(console, "touch", root + "/cancel")
        try:
            result = await wait_json(r, root + "/result.json", timeout=5)
            r.record(label + "-guard-final", result)
            assert not result["restore_errors"] and result.get("restored") == result.get("original"), result
            # A continuous lease's result exists only now; Fault.hit() reads
            # it from here once the root is gone.
            fault.result = fault.result or result
            await console_command(console, "rm", "-rf", root if base == FAULT_DIR else base)
        except BaseException:
            await console_python(console, f'''
from pathlib import Path
import json
root = Path({root!r})
saved = root / 'original.json'
if saved.exists():
    knobs = Path('/sys/kernel/debug/failslab')
    (knobs / 'probability').write_text('0')
    for name, value in json.loads(saved.read_text()).items():
        if name != 'probability': (knobs / name).write_text(value)
''')
            raise


async def same_service(r, before):
    assert await supervision_status(r) == before
    assert (await read(r.target, r.session, "/proc/sys/kernel/random/boot_id")).strip() == r.service_boot
    status = await service_status(r)
    assert status["admission_ready"] and not status["reconciliation_paused"], status
