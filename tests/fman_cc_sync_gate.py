"""Regression tests for the F-168/F-215 patch-fold interaction."""

from pathlib import Path
import subprocess
import sys

import pytest

pytestmark = pytest.mark.host
FIXUP = Path(__file__).resolve().parents[1] / "bin/kernel-fixups/F_168.py"
TRANSITIONS = FIXUP.with_name("F_292.py")
PORT = Path("drivers/net/ethernet/freescale/fman/fman_port.c")
ANCHOR = "\tiowrite32be(cc_muram_off, &port->bmi_regs->rx.fmbm_rccb);\n"
SYNC = "\t\tfman_set_fpm_extc(port->fm, inv0);\n"
GUARD = (
    "\t/* F-215(sync-gate): first port only. */\n"
    "\tif (cc_muram_off && fman_cc_engaged_get(port->fm) == 0) {\n"
    "\t\tconst u32 inv0 = 0x80000000U;\n" + SYNC + "\t}\n"
)


def source(extra=""):
    return (
        "int fman_port_set_cc_base(struct fman_port *port, u32 cc_muram_off)\n"
        "{\n" + ANCHOR + extra + "}\n"
        "EXPORT_SYMBOL_GPL(fman_port_set_cc_base);\n"
    )


def run_fixup(tree, fixup=FIXUP):
    return subprocess.run(
        [sys.executable, str(fixup)], cwd=tree,
        capture_output=True, text=True, check=False,
    )


def test_f168_preserves_folded_f215_guard(tmp_path):
    port = tmp_path / PORT
    port.parent.mkdir(parents=True)
    port.write_text(source(GUARD))
    before = port.read_bytes()
    for _ in range(2):
        result = run_fixup(tmp_path)
        assert result.returncode == 0, result.stdout + result.stderr
        assert port.read_bytes() == before


def test_f168_applies_once_on_unpatched_source(tmp_path):
    port = tmp_path / PORT
    port.parent.mkdir(parents=True)
    port.write_text(source())
    assert run_fixup(tmp_path).returncode == 0
    before = port.read_bytes()
    assert before.count(SYNC.encode()) == 1
    assert run_fixup(tmp_path).returncode == 0
    assert port.read_bytes() == before


@pytest.mark.parametrize("extra", [
    SYNC + GUARD,
    GUARD.replace(SYNC, SYNC + SYNC),
    GUARD.replace("fman_cc_engaged_get(port->fm) == 0",
                  "fman_cc_engaged_get(port->fm) != 0"),
])
def test_f168_rejects_duplicate_or_drifted_guard(tmp_path, extra):
    port = tmp_path / PORT
    port.parent.mkdir(parents=True)
    port.write_text(source(extra))
    before = port.read_bytes()
    result = run_fixup(tmp_path)
    assert result.returncode != 0
    assert "FATAL" in result.stdout
    assert port.read_bytes() == before


@pytest.fixture
def transition_tree(tmp_path):
    root = tmp_path / PORT.parent
    root.mkdir(parents=True)
    header = root / "fman.h"
    fman = root / "fman.c"
    port = root / "fman_port.c"
    header.write_text(
        "#include <linux/io.h>\n"
        "struct fman {\n"
        "\t/* F-215: count of RX ports currently engaged for AC_CC (non-zero\n"
        "\t * fmbm_rccb). The global FMFP_EXTC[INV0] SYNC is asserted only when\n"
        "\t * this is 0 (first port); a live sibling keeps the FMan controller\n"
        "\t * non-quiescent so INV0 could never clear -> multi-port arm wedge. */\n"
        "\tu8 cc_engaged_ports;\n};\n"
    )
    fman.write_text(
        "\tif (!fman)\n\t\treturn ERR_PTR(-ENOMEM);\n\n"
        "\tfm_node = of_node_get(of_dev->dev.of_node);\n"
        "/* F-215: engaged-port counter. Tracks how many RX ports currently have\n"
        " * a non-zero fmbm_rccb (AC_CC engaged). Mutated only from the PCD\n"
        " * arm/disarm path (fman_port_set_cc_base), which the PCD layer\n"
        " * serializes; used to assert the global FMFP_EXTC[INV0] SYNC only for\n"
        " * the first engaged port. */\n"
        "\treturn fman ? fman->cc_engaged_ports : 0;\n"
        "\tif (fman)\n\t\tfman->cc_engaged_ports++;\n"
        "\tif (fman && fman->cc_engaged_ports)\n"
        "\t\tfman->cc_engaged_ports--;\n"
    )
    accounting = (
        "\tif (cc_muram_off)\n"
        "\t\tfman_cc_engaged_inc(port->fm);\n"
        "\telse\n"
        "\t\tfman_cc_engaged_dec(port->fm);\n"
    )
    rx = (
        "int fman_port_set_cc_base(struct fman_port *port, u32 cc_muram_off)\n"
        "{\n\tu32 rfpne;\n"
        "\tif (!port || port->port_type != FMAN_PORT_TYPE_RX)\n"
        "\t\treturn -EINVAL;\n"
        "\t/* F-215(sync-gate): maintain the engaged-port counter used above.\n"
        "\t * arm (cc_muram_off != 0) -> ++, teardown (== 0) -> -- (floored). */\n"
        + accounting + "\treturn 0;\n}\n"
        "EXPORT_SYMBOL_GPL(fman_port_set_cc_base);\n"
    )
    oh = (
        "int fman_port_set_oh_cc_base(struct fman_port *port, u32 cc_muram_off)\n"
        "{\n"
        "\tif (!port || port->port_type != FMAN_PORT_TYPE_OH)\n"
        "\t\treturn -EINVAL;\n"
        + accounting + "\treturn 0;\n}\n"
        "EXPORT_SYMBOL_GPL(fman_port_set_oh_cc_base);\n"
    )
    port.write_text(
        "\tu32 ctrl_params_page;\t/* FM_CTL params page MURAM off (0116) */\n"
        + rx + oh
    )
    return tmp_path, (header, fman, port)


def test_f292_applies_atomically_and_is_idempotent(transition_tree):
    tree, files = transition_tree
    result = run_fixup(tree, TRANSITIONS)
    assert result.returncode == 0, result.stdout + result.stderr
    before = [path.read_bytes() for path in files]
    result = run_fixup(tree, TRANSITIONS)
    assert result.returncode == 0, result.stdout + result.stderr
    assert [path.read_bytes() for path in files] == before


@pytest.mark.parametrize("filename, old, new", [
    ("fman.c", "fm_node = of_node_get", "fm_node = missing"),
    ("fman_port.c", "FMAN_PORT_TYPE_OH", "FMAN_PORT_TYPE_OTHER"),
])
def test_f292_rejects_drift_without_partial_writes(transition_tree, filename,
                                                   old, new):
    tree, files = transition_tree
    target = next(path for path in files if path.name == filename)
    target.write_text(target.read_text().replace(old, new))
    before = [path.read_bytes() for path in files]
    result = run_fixup(tree, TRANSITIONS)
    assert result.returncode != 0
    assert "FATAL" in result.stderr
    assert [path.read_bytes() for path in files] == before
