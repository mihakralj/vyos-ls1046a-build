"""A classifier delete CDX cannot prove stops the datapath, and CDX restarts it.

A delete that fails before its unlink leaves a key the FMan may still walk to.
CDX latches the failure, stops every classifier port and, once they are idle,
settles the key -- deleting it again or finding it in no bucket -- and starts
the ports again, all in the same boot. The unicast, multicast and IPsec cases
each fail one delete of their own kind (the test image's knobs) and hand the
stopped window to these helpers, which prove it and then the restart.

The test image's flowtable_restart_hold keeps the ports stopped, with every key
recorded, until it is cleared: that is the window a case inspects. The stopped
ports take the management path with them, so everything inside the window
goes over the DUT's UART; the agent is used again once the ports run.

CDX allows three restarts in ten minutes and then leaves the latch for a
reboot. The cases between them restart more often than that, and a rerun in
the same boot more again, so each runs inside restart_budget(), which raises
the limit for the case and puts it back after.
"""
from __future__ import annotations
import asyncio
from contextlib import asynccontextmanager
import json
import re
import shlex
import time
import pytest
from _flowtable_rig import (CONSOLE_NOISE, RX_PORTS_CODE, RX_PORTS_SCRIPT, console_command, console_json, console_python, status_text)

HOLD = "/sys/module/cdx/parameters/flowtable_restart_hold"
LIMIT = "/sys/module/cdx/parameters/flowtable_restart_limit"
UNICAST_FAULT = "/sys/module/cdx/parameters/flowtable_fail_unlink"
ROOT_FAULT = "/sys/module/cdx/parameters/ehash_fail_unlink"
RESTARTED = "datapath restarted after unproven deletion"
TERMINAL = "reboot required"
UNSTARTED = "did not start again"
KASAN = "BUG: KASAN"
# How long a restart with nothing holding it may take, from the failed delete
# to the receive ports running again.
RESTART_BOUND = 2.0
RUNNING = {"6": 1, "7": 1}
STOPPED = {"6": 0, "7": 0}
# The budget a case restarts under: more than any case or rerun spends in one
# window. Never 0, which leaves every latch for a reboot.
CASE_RESTART_LIMIT = 1000


async def require_knobs(agent, session, *paths):
    """The fault-injection image's knobs, read over the agent while it still
    reaches the DUT. A production image has none, and these cases are then
    misconfigured rather than skipped."""
    for path in (HOLD, *paths):
        present = await agent.fs_read(session, path)
        if present["errno"]:
            pytest.fail(f"{path} is missing: this is not the fault-injection test image")


async def proc(console) -> dict:
    """The adapter's header over the UART, less any line another writer --
    the managed service, the kernel -- dropped into it."""
    text = (await console_command(console, "cat", "/proc/cdx_flowtable"))["stdout"]
    return status_text(CONSOLE_NOISE.sub("", text).strip())


async def ports(console) -> dict:
    return console_json((await console_python(console, RX_PORTS_SCRIPT))["stdout"])


async def write(console, path, value):
    await console_command(console, "sh", "-c", f"echo {shlex.quote(str(value))} > {shlex.quote(path)}")


async def knob(console, path) -> str:
    return (await console_command(console, "cat", path))["stdout"].strip()


async def dmesg_count(console, text) -> int:
    """How many kernel log lines carry `text`, counted on the DUT: the whole
    log of a KASAN image is slow at 115200 baud."""
    out = (await console_command(console, "sh", "-c",
                                 f"dmesg | grep -c -F -- {shlex.quote(text)} || true"))["stdout"]
    counts = [line for line in out.splitlines() if re.fullmatch(r"\d+", line.strip())]
    assert counts, out
    return int(counts[-1])


async def log_marks(console) -> dict:
    """What the kernel log says so far, to compare a case's own lines with."""
    return {text: await dmesg_count(console, text) for text in (RESTARTED, TERMINAL, UNSTARTED, KASAN)}


