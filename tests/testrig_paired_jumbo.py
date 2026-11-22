"""Host checks for matched, alternating jumbo row comparisons."""

import copy
import csv
import importlib
import json
import os
from pathlib import Path
import sys

import pytest

from testrig_iperf_measurement import tcp_result

pytestmark = pytest.mark.host
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bin"))


@pytest.fixture
def jumbo(monkeypatch):
    module = importlib.import_module("testrig_jumbo_parity")
    spec = importlib.util.spec_from_file_location("jumbo_test_parity", ROOT / "bin/testrig_parity.py")
    parity = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(parity)
    monkeypatch.setattr(module, "testrig_parity", parity)
    return module


def snapshots():
    port = {"mtu": 1500, "carrier": 1, "addresses": ["192.0.2.1/24"],
            "rings": "RX: 4096", "features": "gro: on", "coalescing": "adaptive",
            "channels": "8", "rss": "0: 0 1 2", "rps": ["00"] * 8,
            "rss_fields": {"tcp4": ["IP SA"], "tcp6": ["IP SA"]},
            "qdiscs": [{"kind": "fq"}], "link": "Speed: 10000Mb/s",
            "vlan": {"mtu": 1500, "addresses": ["192.0.2.10/24"]}}
    baseline = {"kernel": "test", "iperf": "iperf 3.22", "iperf_sha256": "a" * 64,
                "ports": {"ask2": copy.deepcopy(port), "vendor": copy.deepcopy(port)}}
    ask_before = copy.deepcopy(baseline)
    ask_after = copy.deepcopy(baseline)
    vendor_before = copy.deepcopy(baseline)
    vendor_after = copy.deepcopy(baseline)
    for data, board in ((ask_before, "ask2"), (ask_after, "ask2"),
                        (vendor_before, "vendor"), (vendor_after, "vendor")):
        data["ports"][board]["mtu"] = 9000
        data["ports"][board]["vlan"]["mtu"] = 9000
    return baseline, ask_before, ask_after, vendor_before, vendor_after


def test_schedule_alternates_dut1_then_dut2_for_each_row_and_repetition(jumbo):
    plan = jumbo.schedule(["unicast-9k", "nat-9k"], 3)
    assert plan == [(row, rep, board)
                    for row in ("unicast-9k", "nat-9k")
                    for rep in (1, 2, 3) for board in ("ask2", "vendor")]


@pytest.mark.parametrize(("rows", "reps"), [
    (["bridge-9k"], 3), (["nat-9k", "nat-9k"], 3), (["nat-9k"], 2),
])
def test_schedule_rejects_unsupported_or_non_best_of_three(jumbo, rows, reps):
    with pytest.raises(ValueError):
        jumbo.schedule(rows, reps)


def test_matched_jumbo_generator_conditions_are_accepted(jumbo):
    jumbo.compare_group(*snapshots())


@pytest.mark.parametrize("board", ["ask2", "vendor"])
def test_pre_traffic_snapshot_rejects_drift(jumbo, board):
    baseline, ask_before, _, vendor_before, _ = snapshots()
    current = ask_before if board == "ask2" else vendor_before
    jumbo.compare_snapshot(baseline, current, board, "before")
    current["ports"][board]["rings"] = "RX: 512"
    with pytest.raises(ValueError, match="conditions changed"):
        jumbo.compare_snapshot(baseline, current, board, "before")


@pytest.mark.parametrize("field", ["kernel", "iperf", "iperf_sha256"])
def test_missing_generator_identity_is_rejected(jumbo, field):
    baseline, ask_before, ask_after, vendor_before, vendor_after = snapshots()
    for data in (baseline, ask_before, ask_after, vendor_before, vendor_after):
        data.pop(field)
    with pytest.raises(ValueError):
        jumbo.compare_group(baseline, ask_before, ask_after, vendor_before, vendor_after)


@pytest.mark.parametrize("defect", [
    "active-drift", "inactive-drift", "post-drift", "iperf", "vlan-mtu", "both-drift",
])
def test_jumbo_snapshot_drift_is_rejected(jumbo, defect):
    baseline, ask_before, ask_after, vendor_before, vendor_after = snapshots()
    if defect == "active-drift":
        ask_before["ports"]["ask2"]["rings"] = "RX: 512"
    elif defect == "inactive-drift":
        ask_before["ports"]["vendor"]["rps"] = ["ff"]
    elif defect == "post-drift":
        vendor_after["ports"]["vendor"]["rss"] = "0: 0 0"
    elif defect == "iperf":
        vendor_before["iperf_sha256"] = "b" * 64
    elif defect == "vlan-mtu":
        vendor_before["ports"]["vendor"]["vlan"]["mtu"] = 1500
    else:
        for data, board in ((ask_before, "ask2"), (ask_after, "ask2"),
                            (vendor_before, "vendor"), (vendor_after, "vendor")):
            data["ports"][board]["rps"] = ["ff"]
    with pytest.raises(ValueError):
        jumbo.compare_group(baseline, ask_before, ask_after, vendor_before, vendor_after)


