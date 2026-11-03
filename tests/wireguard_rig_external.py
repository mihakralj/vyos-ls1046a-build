"""Host tests for externally managed WireGuard benchmark servers."""

import json
import os
from pathlib import Path
import subprocess

import pytest

pytestmark = pytest.mark.host
ROOT = Path(__file__).resolve().parents[1]
SINGLE = ROOT / "bin/testrig-wireguard.sh"
MULTI = ROOT / "bin/testrig-wireguard-multi.sh"

FAKE_SSH = r"""#!/usr/bin/env python3
import json
import os
import sys

args = sys.argv[1:]
index = next(i for i, arg in enumerate(args) if "@" in arg)
remote = args[index + 1:]
command = " ".join(remote)
with open(os.environ["FAKE_SSH_LOG"], "a") as log:
    log.write(json.dumps(command) + "\n")

if "iperf3 -c " in command:
    if os.environ.get("FAKE_IPERF_FAIL_PORT") and (
        "-p " + os.environ["FAKE_IPERF_FAIL_PORT"]
    ) in command:
        print(json.dumps({"error": "connection refused"}))
        sys.exit(1)
    rate = 2e9
    if os.environ.get("FAKE_IPERF_RATE_PORT") and (
        "-p " + os.environ["FAKE_IPERF_RATE_PORT"]
    ) in command:
        rate = float(os.environ["FAKE_IPERF_RATE_BPS"])
    print(json.dumps({
        "intervals": [{"sum": {"start": 0, "bits_per_second": rate, "omitted": False}}],
        "end": {"sum_sent": {"retransmits": 0}, "cpu_utilization_percent": {"host_total": 10}},
    }))
elif os.environ.get("FAKE_FW4_ERROR") and "nft insert rule inet fw4 forward" in command:
    sys.exit(1)
elif remote[:2] in (["sh", "-s"], ["bash", "-s"]):
    sys.stdin.read()
    print("cpu 10 0 0 90 0 0 0 0 0 0\ncpu0 10 0 0 90 0 0 0 0 0 0\n"
          "N eth3 100\nN eth4 100\n--\n"
          "cpu 20 0 0 180 0 0 0 0 0 0\ncpu0 20 0 0 180 0 0 0 0 0 0\n"
          "N eth3 110\nN eth4 110\n--")
elif command in ("wg genkey", "wg pubkey"):
    print("fake-key")
"""