async def wait_stopped(console, before: dict, timeout: float = 15) -> tuple[dict, dict]:
    """The latch holds and the receive ports are disabled, and CDX means to
    restart: not terminal, and no restart yet."""
    deadline = time.monotonic() + timeout
    while True:
        state = await proc(console)
        rx = await ports(console)
        if state["fatal"] == 1 and rx == STOPPED:
            break
        assert time.monotonic() < deadline, (state, rx)
        await asyncio.sleep(0.2)
    assert state["fatal_terminal"] == 0 and state["restarts"] == before["restarts"], (before, state)
    return state, rx


async def latch_cleared(console, timeout: float = 10) -> bool:
    """Whether CDX has no restart pending, waiting up to `timeout` for one to
    finish: the latch clear in the adapter's /proc. A case that failed with
    the adapter unloaded gets it loaded again, which the latch refuses until
    it clears."""
    deadline = time.monotonic() + timeout
    while True:
        present = await console_command(console, "test", "-e", "/proc/cdx_flowtable", check=False)
        if present["rc"]:
            await console_command(console, "modprobe", "ask_flowtable", check=False, timeout=30)
        else:
            state = await proc(console)
            if not state["fatal"]:
                return True
            if state["fatal_terminal"]:
                return False
        if time.monotonic() >= deadline:
            return False
        await asyncio.sleep(0.25)


@asynccontextmanager
async def quiet_console(console):
    """Keep the kernel's warnings off the console for a case: the stop and
    the restart are reported from a worker, and a line landing inside a
    console read corrupts it. dmesg keeps every line for the assertions; the
    levels are put back after."""
    printk = (await console_command(console, "cat", "/proc/sys/kernel/printk"))["stdout"].split()
    await console_command(console, "sysctl", "-w", "kernel.printk=1 4 1 7")
    try:
        yield
    finally:
        await console_command(console, "sysctl", "-w", "kernel.printk=" + " ".join(printk[:4]))


@asynccontextmanager
async def restart_budget(console, owner=None, faults: tuple[str, ...] = ()):
    """Restart under a budget no case spends, and put the boot's own back
    after, whatever happened.

    On the way out the hold and every fault knob named are cleared first --
    a case that failed inside its window must not leave the ports stopped or
    a delete armed to fail. The boot's limit comes back only once the latch
    has cleared: CDX checks the limit on every attempt, and a restart still
    pending would find a budget the suite has long spent and stop the ports
    for good. One that has not cleared leaves the limit raised and says so.
    Everything goes over the console, which reaches the DUT while the ports
    are stopped. The limit to put back is left on `owner` until it is, for a
    fixture that restores what this could not."""
    saved = await knob(console, LIMIT)
    assert re.fullmatch(r"\d+", saved), saved
    if owner is not None:
        owner.restart_limit = saved
    await write(console, LIMIT, CASE_RESTART_LIMIT)
    failed = False
    try:
        yield saved
    except BaseException:
        failed = True
        raise
    finally:
        for path in (HOLD, *faults):
            await write(console, path, 0)
        if await latch_cleared(console):
            await write(console, LIMIT, saved)
            if owner is not None:
                owner.restart_limit = None
        elif not failed:
            pytest.fail("the datapath has not restarted; CDX's restart limit is left raised")


async def assert_port_start_refused(console, devs):
    """No port opens under the latch: the netdev's own open is refused, and
    the port stays stopped. Each is left down, for the case to bring up once
    the datapath has restarted."""
    refused = []
    for dev in devs:
        await console_command(console, "ip", "link", "set", "dev", dev, "down")
        start = await console_command(console, "ip", "link", "set", "dev", dev, "up", check=False)
        assert start["rc"] != 0 and "Input/output error" in start["stdout"], start
        link = json.loads((await console_command(console, "ip", "-j", "link", "show", "dev", dev))["stdout"])[0]
        assert "UP" not in link["flags"], link
        assert await ports(console) == STOPPED
        refused.append({"dev": dev, "start": start})
    return refused


