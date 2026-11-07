"""Replay and drift coverage for QMan shutdown and query responses."""

from pathlib import Path
import runpy
import subprocess
import sys

import pytest


pytestmark = pytest.mark.host
FIXUP = Path(__file__).resolve().parents[1] / "bin/kernel-fixups/F_303.py"
FIX = runpy.run_path(str(FIXUP))
QUERY_FIXUP = Path(__file__).resolve().parents[1] / "bin/kernel-fixups/F_305.py"
QUERY = runpy.run_path(str(QUERY_FIXUP))
RESPONSE_FIXUP = Path(__file__).resolve().parents[1] / "bin/kernel-fixups/F_306.py"
RESPONSE = runpy.run_path(str(RESPONSE_FIXUP))
QMAN = Path("drivers/soc/fsl/qbman/qman.c")


@pytest.fixture
def tree(tmp_path):
    path = tmp_path / QMAN
    path.parent.mkdir(parents=True)
    path.write_text(
        "int qman_shutdown_fq(u32 fqid)\n{\n" +
        FIX["DECL_OLD"] + FIX["SAVE_OLD"] +
        "\t\t" + FIX["ORL_OLD"] + " {}\n" +
        "\t\t" + FIX["EMPTY_OLD"] + " {}\n}\n"
    )
    return tmp_path


def apply(tree):
    return subprocess.run(
        [sys.executable, "-B", str(FIXUP)], cwd=tree,
        capture_output=True, text=True, check=False,
    )


def test_f303_replay_and_idempotence(tree):
    result = apply(tree)
    assert result.returncode == 0, result.stdout + result.stderr
    path = tree / QMAN
    fixed = path.read_text()
    assert all(fixed.count(new) == 1 for _, new in FIX["REPLACEMENTS"])
    result = apply(tree)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "already applied" in result.stdout
    assert path.read_text() == fixed


@pytest.mark.parametrize("damage", ["duplicate", "changed-status", "partial"])
def test_f303_rejects_drift_without_writing(tree, damage):
    path = tree / QMAN
    source = path.read_text()
    if damage == "duplicate":
        source = source.replace(FIX["SAVE_OLD"], FIX["SAVE_OLD"] * 2)
    elif damage == "changed-status":
        source = source.replace(FIX["ORL_OLD"], "if (some_other_status)")
    else:
        source = source.replace(FIX["DECL_OLD"], FIX["DECL_NEW"])
    path.write_text(source)
    result = apply(tree)
    assert result.returncode != 0
    assert "FATAL" in result.stderr
    assert path.read_text() == source


@pytest.fixture
def query_tree(tmp_path):
    path = tmp_path / QMAN
    path.parent.mkdir(parents=True)
    path.write_text(
        "int qman_query_fq(struct qman_fq *fq, struct qm_fqd *fqd)\n{\n" +
        QUERY["OLD"] + "\t\t*fqd = mcr->queryfq.fqd;\n}\n" +
        "int qman_query_fq_np(struct qman_fq *fq, struct qm_mcr_queryfq_np *np)\n{\n" +
        QUERY["OLD_NP"] + "\t\t*np = mcr->queryfq_np;\n}\n"
    )
    return tmp_path


def apply_query(tree):
    return subprocess.run(
        [sys.executable, "-B", str(QUERY_FIXUP)], cwd=tree,
        capture_output=True, text=True, check=False,
    )


def test_f305_replay_and_idempotence(query_tree):
    result = apply_query(query_tree)
    assert result.returncode == 0, result.stdout + result.stderr
    path = query_tree / QMAN
    fixed = path.read_text()
    assert QUERY["NEW"] in fixed and QUERY["OLD"] not in fixed
    assert QUERY["NEW_NP"] in fixed and QUERY["OLD_NP"] not in fixed
    result = apply_query(query_tree)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "already applied" in result.stdout
    assert path.read_text() == fixed


