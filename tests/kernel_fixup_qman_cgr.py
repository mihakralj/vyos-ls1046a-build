"""Host-only replay and drift tests for the F-300 CGR fixup."""

from pathlib import Path
import runpy
import subprocess
import sys

import pytest


pytestmark = pytest.mark.host
ROOT = Path(__file__).resolve().parents[1]
FIXUP = ROOT / "bin/kernel-fixups/F_300.py"
FIX = runpy.run_path(str(FIXUP))
QMAN = Path("drivers/soc/fsl/qbman/qman.c")
HEADER = Path("include/soc/fsl/qman.h")
DPAA = Path("drivers/net/ethernet/freescale/dpaa/dpaa_eth.c")
DPAA_HEADER = Path("drivers/net/ethernet/freescale/dpaa/dpaa_eth.h")
ETHTOOL = Path("drivers/net/ethernet/freescale/dpaa/dpaa_ethtool.c")
PATHS = (QMAN, HEADER, DPAA, DPAA_HEADER, ETHTOOL)


def pristine_sources():
    qman = (
        "static DEFINE_PER_CPU(struct qman_portal, qman_affine_portal);\n"
        "static void qm_congestion_task(struct work_struct *work)\n"
        "{\n"
        "\traw_spin_lock_irq(&p->cgr_lock);\n"
        "\tcgr->cb(p, cgr, 1);\n"
        "\traw_spin_unlock_irq(&p->cgr_lock);\n"
        "}\n"
        "int qman_create_cgr(struct qman_cgr *cgr, u32 flags,\n"
        "\t\t    struct qm_mcc_initcgr *opts)\n"
        "{\n"
        "\traw_spin_lock_irq(&p->cgr_lock);\n"
        + FIX["LINKED_CREATE"]
        + "\tif (cgr->cb)\n"
        "\t\tcgr->cb(p, cgr, 1);\n"
        "out:\n"
        "\traw_spin_unlock_irq(&p->cgr_lock);\n"
        "\treturn ret;\n"
        "}\n"
        "EXPORT_SYMBOL(qman_create_cgr);\n"
        + FIX["OLD_DELETE"]
    )
    header = (
        "/**\n * qman_delete_cgr - Deregisters a congestion group object\n */\n"
        " * This will select the proper CPU and run there qman_delete_cgr().\n"
        "void qman_delete_cgr_safe(struct qman_cgr *cgr);\n"
    )
    dpaa_header = (
        "struct dpaa_priv;\n"
        "struct dummy {\n"
        "\t\tstruct qman_cgr cgr;\n"
        "\tstruct qman_cgr ingress_cgr;\n"
        "};\n"
    )
    setup = ""
    cleanup = ""
    for member in ("cgr_data.cgr", "ingress_cgr"):
        setup += (
            f"\terr = qman_alloc_cgrid(&priv->{member}.cgrid);\n"
            "\tif (err < 0) {\n\t}\n"
            f"\terr = qman_create_cgr(&priv->{member}, 0, NULL);\n"
            "\tif (err < 0) {\n"
            f"\t\tqman_release_cgrid(priv->{member}.cgrid);\n"
            "\t\tgoto out_error;\n\t}\n"
        )
        pair = (
            f"\tqman_delete_cgr_safe(&priv->{member});\n"
            f"\tqman_release_cgrid(priv->{member}.cgrid);\n"
        )
        cleanup += pair * 2
    dpaa = (
        FIX["CALLBACK_COMMENT"] + FIX["OLD_CALLBACK"] + setup + cleanup
        + ("\t/* priv->cgr_data.cgr */\n" * 7)
        + ("\t/* priv->ingress_cgr */\n" * 3)
        + "\tdev_set_drvdata(dev, NULL);\n\tunregister_netdev(net_dev);\n"
    )
    return {
        QMAN: qman,
        HEADER: header,
        DPAA: dpaa,
        DPAA_HEADER: dpaa_header,
        ETHTOOL: "qman_query_cgr_congested(&priv->cgr_data.cgr, &status);\n",
    }


@pytest.fixture
def tree(tmp_path):
    for path, content in pristine_sources().items():
        target = tmp_path / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)
    return tmp_path


def apply(tree):
    return subprocess.run(
        [sys.executable, "-B", str(FIXUP)], cwd=tree,
        capture_output=True, text=True, check=False,
    )


def contents(tree):
    return {path: (tree / path).read_bytes() for path in PATHS}


def test_f300_fresh_replay_and_idempotence(tree):
    result = apply(tree)
    assert result.returncode == 0, result.stdout + result.stderr
    fixed = contents(tree)
    assert b"qman_cgr_disable_callback" in fixed[QMAN]
    assert b"dpaa_cgr_create_failed" in fixed[DPAA]
    assert b"int qman_delete_cgr_safe" in fixed[HEADER]
    result = apply(tree)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "already applied" in result.stdout
    assert contents(tree) == fixed


@pytest.mark.parametrize(
    "damage", ["duplicate-delete", "missing-reference", "unlocked-callback",
               "linked-create-error", "partial-application"],
)
def test_f300_rejects_drift_without_writing(tree, damage):
    qman = tree / QMAN
    dpaa = tree / DPAA
    if damage == "duplicate-delete":
        qman.write_text(qman.read_text() + FIX["OLD_DELETE"])
    elif damage == "missing-reference":
        dpaa.write_text(dpaa.read_text().replace("priv->ingress_cgr", "other", 1))
    elif damage == "unlocked-callback":
        qman.write_text(qman.read_text().replace(
            "\traw_spin_lock_irq(&p->cgr_lock);\n", "", 1,
        ))
    elif damage == "linked-create-error":
        qman.write_text(qman.read_text().replace(
            "\tif (cgr->cb)\n", "\tret = -EIO;\n\tif (cgr->cb)\n", 1,
        ))
    else:
        assert apply(tree).returncode == 0
        dpaa.write_text(dpaa.read_text().replace(FIX["MARKER"], "partial F-300"))
    before = contents(tree)
    result = apply(tree)
    assert result.returncode != 0
    assert "FATAL" in result.stderr
    assert contents(tree) == before
