"""Host tests for externally managed WireGuard benchmark servers."""

import csv
import json
import os
from pathlib import Path
import re
import shutil
import subprocess

import pytest

pytestmark = pytest.mark.host
ROOT = Path(__file__).resolve().parents[1]
SINGLE = ROOT / "bin/testrig-wireguard.sh"
MULTI = ROOT / "bin/testrig-wireguard-multi.sh"
MATRIX = ROOT / "bin/wg-matrix.sh"

FAKE_SSH = r"""#!/usr/bin/env python3
import json
import os
from pathlib import Path
import re
import subprocess
import sys

args = sys.argv[1:]
index = next(i for i, arg in enumerate(args) if "@" in arg)
remote = args[index + 1:]
command = " ".join(remote)
with open(os.environ["FAKE_SSH_LOG"], "a") as log:
    log.write(json.dumps(command) + "\n")
if os.environ.get("FAKE_SSH_HOST_LOG"):
    with open(os.environ["FAKE_SSH_HOST_LOG"], "a") as log:
        log.write(args[index] + "\n")

if os.environ.get("FAKE_PROBE_EXEC") and "ip link add wgc" in command:
    sys.exit(subprocess.run(["sh", "-c", command]).returncode)
elif "iperf3 -c " in command:
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
    end = {
        "sum_sent": {"retransmits": int(os.environ.get("FAKE_FORWARD_RETRANS", "0"))},
        "cpu_utilization_percent": {"host_total": 10},
    }
    if "--bidir" in command and not os.environ.get("FAKE_MISSING_REVERSE"):
        end["sum_sent_bidir_reverse"] = {
            "retransmits": int(os.environ.get("FAKE_REVERSE_RETRANS", "0"))
        }
    interval = {"sum": {"start": 0, "bits_per_second": rate, "omitted": False}}
    if "--bidir" in command and not os.environ.get("FAKE_MISSING_REVERSE_INTERVAL"):
        interval["sum_bidir_reverse"] = {
            "start": 0, "bits_per_second": rate, "omitted": False
        }
    print(json.dumps({
        "intervals": [interval],
        "end": end,
    }))
elif os.environ.get("FAKE_FW4_ERROR") and "nft insert rule inet fw4 forward" in command:
    sys.exit(1)
elif "python3 - start /tmp/wgrig-srv-" in command and os.environ.get("FAKE_SERVER_START_FAIL"):
    sys.exit(int(os.environ["FAKE_SERVER_START_FAIL"]))
elif "python3 - identity /tmp/wgrig-srv-" in command:
    print("123:456:" + command.split()[-1])
elif "sh -s -- add plain " in command and os.environ.get("FAKE_ROUTE_FAIL"):
    sys.exit(int(os.environ["FAKE_ROUTE_FAIL"]))
elif "tee /sys/module/wireguard/parameters/wgfp_peer" in command:
    if os.environ.get("FAKE_BIND_ERROR"):
        sys.exit(1)
    state_file = Path(os.environ["FAKE_BIND_STATE"])
    match = re.search(r"echo\s+(?:'([^']*)'|(\S+))\s+\|", command)
    if not match:
        sys.exit(1)
    state_file.write_text((match.group(1) or match.group(2)).strip())
elif "cat /sys/kernel/debug/dpaa_wgfp/stats" in command:
    if os.environ.get("FAKE_QMAN_ERROR"):
        sys.exit(1)
    state = os.environ.get(
        "FAKE_QMAN_STATE",
        "touch=2 sinks=1 xmit_ok=123 xmit_fail=0 xmit_ern=0\n"
        "sink0 cpu=0 fqid=0x2be frames=10 errs=0 sg=0")
    if os.environ.get("FAKE_QMAN_CHANGE"):
        from pathlib import Path
        count_file = Path(os.environ["FAKE_QMAN_READ_COUNT"])
        count = int(count_file.read_text()) + 1 if count_file.exists() else 1
        count_file.write_text(str(count))
        if count > 1:
            key = os.environ["FAKE_QMAN_CHANGE"]
            state = state.replace(f"{key}=0", f"{key}=1")
    print(state)
elif "cat /sys/module/wireguard/parameters/wgfp_peer" in command:
    if os.environ.get("FAKE_WGFP_ERROR"):
        sys.exit(1)
    state_file = Path(os.environ["FAKE_BIND_STATE"])
    names = state_file.read_text() if state_file.exists() else "off"
    default = f"on sealed=123 rx=456 dev={names}" if names != "off" else "off"
    if os.environ.get("FAKE_WGFP_CONFLICT_AT"):
        count_file = Path(os.environ["FAKE_WGFP_READ_COUNT"])
        count = int(count_file.read_text()) + 1 if count_file.exists() else 1
        count_file.write_text(str(count))
        if count >= int(os.environ["FAKE_WGFP_CONFLICT_AT"]):
            default = "on sealed=2 rx=3 dev=existing"
    print(os.environ.get("FAKE_WGFP_STATE", default))
elif remote[:2] in (["sh", "-s"], ["bash", "-s"]):
    sys.stdin.read()
    if os.environ.get("FAKE_STAT_ERROR"):
        sys.exit(1)
    print("cpu 10 0 0 90 0 0 0 0 0 0\ncpu0 10 0 0 90 0 0 0 0 0 0\n"
          "N eth3 100\nN eth4 100\n--\n"
          "cpu 20 0 0 180 0 0 0 0 0 0\ncpu0 20 0 0 180 0 0 0 0 0 0\n"
          "N eth3 110\nN eth4 110\n--")
elif command in ("wg genkey", "wg pubkey"):
    print("fake-key")
"""


