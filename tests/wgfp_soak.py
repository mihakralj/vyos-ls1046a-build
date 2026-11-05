"""Host tests for the supervised WireGuard soak gate."""

import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import time

import pytest

pytestmark = pytest.mark.host
SCRIPT = Path(__file__).resolve().parents[1] / "bin/testrig-wgfp-soak.sh"
STOP = Path(__file__).resolve().parents[1] / "bin/testrig-wgfp-stop.py"

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
    if scenario in {"client_fail", "binding_changed_on_error"}:
        if scenario == "binding_changed_on_error":
            state["binding_other"] = True
        status = 1
    else:
        state["traffic"] = True
        if scenario == "binding_changed_during_soak":
            state["binding_other"] = True
        state["pid"] = state.get("pid", 1000) + 1
        result = str(state["pid"])
elif "awk '{print $22}' /proc/" in command:
    result = "12345"
elif "test ! -e " in command or "ss -H -ltn " in command:
    pass
elif "bind " in command and "wgfp_peer_lease" in command:
    state["bound"] = True
elif "stop " in command and "wgfp_peer_lease" in command:
    state["bound"] = False
elif "cat /sys/module/wireguard/parameters/wgfp_peer" in command:
    bound = state.get("bound", scenario == "already_bound")
    dev = "other" if state.get("binding_other", scenario == "already_bound") else "wg0"
    result = ("on sealed=" + ("200" if state.get("traffic") else "100") +
              " punted=0 dev=" + dev) if bound else "off"
elif command == "date +%s":
    result = "1234567890"
elif "pcd-snapshot capture" in command:
    if scenario == "binding_changed_before_setup":
        state["bound"] = True
        state["binding_other"] = True
elif "rm -f " in command:
    pass
elif "python3 -c " in command and "pidfd_open" in command:
    status = 1 if scenario == "no_pidfd" else 0
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
    elif scenario == "unknown_count":
        encoding = " frm_cnt_encoding=unknown"
    elif scenario == "conflicting_count_markers":
        encoding = " frm_cnt_encoding=host frm_cnt_encoding=unknown"
    elif scenario == "duplicate_count_marker":
        encoding = " frm_cnt_encoding=host frm_cnt_encoding=host"
    else:
        encoding = " frm_cnt_encoding=host"
    if scenario == "missing_ern_before" or (
            scenario == "missing_ern_final" and state["stats_reads"] >= 4):
        ern = ""
    elif scenario == "duplicate_ern_final" and state["stats_reads"] >= 4:
        ern = " xmit_ern=0 xmit_ern=0"
    elif scenario == "ern_increased" and state.get("traffic"):
        ern = " xmit_ern=1"
    else:
        ern = f" xmit_ern={7 if scenario == 'existing_ern' else 0}"
    result = f"xmit_fail=0{ern}{encoding}\nsink0 frm_cnt={10 if scenario == 'stuck_sink' else 0} byte_cnt={14340 if scenario == 'stuck_sink' else 0}"
elif "cat /sys/kernel/debug/fman_pcd/0/fe_flow" in command:
    status = 1 if scenario == "flow_read_fail" else 0
    result = "empty"
elif "journalctl" in command:
    pass
elif remote[:2] == ["python3", "-"] and len(remote) == 6:
    sys.stdin.read()
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
        "WG_LEASE_TOKEN": "0123456789abcdef0123456789abcdef",
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
    stopped = [args for _, args in commands if args[:2] == ["python3", "-"] and len(args) == 6]
    assert len(stopped) == 8
    assert all(args[2].isdigit() and args[3] == "12345" and args[5].isdigit()
               for args in stopped)


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
        ("missing_ern_before", "missing or invalid xmit_ern counter"),
        ("missing_ern_final", "xmit_ern 0 -> unavailable"),
        ("duplicate_ern_final", "xmit_ern 0 -> unavailable"),
        ("ern_increased", "xmit_ern 0 -> 1"),
    ],
)
def test_soak_fails_closed_and_unbinds(tmp_path, scenario, message):
    result, commands, state = run_soak(tmp_path, scenario)
    assert result.returncode != 0
    assert message in result.stdout + result.stderr
    assert state["bound"] is False
    if scenario == "client_fail":
        assert len([args for _, args in commands
                    if args[:2] == ["python3", "-"] and len(args) == 6]) == 4


