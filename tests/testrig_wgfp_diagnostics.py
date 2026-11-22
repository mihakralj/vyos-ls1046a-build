"""Host safety checks for legacy WGFP diagnostic runners."""

import os
from pathlib import Path
import re
import subprocess

import pytest

pytestmark = pytest.mark.host
ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize(
    ("name", "p7"),
    [
        ("testrig-wgfp-p3.sh", "0"),
        ("testrig-wgfp-p6.sh", "0"),
        ("testrig-wgfp-p6.sh", "1"),
        ("testrig-wgfp-p2b3.sh", "0"),
    ],
)
def test_unsafe_wgfp_diagnostic_refuses_before_remote_access(tmp_path, name, p7):
    script = ROOT / "bin" / name
    source = script.read_text()
    assert not re.search(r"\beval\b|\$\{![^}]*\}|\$\{[^}]*@P\}", source), (
        "refusing to execute a diagnostic containing unsafe command expansion"
    )

    calls = tmp_path / "ssh-calls"
    ssh = tmp_path / "ssh"
    ssh.write_text('#!/bin/sh\nprintf "%s\\n" "$*" >> "$CALLS"\nexit 1\n')
    ssh.chmod(0o755)
    env = os.environ | {
        "PATH": f"{tmp_path}:{os.environ['PATH']}",
        "HOME": str(tmp_path),
        "CALLS": str(calls),
        "WG_LEASE_TOKEN": "a" * 32,
        "P7": p7,
    }
    result = subprocess.run(
        ["bash", str(script)],
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
    )

    assert not calls.exists(), calls.read_text()
    assert result.returncode != 0
    assert "ownership" in result.stderr.lower()