def run_rig(tmp_path, script, extra, operation="run"):
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
        "FAKE_SSH_HOST_LOG": str(tmp_path / "hosts.log"),
        "FAKE_QMAN_READ_COUNT": str(tmp_path / "qman-count"),
        "FAKE_BIND_STATE": str(tmp_path / "wgfp-bound"),
        "FAKE_WGFP_READ_COUNT": str(tmp_path / "wgfp-reads"),
        "J": str(tmp_path / "json"),
        "OUT": str(tmp_path / "result.csv"),
        "DIRS": "up down",
        "DUR": "3",
        "STEADY_FROM": "0",
        "REPS": "1",
        "WGFP": "0",
    }
    env.update(extra)
    args = ["bash", str(script), operation]
    if script == SINGLE and operation == "run":
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


@pytest.mark.parametrize("failure", ["add", "del"])
def test_preflight_probe_failure_does_not_remove_an_existing_interface(tmp_path, failure):
    ssh = tmp_path / "ssh"
    ssh.write_text(FAKE_SSH)
    ssh.chmod(0o755)
    commands = tmp_path / "ip.log"
    for name, content in (
        ("sudo", '#!/bin/sh\nexec "$@"\n'),
        ("modprobe", "#!/bin/sh\nexit 0\n"),
        ("wg", "#!/bin/sh\nexit 0\n"),
        ("ip", """#!/bin/sh
echo "$*" >> "$FAKE_IP_LOG"
if [ "$2" = "$FAKE_PROBE_FAILURE" ]; then echo "probe $2 failed" >&2; exit 1; fi
exit 0
"""),
    ):
        fake = tmp_path / name
        fake.write_text(content)
        fake.chmod(0o755)
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
        "FAKE_SSH_LOG": str(tmp_path / "ssh.log"),
        "FAKE_IP_LOG": str(commands),
        "FAKE_PROBE_EXEC": "1",
        "FAKE_PROBE_FAILURE": failure,
        "J": str(tmp_path / "json"),
        "WG_STATE_PATH": str(tmp_path / "state"),
    }
    result = subprocess.run(
        ["bash", str(SINGLE), "check"], env=env, text=True,
        capture_output=True, timeout=30
    )
    assert result.returncode != 0
    assert f"probe {failure} failed" in result.stderr
    calls = commands.read_text().splitlines()
    assert calls[0].startswith("link add wgc")
    if failure == "add":
        assert len(calls) == 1
    else:
        assert len(calls) == 2 and calls[1].startswith("link del wgc")


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


