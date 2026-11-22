"""Read-only status for an in-progress paired battery."""

import json
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.host
SCRIPT = Path(__file__).resolve().parents[1] / "bin/testrig-paired-status.py"


def campaign(path):
    profile = {"duration": 32, "repetitions": 3, "streams": 8}
    (path / "protocol.json").write_text(json.dumps(profile))
    (path / "battery.json").write_text(json.dumps({
        "repetitions": 3, "platforms": {"ask2": "VyOS", "vendor": "OpenWrt"},
        "rows": [{"name": "unicast", "status": "pending"},
                 {"name": "wireguard", "status": "blocked", "reason": "drain gate"}],
        "trials": [{"row": "unicast", "column": "up-v4", "rep": rep,
                    "board": board, "cell": "unicast-v4", "bidir": 0}
                   for rep in range(1, 4) for board in ("ask2", "vendor")],
    }))
    return profile


def pair(path, profile, rep, one, two):
    (path / f"pair-unicast-up-v4-r{rep}.json").write_text(json.dumps({
        "profile": profile,
        "measurements": [
            {"board": "ask2", "gbps": one, "cell": "unicast-v4", "verdict": "HW"},
            {"board": "vendor", "gbps": two, "cell": "unicast-v4", "verdict": "HW"},
        ],
    }))


def run(path):
    return subprocess.run(["python3", str(SCRIPT), str(path)], text=True, capture_output=True)


def test_status_labels_provisional_results_and_blocked_rows(tmp_path):
    profile = campaign(tmp_path)
    pair(tmp_path, profile, 1, 9.1, 9.2)
    pair(tmp_path, profile, 2, 9.3, 9.0)
    result = run(tmp_path)
    assert result.returncode == 0, result.stderr
    assert "DUT1 VyOS" in result.stdout and "DUT2 OpenWrt" in result.stdout
    assert "9.30(2/3)" in result.stdout and "9.20(2/3)" in result.stdout
    assert "wireguard: drain gate" in result.stdout
    assert "provisional" in result.stdout
    assert not (tmp_path / "matrix.json").exists()


def test_status_never_mixes_mismatched_or_failed_pairs(tmp_path):
    profile = campaign(tmp_path)
    pair(tmp_path, profile | {"duration": 30}, 1, 9.1, 9.2)
    result = run(tmp_path)
    assert result.returncode != 0 and "profile" in result.stderr
    pair(tmp_path, profile, 1, 9.1, float("nan"))
    result = run(tmp_path)
    assert result.returncode != 0 and "rate" in result.stderr
    pair(tmp_path, profile, 1, 9.1, 9.2)
    sample = tmp_path / "pair-unicast-up-v4-r1.json"
    data = json.loads(sample.read_text())
    data["measurements"][1]["cell"] = "nat-v4"
    sample.write_text(json.dumps(data))
    result = run(tmp_path)
    assert result.returncode != 0 and "cell" in result.stderr


def test_status_labels_complete_best_of_three(tmp_path):
    profile = campaign(tmp_path)
    for rep, one in enumerate((9.1, 9.4, 9.3), 1):
        pair(tmp_path, profile, rep, one, 9.2)
    result = run(tmp_path)
    assert result.returncode == 0, result.stderr
    assert "9.40(3/3)" in result.stdout


@pytest.mark.parametrize("verdict", ["SKIP", "FAIL(no traffic)"])
def test_status_refuses_failed_pair(tmp_path, verdict):
    profile = campaign(tmp_path)
    pair(tmp_path, profile, 1, 9.1, 9.2)
    sample = tmp_path / "pair-unicast-up-v4-r1.json"
    data = json.loads(sample.read_text())
    data["measurements"][0]["verdict"] = verdict
    sample.write_text(json.dumps(data))
    result = run(tmp_path)
    assert result.returncode != 0 and "failed or skipped" in result.stderr


def test_status_flags_software_classification_without_discarding_it(tmp_path):
    profile = campaign(tmp_path)
    pair(tmp_path, profile, 1, 9.1, 9.2)
    sample = tmp_path / "pair-unicast-up-v4-r1.json"
    data = json.loads(sample.read_text())
    data["measurements"][1]["verdict"] = "SW"
    sample.write_text(json.dumps(data))
    result = run(tmp_path)
    assert result.returncode == 0, result.stderr
    assert "9.20(1/3)*" in result.stdout
