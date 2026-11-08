"""Verify the final kernel config preserves the FMan removal safeguards."""

import os
from pathlib import Path
import subprocess

import pytest


pytestmark = pytest.mark.host
SETUP = Path(__file__).resolve().parents[1] / "bin/ci-setup-kernel.sh"


@pytest.fixture
def config_build(tmp_path):
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    config = scripts / "config"
    config.write_text(
        """#!/usr/bin/env python3
import pathlib
import sys

args = sys.argv[1:]
if args[0] in ("--enable", "--disable", "--set-val"):
    symbol = args[1]
    value = args[2] if args[0] == "--set-val" else (
        "y" if args[0] == "--enable" else "n"
    )
    path = pathlib.Path(".config")
    lines = path.read_text().splitlines()
    lines = [line for line in lines if not (
        line.startswith(symbol + "=") or
        line == "# " + symbol + " is not set"
    )]
    lines.append(symbol + "=" + value if value != "n" else
                 "# " + symbol + " is not set")
    path.write_text("\\n".join(lines) + "\\n")
"""
    )
    config.chmod(0o755)
    make = tmp_path / "make"
    make.write_text(
        """#!/bin/sh
case "$TEST_KCONFIG_OVERRIDE" in
    overlay) scripts/config --enable CONFIG_OF_OVERLAY ;;
    fman) scripts/config --set-val CONFIG_FSL_FMAN m ;;
    dpaa) scripts/config --disable CONFIG_FSL_DPAA ;;
    ethernet) scripts/config --set-val CONFIG_FSL_DPAA_ETH m ;;
    fail) echo "simulated olddefconfig failure" >&2; exit 1 ;;
esac
"""
    )
    make.chmod(0o755)
    (tmp_path / ".config").write_text(
        "CONFIG_OF_OVERLAY=y\nCONFIG_FSL_FMAN=m\n"
        "# CONFIG_FSL_DPAA is not set\nCONFIG_FSL_DPAA_ETH=m\n"
    )
    source = SETUP.read_text()
    start = "cat > /tmp/ls1046a-post-defconfig.sh << 'LS1046A_POSTDEFCONFIG_EOF'\n"
    end = "\nLS1046A_POSTDEFCONFIG_EOF"
    block = source.split(start, 1)[1].split(end, 1)[0]
    return tmp_path, block


def run_post_merge(config_build, *, override="", kunit="false", kasan="false"):
    path, block = config_build
    env = {
        **os.environ,
        "PATH": f"{path}{os.pathsep}{os.environ['PATH']}",
        "TEST_KCONFIG_OVERRIDE": override,
        "KUNIT": kunit,
        "KASAN": kasan,
        "GITHUB_WORKSPACE": str(SETUP.parents[1]),
    }
    return subprocess.run(
        ["bash", "-e"], input=block, text=True, cwd=path, env=env,
        capture_output=True, check=False,
    )


@pytest.mark.parametrize(("kunit", "kasan"), [
    ("false", "false"), ("true", "false"), ("false", "true"), ("true", "true"),
])
def test_post_merge_forces_removal_safeguards(config_build, kunit, kasan):
    result = run_post_merge(config_build, kunit=kunit, kasan=kasan)
    assert result.returncode == 0, result.stdout + result.stderr
    lines = (config_build[0] / ".config").read_text().splitlines()
    assert "# CONFIG_OF_OVERLAY is not set" in lines
    for symbol in ("CONFIG_FSL_FMAN", "CONFIG_FSL_DPAA", "CONFIG_FSL_DPAA_ETH"):
        assert f"{symbol}=y" in lines


@pytest.mark.parametrize(("override", "message"), [
    ("overlay", "CONFIG_OF_OVERLAY must be disabled"),
    ("fman", "CONFIG_FSL_FMAN must be built in"),
    ("dpaa", "CONFIG_FSL_DPAA must be built in"),
    ("ethernet", "CONFIG_FSL_DPAA_ETH must be built in"),
])
def test_post_merge_fails_if_olddefconfig_reverses_safeguard(
    config_build, override, message,
):
    result = run_post_merge(config_build, override=override, kunit="true")
    assert result.returncode != 0
    assert message in result.stderr


def test_config_check_rejects_conflicting_overlay_entries(config_build):
    assert run_post_merge(config_build).returncode == 0
    config = config_build[0] / ".config"
    with config.open("a") as out:
        out.write("CONFIG_OF_OVERLAY=y\n")
    check = SETUP.parents[1] / "kernel/common/scripts/check-fman-config.sh"
    result = subprocess.run(
        ["bash", str(check), str(config)], capture_output=True, text=True,
        check=False,
    )
    assert result.returncode != 0
    assert "CONFIG_OF_OVERLAY must be disabled" in result.stderr


def run_local_staging(config_build, *, stage_only="0", override=""):
    path, _ = config_build
    stage = SETUP.parents[1] / "kernel/common/scripts/stage-kernel.sh"
    text = stage.read_text()
    start = 'if [[ "$STAGE_ONLY" == "1" ]]; then'
    block = start + text.split(start, 1)[1]
    env = {
        **os.environ,
        "PATH": f"{path}{os.pathsep}{os.environ['PATH']}",
        "TEST_KCONFIG_OVERRIDE": override,
        "STAGE_ONLY": stage_only,
        "SCRIPT_DIR": str(stage.parent),
        "KSRC": str(path),
        "DEFCONFIG": str(path / ".config"),
        "ARCH": "arm64",
        "CROSS_COMPILE": "",
        "KVER": "test",
    }
    return subprocess.run(
        ["bash", "-e"], input=(
            'info() { :; }; ok() { :; }; err() { echo "$*" >&2; exit 1; }\n'
            + block
        ), text=True, cwd=path, env=env, capture_output=True, check=False,
    )


def test_local_staging_preserves_stage_only(config_build):
    config = config_build[0] / ".config"
    before = config.read_text()
    result = run_local_staging(config_build, stage_only="1", override="fail")
    assert result.returncode == 0, result.stderr
    assert config.read_text() == before


def test_local_staging_checks_resolved_config(config_build):
    assert run_post_merge(config_build).returncode == 0
    result = run_local_staging(config_build)
    assert result.returncode == 0, result.stderr
    result = run_local_staging(config_build, override="overlay")
    assert result.returncode != 0
    assert "CONFIG_OF_OVERLAY must be disabled" in result.stderr


def test_local_staging_reports_olddefconfig_failure(config_build):
    result = run_local_staging(config_build, override="fail")
    assert result.returncode != 0
    assert "simulated olddefconfig failure" in result.stderr
    assert "make olddefconfig failed" in result.stderr


def test_dev_build_rejects_unresolved_stage_only_config(tmp_path):
    source = (SETUP.parents[1] / "bin/dev-build.sh").read_text()
    start = "cmd_kernel() {"
    end = "\n_rebuild_dtb() {"
    block = start + source.split(start, 1)[1].split(end, 1)[0]
    result = subprocess.run(
        ["bash", "-e"], input=(
            'die() { echo "$*" >&2; exit 1; }; hdr() { :; }; '
            'bash() { echo "unexpected staging" >&2; }\n'
            + block + "\ncmd_kernel\n"
        ), text=True, cwd=tmp_path, env={**os.environ, "STAGE_ONLY": "1"},
        capture_output=True, check=False,
    )
    assert result.returncode != 0
    assert "STAGE_ONLY=1 cannot be used for a kernel build" in result.stderr
    assert "unexpected staging" not in result.stderr