def test_multi_manual_setup_binds_all_fast_path_peers(tmp_path):
    result, commands = run_rig(
        tmp_path, MULTI, {"WGFP": "1", "TUNNELS": "2", "ACTIVE": "2"}, operation="setup"
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert any("echo 'wgm0 wgm1 ' | sudo tee /sys/module/wireguard/parameters/wgfp_peer" in c
               for c in commands)
    assert not any("echo off | sudo tee /sys/module/wireguard/parameters/wgfp_peer" in c
                   for c in commands)


def test_multi_manual_setup_reports_fast_path_bind_failure(tmp_path):
    result, _ = run_rig(
        tmp_path, MULTI, {"WGFP": "1", "TUNNELS": "2", "ACTIVE": "2",
                          "FAKE_BIND_ERROR": "1"}, operation="setup"
    )
    assert result.returncode != 0
    assert "wgfp bind failed" in result.stderr


def test_multi_manual_teardown_uses_persisted_server_and_route_settings(tmp_path):
    setup, before = run_rig(tmp_path, MULTI, {
        "TUNNELS": "2", "WG_SKIP_SERVERS": "0", "WGMTU": "1300",
    }, operation="setup")
    assert setup.returncode == 0, setup.stdout + setup.stderr
    result, commands = run_rig(tmp_path, MULTI, {
        "TUNNELS": "4", "WG_SKIP_SERVERS": "1", "WGMTU": "1420",
    }, operation="teardown")
    assert result.returncode == 0, result.stdout + result.stderr
    cleanup = commands[len(before):]
    assert sum("python3 - identity /tmp/wgrig-srv-" in c for c in cleanup) == 2
    assert sum("sh -s -- delete plain " in c and " 1300 " in c for c in cleanup) == 2
    assert not (tmp_path / "state.multi-lease").exists()


def test_multi_manual_teardown_unbinds_its_peers_before_removing_links(tmp_path):
    setup, before = run_rig(tmp_path, MULTI, {
        "WGFP": "1", "TUNNELS": "2",
    }, operation="setup")
    assert setup.returncode == 0, setup.stdout + setup.stderr
    result, commands = run_rig(tmp_path, MULTI, {
        "WGFP": "0", "TUNNELS": "4",
    }, operation="teardown")
    assert result.returncode == 0, result.stdout + result.stderr
    cleanup = commands[len(before):]
    unbind = next(i for i, c in enumerate(cleanup) if "echo off | sudo tee" in c)
    link_delete = next(i for i, c in enumerate(cleanup) if "sh -s -- delete wgm" in c)
    assert unbind < link_delete


def test_multi_teardown_refuses_an_external_binding_that_uses_its_link(tmp_path):
    setup, before = run_rig(tmp_path, MULTI, {"TUNNELS": "2"}, operation="setup")
    assert setup.returncode == 0, setup.stdout + setup.stderr
    (tmp_path / "wgfp-bound").write_text("wgm0 existing")
    result, commands = run_rig(tmp_path, MULTI, {"TUNNELS": "2"}, operation="teardown")
    assert result.returncode != 0
    assert "WGFP binding is not owned" in result.stderr
    cleanup = commands[len(before):]
    assert not any("sh -s -- delete wgm" in c or "echo off | sudo tee" in c for c in cleanup)
    assert (tmp_path / "state.multi-lease").exists()


def test_multi_partial_route_collision_preserves_foreign_route(tmp_path):
    result, commands = run_rig(tmp_path, MULTI, {
        "TUNNELS": "2", "FAKE_ROUTE_FAIL": "10",
    }, operation="setup")
    assert result.returncode != 0
    assert any("sh -s -- delete far " in c for c in commands)
    assert not any("sh -s -- delete plain " in c for c in commands)
    assert not (tmp_path / "state.multi-lease").exists()


def test_multi_uncertain_route_creation_retains_lease_and_links(tmp_path):
    result, commands = run_rig(tmp_path, MULTI, {
        "TUNNELS": "2", "FAKE_ROUTE_FAIL": "255",
    }, operation="setup")
    assert result.returncode != 0
    assert "uncertain ownership" in result.stderr
    assert not any("sh -s -- delete wgm" in c for c in commands)
    assert (tmp_path / "state.multi-lease" / "route-plain-0").read_text() == "pending\n"


def test_multi_foreign_server_directory_is_not_adopted(tmp_path):
    result, commands = run_rig(tmp_path, MULTI, {
        "TUNNELS": "2", "WG_SKIP_SERVERS": "0", "FAKE_SERVER_START_FAIL": "10",
    }, operation="setup")
    assert result.returncode != 0
    assert not any("python3 - identity /tmp/wgrig-srv-" in c for c in commands)
    assert not (tmp_path / "state.multi-lease").exists()


def test_multi_keys_are_piped_without_shared_key_files(tmp_path):
    result, commands = run_rig(tmp_path, MULTI, {"TUNNELS": "2"}, operation="setup")
    assert result.returncode == 0, result.stdout + result.stderr
    assert sum("private-key /dev/stdin" in c for c in commands) == 4
    assert not any("/tmp/wgmk" in c for c in commands)


@pytest.mark.parametrize("script", [SINGLE, MULTI])
@pytest.mark.parametrize("operation", ["setup", "run"])
def test_rig_refuses_to_replace_existing_fast_path_before_cleanup(tmp_path, script, operation):
    result, commands = run_rig(
        tmp_path, script, {"WGFP": "1", "TUNNELS": "2",
                           "FAKE_WGFP_STATE": "on sealed=2 rx=3 dev=existing"},
        operation=operation,
    )
    assert result.returncode != 0
    assert "WGFP is already bound" in result.stderr
    assert not any("ip link del wg0" in command or "ip link del wgm" in command
                   or "ip route flush" in command
                   or "nft delete" in command or " kill " in command for command in commands)


def test_multi_rechecks_fast_path_before_binding(tmp_path):
    result, commands = run_rig(
        tmp_path, MULTI, {"WGFP": "1", "TUNNELS": "2", "FAKE_WGFP_CONFLICT_AT": "2"},
        operation="setup",
    )
    assert result.returncode != 0
    assert "WGFP is already bound" in result.stderr
    assert not any("tee /sys/module/wireguard/parameters/wgfp_peer" in c for c in commands)


def test_multi_run_uses_configured_vendor_and_generators(tmp_path):
    result, _ = run_rig(tmp_path, MULTI, {
        "DUT": "", "DUT_KIND": "vendor", "VENDOR_DUT": "root@192.168.1.70",
        "GEN1": "admin@192.0.2.11", "GEN2": "admin@192.0.2.12",
        "WG_SKIP_FW4": "1", "TUNNELS": "2", "ACTIVE": "2",
    })
    assert result.returncode == 0, result.stdout + result.stderr
    hosts = set((tmp_path / "hosts.log").read_text().splitlines())
    assert hosts == {"root@192.168.1.70", "admin@192.0.2.11", "admin@192.0.2.12"}


def test_multi_run_requires_configured_generators(tmp_path):
    result, commands = run_rig(tmp_path, MULTI, {"GEN1": ""})
    assert result.returncode != 0
    assert "GEN1" in result.stderr
    assert not commands


@pytest.mark.parametrize(
    ("script", "column", "expected"),
    [(SINGLE, "retr_or_loss", 9), (MULTI, "retrans", 18)],
)
def test_bidirectional_retransmits_include_both_senders(tmp_path, script, column, expected):
    result, _ = run_rig(tmp_path, script, {
        "DIRS": "bidir", "TUNNELS": "2", "ACTIVE": "2",
        "FAKE_FORWARD_RETRANS": "2", "FAKE_REVERSE_RETRANS": "7",
    })
    assert result.returncode == 0, result.stdout + result.stderr
    with (tmp_path / "result.csv").open(newline="") as output:
        rows = list(csv.DictReader(output))
    assert len(rows) == 1
    assert int(rows[0][column]) == expected


@pytest.mark.parametrize("script", [SINGLE, MULTI])
def test_bidirectional_missing_reverse_retransmits_fails(tmp_path, script):
    result, _ = run_rig(tmp_path, script, {
        "DIRS": "bidir", "TUNNELS": "2", "ACTIVE": "2",
        "FAKE_MISSING_REVERSE": "1",
    })
    assert result.returncode != 0
    assert "missing or invalid sum_sent_bidir_reverse retransmits" in result.stderr


@pytest.mark.parametrize("script", [SINGLE, MULTI])
def test_bidirectional_missing_reverse_interval_fails(tmp_path, script):
    result, _ = run_rig(tmp_path, script, {
        "DIRS": "bidir", "TUNNELS": "2", "ACTIVE": "2",
        "FAKE_MISSING_REVERSE_INTERVAL": "1",
    })
    assert result.returncode != 0
    assert "reverse" in result.stderr


@pytest.mark.parametrize("script", [SINGLE, MULTI])
def test_client_timeout_is_applied_remotely(tmp_path, script):
    result, commands = run_rig(
        tmp_path, script, {"WG_CLIENT_TIMEOUT": "9", "TUNNELS": "2"}
    )
    assert result.returncode == 0, result.stdout + result.stderr
    clients = [command for command in commands if "iperf3 -c " in command]
    assert clients
    assert all("timeout --signal=TERM --kill-after=5s 9s iperf3 -c " in command
               for command in clients)


@pytest.mark.parametrize("script", [SINGLE, MULTI])
def test_invalid_client_timeout_fails_before_remote_operations(tmp_path, script):
    result, commands = run_rig(tmp_path, script, {"WG_CLIENT_TIMEOUT": "3"})
    assert result.returncode == 2
    assert "WG_CLIENT_TIMEOUT must be an integer greater than DUR" in result.stderr
    assert not commands


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
    ("script", "extra"),
    [(SINGLE, {"FAKE_IPERF_FAIL_PORT": "5231"}),
     (MULTI, {"TUNNELS": "2", "ACTIVE": "2", "FAKE_IPERF_FAIL_PORT": "5241"})],
)
def test_owned_server_client_failure_is_not_reported_as_success(tmp_path, script, extra):
    result, _ = run_rig(tmp_path, script, extra | {"WG_SKIP_SERVERS": "0"})
    assert result.returncode != 0
    assert "iperf" in result.stderr


