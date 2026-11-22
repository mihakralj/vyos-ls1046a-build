"""Host checks for matched, fail-closed DUT comparisons."""

import copy
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest

pytestmark = pytest.mark.host
ROOT = Path(__file__).resolve().parents[1]


def profile(**values):
    env = {key: value for key, value in os.environ.items()
           if key not in {"DUR", "STEADY_FROM", "MEASURE_TO", "STREAMS", "REPS",
                          "WG_REPS", "BIDIR_DUR", "BIDIR_STEADY_FROM", "SAMPLE",
                          "DUT_TRACE", "IPERF_ARGS", "PARITY_CC", "RUN_ON"}}
    return subprocess.run(
        ["bash", "-c",
         '. "$1" || exit $?; printf "%s\\n" "$DUR $STEADY_FROM $MEASURE_TO '
         '$STREAMS $REPS $BIDIR_DUR $BIDIR_STEADY_FROM $WG_REPS $SAMPLE $DUT_TRACE" '
         '"$IPERF_ARGS"', "test", str(ROOT / "bin/testrig-parity-profile.sh")],
        env=env | values, text=True, capture_output=True,
    )


def test_common_profile_has_identical_durations_and_streams():
    result = profile()
    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == ["32 10 30 8 3 32 10 3 0 0", "-C bbr"]


@pytest.mark.parametrize("values", [
    {"BIDIR_DUR": "20"}, {"WG_REPS": "5"}, {"SAMPLE": "1"}, {"DUT_TRACE": "1"},
    {"IPERF_ARGS": "-M 1200"}, {"MEASURE_TO": "32"}, {"MEASURE_TO": "10"},
    {"STREAMS": "0"}, {"DUR": "030"}, {"REPS": "1;false"}, {"RUN_ON": "dell2"},
])
def test_common_profile_rejects_asymmetry_or_invalid_settings(values):
    result = profile(**values)
    assert result.returncode != 0
    assert "ERROR" in result.stderr


def test_cubic_is_an_explicit_common_control_not_a_default_workaround():
    result = profile(PARITY_CC="cubic", DUR="22", MEASURE_TO="20")
    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == ["22 10 20 8 3 22 10 3 0 0", "-C cubic"]


