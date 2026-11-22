"""Host tests for the fresh ASK2 matrix driver."""

import os
from pathlib import Path
import shutil
import subprocess

import pytest

pytestmark = pytest.mark.host
ROOT = Path(__file__).resolve().parents[1]

FAKE_DRIVER = r"""#!/usr/bin/env bash
name=${0##*/}
printf '%s %s ports=%s outer=%s\n' "$name" "$*" "${PORTS:-}" "${OUTER:-4}" >> "$MATRIX_LOG"
if [ "${FAKE_MATRIX_FAIL:-}" = quick ] && [ "$name" = testrig-offload-quick.sh ]; then
  echo "quick run failed" >&2
  exit 1
fi
if [ "${FAKE_MATRIX_FAIL:-}" = check ] && [ "$name" = testrig-gen-tune.sh ] && [ "${1:-}" = check ]; then
  echo "generator coalescing drift"
  exit 1
fi
"""


@pytest.fixture
def matrix(tmp_path):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    for name in ("testrig-ask2-matrix.sh", "testrig-env.sh"):
        shutil.copy(ROOT / "bin" / name, bindir / name)
    for name in ("testrig-gen-tune.sh", "testrig-offload-quick.sh", "wg-parity.sh"):
        path = bindir / name
        path.write_text(FAKE_DRIVER)
        path.chmod(0o755)
    (bindir / "ask2-matrix-table.py").write_text("print('matrix table')\n")
    date = tmp_path / "date"
    date.write_text("#!/bin/sh\ncase \"$1\" in +%T) echo 00:00:00;; *) echo 20260101-0000;; esac\n")
    date.chmod(0o755)
    log = tmp_path / "runs.log"
    out = tmp_path / "results"
    env = os.environ | {
        "PATH": f"{tmp_path}:{os.environ['PATH']}",
        "TESTRIG_ENV": str(tmp_path / "no-rig-config"),
        "ASK2_DUT": "test@dut",
        "OUTDIR": str(out),
        "REPS": "1",
        "WG_REPS": "1",
        "WG_PORT": "",
        "OUTER": "4",
        "FAKE_MATRIX_FAIL": "",
        "MATRIX_LOG": str(log),
    }

    def run(**extra):
        result = subprocess.run(
            ["bash", str(bindir / "testrig-ask2-matrix.sh")],
            env=env | extra,
            capture_output=True,
            text=True,
            timeout=10,
        )
        return result, log.read_text().splitlines() if log.exists() else []

    return run, out


def test_matrix_stops_after_first_failed_cell(matrix):
    run, _ = matrix
    result, calls = run(FAKE_MATRIX_FAIL="quick")
    assert result.returncode != 0
    assert len([line for line in calls if line.startswith("testrig-offload-quick.sh")]) == 1
    assert not any(line.startswith("wg-parity.sh") for line in calls)


def test_matrix_reports_tuning_drift_and_stops(matrix):
    run, _ = matrix
    result, calls = run(FAKE_MATRIX_FAIL="check")
    assert result.returncode != 0
    assert "generator coalescing drift" in result.stdout + result.stderr
    assert not any(line.startswith("testrig-offload-quick.sh") for line in calls)


def test_matrix_refuses_to_reuse_an_existing_run(matrix):
    run, out = matrix
    run_dir = out / "ask2-matrix-20260101-0000"
    run_dir.mkdir(parents=True)
    (run_dir / "existing").write_text("keep")
    result, calls = run()
    assert result.returncode != 0
    assert (run_dir / "existing").read_text() == "keep"
    assert calls == []


def test_matrix_rejects_an_invalid_wireguard_port_before_tuning(matrix):
    run, _ = matrix
    result, calls = run(WG_PORT="eth0")
    assert result.returncode != 0
    assert "WG_PORT" in result.stderr
    assert calls == []


def test_matrix_uses_eth3_for_both_wireguard_outer_families(matrix):
    run, _ = matrix
    result, calls = run()
    assert result.returncode == 0, result.stdout + result.stderr
    wg = [line for line in calls if line.startswith("wg-parity.sh")]
    assert len(wg) == 2
    assert all("ports=eth3" in line for line in wg)
    assert {line.rsplit("outer=", 1)[1] for line in wg} == {"4", "6"}


def test_matrix_does_not_inherit_the_outer_family_for_the_ipv4_row(matrix):
    run, _ = matrix
    result, calls = run(OUTER="6")
    assert result.returncode == 0, result.stdout + result.stderr
    wg = [line for line in calls if line.startswith("wg-parity.sh")]
    assert [line.rsplit("outer=", 1)[1] for line in wg] == ["4", "6"]
