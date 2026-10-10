"""Shared multicast rig plumbing: capturing on the LAN VM, and the oracles
that tell hardware replication apart from the bridge flooding in software.

LAN commands use the VM's guest-agent channel. Captures run as background
processes coordinated through pidfiles, spanning multiple command calls.
"""

from __future__ import annotations
import asyncio
import json
import shlex
import pytest_asyncio
from ask_orch.lifecycle import checked


# ---- the global switch -----------------------------------------------------

MULTICAST_SWITCH = "/sys/module/ask_flowtable/parameters/multicast"


async def multicast_on(target_agent, session) -> None:
    """Multicast acceleration on, as a boot leaves it.

    The offload service switches it off whenever it stops or its policy is
    disabled, so a case that expects multicast in hardware switches it on
    itself rather than inherit whatever the test before it left.

    It does not stop the boot's service: a case running a service of its own
    calls this too, from the smcroute daemon it starts. A case that owns the
    policy without one stops the boot's service in its own setup
    (stop_boot_daemon())."""
    result = await target_agent.fs_write(session, MULTICAST_SWITCH, "Y")
    assert result["errno"] == 0, (MULTICAST_SWITCH, result)


# ---- the bridge's own querier ----------------------------------------------

async def arm_bridge_querier(run, bridge: str) -> None:
    """Make a new bridge's own querier count, in both families, now.

    Snooping decides nothing without a querier: the bridge floods every group,
    registered or not, to every port and up to the host. And a bridge created
    with `mcast_querier 1` does not count as one for a whole query response
    interval -- ten seconds by default -- while other queriers get the chance
    to answer, nor for IPv6 at all until a query of its own has gone out from a
    usable link-local address. Its first one leaves before the address exists,
    which counts it out until the next startup query, half a minute on.

    The bridged learner asks the bridge what it forwards, so while the bridge
    floods it refuses to carry the group (`refused-host`: the flood reaches
    the host too) rather than install the MDB's ports in its place. Restarting
    the querier once the address is usable, with a one-second response
    interval, makes both families count within two seconds.

    `run(*argv)` executes on the DUT and returns the agent's result dict."""
    for _ in range(100):
        out = await run("ip", "-6", "-j", "addr", "show", "dev", bridge, "scope", "link")
        info = [a for i in json.loads(out.get("stdout") or "[]") for a in i.get("addr_info", [])]
        if info and not any(a.get("tentative") for a in info):
            break
        await asyncio.sleep(0.1)
    # Without an address IPv6 snooping cannot query; IPv4 is armed regardless.
    await run("ip", "link", "set", "dev", bridge, "type", "bridge",
              "mcast_query_response_interval", "100")
    for value in ("0", "1"):
        await run("ip", "link", "set", "dev", bridge, "type", "bridge", "mcast_querier", value)
    await asyncio.sleep(2.0)


# ---- capture ------------------------------------------------------------

_TCPDUMP_PIDFILE_PREFIX = "/tmp/ask_mcast_tcpdump"


@pytest_asyncio.fixture
async def pcap_cleanup_lan(lan):
    """Tracks pcap paths created during a test; rm them on teardown so
    /tmp on the LAN VM doesn't accumulate over many parametrize runs.
    UART has direct shell access — single rm call suffices.
    """
    paths: list[str] = []
    try:
        yield paths
    finally:
        if paths:
            checked(await asyncio.to_thread(lan.run, shlex.join(["rm", "-f", *paths]), 5))


