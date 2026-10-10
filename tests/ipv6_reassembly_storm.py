"""IPv6 reassembly storm.

Mirror of reassembly_storm.py for IPv6 fragmentation. Drives a
storm of IPv6-fragmented UDP packets through the DUT's reassembly
path; gates on splat_window for sanitizer hits and kmemleak filtered
to reassembly-symbol needles for in-subsystem leaks.

KASAN-eligible — reassembly is Linux's, in its IPv6 fragment queues, as the
IPv4 storm's is in the IPv4 ones.
"""

from __future__ import annotations

import asyncio
import os
import textwrap

import pytest

from _topology import (lan_run_python)


# The IPv6 counterpart of the IPv4 storm's filter: the shared fragment-queue
# allocator, the stack's own reassembly and conntrack's defragmentation.
# Limiting matches to them keeps hardware-owned DPAA buffers, which kmemleak's
# pointer scanner cannot see, from reading as leaks.
REASSM_LEAK_FILTER = [
    "inet_frag_alloc", "inet_frag_create",
    "ip6_frag_queue", "ip6_frag_reasm", "ipv6_frag_rcv",
    "nf_ct_frag6_gather", "nf_ct_frag6_queue", "nf_ct_frag6_reasm",
]


WAN_IPV6 = os.environ.get("ASK_WAN_IPV6", "fc00:beef::99")

# Knobs sized like the IPv4 storm: many distinct 5-tuples to churn the
# reassembly context pool, fragments interleaved.
N_PACKETS      = 1500
N_SOURCE_PORTS = 150
UDP_SPORT_BASE = 30000
UDP_DPORT      = 5202   # != IPv4 storm's 5201 to avoid any cross-talk
PKT_MIN_BYTES  = 2000
PKT_MAX_BYTES  = 5000
FRAGSIZE       = 1280   # IPv6 minimum MTU; standard fragment unit


def _storm_script(duplicate: bool) -> str:
    return textwrap.dedent(f"""
        import random
        from scapy.all import IPv6, IPv6ExtHdrFragment, UDP, Raw, fragment6, send
        random.seed(0xDEAD6)
        all_frags = []
        for i in range({N_PACKETS}):
            sport = {UDP_SPORT_BASE} + (i % {N_SOURCE_PORTS})
            size  = random.randint({PKT_MIN_BYTES}, {PKT_MAX_BYTES})
            pkt   = (IPv6(dst="{WAN_IPV6}")
                     / IPv6ExtHdrFragment()
                     / UDP(sport=sport, dport={UDP_DPORT})
                     / Raw(b"Y" * size))
            frags = fragment6(pkt, {FRAGSIZE})
            if {int(bool(duplicate))}:
                doubled = []
                for f in frags:
                    doubled.append(f); doubled.append(f)
                frags = doubled
            all_frags.extend(frags)
        random.shuffle(all_frags)
        send(all_frags, verbose=0, inter=0)
        print("STORM_DONE n_frags=%d" % len(all_frags))
    """).strip()


async def _run_storm(lan, duplicate: bool) -> str:
    script = _storm_script(duplicate=duplicate)
    label = "ipv6_storm_dup" if duplicate else "ipv6_storm"
    r = await lan_run_python(lan, script, label=label, timeout=180.0)
    assert r.rc == 0, f"storm script failed: rc={r.rc}, out={r.stdout!r}"
    assert "STORM_DONE" in r.stdout, f"storm did not finish: {r.stdout!r}"
    return r.stdout


@pytest.mark.parametrize(
    "duplicate,label",
    [(False, "plain"), (True, "duplicates")],
    ids=["plain", "duplicates"],
)
async def test_ipv6_reassembly_storm(
    aiohttp_session, target_agent, lan, splat_window, ipv6_topology,
    duplicate, label,
):
    await target_agent.kmemleak_clear(aiohttp_session)
    out = await _run_storm(lan, duplicate=duplicate)
    await asyncio.sleep(3.0)
    report = await target_agent.kmemleak(
        aiohttp_session, filter_substrs=REASSM_LEAK_FILTER,
    )
    assert report["leak_count"] == 0, (
        f"kmemleak found {report['leak_count']} new leak(s) in ASK code "
        f"after IPv6 {label} storm ({out.strip().splitlines()[-1]}):\n"
        + report.get("report", "")[:4000]
    )