def write_trial(csv_path, json_path, cell, bidir, family):
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.parent.mkdir(parents=True, exist_ok=True)
    tag = cell + ("-bidir" if bidir else "")
    with csv_path.open("w", newline="") as output:
        writer = csv.DictWriter(output, fieldnames=["cell", "gbps", "verdict"])
        writer.writeheader()
        writer.writerow({"cell": tag, "gbps": 3.0 if bidir else 1.0, "verdict": "HW"})
    data = tcp_result(bidir=bidir, duration=32, streams=8, rate=1e9, reverse_rate=2e9)
    data["start"].update(
        version="iperf 3.22", tcp_mss_default=8948 if family == 4 else 8928,
        connected=[{"local_host": "192.0.2.1" if family == 4 else "2001:db8::1",
                    "remote_host": "192.0.2.2" if family == 4 else "2001:db8::2",
                    "remote_port": 5278}] * (16 if bidir else 8),
    )
    data["end"].update(sender_tcp_congestion="bbr", receiver_tcp_congestion="bbr")
    json_path.write_text(json.dumps(data))


def trials(tmp_path, jumbo):
    vendor = tmp_path / "vendor"
    vendor.mkdir()
    boot = "020b5ec3-7705-49a0-859a-db4d0fac67af"
    (tmp_path / "boot-ask2.txt").write_text(boot + "\n")
    (tmp_path / "boot-vendor.txt").write_text(boot + "\n")
    (tmp_path / "ask2-unicast-9k-r1.complete").write_text(f"boot={boot}\n")
    (vendor / "COMPLETE").write_text(f"mode=run-routed\nboot={boot}\n")
    baseline, ask_before, ask_after, vendor_before, vendor_after = snapshots()
    for directory, before, after in (
        (tmp_path / "baseline", baseline, None),
        (tmp_path / "ask2-unicast-9k-r1-snapshot", ask_before, ask_after),
        (tmp_path / "vendor-unicast-9k-r1-snapshot", vendor_before, vendor_after),
    ):
        directory.mkdir()
        for gen in (1, 2):
            (directory / f"gen{gen}-before.json").write_text(json.dumps(before))
            if after is not None:
                (directory / f"gen{gen}-after.json").write_text(json.dumps(after))
    for column, cell, bidir in jumbo.cells("unicast-9k"):
        family = 6 if column.endswith("v6") else 4
        tag = cell + ("-bidir" if bidir else "")
        stem = tmp_path / f"ask2-unicast-9k-{column}-r1"
        write_trial(stem.with_suffix(".csv"), Path(f"{stem}-json") / f"{tag}.json",
                    cell, bidir, family)
        write_trial(vendor / f"quick9k-{tag}-r1.csv",
                    vendor / "json" / f"{tag}-r1" / f"{tag}.json", cell, bidir, family)
    return vendor


@pytest.fixture
def profile_env(monkeypatch):
    for key, value in {
        "DUR": "32", "STEADY_FROM": "10", "MEASURE_TO": "30",
        "STREAMS": "8", "REPS": "3", "PARITY_CC": "bbr", "PARITY_MTU": "9000",
    }.items():
        monkeypatch.setenv(key, value)


def test_record_requires_and_scores_all_six_raw_pairs(jumbo, tmp_path, profile_env):
    vendor = trials(tmp_path, jumbo)
    jumbo.record_group(tmp_path, vendor, "unicast-9k", 1)
    pairs = sorted(tmp_path.glob("pair-*.json"))
    assert len(pairs) == 6
    for path in pairs:
        pair = json.loads(path.read_text())
        assert pair["profile"]["mtu"] == 9000
        assert [row["board"] for row in pair["measurements"]] == ["ask2", "vendor"]


def test_record_rejects_mismatched_endpoint_without_partial_results(jumbo, tmp_path, profile_env):
    vendor = trials(tmp_path, jumbo)
    path = vendor / "json/unicast-v6-bidir-r1/unicast-v6-bidir.json"
    data = json.loads(path.read_text())
    data["start"]["connected"][0]["remote_host"] = "2001:db8::3"
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="endpoints differ"):
        jumbo.record_group(tmp_path, vendor, "unicast-9k", 1)
    assert not list(tmp_path.glob("pair-*.json"))


def test_reference_profile_differs_only_by_mtu(jumbo, profile_env):
    reference = {
        "protocol": "matched-iperf-v1", "duration": 32, "steady_from": 10,
        "measure_to": 30, "streams": 8, "repetitions": 3, "congestion": "bbr",
        "mtu": 1500, "interval": 1, "zerocopy": True, "extra_sampling": False,
        "rate_semantics": "client sender normally, client receiver with -R; bidir adds reverse receiver",
    }
    jumbo.compare_reference(reference)
    reference["congestion"] = "cubic"
    with pytest.raises(ValueError, match="reference"):
        jumbo.compare_reference(reference)


def test_record_rejects_incomplete_adapter_cleanup(jumbo, tmp_path, profile_env):
    vendor = trials(tmp_path, jumbo)
    (vendor / "COMPLETE").unlink()
    with pytest.raises(OSError):
        jumbo.record_group(tmp_path, vendor, "unicast-9k", 1)
    assert not list(tmp_path.glob("pair-*.json"))


@pytest.mark.parametrize(("key", "value"), [("REPS", "2"), ("PARITY_MTU", "1500")])
def test_record_rejects_non_jumbo_or_non_best_of_three(jumbo, tmp_path, profile_env,
                                                       monkeypatch, key, value):
    monkeypatch.setenv(key, value)
    with pytest.raises(ValueError, match="MTU 9000 and three repetitions"):
        jumbo.record_group(tmp_path, tmp_path, "unicast-9k", 1)