def test_f305_upgrades_previous_query_only_fix(query_tree):
    path = query_tree / QMAN
    path.write_text(path.read_text().replace(QUERY["OLD"], QUERY["NEW"]))
    result = apply_query(query_tree)
    assert result.returncode == 0, result.stdout + result.stderr
    assert QUERY["NEW"] in path.read_text()
    assert QUERY["NEW_NP"] in path.read_text()


def test_f305_changes_only_the_query_functions(query_tree):
    path = query_tree / QMAN
    unrelated = "void other(void)\n{\n" + QUERY["OLD"] + "}\n"
    path.write_text(unrelated + path.read_text())
    result = apply_query(query_tree)
    assert result.returncode == 0, result.stdout + result.stderr
    fixed = path.read_text()
    assert fixed.startswith(unrelated)
    assert fixed.count(QUERY["NEW"]) == 1
    assert fixed.count(QUERY["NEW_NP"]) == 1


@pytest.mark.parametrize(
    "damage", ["duplicate", "changed-verb", "partial", "duplicate-np",
               "changed-np", "reverse-partial"],
)
def test_f305_rejects_drift_without_writing(query_tree, damage):
    path = query_tree / QMAN
    source = path.read_text()
    if damage == "duplicate":
        source = source.replace(QUERY["OLD"], QUERY["OLD"] * 2)
    elif damage == "changed-verb":
        source = source.replace("QM_MCR_VERB_QUERYFQ);", "QM_MCR_VERB_QUERYFQ_NP);")
    elif damage == "partial":
        source = source.replace("== QM_MCR_VERB_QUERYFQ", "!= QM_MCR_VERB_QUERYFQ")
    elif damage == "duplicate-np":
        source = source.replace(QUERY["OLD_NP"], QUERY["OLD_NP"] * 2)
    elif damage == "changed-np":
        source = source.replace("QM_MCR_VERB_QUERYFQ_NP);", "QM_MCR_VERB_QUERYFQ);")
    else:
        source = source.replace(QUERY["OLD_NP"], QUERY["NEW_NP"])
    path.write_text(source)
    result = apply_query(query_tree)
    assert result.returncode != 0
    assert "FATAL" in result.stderr
    assert path.read_text() == source


@pytest.fixture
def response_tree(tmp_path):
    path = tmp_path / QMAN
    path.parent.mkdir(parents=True)
    path.write_text(
        "int qman_shutdown_fq(u32 fqid)\n{\n" +
        "".join(old * count for old, _, count in RESPONSE["REPLACEMENTS"]) +
        "}\n"
    )
    return tmp_path


def apply_response(tree):
    return subprocess.run(
        [sys.executable, "-B", str(RESPONSE_FIXUP)], cwd=tree,
        capture_output=True, text=True, check=False,
    )


def test_f306_replay_and_idempotence(response_tree):
    result = apply_response(response_tree)
    assert result.returncode == 0, result.stdout + result.stderr
    path = response_tree / QMAN
    fixed = path.read_text()
    assert all(fixed.count(old) == 0 and fixed.count(new) == count
               for old, new, count in RESPONSE["REPLACEMENTS"])
    result = apply_response(response_tree)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "already applied" in result.stdout
    assert path.read_text() == fixed


@pytest.mark.parametrize("damage", ["duplicate", "changed-verb", "partial", "one-oos"])
def test_f306_rejects_drift_without_writing(response_tree, damage):
    path = response_tree / QMAN
    source = path.read_text()
    if damage == "duplicate":
        source = source.replace(RESPONSE["NP_OLD"], RESPONSE["NP_OLD"] * 2)
    elif damage == "changed-verb":
        source = source.replace("QM_MCR_VERB_ALTER_RETIRE);", "QM_MCR_VERB_ALTER_OOS);")
    elif damage == "partial":
        source = source.replace(RESPONSE["QUERY_OLD"], RESPONSE["QUERY_NEW"])
    else:
        source = source.replace(RESPONSE["OOS_OLD"], RESPONSE["OOS_NEW"], 1)
    path.write_text(source)
    result = apply_response(response_tree)
    assert result.returncode != 0
    assert "FATAL" in result.stderr
    assert path.read_text() == source
