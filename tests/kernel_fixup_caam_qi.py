"""Replay and drift coverage for CAAM QI fixups."""

from pathlib import Path
import runpy
import subprocess
import sys

import pytest


pytestmark = pytest.mark.host
ROOT = Path(__file__).resolve().parents[1]
FIXUP = ROOT / "bin/kernel-fixups/F_301.py"
FIX = runpy.run_path(str(FIXUP))
BACKPRESSURE_FIXUP = ROOT / "bin/kernel-fixups/F_302.py"
BACKPRESSURE = runpy.run_path(str(BACKPRESSURE_FIXUP))
INIT_FIXUP = ROOT / "bin/kernel-fixups/F_304.py"
INIT = runpy.run_path(str(INIT_FIXUP))
QI = Path("drivers/crypto/caam/qi.c")


@pytest.fixture
def tree(tmp_path):
    path = tmp_path / QI
    path.parent.mkdir(parents=True)
    path.write_text(FIX["OLD_ENQUEUE"] + FIX["OLD_DEPTH"])
    return tmp_path


def apply(tree):
    return subprocess.run(
        [sys.executable, "-B", str(FIXUP)], cwd=tree,
        capture_output=True, text=True, check=False,
    )


def test_f301_replay_and_idempotence(tree):
    result = apply(tree)
    assert result.returncode == 0, result.stdout + result.stderr
    path = tree / QI
    fixed = path.read_text()
    assert FIX["NEW_ENQUEUE"] in fixed and FIX["NEW_DEPTH"] in fixed
    result = apply(tree)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "already applied" in result.stdout
    assert path.read_text() == fixed


@pytest.mark.parametrize("damage", ["duplicate", "changed-depth", "partial"])
def test_f301_rejects_drift_without_writing(tree, damage):
    path = tree / QI
    source = path.read_text()
    if damage == "duplicate":
        source += FIX["OLD_ENQUEUE"]
    elif damage == "changed-depth":
        source = source.replace(FIX["OLD_DEPTH"], "\t\tif (other_depth)\n")
    else:
        source = source.replace(FIX["OLD_DEPTH"], FIX["NEW_DEPTH"])
    path.write_text(source)
    result = apply(tree)
    assert result.returncode != 0
    assert "FATAL" in result.stderr
    assert path.read_text() == source


@pytest.fixture
def backpressure_tree(tmp_path):
    path = tmp_path / QI
    path.parent.mkdir(parents=True)
    path.write_text(
        BACKPRESSURE["OLD_DECL"] + BACKPRESSURE["OLD_LOOP"] +
        BACKPRESSURE["DMA_GUARD"] +
        "\n\treturn ret;\n}\nEXPORT_SYMBOL(caam_qi_enqueue);\n"
    )
    return tmp_path


def apply_backpressure(tree):
    return subprocess.run(
        [sys.executable, "-B", str(BACKPRESSURE_FIXUP)], cwd=tree,
        capture_output=True, text=True, check=False,
    )


def test_f302_replay_and_idempotence(backpressure_tree):
    result = apply_backpressure(backpressure_tree)
    assert result.returncode == 0, result.stdout + result.stderr
    path = backpressure_tree / QI
    fixed = path.read_text()
    assert all(new in fixed for _, new in BACKPRESSURE["REPLACEMENTS"])
    result = apply_backpressure(backpressure_tree)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "already applied" in result.stdout
    assert path.read_text() == fixed


def test_f301_and_f302_replay_together(tmp_path):
    path = tmp_path / QI
    path.parent.mkdir(parents=True)
    path.write_text(
        BACKPRESSURE["OLD_DECL"] + BACKPRESSURE["OLD_LOOP"] +
        "\n\treturn ret;\n}\nEXPORT_SYMBOL(caam_qi_enqueue);\n" + FIX["OLD_DEPTH"]
    )
    for apply_fix in (apply, apply_backpressure):
        result = apply_fix(tmp_path)
        assert result.returncode == 0, result.stdout + result.stderr
    fixed = path.read_text()
    for apply_fix in (apply, apply_backpressure):
        result = apply_fix(tmp_path)
        assert result.returncode == 0, result.stdout + result.stderr
        assert "already applied" in result.stdout
        assert path.read_text() == fixed


def test_f302_upgrades_previous_retry_loop(backpressure_tree):
    path = backpressure_tree / QI
    source = path.read_text().replace(
        BACKPRESSURE["OLD_DECL"], BACKPRESSURE["NEW_DECL"]
    ).replace(BACKPRESSURE["OLD_LOOP"], BACKPRESSURE["PREVIOUS_LOOP"])
    path.write_text(source)
    result = apply_backpressure(backpressure_tree)
    assert result.returncode == 0, result.stdout + result.stderr
    fixed = path.read_text()
    assert BACKPRESSURE["PREVIOUS_LOOP"] not in fixed
    assert BACKPRESSURE["NEW_LOOP"] in fixed
    result = apply_backpressure(backpressure_tree)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "already applied" in result.stdout
    assert path.read_text() == fixed


@pytest.mark.parametrize("damage", ["duplicate", "changed-call", "partial", "no-dma"])
def test_f302_rejects_drift_without_writing(backpressure_tree, damage):
    path = backpressure_tree / QI
    source = path.read_text()
    if damage == "duplicate":
        source += BACKPRESSURE["OLD_LOOP"]
    elif damage == "changed-call":
        source = source.replace("ret = qman_enqueue(", "ret = other_enqueue(")
    elif damage == "partial":
        source = source.replace(BACKPRESSURE["OLD_DECL"], BACKPRESSURE["NEW_DECL"])
    else:
        source = source.replace(BACKPRESSURE["DMA_GUARD"], "")
    path.write_text(source)
    result = apply_backpressure(backpressure_tree)
    assert result.returncode != 0
    assert "FATAL" in result.stderr
    assert path.read_text() == source


@pytest.fixture
def init_tree(tmp_path):
    path = tmp_path / QI
    path.parent.mkdir(parents=True)
    path.write_text(
        "int caam_qi_init(struct platform_device *pdev)\n{\n" +
        INIT["OLD"] + "\treturn err;\n}\n"
    )
    return tmp_path


def apply_init(tree):
    return subprocess.run(
        [sys.executable, "-B", str(INIT_FIXUP)], cwd=tree,
        capture_output=True, text=True, check=False,
    )


def test_f304_replay_and_idempotence(init_tree):
    result = apply_init(init_tree)
    assert result.returncode == 0, result.stdout + result.stderr
    path = init_tree / QI
    fixed = path.read_text()
    assert INIT["NEW"] in fixed and INIT["OLD"] not in fixed
    result = apply_init(init_tree)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "already applied" in result.stdout
    assert path.read_text() == fixed


@pytest.mark.parametrize("damage", ["duplicate", "changed-action", "partial"])
def test_f304_rejects_drift_without_writing(init_tree, damage):
    path = init_tree / QI
    source = path.read_text()
    if damage == "duplicate":
        source = source.replace(INIT["OLD"], INIT["OLD"] * 2)
    elif damage == "changed-action":
        source = source.replace("caam_qi_shutdown, qidev", "other_shutdown, qidev")
    else:
        source = source.replace("goto fail2;", "goto free_cpumask;")
    path.write_text(source)
    result = apply_init(init_tree)
    assert result.returncode != 0
    assert "FATAL" in result.stderr
    assert path.read_text() == source
