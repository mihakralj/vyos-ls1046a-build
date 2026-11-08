"""Host-only regression coverage for the F-299 INITFQ DMA fixup."""

from pathlib import Path
import subprocess
import sys

import pytest

pytestmark = pytest.mark.host
FIXUP = Path(__file__).resolve().parents[1] / "bin/kernel-fixups/F_299.py"
QMAN = Path("drivers/soc/fsl/qbman/qman.c")

PRISTINE = """\
int qman_init_fq(struct qman_fq *fq, u32 flags, struct qm_mcc_initfq *opts)
{
\tint ret = 0;

\tmyverb = flags;
\t/* Issue an INITFQ_[PARKED|SCHED] management command */
\tmcc = qm_mc_start(&p->p);
\tif (fq_isclear(fq, QMAN_FQ_FLAG_TO_DCPORTAL)) {
\t\tdma_addr_t phys_fq;

\t\tmcc->initfq.we_mask |= cpu_to_be16(QM_INITFQ_WE_CONTEXTB);
\t\tif (wants_context_a) {
\t\t\tstruct qman_portal *p = qman_dma_portal;

\t\t\tphys_fq = dma_map_single(p->config->dev, fq,
\t\t\t\t\t\t sizeof(*fq), DMA_TO_DEVICE);
\t\t\tif (dma_mapping_error(p->config->dev, phys_fq)) {
\t\t\t\tdev_err(p->config->dev, "dma_mapping failed\\n");
\t\t\t\tret = -EIO;
\t\t\t\tgoto out;
\t\t\t}

\t\t\tqm_fqd_stashing_set64(&mcc->initfq.fqd, phys_fq);
\t\t}
\t}
\tqm_mc_commit(&p->p, myverb);
}
EXPORT_SYMBOL(qman_init_fq);
"""


@pytest.fixture
def tree(tmp_path):
    path = tmp_path / QMAN
    path.parent.mkdir(parents=True)
    path.write_text(PRISTINE)
    return tmp_path


def apply(tree):
    return subprocess.run(
        [sys.executable, "-B", str(FIXUP)], cwd=tree,
        capture_output=True, text=True, check=False,
    )


def shadow(text):
    return text.replace(
        "\tif (fq_isclear(fq, QMAN_FQ_FLAG_TO_DCPORTAL)) {\n"
        "\t\tmcc->initfq.we_mask",
        "\tif (fq_isclear(fq, QMAN_FQ_FLAG_TO_DCPORTAL)) {\n"
        "\t\tdma_addr_t phys_fq;\n\n"
        "\t\tmcc->initfq.we_mask",
        1,
    )


def test_f299_fresh_and_idempotent(tree):
    result = apply(tree)
    assert result.returncode == 0, result.stdout + result.stderr
    path = tree / QMAN
    fixed = path.read_text()
    assert fixed.count("dma_addr_t phys_fq") == 1
    assert fixed.index("dma_map_single(") < fixed.index("qm_mc_start(")
    assert fixed.index("qm_mc_start(") < fixed.index("qm_fqd_stashing_set64(")
    assert fixed.index("qm_fqd_stashing_set64(") < fixed.index("qm_mc_commit(")
    result = apply(tree)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "already applied" in result.stdout
    assert path.read_text() == fixed


def test_f299_repairs_previously_shadowed_output(tree):
    assert apply(tree).returncode == 0
    path = tree / QMAN
    fixed = path.read_text()
    broken = shadow(fixed)
    assert broken != fixed and broken.count("dma_addr_t phys_fq") == 2
    path.write_text(broken)
    result = apply(tree)
    assert result.returncode == 0, result.stdout + result.stderr
    assert path.read_text() == fixed
    assert apply(tree).returncode == 0
    assert path.read_text() == fixed


@pytest.mark.parametrize(
    "damage", ["missing-marker", "changed-mapping", "duplicate-shadow", "split-shadow"],
)
def test_f299_rejects_partial_or_ambiguous_source(tree, damage):
    assert apply(tree).returncode == 0
    path = tree / QMAN
    text = path.read_text()
    if damage == "missing-marker":
        text = text.replace("\t/* F-299: map FQ context before MC start */\n", "")
    elif damage == "changed-mapping":
        text = shadow(text).replace("phys_fq = dma_map_single(", "phys_fq = other_map(", 1)
    elif damage == "duplicate-shadow":
        text = shadow(text).replace(
            "\t\tdma_addr_t phys_fq;\n\n",
            "\t\tdma_addr_t phys_fq;\n\t\tdma_addr_t phys_fq;\n\n",
            1,
        )
    else:
        text = text.replace(
            "\tqm_mc_commit(&p->p, myverb);",
            "\t{ dma_addr_t\n\t\tphys_fq; }\n\tqm_mc_commit(&p->p, myverb);",
            1,
        )
    path.write_text(text)
    result = apply(tree)
    assert result.returncode != 0
    assert "FATAL" in result.stderr
    assert path.read_text() == text
