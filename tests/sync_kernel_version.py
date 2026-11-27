"""Host tests for versions.lock upstream synchronization and kernel-pin scripts."""

import os
from pathlib import Path
import shutil
import subprocess

import pytest
import yaml

pytestmark = pytest.mark.host
ROOT = Path(__file__).resolve().parents[1]
SYNC_SCRIPT = ROOT / "kernel/common/scripts/sync-kernel-version.sh"
STAGE_SCRIPT = ROOT / "kernel/common/scripts/stage-kernel.sh"
PATCH_HEALTH_SCRIPT = ROOT / "kernel/common/scripts/patch-health.sh"
DEV_BUILD_SCRIPT = ROOT / "bin/dev-build.sh"
AUTO_BUILD_YML = ROOT / ".github/workflows/auto-build.yml"
SYNC_WORKFLOW_YML = ROOT / ".github/workflows/sync-versions-lock.yml"


def _make_fake_repo(tmp_path, lock_ver="6.18.62", lock_rev="1", toml_ver=None, toml_rev=None):
    repo = tmp_path / "repo"
    scripts = repo / "kernel/common/scripts"
    scripts.mkdir(parents=True)
    shutil.copy(SYNC_SCRIPT, scripts / "sync-kernel-version.sh")
    lock_series = ".".join(lock_ver.split(".")[:2])
    (repo / "versions.lock").write_text(
        f': "${{KERNEL_VERSION:={lock_ver}}}"\n'
        f': "${{KERNEL_SERIES:={lock_series}}}"\n'
        f': "${{KERNEL_REVISION:={lock_rev}}}"\n'
        'export KERNEL_VERSION KERNEL_SERIES KERNEL_REVISION\n'
        ': "${ARCH:=arm64}"\n'
        'export ARCH\n'
    )
    if toml_ver is not None:
        toml_dir = repo / "vyos-build/data"
        toml_dir.mkdir(parents=True)
        rev_line = f'kernel_revision = "{toml_rev}"\n' if toml_rev is not None else ""
        (toml_dir / "defaults.toml").write_text(
            f'kernel_version = "{toml_ver}"\n{rev_line}'
        )
    return repo


def _clean_env(extra=None):
    env = {
        k: v
        for k, v in os.environ.items()
        if k not in {
            "KERNEL_VERSION",
            "KERNEL_SERIES",
            "KERNEL_REVISION",
            "VYOS_BUILD_DEFAULTS",
            "VYOS_UPSTREAM_DEFAULTS_URL",
            "SYNC_FETCH_UPSTREAM",
        }
    }
    if extra:
        env.update(extra)
    return env


def _run_sync(repo, *args, env_extra=None):
    return subprocess.run(
        ["bash", str(repo / "kernel/common/scripts/sync-kernel-version.sh"), *args],
        capture_output=True,
        text=True,
        env=_clean_env(env_extra),
        timeout=10,
    )


def test_update_rewrites_version_series_and_revision(tmp_path):
    repo = _make_fake_repo(tmp_path, lock_ver="6.18.62", lock_rev="1", toml_ver="6.19.2", toml_rev="3")
    res = _run_sync(repo, "--update")
    assert res.returncode == 0, res.stderr
    lock_text = (repo / "versions.lock").read_text()
    assert ': "${KERNEL_VERSION:=6.19.2}"' in lock_text
    assert ': "${KERNEL_SERIES:=6.19}"' in lock_text
    assert ': "${KERNEL_REVISION:=3}"' in lock_text


def test_check_fails_on_revision_only_mismatch_and_update_fixes_it(tmp_path):
    repo = _make_fake_repo(tmp_path, lock_ver="6.18.64", lock_rev="1", toml_ver="6.18.64", toml_rev="2")
    chk = _run_sync(repo, "--check")
    assert chk.returncode == 1, f"expected mismatch exit 1, got {chk.returncode}: {chk.stdout}"
    upd = _run_sync(repo, "--update")
    assert upd.returncode == 0, upd.stderr
    assert ': "${KERNEL_REVISION:=2}"' in (repo / "versions.lock").read_text()
    chk2 = _run_sync(repo, "--check")
    assert chk2.returncode == 0, chk2.stdout + chk2.stderr


def test_fetch_reads_upstream_url_over_stale_local_defaults(tmp_path):
    # Local vyos-build is stale (6.18.54), upstream URL is 6.18.64 rev 2
    repo = _make_fake_repo(tmp_path, lock_ver="6.18.62", lock_rev="1", toml_ver="6.18.54", toml_rev="1")
    upstream = tmp_path / "upstream-defaults.toml"
    upstream.write_text('kernel_version = "6.18.64"\nkernel_revision = "2"\n')

    res = _run_sync(
        repo,
        "--fetch",
        "--update",
        env_extra={"VYOS_UPSTREAM_DEFAULTS_URL": upstream.as_uri()},
    )
    assert res.returncode == 0, res.stdout + res.stderr
    lock_text = (repo / "versions.lock").read_text()
    assert ': "${KERNEL_VERSION:=6.18.64}"' in lock_text
    assert ': "${KERNEL_SERIES:=6.18}"' in lock_text
    assert ': "${KERNEL_REVISION:=2}"' in lock_text


