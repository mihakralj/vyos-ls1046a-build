"""ASK2 hardware-offload checks for the `ask2` DUT image.

Delivery alone proves nothing about offload, so each traffic test reads the
FMan ehash per-record packet counters (`fe_ehash_stats`) before and after and
requires the flow to account for the packets that were sent.
"""

import json
import os
import re

import pytest

from ask_orch.lab import Bench

EHASH_STATS = "/sys/kernel/debug/fman_pcd/0/fe_ehash_stats"
FE_ARM = "/sys/kernel/debug/fman_pcd/0/fe_arm"
PORT_IDS = {"eth3": "0x10", "eth4": "0x11"}
PACKETS_MIN_SHARE = 0.95
MURAM_BUDGET = "/sys/kernel/debug/fman_pcd/0/muram_budget"
THROUGHPUT_FLOOR_MBPS = 2000


def _bench():
    bench = Bench.load(os.environ["ASK_LAB_DUT"])
    if bench.dut.get("kind") != "ask2":
        pytest.skip("ASK2 checks need the ask2 DUT image")
    return bench


def _dut_read(bench, path):
    result = bench.endpoint("dut").execute(["cat", path], timeout=15)
    assert result.rc == 0, (path, result.stdout)
    return result.stdout


def _records(bench):
    found = re.findall(r"(tbl\[\d+\] idx=\d+) .*?record_dma=(0x[0-9a-f]+).*?pkt_count=(\d+)",
                       _dut_read(bench, EHASH_STATS))
    return {(slot, dma): int(count) for slot, dma, count in found}


def _counted_since(bench, before):
    # Records age out and are reused between reads, so sum per-record growth only.
    return sum(max(count - before.get(key, 0), 0) for key, count in _records(bench).items())


@pytest.mark.portable
@pytest.mark.ask2
def test_ask2_module_and_ports_armed(lab_report):
    _assert_armed(_bench())


def _assert_armed(bench):
    dut = bench.endpoint("dut")
    assert dut.execute(["test", "-d", "/sys/module/ask"]).rc == 0, "ask.ko is not loaded"
    arm = _dut_read(bench, FE_ARM)
    assert re.search(r"fe_pool engaged:\s+YES", arm), arm
    for interface in (bench.dut["lan_if"], bench.dut["wan_if"]):
        assert PORT_IDS[interface] in arm.split("Armed ports:")[1].split("\n")[0], (interface, arm)


@pytest.mark.portable
@pytest.mark.ask2
@pytest.mark.parametrize("family", [4, 6])
def test_unicast_udp_forwarded_in_hardware(lab_report, family):
    bench = _bench()
    _assert_armed(bench)
    address = bench.wan["address" if family == 4 else "address6"]
    before = _records(bench)
    run = bench.endpoint("lan").execute(
        ["iperf3", f"-{family}", "-c", address, "-u", "-b", "200M", "-l", "1200", "-t", "3", "-J"],
        timeout=30)
    assert run.rc == 0, run.stdout
    summary = json.loads(run.stdout)["end"]["sum"]
    sent = summary["packets"]
    assert sent > 1000 and summary["lost_packets"] == 0, summary
    counted = _counted_since(bench, before)
    assert counted >= PACKETS_MIN_SHARE * sent, (
        f"only {counted} of {sent} packets were counted by FMan ehash records; the flow was not offloaded")


@pytest.mark.portable
@pytest.mark.ask2
@pytest.mark.parametrize("family", [4, 6])
def test_unicast_tcp_forwarded_in_hardware(lab_report, family):
    bench = _bench()
    _assert_armed(bench)
    address = bench.wan["address" if family == 4 else "address6"]
    before = _records(bench)
    run = bench.endpoint("lan").execute(
        ["iperf3", f"-{family}", "-c", address, "-t", "3", "-J"], timeout=30)
    assert run.rc == 0, run.stdout
    end = json.loads(run.stdout)["end"]
    segments = end["sum_sent"]["bytes"] // 1448
    assert segments > 1000, end["sum_sent"]
    counted = _counted_since(bench, before)
    assert counted >= PACKETS_MIN_SHARE * segments, (
        f"only {counted} of about {segments} segments were counted by FMan ehash records; the flow was not offloaded")


