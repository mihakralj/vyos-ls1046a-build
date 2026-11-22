"""Host checks for exclusive ownership of bridge member ports."""

import os
import json
from pathlib import Path
import subprocess

import pytest

from testrig_iperf_measurement import tcp_result

pytestmark = pytest.mark.host
ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / "bin/testrig-vendor-bridge-port.sh"
RUNNER = ROOT / "bin/testrig-vendor-bridge.sh"
BRIDGE = "brt01234567"
OWNER = "ask2wan-0123456789abcdef-br"


def run_port(tmp_path, operation, master="", owner=OWNER):
    log = tmp_path / "ip-calls"
    ip = tmp_path / "ip"
    ip.write_text(
        '#!/bin/sh\n'
        'printf "%s\\n" "$*" >> "$CALLS"\n'
        'case "$*" in\n'
        '  "-o link show dev eth3")\n'
        f'    if [ -f "$DETACHED" ]; then master=""; '
        f'elif [ -f "$JOINED" ]; then master="master {BRIDGE}"; else master="$MASTER"; fi\n'
        '    printf "3: eth3: <UP> mtu 1500 %s\\n" "$master";;\n'
        f'  "-d -o link show dev {BRIDGE}")\n'
        f'    printf "9: {BRIDGE}: <UP> mtu 1500 bridge altname %s\\n" "$OWNER";;\n'
        '  "link set dev eth3 nomaster") touch "$DETACHED";;\n'
        f'  "link set dev eth3 master {BRIDGE}") touch "$JOINED";;\n'
        'esac\n'
    )
    ip.chmod(0o755)
    env = os.environ | {"PATH": f"{tmp_path}:{os.environ['PATH']}",
                        "CALLS": str(log), "MASTER": master, "OWNER": owner,
                        "DETACHED": str(tmp_path / "detached"),
                        "JOINED": str(tmp_path / "joined")}
    result = subprocess.run(["sh", str(HELPER), operation, "eth3", BRIDGE, OWNER],
                            env=env, capture_output=True, text=True, check=False)
    return result, log.read_text().splitlines() if log.exists() else []


def test_leave_refuses_foreign_master(tmp_path):
    result, calls = run_port(tmp_path, "leave", "master unrelated")
    assert result.returncode != 0
    assert not any("nomaster" in call for call in calls)


def test_leave_requires_owned_bridge(tmp_path):
    result, calls = run_port(tmp_path, "leave", f"master {BRIDGE}", "different-owner")
    assert result.returncode != 0
    assert not any("nomaster" in call for call in calls)


def test_leave_detaches_only_owned_bridge(tmp_path):
    result, calls = run_port(tmp_path, "leave", f"master {BRIDGE}")
    assert result.returncode == 0, result.stderr
    assert "link set dev eth3 nomaster" in calls


def test_join_refuses_existing_master(tmp_path):
    result, calls = run_port(tmp_path, "join", "master unrelated")
    assert result.returncode != 0
    assert not any(call.startswith("link set") for call in calls)


def test_join_attaches_only_to_owned_bridge(tmp_path):
    result, calls = run_port(tmp_path, "join")
    assert result.returncode == 0, result.stderr
    assert f"link set dev eth3 master {BRIDGE}" in calls


def test_bridge_rejects_scoped_ipv6_endpoint_before_bench_access():
    script = RUNNER.read_text()
    snippet = script[script.index('if ! python3 - "$BR_G1_IPV4"'):script.index("\nMODE=")]
    env = os.environ | {
        "BR_G1_IPV4": "192.0.2.1", "BR_G2_IPV4": "198.51.100.2",
        "BR_G1_IPV6": "fe80::1%zone", "BR_G2_IPV6": "2001:db8:2::2",
        "BR_PROBE1": "192.0.2.200", "BR_PROBE2": "198.51.100.200",
    }
    result = subprocess.run(
        ["bash", "-c", snippet], env=env, capture_output=True, text=True, check=False,
    )
    assert result.returncode != 0


def parse_result(tmp_path, sample, *, mtu=1500, mss=1448, iperf=None, direction="bidir"):
    parser = RUNNER.read_text().split("<<'PY'\n")[-1].split("\nPY\n", 1)[0]
    data = tmp_path / "iperf.json"
    stats = tmp_path / "dut.txt"
    default = tcp_result(duration=30, streams=8, bidir=direction == "bidir", reverse=direction == "down")
    default["start"]["tcp_mss_default"] = mss
    data.write_text(json.dumps(default if iperf is None else iperf))
    stats.write_text(sample)
    return subprocess.run(
        ["python3", "-", str(data), str(stats), "10", BRIDGE,
         "02:00:00:00:00:01", "02:00:00:00:00:02", "4", str(mtu),
         "30", "8", direction, "192.0.2.1", "198.51.100.2", "5277", "29"],
        input=parser, env=os.environ | {"PYTHONPATH": str(ROOT / "bin")},
        text=True, capture_output=True, check=False,
    )


