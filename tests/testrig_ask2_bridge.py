"""Host safety checks for the legacy ASK2 bridge runner."""

import os
from pathlib import Path
import subprocess

import pytest

pytestmark = pytest.mark.host
ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("parity", [None, "0", "1"])
def test_bridge_runner_refuses_before_remote_access_or_output(tmp_path, parity):
    calls = tmp_path / "ssh-calls"
    ssh = tmp_path / "ssh"
    ssh.write_text('#!/bin/sh\nprintf "%s\\n" "$*" >> "$CALLS"\nexit 1\n')
    ssh.chmod(0o755)
    env = os.environ | {
        "PATH": f"{tmp_path}:{os.environ['PATH']}",
        "HOME": str(tmp_path),
        "TESTRIG_ENV": str(tmp_path / "no-rig-config"),
        "ASK2_DUT": "test@dut",
        "GEN1": "test@gen1",
        "GEN2": "test@gen2",
        "RIG_KEY": "",
        "OUTDIR": str(tmp_path / "results"),
        "CALLS": str(calls),
    }
    if parity is None:
        env.pop("RIG_PARITY", None)
    else:
        env["RIG_PARITY"] = parity

    result = subprocess.run(
        ["bash", str(ROOT / "bin/testrig-ask2-bridge.sh")],
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
    )

    assert not calls.exists(), calls.read_text()
    assert not (tmp_path / "results").exists()
    assert result.returncode == 2
    assert "ownership" in result.stderr.lower()