def test_soak_does_not_replace_an_existing_binding(tmp_path):
    result, commands, _ = run_soak(tmp_path, "already_bound")
    assert result.returncode != 0
    assert "fast path already bound" in result.stderr
    assert not any("wgfp_peer_lease" in " ".join(args) or "rm -f" in " ".join(args)
                   for _, args in commands)


@pytest.mark.parametrize("scenario", [
    "binding_changed_before_setup", "binding_changed_during_soak", "binding_changed_on_error",
])
def test_soak_does_not_unbind_another_users_fast_path(tmp_path, scenario):
    result, commands, state = run_soak(tmp_path, scenario)
    assert result.returncode != 0
    assert state["bound"] is True
    assert state["binding_other"] is True
    assert not any("stop " in " ".join(args) and "wgfp_peer_lease" in " ".join(args) for _, args in commands)
    if scenario == "binding_changed_before_setup":
        assert not any("bind " in " ".join(args) for _, args in commands)


def test_soak_rejects_missing_pidfd_support_before_binding(tmp_path):
    result, commands, _ = run_soak(tmp_path, "no_pidfd")
    assert result.returncode != 0
    assert "generator lacks Python pidfd support" in result.stderr
    assert not any("bind " in " ".join(args) for _, args in commands)


@pytest.mark.parametrize(
    "scenario", [
        "unverified_count", "unverified_count_suffix", "unknown_count",
        "conflicting_count_markers", "duplicate_count_marker",
    ]
)
def test_soak_rejects_unverified_queue_counters_before_binding(tmp_path, scenario):
    result, commands, state = run_soak(tmp_path, scenario)
    assert result.returncode != 0
    assert "sink frm_cnt byte order is unverified" in result.stderr
    assert not state.get("bound", False)
    assert not any("bind " in " ".join(args) or "iperf3 " in " ".join(args)
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


def test_existing_enqueue_rejections_do_not_fail_a_clean_soak(tmp_path):
    result, _, _ = run_soak(tmp_path, "existing_ern")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "PASS: no asynchronous enqueue rejections" in result.stdout


def test_short_empty_final_interval_is_not_a_stall(tmp_path):
    result, _, _ = run_soak(tmp_path, "short_zero_tail")
    assert result.returncode == 0, result.stdout + result.stderr


def test_sample_time_includes_telemetry_overhead(tmp_path):
    result, _, _ = run_soak(tmp_path, "elapsed")
    assert result.returncode == 0, result.stdout + result.stderr
    sample = next(line for line in result.stdout.splitlines() if "t=" in line)
    elapsed = int(sample.split("t=", 1)[1].split("s", 1)[0])
    assert elapsed >= 2


def process_start(pid):
    stat = Path(f"/proc/{pid}/stat").read_text()
    return int(stat[stat.rfind(")") + 2:].split()[19])


def test_stop_rejects_reused_pid_without_signalling():
    process = subprocess.Popen(["sleep", "30"])
    try:
        started = process_start(process.pid)
        result = subprocess.run(
            ["python3", str(STOP), str(process.pid), str(started + 1), "client", "5241"],
            capture_output=True, text=True, timeout=5,
        )
        assert result.returncode != 0
        assert "was reused" in result.stderr
        assert process.poll() is None
    finally:
        process.terminate()
        process.wait(timeout=5)


@pytest.mark.skipif(shutil.which("iperf3") is None, reason="iperf3 is required")
def test_stop_checks_command_and_terminates_only_the_owned_iperf():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    process = subprocess.Popen(
        ["iperf3", "-s", "-1", "-p", str(port)],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        for _ in range(50):
            if Path(f"/proc/{process.pid}/exe").resolve().name == "iperf3":
                break
            time.sleep(0.01)
        assert process.poll() is None
        started = process_start(process.pid)
        wrong = subprocess.run(
            ["python3", str(STOP), str(process.pid), str(started), "server", str(port % 65535 + 1)],
            capture_output=True, text=True, timeout=5,
        )
        assert wrong.returncode != 0
        assert "does not match" in wrong.stderr
        assert process.poll() is None
        stopped = subprocess.run(
            ["python3", str(STOP), str(process.pid), str(started), "server", str(port)],
            capture_output=True, text=True, timeout=8,
        )
        assert stopped.returncode == 0, stopped.stderr
        process.wait(timeout=5)
    finally:
        if process.poll() is None:
            process.terminate()
            process.wait(timeout=5)