def _iperf(bench, family, *args, address=None, timeout=40):
    address = address or bench.wan["address" if family == 4 else "address6"]
    run = bench.endpoint("lan").execute(["iperf3", f"-{family}", "-c", address, *args, "-J"], timeout=timeout)
    assert run.rc == 0, run.stdout
    text = run.stdout[run.stdout.index("{"):]
    return json.JSONDecoder().raw_decode(text)[0]["end"]


def _set_families(bench, mask):
    dut = bench.endpoint("dut")
    for interface in (bench.dut["lan_if"], bench.dut["wan_if"]):
        result = dut.execute(["sudo", "vyos-offload-ask", "--port", PORT_IDS[interface], "family", str(mask)],
                             timeout=30)
        assert result.rc == 0, result.stdout


@pytest.mark.portable
@pytest.mark.ask2
@pytest.mark.parametrize("parameter", ["vlan_offload", "pppoe_offload", "nat44_offload", "nat66_offload",
                                       "ipv6_hw_frag", "vlan_push_only"])
def test_ask2_kill_switch_default_on(lab_report, parameter):
    value = _dut_read(_bench(), f"/sys/module/ask/parameters/{parameter}").strip()
    assert value == "Y", f"ask.{parameter} defaults to {value!r}; shipping default is enabled"


@pytest.mark.portable
@pytest.mark.ask2
@pytest.mark.parametrize("family", [4, 6])
def test_unicast_udp_reverse_direction_in_hardware(lab_report, family):
    bench = _bench()
    _assert_armed(bench)
    before = _records(bench)
    end = _iperf(bench, family, "-u", "-b", "200M", "-l", "1200", "-t", "3", "-R")
    sent, lost = end["sum"]["packets"], end["sum"]["lost_packets"]
    assert sent > 1000 and lost == 0, end["sum"]
    counted = _counted_since(bench, before)
    assert counted >= PACKETS_MIN_SHARE * sent, f"only {counted} of {sent} reverse packets offloaded"


@pytest.mark.portable
@pytest.mark.ask2
def test_parallel_tcp_flows_in_hardware(lab_report):
    bench = _bench()
    _assert_armed(bench)
    before = _records(bench)
    end = _iperf(bench, 4, "-P", "4", "-t", "3")
    segments = end["sum_sent"]["bytes"] // 1448
    assert segments > 4000, end["sum_sent"]
    counted = _counted_since(bench, before)
    assert counted >= PACKETS_MIN_SHARE * segments, f"only {counted} of about {segments} segments offloaded"


@pytest.mark.portable
@pytest.mark.ask2
def test_offloaded_tcp_throughput_floor(lab_report):
    bench = _bench()
    _assert_armed(bench)
    end = _iperf(bench, 4, "-t", "5")
    mbps = end["sum_received"]["bits_per_second"] / 1e6
    assert mbps >= THROUGHPUT_FLOOR_MBPS, f"{mbps:.0f} Mbit/s below the {THROUGHPUT_FLOOR_MBPS} Mbit/s floor"


@pytest.mark.portable
@pytest.mark.ask2
def test_disengage_is_reversible(lab_report):
    bench = _bench()
    muram_before = _dut_read(bench, MURAM_BUDGET)
    used_before = re.search(r"used\s+(\d+)", muram_before).group(1)
    try:
        _set_families(bench, 0)
        arm = _dut_read(bench, FE_ARM)
        assert "(none)" in arm.split("Armed ports:")[1].split("\n")[0], arm
        end = _iperf(bench, 4, "-u", "-b", "100M", "-l", "1200", "-t", "2")
        assert end["sum"]["lost_packets"] == 0, f"forwarding broke while disengaged: {end['sum']}"
    finally:
        _set_families(bench, 3)
    _assert_armed(bench)
    used_after = re.search(r"used\s+(\d+)", _dut_read(bench, MURAM_BUDGET)).group(1)
    assert used_after == used_before, f"MURAM used {used_before} -> {used_after} after disengage/engage"
    before = _records(bench)
    end = _iperf(bench, 4, "-u", "-b", "200M", "-l", "1200", "-t", "3")
    counted = _counted_since(bench, before)
    assert counted >= PACKETS_MIN_SHARE * end["sum"]["packets"], "offload did not resume after re-engage"


