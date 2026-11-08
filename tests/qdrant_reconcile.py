"""Host tests for the Qdrant reconciliation report."""

import os
from pathlib import Path
import subprocess

import pytest

pytestmark = pytest.mark.host
SCRIPT = Path(__file__).resolve().parents[1] / "bin/qdrant-reconcile.sh"


def report(tmp_path, commits="", full_log="", fail=False):
    git = tmp_path / "git"
    git.write_text(
        """#!/bin/sh
if [ "${FAKE_GIT_FAIL:-0}" = 1 ]; then
  echo "git log failed" >&2
  exit 2
fi
case "$*" in
  *--oneline*) printf '%s' "$FAKE_COMMITS" ;;
  *--format=*) printf '%s' "$FAKE_FULL_LOG" ;;
  *) exit 3 ;;
esac
"""
    )
    git.chmod(0o755)
    env = os.environ | {
        "PATH": f"{tmp_path}:{os.environ['PATH']}",
        "FAKE_COMMITS": commits,
        "FAKE_FULL_LOG": full_log,
        "FAKE_GIT_FAIL": "1" if fail else "0",
    }
    return subprocess.run(
        ["bash", str(SCRIPT)], cwd=tmp_path, env=env,
        text=True, capture_output=True, timeout=10,
    )


def test_empty_period_produces_complete_report(tmp_path):
    result = report(tmp_path)
    assert result.returncode == 0, result.stderr
    assert "Total commits in period: 0" in result.stdout
    assert "Fixes (F-xxx, fix):     0" in result.stdout
    assert "Reverts:                0" in result.stdout
    assert "Board test results:\n  (none)" in result.stdout
    assert "Iteration sagas (multiple versions of same fix):\n  (none)" in result.stdout
    assert "=== ACTION REQUIRED ===" in result.stdout


def test_report_counts_signals_and_extracts_all_fixup_ids(tmp_path):
    commits = (
        "a1 fix F-12: board test pass F-2\n"
        "b2 docs: F-2 v3 plan\n"
        "c3 build: ci\n"
        "d4 feat: throughput\n"
        "e5 revert F-12"
    )
    result = report(tmp_path, commits, "a1 full commit")
    assert result.returncode == 0, result.stderr
    assert "Total commits in period: 5" in result.stdout
    assert "Fixes (F-xxx, fix):     3" in result.stdout
    assert "Documentation:          1" in result.stdout
    assert "Build/CI:               1" in result.stdout
    assert "Features:               1" in result.stdout
    assert "Reverts:                1" in result.stdout
    assert "--- FIXUP IDs IN PERIOD ---\nF-2\nF-12\n" in result.stdout
    assert "Board test results:\na1 fix F-12: board test pass F-2" in result.stdout
    assert "Iteration sagas (multiple versions of same fix):\nF-2 v3" in result.stdout
    assert "a1 full commit" in result.stdout


def test_git_failure_is_not_reported_as_an_empty_period(tmp_path):
    result = report(tmp_path, fail=True)
    assert result.returncode != 0
    assert "git log failed" in result.stderr
    assert "=== ACTION REQUIRED ===" not in result.stdout
