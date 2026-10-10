"""Sustained native ownership turnover at the production admission budget.

IPv4 turns over NAT connections through the gateway's MASQUERADE policy and
IPv6 turns over routed connections between the two ULA /64s. Both fill all
32,768 directions and run the same rounds against that budget.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
import json
import os
import resource
import socket
import statistics
import time

import pytest

from _topology import LAN_IPV6, TARGET_LAN_IF, TARGET_WAN_IF, WAN_IPV6
from _flowtable_capacity import (
    BASE,
    CAPACITY,
    CONNECTIONS,
    batch,
    check_rows,
    enable_snapshots,
    release,
    delete_udp,
    delivery,
    hardware_window,
    lan_counters,
    socket_drops,
    start,
    unchanged,
    wait_entries,
)
from _flowtable_connections import (healthy, peer)
from _flowtable_rig import (DPORT, TABLE, WAN_IP, Echo, command, read)
from _flowtable_tcp import cpu_delta, transfer_counters
from ask_orch.commands import console_python
from ask_orch.uart import Console

pytestmark = pytest.mark.skipif(
    os.environ.get("ASK_FLOWTABLE_CHURN") != "1",
    reason="explicit sustained full-capacity churn proof")
GROUP = 256
SURVIVORS = GROUP
# Connections never opened before the last round, which retires one group for
# good and admits these into exactly the directions it freed.
FRESH = GROUP
EGRESS_TABLE = "ask_churn_egress"
# Linux owns flow lifetime. A hardware-owned flow keeps its deadline only
# through a stats round trip, which becomes eligible after the first tenth
# of the flow timeout and is skipped while any offload work for that flow is
# still pending. Every such read shares one hardware transaction with the
# installs and retirements of this workload, so a live flow can miss that window,
# be retired and then be readmitted on its next packet. Keep the offload timeout
# wide enough that the refresh is not routinely starved, and account for the
# turnover that remains instead of demanding a window the datapath cannot offer.
OFFLOAD_TIMEOUT = {"tcp": 120, "udp": 30}
# Directions one round may see readmitted without the group requesting it, and
# the average the whole run may sustain. A regression that retires live flows
# systematically exceeds these; a single starved refresh does not.
TURNOVER_ROUND = 64
TURNOVER_BUDGET = 16
# Forward-hook packets one readmitted direction may cost before hardware
# resumes. Software forwarding continues throughout, so this is latency, not loss.
SOFTWARE_PER_READMISSION = 8


@dataclass
class ChurnPath:
    """What differs between the address families. `public` is the source the
    WAN endpoint sees: the gateway's own address under IPv4 MASQUERADE, the
    LAN peer's own address on the routed IPv6 path."""
    family: str        # conntrack(8) -f
    selector: str      # nft address expression: ip or ip6
    lan: str
    wan: str
    public: str
    receiver: Echo     # the WAN endpoint's UDP echo
    listen: tuple = ()  # addresses the WAN TCP listeners add to WAN_IP

    def spec(self, r, ident):
        sport = BASE + ident // 2
        spec = {"id": ident, "proto": "tcp" if ident & 1 else "udp", "sport": sport,
                "abort": ident % 4 == 3}
        if self.lan != r.lan_ip:
            spec["lan"] = self.lan
        if self.wan != WAN_IP:
            spec["connect_ip"] = self.wan
        if self.public != self.lan:
            spec["remote"] = [self.public, sport]
        return spec



async def flowtable_work(r):
    """Offload work the flowtable core still has queued: add, del and stats.

    A hardware-owned flow keeps its Linux deadline only through a stats round
    trip, so a stats backlog is what retires a live flow. Returns None on a
    kernel built without CONFIG_NF_FLOW_TABLE_PROCFS, where it cannot be seen.
    """
    result = await r.target.fs_read(r.session, "/proc/net/stat/nf_flowtable", max_bytes=8192)
    if result["errno"]:
        return None
    # Unlike /proc/net/stat/nf_conntrack alongside it, this file prints decimal.
    lines = bytes.fromhex(result["content_hex"]).decode().splitlines()
    fields = lines[0].split()
    queued = dict.fromkeys(fields, 0)
    for line in lines[1:]:
        for name, value in zip(fields, line.split()):
            queued[name] += int(value)
    return queued