def _udp_offloaded(bench, family, *extra, duration="3"):
    before = _records(bench)
    end = _iperf(bench, family, "-u", "-b", "200M", "-l", "1200", "-t", duration, *extra)
    return end["sum"], _counted_since(bench, before)


@pytest.mark.portable
@pytest.mark.ask2
@pytest.mark.parametrize("mask,offloaded,software", [(1, 4, 6), (2, 6, 4)])
def test_family_mask_keeps_both_families_forwarding(lab_report, mask, offloaded, software):
    bench = _bench()
    try:
        _set_families(bench, mask)
        summary, counted = _udp_offloaded(bench, offloaded)
        assert summary["lost_packets"] == 0 and counted >= PACKETS_MIN_SHARE * summary["packets"], (summary, counted)
        summary, counted = _udp_offloaded(bench, software)
        assert summary["lost_packets"] == 0, f"IPv{software} stopped forwarding: {summary}"
    finally:
        _set_families(bench, 3)
    _assert_armed(bench)


@pytest.mark.portable
@pytest.mark.ask2
@pytest.mark.parametrize("family", [4, 6])
def test_fragmented_udp_delivered_intact(lab_report, family):
    bench = _bench()
    _assert_armed(bench)
    end = _iperf(bench, family, "-u", "-b", "100M", "-l", "4000", "-t", "3")
    assert end["sum"]["packets"] > 500 and end["sum"]["lost_packets"] == 0, end["sum"]


@pytest.mark.portable
@pytest.mark.ask2
@pytest.mark.parametrize("family", [4, 6])
def test_slow_path_icmp_still_forwarded(lab_report, family):
    bench = _bench()
    address = bench.wan["address" if family == 4 else "address6"]
    ping = ["ping", f"-{family}", "-c", "20", "-i", "0.2", "-W", "2", address]
    result = bench.endpoint("lan").execute(ping, timeout=40)
    assert result.rc == 0 and " 0% packet loss" in result.stdout, result.stdout


@pytest.mark.portable
@pytest.mark.ask2
def test_many_parallel_flows_in_hardware(lab_report):
    bench = _bench()
    _assert_armed(bench)
    muram_before = re.search(r"used\s+(\d+)", _dut_read(bench, MURAM_BUDGET)).group(1)
    before = _records(bench)
    end = _iperf(bench, 4, "-u", "-P", "64", "-b", "2M", "-l", "1200", "-t", "4")
    sent = end["sum"]["packets"]
    assert sent > 5000, end["sum"]
    counted = _counted_since(bench, before)
    assert counted >= PACKETS_MIN_SHARE * sent, f"only {counted} of {sent} packets across 64 flows offloaded"
    muram_after = re.search(r"used\s+(\d+)", _dut_read(bench, MURAM_BUDGET)).group(1)
    assert muram_after == muram_before, f"MURAM used {muram_before} -> {muram_after} after 64 flows"


@pytest.mark.portable
@pytest.mark.ask2
def test_pcd_state_reversible_across_engage_cycle(lab_report):
    bench = _bench()
    dut = bench.endpoint("dut")
    ref = "/tmp/ask2-pcd-ref.json"
    assert dut.execute(["sudo", "pcd-snapshot", "capture", "-o", ref], timeout=60).rc == 0
    try:
        _set_families(bench, 0)
    finally:
        _set_families(bench, 3)
    diff = dut.execute(["sudo", "pcd-snapshot", "diff", ref], timeout=60)
    dut.execute(["rm", "-f", ref])
    assert diff.rc == 0 and "fully reversible" in diff.stdout, diff.stdout


