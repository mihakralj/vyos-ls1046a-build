"""Stress Linux IPv4 fragment handling while normal ASK forwarding continues.

Mono sends fragmented traffic through the software path. These tests exercise
out-of-order and duplicate fragments, with sanitizer and allocation checks;
they do not exercise the removed SDK/CDX hardware-reassembly implementation.
The concurrent iperf variant also checks that normal forwarding remains live.
"""

from __future__ import annotations

import asyncio
import os
import socket
import textwrap

import pytest

from _topology import lan_run, lan_run_python

# Limit kmemleak matching to the software fragment path. Hardware-owned DPAA
# buffers are invisible to its pointer scanner and produce unrelated reports.
FRAGMENT_LEAK_FILTER = [
    "inet_frag_alloc", "inet_frag_create", "ip_frag_queue", "ip_frag_reasm", "ip_defrag",
]


WAN_IPERF_IP = os.environ.get("ASK_WAN_IPERF_IP", "")

# Interleave many datagrams across flows, with several fragments per datagram.
# The shuffled and duplicate variants exercise incomplete queues and expiry.
N_PACKETS       = 1500
N_SOURCE_PORTS  = 150
UDP_SPORT_BASE  = 30000
UDP_DPORT       = 5201          # wan iperf3 server
PKT_MIN_BYTES   = 2000
PKT_MAX_BYTES   = 5000
FRAGSIZE        = 500


def _storm_script(duplicate: bool) -> str:
    """Return the scapy script text; runs as root on lan."""
    return textwrap.dedent(f"""
        import random, sys
        from scapy.all import IP, UDP, Raw, fragment, send
        random.seed(0xA5C03E)
        all_frags = []
        for i in range({N_PACKETS}):
            sport = {UDP_SPORT_BASE} + (i % {N_SOURCE_PORTS})
            size  = random.randint({PKT_MIN_BYTES}, {PKT_MAX_BYTES})
            pkt   = IP(dst="{WAN_IPERF_IP}") / UDP(sport=sport, dport={UDP_DPORT}) \\
                    / Raw(b"X" * size)
            frags = fragment(pkt, fragsize={FRAGSIZE})
            if {int(bool(duplicate))}:
                # Interleave the duplicate after the original so the
                # list contains two copies of every fragment before shuffling.
                doubled = []
                for f in frags:
                    doubled.append(f)
                    doubled.append(f)
                frags = doubled
            all_frags.extend(frags)
        # Shuffle across the whole storm so different contexts interleave;
        # this creates many simultaneously incomplete datagrams.
        random.shuffle(all_frags)
        # Batch-send to keep per-packet sendto() overhead manageable.
        send(all_frags, verbose=0, inter=0)
        print("STORM_DONE n_frags=%d" % len(all_frags))
    """).strip()


@pytest.mark.rfc("791", section="3.2")
async def test_reassembly_delivers(lan):
    """Shuffled fragments of many datagrams arrive at the far end whole: what
    the DUT reassembles and fragments again is byte for byte what was sent."""
    sizes = [2000 + 37 * i for i in range(80)]
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    # Read only once all is sent: the buffer holds every datagram.
    sock.setsockopt(socket.SOL_SOCKET, 33, 8 << 20)  # SO_RCVBUFFORCE
    sock.bind((WAN_IPERF_IP, 5203))
    sock.setblocking(False)
    payload = lambda i: i.to_bytes(4, "big") + bytes((i * 7 + k) % 251 for k in range(sizes[i] - 4))
    script = textwrap.dedent(f"""
        import random
        from scapy.all import IP, UDP, Raw, fragment, send
        sizes = {sizes!r}
        frags, shuffle = [], random.Random(791).shuffle
        for start in range(0, len(sizes), 5):
            # Shuffled five datagrams at a time: Linux drops a queue that
            # more than net.ipv4.ipfrag_max_dist (64) of the same host's
            # fragments overtook, and five never exceed 55.
            group = []
            for i in range(start, min(start + 5, len(sizes))):
                data = i.to_bytes(4, "big") + bytes((i * 7 + k) % 251 for k in range(sizes[i] - 4))
                group += fragment(IP(dst="{WAN_IPERF_IP}", id=0x791 + i) / UDP(sport=30500, dport=5203) / Raw(data), 500)
            shuffle(group)
            frags += group
        send(frags, verbose=0)
    """)
    try:
        result = await lan_run_python(lan, script, label="fragment_delivery", timeout=120)
        assert result.rc == 0, result.stdout
        received, loop = {}, asyncio.get_running_loop()
        while len(received) < len(sizes):
            try:
                data = await asyncio.wait_for(loop.sock_recv(sock, 8192), 5)
            except TimeoutError:
                break
            received[int.from_bytes(data[:4], "big")] = data
        assert sorted(received) == list(range(len(sizes))), sorted(set(range(len(sizes))) - set(received))
        assert all(data == payload(i) for i, data in received.items())
    finally:
        sock.close()


async def _run_storm(lan_console, duplicate: bool) -> str:
    """Fire the storm on lan via UART, return the script's stdout.

    Depends on system python3 having scapy importable on the lan VM.
    The lan VM is NAT-isolated (no IP path from the orchestrator), so
    install it by hand over the UART console:
    `apt-get install python3-scapy`.

    Scapy's pure-Python send() does ~10 kpps; worst case 30k frags
    finishes in ~3 s, but the kernel PTY path adds its own slack.
    """
    script = _storm_script(duplicate=duplicate)
    label = "reassembly_storm_dup" if duplicate else "reassembly_storm"
    r = await lan_run_python(lan_console, script, label=label, timeout=180.0)
    assert r.rc == 0, f"storm script failed: rc={r.rc}, out={r.stdout!r}"
    assert "STORM_DONE" in r.stdout, f"storm did not finish: {r.stdout!r}"
    return r.stdout