async def work_peak(r, task):
    """Track the deepest offload backlog seen while `task` runs.

    Sampling once says nothing: the backlog builds and drains within a single
    retirement, so the peak is the only figure that describes the pressure.
    """
    peak = {}
    while not task.done():
        sample = await flowtable_work(r)
        if sample is None:
            return None
        peak = {name: max(peak.get(name, 0), value) for name, value in sample.items()}
        await asyncio.sleep(0.5)
    return peak


async def settled(r, p, retire, admit, timeout=90):
    """Sample a whole table at full occupancy.

    Linux can retire a live flow between the occupancy check and the snapshot,
    so resample rather than reason about a torn view of ownership.
    """
    state, absences, reads = None, [], []
    for _ in range(5):
        await wait_entries(r, CAPACITY, p, timeout=timeout)
        # Time the dump. It reads every entry's counters from DDR under the
        # same hardware transaction as admission, retirement and the stats
        # refresh, so a slow one is itself a source of the pressure measured.
        state = await r.state()
        reads.append(state["read_seconds"])
        checked = await r.target.observe(r.session, "check", snapshot=state["snapshot"], retire=retire, admit=admit)
        if state["entries"] == CAPACITY and checked["missing_count"] == checked["unexpected_count"] == 0:
            # Report what had to be retried. A resample that hides a flow
            # cycling through software is evidence, not noise.
            return state, {"absences": absences, "read_seconds": reads}
        absences.append(checked)
        await release(r, state)
    header = {k: v for k, v in (state or {}).items() if k != "flows"}
    pytest.fail(f"table never settled at {CAPACITY} directions: {header} "
                f"absences={absences} read_seconds={reads}")


async def memory(r, *, full=False):
    if not full:
        # The round only needs numeric memory totals. Keep raw slab diagnostics
        # at the run boundaries instead of carrying them over UART each time.
        result = await console_python(Console.target(), '''
import json
from pathlib import Path
print(json.dumps({'kib': {line.split()[0].rstrip(':'): int(line.split()[1])
                         for line in Path('/proc/meminfo').read_text().splitlines()},
                  'conntracks': int(Path('/proc/sys/net/netfilter/nf_conntrack_count').read_text())}))
''')
        return json.loads(result["stdout"])
    meminfo = await read(r.target, r.session, "/proc/meminfo")
    slabinfo = await read(r.target, r.session, "/proc/slabinfo")
    return {"meminfo": meminfo, "slabinfo": slabinfo,
            "kib": {line.split()[0].rstrip(":"): int(line.split()[1])
                    for line in meminfo.splitlines()},
            "conntracks": int(await read(r.target, r.session,
                                         "/proc/sys/net/netfilter/nf_conntrack_count"))}


async def control_counters(r):
    result = await command(r.target, r.session, "nft", "-j", "list", "counters", "table", "inet", TABLE)
    return {obj["counter"]["name"]: obj["counter"]["packets"]
            for obj in json.loads(result["stdout"])["nftables"] if "counter" in obj}


async def egress_counters(r):
    result = await command(r.target, r.session, "nft", "-j", "list", "counters", "table", "netdev", EGRESS_TABLE)
    return {obj["counter"]["name"]: obj["counter"]["packets"]
            for obj in json.loads(result["stdout"])["nftables"] if "counter" in obj}


def transfer_summary(p, reports):
    result = delivery(p, reports)
    result["tcp_records"] = sum(v["count"] for i, v in reports.items() if p.flows[int(i)]["proto"] == "tcp")
    result["late"] = sum(v["late"] for v in reports.values())
    return result


async def test_flowtable_sustained_churn(rig):
    r = rig
    address = json.loads((await command(r.target, r.session, "ip", "-j", "-4", "addr",
                                       "show", "dev", TARGET_WAN_IF))["stdout"])[0]
    public = next(a["local"] for a in address["addr_info"] if a["family"] == "inet")
    await sustained_churn(r, ChurnPath("ipv4", "ip", r.lan_ip, WAN_IP, public, r.echo))