def test_bridge_result_counts_only_matching_hardware_pairs(tmp_path):
    sample = (
        "cpu 10 0 0 100 0 0 0 0 0 0\nRX_eth3 20\nRX_eth4 30\nL2_BEGIN\n"
        f"l2flow br={BRIDGE} src=02:00:00:00:00:01 dst=02:00:00:00:00:02 "
        "port_in=eth3 port_out=eth4 type=0x0800 packets=10\nL2_END\n"
        "cpu 30 0 0 180 0 0 0 0 0 0\nRX_eth3 40\nRX_eth4 50\nL2_BEGIN\n"
        f"l2flow br={BRIDGE} src=02:00:00:00:00:01 dst=02:00:00:00:00:02 "
        "port_in=eth3 port_out=eth4 type=0x0800 packets=35\n"
        "l2flow br=other src=02:00:00:00:00:01 dst=02:00:00:00:00:02 "
        "port_in=eth3 port_out=eth4 type=0x0800 packets=900\nL2_END\n"
    )
    result = parse_result(tmp_path, sample)
    assert result.returncode == 0, result.stderr
    assert result.stdout.split() == ["10.000", "20.0", "25"]


def test_bridge_result_rejects_missing_dut_ingress(tmp_path):
    sample = (
        "cpu 10 0 0 100 0 0 0 0 0 0\nRX_eth3 20\nRX_eth4 30\nL2_BEGIN\nL2_UNAVAILABLE\nL2_END\n"
        "cpu 30 0 0 180 0 0 0 0 0 0\nRX_eth3 20\nRX_eth4 50\nL2_BEGIN\nL2_UNAVAILABLE\nL2_END\n"
    )
    result = parse_result(tmp_path, sample)
    assert result.returncode != 0
    assert "both ports" in result.stderr


def active_port_sample():
    return (
        "cpu 10 0 0 100 0 0 0 0 0 0\nRX_eth3 20\nRX_eth4 30\nL2_BEGIN\nL2_UNAVAILABLE\nL2_END\n"
        "cpu 30 0 0 180 0 0 0 0 0 0\nRX_eth3 40\nRX_eth4 50\nL2_BEGIN\nL2_UNAVAILABLE\nL2_END\n"
    )


def test_bridge_jumbo_result_rejects_standard_size_tcp(tmp_path):
    result = parse_result(tmp_path, active_port_sample(), mtu=9000, mss=1448)
    assert result.returncode != 0
    assert "MSS" in result.stderr


def jumbo_bidir_iperf():
    data = tcp_result(duration=30, streams=8, bidir=True)
    data["start"].update(tcp_mss_default=8948, connected=[
        {"local_host": "192.0.2.1", "remote_host": "198.51.100.2", "remote_port": 5277}
        for _ in range(16)])
    return data


@pytest.mark.parametrize(
    "defect", ["short", "missing_reverse", "wrong_endpoint", "wrong_streams"]
)
def test_jumbo_bridge_rejects_incomplete_iperf_evidence(tmp_path, defect):
    iperf = jumbo_bidir_iperf()
    if defect == "short":
        iperf["intervals"] = iperf["intervals"][:12]
    elif defect == "missing_reverse":
        iperf["intervals"][15].pop("sum_bidir_reverse")
    elif defect == "wrong_endpoint":
        iperf["start"]["connected"][0]["remote_host"] = "198.51.100.3"
    else:
        iperf["start"]["test_start"]["num_streams"] = 4
    result = parse_result(tmp_path, active_port_sample(), mtu=9000, iperf=iperf)
    assert result.returncode != 0


def test_jumbo_bridge_accepts_complete_bidir_evidence(tmp_path):
    result = parse_result(tmp_path, active_port_sample(), mtu=9000, iperf=jumbo_bidir_iperf())
    assert result.returncode == 0, result.stderr


def test_bridge_stalled_reverse_is_valid_evidence(tmp_path):
    iperf = jumbo_bidir_iperf()
    for interval in iperf["intervals"]:
        interval["sum_bidir_reverse"].update(bits_per_second=0, bytes=0)
    result = parse_result(tmp_path, active_port_sample(), mtu=9000, iperf=iperf)
    assert result.returncode == 0, result.stderr
    assert result.stdout.split()[0] == "8.000"


def test_bridge_excludes_the_extended_terminal_interval(tmp_path):
    iperf = jumbo_bidir_iperf()
    for sample in iperf["intervals"][-1].values():
        sample.update(end=39, seconds=10, bits_per_second=0, bytes=0)
    result = parse_result(tmp_path, active_port_sample(), mtu=9000, iperf=iperf)
    assert result.returncode == 0, result.stderr
    assert result.stdout.split()[0] == "10.000"