@pytest.mark.parametrize(
    "duplicate,label",
    [
        (False, "plain"),
        (True,  "duplicates"),
    ],
    ids=["plain", "duplicates"],
)
async def test_reassembly_fragment_storm(
    aiohttp_session, target_agent, lan, splat_window, duplicate, label,
):
    """Fragment storm from lan through target's Linux reassembly.

    splat_window gates UBSAN/KFENCE/lockdep/WARN/BUG during the storm.
    kmemleak delta (cleared pre-storm, filtered post-storm) catches any
    in-subsystem leak that survives the release path.
    """
    # Establish the kmemleak cursor: "clear" marks every currently-
    # reported leak as seen, so the post-storm scan only surfaces leaks
    # detected inside this test's window. Without this, the ~16k DPAA
    # baseline false-positives would drown any real signal.
    await target_agent.kmemleak_clear(aiohttp_session)

    out = await _run_storm(lan, duplicate=duplicate)

    # Give the kernel a moment to quiesce: the bpool replenish hook
    # and any deferred softirqs should settle before we scan kmemleak.
    # kmemleak's own scanner also needs a jiffy to walk the heap.
    await asyncio.sleep(3.0)

    report = await target_agent.kmemleak(
        aiohttp_session, filter_substrs=FRAGMENT_LEAK_FILTER,
    )
    assert report["leak_count"] == 0, (
        f"kmemleak found {report['leak_count']} new leak(s) in the software fragment path "
        f"after {label} storm ({out.strip().splitlines()[-1]}):\n"
        + report.get("report", "")[:4000]
    )


# --- Fragmentation under concurrent live traffic ---------
#
# Liveness-only oracle: iperf3 must complete and report non-zero
# throughput. No Mbps SLA — throughput baselines are deferred
# pending a noise-floor study. The signal here is "the storm doesn't
# kill the offload path entirely", not "throughput stays at X".

import re as _re

_IPERF_RX_RE = _re.compile(
    r"^\[\s*\d+\]\s+[\d.]+-[\d.]+\s+sec\s+[\d.]+\s[KMGT]?Bytes\s+([\d.]+)\s+([KMG]?)bits/sec",
    _re.M,
)
_IPERF_DURATION_S = int(os.environ.get("ASK_IPERF_DURATION", "5"))


def _iperf_receiver_bps(log: str) -> float | None:
    matches = _IPERF_RX_RE.findall(log)
    if not matches:
        return None
    val, unit = matches[-1]
    scale = {"": 1.0, "K": 1e3, "M": 1e6, "G": 1e9}.get(unit, 0.0)
    return float(val) * scale


async def test_with_concurrent_iperf(
    aiohttp_session, target_agent, lan, splat_window,
):
    """Fragment storm AND iperf3 in parallel through the DUT.

    The storm exercises software fragment handling. iperf3 exercises
    the offloaded fast path. Running both at once verifies the
    interleave doesn't collapse the offload path entirely (liveness)
    and doesn't introduce splats or leaks (correctness).
    """
    await target_agent.kmemleak_clear(aiohttp_session)

    # Unique per-test-invocation log path — avoids collisions across
    # reruns within the same minute.
    log_path = f"/tmp/iperf-storm-{os.getpid()}-{int(asyncio.get_event_loop().time() * 1e6)}.log"

    # Launch iperf3 in the background; the UART command returns
    # immediately due to the trailing `&`. iperf3 then runs on the LAN
    # VM concurrently with the storm. We do NOT run two UART commands
    # at once — that would interleave on the serial channel and
    # corrupt the marker-based exit detection.
    await lan_run(
        lan,
        f"nohup iperf3 -c {WAN_IPERF_IP} -t {_IPERF_DURATION_S} "
        f"> {log_path} 2>&1 & echo started",
    )
    # Brief warm-up so the TCP handshake completes before fragments
    # start sending fragmented traffic.
    await asyncio.sleep(0.5)

    # Storm runs synchronously on the UART. iperf3 keeps running in
    # parallel as a backgrounded LAN process.
    storm_out = await _run_storm(lan, duplicate=False)

    # The storm typically takes several seconds; ensure iperf3 has
    # finished before we read its log.
    await asyncio.sleep(2.0)
    log_result = await lan_run(lan, f"cat {log_path}")
    iperf_log = log_result.stdout

    # iperf3 must have completed and reported non-zero throughput.
    bps = _iperf_receiver_bps(iperf_log)
    assert bps is not None, (
        f"iperf3 summary missing during concurrent storm; log:\n{iperf_log}"
    )
    assert bps > 0, (
        f"iperf3 reported zero throughput during concurrent fragment "
        f"storm — offload path collapsed. log tail:\n"
        + "\n".join(iperf_log.splitlines()[-10:])
    )

    # kmemleak grace then scan, same shape as the standalone variant.
    await asyncio.sleep(3.0)
    report = await target_agent.kmemleak(
        aiohttp_session, filter_substrs=FRAGMENT_LEAK_FILTER,
    )
    assert report["leak_count"] == 0, (
        f"kmemleak found {report['leak_count']} new leak(s) after "
        f"storm+iperf concurrent run ({storm_out.strip().splitlines()[-1]}):\n"
        + report.get("report", "")[:4000]
    )
