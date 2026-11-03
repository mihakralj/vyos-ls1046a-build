"""Host tests for the supervised WireGuard soak gate."""

import json
import os
from pathlib import Path
import subprocess

import pytest

pytestmark = pytest.mark.host
SCRIPT = Path(__file__).resolve().parents[1] / "bin/testrig-wgfp-soak.sh"

FAKE_SSH = r"""#!/usr/bin/env python3
import json
import os
from pathlib import Path
import subprocess
import sys
import time

state_file = Path(os.environ["FAKE_SSH_STATE"])
log_file = Path(os.environ["FAKE_SSH_LOG"])
state = json.loads(state_file.read_text()) if state_file.exists() else {}
args = sys.argv[1:]
index = next(i for i, arg in enumerate(args) if "@192.168.1." in arg)
host = args[index]
remote = args[index + 1:]
command = " ".join(remote)
with log_file.open("a") as log:
    log.write(json.dumps([host, remote]) + "\n")
scenario = os.environ.get("FAKE_SSH_SCENARIO", "")
result = ""
status = 0

if remote[:4] == ["sudo", "-n", "python3", "-"]:
    interface = remote[4]
    key = interface + "_reads"
    state[key] = state.get(key, 0) + 1
    if scenario == "elapsed" and interface == "eth3" and state[key] == 2:
        time.sleep(1.1)
    drift = scenario in {"pool_drift", "normal_consumption"}
    count = 2000 if drift and interface == "eth3" and state[key] >= 3 else 2161
    result = f"{3 if interface == 'eth3' else 4}:{count}"
elif remote[:3] == ["sh", "-s", "--"]:
    pass
elif "iperf3 -s -1" in command:
    state["pid"] = state.get("pid", 1000) + 1
    result = str(state["pid"])
elif "iperf3 -c " in command:
    if scenario == "client_fail":
        status = 1
    else:
        state["traffic"] = True
        state["pid"] = state.get("pid", 1000) + 1
        result = str(state["pid"])
elif "awk '{print $22}' /proc/" in command:
    result = "12345"
elif "test ! -e " in command or "ss -H -ltn " in command:
    pass
elif "echo wg0 | sudo tee" in command:
    state["bound"] = True
elif "echo off | sudo tee" in command:
    state["bound"] = False
elif "cat /sys/module/wireguard/parameters/wgfp_peer" in command:
    bound = state.get("bound", scenario == "already_bound")
    result = ("wg0 sealed=" + ("200" if state.get("traffic") else "100") + " punted=0") if bound else "off"
elif command == "date +%s":
    result = "1234567890"
elif "pcd-snapshot capture" in command or "rm -f " in command:
    pass
elif "pcd-snapshot diff" in command:
    result = "rc=0"
elif "/usr/sbin/ethtool -S " in command:
    interface = command.split()[-1]
    consumed = scenario == "normal_consumption" and interface == "eth3" and state.get("eth3_reads", 0) >= 3
    result = f"bpool [TOTAL]: {2000 if consumed else 2161}"
elif "cat /sys/kernel/debug/dpaa_wgfp/stats" in command:
    state["stats_reads"] = state.get("stats_reads", 0) + 1
    if (scenario == "unverified_count" or
            (scenario == "lost_count_marker" and state["stats_reads"] >= 4)):
        encoding = ""
    elif scenario == "unverified_count_suffix":
        encoding = " frm_cnt_encoding=hostile"
    else:
        encoding = " frm_cnt_encoding=host"
    result = f"xmit_fail=0{encoding}\nsink0 frm_cnt={10 if scenario == 'stuck_sink' else 0}"
elif "cat /sys/kernel/debug/fman_pcd/0/fe_flow" in command:
    status = 1 if scenario == "flow_read_fail" else 0
    result = "empty"
elif "journalctl" in command:
    pass
elif remote[:2] == ["python3", "-"]:
    program = sys.stdin.read()
    duration = float(remote[-1])
    seconds = duration / 2 if scenario == "short_result" else duration
    rate = 0 if scenario == "bad_result" else 1_000_000_000
    recv_seconds = duration / 2 if scenario == "short_receive" else seconds
    recv_rate = 0 if scenario == "bad_receive" else rate
    data = {"end": {
        "sum_sent": {"seconds": seconds, "bits_per_second": rate},
        "sum_received": {"seconds": recv_seconds, "bits_per_second": recv_rate},
    }, "intervals": [
        {"sum": {"start": 0, "end": duration, "seconds": duration,
                 "bits_per_second": rate, "omitted": False}},
    ]}
    if scenario == "reported_error":
        data["error"] = "connection terminated"
    elif scenario == "stalled_interval":
        data["intervals"][0]["sum"]["bits_per_second"] = 0
    elif scenario == "missing_intervals":
        del data["intervals"]
    elif scenario == "nonfinite_interval":
        data["intervals"][0]["sum"]["bits_per_second"] = float("nan")
    elif scenario == "interval_gap":
        data["intervals"][0]["sum"]["start"] = 0.2
    elif scenario == "coarse_interval":
        data["intervals"][0]["sum"].update(end=2.0, seconds=2.0)
    elif scenario == "short_zero_tail":
        data["intervals"] = [
            {"sum": {"start": 0, "end": 0.9, "seconds": 0.9,
                     "bits_per_second": rate, "omitted": False}},
            {"sum": {"start": 0.9, "end": 1.0, "seconds": 0.1,
                     "bits_per_second": 0, "omitted": False}},
        ]
    elif scenario == "short_zero_middle":
        data["intervals"] = [
            {"sum": {"start": 0, "end": 0.4, "seconds": 0.4,
                     "bits_per_second": rate, "omitted": False}},
            {"sum": {"start": 0.4, "end": 0.5, "seconds": 0.1,
                     "bits_per_second": 0, "omitted": False}},
            {"sum": {"start": 0.5, "end": 1.0, "seconds": 0.5,
                     "bits_per_second": rate, "omitted": False}},
        ]
    result_file = state_file.with_name("iperf.json")
    result_file.write_text(json.dumps(data))
    parsed = subprocess.run(
        [sys.executable, "-c", program, str(result_file), remote[-1]],
        text=True, capture_output=True,
    )
    result = parsed.stdout.strip()
    status = parsed.returncode
    if parsed.stderr:
        print(parsed.stderr, file=sys.stderr, end="")
else:
    print("unexpected fake SSH command: " + command, file=sys.stderr)
    status = 2
state_file.write_text(json.dumps(state))
if result:
    print(result)
sys.exit(status)
"""