def test_fetch_rejects_malformed_upstream_payload(tmp_path):
    repo = _make_fake_repo(tmp_path, lock_ver="6.18.64", lock_rev="1")
    bad = tmp_path / "bad.toml"
    bad.write_text("<html>502 Bad Gateway</html>\n")

    res = _run_sync(
        repo,
        "--fetch",
        "--update",
        env_extra={"VYOS_UPSTREAM_DEFAULTS_URL": bad.as_uri()},
    )
    assert res.returncode != 0
    assert "usage:" not in res.stderr
    assert ': "${KERNEL_VERSION:=6.18.64}"' in (repo / "versions.lock").read_text()


def test_common_sh_exports_kernel_revision(tmp_path):
    res = subprocess.run(
        [
            "bash",
            "-c",
            f'BC_QUIET=1 . "{ROOT}/bin/common.sh" && printf "%s:%s:%s\\n" "$KERNEL_VERSION" "$KERNEL_SERIES" "$KERNEL_REVISION"',
        ],
        capture_output=True,
        text=True,
        env=_clean_env({"VYOS_BUILD_DEFAULTS": str(tmp_path / "nonexistent.toml")}),
        timeout=10,
    )
    assert res.returncode == 0, res.stderr
    parts = res.stdout.strip().split(":")
    assert len(parts) == 3 and all(parts)


def test_ci_setup_kernel_parses_versions_lock_series_cleanly():
    text = (ROOT / "bin/ci-setup-kernel.sh").read_text()
    assert "awk -F= '/KERNEL_SERIES/" not in text


def test_stage_and_patch_health_source_sync_before_versions_lock():
    for script in (STAGE_SCRIPT, PATCH_HEALTH_SCRIPT):
        text = script.read_text()
        sync_pos = text.index('sync-kernel-version.sh"')
        lock_pos = text.index('versions.lock"')
        assert sync_pos < lock_pos, (
            f"{script.name} sources versions.lock before sync-kernel-version.sh, "
            "which poisons KERNEL_VERSION as an env override"
        )
        assert "!= " in text and ".kernel-version" in text, (
            f"{script.name} must re-fetch when work/.kernel-version differs from target version"
        )


def test_auto_build_syncs_versions_lock_on_main_release():
    wf = yaml.safe_load(AUTO_BUILD_YML.read_text())
    build_outputs = wf["jobs"]["build_iso"]["outputs"]
    assert "kernel_version" in build_outputs
    assert "kernel_revision" in build_outputs

    publish_steps = wf["jobs"]["publish"]["steps"]
    feed_step = next(s for s in publish_steps if "write update-check feed" in s.get("name", ""))
    assert "sync-kernel-version.sh --update" in feed_step["run"]
    assert "KERNEL_VERSION" in feed_step["env"]
    assert "KERNEL_REVISION" in feed_step["env"]

    rebase_step = next(
        s for s in publish_steps if s.get("id") == "check_if_the_repository_was_modified_during_runtime"
    )
    assert 'FEEDS="version.json versions.lock"' in rebase_step["run"]

    autocommit_step = next(s for s in publish_steps if "create autocommit" in s.get("name", ""))
    assert "versions.lock" in autocommit_step["with"]["file_pattern"]

    # Guardrail from .github/workflows/AGENTS.md: auto-build.yml stays reusable-only
    on_block = wf.get("on") or wf.get(True)
    assert "workflow_dispatch" not in on_block


def test_scheduled_sync_workflow_keeps_main_and_dpaa1_in_sync():
    assert SYNC_WORKFLOW_YML.exists(), "missing scheduled sync-versions-lock.yml workflow"
    wf = yaml.safe_load(SYNC_WORKFLOW_YML.read_text())
    on_block = wf.get("on") or wf.get(True)
    assert "schedule" in on_block
    assert "workflow_dispatch" in on_block
    jobs = wf["jobs"]
    sync_job = next(iter(jobs.values()))
    assert sync_job["runs-on"] == "ubuntu-latest"
    branches = sync_job["strategy"]["matrix"]["branch"]
    assert set(branches) >= {"main", "dpaa1"}
    steps_text = "\n".join(str(s.get("run", "")) + str(s.get("with", "")) for s in sync_job["steps"])
    assert "sync-kernel-version.sh" in steps_text
    assert "--fetch" in steps_text
    assert "--update" in steps_text
    assert "versions.lock" in steps_text


def test_dev_build_uses_sync_kernel_version():
    text = DEV_BUILD_SCRIPT.read_text()
    assert "sync-kernel-version.sh" in text
