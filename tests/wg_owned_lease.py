"""Verify that WireGuard rig leases serialize cleanup and retain uncertain state."""

import os
from pathlib import Path
import subprocess

import pytest

pytestmark = pytest.mark.host
LEASE = Path(__file__).resolve().parents[1] / "bin/testrig-wg-lease.sh"


def lease_env(tmp_path):
    return os.environ | {
        "WG_LEASE_DIR": str(tmp_path / "lease"),
        "WG_LEASE_KIND": "multi",
        "WG_LEASE_COUNT": "2",
        "DUT_KIND": "ask2",
        "DUT": "vyos@192.0.2.1",
        "GEN1": "admin@192.0.2.2",
        "GEN2": "admin@192.0.2.3",
        "RIG_NS": "",
        "IPERF_BASE": "5240",
    }


def test_lease_excludes_concurrent_teardown(tmp_path):
    env = lease_env(tmp_path)
    owner = subprocess.Popen(
        ["bash", "-c",
         '. "$1"; wg_lease_begin || exit; echo ready; read -r _; wg_lease_end', "_", str(LEASE)],
        env=env, text=True, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    try:
        assert owner.stdout.readline().strip() == "ready"
        contender = subprocess.run(
            ["bash", "-c", '. "$1"; wg_lease_load', "_", str(LEASE)],
            env=env, text=True, capture_output=True, check=False, timeout=10,
        )
        assert contender.returncode != 0
        assert "in use" in contender.stderr
        owner.stdin.write("finish\n")
        owner.stdin.flush()
        _, stderr = owner.communicate(timeout=10)
        assert owner.returncode == 0, stderr
        assert not (tmp_path / "lease").exists()
    finally:
        if owner.poll() is None:
            owner.kill()
            owner.wait(timeout=10)


def test_lease_keeps_metadata_when_resource_ownership_is_uncertain(tmp_path):
    env = lease_env(tmp_path)
    result = subprocess.run(
        ["bash", "-c", """
          . "$1"; wg_lease_begin || exit
          printf 'pending\\n' > "$WG_LEASE_DIR/route-far-0"
          if wg_lease_end; then exit 1; fi
          [ -f "$WG_LEASE_DIR/token" ] && [ -f "$WG_LEASE_DIR/options" ] || exit 1
          rm -- "$WG_LEASE_DIR/route-far-0"
          wg_lease_end
        """, "_", str(LEASE)],
        env=env, text=True, capture_output=True, check=False, timeout=10,
    )
    assert result.returncode == 0, result.stderr
    assert "uncertain resource" in result.stderr
    assert not (tmp_path / "lease").exists()