@pytest.mark.portable
@pytest.mark.ask2
def test_no_kernel_errors_under_traffic(lab_report):
    bench = _bench()
    dut = bench.endpoint("dut")
    errors = ["sudo", "dmesg", "--level=err,crit,alert,emerg"]
    before = dut.execute(errors, timeout=15).stdout.splitlines()
    _iperf(bench, 4, "-t", "3")
    _iperf(bench, 6, "-u", "-b", "200M", "-l", "1200", "-t", "3")
    after = dut.execute(errors, timeout=15).stdout.splitlines()
    assert after[-len(before):] == before if before else not after, "\n".join(after[len(before):])


@pytest.mark.portable
@pytest.mark.ask2
def test_ask_check_ready(lab_report):
    result = _bench().endpoint("dut").execute(["sudo", "ask-check"], timeout=120)
    assert result.rc == 0 and "0 FAIL" in result.stdout, result.stdout[-1500:]


@pytest.mark.portable
@pytest.mark.ask2
def test_unrelated_kill_switch_toggle_keeps_offload(lab_report):
    bench = _bench()
    dut = bench.endpoint("dut")
    param = "/sys/module/ask/parameters/nat44_offload"
    try:
        assert dut.execute(["sudo", "sh", "-c", f"echo N > {param}"]).rc == 0
        summary, counted = _udp_offloaded(bench, 4)
        assert summary["lost_packets"] == 0 and counted >= PACKETS_MIN_SHARE * summary["packets"], (summary, counted)
    finally:
        dut.execute(["sudo", "sh", "-c", f"echo Y > {param}"])
    assert _dut_read(bench, param).strip() == "Y"


VLAN_CASES = {
    "vlan-vlan-4": (4, "10.99.10.112", "10.99.20.113"),
    "vlan-vlan-6": (6, "fd99:10::112", "fd99:20::113"),
    "vlan-port-4": (4, "10.99.10.112", "10.99.2.113"),
    "vlan-port-6": (6, "fd99:10::112", "fd99:2::113"),
}


@pytest.mark.portable
@pytest.mark.ask2
@pytest.mark.parametrize("case", sorted(VLAN_CASES))
def test_vlan_unicast_in_hardware(lab_report, case):
    # TCP: iperf3 UDP replies leave dell2 from the wrong VLAN address on vlan-vlan paths (rig limitation).
    family, source, destination = VLAN_CASES[case]
    bench = _bench()
    _assert_armed(bench)
    before = _records(bench)
    end = _iperf(bench, family, "-B", source, "-t", "3", address=destination)
    segments = end["sum_sent"]["bytes"] // 1448
    assert segments > 1000, end["sum_sent"]
    counted = _counted_since(bench, before)
    assert counted >= PACKETS_MIN_SHARE * segments, f"only {counted} of about {segments} VLAN segments offloaded"


def _nat66_counter(bench):
    out = bench.endpoint("dut").execute(["sudo", "nft", "list", "ruleset"], timeout=20).stdout
    match = re.search(r"counter packets (\d+) bytes \d+ masquerade comment \"SRC-NAT66-100\"", out)
    assert match, "SRC-NAT66-100 masquerade rule missing from the DUT ruleset"
    return int(match.group(1))


@pytest.mark.portable
@pytest.mark.ask2
def test_nat66_masquerade_in_hardware(lab_report):
    bench = _bench()
    _assert_armed(bench)
    nat_before = _nat66_counter(bench)
    before = _records(bench)
    end = _iperf(bench, 6, "-B", "fd99:1::112", "-u", "-b", "200M", "-l", "1200", "-t", "3")
    sent = end["sum"]["packets"]
    assert sent > 1000 and end["sum"]["lost_packets"] == 0, end["sum"]
    assert _nat66_counter(bench) > nat_before, "NAT66 rule saw no new flow; the NAT path was not exercised"
    counted = _counted_since(bench, before)
    assert counted >= PACKETS_MIN_SHARE * sent, f"only {counted} of {sent} NAT66 packets offloaded"