def test_jumbo_bridge_verification_stops_on_flowtable_health_failure():
    script = RUNNER.read_text()
    verify = script[script.index("verify_bridge(){"):script.index("\nprobe_topology\n")]
    result = subprocess.run(
        ["bash", "-c",
         'fail(){ echo "ERROR: $*" >&2; return 1; }\n'
         'check_boot(){ :; }\n'
         'd(){ case "$*" in *ask_flowtable/status*) return 1;; *) return 0;; esac; }\n'
         'BR=brtest OWNER=owner TEST_MTU=9000 JUMBO_BPID=32 RIG_JUMBO_POOL_MIN=6000\n'
         f'{verify}\nverify_bridge\n'],
        capture_output=True, text=True, check=False,
    )
    assert result.returncode != 0
    assert "health" in result.stderr


def run_topology_probe(tmp_path, *, leak=False, drops=0, early=False, expired=False,
                       probe_failed=False):
    script = RUNNER.read_text()
    probe = script[script.index("probe_topology(){"):script.index("\nverify_bridge(){")]
    harness = """
GEN1=gen1 GEN2=gen2 GEN_IF_NS=test0
BR_PROBE1=192.0.2.1 BR_PROBE2=192.0.2.2
LEASE=$1 LEAK=$2 DROPS=$3 EARLY=$4 EXPIRED=$5 PROBE_FAILED=$6
fixture_mac1=02:00:00:00:00:01 fixture_mac2=02:00:00:00:00:02
sleep() { :; }
say() { :; }
fail() { echo "ERROR: $*" >&2; return 1; }
g() {
    host=$1; shift
    case "$*" in
      *"/address")
        if [ "$host" = "$GEN1" ]; then echo "$fixture_mac1"; else echo "$fixture_mac2"; fi ;;
      *tcpdump*)
        if [ "$host" = "$GEN1" ]; then
            printf 'arp %s\\narp %s\\n' "$fixture_mac1" "$fixture_mac1"
        else
            printf 'arp %s\\narp %s\\n' "$fixture_mac2" "$fixture_mac2"
        fi
        case " $* " in
          *" -c 2 "*)
            [ "$host" != "$GEN1" ] || [ "$EXPIRED" = 0 ] || touch "$LEASE/expired"
            echo "$DROPS packets dropped by kernel" >&2
            return 0 ;;
        esac
        if [ "$host" = "$GEN1" ] && [ "$EARLY" = 1 ]; then return 0; fi
        if [ "$host" = "$GEN1" ] && [ "$EXPIRED" = 1 ]; then
            while [ ! -e "$LEASE/probe1" ]; do command sleep 0.01; done
            touch "$LEASE/expired"
            echo "$DROPS packets dropped by kernel" >&2
            touch "$LEASE/done-$host"
            return 124
        fi
        while [ ! -e "$LEASE/release" ]; do command sleep 0.01; done
        if [ "$host" = "$GEN1" ] && [ "$LEAK" = 1 ]; then
            printf 'arp %s\\n' "$fixture_mac2"
        fi
        echo "$DROPS packets dropped by kernel" >&2
        touch "$LEASE/done-$host"
        return 124 ;;
      *ping*)
        if [ "$host" = "$GEN1" ]; then
            touch "$LEASE/probe1"
            [ "$PROBE_FAILED" = 0 ] || return 2
        else
            if [ "$EXPIRED" = 1 ]; then
                while [ ! -e "$LEASE/expired" ]; do command sleep 0.01; done
                command sleep 0.05
            fi
            (command sleep 0.05; touch "$LEASE/release") &
        fi
        return 0 ;;
      *) return 1 ;;
    esac
}
"""
    return subprocess.run(
        ["bash", "-c", harness + probe + "\nprobe_topology\n", "test",
         str(tmp_path), str(int(leak)), str(drops), str(int(early)), str(int(expired)),
         str(int(probe_failed))],
        capture_output=True, text=True, check=False, timeout=5,
    )


def test_bridge_topology_accepts_complete_isolated_captures(tmp_path):
    result = run_topology_probe(tmp_path)
    assert result.returncode == 0, result.stderr


def test_bridge_topology_rejects_late_cross_segment_packet(tmp_path):
    result = run_topology_probe(tmp_path, leak=True)
    assert result.returncode != 0
    assert "share an untagged" in result.stderr


def test_bridge_topology_rejects_capture_drops(tmp_path):
    result = run_topology_probe(tmp_path, drops=1)
    assert result.returncode != 0
    assert "capture" in result.stderr


def test_bridge_topology_rejects_early_capture_exit(tmp_path):
    result = run_topology_probe(tmp_path, early=True)
    assert result.returncode != 0
    assert "capture" in result.stderr


def test_bridge_topology_rejects_timeout_before_both_probes(tmp_path):
    result = run_topology_probe(tmp_path, expired=True)
    assert result.returncode != 0
    assert "capture" in result.stderr


def test_bridge_topology_waits_for_captures_after_probe_failure(tmp_path):
    result = run_topology_probe(tmp_path, probe_failed=True)
    assert result.returncode != 0
    assert "probe" in result.stderr
    assert (tmp_path / "done-gen1").exists()
    assert (tmp_path / "done-gen2").exists()