def run_soak(tmp_path, scenario, port_base="5241"):
    ssh = tmp_path / "ssh"
    ssh.write_text(FAKE_SSH)
    ssh.chmod(0o755)
    sleep = tmp_path / "sleep"
    sleep.write_text(
        '#!/bin/sh\n'
        'if [ "$FAKE_SSH_SCENARIO" = elapsed ] && [ "$1" = 1 ]; then /bin/sleep 1; fi\n'
        'exit 0\n'
    )
    sleep.chmod(0o755)
    state_file = tmp_path / "state.json"
    log_file = tmp_path / "ssh.log"
    env = os.environ | {
        "PATH": f"{tmp_path}:{os.environ['PATH']}",
        "DUR": "1",
        "PORT_BASE": port_base,
        "FAKE_SSH_STATE": str(state_file),
        "FAKE_SSH_LOG": str(log_file),
        "FAKE_SSH_SCENARIO": scenario,
    }
    result = subprocess.run(
        ["bash", str(SCRIPT), "soak"], env=env, text=True,
        capture_output=True, timeout=30,
    )
    commands = [json.loads(line) for line in log_file.read_text().splitlines()]
    state = json.loads(state_file.read_text())
    return result, commands, state


def test_soak_passes_only_with_real_pool_recovery_and_results(tmp_path):
    result, commands, state = run_soak(tmp_path, "")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "PASS: BMan pool drift matches software-accounted consumption" in result.stdout
    assert "PASS: sink queues empty" in result.stdout
    assert "PASS: fast path sealed packets" in result.stdout
    assert state["bound"] is False
    stopped = [args for _, args in commands if args[:3] == ["sh", "-s", "--"]]
    assert len(stopped) == 8
    assert all(len(args) == 5 and args[3].isdigit() and args[4] == "12345" for args in stopped)