def test_owned_server_zero_rate_is_not_reported_as_success(tmp_path):
    result, _ = run_rig(tmp_path, MULTI, {
        "WG_SKIP_SERVERS": "0", "TUNNELS": "2", "ACTIVE": "2",
        "FAKE_IPERF_RATE_PORT": "5241", "FAKE_IPERF_RATE_BPS": "0",
    })
    assert result.returncode != 0
    assert "no traffic" in result.stderr


@pytest.mark.parametrize("script", [SINGLE, MULTI])
def test_owned_server_dut_statistics_failure_is_not_reported_as_success(tmp_path, script):
    result, _ = run_rig(tmp_path, script, {
        "WG_SKIP_SERVERS": "0", "FAKE_STAT_ERROR": "1", "TUNNELS": "2",
    })
    assert result.returncode != 0
    assert "statistics sampling failed" in result.stderr


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


@pytest.mark.parametrize("script", [SINGLE, MULTI])
def test_optional_fast_path_counters_bracket_each_cell(tmp_path, script):
    log = tmp_path / "counters.tsv"
    result, _ = run_rig(
        tmp_path, script, {"WGFP": "1", "WGFP_COUNTER_LOG": str(log),
                           "TUNNELS": "2", "ACTIVE": "2"}
    )
    assert result.returncode == 0, result.stdout + result.stderr
    rows = [line.split("\t") for line in log.read_text().splitlines()]
    assert [(row[1], row[3]) for row in rows] == [
        ("up", "before"), ("up", "after"),
        ("down", "before"), ("down", "after"),
    ]
    assert all("sealed=123" in row[5] and "rx=456" in row[5] and
               "xmit_ok=123" in row[6] and "xmit_ern=0" in row[6] for row in rows)
    assert all("sink0 " in row[7] and "fqid=0x2be" in row[7] for row in rows)


def test_fast_path_counter_records_each_sink(tmp_path):
    log = tmp_path / "counters.tsv"
    stats = (
        "sinks=2 xmit_ok=123 xmit_fail=0 xmit_ern=0\n"
        "sink0 cpu=0 fqid=0x2be frames=10 errs=0 sg=0\n"
        "sink1 cpu=1 fqid=0x2bf frames=20 errs=0 sg=0"
    )
    result, _ = run_rig(
        tmp_path, SINGLE, {"WGFP": "1", "WGFP_COUNTER_LOG": str(log),
                           "FAKE_QMAN_STATE": stats}
    )
    assert result.returncode == 0, result.stdout + result.stderr
    for row in (line.split("\t") for line in log.read_text().splitlines()):
        assert [sink.split()[0] for sink in row[7].split("|")] == ["sink0", "sink1"]


@pytest.mark.parametrize("script", [SINGLE, MULTI])
def test_fast_path_counter_read_failure_aborts_measurement(tmp_path, script):
    result, commands = run_rig(
        tmp_path, script, {"WGFP": "1", "WGFP_COUNTER_LOG": str(tmp_path / "counters.tsv"),
                           "FAKE_WGFP_ERROR": "1", "TUNNELS": "2", "ACTIVE": "2"}
    )
    assert result.returncode != 0
    assert "cannot read WGFP counters" in result.stderr
    assert not any("iperf3 -c " in command for command in commands)