async def test_flowtable_ipv6_sustained_churn(rig, ipv6_rig):
    """The same proof over routed IPv6. The IPv4 rig
    supplies the health baseline; the IPv6 rig
    addresses both segments and pins both endpoints as neighbours."""
    r = rig
    echo = Echo()
    transport, _ = await asyncio.get_running_loop().create_datagram_endpoint(
        lambda: echo, local_addr=(WAN_IPV6, DPORT), family=socket.AF_INET6)
    try:
        await sustained_churn(r, ChurnPath("ipv6", "ip6", LAN_IPV6, WAN_IPV6, LAN_IPV6, echo,
                                           (WAN_IPV6,)))
    finally:
        transport.close()


async def sustained_churn(r, path):
    # A short run is useful while developing the generator, but the accepted
    # proof uses the default 900 seconds and visits every rotating tuple.
    duration = int(os.environ.get("ASK_FLOWTABLE_CHURN_SECONDS", "900"))
    # Round zero waits out idle UDP expiry, which alone takes the offload
    # timeout. A run shorter than this cannot reach the second round the
    # acceptance below requires.
    assert 3 * OFFLOAD_TIMEOUT["udp"] <= duration <= 3600
    receiver = path.receiver.transport.get_extra_info("socket")
    receiver.setsockopt(socket.SOL_SOCKET, 33, 4 << 20)  # SO_RCVBUFFORCE
    assert receiver.getsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF) >= 4 << 20
    assert socket_drops(receiver) == 0
    # Payload validation remains at each peer. Do not retain millions of full
    # datagrams in the orchestrator merely to count a long-running workload.
    path.receiver.record_payloads = False
    initial = await r.state()
    assert initial["max_entries"] == CAPACITY and initial["entries"] == 0
    specs = [path.spec(r, i) for i in range(CONNECTIONS + FRESH)]
    await enable_snapshots(r, lan=path.lan, wan=path.wan, public=path.public,
                           lan_if=TARGET_LAN_IF, wan_if=TARGET_WAN_IF, dport=DPORT,
                           base=BASE, count=CONNECTIONS, survivors=SURVIVORS)
    ids = list(range(CONNECTIONS))
    fresh = list(range(CONNECTIONS, CONNECTIONS + FRESH))
    groups = [ids[offset:offset + GROUP] for offset in range(SURVIVORS, CONNECTIONS, GROUP)]
    lan, wan, public, ip = path.lan, path.wan, path.public, path.selector
    survivor_ports = f"{BASE}-{BASE + SURVIVORS // 2 - 1}"
    cleanup, samples = [], []
    totals = {"udp_sent": 0, "udp_lost": 0, "tcp_records": 0, "late": 0}
    retirements = {"fin": 0, "rst": 0, "udp_delete": 0, "udp_expiry": 0}
    # Directions Linux retired and readmitted on its own, the forward-hook
    # packets that entitles a survivor to, and conntracks Linux had already
    # reclaimed when this test went to delete them. All three describe the same
    # reclamation pressure, and all three are bounded rather than assumed absent.
    turnover = software_budget = absent = 0
    limits = resource.getrlimit(resource.RLIMIT_NOFILE)
    needed = 2 * len(specs) + 512
    assert needed <= limits[1]
    resource.setrlimit(resource.RLIMIT_NOFILE, (max(limits[0], needed), limits[1]))

    def transfers(p, label, reports):
        r.record(label, {"reports": reports})
        result = transfer_summary(p, reports)
        for key in totals:
            totals[key] += result[key]
        return result

    try:
        r.record("churn-lan-before", await lan_counters(r))
        r.record("churn-memory-before", await memory(r, full=True))
        r.record("churn-kmemleak-baseline", await r.target.kmemleak_clear(r.session))
        for proto, timeout in OFFLOAD_TIMEOUT.items():
            name = f"net.netfilter.nf_flowtable_{proto}_timeout"
            value = (await read(r.target, r.session, "/proc/sys/" + name.replace(".", "/"))).strip()
            cleanup.append(["sysctl", "-w", f"{name}={value}"])
            await command(r.target, r.session, "sysctl", "-w", f"{name}={timeout}")
            await command(r.target, r.session, "conntrack", "-D", "-f", path.family, "-p", proto,
                          "--orig-src", lan, "--orig-dst", wan, "--dport", str(DPORT), check=False, quiet=True)
        existing = await command(r.target, r.session, "nft", "list", "table", "netdev", EGRESS_TABLE, check=False)
        assert existing["rc"] != 0, "churn egress table already exists"
        await r.nft(f'''table netdev {EGRESS_TABLE} {{
 counter lan {{ }}
 counter wan {{ }}
 chain lan {{ type filter hook egress device {TARGET_LAN_IF} priority 0; policy accept;
 {ip} saddr {wan} {ip} daddr {lan} meta l4proto {{ tcp, udp }} th sport {DPORT} th dport {survivor_ports} counter name lan
 }}
 chain wan {{ type filter hook egress device {TARGET_WAN_IF} priority 0; policy accept;
 {ip} saddr {public} {ip} daddr {wan} meta l4proto {{ tcp, udp }} th sport {survivor_ports} th dport {DPORT} counter name wan
 }}
}}''')
        cleanup.append(["nft", "delete", "table", "netdev", EGRESS_TABLE])
        churned_ports = f"{BASE}-{BASE + CONNECTIONS // 2 - 1}"
        await r.nft(f'''table inet {TABLE} {{
 flowtable fast {{ hook ingress priority 0; devices = {{ {TARGET_LAN_IF}, {TARGET_WAN_IF} }}; flags offload; }}
 counter fin {{ }}
 counter rst {{ }}
 counter survivor_forward {{ }}
 chain forward {{ type filter hook forward priority 0; policy accept;
 {ip} saddr {lan} {ip} daddr {wan} meta l4proto {{ tcp, udp }} th sport {survivor_ports} th dport {DPORT} counter name survivor_forward
 {ip} saddr {wan} {ip} daddr {lan} meta l4proto {{ tcp, udp }} th sport {DPORT} th dport {survivor_ports} counter name survivor_forward
 {ip} saddr {lan} {ip} daddr {wan} tcp sport {churned_ports} tcp dport {DPORT} tcp flags & fin == fin counter name fin
 {ip} saddr {lan} {ip} daddr {wan} tcp sport {churned_ports} tcp dport {DPORT} tcp flags & rst == rst counter name rst
 {ip} saddr {lan} {ip} daddr {wan} meta l4proto {{ tcp, udp }} th sport {BASE}-{BASE + len(specs) // 2 - 1} th dport {DPORT} flow add @fast
 }}
}}''')
        async with peer(r, specs, initial_ids=[], lease=max(duration + 600, 3600), tcp_size=1024, reconnect=True,
                        listen_addresses=path.listen) as p:
            warmup = {}
            for offset in range(0, CONNECTIONS, GROUP):
                group = ids[offset:offset + GROUP]
                await p.rpc("open", group)
                warmup.update(await batch(p, group, count=4, interval=0.025))
                await start(p, group)
            transfers(p, "churn-warmup", warmup)
            await wait_entries(r, CAPACITY, p)
            initial_hardware = await r.state()
            before = await hardware_window(r, initial_hardware, "churn-initial-hardware")
            await release(r, initial_hardware)
            await check_rows(r, before)
            counts0 = await control_counters(r)
            egress0 = await egress_counters(r)
            # Initial software exchanges must actually hit each observation
            # hook; a misplaced or incorrectly translated match is not proof.
            assert all(value > 0 for value in egress0.values()), egress0
            started = time.monotonic()
            visited = set()

            async def rotate(round_id, retire, admit, *, expire):
                """Retire one group's connections and admit `admit` in their
                place: the same tuples reopened, or tuples never seen before."""
                nonlocal before, turnover, software_budget, absent
                counters0 = await transfer_counters(state=False)
                cpu0, tx0 = counters0["cpu"], counters0["software_tx"]
                begin = time.monotonic()
                reports = await p.rpc("stop", retire)
                transferred = transfers(p, f"churn-{round_id:04d}-transfers", reports)
                await p.rpc("close", retire)
                if not expire:
                    absent += await delete_udp(r, [specs[i]["sport"] for i in retire
                                                    if specs[i]["proto"] == "udp"],
                                               allow_missing=True, source=lan, destination=wan)
                for ident in retire:
                    if specs[ident]["proto"] == "udp":
                        retirements["udp_expiry" if expire else "udp_delete"] += 1
                    else:
                        retirements["rst" if specs[ident]["abort"] else "fin"] += 1
                # Follow the offload backlog while this round's retirements
                # drain; once the table has settled it says nothing.
                draining = asyncio.create_task(
                    wait_entries(r, CAPACITY - 2 * len(retire), p, timeout=OFFLOAD_TIMEOUT["udp"] + 45))
                work = await work_peak(r, draining)
                freed = await draining
                # Retirement must free precisely this group's ownership. An
                # install here belongs to a flow Linux retired and readmitted by
                # itself; it is balanced by its own delete and is attributed to a
                # surviving tuple once the table is whole again. The adapter must
                # not have retired anything on its own initiative, so every
                # counter describing an adapter-driven retirement stays put, and
                # the three independently maintained occupancy counters agree.
                readmitted = freed["installs"] - before["installs"]
                if readmitted:
                    r.record(f"churn-{round_id:04d}-readmitted",
                             {"before": {k: v for k, v in before.items() if k != "flows"},
                              "freed": freed, "group": retire})
                assert 0 <= readmitted <= TURNOVER_ROUND, (before, freed)
                assert (freed["entries"] == freed["neighbour_refs"] == freed["handle_refs"]
                        == CAPACITY - 2 * len(retire)), freed
                assert freed["installs"] - freed["deletes"] == freed["entries"], freed
                assert all(freed[k] == before[k] for k in (
                    "errors", "invalidated", "quarantine", "rearms",
                    "neighbour_invalidations", "route_invalidations", "mtu_invalidations",
                    "link_invalidations", "mac_invalidations")), (before, freed)
                # Reopening the exact tuples continues their payload serials,
                # which catches stale packets crossing a hardware generation
                # boundary. Fresh tuples prove the freed directions are free.
                await p.rpc("open", admit)
                transferred_warm = transfers(p, f"churn-{round_id:04d}-warmup",
                                             await batch(p, admit, count=4, interval=0.025))
                await start(p, admit)
                await wait_entries(r, CAPACITY, p)
                # Keep admission paced even on machines with very fast control
                # RPCs. This increment does not revisit simultaneous burst loss.
                while time.monotonic() - begin < 4:
                    assert not (await p.rpc("status", compact=True))["errors"]
                    await asyncio.sleep(0.5)
                after, settle = await settled(r, p, retire, admit)
                healthy(after)
                # The group's own change of identity is requested and excluded;
                # anything else that changed is turnover, and it has to account
                # for the installs this round did not ask for. The cookie alone
                # cannot identify that: Linux derives it from the flow tuple's
                # address (`nf_flow_table_offload.c`), so a readmitted flow can
                # reappear under a recycled one. A restarted packet count cannot
                # alias, and the adapter's own -ESTALE guard exists for the same
                # reason, so treat either as a new hardware generation.
                compared = await unchanged(r, before, after, exclude_ids=retire, allow_regenerated=True)
                assert readmitted <= compared["regenerated_count"] <= TURNOVER_ROUND, compared
                regenerated = compared["regenerated"]
                # Admitting the group is the only ownership this round asked
                # for. Anything deleted alongside it is a generation the
                # adapter retired for an admission reason it counts, or a
                # further flow Linux turned over; both publish and retire in
                # pairs, so the surplus installs must match them.
                retries = after["admission_invalidations"] - freed["admission_invalidations"]
                surplus = after["deletes"] - freed["deletes"]
                assert after["installs"] - freed["installs"] == 2 * len(admit) + surplus, (freed, after)
                assert 0 <= surplus <= 2 * retries + TURNOVER_ROUND, (freed, after, retries)
                assert after["installs"] - after["deletes"] == CAPACITY
                survivors_regenerated = compared["survivors_regenerated"]
                turnover += compared["regenerated_count"]
                software_budget += SOFTWARE_PER_READMISSION * len(survivors_regenerated)
                assert compared["survivor_progress_errors"] == 0, compared
                counters1 = await transfer_counters(state=False)
                cpu1, tx1 = counters1["cpu"], counters1["software_tx"]
                # The gc_* columns are cumulative retirement causes, so a peak
                # says nothing about them; take an exact snapshot for the delta.
                work_now = await flowtable_work(r)
                counters = await control_counters(r)
                egress = await egress_counters(r)
                # A survivor leaves hardware only when Linux readmits it, and
                # only for as long as that readmission takes. Software forwarding
                # covers the gap, so this budget bounds latency, not loss: with
                # no readmitted survivor it is zero and these stay exact.
                assert 0 <= counters["survivor_forward"] - counts0["survivor_forward"] <= software_budget, (
                    counts0, counters, software_budget)
                assert all(0 <= egress[dev] - egress0[dev] <= software_budget for dev in egress), (
                    "survivor software transmission", egress0, egress, software_budget)
                assert absent <= TURNOVER_BUDGET * (round_id + 1), (absent, round_id)
                mem = await memory(r)
                assert mem["conntracks"] <= len(specs) + 1024, mem["conntracks"]
                assert socket_drops(receiver) == 0, "generator UDP receive queue overflow"
                sample = {"round": round_id, "seconds": time.monotonic() - started,
                          "round_seconds": time.monotonic() - begin, "expiry": expire,
                          "group": retire, "admitted": admit,
                          "state": {k: v for k, v in after.items() if k != "flows"},
                          "memory": mem, "cpu": cpu_delta(cpu0, cpu1),
                          "forward_counters": counters,
                          "survivor_software_tx": {dev: egress[dev] - egress0[dev] for dev in egress},
                          "software_tx": {dev: tx1[dev] - tx0[dev] for dev in tx0},
                          "transfers": transferred, "warmup": transferred_warm,
                          "readmitted": readmitted, "regenerated": sorted(regenerated),
                          "survivors_regenerated": sorted(survivors_regenerated),
                          "turnover": turnover, "software_budget": software_budget,
                          "absent_conntracks": absent, "offload_work": work,
                          "offload_totals": work_now,
                          "settle": settle,
                          "retirements": dict(retirements), "totals": dict(totals)}
                r.record(f"churn-{round_id:04d}", sample)
                samples.append(sample)
                visited.update(retire)
                await release(r, before)
                before = after

            round_id = 0
            # Duration is a minimum soak, not an assumption about control-link
            # speed. A release proof must also visit every rotating tuple.
            minimum_rounds = len(groups) if duration >= 900 else 2
            while time.monotonic() - started < duration or round_id < minimum_rounds:
                group = groups[round_id % len(groups)]
                await rotate(round_id, group, group, expire=round_id % 8 == 0)
                round_id += 1
            assert round_id >= 2 and all(retirements.values()), retirements
            # Once churn is over, retire one more group for good and fill its
            # directions with connections never admitted before. Leaked
            # ownership would leave the table short of capacity here.
            closed = groups[round_id % len(groups)]
            await rotate(round_id, closed, fresh, expire=False)
            round_id += 1
            active = [ident for ident in ids if ident not in closed] + fresh
            # One starved refresh can turn a round's worth of directions over.
            # A run that sustains that rate is retiring live flows systematically
            # and is the regression this budget exists to catch.
            assert turnover <= TURNOVER_BUDGET * round_id, (turnover, round_id)
            if duration >= 900:
                assert len(visited) == CONNECTIONS - SURVIVORS, (round_id, len(visited))
            counts1 = await control_counters(r)
            controls = {key: counts1[key] - counts0[key] for key in counts0}
            assert 0 <= controls["survivor_forward"] <= software_budget, (controls, software_budget)
            egress1 = await egress_counters(r)
            assert all(0 <= egress1[dev] - egress0[dev] <= software_budget for dev in egress1), (
                egress0, egress1, software_budget)
            assert controls["fin"] >= retirements["fin"] and controls["rst"] >= retirements["rst"], controls
            # Bracket the quiet window. Nothing is asked of the table here, so
            # any retirement inside it is unprompted, and these columns say
            # which condition caused it.
            quiet_before = await flowtable_work(r)
            try:
                final_hardware = await hardware_window(r, before, "churn-final-hardware",
                                                       turnover=TURNOVER_ROUND)
            finally:
                r.record("churn-quiet-window",
                         {"before": quiet_before, "after": await flowtable_work(r)})
            transfers(p, "churn-final-transfers", await p.rpc("stop", active))
            final = await r.delete_table()
            assert final["installs"] == final["deletes"]
            assert all(final[k] == 0 for k in ("entries", "bindings", "handle_refs", "neighbour_refs",
                                              "quarantine", "fatal", "invalidated"))
            assert final["errors"] == initial["errors"], (initial, final)
            # Unreclaimable slab is a coarse platform guard, not an ownership
            # counter. Compare settled full-occupancy samples and retain raw
            # allocator data at the boundaries; allocator caches may stay warm.
            width = max(1, len(samples) // 4)
            head = samples[width:2 * width] or samples[:width]
            growth = statistics.median(s["memory"]["kib"]["SUnreclaim"] for s in samples[-width:]) - statistics.median(
                s["memory"]["kib"]["SUnreclaim"] for s in head)
            r.record("churn-result", {"family": path.family, "requested_seconds": duration, "rounds": round_id,
                                     "churn_seconds": samples[-1]["seconds"], "visited": len(visited),
                                     "retirements": retirements, "totals": totals, "tcp_control": controls,
                                     "turnover": turnover, "software_budget": software_budget,
                                     "absent_conntracks": absent,
                                     "unreclaimable_growth_kib": growth, "generator_drops": socket_drops(receiver),
                                     "udp_received": path.receiver.packets, "state": final,
                                     "last_full_state": {k: v for k, v in final_hardware.items() if k != "flows"}})
            # 32 MiB is a coarse sustained-growth tripwire, not a claim that
            # smaller leaks are acceptable. Exact backend ownership must drain.
            assert growth <= 32 * 1024, ("unreclaimable slab growth", growth)
    finally:
        failures = []

        async def restore(label, operation, *, record=False, allowed_rc=(0,)):
            try:
                result = await operation
                if record:
                    r.record(label, result)
                elif result["rc"] not in allowed_rc:
                    failures.append((label, result))
            except Exception as error:
                failures.append((label, repr(error)))

        try:
            try:
                r.record("churn-receiver", {"packets": path.receiver.packets, "drops": socket_drops(receiver)})
            except Exception as error:
                failures.append(("receiver diagnostics", repr(error)))
            await restore("churn-lan-after", lan_counters(r), record=True)
            await restore("churn-drained", r.delete_table(), record=True)
            for proto in ("udp", "tcp"):
                await restore("delete " + proto, command(
                    r.target, r.session, "conntrack", "-D", "-f", path.family, "-p", proto,
                    "--orig-src", lan, "--orig-dst", wan, "--dport", str(DPORT), check=False, quiet=True),
                    allowed_rc=(0, 1))
            for argv in reversed(cleanup):
                await restore(" ".join(argv), command(r.target, r.session, *argv, check=False))
            await restore("churn-memory-drained", memory(r, full=True), record=True)
            assert not failures, failures
        finally:
            resource.setrlimit(resource.RLIMIT_NOFILE, limits)
    # Scan after closing all peers, deleting test conntracks and allowing
    # deferred reclamation. Scope includes native flow storage and its CDX
    # classifier allocations, without unrelated hardware-owned DPAA pools.
    await asyncio.sleep(5)
    leaks = await r.target.kmemleak(r.session, filter_substrs=[
        "[ask_flowtable]", "[cdx]", "ask_ft_", "cdx_flowtable_",
        "flow_offload_", "nf_flow_", "ExternalHashTable"])
    r.record("churn-kmemleak", leaks)
    assert leaks["leak_count"] == 0, leaks
