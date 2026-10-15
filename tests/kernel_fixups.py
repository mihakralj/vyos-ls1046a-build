"""Host-only regression coverage for the F-199 offload TX work queue."""

import ast
from pathlib import Path
import subprocess
import sys

import pytest

pytestmark = pytest.mark.host
FIXUP = Path(__file__).resolve().parents[1] / "bin/kernel-fixups/F_199.py"
ETH_C = "drivers/net/ethernet/freescale/dpaa/dpaa_eth.c"


@pytest.fixture
def tree(tmp_path):
    nodes = ast.parse(FIXUP.read_text()).body
    paths = {
        name: ast.literal_eval(node.value)
        for node in nodes if isinstance(node, ast.Assign)
        for target in node.targets if isinstance(target, ast.Name)
        if (name := target.id) in {"ETH_C", "ETH_H", "OFF_H"}
    }
    sources = {path: "" for path in paths.values()}
    produced = {path: [] for path in paths.values()}
    for node in nodes:
        if (isinstance(node, ast.Expr) and isinstance(node.value, ast.Call)
                and isinstance(node.value.func, ast.Name)
                and node.value.func.id == "replace"):
            call = node.value
            path = paths[call.args[0].id]
            old = ast.literal_eval(call.args[2])
            # Some anchors are produced by an earlier replacement.
            if not any(old in text for text in produced[path]):
                sources[path] += old + "\n"
            if isinstance(call.args[3], ast.Constant):
                produced[path].append(ast.literal_eval(call.args[3]))
    for path, text in sources.items():
        dest = tmp_path / path
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(text)
    return tmp_path


def apply(tree):
    return subprocess.run(
        [sys.executable, str(FIXUP)], cwd=tree,
        capture_output=True, text=True, check=False,
    )


def snapshot(tree):
    return {p.relative_to(tree): p.read_bytes() for p in tree.rglob("*") if p.is_file()}


def check_wq(text):
    allocator = text.split("int dpaa_alloc_offload_tx_fq(", 1)[1]
    setup = allocator.index("dpaa_setup_egress(")
    wq = allocator.index("dpaa_fq->wq = 6;")
    init = allocator.index("dpaa_fq_init(dpaa_fq, true)")
    assert setup < wq < init
    assert allocator.count("dpaa_fq->wq = 6;") == 1
    assert "0x9a000000c0000000ULL" in text
    assert "FQ_TYPE_TX_NO_CONFIRM && !td_enable" in text
    assert "rtnl_lock();" in allocator and "rtnl_unlock();" in allocator
    assert "list_del(&dpaa_fq->list);" in allocator
    assert "devm_kfree(dev->dev.parent, dpaa_fq);" in allocator


def test_f199_fresh_and_idempotent(tree):
    result = apply(tree)
    assert result.returncode == 0, result.stdout + result.stderr
    check_wq((tree / ETH_C).read_text())
    before = snapshot(tree)
    result = apply(tree)
    assert result.returncode == 0, result.stdout + result.stderr
    assert snapshot(tree) == before


def test_f199_upgrades_existing_wq0_tree(tree):
    assert apply(tree).returncode == 0
    path = tree / ETH_C
    text = path.read_text().replace(
        "\t/* Share best-effort WQ6 with kernel TX; WQ0 starves it. */\n"
        "\tdpaa_fq->wq = 6;\n", "",
    )
    path.write_text(text)
    before = snapshot(tree)
    result = apply(tree)
    assert result.returncode == 0, result.stdout + result.stderr
    check_wq(path.read_text())
    assert {
        p: data for p, data in snapshot(tree).items() if p.as_posix() != ETH_C
    } == {p: data for p, data in before.items() if p.as_posix() != ETH_C}


@pytest.mark.parametrize("drift", ["missing", "duplicate", "wrong-wq"])
def test_f199_stale_tree_fails_on_drift(tree, drift):
    assert apply(tree).returncode == 0
    path = tree / ETH_C
    text = path.read_text().replace(
        "\t/* Share best-effort WQ6 with kernel TX; WQ0 starves it. */\n", "",
    )
    text = text.replace("\tdpaa_fq->wq = 6;\n", "")
    if drift == "missing":
        text = text.replace("&dpaa_fq_cbs.egress_ern", "&changed_callback")
    elif drift == "duplicate":
        text += text
    else:
        text = text.replace("\tlist_add_tail", "\tdpaa_fq->wq = 1;\n\tlist_add_tail")
    path.write_text(text)
    before = snapshot(tree)
    result = apply(tree)
    assert result.returncode != 0
    assert "expected 1 match" in result.stdout
    assert snapshot(tree) == before