@pytest.mark.parametrize(
    ("scenario", "message"),
    [
        ("pool_drift", "BMan/accounting drift"),
        ("flow_read_fail", "cannot read flow table"),
        ("bad_result", "invalid iperf result"),
        ("short_result", "invalid iperf result"),
        ("short_receive", "invalid iperf result"),
        ("bad_receive", "invalid iperf result"),
        ("reported_error", "invalid iperf result"),
        ("stalled_interval", "invalid iperf result"),
        ("missing_intervals", "invalid iperf result"),
        ("nonfinite_interval", "invalid iperf result"),
        ("interval_gap", "invalid iperf result"),
        ("coarse_interval", "invalid iperf result"),
        ("short_zero_middle", "invalid iperf result"),
        ("client_fail", "could not start iperf client"),
        ("stuck_sink", "sink queues not empty"),
        ("lost_count_marker", "sink queues not empty or unavailable"),
    ],
)
def test_soak_fails_closed_and_unbinds(tmp_path, scenario, message):
    result, commands, state = run_soak(tmp_path, scenario)
    assert result.returncode != 0
    assert message in result.stdout + result.stderr
    assert state["bound"] is False
    if scenario == "client_fail":
        assert len([args for _, args in commands if args[:3] == ["sh", "-s", "--"]]) == 4


def test_soak_does_not_replace_an_existing_binding(tmp_path):
    result, commands, _ = run_soak(tmp_path, "already_bound")
    assert result.returncode != 0
    assert "fast path already bound" in result.stderr
    assert not any("echo off" in " ".join(args) or "rm -f" in " ".join(args)
                   for _, args in commands)


@pytest.mark.parametrize("scenario", ["unverified_count", "unverified_count_suffix"])
def test_soak_rejects_unverified_queue_counters_before_binding(tmp_path, scenario):
    result, commands, state = run_soak(tmp_path, scenario)
    assert result.returncode != 0
    assert "sink frm_cnt byte order is unverified" in result.stderr
    assert not state.get("bound", False)
    assert not any("echo wg0" in " ".join(args) or "iperf3 " in " ".join(args)
                   or "pcd-snapshot capture" in " ".join(args)
                   for _, args in commands)


def test_soak_uses_an_isolated_port_range(tmp_path):
    result, commands, _ = run_soak(tmp_path, "", port_base="15341")
    assert result.returncode == 0, result.stdout + result.stderr
    servers = [" ".join(args) for _, args in commands if "iperf3 -s -1" in " ".join(args)]
    assert len(servers) == 4
    assert all(f"-p {port} " in command
               for port, command in zip(range(15341, 15345), servers))


def test_normal_software_consumption_is_not_mistaken_for_a_pool_leak(tmp_path):
    result, _, _ = run_soak(tmp_path, "normal_consumption")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "PASS: BMan pool drift matches software-accounted consumption" in result.stdout


def test_short_empty_final_interval_is_not_a_stall(tmp_path):
    result, _, _ = run_soak(tmp_path, "short_zero_tail")
    assert result.returncode == 0, result.stdout + result.stderr


def test_sample_time_includes_telemetry_overhead(tmp_path):
    result, _, _ = run_soak(tmp_path, "elapsed")
    assert result.returncode == 0, result.stdout + result.stderr
    sample = next(line for line in result.stdout.splitlines() if "t=" in line)
    elapsed = int(sample.split("t=", 1)[1].split("s", 1)[0])
    assert elapsed >= 2