@pytest.fixture
def pppoe_routes():
    bench = _bench()
    lan, wan = bench.endpoint("lan"), bench.endpoint("wan")
    if lan.execute(["ip", "-br", "addr", "show", "ppp0"]).rc != 0:
        pytest.skip("dell1 has no ppp0 PPPoE session")
    rule = ["ip", "rule", "from", "10.99.50.1", "to", "10.99.2.0/24", "lookup", "150"]
    route_lan = ["ip", "route", "10.99.2.0/24", "dev", "ppp0", "table", "150"]
    route_wan = ["ip", "route", "10.99.50.1/32", "via", "10.99.2.185"]
    added = []
    if "lookup 150" not in lan.execute(["ip", "rule"]).stdout:
        assert lan.execute(["sudo", rule[0], "rule", "add", *rule[2:]]).rc == 0
        added.append((lan, ["sudo", "ip", "rule", "del", *rule[2:]]))
    assert lan.execute(["sudo", "ip", "route", "replace", *route_lan[2:]]).rc == 0
    assert wan.execute(["sudo", "ip", "route", "replace", *route_wan[2:]]).rc == 0
    added.append((wan, ["sudo", "ip", "route", "del", *route_wan[2:]]))
    yield bench
    for host, command in added:
        host.execute(command)


@pytest.mark.portable
@pytest.mark.ask2
def test_pppoe_decap_in_hardware(lab_report, pppoe_routes):
    bench = pppoe_routes
    _assert_armed(bench)
    before = _records(bench)
    end = _iperf(bench, 4, "-B", "10.99.50.1", "-u", "-b", "100M", "-l", "1200", "-t", "3")
    sent = end["sum"]["packets"]
    assert sent > 500 and end["sum"]["lost_packets"] == 0, end["sum"]
    counted = _counted_since(bench, before)
    assert counted >= PACKETS_MIN_SHARE * sent, f"only {counted} of {sent} PPPoE-decap packets offloaded"


# Features not built yet: these fail until the feature lands.

@pytest.mark.portable
@pytest.mark.ask2
def test_bridge_offload_available(lab_report):
    bench = _bench()
    assert _dut_read(bench, "/sys/module/ask/parameters/bridge_offload").strip() == "Y"
    check = bench.endpoint("dut").execute(["sudo", "ask-check"], timeout=120).stdout
    assert not re.search(r"software fallback:.*\bbridge\b", check), "ask-check still lists bridge as software"


@pytest.mark.portable
@pytest.mark.ask2
def test_ipsec_offload_available(lab_report):
    bench = _bench()
    assert _dut_read(bench, "/sys/module/ask/parameters/ipsec_offload").strip() == "Y"
    check = bench.endpoint("dut").execute(["sudo", "ask-check"], timeout=120).stdout
    assert not re.search(r"software fallback:.*\bIPsec\b", check), "ask-check still lists IPsec as software"


@pytest.mark.portable
@pytest.mark.ask2
def test_ingress_policer_offloaded_in_hardware(lab_report):
    bench = _bench()
    dut = bench.endpoint("dut")
    interface = bench.dut["lan_if"]
    try:
        assert dut.execute(["sudo", "tc", "qdisc", "add", "dev", interface, "ingress"]).rc == 0
        add = dut.execute(["sudo", "tc", "filter", "add", "dev", interface, "ingress", "pref", "99", "matchall",
                           "skip_sw", "action", "police", "rate", "1gbit", "burst", "1m", "conform-exceed", "drop"])
        assert add.rc == 0, f"tc rejected the hardware policer: {add.stdout}"
        shown = dut.execute(["sudo", "tc", "-s", "filter", "show", "dev", interface, "ingress"]).stdout
        assert "in_hw" in shown, shown
    finally:
        dut.execute(["sudo", "tc", "qdisc", "del", "dev", interface, "ingress"])
