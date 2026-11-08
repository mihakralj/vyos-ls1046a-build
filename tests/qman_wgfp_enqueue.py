"""Host tests for QMan enqueue backpressure and WireGuard egress ownership."""

from pathlib import Path
import subprocess

import pytest

pytestmark = pytest.mark.host
ROOT = Path(__file__).resolve().parents[1]
QMAN_PATCH = ROOT / "kernel/common/patches/board/4019-qman-enqueue-fqid.patch"
WGFP = ROOT / "kernel/common/files/drivers/net/ethernet/freescale/dpaa/dpaa_wgfp.c"
HARNESS = ROOT / "tests/dut/qman_wgfp_enqueue.c"


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


def test_enqueue_backpressure_and_ern_ownership(tmp_path):
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
    wgfp = WGFP.read_text()
    bodies = [
        extract(
            qman, "int qman_enqueue_fqid(",
            "\nEXPORT_SYMBOL_GPL(qman_enqueue_fqid)",
        ),
        extract(wgfp, "static void wgfp_egress_ern(", "\nEXPORT_SYMBOL_GPL(dpaa_wgfp_egress)"),
        extract(wgfp, "void dpaa_wgfp_xmit(", "\nEXPORT_SYMBOL_GPL(dpaa_wgfp_xmit)"),
    ]
    (tmp_path / "qman_wgfp_body.h").write_text("\n\n".join(bodies) + "\n")
    binary = tmp_path / "qman_wgfp"
    subprocess.run(
        [
            "cc", "-std=gnu11", "-Wall", "-Wextra", "-Werror",
            "-Wno-unused-parameter", "-Wno-unused-function", "-I", str(tmp_path),
            str(HARNESS), "-o", str(binary),
        ],
        check=True,
    )
    subprocess.run([str(binary)], check=True)
