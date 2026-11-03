"""Host regression tests for fail-closed WGFP sink lifetime handling."""

from pathlib import Path
import os
import subprocess

import pytest

pytestmark = pytest.mark.host
ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "kernel/common/files/drivers/net/ethernet/freescale/dpaa/dpaa_wgfp.c"
HARNESS = ROOT / "tests/dut/wgfp_sink_lifetime.c"


def test_sink_pool_is_not_destroyed_or_reused_after_uncertain_creation(tmp_path):
    source = SOURCE.read_text()
    start = source.index("static int wgfp_sink_create(")
    end = source.index("\nstatic const struct file_operations wgfp_ctl_fops", start)
    (tmp_path / "wgfp_sink_body.h").write_text(source[start:end])
    binary = tmp_path / "wgfp_sink_lifetime"
    subprocess.run(
        ["cc", "-std=gnu11", "-Wall", "-Wextra", "-Werror",
         "-Wno-unused-parameter", "-I", str(tmp_path), str(HARNESS), "-o", str(binary)],
        check=True,
    )
    subprocess.run([str(binary)], check=True)


@pytest.mark.parametrize(
    ("script", "settings"),
    [
        ("bin/testrig-wgfp-p1.sh", {"RECREATE": "1"}),
        ("bin/testrig-wgfp-p2a.sh", {"RECREATE": "1"}),
        ("bin/testrig-wgfp-p2b.sh", {"AUTO": "0"}),
    ],
)
def test_legacy_harness_rejects_destroy_before_contacting_the_board(tmp_path, script, settings):
    marker = tmp_path / "ssh-called"
    ssh = tmp_path / "ssh"
    ssh.write_text(f"#!/bin/sh\n: > '{marker}'\nexit 99\n")
    ssh.chmod(0o755)
    env = os.environ | settings | {"PATH": f"{tmp_path}:{os.environ['PATH']}"}
    result = subprocess.run(
        ["bash", str(ROOT / script)], env=env, text=True, capture_output=True
    )
    assert result.returncode != 0
    assert "sink destruction is not safe" in result.stderr
    assert not marker.exists()