@pytest.mark.parametrize("script", [SINGLE, MULTI])
def test_qman_counter_read_failure_aborts_measurement(tmp_path, script):
    result, commands = run_rig(
        tmp_path, script, {"WGFP": "1", "WGFP_COUNTER_LOG": str(tmp_path / "counters.tsv"),
                           "FAKE_QMAN_ERROR": "1", "TUNNELS": "2", "ACTIVE": "2"}
    )
    assert result.returncode != 0
    assert "cannot read WGFP QMan counters" in result.stderr
    assert not any("iperf3 -c " in command for command in commands)


@pytest.mark.parametrize("script", [SINGLE, MULTI])
@pytest.mark.parametrize("stats", [
    "xmit_ok=10 xmit_fail=0",
    "xmit_ok=10 xmit_fail=0 xmit_ern=0 xmit_ern=1",
    "xmit_ok=10 xmit_fail=bad xmit_ern=0",
])
def test_invalid_qman_counters_abort_measurement(tmp_path, script, stats):
    result, commands = run_rig(
        tmp_path, script, {"WGFP": "1", "WGFP_COUNTER_LOG": str(tmp_path / "counters.tsv"),
                           "FAKE_QMAN_STATE": stats, "TUNNELS": "2", "ACTIVE": "2"}
    )
    assert result.returncode != 0
    assert "missing, duplicate or invalid WGFP QMan counters" in result.stderr
    assert not any("iperf3 -c " in command for command in commands)


@pytest.mark.parametrize("script", [SINGLE, MULTI])
@pytest.mark.parametrize("stats", [
    "sinks=1 xmit_ok=1 xmit_fail=0 xmit_ern=0",
    "sinks=1 xmit_ok=1 xmit_fail=0 xmit_ern=0\n"
    "sink0 fqid=0x2be frames=1 errs=0 sg=bad",
    "sinks=1 xmit_ok=1 xmit_fail=0 xmit_ern=0\n"
    "sink0 fqid=0x2be frames=1 errs=0 sg=0\n"
    "sink0 fqid=0x2bf frames=1 errs=0 sg=0",
])
def test_invalid_sink_counters_abort_measurement(tmp_path, script, stats):
    result, commands = run_rig(
        tmp_path, script, {"WGFP": "1", "WGFP_COUNTER_LOG": str(tmp_path / "counters.tsv"),
                           "FAKE_QMAN_STATE": stats, "TUNNELS": "2", "ACTIVE": "2"}
    )
    assert result.returncode != 0
    assert "missing, duplicate or invalid WGFP sink counters" in result.stderr
    assert not any("iperf3 -c " in command for command in commands)


@pytest.mark.parametrize("script", [SINGLE, MULTI])
@pytest.mark.parametrize("counter", ["xmit_fail", "xmit_ern"])
def test_changed_qman_failure_counter_aborts_standalone_run(tmp_path, script, counter):
    log = tmp_path / "counters.tsv"
    result, commands = run_rig(
        tmp_path, script, {"WGFP": "1", "WGFP_COUNTER_LOG": str(log),
                           "FAKE_QMAN_CHANGE": counter, "TUNNELS": "2", "ACTIVE": "2"}
    )
    assert result.returncode != 0
    assert "QMan failures or rejections changed" in result.stderr
    assert any("iperf3 -c " in command for command in commands)
    assert [row.split("\t")[3] for row in log.read_text().splitlines()] == ["before", "after"]


@pytest.mark.parametrize("script", [SINGLE, MULTI])
def test_unbound_fast_path_aborts_counter_measurement(tmp_path, script):
    result, commands = run_rig(
        tmp_path, script, {"WGFP": "1", "WGFP_COUNTER_LOG": str(tmp_path / "counters.tsv"),
                           "FAKE_WGFP_STATE": "off", "TUNNELS": "2", "ACTIVE": "2"}
    )
    assert result.returncode != 0
    assert "WGFP is not bound" in result.stderr
    assert not any("iperf3 -c " in command for command in commands)


@pytest.mark.parametrize("script", [SINGLE, MULTI])
def test_fast_path_counter_log_requires_enabled_fast_path(tmp_path, script):
    result, commands = run_rig(
        tmp_path, script, {"WGFP_COUNTER_LOG": str(tmp_path / "counters.tsv"),
                           "TUNNELS": "2", "ACTIVE": "2"}
    )
    assert result.returncode == 2
    assert "requires WGFP=1" in result.stderr
    assert not commands