def spawn_parallel_tcpdumps(
    lan, ifaces: list[str], capfiles: list[str], bpf: str,
) -> None:
    """Launch one backgrounded tcpdump per (iface, capfile) on the LAN VM and
    return once every one of them is capturing.

    Plain `tcpdump -w file &` (no -G/-W) so behaviour is portable across
    tcpdump/libpcap versions. Each tcpdump's PID goes into a sidecar file so
    the later kill is precise instead of `pkill -f tcpdump`-broad.

    A single command chains all spawns, so the round-trip is one shot
    regardless of N — and, more importantly, the captures start together.
    tcpdump prints "listening on" once its filter is attached, and the same
    command waits for that line from each one: a fixed grace after the spawn
    can let the start of a stream go uncaptured.

    `--immediate-mode -U` keeps the end of a stream too. Without it libpcap's
    ring hands packets over only when a block fills or its ~1 s timeout
    retires it, and a SIGTERM shortly after the stream discards whatever the
    open block holds.
    """
    chain = ""
    logs = []
    for iface, capfile in zip(ifaces, capfiles):
        pidfile = f"{_TCPDUMP_PIDFILE_PREFIX}_{iface}.pid"
        log = f"{_TCPDUMP_PIDFILE_PREFIX}_{iface}.log"
        logs.append(log)
        # `-Q in` records inbound only — excludes the egress copy AF_PACKET
        # would otherwise capture, so the count is what arrived at the
        # listener rather than what the listener also sent.
        chain += (
            f"nohup tcpdump -i {iface} -Q in --immediate-mode -U -w {capfile} '{bpf}' "
            f"</dev/null >/dev/null 2>{log} & "
            f"echo $! > {pidfile}; "
        )
    chain += (
        f"for i in $(seq 200); do ready=1; "
        f"for log in {' '.join(logs)}; do "
        f"grep -q 'listening on' $log || ready=0; done; "
        f"[ $ready = 1 ] && break; sleep 0.05; done; "
        f"[ $ready = 1 ] && echo SPAWNED || cat {' '.join(logs)}"
    )
    r = lan.run(chain, timeout=20)
    assert "SPAWNED" in r.stdout, (
        f"tcpdumps not capturing within 10 s: rc={r.rc}, out={r.stdout!r}"
    )


def kill_parallel_tcpdumps(lan, ifaces: list[str]) -> None:
    """SIGTERM the previously-spawned tcpdumps via their pid sidecars."""
    chain = ""
    for iface in ifaces:
        pidfile = f"{_TCPDUMP_PIDFILE_PREFIX}_{iface}.pid"
        # A missing pidfile or an already-dead pid must not fail the chain.
        chain += (
            f"[ -f {pidfile} ] && kill -TERM $(cat {pidfile}) "
            f"2>/dev/null; rm -f {pidfile} {_TCPDUMP_PIDFILE_PREFIX}_{iface}.log; "
        )
    chain += "echo KILLED"
    r = lan.run(chain, timeout=10)
    assert "KILLED" in r.stdout, (
        f"failed to kill tcpdumps: rc={r.rc}, out={r.stdout!r}"
    )


def read_pcap_count(lan, capfile: str, *, match: str = "UDP") -> int:
    """Parse a saved pcap via `tcpdump -r`; count summary lines matching."""
    r = lan.run(f"tcpdump -r {capfile} -nn 2>&1", timeout=10)
    if r.rc != 0:
        raise AssertionError(
            f"tcpdump -r {capfile} failed: rc={r.rc}, out={r.stdout!r}"
        )
    return sum(1 for ln in r.stdout.splitlines() if match in ln)


async def capture_parallel_window(
    lan, *, ifaces: list[str], capfiles: list[str], bpf: str,
    window_s: float = 2.0, match: str = "UDP",
) -> dict[str, int]:
    """Spawn parallel tcpdumps, sleep through the window, kill them, read the
    pcaps, return per-iface counts. Pure UART.

    The caller injects AFTER this returns from its start grace, not before.
    """
    try:
        spawn_parallel_tcpdumps(lan, ifaces, capfiles, bpf)
        await asyncio.sleep(window_s)
    finally:
        kill_parallel_tcpdumps(lan, ifaces)
    # Tiny pause for the pcap writer to flush on TERM.
    await asyncio.sleep(0.2)
    return {
        iface: read_pcap_count(lan, cf, match=match)
        for iface, cf in zip(ifaces, capfiles)
    }