def run_rig(tmp_path, script, extra):
    ssh = tmp_path / "ssh"
    ssh.write_text(FAKE_SSH)
    ssh.chmod(0o755)
    sleep = tmp_path / "sleep"
    sleep.write_text("#!/bin/sh\nexit 0\n")
    sleep.chmod(0o755)
    log = tmp_path / "ssh.log"
    env = os.environ | {
        "PATH": f"{tmp_path}:{os.environ['PATH']}",
        "TESTRIG_ENV": str(tmp_path / "no-testrig-env"),
        "GEN1": "admin@192.168.1.112",
        "GEN2": "admin@192.168.1.113",
        "ASK2_DUT": "vyos@192.168.1.185",
        "DUT": "vyos@192.168.1.185",
        "DUT_KIND": "ask2",
        "RIG_KEY": "",
        "RIG_NS": "",
        "WG_SKIP_SERVERS": "1",
        "WG_EXTERNAL_IPERF": "1",
        "WG_STATE_PATH": str(tmp_path / "state"),
        "WG_DUT_STAT": str(tmp_path / "dut.stat"),
        "FAKE_SSH_LOG": str(log),
        "J": str(tmp_path / "json"),
        "OUT": str(tmp_path / "result.csv"),
        "DIRS": "up down",
        "DUR": "3",
        "STEADY_FROM": "0",
        "REPS": "1",
        "WGFP": "0",
    }
    env.update(extra)
    args = ["bash", str(script), "run"]
    if script == SINGLE:
        args.append("wg-v4")
    result = subprocess.run(args, env=env, text=True, capture_output=True, timeout=30)
    commands = [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []
    return result, commands


def test_single_run_recreates_tunnel_without_touching_external_server(tmp_path):
    result, commands = run_rig(
        tmp_path, SINGLE, {"WG_IPERF_PORT": "15361", "RECREATE": "1"}
    )
    assert result.returncode == 0, result.stdout + result.stderr
    clients = [command for command in commands if "iperf3 -c " in command]
    assert len(clients) == 2
    assert all("-p 15361" in command for command in clients)
    assert all("iperf3 -s " not in command and "/tmp/wg-iperf" not in command for command in commands)
    assert (tmp_path / "result.csv").read_text().count("2.000") == 2


def test_multi_run_uses_external_port_per_tunnel_without_server_cleanup(tmp_path):
    result, commands = run_rig(
        tmp_path, MULTI, {"WG_IPERF_BASE": "15370", "TUNNELS": "2", "ACTIVE": "2"}
    )
    assert result.returncode == 0, result.stdout + result.stderr
    clients = [command for command in commands if "iperf3 -c " in command]
    assert len(clients) == 4
    assert sum("-p 15370" in command for command in clients) == 2
    assert sum("-p 15371" in command for command in clients) == 2
    assert all("iperf3 -s " not in command and "/tmp/wgm-iperf" not in command for command in commands)
    assert (tmp_path / "result.csv").read_text().count("4.000") == 2


@pytest.mark.parametrize("script", [SINGLE, MULTI])
def test_skipping_servers_for_run_requires_explicit_external_mode(tmp_path, script):
    result, commands = run_rig(tmp_path, script, {"WG_EXTERNAL_IPERF": "0"})
    assert result.returncode == 2
    assert "requires WG_EXTERNAL_IPERF=1" in result.stderr
    assert not commands


@pytest.mark.parametrize(
    ("script", "variable", "port"),
    [(SINGLE, "WG_IPERF_PORT", "0"), (SINGLE, "WG_IPERF_PORT", "65536"),
     (MULTI, "WG_IPERF_BASE", "65521"), (MULTI, "WG_IPERF_BASE", "01")],
)
def test_invalid_external_ports_are_rejected_before_remote_operations(tmp_path, script, variable, port):
    result, commands = run_rig(tmp_path, script, {variable: port})
    assert result.returncode == 2
    assert variable in result.stderr
    assert not commands


@pytest.mark.parametrize(
    ("script", "variable", "port"),
    [(SINGLE, "WG_IPERF_PORT", "15361"), (MULTI, "WG_IPERF_BASE", "15370")],
)
def test_custom_port_requires_external_server_management(tmp_path, script, variable, port):
    result, commands = run_rig(tmp_path, script, {variable: port, "WG_SKIP_SERVERS": "0"})
    assert result.returncode == 2
    assert "requires WG_SKIP_SERVERS=1" in result.stderr
    assert not commands


@pytest.mark.parametrize("script", [SINGLE, MULTI])
def test_vendor_can_use_existing_forward_policy_without_fw4(tmp_path, script):
    result, commands = run_rig(
        tmp_path, script, {"DUT_KIND": "vendor", "WG_SKIP_FW4": "1", "TUNNELS": "2"}
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "existing forwarding policy" in result.stderr
    assert not any("nft insert rule inet fw4 forward" in command or
                   "nft delete rule inet fw4 forward" in command for command in commands)


@pytest.mark.parametrize("script", [SINGLE, MULTI])
def test_vendor_fw4_insertion_failure_aborts_setup(tmp_path, script):
    result, commands = run_rig(
        tmp_path, script, {"DUT_KIND": "vendor", "FAKE_FW4_ERROR": "1", "TUNNELS": "2"}
    )
    assert result.returncode != 0
    assert any("nft insert rule inet fw4 forward" in command for command in commands)
    assert not any("iperf3 -c " in command for command in commands)


@pytest.mark.parametrize(
    ("script", "extra"),
    [
        (SINGLE, {"WG_IPERF_PORT": "15361", "FAKE_IPERF_FAIL_PORT": "15361"}),
        (MULTI, {"WG_IPERF_BASE": "15370", "TUNNELS": "2", "ACTIVE": "2",
                 "FAKE_IPERF_FAIL_PORT": "15371"}),
    ],
)
def test_external_server_failure_is_not_reported_as_success(tmp_path, script, extra):
    result, commands = run_rig(tmp_path, script, extra)
    assert result.returncode != 0
    assert "iperf" in result.stderr and "no traffic" in result.stderr
    assert all("iperf3 -s " not in command and "/tmp/wg-iperf" not in command
               and "/tmp/wgm-iperf" not in command for command in commands)


@pytest.mark.parametrize(
    ("script", "extra", "rate_bps", "expected_rate"),
    [
        (SINGLE, {"WG_IPERF_PORT": "15361", "FAKE_IPERF_RATE_PORT": "15361"},
         "15000000", "0.015"),
        (MULTI, {"WG_IPERF_BASE": "15370", "TUNNELS": "2", "ACTIVE": "2",
                 "FAKE_IPERF_RATE_PORT": "15371"}, "15000000", "2.015"),
        (SINGLE, {"WG_IPERF_PORT": "15361", "FAKE_IPERF_RATE_PORT": "15361"},
         "1000", "0.000001"),
        (MULTI, {"WG_IPERF_BASE": "15370", "TUNNELS": "2", "ACTIVE": "2",
                 "FAKE_IPERF_RATE_PORT": "15371"}, "1000", "2.000001"),
    ],
)
def test_slow_external_tunnel_is_recorded_not_reported_as_no_traffic(
    tmp_path, script, extra, rate_bps, expected_rate
):
    result, _ = run_rig(tmp_path, script, extra | {"FAKE_IPERF_RATE_BPS": rate_bps})
    assert result.returncode == 0, result.stdout + result.stderr
    assert (tmp_path / "result.csv").read_text().count(expected_rate) == 2


def test_zero_traffic_in_one_external_tunnel_fails(tmp_path):
    result, _ = run_rig(
        tmp_path, MULTI,
        {"WG_IPERF_BASE": "15370", "TUNNELS": "2", "ACTIVE": "2",
         "FAKE_IPERF_RATE_PORT": "15371", "FAKE_IPERF_RATE_BPS": "0"},
    )
    assert result.returncode != 0
    assert "no traffic" in result.stderr