async def wait_restarted(console, before: dict, *, adapter: bool = True, timeout: float = 10) -> dict:
    """Clear the hold and wait for CDX to restart: the latch gone, one more
    restart counted, and every port it stopped started again. Without the
    adapter there is no /proc to ask, so the adapter's own load is the probe:
    refused while the latch holds."""
    await write(console, HOLD, 0)
    deadline = time.monotonic() + timeout
    while True:
        if not adapter:
            loaded = await console_command(console, "modprobe", "ask_flowtable", check=False, timeout=30)
            if loaded["rc"] == 0:
                adapter = True
                continue
            assert "Operation not supported" in loaded["stdout"], loaded
        else:
            state = await proc(console)
            if not state["fatal"]:
                break
        assert time.monotonic() < deadline, "the datapath did not restart"
        await asyncio.sleep(0.2)
    assert state["restarts"] == before["restarts"] + 1 and not state["fatal_terminal"], (before, state)
    assert state["resume_failures"] == before["resume_failures"], (before, state)
    return state


async def wait_running(console, timeout: float = 5) -> dict:
    deadline = time.monotonic() + timeout
    while True:
        rx = await ports(console)
        if rx == RUNNING:
            return rx
        assert time.monotonic() < deadline, rx
        await asyncio.sleep(0.2)


async def assert_restarted_cleanly(console, marks: dict, restarts: int = 1) -> str:
    """One restart line per restart, nothing that says reboot, no port left
    unstarted, no KASAN report, and the restart line itself for what it
    settled."""
    now = await log_marks(console)
    assert now[RESTARTED] == marks[RESTARTED] + restarts, (marks, now)
    assert now[TERMINAL] == marks[TERMINAL], (marks, now)
    assert now[UNSTARTED] == marks[UNSTARTED], (marks, now)
    assert now[KASAN] == marks[KASAN], (marks, now)
    line = (await console_command(console, "sh", "-c",
                                  f"dmesg | grep -F -- {shlex.quote(RESTARTED)} | tail -n 1"))["stdout"]
    restart_counts(line)
    return line


def restart_counts(line: str) -> tuple[int, int, int]:
    match = re.search(r"\((\d+) keys resolved, (\d+) FQID ranges released, stopped (\d+) ms\)", line)
    assert match, line
    return int(match[1]), int(match[2]), int(match[3])


# Fails one unicast delete and times the restart on the DUT itself, so the UART
# is no part of the measurement: from the delete's own command to the latch
# gone, one more restart counted and both receive ports running again.
TIMED_RESTART = '''
import json, subprocess, time
def proc():
    fields = {{}}
    for line in open('/proc/cdx_flowtable'):
        parts = line.split()
        if len(parts) == 2 and parts[1].isdigit():
            fields[parts[0]] = int(parts[1])
    return fields
dumps = rx_port_dumps()
first = proc()
before = first['restarts']
open({fault!r}, 'w').write('1')
start = time.monotonic()
subprocess.run({trigger!r}, check=True)
latched = False
while time.monotonic() - start < 10:
    state, rx = proc(), rx_ports_enabled(dumps)
    latched |= bool(state['fatal'])
    if not state['fatal'] and state['restarts'] == before + 1 and rx == {{'6': 1, '7': 1}}:
        break
    time.sleep(0.002)
print(json.dumps({{'seconds': time.monotonic() - start, 'before': before, 'restarts': state['restarts'],
                  'fatal': state['fatal'], 'ports': rx, 'latched': latched,
                  'resume_failures': state['resume_failures'] - first['resume_failures']}}))
'''


async def timed_restart(console, trigger: list[str], fault: str = UNICAST_FAULT) -> dict:
    """A restart nothing holds: within RESTART_BOUND of the failed delete."""
    result = console_json((await console_python(console, RX_PORTS_CODE + TIMED_RESTART.format(
        fault=fault, trigger=trigger), timeout=30))["stdout"])
    assert result["restarts"] == result["before"] + 1 and not result["fatal"], result
    assert result["ports"] == RUNNING and result["resume_failures"] == 0, result
    assert result["seconds"] <= RESTART_BOUND, result
    return result