def parity_module():
    spec = importlib.util.spec_from_file_location("testrig_parity", ROOT / "bin/testrig_parity.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def snapshot():
    port = {"mtu": 1500, "carrier": 1, "addresses": ["192.0.2.1/24"],
            "rings": "RX: 4096\nTX: 4096", "features": "generic-receive-offload: on",
            "coalescing": "Adaptive RX: on TX: on", "channels": "Combined: 8",
            "rss": "0: 0 1 2 3 4 5 6 7", "rps": ["00"] * 8,
            "rss_fields": {"tcp4": ["IP SA", "IP DA"], "tcp6": ["IP SA", "IP DA"]},
            "qdiscs": [{"kind": "fq", "options": {"limit": 10000}}] * 8,
            "link": "Speed: 10000Mb/s\nDuplex: Full\nLink detected: yes"}
    return {"kernel": "test", "iperf": "iperf 3.22", "iperf_sha256": "a" * 64,
            "ports": {"ask2": copy.deepcopy(port), "vendor": copy.deepcopy(port)}}


def test_equal_port_conditions_are_accepted_without_comparing_mac_addresses():
    parity_module().compare_ports(snapshot(), 1500)


@pytest.mark.parametrize("field,value", [
    ("mtu", 9000), ("carrier", 0), ("rings", "RX: 512\nTX: 512"),
    ("features", "generic-receive-offload: off"), ("rps", ["ff"] * 8),
    ("rss", "0: 0 0 0 0 0 0 0 0"), ("qdiscs", [{"kind": "fq_codel"}]),
])
def test_mismatched_generator_conditions_fail_closed(field, value):
    data = snapshot()
    data["ports"]["vendor"][field] = value
    with pytest.raises(ValueError, match=field):
        parity_module().compare_ports(data, 1500)


def test_jumbo_preflight_requires_both_paths_at_the_requested_mtu():
    with pytest.raises(ValueError, match="mtu"):
        parity_module().compare_ports(snapshot(), 9000)


def test_jumbo_preflight_rejects_equally_small_vlan_mtus():
    data = snapshot()
    for port in data["ports"].values():
        port.update(mtu=9000, vlan={"mtu": 1500, "addresses": []})
    with pytest.raises(ValueError, match="VLAN mtu"):
        parity_module().compare_ports(data, 9000)


def iperf(mss=8948, cc="bbr", duration=32):
    sample = {"start": 10, "end": 11, "seconds": 1, "bytes": 125000000,
              "bits_per_second": 1e9}
    return {
        "start": {"version": "iperf 3.22", "tcp_mss_default": mss,
                  "test_start": {"protocol": "TCP", "duration": duration,
                                 "num_streams": 8, "bidir": 1, "reverse": 0},
                  "connected": [{"local_host": "192.0.2.1", "remote_host": "198.51.100.2",
                                 "remote_port": 5201}] * 16},
        "intervals": [{"sum": sample, "sum_bidir_reverse": dict(sample)}],
        "end": {"sender_tcp_congestion": cc, "receiver_tcp_congestion": cc},
    }


@pytest.mark.parametrize("change", ["mss", "cc", "duration", "streams", "reverse", "endpoint"])
def test_pair_rejects_mismatched_actual_traffic_parameters(change):
    one, two = iperf(), iperf()
    if change == "mss":
        two["start"]["tcp_mss_default"] = 1448
    elif change == "cc":
        two["end"]["receiver_tcp_congestion"] = "cubic"
    elif change == "endpoint":
        two["start"]["connected"][0] = dict(two["start"]["connected"][0], remote_host="203.0.113.1")
    else:
        key, value = {"duration": ("duration", 10), "streams": ("num_streams", 4),
                      "reverse": ("reverse", 1)}[change]
        two["start"]["test_start"][key] = value
    with pytest.raises(ValueError):
        parity_module().compare_trials(one, two, 32, 8, "bbr", 1)


def test_matching_actual_trial_parameters_are_accepted():
    parity_module().compare_trials(iperf(), iperf(), 32, 8, "bbr", 1)


def test_battery_pairs_each_row_repetition_vyos_first_then_openwrt():
    plan = parity_module().battery_plan(["unicast", "nat"], 3)
    trials = plan["trials"]
    assert len(trials) == 72
    assert [trial["row"] for trial in trials] == ["unicast"] * 36 + ["nat"] * 36
    for offset in range(0, len(trials), 12):
        group = trials[offset:offset + 12]
        assert [trial["board"] for trial in group] == ["ask2"] * 6 + ["vendor"] * 6
        assert [trial["column"] for trial in group[:6]] == [
            "up-v4", "up-v6", "down-v4", "down-v6", "bidir-v4", "bidir-v6"]
        assert all(trial["rep"] == group[0]["rep"] for trial in group)
    assert plan["platforms"] == {"ask2": "VyOS", "vendor": "OpenWrt"}


def test_battery_records_unsafe_rows_without_scheduling_their_traffic():
    plan = parity_module().battery_plan(["unicast", "pppoe", "wireguard", "bridge"], 3)
    assert {trial["row"] for trial in plan["trials"]} == {"unicast"}
    blocked = {row["name"]: row["reason"] for row in plan["rows"] if row["status"] == "blocked"}
    assert "endpoint" in blocked["pppoe"]
    assert "drain" in blocked["wireguard"]
    assert "ownership" in blocked["bridge"]
    with pytest.raises(ValueError, match="unknown"):
        parity_module().battery_plan(["typo"], 3)
    with pytest.raises(ValueError, match="duplicate"):
        parity_module().battery_plan(["unicast", "unicast"], 3)


def test_common_generator_drift_is_rejected_even_when_paths_still_match():
    before = snapshot()
    after = copy.deepcopy(before)
    for port in after["ports"].values():
        port["rps"] = ["ff"] * 8
    with pytest.raises(ValueError, match="changed"):
        parity_module().compare_snapshots(before, after, 1500)


def test_battery_never_reports_best_of_two_as_best_of_three(tmp_path):
    module = parity_module()
    plan = module.battery_plan(["unicast"], 3)
    (tmp_path / "battery.json").write_text(json.dumps(plan))
    for column in ("up-v4", "up-v6", "down-v4", "down-v6", "bidir-v4", "bidir-v6"):
        for rep in (1, 2):
            pair = {"measurements": [{"board": board, "gbps": 9.0}
                                     for board in ("ask2", "vendor")]}
            (tmp_path / f"pair-unicast-{column}-r{rep}.json").write_text(json.dumps(pair))
    result = module.battery_report(tmp_path)
    assert result[0]["status"] == "pending"
    assert all(value["best"] is None and value["n"] == 2
               for board in result[0]["measurements"].values() for value in board.values())


def test_profile_can_be_saved_without_bench_topology(tmp_path):
    result = subprocess.run(
        ["python3", str(ROOT / "bin/testrig_parity.py"), "profile", str(tmp_path / "profile.json")],
        env=os.environ | {"DUR": "32", "STEADY_FROM": "10", "MEASURE_TO": "30",
                          "STREAMS": "8", "REPS": "3", "PARITY_CC": "bbr"},
        capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr
    data = json.loads((tmp_path / "profile.json").read_text())
    assert data["measure_to"] == 30
    assert data["duration"] == 32
    assert data["protocol"] == "matched-iperf-v1"


@pytest.fixture
def paired_runner(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name in ("testrig-parity-bidir.sh", "testrig-parity-profile.sh",
                 "testrig-env.sh", "testrig_parity.py", "testrig_wg_validation.py",
                 "testrig_iperf.py", "testrig-wg-server.py", "testrig-wgfp-stop.py"):
        shutil.copy(ROOT / "bin" / name, bin_dir / name)
    tune = bin_dir / "testrig-gen-tune.sh"
    tune.write_text('#!/bin/bash\n[ "$1" = check ]\n')
    tune.chmod(0o755)
    for name, status in (("flock", 0), ("pgrep", 1)):
        stub = bin_dir / name
        stub.write_text(f"#!/bin/bash\nexit {status}\n")
        stub.chmod(0o755)
    ssh = bin_dir / "ssh"
    ssh.write_text("""#!/bin/bash
printf '%s\\n' "$*" >> "$CALLS"
case "$*" in
  *" snapshot "*) cat "$SNAPSHOT";;
  *"cat /etc/os-release"*)
    case "$*" in
      *test@ask2*) echo ID=vyos;;
      *) echo 'ID=mono'; echo 'ID_LIKE="lede openwrt"';;
    esac;;
  *wgfp_peer*) echo off;;
  *"nft list tables"*)
    if [ "${MOCK_FOREIGN_NAT:-0}" = 1 ]; then echo "table ip ask2q"; fi;;
  *" identity "*) echo 123:456:5278;;
  *"123 456 server"*) [ "${MOCK_CLEANUP_FAIL:-0}" != 1 ] || exit 1;;
  *"/proc/sys/kernel/random/boot_id"*)
    echo x >> "$BOOTS"
    if [ "${MOCK_REBOOT:-0}" = 1 ] && [ "$(wc -l < "$BOOTS")" -gt 2 ]; then
      echo 22222222-2222-2222-2222-222222222222
    else echo 11111111-1111-1111-1111-111111111111; fi;;
esac
""")
    ssh.chmod(0o755)
    quick = bin_dir / "testrig-offload-quick.sh"
    quick.write_text('exec python3 "$MOCK_QUICK" "$@"\n')
    generator = tmp_path / "quick.py"
    generator.write_text("""
import copy, json, os, pathlib, sys
env = os.environ
with open(env["ORDER"], "a") as out:
    entry = {key: env.get(key) for key in
        ("DUT_KIND", "DUR", "STREAMS", "REPS", "SAMPLE", "DUT_TRACE", "RUN_ON",
         "BIDIR", "RIG_PRESERVE_RPS", "IPERF_PORT")}
    out.write(json.dumps(entry | {"cell": sys.argv[1]}) + "\\n")
if env.get("MOCK_QUICK_FAIL") and env["DUT_KIND"] == "vendor":
    raise SystemExit(7)
trial = json.loads(pathlib.Path(env["TRIAL"]).read_text())
bidir = int(env.get("BIDIR", "1"))
trial["start"]["test_start"]["bidir"] = bidir
trial["start"]["test_start"]["reverse"] = int(sys.argv[1].startswith("portfwd-up-"))
trial["start"]["connected"] = trial["start"]["connected"][:8 * (1 + bidir)]
for connection in trial["start"]["connected"]:
    connection["remote_port"] = int(env.get("IPERF_PORT", "5201"))
    if "-v6" in sys.argv[1]:
        connection.update(local_host="2001:db8:1::1", remote_host="2001:db8:2::2")
rate = 0 if env["DUT_KIND"] == "vendor" else 1e9
sample = trial["intervals"][0]
if not bidir:
    sample.pop("sum_bidir_reverse")
trial["intervals"] = []
for second in range(32):
    item = copy.deepcopy(sample)
    for value in item.values():
        value.update(start=second, end=second + 1, bytes=int(rate / 8), bits_per_second=rate)
    trial["intervals"].append(item)
for key in ("sum_sent", "sum_received", "sum_sent_bidir_reverse", "sum_received_bidir_reverse"):
    trial["end"][key] = dict(start=0, end=32, seconds=32, bytes=int(rate * 4),
                             bits_per_second=rate, sender="received" not in key)
tag = sys.argv[1] + ("-bidir" if bidir else "")
directory = pathlib.Path(env["J"])
directory.mkdir()
(directory / (tag + ".json")).write_text(json.dumps(trial))
cell = "wrong" if env.get("MOCK_WRONG_CELL") else tag
pathlib.Path(env["OUT"]).write_text(
    "cell,gbps,verdict\\n%s,%.3f,%s\\n" %
    (cell, rate * (1 + bidir) * 1e-9, env.get("MOCK_VERDICT", "SW")))
""")
    snapshot_path, trial_path = tmp_path / "snapshot.json", tmp_path / "trial.json"
    snapshot_path.write_text(json.dumps(snapshot()))
    trial_path.write_text(json.dumps(iperf(mss=1448)))
    output = tmp_path / "results"
    env = os.environ | {
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "TESTRIG_ENV": str(tmp_path / "no-config"), "RIG_KEY": "",
        "GEN1": "test@g1", "GEN2": "test@g2",
        "ASK2_DUT": "test@ask2", "VENDOR_DUT": "test@vendor",
        "RIG_OUT": str(tmp_path / "rig-state"), "RUN_DIR": str(output), "REPS": "2",
        "CALLS": str(tmp_path / "calls"), "BOOTS": str(tmp_path / "boots"),
        "SNAPSHOT": str(snapshot_path), "TRIAL": str(trial_path),
        "ORDER": str(tmp_path / "order"), "MOCK_QUICK": str(generator),
    }

    def run(*args, **overrides):
        return subprocess.run(["bash", str(bin_dir / "testrig-parity-bidir.sh"), *args],
                              env=env | overrides, capture_output=True, text=True, timeout=30)
    return run, output, tmp_path


@pytest.mark.parametrize("cell", ["nat-v4", "nat-v4-rev", "pppoe-down-v4", "portfwd-down-v4"])
def test_paired_runner_alternates_and_retains_zero_software_trials(paired_runner, cell):
    run, output, work = paired_runner
    result = run(cell)
    assert result.returncode == 0, result.stderr
    order = [json.loads(line) for line in (work / "order").read_text().splitlines()]
    assert [item["DUT_KIND"] for item in order] == ["ask2", "vendor", "vendor", "ask2"]
    keys = ("DUT_KIND", "DUR", "STREAMS", "REPS", "SAMPLE", "DUT_TRACE", "RUN_ON")
    assert all({key: item[key] for key in keys} | {"DUT_KIND": "same"} == {
        "DUT_KIND": "same", "DUR": "32", "STREAMS": "8", "REPS": "2",
        "SAMPLE": "0", "DUT_TRACE": "0", "RUN_ON": "agent"} for item in order)
    pair = json.loads((output / f"pair-{cell}-r1.json").read_text())
    assert pair["measurements"][1]["gbps"] == 0
    assert pair["measurements"][1]["verdict"] == "SW"
    assert (output / "COMPLETE").exists()


def test_preflight_sends_no_traffic_and_never_overwrites_results(paired_runner):
    run, output, work = paired_runner
    result = run("--preflight")
    assert result.returncode == 0, result.stderr
    assert not (work / "order").exists()
    assert not (output / "COMPLETE").exists()
    calls = (work / "calls").read_text()
    result = run("--preflight")
    assert result.returncode != 0 and "already exists" in result.stderr
    assert (work / "calls").read_text() == calls


@pytest.mark.parametrize("setting,message", [
    ("MOCK_REBOOT", "rebooted"), ("MOCK_WRONG_CELL", "unexpected cell"),
])
def test_paired_runner_does_not_mark_invalid_campaign_complete(paired_runner, setting, message):
    run, output, _ = paired_runner
    result = run("nat-v4", **{setting: "1"})
    assert result.returncode != 0 and message in result.stderr
    assert not (output / "COMPLETE").exists()


def test_jumbo_traffic_requires_confirmed_cold_boots(paired_runner):
    run, output, work = paired_runner
    data = snapshot()
    for port in data["ports"].values():
        port["mtu"] = 9000
    (work / "snapshot.json").write_text(json.dumps(data))
    result = run("nat-v4", PARITY_MTU="9000")
    assert result.returncode != 0 and "confirmed cold-boot IDs" in result.stderr
    assert not (work / "order").exists()
    assert not (output / "COMPLETE").exists()


def test_equally_wrong_address_families_do_not_pass_as_requested_cell(paired_runner):
    run, output, work = paired_runner
    data = iperf(mss=1448)
    for connection in data["start"]["connected"]:
        connection.update(local_host="2001:db8:1::1", remote_host="2001:db8:2::2")
    (work / "trial.json").write_text(json.dumps(data))
    result = run("nat-v4")
    assert result.returncode != 0 and "address family" in result.stderr
    assert not (output / "COMPLETE").exists()


@pytest.mark.parametrize("row", ["unicast", "port-forward"])
def test_row_battery_runs_three_vyos_then_openwrt_groups_and_owns_servers(paired_runner, row):
    run, output, work = paired_runner
    result = run("--battery", row, REPS="3")
    assert result.returncode == 0, result.stderr
    order = [json.loads(line) for line in (work / "order").read_text().splitlines()]
    assert [item["DUT_KIND"] for item in order] == (["ask2"] * 6 + ["vendor"] * 6) * 3
    assert all(item["RIG_PRESERVE_RPS"] == "1" and item["IPERF_PORT"] == "5278" for item in order)
    assert len(list(output.glob("pair-*.json"))) == 18
    matrix = json.loads((output / "matrix.json").read_text())
    assert matrix[0]["status"] == "complete"
    assert all(value["n"] == 3 for board in matrix[0]["measurements"].values()
               for value in board.values())
    assert (output / "COMPLETE").exists()
    calls = (work / "calls").read_text().splitlines()
    assert sum("python3 - start " in call for call in calls) == 4
    assert sum("python3 - remove " in call for call in calls) == 4
    assert len({call.split(" start ")[1].split()[0] for call in calls if "python3 - start " in call}) == 4


def test_row_battery_keeps_gated_rows_explicitly_partial(paired_runner):
    run, output, work = paired_runner
    result = run("--battery", "unicast", "wireguard", REPS="3")
    assert result.returncode == 0, result.stderr
    assert (output / "PARTIAL").exists() and not (output / "COMPLETE").exists()
    matrix = json.loads((output / "matrix.json").read_text())
    assert matrix[1]["status"] == "blocked" and "drain" in matrix[1]["reason"]
    assert len((work / "order").read_text().splitlines()) == 36


@pytest.mark.parametrize("setting,message", [
    ("MOCK_FOREIGN_NAT", "foreign ask2q"),
    ("MOCK_QUICK_FAIL", ""),
    ("MOCK_CLEANUP_FAIL", "cleanup is uncertain"),
])
def test_row_battery_stops_on_conflicts_failures_or_uncertain_cleanup(paired_runner, setting, message):
    run, output, _ = paired_runner
    result = run("--battery", "unicast", REPS="3", **{setting: "1"})
    assert result.returncode != 0 and message in result.stderr
    assert not (output / "COMPLETE").exists() and not (output / "PARTIAL").exists()


def test_row_battery_preflight_never_starts_servers_or_traffic(paired_runner):
    run, output, work = paired_runner
    result = run("--battery", "--preflight", "unicast", REPS="3")
    assert result.returncode == 0, result.stderr
    assert not (work / "order").exists()
    assert "python3 - start " not in (work / "calls").read_text()
    assert not (output / "COMPLETE").exists()


@pytest.mark.parametrize("verdict", ["SKIP", "FAIL(no traffic)"])
def test_failed_or_skipped_pairs_cannot_enter_the_matrix(paired_runner, verdict):
    run, output, _ = paired_runner
    result = run("nat-v4", MOCK_VERDICT=verdict)
    assert result.returncode != 0 and "failed or skipped" in result.stderr
    assert not list(output.glob("pair-*.json")) and not (output / "COMPLETE").exists()