MATRIX_FAKE_SSH = r"""#!/usr/bin/env python3
import json
import os
from pathlib import Path
import re
import sys

args = sys.argv[1:]
index = next(i for i, arg in enumerate(args) if "@" in arg)
remote = args[index + 1:]
command = " ".join(remote)
state = Path(os.environ["FAKE_NFT_STATE"])
with open(os.environ["FAKE_SSH_LOG"], "a") as log:
    log.write(json.dumps(command) + "\n")
if remote[:2] == ["sh", "-s"]:
    sys.stdin.read()
    if os.environ.get("FAKE_MATRIX_FAIL") == "preflight":
        print("existing resource", file=sys.stderr)
        sys.exit(1)
if "nft 'create table" in command:
    if os.environ.get("FAKE_MATRIX_FAIL") == "table":
        sys.exit(1)
    match = re.search(r'create table inet (\w+) \{ comment "(\w+)"; \}', command)
    if not match:
        sys.exit(1)
    state.write_text(json.dumps({"name": match.group(1), "token": match.group(2)}))
if "nft -a list table" in command:
    if os.environ.get("FAKE_MATRIX_FAIL") == "list":
        sys.exit(1)
    table = json.loads(state.read_text())
    token = "someone-else" if os.environ.get("FAKE_MATRIX_FAIL") == "owner" else table["token"]
    handle = "" if os.environ.get("FAKE_MATRIX_FAIL") == "handle" else " # handle 42"
    print(f'table inet {table["name"]} {{{handle}\n\tcomment "{token}"\n}}')
if os.environ.get("FAKE_MATRIX_FAIL") == "chain" and "nft 'add chain" in command:
    sys.exit(1)
"""

MATRIX_FAKE_RIG = r"""#!/usr/bin/env bash
echo "$(basename "$0") $DUT $DUT_KIND" >> "$FAKE_RIG_LOG"
if [ "${FAKE_MATRIX_FAIL:-}" = single ] && [[ $0 = *testrig-wireguard.sh ]]; then
    echo "single failed" >&2
    exit 1
fi
cell=${TUNNELS:-wg-v4}
peers=${TUNNELS:-1}
printf 'dir,rep,gbps\n' > "$OUT"
for dir in up down bidir; do
    printf '%s,1,1.5\n' "$dir" >> "$OUT"
    python3 - "$J" "$cell" "$dir" "$DUR" "${FAKE_MATRIX_FAIL:-}" <<'PY'
import json, sys
from pathlib import Path

directory, cell, direction, duration, failure = sys.argv[1:]
duration = int(duration)
tunnels = int(cell) if cell.isdigit() else 1
rate = (1.8e9 if failure == "mismatch" else 1.5e9) / tunnels
forward = rate / 2 if direction == "bidir" else rate
for peer in range(tunnels):
    intervals = []
    for second in range(duration - (2 if failure == "short" else 0)):
        sample = {"start": second, "end": second + 1, "seconds": 1,
                  "bits_per_second": forward, "omitted": False}
        interval = {"sum": sample}
        if direction == "bidir":
            interval["sum_bidir_reverse"] = dict(sample)
        intervals.append(interval)
    stats = {"seconds": duration, "bits_per_second": forward, "retransmits": 0}
    end = {"sum_sent": stats, "sum_received": stats}
    if direction == "bidir":
        end.update(sum_sent_bidir_reverse=stats, sum_received_bidir_reverse=stats)
    name = (f"wg-v4-{direction}-r1.json" if tunnels == 1 else
            f"wgm-{direction}-n{tunnels}-k{peer}-r1.json")
    (Path(directory) / name).write_text(json.dumps({"intervals": intervals, "end": end}))
PY
    if [ "${WGFP:-0}" = 1 ]; then
        printf '%s\t%s\t1\tbefore\t1\ton sealed=0 rx=0 peers=%s install_fail=0\t%s\t%s\n' \
            "$cell" "$dir" "$peers" "sinks=1 xmit_ok=0 xmit_fail=0 xmit_ern=0" \
            "sink0 fqid=0x2be frames=10 errs=0 sg=0" >> "$WGFP_COUNTER_LOG"
        rx=10; [ "${FAKE_MATRIX_FAIL:-}" != no_rx ] || rx=0
        qman="sinks=1 xmit_ok=10 xmit_fail=0 xmit_ern=0"
        sink="sink0 fqid=0x2be frames=20 errs=0 sg=0"
        case "${FAKE_MATRIX_FAIL:-}" in
            qman_fail) qman="sinks=1 xmit_ok=10 xmit_fail=1 xmit_ern=0" ;;
            qman_ern) qman="sinks=1 xmit_ok=10 xmit_fail=0 xmit_ern=1" ;;
            qman_stall) qman="sinks=1 xmit_ok=0 xmit_fail=0 xmit_ern=0" ;;
            qman_missing) qman="sinks=1 xmit_ok=10 xmit_fail=0" ;;
            qman_duplicate) qman="sinks=1 xmit_ok=10 xmit_fail=0 xmit_ern=0 xmit_ern=1" ;;
            sink_stall) sink="sink0 fqid=0x2be frames=10 errs=0 sg=0" ;;
            sink_error) sink="sink0 fqid=0x2be frames=20 errs=1 sg=0" ;;
            sink_sg) sink="sink0 fqid=0x2be frames=20 errs=0 sg=1" ;;
            sink_identity) sink="sink0 fqid=0x2bf frames=20 errs=0 sg=0" ;;
        esac
        printf '%s\t%s\t1\tafter\t1\ton sealed=10 rx=%s peers=%s install_fail=0\t%s\t%s\n' \
            "$cell" "$dir" "$rx" "$peers" "$qman" "$sink" >> "$WGFP_COUNTER_LOG"
    fi
done
echo "measurement complete"
"""


