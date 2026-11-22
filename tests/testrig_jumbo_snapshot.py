"""Host checks for paired jumbo generator snapshots."""

import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest

pytestmark = pytest.mark.host
ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def snapshot_rig(tmp_path):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    for name in ("testrig-jumbo-snapshot.sh", "testrig-env.sh", "testrig_parity.py"):
        source = ROOT / "bin" / name
        if source.exists():
            shutil.copy(source, bindir / name)
    tune = bindir / "testrig-gen-tune.sh"
    tune.write_text('#!/bin/sh\n[ "$1" = check ] && echo tuning-ok\n')
    tune.chmod(0o755)
    fakebin = tmp_path / "fakebin"
    fakebin.mkdir()
    ssh = fakebin / "ssh"
    ssh.write_text(
        '#!/bin/sh\n'
        'printf "%s\\n" "$*" >> "$CALLS"\n'
        'case "$*" in *gen2* ) [ "${FAIL_GEN2:-0}" = 0 ] || exit 255;; esac\n'
        'cat >/dev/null\n'
        'printf \'{"snapshot":"%s"}\\n\' "$*"\n'
    )
    ssh.chmod(0o755)
    output = tmp_path / "output"
    output.mkdir()
    env = os.environ | {
        "PATH": f"{fakebin}:{os.environ['PATH']}",
        "TESTRIG_ENV": str(tmp_path / "no-config"),
        "GEN1": "test@gen1", "GEN2": "test@gen2", "GEN_IF": "rootnic",
        "GEN_IF_NS": "othernic", "VENDOR_NS": "testns", "RIG_KEY": "",
        "CALLS": str(tmp_path / "calls"),
    }

    def run(directory=output, phase="before", **extra):
        return subprocess.run(["bash", str(bindir / "testrig-jumbo-snapshot.sh"),
                               str(directory), phase], env=env | extra,
                              capture_output=True, text=True, timeout=30)

    return run, output


def test_snapshot_captures_both_generator_paths_and_tuning(snapshot_rig):
    run, output = snapshot_rig
    result = run()
    assert result.returncode == 0, result.stderr
    assert (output / "generator-tuning-before.txt").read_text().strip() == "tuning-ok"
    assert "test@gen1" in json.loads((output / "gen1-before.json").read_text())["snapshot"]
    assert "test@gen2" in json.loads((output / "gen2-before.json").read_text())["snapshot"]


def test_snapshot_does_not_overwrite_prior_evidence(snapshot_rig):
    run, output = snapshot_rig
    assert run().returncode == 0
    before = (output / "gen1-before.json").read_text()
    result = run()
    assert result.returncode != 0
    assert (output / "gen1-before.json").read_text() == before


@pytest.mark.parametrize("phase", ["", "middle", "../before"])
def test_snapshot_rejects_invalid_phase_without_output(snapshot_rig, phase):
    run, output = snapshot_rig
    assert run(phase=phase).returncode != 0
    assert list(output.iterdir()) == []


def test_snapshot_surfaces_generator_failure(snapshot_rig):
    run, output = snapshot_rig
    result = run(FAIL_GEN2="1")
    assert result.returncode != 0
    assert (output / "gen1-before.json").exists()
