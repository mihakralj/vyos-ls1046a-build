"""Host tests for QMan enqueue backpressure and WireGuard egress ownership."""

from pathlib import Path
import subprocess

import pytest

pytestmark = pytest.mark.host
ROOT = Path(__file__).resolve().parents[1]
QMAN_PATCH = ROOT / "kernel/common/patches/board/4019-qman-enqueue-fqid.patch"
WGFP = ROOT / "kernel/common/files/drivers/net/ethernet/freescale/dpaa/dpaa_wgfp.c"


def extract(source, start, end):
    offset = source.index(start)
    return source[offset:source.index(end, offset)]


def preimage_hunk(path, filename):
    lines = path.read_text().splitlines()
    section = lines.index(f"diff --git a/{filename} b/{filename}")
    start = next(
        i for i in range(section + 1, len(lines)) if lines[i].startswith("@@ ")
    )
    end = next(
        (i for i in range(start + 1, len(lines)) if lines[i].startswith("diff --git ")),
        len(lines),
    )
    return "\n".join(line[1:] for line in lines[start + 1:end]
                     if line.startswith((" ", "-"))) + "\n"


def test_enqueue_backpressure_and_wgfp_no_real_bpid_ownership(tmp_path):
    for filename in ("drivers/soc/fsl/qbman/qman.c", "include/soc/fsl/qman.h"):
        target = tmp_path / filename
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(preimage_hunk(QMAN_PATCH, filename))
    qman_path = tmp_path / "drivers/soc/fsl/qbman/qman.c"
    legacy = extract(qman_path.read_text(), "int qman_enqueue(",
                     "\nEXPORT_SYMBOL(qman_enqueue)")
    subprocess.run(["git", "apply", "--no-index", str(QMAN_PATCH)],
                   cwd=tmp_path, check=True)
    qman = qman_path.read_text()
    assert extract(qman, "int qman_enqueue(", "\nEXPORT_SYMBOL(qman_enqueue)") == legacy
    tagged = extract(
        qman, "int qman_enqueue_fqid(", "\nEXPORT_SYMBOL_GPL(qman_enqueue_fqid)"
    )
    assert "return eq ? 0 : -EBUSY;" in tagged
    wgfp = WGFP.read_text()
    xmit = extract(wgfp, "void dpaa_wgfp_xmit(",
                   "\nEXPORT_SYMBOL_GPL(dpaa_wgfp_xmit)")
    assert "qman_enqueue" not in xmit
    assert "qman_enqueue_fqid" not in xmit
    assert "dpaa_wgfp_tx_submit(" in xmit
    assert "dpaa_wgfp_xmit_skb(" in xmit
    submit = extract(wgfp, "void dpaa_wgfp_tx_submit(",
                     "\nEXPORT_SYMBOL_GPL(dpaa_wgfp_tx_submit)")
    assert "fd.bpid = WGFP_BPID_INV;" in submit
    submit_skb = extract(wgfp, "static void wgfp_submit_skb(",
                         "\nvoid dpaa_wgfp_xmit(")
    assert "dev_queue_xmit(skb)" in submit_skb