def run_matrix(tmp_path, target, extra=None):
    repo = tmp_path / "repo"
    bin_dir = repo / "bin"
    bin_dir.mkdir(parents=True)
    shutil.copy(MATRIX, bin_dir / "wg-matrix.sh")
    shutil.copy(ROOT / "bin/testrig-env.sh", bin_dir / "testrig-env.sh")
    for name in ("testrig-wireguard.sh", "testrig-wireguard-multi.sh"):
        script = bin_dir / name
        script.write_text(MATRIX_FAKE_RIG)
        script.chmod(0o755)
    ssh = tmp_path / "ssh"
    ssh.write_text(MATRIX_FAKE_SSH)
    ssh.chmod(0o755)
    log = tmp_path / "ssh.log"
    rig_log = tmp_path / "rig.log"
    env = os.environ | {
        "PATH": f"{tmp_path}:{os.environ['PATH']}",
        "HOME": str(tmp_path),
        "TESTRIG_ENV": str(tmp_path / "no-testrig-env"),
        "GEN1": "admin@192.168.1.112",
        "GEN2": "admin@192.168.1.113",
        "DUT": "",
        "RIG_NS": "",
        "WG_PORT": "eth4",
        "OUTER": "4",
        "WG_NOTRACK": "0",
        "WGFP": "0",
        "WGFP_COUNTER_LOG": "",
        "WG_STATE_PATH": "",
        "WG_DUT_STAT": "",
        "WG_SKIP_FW4": "0",
        "WG_SKIP_SERVERS": "0",
        "KEEP": "0",
        "J": "",
        "TAG": "wgtest",
        "REPS1": "1",
        "REPSN": "1",
        "DUR": "3",
        "STEADY_FROM": "0",
        "OUTDIR": str(tmp_path / "out"),
        "FAKE_SSH_LOG": str(log),
        "FAKE_RIG_LOG": str(rig_log),
        "FAKE_NFT_STATE": str(tmp_path / "nft.json"),
    }
    env.update(extra or {})
    result = subprocess.run(
        ["bash", str(bin_dir / "wg-matrix.sh"), target],
        env=env, text=True, capture_output=True, timeout=30
    )
    commands = [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []
    rigs = rig_log.read_text().splitlines() if rig_log.exists() else []
    return result, commands, rigs


def test_matrix_uses_owned_scoped_nft_table_without_changing_vyos_config(tmp_path):
    result, commands, rigs = run_matrix(tmp_path, "185", {"WGFP": "1"})
    assert result.returncode == 0, result.stdout + result.stderr
    add = next(command for command in commands if "nft 'create table inet " in command)
    table, token = re.search(r'create table inet (wgmatrix\d+) \{ comment "(\w+)"; \}', add).groups()
    assert token.startswith(table)
    setup = next(command for command in commands if "nft 'add chain" in command)
    assert f"inet {table} pre" in setup and f"inet {table} out" in setup
    assert "ip saddr 10.99.2.113 ip daddr 10.99.2.185" in setup
    assert "udp sport 51820-51833 udp dport 51820-51833 notrack" in setup
    assert any(f"nft -a list table inet {table}" in command for command in commands)
    assert sum("nft delete table inet handle 42" in command for command in commands) == 1
    assert not any("conntrack ignore" in command or "sysctl" in command for command in commands)
    assert len(rigs) == 3 and all("vyos@192.168.1.185 ask2" in rig for rig in rigs)


@pytest.mark.parametrize("failure", ["chain", "single"])
def test_matrix_removes_owned_table_after_failure(tmp_path, failure):
    result, commands, rigs = run_matrix(tmp_path, "185", {"FAKE_MATRIX_FAIL": failure})
    assert result.returncode != 0
    assert any("nft 'create table inet " in command for command in commands)
    assert sum("nft delete table inet handle 42" in command for command in commands) == 1
    assert len(rigs) == (1 if failure == "single" else 0)


@pytest.mark.parametrize("failure", ["preflight", "table"])
def test_matrix_does_not_remove_resources_it_did_not_create(tmp_path, failure):
    result, commands, rigs = run_matrix(tmp_path, "185", {"FAKE_MATRIX_FAIL": failure})
    assert result.returncode != 0
    assert not any("nft delete table" in command for command in commands)
    assert not rigs


def test_matrix_targets_current_vendor_board_without_nft_changes(tmp_path):
    result, commands, rigs = run_matrix(tmp_path, "70")
    assert result.returncode == 0, result.stdout + result.stderr
    assert len(rigs) == 3 and all("root@192.168.1.70 vendor" in rig for rig in rigs)
    assert not any("nft 'create table" in command or "nft delete table" in command
                   for command in commands)


@pytest.mark.parametrize("failure", ["owner", "handle", "list"])
def test_matrix_refuses_to_delete_an_unverified_nft_table(tmp_path, failure):
    result, commands, _ = run_matrix(tmp_path, "185", {"FAKE_MATRIX_FAIL": failure})
    assert result.returncode != 0
    assert "nft table" in result.stderr
    assert not any("nft delete table" in command for command in commands)


def test_matrix_rejects_missing_fast_path_receive_and_removes_its_table(tmp_path):
    result, commands, rigs = run_matrix(
        tmp_path, "185", {"WGFP": "1", "FAKE_MATRIX_FAIL": "no_rx"}
    )
    assert result.returncode != 0
    assert "fast-path counters did not advance" in result.stderr
    assert len(rigs) == 3
    assert any("nft delete table" in command for command in commands)


@pytest.mark.parametrize(
    ("failure", "message"),
    [
        ("qman_fail", "QMan failures or rejections changed"),
        ("qman_ern", "QMan failures or rejections changed"),
        ("qman_stall", "QMan submissions did not advance"),
        ("qman_missing", "missing WGFP counters"),
        ("qman_duplicate", "invalid or duplicate WGFP counter"),
        ("sink_stall", "sink frame counters did not advance"),
        ("sink_error", "sink errors or SG drops changed"),
        ("sink_sg", "sink errors or SG drops changed"),
        ("sink_identity", "sink FQ identities changed"),
    ],
)
def test_matrix_rejects_invalid_qman_transmit_counters(tmp_path, failure, message):
    result, commands, rigs = run_matrix(
        tmp_path, "185", {"WGFP": "1", "FAKE_MATRIX_FAIL": failure}
    )
    assert result.returncode != 0
    assert message in result.stderr
    assert len(rigs) == 3
    assert any("nft delete table" in command for command in commands)


def test_matrix_rejects_incomplete_iperf_intervals(tmp_path):
    result, commands, rigs = run_matrix(tmp_path, "185", {"FAKE_MATRIX_FAIL": "short"})
    assert result.returncode != 0
    assert "incomplete steady window" in result.stderr
    assert len(rigs) == 3
    assert any("nft delete table" in command for command in commands)


def test_matrix_rejects_csv_that_disagrees_with_iperf(tmp_path):
    result, commands, rigs = run_matrix(tmp_path, "185", {"FAKE_MATRIX_FAIL": "mismatch"})
    assert result.returncode != 0
    assert "differs from its iperf JSON" in result.stderr
    assert len(rigs) == 3
    assert any("nft delete table" in command for command in commands)


PREFLIGHT_FAKE_IP = r"""#!/usr/bin/env python3
import os
import sys

args = sys.argv[1:]
conflict = os.environ.get("FAKE_CONFLICT", "")
if args[:2] == ["link", "show"]:
    sys.exit(0 if conflict == "link" and args[2] == "wgm3" else 1)
if "rule" in args:
    if conflict == "rule":
        print("12100: from all lookup 200")
    sys.exit(0)
if "route" in args and "table" in args:
    if conflict == "table" and args[-1] == "224":
        print("10.99.1.0/24 dev existing")
        sys.exit(0)
    print("Error: FIB table does not exist.", file=sys.stderr)
    sys.exit(2)
if "route" in args and "exact" in args and conflict == "route" and "10.99.215.0/24" in args:
    print("10.99.215.0/24 dev existing")
sys.exit(0)
"""

PREFLIGHT_FAKE_NFT = r"""#!/usr/bin/env python3
import os
import sys

if os.environ.get("FAKE_CONFLICT") == "nft" and sys.argv[1:] == ["list", "tables"]:
    print("table inet wgnt")
if os.environ.get("FAKE_CONFLICT") == "fw4" and "forward" in sys.argv:
    print('counter accept comment "wgrig" # handle 42')
"""


def run_preflight(tmp_path, which, conflict=""):
    scripts = re.findall(r"<<'REMOTE'\n(.*?)\nREMOTE", MATRIX.read_text(), re.DOTALL)
    script = scripts[which].replace("/tmp/wgk", str(tmp_path / "wgk"))
    script = script.replace("/tmp/wgmk", str(tmp_path / "wgmk"))
    udp = tmp_path / "udp"
    udp6 = tmp_path / "udp6"
    udp.write_text("sl local_address rem_address\n" +
                   ("0: 00000000:CA6C 00000000:0000\n" if conflict == "udp" else ""))
    udp6.write_text("sl local_address rem_address\n")
    script = script.replace("/proc/net/udp6", str(udp6)).replace("/proc/net/udp", str(udp))
    for name, content in (
        ("ip", PREFLIGHT_FAKE_IP),
        ("nft", PREFLIGHT_FAKE_NFT),
        ("ss", "#!/bin/sh\nexit 0\n"),
        ("sudo", '#!/bin/sh\n[ "$1" = -n ] && shift\nexec "$@"\n'),
    ):
        fake = tmp_path / name
        fake.write_text(content)
        fake.chmod(0o755)
    env = os.environ | {"PATH": f"{tmp_path}:{os.environ['PATH']}", "FAKE_CONFLICT": conflict}
    args = ["ask2", "0", "0"] if which == 0 else ["-", "1"]
    if conflict == "fw4":
        args = ["vendor", "0", "0"]
    return subprocess.run(
        ["sh", "-s", "--", *args], input=script, env=env,
        text=True, capture_output=True, timeout=30
    )


@pytest.mark.parametrize(
    ("conflict", "message"),
    [("link", "wgm3 already exists"), ("rule", "rule 12100 already exists"),
     ("table", "route table 224 is in use"), ("route", "10.99.215.0/24 already exists")],
)
def test_generator_preflight_refuses_existing_resources(tmp_path, conflict, message):
    result = run_preflight(tmp_path, 1, conflict)
    assert result.returncode != 0
    assert message in result.stderr


@pytest.mark.parametrize(
    ("conflict", "message"),
    [("udp", "UDP port 51820 already in use"), ("nft", "table inet wgnt already exists"),
     ("fw4", "forward rules already use a WireGuard rig comment")],
)
def test_dut_preflight_refuses_existing_resources(tmp_path, conflict, message):
    result = run_preflight(tmp_path, 0, conflict)
    assert result.returncode != 0
    assert message in result.stderr


@pytest.mark.parametrize("which", [0, 1])
def test_matrix_preflights_accept_empty_resources(tmp_path, which):
    result = run_preflight(tmp_path, which)
    assert result.returncode == 0, result.stdout + result.stderr
